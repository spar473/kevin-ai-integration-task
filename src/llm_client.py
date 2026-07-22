"""Provider-isolated OpenRouter transport and strict structured-output client."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from time import perf_counter
from typing import Any, Generic, Literal, Protocol, Sequence, TypeVar, TypedDict
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from src.config import ConfigurationError, Settings


ResponseT = TypeVar("ResponseT", bound=BaseModel)
ProbeType = Literal["chat", "responses"]
StructuredProbeType = Literal["structured", "structured-required"]
SchemaMode = Literal["strict_json_schema"]

_SAFE_ERROR_CODE = re.compile(r"^[a-zA-Z0-9._:/-]{1,120}$")
_SAFE_PROVIDER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 .:/&+_-]{0,79}$")
_SAFE_ROUTING_REASON = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 .,:=_/+()&-]{0,199}$")
_SAFE_VALIDATION_LOCATION = re.compile(r"^[a-zA-Z0-9_.\[\]-]{1,240}$")
_TRANSIENT_ERROR_TYPES = {
    "connection_error",
    "provider_overloaded",
    "provider_unavailable",
    "rate_limit_exceeded",
    "server",
    "timeout",
}
_STATUS_CATEGORIES = {
    400: "invalid_request",
    401: "authentication",
    402: "payment_required",
    403: "permission_denied",
    404: "not_found",
    408: "timeout",
    429: "rate_limit_exceeded",
    500: "server",
    502: "provider_unavailable",
    503: "provider_unavailable",
    504: "timeout",
}


class LLMMessage(TypedDict):
    """A supported system or user message."""

    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True, slots=True)
class StructuredLLMResponse(Generic[ResponseT]):
    """Validated data with safe call metadata, never raw provider payloads."""

    data: ResponseT
    model: str | None
    provider: str | None
    schema_mode: SchemaMode
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    attempts: int


class LLMClient(Protocol):
    """Provider-neutral structured generation boundary."""

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[ResponseT],
        temperature: float | None = None,
    ) -> StructuredLLMResponse[ResponseT]:
        """Generate and validate one structured response."""
        ...


@dataclass(frozen=True, slots=True)
class OpenRouterErrorDiagnostics:
    """Safe OpenRouter error information without provider message text."""

    category: str
    http_status: int | None
    error_type: str | None
    provider_code: str | None
    retryable: bool
    retry_after_seconds: int | None
    attempted_model: str | None
    validation_stage: str | None = None
    json_decoder_line: int | None = None
    json_decoder_column: int | None = None
    json_decoder_position: int | None = None
    response_character_count: int | None = None
    content_empty: bool | None = None
    finish_reason: str | None = None
    validation_error_count: int | None = None
    validation_field_locations: tuple[str, ...] = ()
    validation_error_types: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return only reviewed fields suitable for redacted output."""
        return {
            "category": self.category,
            "http_status": self.http_status,
            "error_type": self.error_type,
            "provider_code": self.provider_code,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "attempted_model": self.attempted_model,
            "validation_stage": self.validation_stage,
            "json_decoder_line": self.json_decoder_line,
            "json_decoder_column": self.json_decoder_column,
            "json_decoder_position": self.json_decoder_position,
            "response_character_count": self.response_character_count,
            "content_empty": self.content_empty,
            "finish_reason": self.finish_reason,
            "validation_error_count": self.validation_error_count,
            "validation_field_locations": list(self.validation_field_locations),
            "validation_error_types": list(self.validation_error_types),
        }


class LLMClientError(RuntimeError):
    """Base error for safe LLM client failures."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: OpenRouterErrorDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class LLMConfigurationError(LLMClientError):
    """Raised when a requested call lacks valid credentials or a model."""


class LLMTimeoutError(LLMClientError):
    """Raised after all bounded provider attempts time out."""


class LLMProviderError(LLMClientError):
    """Raised when OpenRouter rejects or cannot serve a request."""


class InvalidStructuredOutputError(LLMClientError):
    """Raised when output is absent, malformed, or schema-invalid."""


def _safe_error_code(value: object) -> str | None:
    """Keep only compact code-like values, never arbitrary provider text."""
    if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return None


def _safe_model_slug(value: object) -> str | None:
    """Keep a model slug only when it is a compact identifier."""
    return _safe_error_code(value)


def _safe_provider_name(value: object) -> str | None:
    """Keep compact provider names while discarding arbitrary text."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_PROVIDER_NAME.fullmatch(candidate) else None


def _safe_routing_reason(value: object) -> str | None:
    """Keep a compact single-line router summary, never arbitrary response text."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_ROUTING_REASON.fullmatch(candidate) else None


def _safe_number(value: object) -> float | int | None:
    """Return finite numeric metadata without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _safe_token_count(value: object) -> int | None:
    """Return a non-negative integer token count only."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_retry_after(response: httpx.Response | None) -> int | None:
    """Read a bounded Retry-After value without exposing other headers."""
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return seconds if 0 <= seconds <= 86_400 else None


def _response_payload(response: httpx.Response | None) -> dict[str, object]:
    """Read JSON for reviewed extraction only; never retain raw response text."""
    if response is None:
        return {}
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_retryable(http_status: int | None, error_type: str | None) -> bool:
    """Identify only transient OpenRouter failure categories."""
    if error_type is not None:
        return error_type in _TRANSIENT_ERROR_TYPES
    return http_status in {408, 429, 500, 502, 503, 504}


def _diagnostics_from_payload(
    payload: dict[str, object],
    *,
    http_status: int | None,
    response: httpx.Response | None,
    attempted_model: str | None,
) -> OpenRouterErrorDiagnostics:
    """Extract stable error codes while excluding messages and raw metadata."""
    error = payload.get("error")
    error_data = error if isinstance(error, dict) else {}
    metadata = error_data.get("metadata")
    metadata_data = metadata if isinstance(metadata, dict) else {}
    error_type = _safe_error_code(
        metadata_data.get("error_type")
        or error_data.get("error_type")
        or payload.get("error_type")
    )
    provider_code = _safe_error_code(
        metadata_data.get("provider_code")
        or error_data.get("provider_code")
        or error_data.get("code")
    )
    category = error_type or _STATUS_CATEGORIES.get(http_status, "provider_error")
    return OpenRouterErrorDiagnostics(
        category=category,
        http_status=http_status,
        error_type=error_type,
        provider_code=provider_code,
        retryable=_is_retryable(http_status, error_type),
        retry_after_seconds=_safe_retry_after(response),
        attempted_model=attempted_model,
    )


def _safe_error_message(diagnostics: OpenRouterErrorDiagnostics) -> str:
    """Describe an error category without provider response text."""
    suffix = (
        f" (HTTP {diagnostics.http_status})"
        if diagnostics.http_status is not None
        else ""
    )
    return f"OpenRouter request failed: {diagnostics.category}{suffix}."


def _configuration_diagnostics(model: str | None) -> OpenRouterErrorDiagnostics:
    """Return a safe configuration failure."""
    return OpenRouterErrorDiagnostics(
        category="configuration",
        http_status=None,
        error_type="configuration",
        provider_code=None,
        retryable=False,
        retry_after_seconds=None,
        attempted_model=model,
    )


def _transport_diagnostics(
    category: Literal["timeout", "connection_error"], model: str | None
) -> OpenRouterErrorDiagnostics:
    """Return safe direct-transport failure metadata."""
    return OpenRouterErrorDiagnostics(
        category=category,
        http_status=None,
        error_type=category,
        provider_code=None,
        retryable=True,
        retry_after_seconds=None,
        attempted_model=model,
    )


def _inline_schema_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline non-recursive local definitions for provider compatibility."""
    definitions = schema.pop("$defs", {})
    if not isinstance(definitions, dict):
        return schema

    def resolve(value: object, resolving: tuple[str, ...] = ()) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.rsplit("/", 1)[-1]
                definition = definitions.get(name)
                if not isinstance(definition, dict) or name in resolving:
                    raise ValueError("provider schema must not contain recursive references")
                return resolve(deepcopy(definition), (*resolving, name))
            return {key: resolve(child, resolving) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, resolving) for child in value]
        return value

    resolved = resolve(schema)
    if not isinstance(resolved, dict):
        raise ValueError("provider schema root must be an object")
    return resolved


def build_strict_json_schema(response_model: type[BaseModel]) -> dict[str, Any]:
    """Return a compact strict schema with every object closed and required."""
    schema = deepcopy(response_model.model_json_schema())
    if getattr(response_model, "inline_provider_schema", False):
        schema = _inline_schema_definitions(schema)

    def close_objects(value: object) -> None:
        if isinstance(value, dict):
            for annotation in ("default", "description", "examples", "title"):
                value.pop(annotation, None)
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for key, child in value.items():
                if key == "properties" and isinstance(child, dict):
                    for property_schema in child.values():
                        close_objects(property_schema)
                else:
                    close_objects(child)
        elif isinstance(value, list):
            for child in value:
                close_objects(child)

    close_objects(schema)
    return schema


def _chat_completions_url(settings: Settings) -> str:
    """Build the exact OpenRouter chat-completions URL from the API base."""
    return f"{str(settings.openrouter_base_url).rstrip('/')}/chat/completions"


def _authorization_headers(
    settings: Settings, *, router_metadata: bool = False
) -> dict[str, str]:
    """Build request headers that are never returned or logged."""
    assert settings.openrouter_api_key is not None
    headers = {
        "Authorization": "Bearer " + settings.openrouter_api_key.get_secret_value(),
        "Content-Type": "application/json",
    }
    if router_metadata:
        headers["X-OpenRouter-Metadata"] = "enabled"
    return headers


def _strict_response_format(
    *, name: str, schema: dict[str, object]
) -> dict[str, object]:
    """Build OpenRouter's strict JSON Schema response-format object."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def _structured_request_body(
    *,
    model: str,
    messages: Sequence[LLMMessage],
    response_model: type[BaseModel],
    temperature: float | None,
) -> dict[str, object]:
    """Build the one production strict request shape shared by diagnostics."""
    body: dict[str, object] = {
        "model": model,
        "messages": [dict(message) for message in messages],
        "response_format": _strict_response_format(
            name=response_model.__name__,
            schema=build_strict_json_schema(response_model),
        ),
        "provider": {"require_parameters": True},
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = temperature
    return body


def _first_choice(payload: dict[str, object]) -> dict[str, object] | None:
    """Locate the first choice without retaining any response content."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    return choice if isinstance(choice, dict) else None


def _chat_content(payload: dict[str, object]) -> str | None:
    """Locate first-choice message content without logging or retaining it."""
    choice = _first_choice(payload)
    if choice is None:
        return None
    message = choice.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _safe_finish_reason(payload: dict[str, object]) -> str | None:
    """Return only a compact first-choice finish reason."""
    choice = _first_choice(payload)
    return _safe_error_code(choice.get("finish_reason")) if choice else None


def _schema_field_names(response_model: type[BaseModel]) -> set[str]:
    """Collect trusted model field names for redacted validation locations."""
    names: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(str(key) for key in properties)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(response_model.model_json_schema())
    return names


def _safe_pydantic_details(
    error: ValidationError, response_model: type[BaseModel]
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    """Reduce Pydantic errors to trusted locations and stable type codes."""
    allowed_fields = _schema_field_names(response_model)
    safe_locations: list[str] = []
    safe_types: list[str] = []
    errors = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    for item in errors:
        location_parts: list[str] = []
        location = item.get("loc")
        if isinstance(location, tuple):
            for part in location:
                if isinstance(part, int):
                    location_parts.append(f"[{part}]")
                elif isinstance(part, str) and part in allowed_fields:
                    location_parts.append(part)
                else:
                    location_parts.append("unknown_field")
        rendered_location = ".".join(location_parts) or "response_root"
        safe_locations.append(rendered_location)
        safe_types.append(_safe_error_code(item.get("type")) or "validation_error")
    return len(errors), tuple(safe_locations), tuple(safe_types)


def _safe_semantic_details(
    error: ValueError,
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    """Read only application-owned semantic locations and stable codes."""
    issues = getattr(error, "issues", ())
    locations: list[str] = []
    error_types: list[str] = []
    if isinstance(issues, tuple):
        for issue in issues:
            if not isinstance(issue, tuple) or len(issue) != 2:
                continue
            location, error_type = issue
            if not isinstance(location, str) or not _SAFE_VALIDATION_LOCATION.fullmatch(
                location
            ):
                location = "response_root"
            locations.append(location)
            error_types.append(_safe_error_code(error_type) or "semantic_error")
    return len(locations), tuple(locations), tuple(error_types)


def _structured_output_diagnostics(
    category: str,
    *,
    stage: str,
    attempted_model: str,
    http_status: int | None,
    content: str | None,
    finish_reason: str | None,
    json_error: json.JSONDecodeError | None = None,
    validation_details: tuple[int, tuple[str, ...], tuple[str, ...]] | None = None,
) -> OpenRouterErrorDiagnostics:
    """Build value-free diagnostics for one structured-response stage."""
    error_count, locations, error_types = validation_details or (None, (), ())
    return OpenRouterErrorDiagnostics(
        category=category,
        http_status=http_status,
        error_type=category,
        provider_code=None,
        retryable=False,
        retry_after_seconds=None,
        attempted_model=attempted_model,
        validation_stage=stage,
        json_decoder_line=json_error.lineno if json_error else None,
        json_decoder_column=json_error.colno if json_error else None,
        json_decoder_position=json_error.pos if json_error else None,
        response_character_count=len(content) if isinstance(content, str) else 0,
        content_empty=not bool(content and content.strip()),
        finish_reason=finish_reason,
        validation_error_count=error_count,
        validation_field_locations=locations,
        validation_error_types=error_types,
    )


def _parse_structured_payload(
    payload: dict[str, object],
    response_model: type[ResponseT],
    attempted_model: str,
    *,
    http_status: int | None = None,
) -> ResponseT:
    """Run explicit content, JSON, schema, and semantic validation stages."""
    choice = _first_choice(payload)
    if choice is not None:
        choice_error = choice.get("error")
        if isinstance(choice_error, dict):
            diagnostics = _diagnostics_from_payload(
                {"error": choice_error},
                http_status=http_status,
                response=None,
                attempted_model=attempted_model,
            )
            raise LLMProviderError(
                _safe_error_message(diagnostics), diagnostics=diagnostics
            )

    finish_reason = _safe_finish_reason(payload)
    content = _chat_content(payload)
    if not content or not content.strip():
        diagnostics = _structured_output_diagnostics(
            "missing_response_content",
            stage="content",
            attempted_model=attempted_model,
            http_status=http_status,
            content=content,
            finish_reason=finish_reason,
        )
        raise InvalidStructuredOutputError(
            _safe_error_message(diagnostics), diagnostics=diagnostics
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        diagnostics = _structured_output_diagnostics(
            "invalid_json",
            stage="json_decode",
            attempted_model=attempted_model,
            http_status=http_status,
            content=content,
            finish_reason=finish_reason,
            json_error=exc,
        )
        raise InvalidStructuredOutputError(
            _safe_error_message(diagnostics), diagnostics=diagnostics
        ) from None
    try:
        validated = response_model.model_validate(parsed)
    except ValidationError as exc:
        diagnostics = _structured_output_diagnostics(
            "schema_validation_failed",
            stage="pydantic_schema",
            attempted_model=attempted_model,
            http_status=http_status,
            content=content,
            finish_reason=finish_reason,
            validation_details=_safe_pydantic_details(exc, response_model),
        )
        raise InvalidStructuredOutputError(
            _safe_error_message(diagnostics), diagnostics=diagnostics
        ) from None

    semantic_validator = getattr(validated, "validate_semantics", None)
    if callable(semantic_validator):
        try:
            semantic_validator()
        except ValueError as exc:
            diagnostics = _structured_output_diagnostics(
                "semantic_validation_failed",
                stage="semantic_validation",
                attempted_model=attempted_model,
                http_status=http_status,
                content=content,
                finish_reason=finish_reason,
                validation_details=_safe_semantic_details(exc),
            )
            raise InvalidStructuredOutputError(
                _safe_error_message(diagnostics), diagnostics=diagnostics
            ) from None
    return validated


class OpenRouterClient:
    """Direct strict-structured OpenRouter implementation of ``LLMClient``."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 30.0,
        max_attempts: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self._settings = settings
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._http_client = http_client

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[ResponseT],
        temperature: float | None = None,
    ) -> StructuredLLMResponse[ResponseT]:
        """POST one strict request shape and validate its response with Pydantic."""
        try:
            self._settings.require_api_configuration()
        except ConfigurationError as exc:
            raise LLMConfigurationError(str(exc)) from None

        attempted_model = _safe_model_slug(self._settings.openrouter_model)
        if attempted_model is None:
            raise LLMConfigurationError(
                "OPENROUTER_MODEL must be a compact provider/model slug."
            )

        body = _structured_request_body(
            model=attempted_model,
            messages=messages,
            response_model=response_model,
            temperature=temperature,
        )
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self._timeout)
        started_at = perf_counter()
        try:
            response, attempts = self._post_with_retries(
                client, body=body, attempted_model=attempted_model
            )
            payload = _response_payload(response)
            data = _parse_structured_payload(
                payload,
                response_model,
                attempted_model,
                http_status=response.status_code,
            )
        finally:
            if owns_client:
                client.close()

        usage = payload.get("usage")
        usage_data = usage if isinstance(usage, dict) else {}
        return StructuredLLMResponse(
            data=data,
            model=_safe_model_slug(payload.get("model")),
            provider=_safe_provider_name(payload.get("provider")),
            schema_mode="strict_json_schema",
            input_tokens=_safe_token_count(usage_data.get("prompt_tokens")),
            output_tokens=_safe_token_count(usage_data.get("completion_tokens")),
            total_tokens=_safe_token_count(usage_data.get("total_tokens")),
            latency_ms=round((perf_counter() - started_at) * 1000),
            attempts=attempts,
        )

    def _post_with_retries(
        self,
        client: httpx.Client,
        *,
        body: dict[str, object],
        attempted_model: str,
    ) -> tuple[httpx.Response, int]:
        """Retry only transient transport or HTTP failures within the fixed bound."""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = client.post(
                    _chat_completions_url(self._settings),
                    headers=_authorization_headers(self._settings),
                    json=body,
                )
            except httpx.TimeoutException:
                if attempt == self._max_attempts:
                    diagnostics = _transport_diagnostics("timeout", attempted_model)
                    raise LLMTimeoutError(
                        "OpenRouter request failed: timeout after bounded retries.",
                        diagnostics=diagnostics,
                    ) from None
                continue
            except httpx.RequestError:
                if attempt == self._max_attempts:
                    diagnostics = _transport_diagnostics(
                        "connection_error", attempted_model
                    )
                    raise LLMProviderError(
                        _safe_error_message(diagnostics), diagnostics=diagnostics
                    ) from None
                continue

            if response.is_success:
                return response, attempt
            diagnostics = _diagnostics_from_payload(
                _response_payload(response),
                http_status=response.status_code,
                response=response,
                attempted_model=attempted_model,
            )
            if not diagnostics.retryable or attempt == self._max_attempts:
                raise LLMProviderError(
                    _safe_error_message(diagnostics), diagnostics=diagnostics
                ) from None

        diagnostics = _transport_diagnostics("connection_error", attempted_model)
        raise LLMProviderError(
            _safe_error_message(diagnostics), diagnostics=diagnostics
        )

@dataclass(frozen=True, slots=True)
class OpenRouterPreflightResult:
    """Safe metadata returned by OpenRouter's current-key endpoint."""

    authenticated: bool
    management_key: bool | None
    expires_at: str | None
    expired: bool | None
    configured_limit: float | int | None
    remaining_limit: float | int | None
    credit_status: str
    usable_for_inference: bool
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        """Return an intentionally redacted preflight report."""
        return {
            "authenticated": self.authenticated,
            "management_key": self.management_key,
            "expires_at": self.expires_at,
            "expired": self.expired,
            "configured_limit": self.configured_limit,
            "remaining_limit": self.remaining_limit,
            "credit_status": self.credit_status,
            "usable_for_inference": self.usable_for_inference,
            "http_status": self.diagnostics.http_status,
            "error_type": self.diagnostics.error_type,
            "provider_code": self.diagnostics.provider_code,
            "retryable": self.diagnostics.retryable,
            "retry_after_seconds": self.diagnostics.retry_after_seconds,
        }


class OpenRouterPreflightClient:
    """Safe current-key diagnostic with no inference request."""

    def __init__(self, settings: Settings, *, timeout: float = 15.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._settings = settings
        self._timeout = timeout

    def preflight(
        self, *, http_client: httpx.Client | None = None
    ) -> OpenRouterPreflightResult:
        """Call only ``GET /key`` and return reviewed key metadata."""
        model = _safe_model_slug(self._settings.openrouter_model)
        if self._settings.openrouter_api_key is None:
            return self._failed(_configuration_diagnostics(model))
        owns_client = http_client is None
        client = http_client or httpx.Client(timeout=self._timeout)
        try:
            try:
                response = client.get(
                    f"{str(self._settings.openrouter_base_url).rstrip('/')}/key",
                    headers=_authorization_headers(self._settings),
                )
            except httpx.TimeoutException:
                return self._failed(_transport_diagnostics("timeout", model))
            except httpx.RequestError:
                return self._failed(_transport_diagnostics("connection_error", model))
        finally:
            if owns_client:
                client.close()

        payload = _response_payload(response)
        if not response.is_success:
            return self._failed(
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                )
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return self._failed(
                OpenRouterErrorDiagnostics(
                    "invalid_preflight_response",
                    response.status_code,
                    "invalid_preflight_response",
                    None,
                    False,
                    None,
                    model,
                )
            )
        expires_at = self._safe_expiry(data.get("expires_at"))
        expired = self._is_expired(expires_at)
        management = data.get("is_management_key")
        management_key = management if isinstance(management, bool) else None
        configured_limit = _safe_number(data.get("limit"))
        remaining_limit = _safe_number(data.get("limit_remaining"))
        credit_status = self._credit_status(configured_limit, remaining_limit)
        diagnostics = OpenRouterErrorDiagnostics(
            "authenticated", response.status_code, None, None, False, None, model
        )
        return OpenRouterPreflightResult(
            authenticated=True,
            management_key=management_key,
            expires_at=expires_at,
            expired=expired,
            configured_limit=configured_limit,
            remaining_limit=remaining_limit,
            credit_status=credit_status,
            usable_for_inference=(
                not management_key and not expired and credit_status != "exhausted"
            ),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _failed(diagnostics: OpenRouterErrorDiagnostics) -> OpenRouterPreflightResult:
        return OpenRouterPreflightResult(
            False, None, None, None, None, None, "unknown", False, diagnostics
        )

    @staticmethod
    def _safe_expiry(value: object) -> str | None:
        if not isinstance(value, str) or len(value) > 64:
            return None
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return value

    @staticmethod
    def _is_expired(expires_at: str | None) -> bool | None:
        if expires_at is None:
            return None
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC) <= datetime.now(UTC)

    @staticmethod
    def _credit_status(
        configured: float | int | None, remaining: float | int | None
    ) -> str:
        if remaining is not None:
            return "exhausted" if remaining <= 0 else "available"
        return "not_limited_or_not_reported" if configured is None else "unknown"


@dataclass(frozen=True, slots=True)
class OpenRouterEndpointProbeResult:
    endpoint_count: int
    available_provider_names: tuple[str, ...]
    supports_response_format_or_structured_outputs: bool
    success: bool
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        return {
            "probe_type": "endpoints",
            "success": self.success,
            "endpoint_count": self.endpoint_count,
            "available_provider_names": list(self.available_provider_names),
            "supports_response_format_or_structured_outputs": (
                self.supports_response_format_or_structured_outputs
            ),
            "http_status": self.diagnostics.http_status,
            "error_category": None if self.success else self.diagnostics.category,
        }


@dataclass(frozen=True, slots=True)
class OpenRouterInferenceProbeResult:
    probe_type: ProbeType
    success: bool
    returned_model: str | None
    text_output_received: bool
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        return {
            "probe_type": self.probe_type,
            "success": self.success,
            "http_status": self.diagnostics.http_status,
            "error_category": None if self.success else self.diagnostics.category,
            "returned_model": self.returned_model,
            "text_output_received": self.text_output_received,
        }


class _StructuredProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str
    number: int


@dataclass(frozen=True, slots=True)
class OpenRouterStructuredProbeResult:
    probe_type: StructuredProbeType
    success: bool
    returned_model: str | None
    valid_structured_json_received: bool
    schema_validation_passed: bool
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        return {
            "probe_type": self.probe_type,
            "success": self.success,
            "http_status": self.diagnostics.http_status,
            "error_category": None if self.success else self.diagnostics.category,
            "returned_model": self.returned_model,
            "valid_structured_json_received": self.valid_structured_json_received,
            "schema_validation_passed": self.schema_validation_passed,
        }


@dataclass(frozen=True, slots=True)
class OpenRouterDiscoverySchemaProbeResult:
    """Redacted routing and validation status for the production discovery schema."""

    success: bool
    returned_model: str | None
    total_provider_count: int | None
    available_provider_count: int
    selected_provider_names: tuple[str, ...]
    excluded_provider_names: tuple[str, ...]
    routing_reason: str | None
    schema_validation_succeeded: bool
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        """Return only reviewed diagnostic fields, never request or response content."""
        category = self.diagnostics.category
        generation_occurred = self.diagnostics.http_status == 200
        json_parsing_succeeded = self.success or category in {
            "schema_validation_failed",
            "semantic_validation_failed",
            "mapping_failed",
        }
        pydantic_validation_succeeded = self.success or category in {
            "semantic_validation_failed",
            "mapping_failed",
        }
        semantic_validation_succeeded = self.success or category == "mapping_failed"
        return {
            "probe_type": "discovery-schema",
            "success": self.success,
            "http_status": self.diagnostics.http_status,
            "error_category": None if self.success else self.diagnostics.category,
            "returned_model": self.returned_model,
            "total_provider_count": self.total_provider_count,
            "available_provider_count": self.available_provider_count,
            "selected_provider_names": list(self.selected_provider_names),
            "excluded_provider_names": list(self.excluded_provider_names),
            "routing_reason": self.routing_reason,
            "generation_occurred": generation_occurred,
            "json_parsing_succeeded": json_parsing_succeeded,
            "pydantic_validation_succeeded": pydantic_validation_succeeded,
            "semantic_validation_succeeded": semantic_validation_succeeded,
            "mapping_succeeded": self.success,
            "schema_validation_succeeded": self.schema_validation_succeeded,
            "validation_stage": self.diagnostics.validation_stage,
            "json_decoder_line": self.diagnostics.json_decoder_line,
            "json_decoder_column": self.diagnostics.json_decoder_column,
            "json_decoder_position": self.diagnostics.json_decoder_position,
            "response_character_count": (
                self.diagnostics.response_character_count
            ),
            "content_empty": self.diagnostics.content_empty,
            "finish_reason": self.diagnostics.finish_reason,
            "validation_error_count": self.diagnostics.validation_error_count,
            "validation_field_locations": list(
                self.diagnostics.validation_field_locations
            ),
            "validation_error_types": list(
                self.diagnostics.validation_error_types
            ),
        }


class OpenRouterEndpointProbeClient:
    """Redacted direct endpoint and synthetic transport diagnostics."""

    _PROMPT = "Reply exactly OK."
    _STRUCTURED_PROMPT = "Return the status and number."
    _DISCOVERY_SCHEMA_PROMPT = (
        "Return one assumption, one ambiguity, and one next question."
    )
    _STRUCTURED_SCHEMA: dict[str, object] = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "number": {"type": "integer"},
        },
        "required": ["status", "number"],
        "additionalProperties": False,
    }

    def __init__(self, settings: Settings, *, timeout: float = 15.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._settings = settings
        self._timeout = timeout

    def inspect_endpoints(
        self, model_slug: str, *, http_client: httpx.Client | None = None
    ) -> OpenRouterEndpointProbeResult:
        model = self._validated_model(model_slug)
        if self._settings.openrouter_api_key is None:
            return self._failed_endpoint(_configuration_diagnostics(model))
        url = (
            f"{str(self._settings.openrouter_base_url).rstrip('/')}/models/"
            f"{quote(model, safe='/:')}/endpoints"
        )
        response = self._request("GET", url, model=model, http_client=http_client)
        if isinstance(response, OpenRouterErrorDiagnostics):
            return self._failed_endpoint(response)
        payload = _response_payload(response)
        if not response.is_success:
            return self._failed_endpoint(
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                )
            )
        data = payload.get("data")
        endpoints_value = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(endpoints_value, list):
            diagnostics = OpenRouterErrorDiagnostics(
                "invalid_endpoint_probe_response",
                response.status_code,
                "invalid_endpoint_probe_response",
                None,
                False,
                None,
                model,
            )
            return self._failed_endpoint(diagnostics)
        endpoints = [item for item in endpoints_value if isinstance(item, dict)]
        providers = tuple(
            sorted(
                {
                    name
                    for endpoint in endpoints
                    if (name := _safe_provider_name(endpoint.get("provider_name")))
                    is not None
                }
            )
        )
        supports = any(self._supports_structured(endpoint) for endpoint in endpoints)
        diagnostics = OpenRouterErrorDiagnostics(
            "endpoint_metadata_available",
            response.status_code,
            None,
            None,
            False,
            None,
            model,
        )
        return OpenRouterEndpointProbeResult(
            len(endpoints), providers, supports, True, diagnostics
        )

    def probe_inference(
        self,
        probe_type: ProbeType,
        model_slug: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> OpenRouterInferenceProbeResult:
        if probe_type not in {"chat", "responses"}:
            raise ValueError("probe_type must be 'chat' or 'responses'")
        model = self._validated_model(model_slug)
        if self._settings.openrouter_api_key is None:
            return self._failed_inference(
                probe_type, _configuration_diagnostics(model)
            )
        base = str(self._settings.openrouter_base_url).rstrip("/")
        if probe_type == "chat":
            url = f"{base}/chat/completions"
            body: dict[str, object] = {
                "model": model,
                "messages": [{"role": "user", "content": self._PROMPT}],
            }
        else:
            url = f"{base}/responses"
            body = {"model": model, "input": self._PROMPT}
        response = self._request(
            "POST", url, model=model, body=body, http_client=http_client
        )
        if isinstance(response, OpenRouterErrorDiagnostics):
            return self._failed_inference(probe_type, response)
        payload = _response_payload(response)
        if not response.is_success:
            return self._failed_inference(
                probe_type,
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                ),
            )
        text_received = (
            bool((_chat_content(payload) or "").strip())
            if probe_type == "chat"
            else self._responses_text_received(payload)
        )
        diagnostics = OpenRouterErrorDiagnostics(
            "probe_completed", response.status_code, None, None, False, None, model
        )
        return OpenRouterInferenceProbeResult(
            probe_type,
            True,
            _safe_model_slug(payload.get("model")),
            text_received,
            diagnostics,
        )

    def probe_structured(
        self,
        probe_type: StructuredProbeType,
        model_slug: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> OpenRouterStructuredProbeResult:
        if probe_type not in {"structured", "structured-required"}:
            raise ValueError("invalid structured probe type")
        model = self._validated_model(model_slug)
        if self._settings.openrouter_api_key is None:
            return self._failed_structured(
                probe_type, _configuration_diagnostics(model)
            )
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": self._STRUCTURED_PROMPT}],
            "response_format": _strict_response_format(
                name="status_number", schema=self._STRUCTURED_SCHEMA
            ),
            "stream": False,
        }
        if probe_type == "structured-required":
            body["provider"] = {"require_parameters": True}
        response = self._request(
            "POST",
            _chat_completions_url(self._settings),
            model=model,
            body=body,
            http_client=http_client,
        )
        if isinstance(response, OpenRouterErrorDiagnostics):
            return self._failed_structured(probe_type, response)
        payload = _response_payload(response)
        if not response.is_success:
            return self._failed_structured(
                probe_type,
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                ),
            )
        valid_json, schema_valid = self._validate_probe_content(payload)
        category = (
            "structured_probe_completed"
            if schema_valid
            else "schema_validation_failed"
            if valid_json
            else "invalid_structured_json"
        )
        diagnostics = OpenRouterErrorDiagnostics(
            category, response.status_code, None, None, False, None, model
        )
        return OpenRouterStructuredProbeResult(
            probe_type,
            schema_valid,
            _safe_model_slug(payload.get("model")),
            valid_json,
            schema_valid,
            diagnostics,
        )

    def probe_discovery_schema(
        self,
        model_slug: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> OpenRouterDiscoverySchemaProbeResult:
        """Probe the exact production discovery schema with redacted routing data."""
        from src.models import DiscoveryExtractionResponse, WorkflowStage

        model = self._validated_model(model_slug)
        if self._settings.openrouter_api_key is None:
            return self._failed_discovery_schema(_configuration_diagnostics(model))
        body = _structured_request_body(
            model=model,
            messages=[{"role": "user", "content": self._DISCOVERY_SCHEMA_PROMPT}],
            response_model=DiscoveryExtractionResponse,
            temperature=None,
        )
        response = self._request(
            "POST",
            _chat_completions_url(self._settings),
            model=model,
            body=body,
            http_client=http_client,
            router_metadata=True,
        )
        if isinstance(response, OpenRouterErrorDiagnostics):
            return self._failed_discovery_schema(response)

        payload = _response_payload(response)
        routing = self._routing_metadata(payload)
        if not response.is_success:
            return self._failed_discovery_schema(
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                ),
                routing=routing,
            )

        try:
            compact_result = _parse_structured_payload(
                payload,
                DiscoveryExtractionResponse,
                model,
                http_status=response.status_code,
            )
            try:
                compact_result.to_discovery_turn_result(
                    current_stage=WorkflowStage.BASIC_INFO
                )
            except (ValidationError, ValueError, TypeError):
                diagnostics = _structured_output_diagnostics(
                    "mapping_failed",
                    stage="domain_mapping",
                    attempted_model=model,
                    http_status=response.status_code,
                    content=None,
                    finish_reason=_safe_finish_reason(payload),
                )
                return self._failed_discovery_schema(diagnostics, routing=routing)
        except LLMProviderError as exc:
            if exc.diagnostics is None:
                diagnostics = OpenRouterErrorDiagnostics(
                    "provider_error",
                    response.status_code,
                    None,
                    None,
                    False,
                    None,
                    model,
                )
            else:
                diagnostics = OpenRouterErrorDiagnostics(
                    exc.diagnostics.category,
                    response.status_code,
                    exc.diagnostics.error_type,
                    exc.diagnostics.provider_code,
                    exc.diagnostics.retryable,
                    exc.diagnostics.retry_after_seconds,
                    model,
                )
            return self._failed_discovery_schema(diagnostics, routing=routing)
        except InvalidStructuredOutputError as exc:
            diagnostics = exc.diagnostics or _structured_output_diagnostics(
                "schema_validation_failed",
                stage="pydantic_schema",
                attempted_model=model,
                http_status=response.status_code,
                content=None,
                finish_reason=_safe_finish_reason(payload),
            )
            return self._failed_discovery_schema(diagnostics, routing=routing)

        diagnostics = OpenRouterErrorDiagnostics(
            "discovery_schema_validated",
            response.status_code,
            None,
            None,
            False,
            None,
            model,
        )
        return OpenRouterDiscoverySchemaProbeResult(
            True,
            _safe_model_slug(payload.get("model")),
            routing[0],
            routing[1],
            routing[2],
            routing[3],
            routing[4],
            True,
            diagnostics,
        )

    def _request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        model: str,
        body: dict[str, object] | None = None,
        http_client: httpx.Client | None,
        router_metadata: bool = False,
    ) -> httpx.Response | OpenRouterErrorDiagnostics:
        owns_client = http_client is None
        client = http_client or httpx.Client(timeout=self._timeout)
        try:
            try:
                if method == "GET":
                    return client.get(
                        url,
                        headers=_authorization_headers(
                            self._settings, router_metadata=router_metadata
                        ),
                    )
                return client.post(
                    url,
                    headers=_authorization_headers(
                        self._settings, router_metadata=router_metadata
                    ),
                    json=body,
                )
            except httpx.TimeoutException:
                return _transport_diagnostics("timeout", model)
            except httpx.RequestError:
                return _transport_diagnostics("connection_error", model)
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _validated_model(model: str) -> str:
        safe = _safe_model_slug(model)
        if safe is None:
            raise ValueError("model_slug must be a compact OpenRouter identifier")
        return safe

    @staticmethod
    def _supports_structured(endpoint: dict[str, object]) -> bool:
        parameters = endpoint.get("supported_parameters")
        return (
            isinstance(parameters, list)
            and any(
                item in {"response_format", "structured_outputs"}
                for item in parameters
            )
        ) or endpoint.get("supports_response_format") is True or endpoint.get(
            "supports_structured_outputs"
        ) is True

    @staticmethod
    def _responses_text_received(payload: dict[str, object]) -> bool:
        output = payload.get("output")
        if not isinstance(output, list):
            return False
        for item in output:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return True
        return False

    @staticmethod
    def _validate_probe_content(payload: dict[str, object]) -> tuple[bool, bool]:
        content = _chat_content(payload)
        if not content:
            return False, False
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False, False
        if not isinstance(parsed, dict):
            return False, False
        try:
            _StructuredProbePayload.model_validate(parsed)
        except ValidationError:
            return True, False
        return True, True

    def _routing_metadata(
        self,
        payload: dict[str, object],
    ) -> tuple[int | None, int, tuple[str, ...], tuple[str, ...], str | None]:
        """Extract only allow-listed router fields from a provider payload."""
        metadata = payload.get("openrouter_metadata")
        if not isinstance(metadata, dict):
            error = payload.get("error")
            error_data = error if isinstance(error, dict) else {}
            error_metadata = error_data.get("metadata")
            error_metadata_data = (
                error_metadata if isinstance(error_metadata, dict) else {}
            )
            metadata = error_metadata_data.get("openrouter_metadata")
        metadata_data = metadata if isinstance(metadata, dict) else {}
        endpoints = metadata_data.get("endpoints")
        endpoints_data = endpoints if isinstance(endpoints, dict) else {}
        available = endpoints_data.get("available")
        available_items = (
            [item for item in available if isinstance(item, dict)]
            if isinstance(available, list)
            else []
        )
        selected: set[str] = set()
        excluded: set[str] = set()
        api_key = self._settings.openrouter_api_key
        secret = api_key.get_secret_value() if api_key is not None else None
        for endpoint in available_items:
            provider = _safe_provider_name(endpoint.get("provider"))
            if provider is None or (secret is not None and secret in provider):
                continue
            if endpoint.get("selected") is True:
                selected.add(provider)
            elif endpoint.get("selected") is False:
                excluded.add(provider)
        reason = _safe_routing_reason(metadata_data.get("summary"))
        if reason is None:
            routing = metadata_data.get("routing")
            routing_data = routing if isinstance(routing, dict) else {}
            reason = _safe_routing_reason(
                metadata_data.get("reason") or routing_data.get("reason")
            )
        if reason is not None and secret is not None and secret in reason:
            reason = None
        return (
            _safe_token_count(endpoints_data.get("total")),
            len(available_items),
            tuple(sorted(selected)),
            tuple(sorted(excluded)),
            reason,
        )

    @staticmethod
    def _failed_endpoint(
        diagnostics: OpenRouterErrorDiagnostics,
    ) -> OpenRouterEndpointProbeResult:
        return OpenRouterEndpointProbeResult(0, (), False, False, diagnostics)

    @staticmethod
    def _failed_inference(
        probe_type: ProbeType, diagnostics: OpenRouterErrorDiagnostics
    ) -> OpenRouterInferenceProbeResult:
        return OpenRouterInferenceProbeResult(
            probe_type, False, None, False, diagnostics
        )

    @staticmethod
    def _failed_structured(
        probe_type: StructuredProbeType, diagnostics: OpenRouterErrorDiagnostics
    ) -> OpenRouterStructuredProbeResult:
        return OpenRouterStructuredProbeResult(
            probe_type, False, None, False, False, diagnostics
        )

    def _failed_discovery_schema(
        self,
        diagnostics: OpenRouterErrorDiagnostics,
        *,
        routing: tuple[
            int | None, int, tuple[str, ...], tuple[str, ...], str | None
        ] = (None, 0, (), (), None),
    ) -> OpenRouterDiscoverySchemaProbeResult:
        return OpenRouterDiscoverySchemaProbeResult(
            False,
            None,
            routing[0],
            routing[1],
            routing[2],
            routing[3],
            routing[4],
            False,
            self._redact_configured_secret(diagnostics),
        )

    def _redact_configured_secret(
        self, diagnostics: OpenRouterErrorDiagnostics
    ) -> OpenRouterErrorDiagnostics:
        """Drop any diagnostic identifier that reflects the configured key."""
        api_key = self._settings.openrouter_api_key
        if api_key is None:
            return diagnostics
        secret = api_key.get_secret_value()

        def safe(value: str | None) -> str | None:
            return value if value is None or secret not in value else None

        category = safe(diagnostics.category)
        return OpenRouterErrorDiagnostics(
            category=category
            or _STATUS_CATEGORIES.get(diagnostics.http_status, "provider_error"),
            http_status=diagnostics.http_status,
            error_type=safe(diagnostics.error_type),
            provider_code=safe(diagnostics.provider_code),
            retryable=diagnostics.retryable,
            retry_after_seconds=diagnostics.retry_after_seconds,
            attempted_model=safe(diagnostics.attempted_model),
            validation_stage=diagnostics.validation_stage,
            json_decoder_line=diagnostics.json_decoder_line,
            json_decoder_column=diagnostics.json_decoder_column,
            json_decoder_position=diagnostics.json_decoder_position,
            response_character_count=diagnostics.response_character_count,
            content_empty=diagnostics.content_empty,
            finish_reason=diagnostics.finish_reason,
            validation_error_count=diagnostics.validation_error_count,
            validation_field_locations=diagnostics.validation_field_locations,
            validation_error_types=diagnostics.validation_error_types,
        )


@dataclass(frozen=True, slots=True)
class OpenRouterModelLookupResult:
    model: str
    available: bool
    supports_structured_outputs: bool | None
    supports_response_format: bool | None
    diagnostics: OpenRouterErrorDiagnostics

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "available": self.available,
            "supports_structured_outputs": self.supports_structured_outputs,
            "supports_response_format": self.supports_response_format,
            "http_status": self.diagnostics.http_status,
            "error_type": self.diagnostics.error_type,
            "provider_code": self.diagnostics.provider_code,
            "retryable": self.diagnostics.retryable,
            "retry_after_seconds": self.diagnostics.retry_after_seconds,
        }


class OpenRouterModelLookupClient:
    """Non-inference OpenRouter model lookup client."""

    def __init__(self, settings: Settings, *, timeout: float = 15.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._settings = settings
        self._timeout = timeout

    def lookup(
        self, model_slug: str, *, http_client: httpx.Client | None = None
    ) -> OpenRouterModelLookupResult:
        model = _safe_model_slug(model_slug)
        if model is None:
            raise ValueError("model_slug must be a compact OpenRouter identifier")
        owns_client = http_client is None
        client = http_client or httpx.Client(timeout=self._timeout)
        url = (
            f"{str(self._settings.openrouter_base_url).rstrip('/')}/model/"
            f"{quote(model, safe='/:')}"
        )
        try:
            try:
                response = client.get(url)
            except httpx.TimeoutException:
                return self._failed(model, _transport_diagnostics("timeout", model))
            except httpx.RequestError:
                return self._failed(
                    model, _transport_diagnostics("connection_error", model)
                )
        finally:
            if owns_client:
                client.close()
        payload = _response_payload(response)
        if not response.is_success:
            return self._failed(
                model,
                _diagnostics_from_payload(
                    payload,
                    http_status=response.status_code,
                    response=response,
                    attempted_model=model,
                ),
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            diagnostics = OpenRouterErrorDiagnostics(
                "invalid_model_lookup_response",
                response.status_code,
                "invalid_model_lookup_response",
                None,
                False,
                None,
                model,
            )
            return self._failed(model, diagnostics)
        parameters = data.get("supported_parameters")
        supported = (
            {item for item in parameters if isinstance(item, str)}
            if isinstance(parameters, list)
            else set()
        )
        diagnostics = OpenRouterErrorDiagnostics(
            "model_available", response.status_code, None, None, False, None, model
        )
        return OpenRouterModelLookupResult(
            model,
            True,
            "structured_outputs" in supported,
            "response_format" in supported,
            diagnostics,
        )

    @staticmethod
    def _failed(
        model: str, diagnostics: OpenRouterErrorDiagnostics
    ) -> OpenRouterModelLookupResult:
        return OpenRouterModelLookupResult(model, False, None, None, diagnostics)
