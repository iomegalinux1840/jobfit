import pytest

from jobfit.providers import ProviderError, complete_profile_json, resolve_config


def test_openai_model_alias_is_normalized():
    assert resolve_config("openai", "openai/gpt-4.1-mini").model == "gpt-4.1-mini"


def test_provider_adapters_normalize_common_json_responses(monkeypatch):
    responses = {
        "openai": {"output_text": '{"skills": ["python"]}'},
        "xai": {"output_text": '{"skills": ["python"]}'},
        "openrouter": {"choices": [{"message": {"content": '{"skills": ["python"]}'}}]},
        "anthropic": {"content": [{"type": "text", "text": '{"skills": ["python"]}'}]},
        "gemini": {
            "candidates": [{"content": {"parts": [{"text": '{"skills": ["python"]}'}]}}]
        },
        "ollama": {"response": '{"skills": ["python"]}'},
    }
    env_by_provider = {
        "openai": "OPENAI_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    for env_var in env_by_provider.values():
        monkeypatch.setenv(env_var, "test-key")

    seen = []
    diagnostics = []

    def fake_post(url, headers, body, diagnostic=None):
        seen.append((url, headers, body))
        if "/api/generate" in url:
            provider = "ollama"
        elif "x.ai" in url:
            provider = "xai"
        elif "openrouter" in url:
            provider = "openrouter"
        elif "anthropic" in url:
            provider = "anthropic"
        elif "googleapis" in url:
            provider = "gemini"
        else:
            provider = "openai"
        return responses[provider]

    monkeypatch.setattr("jobfit.providers._post_json", fake_post)

    for provider in responses:
        result = complete_profile_json(
            "return profile JSON",
            resolve_config(provider, "test-model"),
            diagnostic=diagnostics.append,
        )
        assert result == {"skills": ["python"]}

    assert len(seen) == len(responses)
    openai_request = next(item for item in seen if "api.openai.com" in item[0])
    assert openai_request[2]["store"] is False
    assert sum("text extraction" in event for event in diagnostics) == len(responses)
    assert all("return profile JSON" not in event for event in diagnostics)


def test_chat_diagnostics_explain_empty_assistant_content_without_echoing_prompt(
    monkeypatch,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    events = []

    def fake_post(url, headers, body, diagnostic=None):
        return {
            "id": "response-id",
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning": "internal reasoning must not be used",
                    },
                }
            ],
        }

    monkeypatch.setattr("jobfit.providers._post_json", fake_post)

    with pytest.raises(ProviderError, match="no assistant content"):
        complete_profile_json(
            "PRIVATE RESUME CONTENT",
            resolve_config("openrouter", "test-model"),
            diagnostic=events.append,
        )

    joined = "\n".join(events)
    assert "choices,id,model" in joined
    assert "finish_reason=length" in joined
    assert "message_keys=[content,reasoning,role]" in joined
    assert "PRIVATE RESUME CONTENT" not in joined
    assert "internal reasoning must not be used" not in joined
