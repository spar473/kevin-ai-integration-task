"""Typed, secret-safe application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator


class ConfigurationError(RuntimeError):
    """Raised when an API operation lacks required configuration."""


class Settings(BaseModel):
    """Runtime settings loaded from environment variables or an explicit mapping."""

    model_config = ConfigDict(extra="forbid")

    openrouter_base_url: AnyHttpUrl = Field(
        default="https://openrouter.ai/api/v1"
    )
    openrouter_model: str | None = None
    app_env: str = "development"
    openrouter_api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("openrouter_base_url")
    @classmethod
    def base_url_must_not_be_a_completion_endpoint(
        cls, value: AnyHttpUrl
    ) -> AnyHttpUrl:
        """Require an API base URL rather than a complete chat-completions endpoint."""
        path = value.path.rstrip("/")
        if path.endswith("/chat/completions"):
            raise ValueError(
                "OPENROUTER_BASE_URL must be an API base URL, not /chat/completions"
            )
        if path != "/api/v1":
            raise ValueError("OPENROUTER_BASE_URL must end with /api/v1")
        return value

    @field_validator("openrouter_model", mode="before")
    @classmethod
    def empty_model_is_unconfigured(cls, value: object) -> object:
        """Treat blank model values and the example placeholder as unconfigured."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped == "replace_with_confirmed_model_slug":
                return None
            return stripped
        return value

    @field_validator("openrouter_api_key", mode="before")
    @classmethod
    def empty_key_is_unconfigured(cls, value: object) -> object:
        """Treat blank keys and the example placeholder as unconfigured."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped == "replace_with_your_key":
                return None
            return stripped
        return value

    @field_validator("app_env", mode="before")
    @classmethod
    def normalise_app_env(cls, value: object) -> object:
        """Reject an empty application environment label."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError("APP_ENV must not be empty")

    @property
    def api_key_configured(self) -> bool:
        """Return only whether a usable API key is present."""
        return self.openrouter_api_key is not None

    def require_api_configuration(self) -> None:
        """Raise a safe error unless both provider credentials and model exist."""
        missing: list[str] = []
        if not self.api_key_configured:
            missing.append("OPENROUTER_API_KEY")
        if not self.openrouter_model:
            missing.append("OPENROUTER_MODEL")
        if missing:
            raise ConfigurationError(
                "API call cannot start; missing configuration: " + ", ".join(missing)
            )

    def __str__(self) -> str:
        """Return a status string that never includes secret material."""
        model = self.openrouter_model or "not configured"
        return (
            f"Settings(app_env={self.app_env!r}, "
            f"openrouter_base_url={str(self.openrouter_base_url)!r}, "
            f"openrouter_model={model!r}, "
            f"api_key_configured={self.api_key_configured})"
        )

    def __repr__(self) -> str:
        """Use the same explicitly redacted representation as ``str``."""
        return str(self)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from a mapping, or load the local environment on demand."""
        if env is None:
            load_dotenv(override=False)
            env = os.environ
        return cls(
            openrouter_api_key=env.get("OPENROUTER_API_KEY"),
            openrouter_base_url=env.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            openrouter_model=env.get("OPENROUTER_MODEL"),
            app_env=env.get("APP_ENV", "development"),
        )
