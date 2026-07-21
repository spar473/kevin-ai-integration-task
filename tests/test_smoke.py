"""Basic repository and core-module smoke tests."""

import importlib
from pathlib import Path

from src.models import RoleSpecification, WorkflowStage
from src.workflow import WorkflowState, next_incomplete_stage


ROOT = Path(__file__).resolve().parents[1]


def test_core_modules_import() -> None:
    for module_name in (
        "src.config",
        "src.models",
        "src.llm_client",
        "src.workflow",
        "src.storage",
    ):
        assert importlib.import_module(module_name)


def test_basic_repository_assumptions() -> None:
    assert (ROOT / "docs").is_dir()
    assert len(list((ROOT / "docs").glob("*.md"))) >= 4
    assert (ROOT / "data" / "fixtures" / "marketing_intern.json").is_file()


def test_empty_role_starts_at_basic_info() -> None:
    state = WorkflowState(role_specification=RoleSpecification(role_id="role_001"))

    assert next_incomplete_stage(state) is WorkflowStage.BASIC_INFO

