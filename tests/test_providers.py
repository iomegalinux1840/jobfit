from jobfit.providers import complete_profile_json, resolve_config


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

    def fake_post(url, headers, body):
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
            "return profile JSON", resolve_config(provider, "test-model")
        )
        assert result == {"skills": ["python"]}

    assert len(seen) == len(responses)
    openai_request = next(item for item in seen if "api.openai.com" in item[0])
    assert openai_request[2]["store"] is False
