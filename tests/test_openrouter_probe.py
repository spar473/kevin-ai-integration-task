"""Mocked coverage for every retained OpenRouter diagnostic mode."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import openrouter_probe
from src.config import Settings
from src.llm_client import OpenRouterClient, build_strict_json_schema
from src.llm_diagnostics import (
    OpenRouterEndpointProbeClient,
    OpenRouterModelLookupClient,
    OpenRouterPreflightClient,
)
from src.models import DiscoveryExtractionResponse


MODEL = "openai/gpt-5.6-terra"


def configured_settings(api_key: str = "test-openrouter-key") -> Settings:
    """Return test-only settings without loading local environment files."""
    return Settings.from_env(
        {
            "OPENROUTER_API_KEY": api_key,
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_MODEL": MODEL,
            "APP_ENV": "test",
        }
    )


def valid_discovery_payload() -> dict[str, object]:
    """Return the smallest useful compact provider discovery result."""
    return {
        "incremental_requirements": [],
        "assumptions": [
            {
                "statement": "The role may require collaboration.",
                "source_statement": "Synthetic input",
            }
        ],
        "ambiguities": [
            {
                "description": "The expected outcome is unknown.",
                "source_statement": "Synthetic input",
                "why_confirmation_is_needed": "Observable scope is required.",
            }
        ],
        "possible_contradictions": [],
        "next_question": "What observable outcome matters most?",
        "stage_recommendation": "advance",
    }


def test_preflight_reports_only_safe_current_key_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "is_management_key": False,
                    "label": "must-not-be-returned",
                    "expires_at": "2099-12-31T23:59:59Z",
                    "limit": 20,
                    "limit_remaining": 12,
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterPreflightClient(configured_settings()).preflight(
            http_client=client
        )
    finally:
        client.close()

    assert result.authenticated is True
    assert result.usable_for_inference is True
    assert result.credit_status == "available"
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/key"
    assert "must-not-be-returned" not in json.dumps(result.as_dict())


def test_model_lookup_uses_the_non_inference_route_and_reports_capabilities() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "id": MODEL,
                    "supported_parameters": [
                        "response_format",
                        "structured_outputs",
                    ],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterModelLookupClient(configured_settings()).lookup(
            MODEL, http_client=client
        )
    finally:
        client.close()

    assert result.available is True
    assert result.supports_response_format is True
    assert result.supports_structured_outputs is True
    assert str(requests[0].url) == (
        "https://openrouter.ai/api/v1/model/openai/gpt-5.6-terra"
    )
    assert "Authorization" not in requests[0].headers


def test_endpoints_mode_reports_provider_and_structured_capability_summary() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "endpoints": [
                        {
                            "provider_name": "OpenAI",
                            "supported_parameters": ["response_format"],
                        },
                        {
                            "provider_name": "Azure",
                            "supported_parameters": [],
                        },
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings()
        ).inspect_endpoints(MODEL, http_client=client)
    finally:
        client.close()

    assert result.success is True
    assert result.endpoint_count == 2
    assert result.available_provider_names == ("Azure", "OpenAI")
    assert result.supports_response_format_or_structured_outputs is True
    assert str(requests[0].url) == (
        "https://openrouter.ai/api/v1/models/openai/gpt-5.6-terra/endpoints"
    )


def test_chat_mode_posts_only_the_fixed_synthetic_message() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [{"message": {"content": "OK"}}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(configured_settings()).probe_inference(
            "chat", MODEL, http_client=client
        )
    finally:
        client.close()

    assert result.success is True
    assert result.text_output_received is True
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert json.loads(requests[0].content) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply exactly OK."}],
    }


def test_responses_mode_posts_only_the_fixed_synthetic_input() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "output": [
                    {"content": [{"type": "output_text", "text": "OK"}]}
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(configured_settings()).probe_inference(
            "responses", MODEL, http_client=client
        )
    finally:
        client.close()

    assert result.success is True
    assert result.text_output_received is True
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/responses"
    assert json.loads(requests[0].content) == {
        "model": MODEL,
        "input": "Reply exactly OK.",
    }


def test_structured_modes_differ_only_by_required_parameter_routing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [
                    {"message": {"content": '{"status":"ok","number":1}'}}
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    probe = OpenRouterEndpointProbeClient(configured_settings())
    try:
        basic = probe.probe_structured("structured", MODEL, http_client=client)
        required = probe.probe_structured(
            "structured-required", MODEL, http_client=client
        )
    finally:
        client.close()

    assert basic.success is True
    assert required.success is True
    basic_body = json.loads(requests[0].content)
    required_body = json.loads(requests[1].content)
    assert "provider" not in basic_body
    assert required_body["provider"] == {"require_parameters": True}
    del required_body["provider"]
    assert required_body == basic_body
    assert basic_body["stream"] is False
    assert basic_body["response_format"]["json_schema"]["strict"] is True


def test_probe_error_output_excludes_api_key_and_raw_provider_message() -> None:
    secret = "test-key-that-must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            request=request,
            json={
                "error": {
                    "message": f"raw provider message containing {secret}",
                    "metadata": {"error_type": "not_found"},
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings(secret)
        ).probe_structured("structured-required", MODEL, http_client=client)
    finally:
        client.close()

    rendered = json.dumps(result.as_dict())
    assert result.success is False
    assert result.diagnostics.category == "not_found"
    assert secret not in rendered
    assert "raw provider message" not in rendered


def test_discovery_schema_mode_reuses_the_exact_production_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(valid_discovery_payload())
                        }
                    }
                ],
                "openrouter_metadata": {
                    "summary": "2 available, selected OpenAI",
                    "endpoints": {
                        "total": 2,
                        "available": [
                            {"provider": "OpenAI", "selected": True},
                            {"provider": "Azure", "selected": False},
                        ],
                    },
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings()
        ).probe_discovery_schema(MODEL, http_client=client)
    finally:
        client.close()

    assert result.success is True
    assert result.returned_model == MODEL
    assert result.schema_validation_succeeded is True
    assert result.total_provider_count == 2
    assert result.available_provider_count == 2
    assert result.selected_provider_names == ("OpenAI",)
    assert result.excluded_provider_names == ("Azure",)
    assert result.routing_reason == "2 available, selected OpenAI"
    assert result.as_dict()["json_parsing_succeeded"] is True
    assert result.as_dict()["pydantic_validation_succeeded"] is True
    assert result.as_dict()["semantic_validation_succeeded"] is True
    assert result.as_dict()["mapping_succeeded"] is True
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["X-OpenRouter-Metadata"] == "enabled"
    body = json.loads(request.content)
    assert body == {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": "Return one assumption, one ambiguity, and one next question.",
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "DiscoveryExtractionResponse",
                "strict": True,
                "schema": build_strict_json_schema(DiscoveryExtractionResponse),
            },
        },
        "provider": {"require_parameters": True},
        "stream": False,
    }


def test_discovery_probe_and_production_client_share_the_temperature_free_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(valid_discovery_payload())
                        }
                    }
                ],
                "openrouter_metadata": {
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {"provider": "OpenAI", "selected": True}
                        ],
                    }
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = configured_settings()
    try:
        OpenRouterClient(settings, http_client=http_client).generate_structured(
            messages=[{"role": "user", "content": "Synthetic production input"}],
            response_model=DiscoveryExtractionResponse,
            temperature=None,
        )
        OpenRouterEndpointProbeClient(settings).probe_discovery_schema(
            MODEL, http_client=http_client
        )
    finally:
        http_client.close()

    production_body = json.loads(requests[0].content)
    probe_body = json.loads(requests[1].content)
    assert "temperature" not in production_body
    assert "temperature" not in probe_body
    production_body.pop("messages")
    probe_body.pop("messages")
    assert probe_body == production_body


def test_discovery_schema_mode_preserves_safe_router_error_metadata() -> None:
    secret = "probe-key-that-must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            request=request,
            json={
                "error": {
                    "message": f"unsafe response containing {secret}",
                    "metadata": {
                        "error_type": "not_found",
                        "provider_code": secret,
                    },
                },
                "openrouter_metadata": {
                    "summary": f"unsafe routing reason {secret}",
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {"provider": secret, "selected": False},
                            {"provider": "OpenAI", "selected": False},
                        ],
                    },
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings(secret)
        ).probe_discovery_schema(MODEL, http_client=client)
    finally:
        client.close()

    rendered = json.dumps(result.as_dict())
    assert result.success is False
    assert result.diagnostics.http_status == 404
    assert result.diagnostics.category == "not_found"
    assert result.diagnostics.provider_code is None
    assert result.total_provider_count == 1
    assert result.available_provider_count == 2
    assert result.selected_provider_names == ()
    assert result.excluded_provider_names == ("OpenAI",)
    assert result.routing_reason is None
    assert result.schema_validation_succeeded is False
    assert secret not in rendered
    assert "unsafe response" not in rendered
    assert "Authorization" not in rendered


def test_discovery_schema_mode_reports_schema_invalid_success_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [
                    {"message": {"content": '{"assumptions": []}'}}
                ],
                "openrouter_metadata": {
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {"provider": "OpenAI", "selected": True}
                        ],
                    }
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings()
        ).probe_discovery_schema(MODEL, http_client=client)
    finally:
        client.close()

    assert result.success is False
    assert result.diagnostics.category == "schema_validation_failed"
    assert result.diagnostics.http_status == 200
    assert result.diagnostics.validation_stage == "pydantic_schema"
    assert result.diagnostics.validation_error_count == 5
    assert set(result.diagnostics.validation_error_types) == {"missing"}
    assert result.as_dict()["json_parsing_succeeded"] is True
    assert result.as_dict()["pydantic_validation_succeeded"] is False
    assert result.schema_validation_succeeded is False
    assert result.selected_provider_names == ("OpenAI",)


def test_discovery_schema_probe_reports_malformed_json_without_content() -> None:
    generated_value = "private-generated-probe-value"
    content = '{"next_question":"' + generated_value + '", invalid}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "openrouter_metadata": {
                    "endpoints": {
                        "total": 1,
                        "available": [
                            {"provider": "OpenAI", "selected": True}
                        ],
                    }
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = OpenRouterEndpointProbeClient(
            configured_settings()
        ).probe_discovery_schema(MODEL, http_client=client)
    finally:
        client.close()

    rendered = json.dumps(result.as_dict())
    assert result.success is False
    assert result.diagnostics.category == "invalid_json"
    assert result.diagnostics.validation_stage == "json_decode"
    assert result.diagnostics.json_decoder_line == 1
    assert result.diagnostics.response_character_count == len(content)
    assert result.diagnostics.content_empty is False
    assert result.diagnostics.finish_reason == "stop"
    assert result.as_dict()["generation_occurred"] is True
    assert result.as_dict()["json_parsing_succeeded"] is False
    assert generated_value not in rendered


def test_discovery_schema_cli_flag_delegates_without_a_live_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = configured_settings()
    called_with: list[str] = []

    monkeypatch.setattr(
        "sys.argv", ["openrouter_probe.py", "--discovery-schema"]
    )
    monkeypatch.setattr(
        openrouter_probe.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )

    def fake_probe(
        self: OpenRouterEndpointProbeClient, model: str
    ) -> SimpleNamespace:
        called_with.append(model)
        return SimpleNamespace(
            success=True,
            as_dict=lambda: {
                "probe_type": "discovery-schema",
                "success": True,
                "http_status": 200,
                "error_category": None,
                "returned_model": MODEL,
                "total_provider_count": 1,
                "available_provider_count": 1,
                "selected_provider_names": ["OpenAI"],
                "excluded_provider_names": [],
                "routing_reason": "selected OpenAI",
                "schema_validation_succeeded": True,
            },
        )

    monkeypatch.setattr(
        OpenRouterEndpointProbeClient, "probe_discovery_schema", fake_probe
    )

    assert openrouter_probe.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["probe_type"] == "discovery-schema"
    assert output["schema_validation_succeeded"] is True
    assert called_with == [MODEL]
