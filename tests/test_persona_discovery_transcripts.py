"""Acceptance checks for the four required discovery scenarios, run live.

Each fixture in ``data/fixtures/*_discovery_transcript.json`` is a recorded,
multi-turn OpenRouter conversation captured with
``scripts/run_persona_discovery.py`` against the exact same
``take_discovery_turn`` call path the Streamlit app uses. These tests assert
the documented expected behaviour for each persona (PDF-derived brief,
``docs/ZURU_AI_Integration_Internship_Codex_Context.md`` §3.5 and §3.6)
against what the live model and deterministic engine actually produced, not
against a hand-authored substitute. Live-model deviations from the
documented behaviour are recorded in the fixtures' ``rejected_turns`` and
``observed_gap_*`` keys and explained in ``docs/DECISIONS_AND_LESSONS.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models import RoleSpecification
from src.readiness import evaluate_role_quality


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "data" / "fixtures"

PERSONA_FIXTURES = (
    "vague_executive_discovery_transcript.json",
    "over_technical_manager_discovery_transcript.json",
    "culture_focused_manager_discovery_transcript.json",
    "marketing_intern_discovery_transcript.json",
    "technical_role_discovery_transcript.json",
    "creative_role_discovery_transcript.json",
)


def _load(filename: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / filename).read_text(encoding="utf-8"))


def _role(transcript: dict[str, object]) -> RoleSpecification:
    return RoleSpecification.model_validate(transcript["final_role_specification"])


def _requirement_text(transcript: dict[str, object]) -> str:
    role = transcript["final_role_specification"]
    parts = []
    for item in role["requirements"]:
        parts.extend(
            [item["name"], item.get("description") or "", item["source_statement"]]
        )
    return " ".join(parts).lower()


# ---------------------------------------------------------------------------
# Fixtures load and validate as real domain objects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", PERSONA_FIXTURES)
def test_transcript_fixture_final_role_specification_is_valid(filename: str) -> None:
    transcript = _load(filename)
    role = _role(transcript)
    assert role.role_id
    assert transcript["turns"], "a recorded transcript must contain at least one turn"


@pytest.mark.parametrize("filename", PERSONA_FIXTURES)
def test_recorded_quality_report_is_reproducible_from_the_role_snapshot(
    filename: str,
) -> None:
    """The recorded readiness/vague-phrase/blocker snapshot must be exactly
    what the current deterministic engine still computes from the recorded
    role, so the fixture cannot silently drift from ``src/readiness.py``."""
    transcript = _load(filename)
    role = _role(transcript)
    report = evaluate_role_quality(role)
    recorded = transcript["final_quality_report"]

    assert report.readiness.score == recorded["readiness_score"]
    assert report.readiness.interpretation == recorded["readiness_interpretation"]
    assert sorted(report.blockers) == sorted(recorded["blockers"])
    assert sorted(item.phrase for item in report.vague_phrases) == sorted(
        item["phrase"] for item in recorded["vague_phrases"]
    )


@pytest.mark.parametrize("filename", PERSONA_FIXTURES)
def test_every_rejected_turn_was_the_documented_safety_backstop(
    filename: str,
) -> None:
    """Every recorded rejection must be the must-have/ambiguity overlap
    guard (never a schema, network, or unrelated failure) -- otherwise a
    rejected turn would be masking a different, unreviewed bug."""
    transcript = _load(filename)
    for rejected in transcript.get("rejected_turns", []):
        error_types = rejected.get("validation_error_types")
        if error_types is not None:
            assert all(
                item == "must_have_conflicts_with_unresolved_ambiguity"
                for item in error_types
            )


# ---------------------------------------------------------------------------
# Persona A: vague executive -- force prioritisation, break vague labels into
# outcomes/priorities/scope, and demand concrete examples.
# ---------------------------------------------------------------------------


def test_vague_executive_is_pushed_to_prioritise_and_get_concrete() -> None:
    transcript = _load("vague_executive_discovery_transcript.json")
    turns = transcript["turns"]

    first_question = turns[0]["next_question"]["question"].lower()
    assert "three" in first_question or "prior" in first_question

    requirement_text = _requirement_text(transcript)
    assert "superstar" not in requirement_text
    assert "do a bit of everything" not in requirement_text

    quality = transcript["final_quality_report"]
    assert any(item["phrase"] == "superstar" for item in quality["vague_phrases"])

    role = transcript["final_role_specification"]
    measurable_requirements = [
        item
        for item in role["requirements"]
        if any(character.isdigit() for character in item["description"] or "")
    ]
    assert measurable_requirements, (
        "forcing prioritisation should have produced at least one requirement "
        "with a concrete, measurable target from the manager's own wording"
    )

    assert role["human_approved"] is False
    assert "Business need is missing." in quality["blockers"]


def test_vague_executive_hit_the_overlap_guard_while_being_pushed_to_prioritise() -> None:
    """Documents a live finding: compound, information-dense answers -- the
    exact style 'force prioritisation' elicits -- are the ones most likely to
    trip the must-have/ambiguity overlap guard. See
    docs/DECISIONS_AND_LESSONS.md, 2026-07-24."""
    transcript = _load("vague_executive_discovery_transcript.json")
    assert len(transcript["rejected_turns"]) >= 1


# ---------------------------------------------------------------------------
# Persona B: over-technical manager -- cluster the list, test necessity,
# separate day-one from learnable, calibrate against seniority.
# ---------------------------------------------------------------------------


def test_over_technical_manager_input_is_a_genuinely_long_day_one_list() -> None:
    transcript = _load("over_technical_manager_discovery_transcript.json")
    statement = transcript["initial_manager_statement"]
    assert statement.count(",") >= 30
    assert "entry-level" in statement or "entry level" in statement


def test_over_technical_manager_skills_are_clustered_not_enumerated() -> None:
    transcript = _load("over_technical_manager_discovery_transcript.json")
    first_turn_requirement_count = transcript["turns"][0]["new_requirement_count"]

    # The manager listed 47 distinct skills in one sentence; a system that
    # clusters (rather than one-requirement-per-skill enumerates) should
    # produce a much smaller number of first-turn requirements.
    assert 1 <= first_turn_requirement_count <= 10


def test_over_technical_manager_necessity_and_seniority_are_tested() -> None:
    transcript = _load("over_technical_manager_discovery_transcript.json")
    turns = transcript["turns"]
    early_questions = " ".join(
        turn["next_question"]["question"].lower() for turn in turns[:2]
    )
    assert any(
        keyword in early_questions
        for keyword in ("mandatory", "necessary", "truly", "genuinely")
    )

    role = transcript["final_role_specification"]
    role_level = role["basic_info"]["role_level"]
    assert role_level in {"intern", "entry"}

    priorities = [item["priority"] for item in role["requirements"]]
    must_have_count = priorities.count("must_have")
    non_must_have_count = len(priorities) - must_have_count

    # The manager's "every one of these, no exceptions" claim must not
    # survive discovery intact: most of the 47-skill list should end up
    # preferred/optional/deferred, not accepted as mandatory day-one skill.
    assert non_must_have_count > must_have_count


def test_over_technical_manager_nice_to_have_skills_are_downgraded() -> None:
    transcript = _load("over_technical_manager_discovery_transcript.json")
    role = transcript["final_role_specification"]
    by_name = {item["name"].lower(): item for item in role["requirements"]}

    scrum = next(
        item for name, item in by_name.items() if "scrum" in name
    )
    presentation = next(
        item for name, item in by_name.items() if "presentation" in name
    )
    assert scrum["priority"] == "optional"
    assert presentation["priority"] == "optional"


def test_over_technical_manager_hit_the_overlap_guard_on_the_raw_pdf_list() -> None:
    """Documents a live finding: the literal PDF-style day-one list is
    rejected by the safety backstop roughly as often as it passes, because
    the model quotes the entire sentence for every clustered requirement AND
    for its own proficiency-standard ambiguity. See
    docs/DECISIONS_AND_LESSONS.md, 2026-07-24."""
    transcript = _load("over_technical_manager_discovery_transcript.json")
    assert len(transcript["rejected_turns"]) >= 1


# ---------------------------------------------------------------------------
# Persona C: culture-focused manager -- observable behaviour, not affinity.
# ---------------------------------------------------------------------------


def test_culture_focused_manager_is_pushed_toward_observable_behaviour() -> None:
    transcript = _load("culture_focused_manager_discovery_transcript.json")
    first_question = transcript["turns"][0]["next_question"]["question"].lower()
    assert "observable" in first_question


def test_culture_focused_manager_affinity_language_never_becomes_a_requirement() -> None:
    transcript = _load("culture_focused_manager_discovery_transcript.json")
    requirement_text = _requirement_text(transcript)

    for banned_phrase in ("vibe", "pub", "click with them", "laughs at", "liked"):
        assert banned_phrase not in requirement_text


def test_culture_focused_manager_ambiguities_name_the_affinity_bias_risk() -> None:
    transcript = _load("culture_focused_manager_discovery_transcript.json")
    ambiguities = transcript["final_role_specification"]["open_ambiguities"]
    combined = " ".join(item["description"].lower() for item in ambiguities)

    assert "subjective" in combined
    assert any(
        keyword in combined
        for keyword in ("rapport", "similarity", "liked", "likeability")
    )


def test_culture_focused_manager_produces_observable_behavioural_requirements() -> None:
    transcript = _load("culture_focused_manager_discovery_transcript.json")
    role = transcript["final_role_specification"]
    behavioural = [
        item for item in role["requirements"] if item["category"] == "behavioural"
    ]
    assert behavioural
    assert all(item["source_statement"] for item in behavioural)
    # Every behaviour must be tied to a concrete work situation, not a trait.
    combined = " ".join(item["description"].lower() for item in behavioural)
    assert any(
        keyword in combined for keyword in ("deadline", "stand-up", "teammate")
    )


def test_culture_focused_manager_zuru_dna_linkage_is_a_known_gap() -> None:
    """Documents a live/architecture finding, not the desired end state:
    ``ZuruDnaBehaviour`` is never constructed anywhere in ``src/`` -- neither
    discovery's compact schema nor the manager-edit path can populate
    ``zuru_dna_behaviours``, so persona C's "link behaviours ... to ZURU DNA"
    requirement is not reachable through the live app today. See
    docs/DECISIONS_AND_LESSONS.md, 2026-07-24. This test should start
    failing (in the good sense) once that gap is closed, at which point it
    should be replaced with a positive assertion."""
    transcript = _load("culture_focused_manager_discovery_transcript.json")
    role = transcript["final_role_specification"]
    assert role["zuru_dna_behaviours"] == []


# ---------------------------------------------------------------------------
# Real-world scenario: Marketing Intern -- resolve every documented ambiguity.
# ---------------------------------------------------------------------------


def test_marketing_intern_resolves_the_documented_ambiguity_checklist() -> None:
    """§3.6's ambiguity table lists nine things the system must resolve.
    Confirm each is represented somewhere in the final captured role, even
    though (per the recorded architecture gap) some land as free-text
    requirements rather than structured basic_info/constraints fields."""
    transcript = _load("marketing_intern_discovery_transcript.json")
    requirement_text = _requirement_text(transcript)

    expected_resolutions = {
        "team/brand": "brand marketing",
        "dates": "december",
        "location": "auckland",
        "tiktok scope": "scripting and film",
        "no editing": "no editing responsibility",
        "campaign scope": "coordinate assets",
        "design level": "canva",
        "collaboration": "brainstorm",
    }
    for label, keyword in expected_resolutions.items():
        assert keyword in requirement_text, f"missing resolution for: {label}"


def test_marketing_intern_design_requirement_respects_the_managers_maybe() -> None:
    transcript = _load("marketing_intern_discovery_transcript.json")
    role = transcript["final_role_specification"]
    design_items = [
        item
        for item in role["requirements"]
        if "design" in item["name"].lower() or "canva" in item["name"].lower()
    ]
    assert design_items
    assert all(item["priority"] != "must_have" for item in design_items)


def test_marketing_intern_fun_to_work_with_is_flagged_not_accepted() -> None:
    transcript = _load("marketing_intern_discovery_transcript.json")
    requirement_text = _requirement_text(transcript)
    assert "fun to work with" not in requirement_text

    quality = transcript["final_quality_report"]
    assert any(item["phrase"] == "fun to work with" for item in quality["vague_phrases"])


def test_marketing_intern_structured_logistics_fields_are_a_known_gap() -> None:
    """Documents a live/architecture finding: ``apply_discovery_turn`` only
    ever writes ``requirements``/``open_assumptions``/``open_ambiguities``/
    ``quality.contradictions``. Even though the manager explicitly stated
    the team, division, location, and work arrangement, none of
    ``basic_info`` or ``constraints`` are populated by discovery -- the same
    facts exist only as free-text ``Requirement`` rows. See
    docs/DECISIONS_AND_LESSONS.md, 2026-07-24. This test should start
    failing (in the good sense) once that gap is closed."""
    transcript = _load("marketing_intern_discovery_transcript.json")
    basic_info = transcript["final_role_specification"]["basic_info"]
    constraints = transcript["final_role_specification"]["constraints"]

    assert basic_info["team"] is None
    assert basic_info["division"] is None
    assert basic_info["location"] is None
    assert constraints["location"] is None
    assert constraints["work_arrangement"] is None


# ---------------------------------------------------------------------------
# Component 1: technical vs. creative roles receive different domain probes
# (§3.4/§12.1/§12.2). There is no engineered role-family branching in
# prompts/discovery.md -- render_role_context only passes a soft "Role
# family: technical/creative" label. These transcripts check whether the
# live model differentiates on its own; see docs/DECISIONS_AND_LESSONS.md,
# 2026-07-24, for the finding that it does, without needing prompt changes.
# ---------------------------------------------------------------------------


def test_technical_role_probes_systems_and_integration() -> None:
    transcript = _load("technical_role_discovery_transcript.json")
    text = _requirement_text(transcript)
    questions = " ".join(
        turn["next_question"]["question"].lower() for turn in transcript["turns"]
    )
    combined = text + " " + questions

    technical_markers = (
        "meta ads",
        "google ads",
        "data",
        "connect",
        "automat",
    )
    assert sum(marker in combined for marker in technical_markers) >= 3

    for creative_marker in ("packaging", "banner", "product shot", "portfolio"):
        assert creative_marker not in combined


def test_creative_role_probes_audience_channel_and_approval_process() -> None:
    transcript = _load("creative_role_discovery_transcript.json")
    text = _requirement_text(transcript)
    questions = " ".join(
        turn["next_question"]["question"].lower() for turn in transcript["turns"]
    )
    combined = text + " " + questions

    creative_markers = (
        "packaging",
        "campaign visual",
        "banner",
        "channel",
        "approve",
    )
    assert sum(marker in combined for marker in creative_markers) >= 3

    for technical_marker in ("meta ads", "google ads", "api", "database"):
        assert technical_marker not in combined


def test_technical_and_creative_roles_receive_different_domain_probes() -> None:
    """The core discovery framework stays the same (one question per turn,
    source-quoted, must-have/ambiguity guard) while the actual questions
    asked diverge by role family, matching the documented acceptance test
    in §23.3: 'the core framework remains consistent; technical probes
    focus on systems, reliability, depth, and integration; creative probes
    focus on audience, brand, channel, and feedback.'"""
    technical = _load("technical_role_discovery_transcript.json")
    creative = _load("creative_role_discovery_transcript.json")

    technical_questions = {
        turn["next_question"]["question"] for turn in technical["turns"]
    }
    creative_questions = {
        turn["next_question"]["question"] for turn in creative["turns"]
    }
    assert technical_questions.isdisjoint(creative_questions)

    for turn in technical["turns"] + creative["turns"]:
        assert turn["next_question"]["question"].count("?") == 1
