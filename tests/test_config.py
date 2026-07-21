"""Configuration safety and validation tests."""

import pytest
from pydantic import ValidationError

from src.config import ConfigurationError, Settings


def test_safe_representation_does_not_leak_api_key() -> None:
    secret = "sk-test-this-must-never-appear"
    settings = Settings.from_env(
        {
            "OPENROUTER_API_KEY": secret,
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_MODEL": "example/model",
            "APP_ENV": "test",
        }
    )

    assert settings.api_key_configured is True
    assert secret not in repr(settings)
    assert secret not in str(settings)


def test_missing_api_key_is_allowed_for_non_api_setup() -> None:
    settings = Settings.from_env(
        {
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "APP_ENV": "test",
        }
    )

    assert settings.api_key_configured is False
    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
        settings.require_api_configuration()


def test_invalid_base_url_fails_validation() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                "OPENROUTER_BASE_URL": "ftp://not-an-http-endpoint.example",
                "APP_ENV": "test",
            }
        )

