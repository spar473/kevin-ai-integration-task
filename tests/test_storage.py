"""Safe JSON storage tests."""

from pathlib import Path

import pytest

from src.models import BasicRoleInfo, RoleSpecification
from src.storage import (
    JsonStorage,
    MalformedJsonError,
    MissingStorageFile,
    StoragePathError,
    StorageValidationError,
)


def test_save_load_round_trip(tmp_path: Path) -> None:
    storage = JsonStorage(tmp_path)
    role = RoleSpecification(
        role_id="role_001",
        basic_info=BasicRoleInfo(title="Marketing Intern"),
    )

    saved_path = storage.save("session/role.json", role)
    restored = storage.load("session/role.json", RoleSpecification)

    assert saved_path.is_file()
    assert restored == role
    assert saved_path.read_text(encoding="utf-8").endswith("\n")


def test_malformed_json_raises_clear_error(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(MalformedJsonError):
        JsonStorage(tmp_path).load("broken.json", RoleSpecification)


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MissingStorageFile):
        JsonStorage(tmp_path).load("missing.json", RoleSpecification)


def test_schema_validation_failure_is_wrapped(tmp_path: Path) -> None:
    (tmp_path / "invalid.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StorageValidationError):
        JsonStorage(tmp_path).load("invalid.json", RoleSpecification)


@pytest.mark.parametrize("unsafe_path", ["../escape.json", "nested/../../escape.json"])
def test_path_traversal_is_prevented(tmp_path: Path, unsafe_path: str) -> None:
    with pytest.raises(StoragePathError):
        JsonStorage(tmp_path).save(
            unsafe_path, RoleSpecification(role_id="role_001")
        )

