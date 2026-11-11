"""First-run setup and settings on the web page (M10, 2026-11-09).

`velocity web` starts even without a config: the page then shows a setup screen that
asks for the notes folder (suggesting Obsidian vaults it finds in the home folder), and
the settings (vault, OCR program, API defaults) can be changed later on the page. Saving
writes config.toml (config.save_settings) and swaps the running app for one built on the
new config, so no restart is needed (VelocityApp).

API keys are never stored: the page says how to set ANTHROPIC_API_KEY in a terminal. The
free engine's settings name the environment variable that holds its key, if it needs one.
"""

import contextlib
import dataclasses
import importlib.util
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from velocity import openai_engine
from velocity.config import (
    EFFORTS,
    MODEL_SHORT_NAMES,
    Config,
    ConfigError,
    config_path,
    load_config,
    save_settings,
)

MAX_VAULT_SEARCH_DEPTH = 3
FREE_ENGINE_SETTINGS = (
    "openai_base_url",
    "openai_model",
    "openai_api_key_env",
    "openai_context_size",
    "openai_images",
)


def checks(config: Config | None) -> list[dict]:
    """What velocity can use on this computer, each with how to fix it if missing."""
    out = []
    claude = shutil.which("claude")
    version = ""
    if claude:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            version = subprocess.run(
                [claude, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
    out.append(
        {
            "name": "Claude Code",
            "ok": bool(claude),
            "detail": version or ("installed" if claude else "not found"),
            "fix": "Install Claude Code to chat on your Claude plan: "
            "https://claude.com/claude-code",
        }
    )
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    out.append(
        {
            "name": "API key",
            "ok": key,
            "detail": "ANTHROPIC_API_KEY is set" if key else "not set",
            "fix": "For the API engine: run `export ANTHROPIC_API_KEY=...` in the "
            "terminal, then start velocity web from it. velocity never stores keys.",
        }
    )
    marker = importlib.util.find_spec("marker") is not None
    nvidia = shutil.which("nvidia-smi") is not None
    llama = (
        (config and config.llama_cpp_binary and config.llama_cpp_binary.is_file())
        or os.environ.get("LLAMA_CPP_BINARY")
        or shutil.which("llama-server")
    )
    ocr_ok = marker and (nvidia or bool(llama))
    if not marker:
        detail = "the OCR packages aren't installed"
    elif nvidia:
        detail = "ready (NVIDIA GPU)"
    elif llama:
        detail = "ready (llama.cpp)"
    else:
        detail = "no NVIDIA GPU and no llama.cpp program set"
    out.append(
        {
            "name": "Reading PDFs (OCR)",
            "ok": bool(ocr_ok),
            "detail": detail,
            "fix": "Needed to read PDF sections (math included). Install the OCR "
            "packages with `uv sync --group ocr`; without an NVIDIA GPU, also set "
            "the llama.cpp program in Settings. Looking at pages and searching work "
            "without it.",
        }
    )
    return out


def vault_suggestions(home: Path | None = None) -> list[str]:
    """Obsidian vaults (folders with a .obsidian folder) in the home folder, a few
    levels deep."""
    home = home or Path.home()
    found = []
    for root, dirs, _ in os.walk(home):
        depth = len(Path(root).relative_to(home).parts)
        if ".obsidian" in dirs:
            found.append(root)
        dirs[:] = (
            sorted(d for d in dirs if not d.startswith(".") and d != "node_modules")
            if depth < MAX_VAULT_SEARCH_DEPTH
            else []
        )
        if len(found) >= 10:
            break
    return found


def settings_json(config: Config | None, suggest: bool = False) -> dict:
    return {
        "configured": config is not None,
        "config_path": config_path().as_posix(),
        "vault_path": config.vault_path.as_posix() if config else None,
        "llama_cpp_binary": (
            config.llama_cpp_binary.as_posix()
            if config and config.llama_cpp_binary
            else ""
        ),
        "api_model": (config.api_model or "") if config else "",
        "api_effort": (config.api_effort or "") if config else "",
        # The free engine (2026-11-11): the server, its model, and the variable for a key.
        "openai_base_url": (config.openai_base_url or "") if config else "",
        "openai_model": (config.openai_model or "") if config else "",
        "openai_api_key_env": (config.openai_api_key_env or "") if config else "",
        "openai_context_size": (
            config.openai_context_size if config else Config.openai_context_size
        ),
        "openai_images": bool(config and config.openai_images),
        "openai_presets": openai_engine.PRESETS,
        "models": list(MODEL_SHORT_NAMES),
        "efforts": list(EFFORTS),
        "checks": checks(config),
        "suggestions": vault_suggestions() if suggest else [],
        "home": Path.home().as_posix(),
    }


def settings_routes(
    get_config: Callable[[], Config | None],
    on_saved: Callable | None,
    can_save: Callable[[], bool] = lambda: True,
) -> list[Route]:
    """GET/POST /api/settings, shared by the setup app and the full app. Saving rebuilds
    the app on the new config, which would pause an open chat: so not while one is open."""

    async def get(request: Request) -> Response:
        config = get_config()
        suggest = config is None or request.query_params.get("suggest") == "1"
        return JSONResponse(settings_json(config, suggest))

    async def post(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        config = get_config()
        changes = {
            k: body[k]
            for k in ("vault_path", "llama_cpp_binary", "api_model", "api_effort")
            + FREE_ENGINE_SETTINGS
            if k in body
        }
        new_vault = "vault_path" in changes and (
            config is None
            or Path(str(changes["vault_path"])).expanduser().resolve()
            != config.vault_path
        )
        if config is not None and not can_save():
            return JSONResponse(
                {"error": "End or pause the open chat before changing settings."}, 409
            )
        try:
            saved = save_settings(changes, create_vault=bool(body.get("create_vault")))
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        if on_saved:
            await on_saved(saved)
        return JSONResponse({**settings_json(saved), "reload": new_vault})

    async def test_model(request: Request) -> Response:
        """The free engine's Test, on the settings as typed (saved or not)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        base = get_config() or Config(Path.home())
        try:
            size = int(body.get("openai_context_size") or base.openai_context_size)
        except (TypeError, ValueError):
            size = base.openai_context_size
        trial = dataclasses.replace(
            base,
            openai_base_url=str(body.get("openai_base_url") or "").strip() or None,
            openai_model=str(body.get("openai_model") or "").strip() or None,
            openai_api_key_env=str(body.get("openai_api_key_env") or "").strip()
            or None,
            openai_context_size=size,
        )
        return JSONResponse(await openai_engine.check_model(trial))

    return [
        Route("/api/settings", get),
        Route("/api/settings", post, methods=["POST"]),
        Route("/api/settings/test-model", test_model, methods=["POST"]),
    ]


class VelocityApp:
    """What `velocity web` serves: the setup page until there is a config, then the full
    app, rebuilt when the settings change (another vault, say). The app being replaced
    is shut down properly (its open chats are paused)."""

    def __init__(self, config: Config | None, build_full, build_setup) -> None:
        self._build_full = build_full
        self._build_setup = build_setup
        self.config = config
        self.inner: Starlette = (
            build_full(config, self) if config else build_setup(self)
        )
        self._lifespan: contextlib.AsyncExitStack | None = None

    async def reload(self, config: Config) -> None:
        self.config = config
        new = self._build_full(config, self)
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(new.router.lifespan_context(new))
        old, self._lifespan, self.inner = self._lifespan, stack, new
        if old:
            await old.aclose()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "lifespan":
            await self.inner(scope, receive, send)
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self._lifespan = contextlib.AsyncExitStack()
                await self._lifespan.enter_async_context(
                    self.inner.router.lifespan_context(self.inner)
                )
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._lifespan:
                    await self._lifespan.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


def load_config_or_none() -> Config | None:
    """The config, or None when velocity isn't set up yet (no config, or its notes folder
    is gone): the page then shows the setup screen."""
    try:
        return load_config()
    except ConfigError:
        return None
