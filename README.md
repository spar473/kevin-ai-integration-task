# ZURU Talent Copilot

ZURU Talent Copilot is a human-led hiring decision-support prototype. Its intended workflow turns ambiguous manager input into an approved, traceable role specification, then supports hiring-pack generation and evidence-based candidate review. It never makes a final hire or reject decision.

## Current status

Phases 1 through 7 and the append-only Phase 9 audit foundation are
implemented. The prototype supports state-controlled role discovery,
deterministic readiness and approval, versioned hiring-pack generation, and
requirement-level candidate response evaluation with evidence excerpts, rubric
anchors, deterministic confidence, contradiction handling, prompt-injection
controls, persistence, and explicit human review. CV parsing remains a later,
optional phase.

## Limitations and known gaps

Honest, presentation-ready summary of what this prototype does not yet do.
Full technical detail, evidence, and reasoning for every item below is in
[docs/DECISIONS_AND_LESSONS.md](docs/DECISIONS_AND_LESSONS.md).

- **Confidence is a decision-support heuristic, not a validated probability.**
  It reflects evidence specificity and consistency, not a calibrated
  statistical estimate -- treat it as a routing signal for human review, not
  a score.
- **A manager's compliant, concrete, prioritised answer can still be
  rejected** if phrased as one dense sentence, because the model sometimes
  quotes the whole sentence as source for both a `must_have` requirement and
  its own follow-up ambiguity, tripping the must-have/ambiguity overlap
  guard. The guard is intentionally kept strict rather than loosened; the
  open follow-up is to surface *which clause* overlapped in the UI instead
  of today's generic error, so a manager knows to split their answer rather
  than guess why it failed.
- **Discovery only ever extracts requirements, assumptions, and
  ambiguities.** Business need, success outcomes, responsibilities,
  constraints, the assessment plan, and ZURU DNA behaviours are completed by
  a human directly in the "Review Role" tab, not inferred by the model from
  conversation -- a deliberate choice to keep business decisions with a
  human, not a coverage shortfall to be closed by prompting the model
  differently.
- **Technical vs. creative role probing is verified, not engineered.** There
  is no explicit branching logic for role family in the discovery prompt;
  live testing showed the model differentiates its questions correctly on
  its own (systems/integration for technical roles, channel/portfolio/
  approval process for creative roles) across a handful of turns each. This
  has not been verified for every role family, or beyond a short
  conversation.
- **Resolved ambiguities can stay listed as open.** An ambiguity is only
  replaced when a later turn's wording string-matches the original phrase
  exactly; a topic resolved in different words remains listed. Don't treat
  `open_ambiguities` count as a reliable completeness signal late in a
  conversation.
- **Country-specific legal review, learning from past hiring outcomes, and
  real candidate data are explicitly out of scope.** This prototype uses
  synthetic candidate data only; production use would need jurisdiction
  configuration reviewed by legal/HR, and any outcome-based learning would
  need an audited approach to avoid reproducing historical bias.

## Repository structure

```text
app.py                    Streamlit role, pack, evidence, and review interface
src/                      Domain, workflow, generation, evaluation, storage, LLM boundary
prompts/                  Versioned discovery, generation, and evaluation contracts
data/fixtures/            Trackable synthetic and adversarial scenarios
data/sessions/            Ignored local runtime data
scripts/                  Setup checker and opt-in provider smoke test
tests/                    Focused pytest suite
docs/                     Authoritative supplied project documentation
```

## Windows setup

From PowerShell in the repository root, activate the existing environment and install direct dependencies:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If script execution is blocked, use a process-scoped policy before activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Copy the placeholder environment file locally, then replace placeholders in `.env` yourself:

```powershell
Copy-Item .env.example .env
```

Required variables are `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, and `APP_ENV`. Never commit `.env`, paste its key into chat, or include credentials in logs or screenshots.

## Run and validate

```powershell
python scripts/check_setup.py
python -m pytest -q
python -m streamlit run app.py
```

The provider smoke test is optional and manual. It makes a real request and may consume OpenRouter credits:

```powershell
python scripts/openrouter_smoke.py
```

Normal automated tests use injected fakes and never contact OpenRouter.

## Project documentation

- [Setup and working guide](docs/01_SETUP_AND_WORKFLOW_GUIDE.md)
- [Development plan and timeline](docs/02_DEVELOPMENT_PLAN_AND_TIMELINE.md)
- [Technical design and methods](docs/03_TECHNICAL_DESIGN_AND_METHODS.md)
- [Decisions and lessons](docs/DECISIONS_AND_LESSONS.md)
- [ZURU task context and acceptance criteria](docs/ZURU_AI_Integration_Internship_Codex_Context.md)

## Candidate-evaluation safety

Candidate answers are stored as source data and placed only inside explicit
untrusted prompt boundaries. Provider output is accepted only when every quote
traces to source text, every requirement and question ID exists, every score
matches the deterministic evidence-quality category and a real question rubric,
and every mapped requirement is assessed. Missing evidence remains distinct
from direct negative evidence, protected-characteristic content is excluded
from scoring, and the system never makes the final hiring decision.
