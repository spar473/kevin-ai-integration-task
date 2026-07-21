"""Provider-isolated structured LLM client for manually triggered requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, Sequence, TypeVar, TypedDict

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from src.config import ConfigurationError, Settings


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class LLMMessage(TypedDict):
    """A supported system or user message."""

    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True, slots=True)
class StructuredLLMResponse(Generic[ResponseT]):
    """Validated data plus safe provider metadata when supplied."""

    data: ResponseT
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class LLMClient(Protocol):
    """Provider-neutral structured generation boundary."""

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[ResponseT],
        temperature: float = 0.1,
    ) -> StructuredLLMResponse[ResponseT]:
        """Generate and validate one structured response."""
        ...


class LLMClientError(RuntimeError):
    """Base error for safe LLM client failures."""


class LLMConfigurationError(LLMClientError):
    """Raised when a requested call lacks credentials or a model."""


class LLMTimeoutError(LLMClientError):
    """Raised when the provider request times out."""


class LLMProviderError(LLMClientError):
    """Raised when the provider rejects or fails a request."""


class InvalidStructuredOutputError(LLMClientError):
    """Raised when provider output is absent, malformed, or schema-invalid."""


class OpenRouterClient:
    """OpenRouter implementation using its OpenAI-compatible endpoint."""

    def __init__(self, settings: Settings, *, timeout: float = 30.0) -> None:
        self._settings = settings
        self._timeout = timeout

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[ResponseT],
        temperature: float = 0.1,
    ) -> StructuredLLMResponse[ResponseT]:
        """Make one explicit structured request and validate its JSON payload."""
        try:
            self._settings.require_api_configuration()
        except ConfigurationError as exc:
            raise LLMConfigurationError(str(exc)) from exc

        assert self._settings.openrouter_api_key is not None
        assert self._settings.openrouter_model is not None
        client = OpenAI(
            api_key=self._settings.openrouter_api_key.get_secret_value(),
            base_url=str(self._settings.openrouter_base_url),
            timeout=self._timeout,
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "strict": True,
                "schema": response_model.model_json_schema(),
            },
        }
        try:
            response = client.chat.completions.create(
                model=self._settings.openrouter_model,
                messages=list(messages),  # type: ignore[arg-type]
                temperature=temperature,
                response_format=response_format,  # type: ignore[arg-type]
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError("The OpenRouter request timed out.") from exc
        except APIError as exc:
            raise LLMProviderError(
                "OpenRouter could not complete the structured request."
            ) from exc

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise InvalidStructuredOutputError("Provider returned no structured content.")
        try:
            parsed = json.loads(content)
            data = response_model.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise InvalidStructuredOutputError(
                f"Provider output failed {response_model.__name__} validation."
            ) from exc

        usage = response.usage
        return StructuredLLMResponse(
            data=data,
            model=response.model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )
