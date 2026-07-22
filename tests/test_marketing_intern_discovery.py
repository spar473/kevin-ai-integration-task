"""Mocked acceptance checks for the initial Marketing Intern discovery turn."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models import (
    DiscoveryExtractionResponse,
    DiscoveryTurnResult,
    WorkflowStage,
)


ROOT = Path(__file__).resolve().parents[1]


def marketing_intern_result_payload() -> dict[str, object]:
    """Return a safe first-turn model result for the supplied fixture."""
    return {
        "extracted_requirements": [
            {
                "requirement_id": "req_social_media_001",
                "category": "domain",
                "name": "Social media familiarity",
                "description": "The manager mentioned social media, but the relevant platforms and expected level are unresolved.",
                "priority": "preferred",
                "proficiency": None,
                "learnability": None,
                "accepted_equivalents": [],
                "business_rationale": None,
                "evidence_methods": [],
                "source_statement": "They should be creative and good with social media.",
                "source_turn_id": "initial_manager_statement",
                "confidence": 0.6,
                "requires_confirmation": True,
                "approved_by_human": False,
            }
        ],
        "assumptions": [
            {
                "assumption_id": "assumption_001",
                "statement": "The role may involve TikTok-related collaboration.",
                "source_statement": "They'll work with the team on TikTok stuff",
                "requires_confirmation": True,
            }
        ],
        "ambiguities": [
            {
                "ambiguity_id": "ambiguity_team",
                "description": "The team or brand has not been identified.",
                "source_statement": "Marketing Intern",
                "why_confirmation_is_needed": "The scope, collaborators, and ownership level are unknown.",
            },
            {
                "ambiguity_id": "ambiguity_summer",
                "description": "The internship timeframe and working arrangement are not specified.",
                "source_statement": "for summer",
                "why_confirmation_is_needed": "Dates, hours, location, and arrangement must not be assumed.",
            },
            {
                "ambiguity_id": "ambiguity_creative",
                "description": "Creative work is not defined as an observable responsibility.",
                "source_statement": "They should be creative",
                "why_confirmation_is_needed": "It could mean ideation, copywriting, visual design, video, or another activity.",
            },
            {
                "ambiguity_id": "ambiguity_social",
                "description": "Social-media scope and platform responsibilities are not specified.",
                "source_statement": "good with social media",
                "why_confirmation_is_needed": "Platforms, professional experience, and expected level are unknown.",
            },
            {
                "ambiguity_id": "ambiguity_design",
                "description": "Design capability is only a tentative possibility.",
                "source_statement": "Maybe some design skills?",
                "why_confirmation_is_needed": "Its priority, tools, and expected outputs must not be invented.",
            },
            {
                "ambiguity_id": "ambiguity_collaboration",
                "description": "The manager needs observable collaborative behaviour rather than personality fit.",
                "source_statement": "Should be fun to work with.",
                "why_confirmation_is_needed": "The manager must describe job-relevant behaviour in a team situation.",
            },
        ],
        "contradictions": [],
        "next_question": {
            "question_id": "question_001",
            "question": "Which team or brand will this intern support?",
            "target_stage": "basic_info",
            "purpose": "Clarify the role context before inferring responsibilities.",
        },
        "confidence": 0.72,
    }


def marketing_intern_compact_payload() -> dict[str, object]:
    """Return the provider-facing form of the safe Marketing Intern result."""
    domain_payload = marketing_intern_result_payload()
    requirements = domain_payload["extracted_requirements"]
    assumptions = domain_payload["assumptions"]
    ambiguities = domain_payload["ambiguities"]
    assert isinstance(requirements, list)
    assert isinstance(assumptions, list)
    assert isinstance(ambiguities, list)
    requirement = requirements[0]
    assert isinstance(requirement, dict)
    return {
        "incremental_requirements": [
            {
                "category": requirement["category"],
                "name": requirement["name"],
                "description": requirement["description"],
                "priority": requirement["priority"],
                "rationale": "The manager explicitly mentioned social media capability.",
                "source_statement": requirement["source_statement"],
            }
        ],
        "assumptions": [
            {
                "statement": item["statement"],
                "source_statement": item["source_statement"],
            }
            for item in assumptions
            if isinstance(item, dict)
        ],
        "ambiguities": [
            {
                "description": item["description"],
                "source_statement": item["source_statement"],
                "why_confirmation_is_needed": item[
                    "why_confirmation_is_needed"
                ],
            }
            for item in ambiguities
            if isinstance(item, dict)
        ],
        "possible_contradictions": [],
        "next_question": "Which team or brand will this intern support?",
        "stage_recommendation": "stay",
    }


def test_marketing_intern_first_turn_preserves_uncertainty_and_sources() -> None:
    fixture = json.loads(
        (ROOT / "data" / "fixtures" / "marketing_intern.json").read_text(
            encoding="utf-8"
        )
    )
    compact = DiscoveryExtractionResponse.model_validate(
        marketing_intern_compact_payload()
    )
    result = compact.to_discovery_turn_result(
        current_stage=WorkflowStage.BASIC_INFO
    )

    assert len(result.ambiguities) >= 5
    assert all(item.requires_confirmation for item in result.assumptions)
    assert "TikTok stuff" in result.assumptions[0].source_statement
    assert any(
        item.source_statement == "Should be fun to work with."
        and "observable collaborative behaviour" in item.description
        for item in result.ambiguities
    )
    assert all(
        "fun to work" not in f"{item.name} {item.description or ''}".lower()
        for item in result.extracted_requirements
    )
    assert all("creative" not in item.name.lower() for item in result.extracted_requirements)
    assert result.next_question.question.count("?") == 1
    assert len(result.next_question.question.splitlines()) == 1

    redacted_output = result.model_dump_json().lower()
    for invented_value in (
        "salary",
        "benefits",
        "auckland",
        "photoshop",
        "canva",
        "monday 9am",
    ):
        assert invented_value not in redacted_output
    assert fixture["initial_manager_statement"].startswith("We need a Marketing Intern")


def test_discovery_result_rejects_more_than_one_question_field() -> None:
    payload = marketing_intern_result_payload()
    payload["next_questions"] = []

    with pytest.raises(ValidationError, match="next_questions"):
        DiscoveryTurnResult.model_validate(payload)


def test_discovery_prompt_requires_safe_narrow_extraction() -> None:
    prompt = (ROOT / "prompts" / "discovery.md").read_text(encoding="utf-8").lower()

    for expected_instruction in (
        "untrusted",
        "exactly one",
        "observable",
        "do not invent",
        'stage_recommendation` must be exactly `"stay"` or `"advance"',
        "do not output a workflow stage name",
        "return only",
    ):
        assert expected_instruction in prompt
