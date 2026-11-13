"""OpenRouter's free models, for the Free model tab's "Online" steps (decided 2026-11-12).

A learner who has never used OpenRouter shouldn't have to find a model id ending in
`:free`: velocity asks OpenRouter's public model list (no key needed) for the models that
cost nothing and can use tools, only when the learner reaches that step. `openrouter/free`,
OpenRouter's own router over its free models, comes first: it is the simplest choice and
doesn't go stale when free models come and go.
"""

BASE_URL = "https://openrouter.ai/api/v1"
MODELS_API = f"{BASE_URL}/models"
SIGN_UP_PAGE = "https://openrouter.ai/sign-up"
KEYS_PAGE = "https://openrouter.ai/settings/keys"
PRIVACY_PAGE = "https://openrouter.ai/settings/privacy"
ROUTER = "openrouter/free"
# Free models offer up to a million tokens; more than this only makes each request
# slower, and a tutoring session rarely needs it.
MAX_CONTEXT = 131_072


def _free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    try:
        return (
            float(pricing.get("prompt", 1)) == 0
            and float(pricing.get("completion", 1)) == 0
        )
    except (TypeError, ValueError):
        return False


def _entry(model: dict) -> dict:
    inputs = (model.get("architecture") or {}).get("input_modalities") or []
    try:
        context = int(model.get("context_length") or 0)
    except (TypeError, ValueError):
        context = 0
    return {
        "id": str(model["id"]),
        "name": str(model.get("name") or model["id"]),
        "context": min(context, MAX_CONTEXT) if context else MAX_CONTEXT // 4,
        "images": "image" in inputs,
    }


def free_models_from(data: list[dict]) -> list[dict]:
    """The free models that can use tools, OpenRouter's router first, then by name."""
    found = [
        _entry(m)
        for m in data
        if isinstance(m, dict)
        and m.get("id")
        and _free(m)
        and "tools" in (m.get("supported_parameters") or [])
    ]
    found.sort(key=lambda m: (m["id"] != ROUTER, m["name"].lower()))
    return found


async def free_models(client=None) -> dict:
    """{"models": [...]} or {"models": [], "error": "..."}; never raises."""
    import httpx

    try:
        async with client or httpx.AsyncClient(timeout=15) as http:
            response = await http.get(MODELS_API)
    except httpx.HTTPError:
        return {
            "models": [],
            "error": "Couldn't reach OpenRouter. Check your connection.",
        }
    if response.status_code != 200:
        return {"models": [], "error": f"OpenRouter answered {response.status_code}."}
    try:
        data = response.json().get("data") or []
    except (ValueError, AttributeError):
        return {"models": [], "error": "OpenRouter's answer wasn't readable."}
    models = free_models_from(data)
    if not models:
        return {"models": [], "error": "OpenRouter lists no free models right now."}
    return {"models": models}
