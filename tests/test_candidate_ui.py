"""Streamlit AppTest coverage for the Phase 7 Candidate Evidence interface."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from src.evaluation import evaluate_candidate
from src.storage import AuditLog
from src.workflow import WorkflowState
from tests.phase7_helpers import (
    EVALUATION_TIME,
    FakeEvaluationClient,
    approved_marketing_role,
    pack_from_draft,
    strong_response_set,
)


def _seeded_app(*, include_evaluation: bool) -> AppTest:
    role = approved_marketing_role()
    pack = pack_from_draft(session_id="ui")
    evaluation = (
        evaluate_candidate(
            role=role,
            hiring_pack=pack,
            response_set=strong_response_set(),
            llm_client=FakeEvaluationClient(),
            actor="TA Partner",
            evaluated_at=EVALUATION_TIME,
            id_factory=lambda: "evaluation_ui",
        )
        if include_evaluation
        else None
    )
    app = AppTest.from_file("app.py", default_timeout=20)
    app.session_state["workflow_state"] = WorkflowState(
        role_specification=role
    )
    app.session_state["hiring_pack"] = pack
    app.session_state["candidate_evaluation"] = evaluation
    app.session_state["session_id"] = "ui"
    app.session_state["audit_log"] = AuditLog(session_id="ui")
    app.session_state["discovery_messages"] = []
    return app


def test_empty_application_state_has_no_exceptions() -> None:
    app = AppTest.from_file("app.py", default_timeout=20).run()

    assert not app.exception
    assert "Candidate Evidence" in [item.value for item in app.subheader]


def test_approved_role_and_valid_pack_render_candidate_response_inputs() -> None:
    app = _seeded_app(include_evaluation=False).run()
    response_key = "candidate_answer_hiring_pack_fixed_1_sq_001"

    assert not app.exception
    assert "Run evidence evaluation" in [button.label for button in app.button]
    assert len(
        [
            area
            for area in app.text_area
            if area.key and area.key.startswith("candidate_answer_")
        ]
    ) == 5

    app.text_area(key=response_key).set_value(
        "I changed a campaign hook after reviewing retention."
    ).run()

    assert (
        app.text_area(key=response_key).value
        == "I changed a campaign hook after reviewing retention."
    )


def test_persisted_evidence_assessments_and_review_controls_render() -> None:
    app = _seeded_app(include_evaluation=True).run()
    markdown = [item.value for item in app.markdown]
    button_labels = [button.label for button in app.button]

    assert not app.exception
    assert "## Evaluation for candidate_strong_001 (version 1)" in markdown
    assert "### Extracted evidence" in markdown
    assert "### Requirement assessments" in markdown
    assert button_labels.count("Save human review") == 3
    assert any(
        metric.label == "Must-have coverage" for metric in app.metric
    )


def test_rendering_candidate_evaluation_emits_no_audit_event_or_secret() -> None:
    app = _seeded_app(include_evaluation=True).run()
    rendered_values = [
        str(element.value)
        for collection in (
            app.markdown,
            app.caption,
            app.info,
            app.warning,
            app.error,
        )
        for element in collection
    ]

    assert not app.exception
    assert app.session_state["audit_log"].events == []
    assert all("OPENROUTER_API_KEY" not in value for value in rendered_values)
    assert all("Traceback" not in value for value in rendered_values)
