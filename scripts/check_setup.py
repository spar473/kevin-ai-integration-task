"""Validate the local project foundation without exposing secrets."""

from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402


REQUIRED_IMPORTS = {
    "streamlit": "streamlit",
    "pydantic": "pydantic",
    "python-dotenv": "dotenv",
    "httpx": "httpx",
    "openai": "openai",
    "pytest": "pytest",
}
REQUIRED_DOCS = {
    "01_SETUP_AND_WORKFLOW_GUIDE.md",
    "02_DEVELOPMENT_PLAN_AND_TIMELINE.md",
    "03_TECHNICAL_DESIGN_AND_METHODS.md",
    "ZURU_AI_Integration_Internship_Codex_Context.md",
}
REQUIRED_MODULES = {
    "src/config.py",
    "src/models.py",
    "src/llm_client.py",
    "src/workflow.py",
    "src/storage.py",
}
REQUIRED_FIXTURES = {"data/fixtures/marketing_intern.json"}


def report(label: str, passed: bool, detail: str = "") -> bool:
    """Print one concise check result and return its status."""
    marker = "OK" if passed else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{marker}] {label}{suffix}")
    return passed


def main() -> int:
    """Run all setup checks and return non-zero when any required check fails."""
    results: list[bool] = []
    print(f"Python version: {platform.python_version()}")
    print(f"Repository root: {ROOT}")

    for package_name, import_name in REQUIRED_IMPORTS.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            results.append(report(f"Import {package_name}", False))
        else:
            results.append(report(f"Import {package_name}", True))

    docs_dir = ROOT / "docs"
    results.append(report("docs directory", docs_dir.is_dir()))
    existing_docs = {path.name for path in docs_dir.glob("*.md")} if docs_dir.is_dir() else set()
    results.append(
        report(
            "required documentation",
            REQUIRED_DOCS.issubset(existing_docs),
            f"{len(REQUIRED_DOCS.intersection(existing_docs))}/{len(REQUIRED_DOCS)} present",
        )
    )

    try:
        settings = Settings.from_env()
    except ValidationError:
        results.append(report("configuration validation", False, "invalid non-secret value"))
    else:
        results.append(report("configuration validation", True))
        print(f"Application environment: {settings.app_env}")
        print(f"OpenRouter base URL: {settings.openrouter_base_url}")
        print(f"OpenRouter model: {settings.openrouter_model or 'not configured'}")
        print(f"API key configured: {settings.api_key_configured}")

    for relative_path in sorted(REQUIRED_FIXTURES):
        results.append(report(relative_path, (ROOT / relative_path).is_file()))
    for relative_path in sorted(REQUIRED_MODULES):
        results.append(report(relative_path, (ROOT / relative_path).is_file()))

    passed = all(results)
    print("Setup check passed." if passed else "Setup check failed.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
