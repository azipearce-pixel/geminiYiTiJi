#!/usr/bin/env python3
"""Apply the OpenAI-compatible Render overlay to an aistudio-api source tree.

Run this script from the extracted project root (the folder containing main.py).
It is idempotent: it will not duplicate imports, startup hooks, or router mounts.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"[ERROR] {message}")
    raise SystemExit(1)


def insert_once(source: str, anchor: str, addition: str, description: str) -> str:
    if addition.strip() in source:
        return source
    if anchor not in source:
        fail(f"Could not find app.py insertion point for {description}. Is this the expected upstream revision?")
    return source.replace(anchor, anchor + addition, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    app_path = root / "src" / "aistudio_api" / "api" / "app.py"
    source_openai = root / "src" / "aistudio_api" / "api" / "routes_openai.py"
    source_secrets = root / "src" / "aistudio_api" / "api" / "render_secrets.py"

    if not (root / "main.py").is_file() or not app_path.is_file():
        fail("Extract this patch into the aistudio-api repository root (the directory containing main.py).")
    if not source_openai.is_file() or not source_secrets.is_file():
        fail("Patch source files are missing. Re-extract the full patch ZIP into the project root.")

    app = app_path.read_text(encoding="utf-8")
    app = insert_once(
        app,
        "from .routes_models import router as models_router\n",
        "from .routes_openai import router as openai_router\n",
        "OpenAI router import",
    )

    app = insert_once(
        app,
        "    account_store = AccountStore()\n",
        "    from aistudio_api.api.render_secrets import import_render_cookie\n"
        "    import_render_cookie(account_store)\n",
        "Render Cookie startup import",
    )

    app = insert_once(
        app,
        "app.include_router(models_router, dependencies=[Depends(require_api_key)])\n",
        "app.include_router(openai_router, prefix=\"/v1\", dependencies=[Depends(require_api_key)])\n",
        "OpenAI /v1 router mount",
    )

    # Keep /v1 requests out of the web-console SPA fallback.
    old = '            "/v1beta",\n'
    new = old + '            "/v1",\n'
    if '            "/v1",\n' not in app:
        if old not in app:
            fail("Could not find the SPA API-prefix list in app.py.")
        app = app.replace(old, new, 1)

    app_path.write_text(app, encoding="utf-8")
    print("[OK] OpenAI-compatible routes mounted at /v1")
    print("[OK] Render Cookie Secret File import wired into startup")
    print("[OK] Existing Gemini routes, management UI, health endpoint, and Cookie rotator are unchanged")
    print("\nNext: review README_部署说明.md, commit the changed files, and deploy on Render.")


if __name__ == "__main__":
    main()
