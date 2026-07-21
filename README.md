# ZURU Talent Copilot

ZURU Talent Copilot is a human-led hiring decision-support prototype. Its intended workflow turns ambiguous manager input into an approved, traceable role specification, then supports hiring-pack generation and evidence-based candidate review. It never makes a final hire or reject decision.

## Current status

This initial foundation includes typed configuration, Pydantic v2 domain models, deterministic discovery-stage checks, safe JSON persistence, an isolated OpenRouter client, a synthetic Marketing Intern fixture, initial prompt templates, a restrained Streamlit shell, and automated tests. Adaptive discovery, hiring-pack generation, candidate scoring, and CV parsing are intentionally not implemented yet.

## Repository structure

```text
app.py                    Streamlit shell
src/                      Configuration, schemas, workflow, storage, LLM boundary
prompts/                  Initial discovery, generation, and evaluation templates
data/fixtures/            Trackable synthetic scenarios
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

The Streamlit application does not contact OpenRouter in this setup phase.

## Project documentation

- [Setup and working guide](docs/01_SETUP_AND_WORKFLOW_GUIDE.md)
- [Development plan and timeline](docs/02_DEVELOPMENT_PLAN_AND_TIMELINE.md)
- [Technical design and methods](docs/03_TECHNICAL_DESIGN_AND_METHODS.md)
- [ZURU task context and acceptance criteria](docs/ZURU_AI_Integration_Internship_Codex_Context.md)

## Next implementation phase

Build the state-controlled discovery vertical slice: accept the synthetic Marketing Intern statement, extract one validated incremental update, ask one evidence-focused clarification question, preserve manager corrections, and keep human approval as the gate for downstream generation.
