"""A free model without a terminal (decided 2026-11-11, desktop app PR 3): Settings finds
Ollama on this computer, and **Download model** pulls the suggested model through Ollama's
own API with progress, then creates velocity's copy of it with a 32K context built in.
That copy needs no OLLAMA_CONTEXT_LENGTH (Ollama's default context is too small for the
tutor's instructions and cuts them silently; see "the free engine" in DESIGN.md).

The download runs as a job in the server (one at a time), which the page polls; it
outlives an app rebuild after Save, and Cancel stops it (Ollama keeps what it has, so a
new download resumes).
"""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

SUGGESTED = "qwen3:4b"
CONTEXT = 32_768
DOWNLOAD_PAGE = "https://ollama.com/download"


def root_url() -> str:
    """Where Ollama listens: OLLAMA_HOST if set (as Ollama itself reads it), else its
    default."""
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host
    if host.count(":") < 2:  # no port
        host += ":11434"
    return host.rstrip("/")


def openai_url() -> str:
    return root_url() + "/v1"


def velocity_name(model: str) -> str:
    """velocity's copy of a model, with the context built in: qwen3:4b -> velocity-qwen3-4b."""
    return "velocity-" + model.replace(":", "-").replace("/", "-")


Client = Callable[
    [], Any
]  # -> an httpx.AsyncClient (tests pass one on a mock transport)


def _client(timeout: float | None = 5.0) -> Any:
    import httpx

    return httpx.AsyncClient(base_url=root_url(), timeout=timeout)


async def find(client: Client | None = None) -> dict:
    """Is Ollama running here, which version, and which models does it have?"""
    import httpx

    try:
        async with (client or _client)() as http:
            version = (await http.get("/api/version")).json().get("version")
            tags = (await http.get("/api/tags")).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return {"running": False, "version": None, "models": []}
    return {
        "running": True,
        "version": version,
        "models": sorted(m.get("name", "") for m in tags if m.get("name")),
    }


@dataclass
class Download:
    model: str
    phase: str = "starting"  # starting, downloading, preparing, done, failed, cancelled
    completed: int = 0  # bytes of the current part
    total: int = 0
    error: str = ""
    result: str = ""  # velocity's model, when done

    @property
    def finished(self) -> bool:
        return self.phase in ("done", "failed", "cancelled")


class Downloads:
    """The one download job (a model at a time) and its progress, for the page to poll."""

    def __init__(self, client: Client | None = None) -> None:
        self.client = client or (lambda: _client(timeout=None))
        self.current: Download | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict | None:
        return asdict(self.current) if self.current else None

    def start(self, model: str = SUGGESTED) -> Download:
        if self.current and not self.current.finished:
            raise RuntimeError("A model is already downloading.")
        self.current = Download(model)
        self._task = asyncio.create_task(self._run(self.current))
        return self.current

    async def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def wait(self) -> None:
        if self._task:
            await asyncio.shield(self._task)

    async def _run(self, job: Download) -> None:
        import httpx

        try:
            async with self.client() as http:
                await self._pull(http, job)
                job.phase = "preparing"
                job.completed = job.total = 0
                name = velocity_name(job.model)
                response = await http.post(
                    "/api/create",
                    json={
                        "model": name,
                        "from": job.model,
                        "parameters": {"num_ctx": CONTEXT},
                        "stream": False,
                    },
                )
                _raise_for(response)
                job.result = name
                job.phase = "done"
        except asyncio.CancelledError:
            job.phase = "cancelled"
            raise
        except httpx.ConnectError:
            job.phase, job.error = (
                "failed",
                "Ollama isn't running. Start it and try again.",
            )
        except (httpx.HTTPError, OllamaError, ValueError) as e:
            job.phase, job.error = "failed", str(e) or type(e).__name__

    @staticmethod
    async def _pull(http: Any, job: Download) -> None:
        async with http.stream(
            "POST", "/api/pull", json={"model": job.model, "stream": True}
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                _raise_for(response)
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise OllamaError(event["error"])
                if event.get("total"):
                    job.phase = "downloading"
                    job.total = event["total"]
                    job.completed = event.get("completed", 0)


class OllamaError(Exception):
    pass


def _raise_for(response: Any) -> None:
    if response.status_code < 400:
        return
    try:
        message = response.json().get("error")
    except ValueError:
        message = None
    raise OllamaError(message or f"Ollama answered {response.status_code}.")


# One job per server process: it outlives the app being rebuilt after Save.
downloads = Downloads()
