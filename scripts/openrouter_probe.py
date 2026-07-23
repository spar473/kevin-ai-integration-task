"""Thin CLI for safe OpenRouter diagnostics implemented in ``src.llm_diagnostics``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.llm_diagnostics import (  # noqa: E402
    OpenRouterEndpointProbeClient,
    OpenRouterModelLookupClient,
    OpenRouterPreflightClient,
)


def _parse_args() -> argparse.Namespace:
    """Parse exactly one optional diagnostic mode."""
    parser = argparse.ArgumentParser(
        description="Safe, non-inference-by-default OpenRouter diagnostics."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="preflight",
        choices=[
            "preflight",
            "model-lookup",
            "endpoints",
            "chat",
            "responses",
            "structured",
            "structured-required",
        ],
    )
    parser.add_argument(
        "--discovery-schema",
        action="store_true",
        help="Probe the exact compact production discovery schema.",
    )
    parser.add_argument("--model", default=None, help="Override the model slug.")
    args = parser.parse_args()
    if args.discovery_schema and args.mode != "preflight":
        parser.error("--discovery-schema cannot be combined with a positional mode")
    return args


def _configuration_output(mode: str) -> dict[str, object]:
    """Return the selected mode's secret-free configuration failure shape."""
    if mode == "preflight":
        return {
            "authenticated": False,
            "error_type": "configuration",
            "credit_status": "unknown",
        }
    if mode == "model-lookup":
        return {"available": False, "error_type": "configuration"}
    if mode == "endpoints":
        return {
            "probe_type": "endpoints",
            "success": False,
            "endpoint_count": 0,
            "available_provider_names": [],
            "supports_response_format_or_structured_outputs": False,
            "http_status": None,
            "error_category": "configuration",
        }
    if mode == "discovery-schema":
        return {
            "probe_type": "discovery-schema",
            "success": False,
            "http_status": None,
            "error_category": "configuration",
            "returned_model": None,
            "total_provider_count": None,
            "available_provider_count": 0,
            "selected_provider_names": [],
            "excluded_provider_names": [],
            "routing_reason": None,
            "generation_occurred": False,
            "json_parsing_succeeded": False,
            "pydantic_validation_succeeded": False,
            "semantic_validation_succeeded": False,
            "mapping_succeeded": False,
            "schema_validation_succeeded": False,
            "validation_stage": None,
            "json_decoder_line": None,
            "json_decoder_column": None,
            "json_decoder_position": None,
            "response_character_count": None,
            "content_empty": None,
            "finish_reason": None,
            "validation_error_count": None,
            "validation_field_locations": [],
            "validation_error_types": [],
        }
    if mode in {"structured", "structured-required"}:
        return {
            "probe_type": mode,
            "success": False,
            "http_status": None,
            "error_category": "configuration",
            "returned_model": None,
            "valid_structured_json_received": False,
            "schema_validation_passed": False,
        }
    return {
        "probe_type": mode,
        "success": False,
        "http_status": None,
        "error_category": "configuration",
        "returned_model": None,
        "text_output_received": False,
    }


def main() -> int:
    """Delegate one selected diagnostic and print only its redacted result."""
    args = _parse_args()
    mode = "discovery-schema" if args.discovery_schema else args.mode
    try:
        settings = Settings.from_env()
    except ValidationError:
        print(json.dumps(_configuration_output(mode), indent=2))
        return 2

    if mode == "preflight":
        result = OpenRouterPreflightClient(settings).preflight()
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.authenticated else 1

    model = args.model or settings.openrouter_model
    if model is None:
        print(json.dumps(_configuration_output(mode), indent=2))
        return 2
    if mode == "model-lookup":
        result = OpenRouterModelLookupClient(settings).lookup(model)
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.available else 1

    client = OpenRouterEndpointProbeClient(settings)
    if mode == "endpoints":
        probe_result = client.inspect_endpoints(model)
    elif mode == "discovery-schema":
        probe_result = client.probe_discovery_schema(model)
    elif mode in {"structured", "structured-required"}:
        probe_result = client.probe_structured(mode, model)
    else:
        probe_result = client.probe_inference(mode, model)
    print(json.dumps(probe_result.as_dict(), indent=2))
    return 0 if probe_result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
