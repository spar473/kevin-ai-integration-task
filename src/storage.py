"""Safe, atomic JSON persistence for validated Pydantic models."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


class StorageError(RuntimeError):
    """Base error for JSON persistence failures."""


class StoragePathError(StorageError):
    """Raised when a path escapes the configured storage root."""


class MissingStorageFile(StorageError):
    """Raised when a requested JSON file does not exist."""


class MalformedJsonError(StorageError):
    """Raised when a stored file does not contain valid JSON."""


class StorageValidationError(StorageError):
    """Raised when stored JSON does not match its expected schema."""


class StorageSecurityError(StorageError):
    """Raised when a payload appears to contain secret material."""


class JsonStorage:
    """Persist Pydantic models beneath one configured directory."""

    _SECRET_KEYS = {
        "api_key",
        "openrouter_api_key",
        "authorization",
        "access_token",
        "secret",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _safe_path(self, relative_path: str | Path) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise StoragePathError("Storage paths must be relative.")
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise StoragePathError("Storage path escapes the configured directory.")
        return candidate

    @classmethod
    def _contains_secret(cls, value: object) -> bool:
        if isinstance(value, dict):
            if any(str(key).lower() in cls._SECRET_KEYS for key in value):
                return True
            return any(cls._contains_secret(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_secret(item) for item in value)
        return False

    def save(self, relative_path: str | Path, model: BaseModel) -> Path:
        """Atomically save a model as indented UTF-8 JSON."""
        destination = self._safe_path(relative_path)
        payload = model.model_dump(mode="json")
        if self._contains_secret(payload):
            raise StorageSecurityError("Refusing to persist a secret-bearing payload.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return destination

    def load(self, relative_path: str | Path, model_type: type[ModelT]) -> ModelT:
        """Load JSON and validate it as the requested Pydantic model."""
        source = self._safe_path(relative_path)
        if not source.is_file():
            raise MissingStorageFile(f"Stored JSON file was not found: {relative_path}")
        try:
            raw = source.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedJsonError(f"Malformed JSON in {relative_path}") from exc
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise StorageValidationError(
                f"Stored JSON failed {model_type.__name__} validation: {relative_path}"
            ) from exc

