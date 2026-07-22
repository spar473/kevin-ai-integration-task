"""Mocked direct-HTTP tests for the strict OpenRouter production client."""

from __future__ import annotations

import json
import traceback
from typing import Any, Callable

import httpx
import pytest
from pydantic import ValidationError

from src.config import Settings
from src.llm_client import (
    InvalidStructuredOutputError,
    LLMConfigurationError,
    LLMProviderError,
    LLMTimeoutError,
    OpenRouterClient,
)
from src.models import DiscoveryExtractionResponse, DiscoveryTurnResult, WorkflowStage


MODEL = "provider/structured-model"


def valid_discovery_payload() -> dict[str, Any]:
    """Return a schema-valid compact provider discovery result."""
    return {
        "incremental_requirements": [
            {
                "category": "domain",
                "name": "Social media familiarity",
                "description": "The relevant platforms and level remain unresolved.",
                "priority": "preferred",
                "rationale": "The manager explicitly mentioned social media capability.",
                "source_statement": "They should be creative and good with social media.",
            }
        ],
        "assumptions": [
            {
                "statement": "The role may involve TikTok-related collaboration.",
                "source_statement": "They'll work with the team on TikTok stuff",
            }
        ],
        "ambiguities": [
            {
                "description": "The team or brand has not been identified.",
                "source_statement": "Marketing Intern",
                "why_confirmation_is_needed": "The scope is unknown.",
            },
            {
                "description": "Creative work is not observable yet.",
                "source_statement": "They should be creative",
                "why_confirmation_is_needed": "Expected outputs are unknown.",
            },
            {
                "description": "Collaboration needs observable behaviour.",
                "source_statement": "Should be fun to work with.",
                "why_confirmation_is_needed": "Personality fit is not a requirement.",
            },
        ],
        "possible_contradictions": [],
        "next_question": "Which team or brand will this intern support?",
        "stage_recommendation": "stay",
    }


def configured_settings(api_key: str = "test-openrouter-key") -> Settings:
    """Build isolated test settings without reading environment files."""
    return Settings.from_env(
        {
            "OPENROUTER_API_KEY": api_key,
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_MODEL": MODEL,
            "APP_ENV": "test",
        }
    )


def success_response(
    request: httpx.Request,
    *,
    content: str | None = None,
    provider: str | None = "OpenAI",
) -> httpx.Response:
    """Build the documented chat-completion response fields the client consumes."""
    payload: dict[str, object] = {
        "model": MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content or json.dumps(valid_discovery_payload()),
                }
            }
        ],
        "usage": {
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "total_tokens": 168,
        },
    }
    if provider is not None:
        payload["provider"] = provider
    return httpx.Response(200, request=request, json=payload)


def run_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: Settings | None = None,
    max_attempts: int = 2,
    temperature: float | None = None,
) -> tuple[object, list[httpx.Request]]:
    """Run through an in-memory transport and return the result plus requests."""
    requests: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http_client = httpx.Client(transport=httpx.MockTransport(capture))
    try:
        result = OpenRouterClient(
            settings or configured_settings(),
            http_client=http_client,
            max_attempts=max_attempts,
        ).generate_structured(
            messages=[{"role": "user", "content": "Synthetic manager input"}],
            response_model=DiscoveryExtractionResponse,
            temperature=temperature,
        )
    finally:
        http_client.close()
    return result, requests


def test_successful_response_is_validated_with_safe_model_and_usage_metadata() -> None:
    result, requests = run_client(lambda request: success_response(request))

    assert isinstance(result.data, DiscoveryExtractionResponse)
    assert isinstance(
        result.data.to_discovery_turn_result(
            current_stage=WorkflowStage.BASIC_INFO
        ),
        DiscoveryTurnResult,
    )
    assert result.model == MODEL
    assert result.provider == "OpenAI"
    assert result.schema_mode == "strict_json_schema"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (
        123,
        45,
        168,
    )
    assert result.attempts == 1
    assert result.latency_ms >= 0
    assert len(requests) == 1


def test_compact_response_maps_deterministically_to_the_rich_domain_model() -> None:
    payload = valid_discovery_payload()
    payload["possible_contradictions"] = [
        {
            "description": "The requested responsibility may conflict with scope.",
            "source_statements": ["Support campaigns", "Do not own campaigns"],
        }
    ]
    compact = DiscoveryExtractionResponse.model_validate(payload)
    domain = compact.to_discovery_turn_result(
        current_stage=WorkflowStage.BASIC_INFO,
        source_turn_id="turn_001",
    )

    requirement = domain.extracted_requirements[0]
    assert requirement.requirement_id == "requirement_001"
    assert requirement.source_turn_id == "turn_001"
    assert requirement.requires_confirmation is True
    assert requirement.approved_by_human is False
    assert requirement.accepted_equivalents == []
    assert requirement.evidence_methods == []
    assert requirement.confidence is None
    assert domain.assumptions[0].assumption_id == "assumption_001"
    assert domain.assumptions[0].requires_confirmation is True
    assert domain.ambiguities[0].ambiguity_id == "ambiguity_001"
    assert domain.next_question.question_id == "question_001"
    assert domain.next_question.target_stage.value == "basic_info"
    assert domain.contradictions[0].contradiction_id == "contradiction_001"
    assert domain.contradictions[0].severity == "low"
    assert domain.contradictions[0].resolved is False
    assert domain.confidence is None


@pytest.mark.parametrize(
    "question",
    ["Which team? Which brand?", "Which team will this role support"],
)
def test_compact_response_requires_exactly_one_question(question: str) -> None:
    payload = valid_discovery_payload()
    payload["next_question"] = question

    compact = DiscoveryExtractionResponse.model_validate(payload)
    with pytest.raises(ValueError) as exc_info:
        compact.validate_semantics()
    assert "invalid_single_question" in {
        error_type for _, error_type in exc_info.value.issues
    }
    assert question not in str(exc_info.value)


def test_compact_response_validates_semantics_client_side() -> None:
    invalid_category = valid_discovery_payload()
    requirements = invalid_category["incremental_requirements"]
    assert isinstance(requirements, list)
    requirement = requirements[0]
    assert isinstance(requirement, dict)
    requirement["category"] = "personality_fit"
    compact = DiscoveryExtractionResponse.model_validate(invalid_category)
    with pytest.raises(ValueError) as category_error:
        compact.validate_semantics()
    assert category_error.value.issues == (
        ("incremental_requirements.0.category", "unsupported_category"),
    )
    assert "personality_fit" not in str(category_error.value)

    invalid_stage = valid_discovery_payload()
    invalid_stage["stage_recommendation"] = "automatic_rejection"
    compact = DiscoveryExtractionResponse.model_validate(invalid_stage)
    with pytest.raises(ValueError) as stage_error:
        compact.validate_semantics()
    assert stage_error.value.issues == (
        ("stage_recommendation", "unsupported_stage"),
    )
    assert "automatic_rejection" not in str(stage_error.value)


@pytest.mark.parametrize("recommendation", ["stay", "advance"])
def test_exact_progress_recommendations_are_accepted(recommendation: str) -> None:
    payload = valid_discovery_payload()
    payload["stage_recommendation"] = recommendation

    compact = DiscoveryExtractionResponse.model_validate(payload)

    assert compact.semantic_issues() == []


@pytest.mark.parametrize(
    "recommendation",
    ["basic_info", "arbitrary", "STAY", "ADVANCE", " stay", "advance "],
)
def test_non_exact_progress_recommendations_are_rejected(
    recommendation: str,
) -> None:
    payload = valid_discovery_payload()
    payload["stage_recommendation"] = recommendation
    compact = DiscoveryExtractionResponse.model_validate(payload)

    with pytest.raises(ValueError) as exc_info:
        compact.validate_semantics()

    assert exc_info.value.issues == (
        ("stage_recommendation", "unsupported_stage"),
    )
    assert recommendation not in str(exc_info.value)


@pytest.mark.parametrize(
    ("recommendation", "current_stage", "expected_stage"),
    [
        ("stay", WorkflowStage.SUCCESS_OUTCOMES, WorkflowStage.SUCCESS_OUTCOMES),
        ("advance", WorkflowStage.SUCCESS_OUTCOMES, WorkflowStage.RESPONSIBILITIES),
    ],
)
def test_progress_recommendation_maps_through_application_stage_order(
    recommendation: str,
    current_stage: WorkflowStage,
    expected_stage: WorkflowStage,
) -> None:
    payload = valid_discovery_payload()
    payload["stage_recommendation"] = recommendation
    compact = DiscoveryExtractionResponse.model_validate(payload)

    result = compact.to_discovery_turn_result(current_stage=current_stage)

    assert result.next_question.target_stage is expected_stage


def test_model_generated_workflow_stage_is_never_interpreted_as_transition() -> None:
    payload = valid_discovery_payload()
    payload["stage_recommendation"] = WorkflowStage.BUSINESS_NEED.value
    compact = DiscoveryExtractionResponse.model_validate(payload)

    with pytest.raises(ValueError) as exc_info:
        compact.to_discovery_turn_result(current_stage=WorkflowStage.BASIC_INFO)

    assert exc_info.value.issues == (
        ("stage_recommendation", "unsupported_stage"),
    )


def test_provider_schema_and_semantic_validation_boundary_is_explicit() -> None:
    payload = valid_discovery_payload()
    requirements = payload["incremental_requirements"]
    assert isinstance(requirements, list)
    requirement = requirements[0]
    assert isinstance(requirement, dict)
    requirement["name"] = ""
    requirement["priority"] = "future_priority"
    payload["stage_recommendation"] = "future_stage"
    payload["possible_contradictions"] = [
        {"description": "Possible conflict", "source_statements": ["one source"]}
    ]

    structurally_valid = DiscoveryExtractionResponse.model_validate(payload)
    issue_types = {
        error_type for _, error_type in structurally_valid.semantic_issues()
    }
    assert issue_types == {
        "empty_string",
        "insufficient_source_statements",
        "unsupported_priority",
        "unsupported_stage",
    }

    non_nullable_payload = valid_discovery_payload()
    non_nullable_payload["stage_recommendation"] = None
    with pytest.raises(ValidationError) as exc_info:
        DiscoveryExtractionResponse.model_validate(non_nullable_payload)
    assert exc_info.value.errors(include_input=False)[0]["type"] == "string_type"


def test_production_request_uses_exact_url_and_proven_strict_body() -> None:
    result, requests = run_client(lambda request: success_response(request))

    assert result.schema_mode == "strict_json_schema"
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["messages"] == [
        {"role": "user", "content": "Synthetic manager input"}
    ]
    assert "temperature" not in body
    assert body["stream"] is False
    assert body["provider"] == {"require_parameters": True}
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "DiscoveryExtractionResponse"
    schema = response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {
        "incremental_requirements",
        "assumptions",
        "ambiguities",
        "possible_contradictions",
        "next_question",
        "stage_recommendation",
    }
    serialized_schema = json.dumps(schema)
    assert '"anyOf"' not in serialized_schema
    assert '"$defs"' not in serialized_schema
    assert '"minItems"' not in serialized_schema
    assert '"minLength"' not in serialized_schema
    for excluded_field in (
        "approved_by_human",
        "assumption_id",
        "audit",
        "confidence",
        "created_at",
        "model",
        "question_id",
        "requirement_id",
        "source_turn_id",
        "updated_at",
    ):
        assert excluded_field not in serialized_schema

    enum_values: list[object] = []

    def collect_enums(value: object) -> None:
        if isinstance(value, dict):
            if "enum" in value:
                enum_values.append(value["enum"])
            for child in value.values():
                collect_enums(child)
        elif isinstance(value, list):
            for child in value:
                collect_enums(child)

    collect_enums(schema)
    assert enum_values == [
        ["technical", "domain", "behavioural", "logistical", "legal", "other"],
        ["must_have", "preferred", "optional"],
        ["stay", "advance"],
    ]
    assert schema["properties"]["stage_recommendation"]["enum"] == [
        "stay",
        "advance",
    ]
    requirement_item = schema["properties"]["incremental_requirements"]["items"]
    assert requirement_item["properties"]["category"]["enum"] == [
        "technical",
        "domain",
        "behavioural",
        "logistical",
        "legal",
        "other",
    ]
    assert requirement_item["properties"]["priority"]["enum"] == [
        "must_have",
        "preferred",
        "optional",
    ]

    def assert_closed(value: object) -> None:
        if isinstance(value, dict):
            assert "default" not in value
            assert "description" not in value
            assert "examples" not in value
            assert "title" not in value
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(properties)
            for key, child in value.items():
                if key == "properties" and isinstance(child, dict):
                    for property_schema in child.values():
                        assert_closed(property_schema)
                else:
                    assert_closed(child)
        elif isinstance(value, list):
            for child in value:
                assert_closed(child)

    assert_closed(response_format["json_schema"]["schema"])


def test_numeric_temperature_is_included_when_explicitly_configured() -> None:
    _, requests = run_client(
        lambda request: success_response(request), temperature=0.25
    )

    assert json.loads(requests[0].content)["temperature"] == 0.25


def test_missing_provider_metadata_is_safely_none() -> None:
    result, _ = run_client(
        lambda request: success_response(request, provider=None)
    )
    assert result.provider is None


@pytest.mark.parametrize(
    ("environment", "missing_name"),
    [
        (
            {"OPENROUTER_MODEL": MODEL, "APP_ENV": "test"},
            "OPENROUTER_API_KEY",
        ),
        (
            {"OPENROUTER_API_KEY": "test-key", "APP_ENV": "test"},
            "OPENROUTER_MODEL",
        ),
    ],
)
def test_missing_configuration_fails_before_any_request(
    environment: dict[str, str], missing_name: str
) -> None:
    with pytest.raises(LLMConfigurationError, match=missing_name):
        OpenRouterClient(Settings.from_env(environment)).generate_structured(
            messages=[{"role": "user", "content": "Synthetic manager input"}],
            response_model=DiscoveryExtractionResponse,
        )


def test_malformed_json_reports_decoder_location_without_content() -> None:
    generated_value = "private-generated-value"
    content = '{"next_question":"' + generated_value + '", invalid}'

    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        run_client(lambda request: success_response(request, content=content))

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.category == "invalid_json"
    assert diagnostics.validation_stage == "json_decode"
    assert diagnostics.json_decoder_line == 1
    assert diagnostics.json_decoder_column is not None
    assert diagnostics.json_decoder_position is not None
    assert diagnostics.response_character_count == len(content)
    assert diagnostics.content_empty is False
    assert diagnostics.finish_reason == "stop"
    rendered = (
        json.dumps(diagnostics.as_dict())
        + str(exc_info.value)
        + "".join(traceback.format_exception(exc_info.value))
    )
    assert generated_value not in rendered


@pytest.mark.parametrize(
    ("mutation", "expected_type", "expected_location"),
    [
        ("missing", "missing", "next_question"),
        ("extra", "extra_forbidden", "unknown_field"),
    ],
)
def test_pydantic_schema_failures_report_only_safe_details(
    mutation: str, expected_type: str, expected_location: str
) -> None:
    generated_value = "private-generated-value"
    payload = valid_discovery_payload()
    if mutation == "missing":
        del payload["next_question"]
    else:
        payload[generated_value] = generated_value

    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        run_client(
            lambda request: success_response(
                request, content=json.dumps(payload)
            )
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.category == "schema_validation_failed"
    assert diagnostics.validation_stage == "pydantic_schema"
    assert diagnostics.validation_error_count == 1
    assert diagnostics.validation_error_types == (expected_type,)
    assert diagnostics.validation_field_locations == (expected_location,)
    rendered = (
        json.dumps(diagnostics.as_dict())
        + str(exc_info.value)
        + "".join(traceback.format_exception(exc_info.value))
    )
    assert generated_value not in rendered
    assert "input" not in diagnostics.as_dict()


def test_semantic_failure_is_distinct_and_value_free() -> None:
    generated_value = "private-priority-value"
    payload = valid_discovery_payload()
    requirements = payload["incremental_requirements"]
    assert isinstance(requirements, list)
    requirement = requirements[0]
    assert isinstance(requirement, dict)
    requirement["priority"] = generated_value

    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        run_client(
            lambda request: success_response(
                request, content=json.dumps(payload)
            )
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.category == "semantic_validation_failed"
    assert diagnostics.validation_stage == "semantic_validation"
    assert diagnostics.validation_error_count == 1
    assert diagnostics.validation_field_locations == (
        "incremental_requirements.0.priority",
    )
    assert diagnostics.validation_error_types == ("unsupported_priority",)
    rendered = (
        json.dumps(diagnostics.as_dict())
        + str(exc_info.value)
        + "".join(traceback.format_exception(exc_info.value))
    )
    assert generated_value not in rendered


def test_rejected_stage_diagnostics_do_not_expose_generated_value() -> None:
    generated_value = "private-generated-stage"
    payload = valid_discovery_payload()
    payload["stage_recommendation"] = generated_value

    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        run_client(
            lambda request: success_response(
                request, content=json.dumps(payload)
            )
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.category == "semantic_validation_failed"
    assert diagnostics.validation_field_locations == ("stage_recommendation",)
    assert diagnostics.validation_error_types == ("unsupported_stage",)
    rendered = (
        json.dumps(diagnostics.as_dict())
        + str(exc_info.value)
        + "".join(traceback.format_exception(exc_info.value))
    )
    assert generated_value not in rendered


def test_missing_message_content_has_a_distinct_safe_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "model": MODEL,
                "choices": [{"message": {}, "finish_reason": "length"}],
            },
        )

    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        run_client(handler)

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.category == "missing_response_content"
    assert diagnostics.validation_stage == "content"
    assert diagnostics.response_character_count == 0
    assert diagnostics.content_empty is True
    assert diagnostics.finish_reason == "length"


def test_timeout_uses_only_the_bounded_retry_count() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMTimeoutError, match="bounded retries") as exc_info:
            OpenRouterClient(
                configured_settings(), http_client=http_client, max_attempts=2
            ).generate_structured(
                messages=[{"role": "user", "content": "Synthetic manager input"}],
                response_model=DiscoveryExtractionResponse,
            )
    finally:
        http_client.close()
    assert len(requests) == 2
    assert exc_info.value.diagnostics is not None
    assert exc_info.value.diagnostics.category == "timeout"


def test_retryable_http_error_retries_then_parses_success() -> None:
    responses = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal responses
        responses += 1
        if responses == 1:
            return httpx.Response(
                503,
                request=request,
                json={
                    "error": {
                        "metadata": {
                            "error_type": "provider_overloaded",
                            "provider_code": "overloaded",
                        }
                    }
                },
            )
        return success_response(request)

    result, requests = run_client(handler, max_attempts=2)
    assert result.attempts == 2
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("status", "error_type", "provider_code"),
    [
        (400, "invalid_request", "invalid_schema"),
        (401, "authentication", "invalid_api_key"),
        (402, "payment_required", "insufficient_credits"),
        (403, "permission_denied", "permission_denied"),
        (404, "not_found", "model_or_route_not_found"),
    ],
)
def test_non_retryable_errors_are_attempted_once_and_sanitized(
    status: int,
    error_type: str,
    provider_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "test-key-that-must-not-leak"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            request=request,
            json={
                "error": {
                    "message": f"raw provider text containing {secret}",
                    "metadata": {
                        "error_type": error_type,
                        "provider_code": provider_code,
                    },
                }
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMProviderError) as exc_info:
            OpenRouterClient(
                configured_settings(secret), http_client=http_client, max_attempts=2
            ).generate_structured(
                messages=[{"role": "user", "content": "Synthetic manager input"}],
                response_model=DiscoveryExtractionResponse,
            )
    finally:
        http_client.close()

    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.http_status == status
    assert diagnostics.error_type == error_type
    assert diagnostics.provider_code == provider_code
    assert diagnostics.retryable is False
    assert len(requests) == 1
    assert secret not in str(exc_info.value)
    assert secret not in "".join(traceback.format_exception(exc_info.value))
    assert secret not in caplog.text


def test_terminal_retryable_error_preserves_retry_after() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            429,
            request=request,
            headers={"Retry-After": "45"},
            json={
                "error": {
                    "metadata": {
                        "error_type": "rate_limit_exceeded",
                        "provider_code": "rate_limited",
                    }
                }
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMProviderError) as exc_info:
            OpenRouterClient(
                configured_settings(), http_client=http_client, max_attempts=2
            ).generate_structured(
                messages=[{"role": "user", "content": "Synthetic manager input"}],
                response_model=DiscoveryExtractionResponse,
            )
    finally:
        http_client.close()
    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.retryable is True
    assert diagnostics.retry_after_seconds == 45
    assert len(requests) == 2
