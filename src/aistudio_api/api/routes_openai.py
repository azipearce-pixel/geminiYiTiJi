"""OpenAI Chat Completions compatibility for the AI Studio gateway.

The endpoint exposes:
  GET  /v1/models
  POST /v1/chat/completions

Official Gemini API keys are read from Render environment variables or Secret
Files. Requests are round-robin distributed across keys; failed attempts move to
the next key. Only Gemini 3.1–4.0 chat models discovered through the configured
credentials are exposed; Cookie and API-key sources remain isolated.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aistudio_api.config import settings

logger = logging.getLogger("aistudio_api.openai_compat")
router = APIRouter(tags=["OpenAI compatibility"])
GOOGLE_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GoogleApiKeyPool:
    """Round-robin key selector; every actual upstream attempt consumes one turn."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cursor = 0

    @staticmethod
    def _split(raw: str) -> list[str]:
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
            if isinstance(parsed, dict):
                values = parsed.get("keys") or parsed.get("api_keys") or []
                if isinstance(values, list):
                    return [str(x).strip() for x in values if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [x.strip().strip("\"'") for x in re.split(r"[,;\r\n]+", text) if x.strip()]

    def keys(self) -> list[str]:
        values: list[str] = []
        for env_name in (
            "GOOGLE_API_KEYS",
            "GOOGLE_AI_STUDIO_API_KEYS",
            "GEMINI_API_KEYS",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
        ):
            values.extend(self._split(os.getenv(env_name) or ""))
        for env_name in ("GOOGLE_API_KEYS_FILE", "GEMINI_API_KEYS_FILE"):
            file_path = (os.getenv(env_name) or "").strip()
            if not file_path:
                continue
            try:
                path = Path(file_path)
                if path.is_file():
                    values.extend(self._split(path.read_text(encoding="utf-8-sig")))
                else:
                    logger.warning("Google API key Secret File configured by %s was not found", env_name)
            except OSError as exc:
                logger.warning("Could not read Google API key Secret File configured by %s: %s", env_name, exc)
        # Keep order while removing duplicates.
        return list(dict.fromkeys(x for x in values if x))

    async def next_key(self, attempted: set[str] | None = None) -> str | None:
        """Return one untried key and advance the global cursor for that attempt.

        Advancing per *attempt* (rather than per request) means a failed key and
        the fallback key both consume their own turn in the round-robin sequence.
        """
        keys = self.keys()
        if not keys:
            return None
        skipped = attempted or set()
        async with self._lock:
            start = self._cursor % len(keys)
            for offset in range(len(keys)):
                index = (start + offset) % len(keys)
                key = keys[index]
                if key in skipped:
                    continue
                self._cursor = (index + 1) % len(keys)
                return key
        return None


key_pool = GoogleApiKeyPool()
_model_cache_lock = asyncio.Lock()
_model_cache_time = 0.0
_official_model_cache: dict[str, dict[str, Any]] = {}
_cookie_model_cache_lock = asyncio.Lock()
_cookie_model_cache_time = 0.0
_cookie_model_cache: dict[str, dict[str, Any]] = {}

# Expose only conversational Gemini models in the requested generation range.
# Gemini 4 model IDs currently use "gemini-4-<name>" (for example,
# gemini-4-argon); the optional .0 form is accepted as well.
_GEMINI_CHAT_MODEL_RE = re.compile(r"^gemini-(?:3\.[1-9]\d*|4(?:\.0)?)-", re.IGNORECASE)
_NON_CHAT_MODEL_RE = re.compile(
    r"(?:^|[-_.])(?:image|tts|live|transcribe|transcription|translate|audio|speech|"
    r"video|veo|omni|music|lyria|embedding|embed|robotics|realtime|decision)(?:[-_.]|$)",
    re.IGNORECASE,
)


def _clean_model_id(value: object) -> str:
    return str(value or "").strip().removeprefix("models/")


def _is_gemini_chat_model(model_id: object, info: Mapping[str, Any] | None = None) -> bool:
    """True only for Gemini 3.1–4.0 conversational models, not modality tools."""
    clean_id = _clean_model_id(model_id).lower()
    if not clean_id or not _GEMINI_CHAT_MODEL_RE.match(clean_id):
        return False
    if _NON_CHAT_MODEL_RE.search(clean_id):
        return False
    if info is not None:
        methods = info.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            return False
    return True


def _cookie_model_allowed_for_tier(info: Mapping[str, Any], tier: object) -> bool:
    """Respect the AI Studio Pro/Ultra accessModes attached to each Cookie model."""
    modes = info.get("accessModes")
    if not isinstance(modes, list) or not modes:
        return True
    normalized_tier = str(tier or "Free").strip().lower()
    is_high_tier = (
        normalized_tier in ("plus", "pro", "ultra")
        or "ultra" in normalized_tier
        or "pro" in normalized_tier
    )
    is_ultra_tier = "ultra" in normalized_tier
    if 3 in modes and is_high_tier:
        return True
    if 4 in modes and is_ultra_tier:
        return True
    return all(mode not in (3, 4) for mode in modes)


def _api_error(message: str, error_type: str = "api_error", code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": None, "code": code}}


def _redact_keys(message: str) -> str:
    text = str(message)
    for secret in key_pool.keys():
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:1000]


def _safe_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return _redact_keys(str(err.get("message") or err.get("status") or response.text[:1000]))
        return _redact_keys(str(data)[:1000])
    except Exception:
        return _redact_keys(response.text[:1000] or f"Google API returned HTTP {response.status_code}")


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=20.0),
        proxy=settings.proxy_url,
        follow_redirects=True,
        headers={"User-Agent": "aistudio-api-render-openai-compat/1.0"},
    )


async def _fetch_models_for_key(client: httpx.AsyncClient, key: str) -> list[dict[str, Any]]:
    token: str | None = None
    found: list[dict[str, Any]] = []
    # Google listModels is key-scoped and paginated. Only keep models whose
    # returned metadata explicitly supports generateContent.
    for _ in range(10):
        params: dict[str, str | int] = {"pageSize": 1000}
        if token:
            params["pageToken"] = token
        response = await client.get(
            f"{GOOGLE_API_ROOT}/models",
            params=params,
            headers={"x-goog-api-key": key},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {_safe_error_text(response)}")
        data = response.json()
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        if isinstance(raw_models, list):
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                model_id = _clean_model_id(item.get("name"))
                methods = item.get("supportedGenerationMethods") or []
                if not isinstance(methods, list) or "generateContent" not in methods:
                    continue
                if not _is_gemini_chat_model(model_id, item):
                    continue
                found.append({
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "google-api-key",
                    "display_name": str(item.get("displayName") or model_id),
                    "description": str(item.get("description") or ""),
                })
        token = str(data.get("nextPageToken") or "") if isinstance(data, dict) else ""
        if not token:
            break
    return found


async def _get_official_models(force: bool = False) -> dict[str, dict[str, Any]]:
    global _model_cache_time, _official_model_cache
    keys = key_pool.keys()
    if not keys:
        # Do not keep advertising stale models after the configured key source is removed.
        _official_model_cache = {}
        _model_cache_time = time.monotonic()
        return {}
    now = time.monotonic()
    if not force and _model_cache_time > 0 and now - _model_cache_time < 60:
        return dict(_official_model_cache)
    async with _model_cache_lock:
        now = time.monotonic()
        if not force and _model_cache_time > 0 and now - _model_cache_time < 60:
            return dict(_official_model_cache)
        collected: dict[str, dict[str, Any]] = {}
        async with _http_client() as client:
            results = await asyncio.gather(
                *(_fetch_models_for_key(client, key) for key in keys),
                return_exceptions=True,
            )
        ok_keys = 0
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Could not read model list from one Google API key: %s", result)
                continue
            ok_keys += 1
            for item in result:
                collected.setdefault(str(item["id"]), item)
        if ok_keys:
            # An authoritative empty list is still a successful result; remove stale entries.
            _official_model_cache = collected
            _model_cache_time = time.monotonic()
            return dict(collected)
        # If every key failed, clear the previous cache as well: never advertise
        # models that were not verified by the currently configured credentials.
        _official_model_cache = {}
        _model_cache_time = time.monotonic()
        return {}


async def _get_cookie_models(force: bool = False) -> dict[str, dict[str, Any]]:
    """Query each enabled Cookie account directly; never use the static fallback list."""
    global _cookie_model_cache, _cookie_model_cache_time
    now = time.monotonic()
    if not force and _cookie_model_cache_time > 0 and now - _cookie_model_cache_time < (60 if _cookie_model_cache else 10):
        return dict(_cookie_model_cache)

    async with _cookie_model_cache_lock:
        now = time.monotonic()
        if not force and _cookie_model_cache_time > 0 and now - _cookie_model_cache_time < (60 if _cookie_model_cache else 10):
            return dict(_cookie_model_cache)

        result: dict[str, dict[str, Any]] = {}
        try:
            from aistudio_api.infrastructure.account.account_store import AccountStore
            from aistudio_api.infrastructure.account.cookie_parser import load_cookies_from_auth_file
            from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

            store = AccountStore()
            enabled_accounts = [account for account in store.list_accounts() if account.enabled]
            semaphore = asyncio.Semaphore(5)

            async def fetch_account_models(account: Any) -> list[dict[str, Any]]:
                auth_path = store.get_auth_path_optional(account.id, require_exists=True)
                if auth_path is None:
                    return []
                cookies = load_cookies_from_auth_file(str(auth_path))
                if not cookies:
                    return []
                async with semaphore:
                    # _fetch_via_http returns only the live ListModels response. Unlike
                    # get_models(), it does not replace failures with _FALLBACK_MODELS.
                    return await model_discovery._fetch_via_http(
                        cookies,
                        str(account.auth_user or "0"),
                        tier=str(getattr(account, "tier", "Free") or "Free"),
                    )

            fetched = await asyncio.gather(
                *(fetch_account_models(account) for account in enabled_accounts),
                return_exceptions=True,
            )
            for account, account_models in zip(enabled_accounts, fetched):
                if isinstance(account_models, Exception):
                    logger.warning(
                        "Could not discover Cookie models for account %s: %s",
                        account.id,
                        type(account_models).__name__,
                    )
                    continue
                for item in account_models:
                    if not isinstance(item, Mapping):
                        continue
                    model_id = _clean_model_id(item.get("id") or item.get("name"))
                    if not _is_gemini_chat_model(model_id, item):
                        continue
                    if not _cookie_model_allowed_for_tier(item, getattr(account, "tier", "Free")):
                        continue
                    result.setdefault(model_id, {
                        "id": model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": "aistudio-cookie",
                        "display_name": str(item.get("displayName") or item.get("display_name") or model_id),
                        "description": str(item.get("description") or ""),
                    })
        except Exception as exc:
            logger.warning("Could not discover Cookie-account models: %s", type(exc).__name__)
            result = {}

        _cookie_model_cache = result
        _cookie_model_cache_time = time.monotonic()
        return dict(result)


async def _model_sources() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    official, cookie = await asyncio.gather(_get_official_models(), _get_cookie_models())
    return official, cookie


@router.get("/models")
async def list_openai_models() -> dict[str, Any]:
    official, cookie = await _model_sources()
    # Keep both credentials sources visible as distinct models, even when
    # they expose the same upstream Gemini model ID.
    visible: list[dict[str, Any]] = []
    for model_id in cookie:
        visible.append({
            "id": f"pg-{model_id}",
            "object": "model",
            "created": 0,
            "owned_by": "aistudio-cookie",
        })
    for model_id in official:
        visible.append({
            "id": f"ais-{model_id}",
            "object": "model",
            "created": 0,
            "owned_by": "google-ai-studio-api-key",
        })
    return {"object": "list", "data": sorted(visible, key=lambda item: str(item["id"]))}


def _content_to_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return [{"text": ""}]
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in ("text", "input_text", "output_text"):
                parts.append({"text": str(block.get("text") or "")})
            elif block_type in ("image_url", "input_image"):
                image = block.get("image_url") or block.get("image") or {}
                url = image.get("url") if isinstance(image, dict) else image
                if isinstance(url, str) and url.startswith("data:"):
                    header, sep, encoded = url.partition(",")
                    if sep and ";base64" in header:
                        mime = header[5:].split(";", 1)[0] or "image/png"
                        # Validate the data before forwarding it.
                        base64.b64decode(encoded, validate=True)
                        parts.append({"inlineData": {"mimeType": mime, "data": encoded}})
                elif isinstance(url, str) and url.startswith("https://"):
                    # Gemini FileData requires a Google-managed file URI, not an
                    # arbitrary public URL. Reject it rather than fetching arbitrary
                    # user-supplied URLs from the Render server (SSRF protection).
                    raise HTTPException(400, "Remote image URLs are not supported; send a base64 data URL.")
    if not parts:
        parts.append({"text": ""})
    return parts


def _openai_to_gemini(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty array")

    system_text: list[str] = []
    contents: list[dict[str, Any]] = []
    tool_name_by_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").lower()
        content = message.get("content")
        if role in ("system", "developer"):
            if content is not None:
                if isinstance(content, str):
                    system_text.append(content)
                else:
                    system_text.extend(str(p.get("text") or "") for p in content if isinstance(p, dict))
            continue

        parts: list[dict[str, Any]] = []
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            name = tool_name_by_id.get(call_id) or str(message.get("name") or call_id or "tool")
            try:
                tool_result: Any = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                tool_result = {"result": str(content or "")}
            parts.append({"functionResponse": {"name": name, "response": tool_result if isinstance(tool_result, (dict, list)) else {"result": tool_result}}})
            contents.append({"role": "user", "parts": parts})
            continue

        parts.extend(_content_to_parts(content))
        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "")
                if not name:
                    continue
                call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}")
                tool_name_by_id[call_id] = name
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    args = {}
                parts.append({"functionCall": {"name": name, "args": args, "id": call_id}})
            old_call = message.get("function_call")
            if isinstance(old_call, dict) and old_call.get("name"):
                try:
                    args = json.loads(old_call.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    args = {}
                parts.append({"functionCall": {"name": str(old_call["name"]), "args": args}})
            contents.append({"role": "model", "parts": parts})
        else:
            contents.append({"role": "user", "parts": parts})

    if not contents:
        contents = [{"role": "user", "parts": [{"text": ""}]}]

    gemini: dict[str, Any] = {"contents": contents}
    if system_text:
        gemini["systemInstruction"] = {"role": "user", "parts": [{"text": "\n\n".join(system_text)}]}

    generation: dict[str, Any] = {}
    for source, target in (
        ("temperature", "temperature"),
        ("top_p", "topP"),
        ("top_k", "topK"),
        ("stop", "stopSequences"),
        ("presence_penalty", "presencePenalty"),
        ("frequency_penalty", "frequencyPenalty"),
    ):
        value = payload.get(source)
        if value is not None:
            generation[target] = value if source != "stop" or isinstance(value, list) else [value]
    max_tokens = payload.get("max_completion_tokens", payload.get("max_tokens"))
    if max_tokens is not None:
        generation["maxOutputTokens"] = max_tokens
    response_format = payload.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") in ("json_object", "json_schema"):
        generation["responseMimeType"] = "application/json"
    if generation:
        gemini["generationConfig"] = generation

    raw_tools = payload.get("tools")
    declarations: list[dict[str, Any]] = []
    if isinstance(raw_tools, list):
        for tool in raw_tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                continue
            function = tool.get("function") or {}
            if not isinstance(function, dict) or not function.get("name"):
                continue
            declaration: dict[str, Any] = {"name": str(function["name"])}
            if function.get("description"):
                declaration["description"] = str(function["description"])
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                declaration["parameters"] = parameters
            declarations.append(declaration)
    if declarations:
        gemini["tools"] = [{"functionDeclarations": declarations}]
        tool_choice = payload.get("tool_choice")
        mode = "AUTO"
        if tool_choice == "none":
            mode = "NONE"
        elif tool_choice == "required":
            mode = "ANY"
        if mode != "AUTO":
            gemini["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
    return gemini


def _finish_reason(value: Any) -> str | None:
    raw = str(value or "").upper()
    if not raw or raw == "FINISH_REASON_UNSPECIFIED":
        return None
    if raw == "STOP":
        return "stop"
    if raw == "MAX_TOKENS":
        return "length"
    if raw in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"):
        return "content_filter"
    return "stop"


def _usage_to_openai(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(usage.get("promptTokenCount") or 0)
    completion = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    total = int(usage.get("totalTokenCount") or (prompt + completion))
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _gemini_to_openai(data: dict[str, Any], model: str, response_id: str | None = None) -> dict[str, Any]:
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        if isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict) and function_call.get("name"):
            call_id = str(function_call.get("id") or f"call_{uuid.uuid4().hex[:8]}")
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": str(function_call["name"]),
                    "arguments": json.dumps(function_call.get("args") or {}, ensure_ascii=False),
                },
            })
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish = _finish_reason(candidate.get("finishReason") or candidate.get("finish_reason"))
    return {
        "id": response_id or str(data.get("responseId") or f"chatcmpl-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish or "stop"}],
        "usage": _usage_to_openai(data.get("usageMetadata") or data.get("usage_metadata")),
    }


def _gemini_stream_event(data: dict[str, Any], model: str, response_id: str) -> str | None:
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return None
    candidate = candidates[0]
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    delta: dict[str, Any] = {}
    text = "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict) and part.get("thought") is not True
    )
    if text:
        delta["content"] = text
    finish = _finish_reason(candidate.get("finishReason") or candidate.get("finish_reason"))
    chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"


def _openai_sse_error(message: str, error_type: str = "api_error", code: str | None = None) -> str:
    return "data: " + json.dumps(_api_error(message, error_type, code), ensure_ascii=False) + "\n\n"


async def _official_nonstream(model: str, body: dict[str, Any], public_model: str | None = None) -> dict[str, Any]:
    attempted: set[str] = set()
    total_keys = len(key_pool.keys())
    if not total_keys:
        raise HTTPException(503, "No Google AI Studio API keys configured")
    encoded_model = quote(model, safe="-._~")
    url = f"{GOOGLE_API_ROOT}/models/{encoded_model}:generateContent"
    last_message = "All configured Google API keys failed"
    async with _http_client() as client:
        while (key := await key_pool.next_key(attempted)) is not None:
            attempted.add(key)
            index = len(attempted)
            try:
                response = await client.post(url, json=body, headers={"x-goog-api-key": key})
                if response.status_code < 400:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise RuntimeError("Google API returned a non-object response")
                    return _gemini_to_openai(data, public_model or model)
                last_message = f"Google API HTTP {response.status_code}: {_safe_error_text(response)}"
                logger.warning("Google API key attempt %d/%d failed (HTTP %d); trying next key", index, total_keys, response.status_code)
            except Exception as exc:
                last_message = f"Google API transport error ({type(exc).__name__})"
                logger.warning("Google API key attempt %d/%d failed; trying next key: %s", index, total_keys, type(exc).__name__)
    raise HTTPException(502, last_message)


async def _official_stream(model: str, body: dict[str, Any], public_model: str | None = None) -> StreamingResponse | JSONResponse:
    total_keys = len(key_pool.keys())
    if not total_keys:
        return JSONResponse(_api_error("No Google AI Studio API keys configured", "server_error"), status_code=503)
    encoded_model = quote(model, safe="-._~")
    url = f"{GOOGLE_API_ROOT}/models/{encoded_model}:streamGenerateContent"
    client = _http_client()
    upstream: httpx.Response | None = None
    last_message = "All configured Google API keys failed"
    attempted: set[str] = set()
    try:
        while (key := await key_pool.next_key(attempted)) is not None:
            attempted.add(key)
            index = len(attempted)
            try:
                request = client.build_request(
                    "POST", url, params={"alt": "sse"}, json=body,
                    headers={"x-goog-api-key": key},
                )
                upstream = await client.send(request, stream=True)
                if upstream.status_code < 400:
                    break
                error_body = await upstream.aread()
                last_message = f"Google API HTTP {upstream.status_code}: {_safe_error_text(httpx.Response(upstream.status_code, content=error_body))}"
                await upstream.aclose()
                upstream = None
                logger.warning("Google streaming key attempt %d/%d failed (HTTP error); trying next key", index, total_keys)
            except Exception as exc:
                last_message = f"Google API transport error ({type(exc).__name__})"
                if upstream is not None:
                    await upstream.aclose()
                    upstream = None
                logger.warning("Google streaming key attempt %d/%d failed; trying next key: %s", index, total_keys, type(exc).__name__)
        if upstream is None:
            await client.aclose()
            return JSONResponse(_api_error(last_message), status_code=502)
    except Exception:
        await client.aclose()
        raise

    response_id = f"chatcmpl-{uuid.uuid4().hex}"

    async def event_stream() -> AsyncIterator[str]:
        try:
            assert upstream is not None
            async for line in upstream.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    chunk = _gemini_stream_event(data, public_model or model, response_id)
                    if chunk:
                        yield chunk
            final_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": public_model or model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield "data: " + json.dumps(final_chunk, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            # Once output has started, an upstream stream cannot safely be replayed
            # with another key. Report the stream failure instead of duplicating text.
            logger.warning("Google stream ended unexpectedly: %s", type(exc).__name__)
            yield _openai_sse_error(f"Upstream stream interrupted: {exc}")
            yield "data: [DONE]\n\n"
        finally:
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _cookie_completion(model: str, gemini_body: dict[str, Any], stream: bool) -> Any:
    from aistudio_api.api.schemas import GeminiGenerateContentRequest
    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.api_service_gemini import handle_gemini_generate_content

    if runtime_state.client is None:
        raise HTTPException(503, "AI Studio Cookie client is not initialized")
    request_model = GeminiGenerateContentRequest.model_validate(gemini_body)
    # The upstream service accepts the Gemini REST model_path form (models/<id>),
    # not the bare OpenAI model id returned by /v1/models.
    return await handle_gemini_generate_content(f"models/{model}", request_model, runtime_state.client, stream=stream)


async def _collect_cookie_response(response: StreamingResponse) -> dict[str, Any]:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    text = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(502, "Cookie upstream returned an empty response")
    # Non-stream Gemini handler can become a keepalive StreamingResponse after 10s;
    # its final non-empty line is the actual JSON response.
    for candidate in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise HTTPException(502, "Could not decode Cookie upstream response")


async def _cookie_stream_response(response: StreamingResponse, model: str) -> StreamingResponse:
    response_id = f"chatcmpl-{uuid.uuid4().hex}"

    async def event_stream() -> AsyncIterator[str]:
        buffer = ""
        sent_any = False
        try:
            async for chunk in response.body_iterator:
                text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
                buffer += text
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    for line in event.splitlines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        if isinstance(data.get("error"), dict):
                            yield _openai_sse_error(str(data["error"].get("message") or "Cookie upstream error"))
                            yield "data: [DONE]\n\n"
                            return
                        mapped = _gemini_stream_event(data, model, response_id)
                        if mapped:
                            sent_any = True
                            yield mapped
            # Flush a final event without a blank-line terminator.
            if buffer.strip().startswith("data:"):
                try:
                    data = json.loads(buffer.strip()[5:].strip())
                    mapped = _gemini_stream_event(data, model, response_id) if isinstance(data, dict) else None
                    if mapped:
                        sent_any = True
                        yield mapped
                except json.JSONDecodeError:
                    pass
            final_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield "data: " + json.dumps(final_chunk, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.warning("Cookie OpenAI stream failed: %s", type(exc).__name__)
            yield _openai_sse_error(f"Cookie stream interrupted: {exc}")
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/completions")
async def create_chat_completion(request: Request) -> Any:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(_api_error("Request body must be valid JSON", "invalid_request_error"), status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse(_api_error("Request body must be a JSON object", "invalid_request_error"), status_code=400)

    public_model = _clean_model_id(payload.get("model"))
    if not public_model:
        return JSONResponse(_api_error("model is required", "invalid_request_error"), status_code=400)
    if public_model.startswith("pg-"):
        channel = "cookie"
        model = public_model[3:]
    elif public_model.startswith("ais-"):
        channel = "official"
        model = public_model[4:]
    else:
        return JSONResponse(
            _api_error("Model ID must start with pg- (Cookie) or ais- (official API key)", "invalid_request_error"),
            status_code=400,
        )
    if not model:
        return JSONResponse(_api_error("Invalid prefixed model ID", "invalid_request_error"), status_code=400)
    stream = bool(payload.get("stream"))
    try:
        gemini_body = _openai_to_gemini(payload)
    except HTTPException as exc:
        return JSONResponse(_api_error(str(exc.detail), "invalid_request_error"), status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse(_api_error(str(exc), "invalid_request_error"), status_code=400)

    official, cookie = await _model_sources()
    official_has_model = model in official
    cookie_has_model = model in cookie

    # The prefix selects exactly one credential channel. Never send a Cookie
    # model to the official API-key endpoint (or vice versa).
    if channel == "official":
        if not official_has_model:
            return JSONResponse(_api_error("This ais- model is not available from the configured official API keys", "invalid_request_error"), status_code=404)
        if stream:
            return await _official_stream(model, gemini_body, public_model=public_model)
        try:
            data = await _official_nonstream(model, gemini_body, public_model=public_model)
            return JSONResponse(data)
        except HTTPException as exc:
            return JSONResponse(_api_error(str(exc.detail)), status_code=exc.status_code)

    if channel == "cookie" and not cookie_has_model:
        return JSONResponse(_api_error("This pg- model is not available from the configured Cookie accounts", "invalid_request_error"), status_code=404)

    if cookie_has_model:
        try:
            cookie_result = await _cookie_completion(model, gemini_body, stream)
            if stream:
                if isinstance(cookie_result, StreamingResponse):
                    return await _cookie_stream_response(cookie_result, public_model)
                # The existing service normally returns a StreamingResponse for
                # stream=True; handle an unexpected object as a one-event stream.
                if hasattr(cookie_result, "model_dump"):
                    cookie_data = cookie_result.model_dump(mode="json", by_alias=True, exclude_none=True)
                else:
                    cookie_data = cookie_result if isinstance(cookie_result, dict) else {}
                mapped = _gemini_to_openai(cookie_data, public_model)
                return StreamingResponse(
                    iter(["data: " + json.dumps({"id": mapped["id"], "object": "chat.completion.chunk", "created": mapped["created"], "model": public_model, "choices": [{"index": 0, "delta": mapped["choices"][0]["message"], "finish_reason": mapped["choices"][0]["finish_reason"]}]}, ensure_ascii=False) + "\n\n", "data: [DONE]\n\n"]),
                    media_type="text/event-stream",
                )
            if isinstance(cookie_result, StreamingResponse):
                cookie_data = await _collect_cookie_response(cookie_result)
            elif hasattr(cookie_result, "model_dump"):
                cookie_data = cookie_result.model_dump(mode="json", by_alias=True, exclude_none=True)
            elif isinstance(cookie_result, dict):
                cookie_data = cookie_result
            else:
                raise HTTPException(502, "Unexpected Cookie upstream response")
            if isinstance(cookie_data.get("error"), dict):
                error = cookie_data["error"]
                return JSONResponse(_api_error(str(error.get("message") or "Cookie upstream error")), status_code=int(error.get("code") or 502))
            return JSONResponse(_gemini_to_openai(cookie_data, public_model))
        except HTTPException as exc:
            return JSONResponse(_api_error(str(exc.detail)), status_code=exc.status_code)
        except Exception as exc:
            logger.exception("Cookie-backed OpenAI request failed")
            return JSONResponse(_api_error(str(exc)), status_code=502)

    return JSONResponse(_api_error(f"Model '{model}' is not available from configured Cookie or Google API keys", "invalid_request_error", "model_not_found"), status_code=404)
