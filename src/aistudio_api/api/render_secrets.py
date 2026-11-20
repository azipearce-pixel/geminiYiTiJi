"""Load Render Secret File / environment-variable credentials into the app.

Secrets are never logged. The Cookie is imported into the existing AccountStore so
that the upstream project's Cookie rotation and account-selection code remains in
charge of AI Studio web-session requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("aistudio_api.render_secrets")


def _read_value_or_file(value_name: str, file_name: str) -> str | None:
    """Read a secret from an environment variable or a Render Secret File path."""
    file_path = (os.getenv(file_name) or "").strip()
    if file_path:
        path = Path(file_path)
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8-sig").strip()
            logger.warning("Secret File configured by %s was not found", file_name)
        except OSError as exc:
            logger.warning("Could not read Secret File configured by %s: %s", file_name, exc)

    value = os.getenv(value_name)
    return value.strip() if value and value.strip() else None


def _cookie_pairs_from_header(raw: str) -> list[dict[str, str]]:
    text = raw.strip()
    text = re.sub(r"^Cookie:\s*", "", text, flags=re.IGNORECASE)
    pairs: list[dict[str, str]] = []
    for token in text.split(";"):
        name, sep, value = token.strip().partition("=")
        if sep and name.strip() and value.strip():
            pairs.append({"name": name.strip(), "value": value.strip()})
    return pairs


def _normalize_cookie_data(raw: str) -> dict[str, Any]:
    """Accept Cookie-Editor JSON, storage-state JSON, a name/value JSON map,
    or a raw Cookie header and return Playwright-style storage state.
    """
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    cookies: Any = None
    if isinstance(parsed, dict):
        if isinstance(parsed.get("storageState"), dict):
            parsed = parsed["storageState"]
        if isinstance(parsed, dict):
            cookies = parsed.get("cookies")
            if cookies is None and all(isinstance(v, (str, int, float)) for v in parsed.values()):
                cookies = [{"name": str(k), "value": str(v)} for k, v in parsed.items()]
    elif isinstance(parsed, list):
        cookies = parsed

    normalized: list[dict[str, str]] = []
    if isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                normalized.append({"name": name, "value": value})

    if not normalized:
        normalized = _cookie_pairs_from_header(raw)

    if not normalized:
        raise ValueError(
            "Cookie data is not recognized. Use Cookie-Editor JSON export or a raw Cookie header."
        )

    # Preserve first occurrence only; later duplicate cookie names can be stale.
    deduped: dict[str, str] = {}
    for item in normalized:
        deduped[item["name"]] = item["value"]
    return {"cookies": [{"name": k, "value": v} for k, v in deduped.items()]}


def import_render_cookie(account_store: Any) -> None:
    """Import one or more Render Cookie Secret Files as separate accounts.

    Preferred multi-cookie configuration:
      AISTUDIO_COOKIE1_FILE=/etc/secrets/cookie1.json
      AISTUDIO_COOKIE2_FILE=/etc/secrets/cookie2.json

    The older AISTUDIO_COOKIE_FILE / AISTUDIO_COOKIE settings remain supported.
    Up to ten numbered Cookie files are accepted.
    """
    configured: list[tuple[str, str, str]] = []

    # Backward compatibility: the original single-cookie setting remains valid.
    legacy = _read_value_or_file("AISTUDIO_COOKIE", "AISTUDIO_COOKIE_FILE")
    if legacy:
        configured.append(("render_env_cookie", "Render Secret Cookie", legacy))

    # Numbered Secret Files become independent accounts in the existing store.
    for index in range(1, 11):
        file_env_names = (
            f"AISTUDIO_COOKIE{index}_FILE",
            f"AISTUDIO_COOKIE_{index}_FILE",
        )
        raw = None
        for file_env in file_env_names:
            path_value = (os.getenv(file_env) or "").strip()
            if path_value:
                try:
                    path = Path(path_value)
                    if path.is_file():
                        raw = path.read_text(encoding="utf-8-sig").strip()
                    else:
                        logger.warning("Cookie Secret File configured by %s was not found", file_env)
                except OSError as exc:
                    logger.warning("Could not read Cookie Secret File configured by %s: %s", file_env, exc)
                break
        # Also accept direct environment values, though Secret Files are preferred.
        if not raw:
            value = os.getenv(f"AISTUDIO_COOKIE{index}") or os.getenv(f"AISTUDIO_COOKIE_{index}")
            raw = value.strip() if value and value.strip() else None
        if raw:
            configured.append((f"render_env_cookie_{index}", f"Render Cookie {index}", raw))

    if not configured:
        logger.info("No Render Cookie secrets configured; leaving existing accounts unchanged")
        return

    tier = (os.getenv("AISTUDIO_COOKIE_TIER") or "Free").strip() or "Free"
    auth_user = (os.getenv("AISTUDIO_COOKIE_AUTH_USER") or "0").strip() or "0"
    imported = 0
    for account_id, default_name, raw in configured:
        try:
            storage_state = _normalize_cookie_data(raw)
            cookie_fingerprint = hashlib.sha256(
                json.dumps(storage_state, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:20]
            # Include the account slot in the cookie ID so two different Secret Files
            # are retained as separate accounts, even if their payloads happen to match.
            slot = account_id.rsplit("_", 1)[-1] if account_id != "render_env_cookie" else "legacy"
            account_store.save_account(
                name=default_name,
                email=None,
                storage_state=storage_state,
                account_id=account_id,
                auth_user=auth_user,
                cookie_id=f"render-{slot}-{cookie_fingerprint}",
                tier=tier,
                enabled=True,
                group="render-env",
                notes="Imported from Render Environment Variable / Secret File at startup",
            )
            imported += 1
        except Exception as exc:
            # Do not print the original value or parsed cookie payload.
            logger.error("Could not import %s: %s", default_name, exc)

    logger.info("Imported %d Render Cookie account(s)", imported)
