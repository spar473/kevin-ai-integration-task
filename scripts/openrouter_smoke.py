"""Manual, credit-consuming OpenRouter structured-output smoke test."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import ConfigurationError, Settings  # noqa: E402
from src.llm_client import (  # noqa: E402
    LLMClientError,
    LLMMessage,
    InvalidStructuredOutputError,
    LLMClient,
    OpenRouterClient,
    OpenRouterErrorDiagnostics,
    StructuredLLMResponse,
)
from src.models import (  # noqa: E402
    DiscoveryExtractionResponse,
    DiscoveryTurnResult,
    WorkflowStage,
)


def generate_discovery_response(
    client: LLMClient,
    messages: list[LLMMessage],
) -> StructuredLLMResponse[DiscoveryExtractionResponse]:
    """Use the shared structured client with temperature intentionally omitted."""
    return client.generate_structured(
        messages=messages,
        response_model=DiscoveryExtractionResponse,
        temperature=None,
    )


def map_discovery_response(
    response: StructuredLLMResponse[DiscoveryExtractionResponse],
    *,
    current_stage: WorkflowStage,
) -> StructuredLLMResponse[DiscoveryTurnResult]:
    """Map compact provider data while retaining client-side call metadata."""
    try:
        mapped_data = response.data.to_discovery_turn_result(
            current_stage=current_stage
        )
    except (ValidationError, ValueError, TypeError):
        diagnostics = OpenRouterErrorDiagnostics(
            category="mapping_failed",
            http_status=None,
            error_type="mapping_failed",
            provider_code=None,
            retryable=False,
            retry_after_seconds=None,
            attempted_model=response.model,
            validation_stage="domain_mapping",
        )
        raise InvalidStructuredOutputError(
            "Structured discovery response failed: mapping_failed.",
            diagnostics=diagnostics,
        ) from None
    return StructuredLLMResponse(
        data=mapped_data,
        model=response.model,
        provider=response.provider,
        schema_mode=response.schema_mode,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        latency_ms=response.latency_ms,
        attempts=response.attempts,
    )


def validated_output(
    response: StructuredLLMResponse[DiscoveryTurnResult],
    configured_model: str | None,
) -> dict[str, object]:
    """Build the reviewed, redacted result permitted in console output and fixtures."""
    return {
        "validated_discovery_turn_result": response.data.model_dump(mode="json"),
        "model": response.model or configured_model,
        "provider": response.provider,
        "schema_mode": response.schema_mode,
        "token_usage": {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens,
        },
        "latency_ms": response.latency_ms,
        "validation_status": "passed",
    }


def save_validated_fixture(output: dict[str, object], destination: Path) -> None:
    """Persist only validated structured data and safe call metadata."""
    destination.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    """Run one explicitly requested provider call using synthetic fixture data."""
    try:
        settings = Settings.from_env()
    except ValidationError:
        print(
            json.dumps(
                {
                    "validation_status": "not_run",
                    "configuration_issue": "Invalid non-secret OpenRouter configuration.",
                }
            )
        )
        return 2
    try:
        settings.require_api_configuration()
    except ConfigurationError as exc:
        print(
            json.dumps(
                {
                    "validation_status": "not_run",
                    "configuration_issue": str(exc),
                }
            )
        )
        return 2

    fixture_path = ROOT / "data" / "fixtures" / "marketing_intern.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    prompt = (ROOT / "prompts" / "discovery.md").read_text(encoding="utf-8")
    messages: list[LLMMessage] = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                "Extract a compact discovery update from this synthetic manager statement: "
                + fixture["initial_manager_statement"]
            ),
        },
    ]

    try:
        compact_response = generate_discovery_response(
            OpenRouterClient(settings), messages
        )
        response = map_discovery_response(
            compact_response, current_stage=WorkflowStage.BASIC_INFO
        )
    except LLMClientError as exc:
        diagnostics = exc.diagnostics.as_dict() if exc.diagnostics else None
        print(
            json.dumps(
                {
                    "validation_status": "failed",
                    "error": str(exc),
                    "diagnostics": diagnostics,
                }
            )
        )
        return 1

    output = validated_output(response, settings.openrouter_model)
    save_validated_fixture(
        output, ROOT / "data" / "fixtures" / "marketing_intern_initial_output.json"
    )
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
