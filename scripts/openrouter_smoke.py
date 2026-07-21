"""Manual, credit-consuming OpenRouter structured-output smoke test."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import ConfigurationError, Settings  # noqa: E402
from src.llm_client import LLMClientError, LLMMessage, OpenRouterClient  # noqa: E402
from src.models import DiscoveryTurnResult  # noqa: E402


def main() -> int:
    """Run one explicitly requested provider call using synthetic fixture data."""
    print("WARNING: This manual script makes a real OpenRouter request and may consume credits.")
    settings = Settings.from_env()
    try:
        settings.require_api_configuration()
    except ConfigurationError as exc:
        print(f"Stopped safely: {exc}")
        return 2

    fixture_path = ROOT / "data" / "fixtures" / "marketing_intern.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    prompt = (ROOT / "prompts" / "discovery.md").read_text(encoding="utf-8")
    messages: list[LLMMessage] = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                "Extract a small DiscoveryTurnResult from this synthetic manager statement: "
                + fixture["initial_manager_statement"]
            ),
        },
    ]

    try:
        response = OpenRouterClient(settings).generate_structured(
            messages=messages,
            response_model=DiscoveryTurnResult,
            temperature=0.1,
        )
    except LLMClientError as exc:
        print(f"Request failed safely: {exc}")
        return 1

    print(response.data.model_dump_json(indent=2))
    print(
        json.dumps(
            {
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
