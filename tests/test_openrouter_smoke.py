"""Tests for the redacted Marketing Intern smoke-result fixture writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.openrouter_smoke import (
    generate_discovery_response,
    map_discovery_response,
    save_validated_fixture,
    validated_output,
)
from src.llm_client import InvalidStructuredOutputError, StructuredLLMResponse
from src.models import DiscoveryExtractionResponse, DiscoveryTurnResult, WorkflowStage


def compact_provider_response(
    recommendation: str = "advance",
) -> StructuredLLMResponse[DiscoveryExtractionResponse]:
    """Return a valid compact response with safe client metadata."""
    compact = DiscoveryExtractionResponse(
        incremental_requirements=[],
        assumptions=[
            {
                "statement": "The role may support campaign work.",
                "source_statement": "help with campaigns",
            }
        ],
        ambiguities=[
            {
                "description": "Campaign ownership is unclear.",
                "source_statement": "help with campaigns",
                "why_confirmation_is_needed": "Expected outputs are unknown.",
            }
        ],
        possible_contradictions=[],
        next_question="What campaign outcome should the intern support?",
        stage_recommendation=recommendation,
    )
    return StructuredLLMResponse(
        data=compact,
        model="openai/gpt-5.6-terra",
        provider="OpenAI",
        schema_mode="strict_json_schema",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        latency_ms=100,
        attempts=1,
    )


def test_smoke_explicitly_omits_temperature_through_the_shared_client() -> None:
    calls: list[dict[str, object]] = []
    response = compact_provider_response()

    class RecordingClient:
        def generate_structured(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return response

    result = generate_discovery_response(
        RecordingClient(),  # type: ignore[arg-type]
        [{"role": "user", "content": "Synthetic input"}],
    )

    assert result is response
    assert calls[0]["temperature"] is None
    assert calls[0]["response_model"] is DiscoveryExtractionResponse


def test_smoke_maps_compact_data_and_preserves_client_metadata() -> None:
    provider_response = compact_provider_response()

    mapped = map_discovery_response(
        provider_response, current_stage=WorkflowStage.BASIC_INFO
    )

    assert isinstance(mapped.data, DiscoveryTurnResult)
    assert mapped.data.assumptions[0].assumption_id == "assumption_001"
    assert mapped.data.assumptions[0].requires_confirmation is True
    assert mapped.model == provider_response.model
    assert mapped.provider == provider_response.provider
    assert mapped.total_tokens == provider_response.total_tokens
    assert mapped.latency_ms == provider_response.latency_ms
    assert mapped.attempts == provider_response.attempts


def test_smoke_mapping_failure_is_safe() -> None:
    with pytest.raises(InvalidStructuredOutputError) as exc_info:
        map_discovery_response(
            compact_provider_response("advance"),
            current_stage=WorkflowStage.COMPLETE,
        )

    assert exc_info.value.diagnostics is not None
    assert exc_info.value.diagnostics.category == "mapping_failed"
    assert exc_info.value.diagnostics.validation_stage == "domain_mapping"
    assert "advance" not in str(exc_info.value)
    assert "advance" not in json.dumps(exc_info.value.diagnostics.as_dict())


def test_validated_output_writes_only_structured_data_and_safe_metadata(
    tmp_path: Path,
) -> None:
    """A successful smoke result produces the reviewed fixture shape without secrets."""
    result = DiscoveryTurnResult(
        extracted_requirements=[],
        assumptions=[],
        ambiguities=[],
        contradictions=[],
        next_question={
            "question_id": "question_001",
            "question": "Which team will the intern support?",
            "target_stage": "basic_info",
            "purpose": "Clarify the immediate role context.",
        },
        confidence=0.8,
    )
    response = StructuredLLMResponse(
        data=result,
        model="openai/gpt-5.6-terra",
        provider="OpenAI",
        schema_mode="strict_json_schema",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        latency_ms=100,
        attempts=1,
    )

    output = validated_output(response, "configured/fallback-model")
    destination = tmp_path / "marketing_intern_initial_output.json"
    save_validated_fixture(output, destination)

    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved == output
    assert set(saved) == {
        "validated_discovery_turn_result",
        "model",
        "provider",
        "schema_mode",
        "token_usage",
        "latency_ms",
        "validation_status",
    }
    assert saved["model"] == "openai/gpt-5.6-terra"
    assert saved["provider"] == "OpenAI"
    assert saved["schema_mode"] == "strict_json_schema"
    assert saved["validation_status"] == "passed"
    assert "api_key" not in json.dumps(saved).lower()
    assert "authorization" not in json.dumps(saved).lower()
