"""Non-inference OpenRouter diagnostic and probe clients.

Split out of ``src/llm_client.py``. These classes exist to interactively
debug provider routing, structured-output support, and API-key/credit status
against the real OpenRouter API while building the production request shape.
They are used only by ``scripts/openrouter_probe.py`` and its tests --
nothing in the product path (``app.py``, ``src/workflow.py``,
``src/discovery.py``) imports this module. ``src/llm_client.py`` remains the
only file that performs a production structured-generation call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from src.config import Settings
from src.llm_client import (
    InvalidStructuredOutputError,
    LLMProviderError,
    OpenRouterErrorDiagnostics,
    _STATUS_CATEGORIES,
    _authorization_headers,
    _chat_completions_url,
    _chat_content,
    _configuration_diagnostics,
    _diagnostics_from_payload,
    _parse_structured_payload,
    _response_payload,
    _safe_finish_reason,
    _safe_model_slug,
    _safe_number,
    _safe_provider_name,
    _safe_routing_reason,
    _safe_token_count,
    _strict_response_format,
    _structured_output_diagnostics,
    _structured_request_body,
    _transport_diagnostics,
)


ProbeType = Literal["chat", "responses"]
StructuredProbeType = Literal["structured", "structured-required"]


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
