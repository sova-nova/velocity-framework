"""Ollama in Settings and Download model (ollama.py, desktop app PR 3): a fake Ollama on
httpx's mock transport."""

import asyncio
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from velocity import ollama
from velocity.ollama import CONTEXT, Downloads, find, velocity_name


class FakeOllama:
    def __init__(self, models=(), pull_lines=None, create_status=200) -> None:
        self.models = list(models)
        self.pull_lines = pull_lines or [
            {"status": "pulling manifest"},
            {
                "status": "pulling abc",
                "total": 2_500_000_000,
                "completed": 1_000_000_000,
            },
            {
                "status": "pulling abc",
                "total": 2_500_000_000,
                "completed": 2_500_000_000,
            },
            {"status": "success"},
        ]
        self.create_status = create_status
        self.requests: list[tuple[str, dict | None]] = []
        self.gate: asyncio.Event | None = None  # holds the pull open until set

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.10"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": m} for m in self.models]}
            )
        if request.url.path == "/api/pull":
            return httpx.Response(200, stream=self._pull_stream())
        if request.url.path == "/api/create":
            if self.create_status != 200:
                return httpx.Response(self.create_status, json={"error": "bad model"})
            self.models.append(f"{body['model']}:latest")
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404)

    def _pull_stream(self):
        fake = self

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for line in fake.pull_lines:
                    if fake.gate is not None:
                        await fake.gate.wait()
                    yield (json.dumps(line) + "\n").encode()

        return Stream()

    def client(self):
        return httpx.AsyncClient(
            base_url="http://ollama", transport=httpx.MockTransport(self.handler)
        )


def down():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    return httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(refuse)
    )


def test_find_ollama_or_not():
    fake = FakeOllama(models=["qwen3:4b"])
    assert asyncio.run(find(fake.client)) == {
        "running": True,
        "version": "0.12.10",
        "models": ["qwen3:4b"],
    }
    assert asyncio.run(find(down)) == {"running": False, "version": None, "models": []}


def test_download_pulls_then_makes_velocitys_copy_with_the_context():
    fake = FakeOllama()

    async def run():
        jobs = Downloads(fake.client)
        job = jobs.start("qwen3:4b")
        with pytest.raises(RuntimeError, match="already"):
            jobs.start("qwen3:4b")
        await jobs.wait()
        return job

    job = asyncio.run(run())
    assert job.phase == "done" and job.result == "velocity-qwen3-4b"
    assert ("/api/pull", {"model": "qwen3:4b", "stream": True}) in fake.requests
    create = next(b for p, b in fake.requests if p == "/api/create")
    assert create["from"] == "qwen3:4b" and create["model"] == velocity_name("qwen3:4b")
    assert create["parameters"] == {"num_ctx": CONTEXT}
    assert "velocity-qwen3-4b:latest" in fake.models


def test_download_progress_and_cancel():
    fake = FakeOllama()

    async def run():
        fake.gate = asyncio.Event()
        jobs = Downloads(fake.client)
        job = jobs.start("qwen3:4b")
        await asyncio.sleep(0.05)
        assert job.phase == "starting"  # nothing from Ollama yet
        fake.gate.set()
        fake.gate = None
        await asyncio.sleep(0)
        await jobs.cancel()
        return job

    job = asyncio.run(run())
    assert job.phase in ("cancelled", "done")


def test_download_failures_are_reported():
    error = FakeOllama(
        pull_lines=[{"error": "pull model manifest: file does not exist"}]
    )
    jobs = Downloads(error.client)

    async def run(jobs):
        job = jobs.start("nope:1b")
        await jobs.wait()
        return job

    job = asyncio.run(run(jobs))
    assert job.phase == "failed" and "does not exist" in job.error
    job = asyncio.run(run(Downloads(down)))
    assert job.phase == "failed" and "isn't running" in job.error
    job = asyncio.run(run(Downloads(FakeOllama(create_status=500).client)))
    assert job.phase == "failed" and job.error == "bad model"


def test_ollama_host_is_respected(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama.openai_url() == "http://localhost:11434/v1"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert ollama.root_url() == "http://0.0.0.0:11434"
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9999/")
    assert ollama.root_url() == "http://127.0.0.1:9999"


def test_settings_endpoints(monkeypatch):
    from velocity.setup import settings_routes

    fake = FakeOllama(models=["qwen3:4b"])
    monkeypatch.setattr(ollama, "_client", lambda timeout=5.0: fake.client())
    monkeypatch.setattr(ollama, "downloads", Downloads(fake.client))
    client = TestClient(Starlette(routes=settings_routes(lambda: None, None)))
    status = client.get("/api/ollama").json()
    assert status["running"] and not status["ready"] and status["download"] is None
    assert status["velocity_model"] == "velocity-qwen3-4b"
    assert status["base_url"].endswith("/v1") and status["context"] == CONTEXT

    started = client.post("/api/ollama/download")
    assert started.status_code == 200 and started.json()["model"] == "qwen3:4b"
    for _ in range(50):
        status = client.get("/api/ollama").json()
        if status["download"]["phase"] in ("done", "failed"):
            break
    assert status["download"]["phase"] == "done", status
    assert status["ready"]
