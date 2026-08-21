"""Small, dependency-free adapters for popular LLM API shapes.

The provider boundary returns only a JSON object for profile extraction. The
rest of JobFit remains deterministic and provider-independent.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """A provider could not produce a valid profile response."""


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    api_key_env: str | None
    default_model: str


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "heuristic": ProviderSpec("heuristic", "Heuristic (offline)", None, "offline"),
    "ollama": ProviderSpec("ollama", "Ollama (local)", None, "qwen3:8b"),
    "openai": ProviderSpec(
        "openai", "OpenAI Responses", "OPENAI_API_KEY", "gpt-4.1-mini"
    ),
    "openrouter": ProviderSpec(
        "openrouter", "OpenRouter Chat", "OPENROUTER_API_KEY", "openai/gpt-4.1-mini"
    ),
    "anthropic": ProviderSpec(
        "anthropic",
        "Anthropic Messages",
        "ANTHROPIC_API_KEY",
        "claude-3-5-haiku-latest",
    ),
    "xai": ProviderSpec("xai", "xAI Responses", "XAI_API_KEY", "grok-4.1-mini"),
    "gemini": ProviderSpec(
        "gemini", "Google Gemini", "GEMINI_API_KEY", "gemini-2.0-flash"
    ),
}


def provider_options() -> list[tuple[str, str]]:
    return [(spec.label, spec.key) for spec in PROVIDER_SPECS.values()]


def provider_label(provider: str) -> str:
    return PROVIDER_SPECS.get(provider, PROVIDER_SPECS["heuristic"]).label


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key: str | None = None
    ollama_host: str = "http://127.0.0.1:11434"


def resolve_config(provider: str, model: str | None = None) -> ProviderConfig:
    key = (provider or "heuristic").lower()
    if key not in PROVIDER_SPECS:
        raise ProviderError(
            f"Unknown LLM provider '{provider}'. Choose: {', '.join(PROVIDER_SPECS)}"
        )
    spec = PROVIDER_SPECS[key]
    selected_model = model or os.getenv("JOBFIT_MODEL") or spec.default_model
    if key == "openai" and selected_model.startswith("openai/"):
        selected_model = selected_model.removeprefix("openai/")
    api_key = os.getenv(spec.api_key_env) if spec.api_key_env else None
    return ProviderConfig(
        provider=key,
        model=selected_model,
        api_key=api_key,
        ollama_host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
    )


def _post_json(url: str, headers: dict[str, str], body: dict) -> dict:
    safe_url = urllib.parse.urlsplit(url).path or "provider endpoint"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300].replace("\n", " ")
        detail = re.sub(r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+\S+)", "[redacted]", detail)
        raise ProviderError(
            f"Provider request failed at {safe_url}: HTTP {exc.code}: {detail}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise ProviderError(
            f"Could not call LLM provider at {safe_url}: {exc}"
        ) from exc


def _require_key(config: ProviderConfig) -> str:
    if not config.api_key:
        env_var = PROVIDER_SPECS[config.provider].api_key_env
        raise ProviderError(
            f"{provider_label(config.provider)} needs {env_var} in the environment"
        )
    return config.api_key


def _chat_completion(config: ProviderConfig, prompt: str, url: str) -> str:
    response = _post_json(
        url,
        {"Authorization": f"Bearer {_require_key(config)}"},
        {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
    )
    try:
        content = response["choices"][0]["message"]["content"]
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content)
        return str(content)
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Chat provider returned no assistant text") from exc


def _responses_api(config: ProviderConfig, prompt: str, url: str) -> str:
    response = _post_json(
        url,
        {"Authorization": f"Bearer {_require_key(config)}"},
        {
            "model": config.model,
            "input": prompt,
            "temperature": 0,
            "store": False,
        },
    )
    if response.get("output_text"):
        return str(response["output_text"])
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("text"):
                parts.append(str(content["text"]))
    if parts:
        return "".join(parts)
    raise ProviderError("Responses provider returned no output text")


def _anthropic(config: ProviderConfig, prompt: str) -> str:
    response = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": _require_key(config),
            "anthropic-version": "2023-06-01",
        },
        {
            "model": config.model,
            "max_tokens": 2048,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    try:
        return "".join(str(block.get("text", "")) for block in response["content"])
    except (KeyError, TypeError) as exc:
        raise ProviderError("Anthropic returned no message text") from exc


def _gemini(config: ProviderConfig, prompt: str) -> str:
    key = _require_key(config)
    model_path = (
        config.model if config.model.startswith("models/") else f"models/{config.model}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"{urllib.parse.quote(model_path, safe='/')}:generateContent?key={urllib.parse.quote(key)}"
    )
    response = _post_json(
        url,
        {},
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
    )
    try:
        return "".join(
            str(part.get("text", ""))
            for part in response["candidates"][0]["content"]["parts"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Gemini returned no candidate text") from exc


def _ollama(config: ProviderConfig, prompt: str) -> str:
    response = _post_json(
        config.ollama_host.rstrip("/") + "/api/generate",
        {},
        {"model": config.model, "prompt": prompt, "stream": False, "format": "json"},
    )
    if response.get("response"):
        return str(response["response"])
    raise ProviderError("Ollama returned no response text")


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    fenced = re.search(
        r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("LLM response was not valid JSON") from exc
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as nested:
            raise ProviderError("LLM response was not valid JSON") from nested
    if not isinstance(value, dict):
        raise ProviderError("LLM response JSON must be an object")
    return value


def complete_profile_json(prompt: str, config: ProviderConfig) -> dict:
    if config.provider == "heuristic":
        raise ProviderError("Heuristic mode does not call an LLM")
    if config.provider == "ollama":
        text = _ollama(config, prompt)
    elif config.provider == "openai":
        text = _responses_api(config, prompt, "https://api.openai.com/v1/responses")
    elif config.provider == "xai":
        text = _responses_api(config, prompt, "https://api.x.ai/v1/responses")
    elif config.provider == "openrouter":
        text = _chat_completion(
            config, prompt, "https://openrouter.ai/api/v1/chat/completions"
        )
    elif config.provider == "anthropic":
        text = _anthropic(config, prompt)
    elif config.provider == "gemini":
        text = _gemini(config, prompt)
    else:  # pragma: no cover - resolve_config prevents this
        raise ProviderError(f"Unsupported provider: {config.provider}")
    return _parse_json(text)
