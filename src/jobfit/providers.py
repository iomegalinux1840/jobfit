"""Small, dependency-free adapters for popular LLM API shapes.

The provider boundary returns only a JSON object for profile extraction. The
rest of JobFit remains deterministic and provider-independent.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """A provider could not produce a valid profile response."""


Diagnostic = Callable[[str], None]


def _emit(diagnostic: Diagnostic | None, message: str) -> None:
    if diagnostic:
        diagnostic(f"LLM diagnostic: {message}")


def _endpoint(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "provider endpoint"


def _safe_keys(value: object) -> str:
    if not isinstance(value, dict):
        return type(value).__name__
    return ",".join(sorted(str(key) for key in value)) or "<none>"


def _safe_error_fields(error: object) -> str:
    if not isinstance(error, dict):
        return "error=<non-object>"
    fields = []
    for key in ("type", "code", "param"):
        value = error.get(key)
        if value not in (None, ""):
            fields.append(f"{key}={str(value)[:80]}")
    return ",".join(fields) or "error_fields=<none>"


def _response_overview(
    response: object, provider: str, diagnostic: Diagnostic | None
) -> None:
    if not isinstance(response, dict):
        _emit(
            diagnostic,
            f"{provider} response is {type(response).__name__}, expected object",
        )
        return
    details = [f"keys=[{_safe_keys(response)}]"]
    if "error" in response:
        details.append(_safe_error_fields(response["error"]))
    if "id" in response:
        details.append("id=present")
    if "model" in response:
        details.append("model=present")
    _emit(diagnostic, f"{provider} response received — " + " ".join(details))


def _text_result(
    provider: str,
    text: str,
    path: str,
    diagnostic: Diagnostic | None,
) -> str:
    text = text.strip()
    _emit(
        diagnostic,
        f"{provider} text extraction — path={path}, characters={len(text)}",
    )
    return text


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


def _post_json(
    url: str,
    headers: dict[str, str],
    body: dict,
    diagnostic: Diagnostic | None = None,
) -> dict:
    safe_url = _endpoint(url)
    key_present = bool(
        headers.get("Authorization") or headers.get("x-api-key") or "key=" in url
    )
    _emit(
        diagnostic,
        f"request prepared — endpoint={safe_url}, fields=[{_safe_keys(body)}], "
        f"api_key={'present' if key_present else 'not required'}",
    )
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
            elapsed = time.perf_counter() - started
            _emit(
                diagnostic,
                f"HTTP {response.status} received in {elapsed:.2f}s",
            )
            if not isinstance(payload, dict):
                raise ProviderError(
                    f"Provider returned {type(payload).__name__}; expected JSON object"
                )
            return payload
    except urllib.error.HTTPError as exc:
        raw_detail = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(raw_detail)
        except json.JSONDecodeError:
            error_payload = None
        _emit(
            diagnostic,
            f"HTTP {exc.code} received in {time.perf_counter() - started:.2f}s — "
            f"{_safe_error_fields(error_payload.get('error') if isinstance(error_payload, dict) else None)}",
        )
        detail = _safe_error_fields(
            error_payload.get("error") if isinstance(error_payload, dict) else None
        )
        raise ProviderError(
            f"Provider request failed at {safe_url}: HTTP {exc.code}: {detail}"
        ) from exc
    except (OSError, ValueError) as exc:
        _emit(
            diagnostic,
            f"transport or JSON error after {time.perf_counter() - started:.2f}s — "
            f"{type(exc).__name__}",
        )
        raise ProviderError(
            f"Could not call LLM provider at {safe_url}: {exc}"
        ) from exc


def _request_json(
    url: str,
    headers: dict[str, str],
    body: dict,
    diagnostic: Diagnostic | None,
) -> dict:
    """Keep the three-argument private adapter seam easy to monkeypatch in tests."""
    if diagnostic is None:
        return _post_json(url, headers, body)
    return _post_json(url, headers, body, diagnostic)


def _require_key(config: ProviderConfig) -> str:
    if not config.api_key:
        env_var = PROVIDER_SPECS[config.provider].api_key_env
        raise ProviderError(
            f"{provider_label(config.provider)} needs {env_var} in the environment"
        )
    return config.api_key


def _chat_completion(
    config: ProviderConfig,
    prompt: str,
    url: str,
    diagnostic: Diagnostic | None = None,
) -> str:
    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    response = _request_json(
        url,
        {"Authorization": f"Bearer {_require_key(config)}"},
        body,
        diagnostic,
    )
    _response_overview(response, config.provider, diagnostic)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(
            f"{provider_label(config.provider)} returned no choices "
            f"(response keys: {_safe_keys(response)})"
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError("Chat provider returned an invalid choice object")
    message = choice.get("message")
    finish_reason = choice.get("finish_reason", "unknown")
    if not isinstance(message, dict):
        raise ProviderError(
            f"{provider_label(config.provider)} returned no assistant message "
            f"(finish_reason={finish_reason})"
        )
    _emit(
        diagnostic,
        f"{config.provider} choice — finish_reason={finish_reason}, "
        f"message_keys=[{_safe_keys(message)}], "
        f"reasoning={'present' if message.get('reasoning') else 'absent'}",
    )
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return _text_result(
            config.provider, content, "choices[0].message.content", diagnostic
        )
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("text")
        ]
        text = "".join(parts)
        if text.strip():
            return _text_result(
                config.provider,
                text,
                "choices[0].message.content[*].text",
                diagnostic,
            )
    _emit(
        diagnostic,
        f"{config.provider} assistant content is empty; reasoning is not used as profile JSON",
    )
    raise ProviderError(
        f"{provider_label(config.provider)} returned no assistant content "
        f"(finish_reason={finish_reason}, message_keys={_safe_keys(message)})"
    )


def _responses_api(
    config: ProviderConfig,
    prompt: str,
    url: str,
    diagnostic: Diagnostic | None = None,
) -> str:
    response = _request_json(
        url,
        {"Authorization": f"Bearer {_require_key(config)}"},
        {
            "model": config.model,
            "input": prompt,
            "temperature": 0,
            "store": False,
        },
        diagnostic,
    )
    _response_overview(response, config.provider, diagnostic)
    if response.get("output_text"):
        return _text_result(
            config.provider, str(response["output_text"]), "output_text", diagnostic
        )
    parts = []
    output = response.get("output", [])
    item_types = []
    for item in output if isinstance(output, list) else []:
        if isinstance(item, dict):
            item_types.append(str(item.get("type", "unknown")))
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))
    _emit(
        diagnostic,
        f"{config.provider} output items={len(output) if isinstance(output, list) else 0}, "
        f"types=[{','.join(item_types) or '<none>'}], "
        f"incomplete_reason={response.get('incomplete_details', {}).get('reason') if isinstance(response.get('incomplete_details'), dict) else 'none'}",
    )
    if parts:
        return _text_result(
            config.provider, "".join(parts), "output[*].content[*].text", diagnostic
        )
    error = response.get("error")
    raise ProviderError(
        f"{provider_label(config.provider)} returned no output text "
        f"(response keys: {_safe_keys(response)}, {_safe_error_fields(error)})"
    )


def _anthropic(
    config: ProviderConfig, prompt: str, diagnostic: Diagnostic | None = None
) -> str:
    response = _request_json(
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
        diagnostic,
    )
    _response_overview(response, config.provider, diagnostic)
    content = response.get("content")
    text = (
        "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if isinstance(content, list)
        else ""
    )
    if text.strip():
        return _text_result(config.provider, text, "content[*].text", diagnostic)
    raise ProviderError(
        f"{provider_label(config.provider)} returned no message text "
        f"(content_type={type(content).__name__}, stop_reason={response.get('stop_reason', 'unknown')})"
    )


def _gemini(
    config: ProviderConfig, prompt: str, diagnostic: Diagnostic | None = None
) -> str:
    key = _require_key(config)
    model_path = (
        config.model if config.model.startswith("models/") else f"models/{config.model}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"{urllib.parse.quote(model_path, safe='/')}:generateContent?key={urllib.parse.quote(key)}"
    )
    response = _request_json(
        url,
        {},
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
        diagnostic,
    )
    _response_overview(response, config.provider, diagnostic)
    candidates = response.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    parts = (
        candidate.get("content", {}).get("parts", [])
        if isinstance(candidate, dict)
        else []
    )
    text = (
        "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("text")
        )
        if isinstance(parts, list)
        else ""
    )
    if text.strip():
        return _text_result(
            config.provider, text, "candidates[0].content.parts[*].text", diagnostic
        )
    finish = (
        candidate.get("finishReason", "unknown")
        if isinstance(candidate, dict)
        else "unknown"
    )
    _emit(diagnostic, f"{config.provider} candidate finish_reason={finish}")
    raise ProviderError(
        f"{provider_label(config.provider)} returned no candidate text "
        f"(finish_reason={finish}, response keys={_safe_keys(response)})"
    )


def _ollama(
    config: ProviderConfig, prompt: str, diagnostic: Diagnostic | None = None
) -> str:
    response = _request_json(
        config.ollama_host.rstrip("/") + "/api/generate",
        {},
        {"model": config.model, "prompt": prompt, "stream": False, "format": "json"},
        diagnostic,
    )
    _response_overview(response, config.provider, diagnostic)
    if response.get("response"):
        return _text_result(
            config.provider, str(response["response"]), "response", diagnostic
        )
    raise ProviderError(
        f"{provider_label(config.provider)} returned no response text "
        f"(done_reason={response.get('done_reason', 'unknown')})"
    )


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


def complete_profile_json(
    prompt: str, config: ProviderConfig, diagnostic: Diagnostic | None = None
) -> dict:
    _emit(
        diagnostic,
        f"call start — provider={config.provider}, model={config.model}, "
        f"prompt_characters={len(prompt)}",
    )
    if config.provider == "heuristic":
        raise ProviderError("Heuristic mode does not call an LLM")
    if config.provider == "ollama":
        text = _ollama(config, prompt, diagnostic)
    elif config.provider == "openai":
        text = _responses_api(
            config, prompt, "https://api.openai.com/v1/responses", diagnostic
        )
    elif config.provider == "xai":
        text = _responses_api(
            config, prompt, "https://api.x.ai/v1/responses", diagnostic
        )
    elif config.provider == "openrouter":
        text = _chat_completion(
            config, prompt, "https://openrouter.ai/api/v1/chat/completions", diagnostic
        )
    elif config.provider == "anthropic":
        text = _anthropic(config, prompt, diagnostic)
    elif config.provider == "gemini":
        text = _gemini(config, prompt, diagnostic)
    else:  # pragma: no cover - resolve_config prevents this
        raise ProviderError(f"Unsupported provider: {config.provider}")
    parsed = _parse_json(text)
    _emit(
        diagnostic, f"JSON parsed successfully — top_level_keys=[{_safe_keys(parsed)}]"
    )
    return parsed
