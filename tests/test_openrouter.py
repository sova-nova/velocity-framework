"""The Free model tab's Online steps (2026-11-12): OpenRouter's free models, asked on a
mock transport, and the message for a key whose privacy settings block free models."""

import asyncio

import httpx
import openai

from velocity import openrouter
from velocity.config import Config
from velocity.openai_engine import api_error


def model(id, prompt="0", completion="0", tools=True, context=262144, inputs=("text",)):
    return {
        "id": id,
        "name": id.split("/")[-1].title(),
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": ["tools", "temperature"] if tools else ["temperature"],
        "context_length": context,
        "architecture": {"input_modalities": list(inputs)},
    }


LISTING = [
    model("zeta/big:free", inputs=("text", "image")),
    model("acme/paid", prompt="0.000001", completion="0.000002"),
    model("acme/notools:free", tools=False),
    model("openrouter/free", context=200000),
    model("openrouter/auto", prompt="-1", completion="-1"),
    model("alpha/small:free", context=65536),
    {"id": "broken/pricing", "pricing": {"prompt": "n/a"}},
    "not a model",
]


def test_only_free_models_with_tools_router_first():
    found = openrouter.free_models_from(LISTING)
    assert [m["id"] for m in found] == [
        "openrouter/free",
        "zeta/big:free",  # by name: "Big:Free" before "Small:Free"
        "alpha/small:free",
    ]
    by_id = {m["id"]: m for m in found}
    assert by_id["zeta/big:free"]["images"] and not by_id["alpha/small:free"]["images"]
    # Huge contexts are capped; smaller ones kept.
    assert by_id["zeta/big:free"]["context"] == openrouter.MAX_CONTEXT
    assert by_id["alpha/small:free"]["context"] == 65536


def answering(status, body=None):
    def handler(request):
        assert str(request.url) == openrouter.MODELS_API
        assert "authorization" not in request.headers  # the list needs no key
        return httpx.Response(status, json=body or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_asking_openrouter():
    ok = asyncio.run(openrouter.free_models(answering(200, {"data": LISTING})))
    assert ok["models"][0]["id"] == "openrouter/free" and "error" not in ok
    down = asyncio.run(openrouter.free_models(answering(503)))
    assert down == {"models": [], "error": "OpenRouter answered 503."}
    empty = asyncio.run(openrouter.free_models(answering(200, {"data": []})))
    assert "no free models" in empty["error"]

    def refuse(request):
        raise httpx.ConnectError("offline")

    offline = asyncio.run(
        openrouter.free_models(httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    )
    assert "Couldn't reach OpenRouter" in offline["error"]


def test_privacy_settings_that_block_free_models_are_explained(tmp_path):
    config = Config(
        tmp_path, openai_base_url=openrouter.BASE_URL, openai_model="x:free"
    )
    request = httpx.Request("POST", f"{openrouter.BASE_URL}/chat/completions")
    blocked = openai.NotFoundError(
        "No endpoints found matching your data policy (Free model training)",
        response=httpx.Response(404, request=request),
        body=None,
    )
    assert openrouter.PRIVACY_PAGE in api_error(blocked, config)
    unknown = openai.NotFoundError(
        "Not found", response=httpx.Response(404, request=request), body=None
    )
    assert "doesn't know x:free" in api_error(unknown, config)
