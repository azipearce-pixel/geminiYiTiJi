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
    """Import the Render-provided Cookie as a stable managed account.

    Supported settings:
      AISTUDIO_COOKIE       direct environment variable
      AISTUDIO_COOKIE_FILE  path such as /etc/secrets/aistudio_cookie.json
    """
    raw = _read_value_or_file("AISTUDIO_COOKIE", "AISTUDIO_COOKIE_FILE")
    if not raw:
        logger.info("No Render Cookie secret configured; leaving existing accounts unchanged")
        return

    try:
        storage_state = _normalize_cookie_data(raw)
        cookie_fingerprint = hashlib.sha256(
            json.dumps(storage_state, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        tier = (os.getenv("AISTUDIO_COOKIE_TIER") or "Free").strip() or "Free"
        account_store.save_account(
            name=(os.getenv("AISTUDIO_COOKIE_NAME") or "Render Secret Cookie").strip(),
            email=None,
            storage_state=storage_state,
            account_id="render_env_cookie",
            auth_user=(os.getenv("AISTUDIO_COOKIE_AUTH_USER") or "0").strip() or "0",
            cookie_id=f"render-{cookie_fingerprint}",
            tier=tier,
            enabled=True,
            group="render-env",
            notes="Imported from Render Environment Variable / Secret File at startup",
        )
        logger.info("Imported Render Cookie into the existing account store")
    except Exception as exc:
        # Do not print the original value or parsed cookie payload.
        logger.error("Could not import Render Cookie secret: %s", exc)
