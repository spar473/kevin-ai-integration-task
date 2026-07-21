---
title: "ZURU Talent Copilot — Setup and Working Guide"
document_type: "repository setup, tooling, and operating workflow"
status: "Working implementation guide"
last_updated: "2026-07-21"
---

# ZURU Talent Copilot — Setup and Working Guide

## 0. Purpose

This document gives the exact working procedure for setting up and developing the ZURU AI hiring prototype on Windows.

It explains how these pieces fit together:

- **ChatGPT** — product reasoning, research, architecture review, debugging explanation, and presentation preparation.
- **Codex** — repository-aware coding agent that reads the project, edits files, runs commands, and tests changes.
- **VS Code** — the main place where you inspect, edit, run, and debug the application.
- **PowerShell** — the terminal used to create the environment and run commands.
- **Git** — local version control inside the project folder.
- **GitHub** — optional remote backup and repository hosting.
- **OpenRouter** — the model provider used by the prototype at runtime.
- **Streamlit** — the web interface for the prototype.
- **Python, Pydantic, and JSON** — the application language, structured data validation, and prototype persistence.

This guide assumes the main project context file already exists:

```text
docs/ZURU_AI_Integration_Internship_Codex_Context.md
```

That context file remains the main business and task reference. This guide does not repeat the whole task brief.

---

# 1. Confirmed design decisions

| Decision area | Selected approach | Reason |
|---|---|---|
| User interface | **Hybrid form + conversational follow-up** | Basic facts are faster in a form; ambiguity is better handled conversationally. |
| Model strategy | **One primary model** | Easier to control, test, explain, and finish within the assignment period. |
| Workflow control | **Explicit state machine** | Prevents wandering conversations and enables deterministic completion checks. |
| Persistence | **JSON files for the prototype** | Fast to build, inspect, version, and demonstrate. |
| Candidate inputs | **Both screening responses and CV/resume evidence** | Screening responses are core; CV evidence adds a useful secondary route. |
| Framework | **Streamlit** | Fastest route to a usable Python web prototype. |
| Model access | **OpenRouter through a small client abstraction** | Keeps business logic independent from the provider API. |
| Structured output | **Pydantic models plus JSON Schema** | Reduces malformed outputs and supports tests. |
| Hiring decision | **Human-owned** | The system supports review and never automatically rejects candidates. |

---

# 2. Understand Git, GitHub, and the project folder

## 2.1 Git is not a storage location

A Git repository is an ordinary folder containing a hidden `.git` directory.

For example:

```text
C:\Users\Kevin\Documents\GitHub\zuru-talent-copilot\
```

may contain:

```text
.git\
.venv\
src\
tests\
docs\
app.py
README.md
```

The `.git` directory records history. The repository can physically live anywhere on your computer.

## 2.2 GitHub is the remote copy

GitHub is a remote host for the repository.

```text
Local Windows folder
    ↕ git push / git pull
Private GitHub repository
```

For this assignment, keep the GitHub repository **private** until ZURU confirms that the task materials and prototype may be shared publicly.

Never commit:

- the OpenRouter key;
- `.env`;
- real candidate information;
- confidential ZURU resources that you are not authorised to publish;
- temporary debug logs containing prompts or candidate text.

## 2.3 Recommended folder location

Use one of these:

```text
C:\Users\<YOUR_USERNAME>\Documents\GitHub\zuru-talent-copilot
```

or:

```text
C:\dev\zuru-talent-copilot
```

Use `C:\dev` when Documents is automatically synchronised by OneDrive. OneDrive can create file-locking and sync noise around `.venv`, temporary files, and Git metadata.

Check:

```powershell
[Environment]::GetFolderPath("MyDocuments")
```

If the result contains `OneDrive`, prefer:

```text
C:\dev\zuru-talent-copilot
```

The project is still fully tracked by Git and can still be pushed to GitHub.

---

# 3. Safely move the repository you already created

You said you have already used the initial PowerShell commands. Move the repository and recreate the virtual environment afterward.

Python virtual environments often contain absolute paths. Moving `.venv` can create confusing interpreter errors.

## 3.1 Inspect the current location

Open PowerShell in the existing project folder:

```powershell
Get-Location
Get-ChildItem -Force
git status
```

Confirm:

- this is the intended ZURU project;
- `.git` exists;
- unrelated files are not inside it.

## 3.2 Deactivate the virtual environment

If the prompt begins with `(.venv)`:

```powershell
deactivate
```

If `deactivate` is not recognised, close that PowerShell window and open a new one.

## 3.3 Move to the parent folder

```powershell
cd ..
Get-ChildItem
```

Assume the folder is called:

```text
zuru-talent-copilot
```

## 3.4 Create the destination

Documents option:

```powershell
New-Item -ItemType Directory -Force "$HOME\Documents\GitHub"
```

Non-OneDrive option:

```powershell
New-Item -ItemType Directory -Force "C:\dev"
```

## 3.5 Move the entire repository

Documents option:

```powershell
Move-Item ".\zuru-talent-copilot" "$HOME\Documents\GitHub\zuru-talent-copilot"
cd "$HOME\Documents\GitHub\zuru-talent-copilot"
```

`C:\dev` option:

```powershell
Move-Item ".\zuru-talent-copilot" "C:\dev\zuru-talent-copilot"
cd "C:\dev\zuru-talent-copilot"
```

The hidden `.git` folder moves with the project.

## 3.6 Recreate the virtual environment

```powershell
if (Test-Path ".venv") {
    Remove-Item -Recurse -Force ".venv"
}
```

Create:

```powershell
py -3.12 -m venv .venv
```

If unavailable:

```powershell
py -0p
```

Then use an installed modern version:

```powershell
py -3.11 -m venv .venv
```

Activate:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

This changes the policy only for the current PowerShell session.

## 3.7 Confirm the move

```powershell
git status
python --version
where.exe python
```

The Python path should point inside:

```text
...\zuru-talent-copilot\.venv\Scripts\python.exe
```

---

# 4. Verify prerequisites

Run:

```powershell
git --version
py --version
code --version
```

Expected:

- Git prints a version.
- Python launcher prints a version.
- VS Code prints a version.

If `code` is not recognised:

1. Open VS Code.
2. Use **File → Open Folder**.
3. Select the repository.
4. Continue from the integrated terminal.

Codex should be used with the repository root open. It can inspect files, edit the repository, run commands, and execute tests. Treat it as a repository-aware pair programmer rather than a one-shot project generator.

---

# 5. Open the project correctly in VS Code

From the project root:

```powershell
code .
```

In VS Code:

1. Confirm Explorer shows the root of `zuru-talent-copilot`.
2. Open **Terminal → New Terminal**.
3. Confirm the terminal starts in the project root.
4. Activate the environment if needed:

```powershell
.\.venv\Scripts\Activate.ps1
```

5. Select the interpreter:
   - `Ctrl+Shift+P`;
   - `Python: Select Interpreter`;
   - choose `.venv\Scripts\python.exe`.

Open the entire repository folder, not only one Python file.

---

# 6. Install the initial dependencies

Use a small dependency set:

```powershell
python -m pip install --upgrade pip
pip install streamlit pydantic python-dotenv httpx openai pytest
```

| Package | Purpose |
|---|---|
| `streamlit` | Web prototype UI |
| `pydantic` | Typed schemas and validation |
| `python-dotenv` | Reads `.env` |
| `httpx` | Controlled HTTP handling |
| `openai` | OpenAI-compatible client usable with OpenRouter |
| `pytest` | Automated testing |

Add CV support later:

```powershell
pip install pypdf python-docx
```

Freeze:

```powershell
pip freeze > requirements.txt
```

---

# 7. Create the minimum repository structure

```powershell
New-Item -ItemType Directory -Force docs
New-Item -ItemType Directory -Force src
New-Item -ItemType Directory -Force tests
New-Item -ItemType Directory -Force data\reference
New-Item -ItemType Directory -Force data\sessions
New-Item -ItemType Directory -Force data\fixtures
New-Item -ItemType Directory -Force scripts
New-Item -ItemType Directory -Force prompts
```

Create files:

```powershell
New-Item -ItemType File -Force app.py
New-Item -ItemType File -Force README.md
New-Item -ItemType File -Force .gitignore
New-Item -ItemType File -Force .env.example
New-Item -ItemType File -Force src\__init__.py
```

Recommended early structure:

```text
zuru-talent-copilot/
├── app.py
├── README.md
├── requirements.txt
├── .env
├── .env.example
├── .gitignore
├── docs/
│   ├── ZURU_AI_Integration_Internship_Codex_Context.md
│   ├── 01_SETUP_AND_WORKFLOW_GUIDE.md
│   ├── 02_DEVELOPMENT_PLAN_AND_TIMELINE.md
│   └── 03_TECHNICAL_DESIGN_AND_METHODS.md
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── llm_client.py
│   ├── workflow.py
│   ├── storage.py
│   ├── readiness.py
│   ├── discovery.py
│   ├── generation.py
│   └── evaluation.py
├── prompts/
│   ├── discovery.md
│   ├── generation.md
│   └── evaluation.md
├── data/
│   ├── reference/
│   ├── sessions/
│   └── fixtures/
├── tests/
│   ├── test_models.py
│   ├── test_workflow.py
│   ├── test_readiness.py
│   └── test_evaluation_rules.py
└── scripts/
    └── test_openrouter.py
```

Keep this structure small until more separation is actually needed.

---

# 8. Create `.gitignore` before adding the API key

```gitignore
# Secrets
.env
.env.*
!.env.example

# Python
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Streamlit
.streamlit/secrets.toml

# Runtime and generated data
data/sessions/
data/outputs/
logs/
*.log

# Candidate files
uploads/
candidate_uploads/

# IDE and OS
.vscode/
.idea/
.DS_Store
Thumbs.db
```

Synthetic fixtures under `data/fixtures/` may be committed.

---

# 9. Configure OpenRouter safely

`.env.example`:

```env
OPENROUTER_API_KEY=replace_with_your_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=replace_with_confirmed_model_slug
APP_ENV=development
```

Local `.env`:

```env
OPENROUTER_API_KEY=YOUR_REAL_KEY
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=YOUR_SELECTED_MODEL
APP_ENV=development
```

Rules:

- Never paste the real key into ChatGPT or Codex.
- Never show it in screenshots or slides.
- Never log request headers.
- Never commit `.env`.
- Check `git status` before every commit.

Confirm it is ignored:

```powershell
git status --ignored
```

OpenRouter provides an OpenAI-compatible endpoint at `https://openrouter.ai/api/v1`. Compatible models may support strict JSON Schema output, but Pydantic validation is still required.

---

# 10. Place the documentation into the repository

Rename the context file to:

```text
ZURU_AI_Integration_Internship_Codex_Context.md
```

Place all four files under `docs/`:

```text
docs/
├── ZURU_AI_Integration_Internship_Codex_Context.md
├── 01_SETUP_AND_WORKFLOW_GUIDE.md
├── 02_DEVELOPMENT_PLAN_AND_TIMELINE.md
└── 03_TECHNICAL_DESIGN_AND_METHODS.md
```

Keep the documentation set limited to these four main files. Do not create overlapping daily planning documents.

---

# 11. Create the first working application

`app.py`:

```python
import streamlit as st

st.set_page_config(
    page_title="ZURU Talent Copilot",
    page_icon="🧭",
    layout="wide",
)

st.title("ZURU Talent Copilot")
st.caption(
    "Human-led AI assistance for role discovery, hiring-pack generation, "
    "and candidate evidence review."
)

st.info("Project environment is configured. Core workflow implementation begins next.")
```

Run:

```powershell
streamlit run app.py
```

Stop with:

```text
Ctrl+C
```

Create `tests/test_smoke.py`:

```python
def test_environment_smoke() -> None:
    assert True
```

Run:

```powershell
pytest
```

Do not continue until both work.

---

# 12. Configure Git identity

Check:

```powershell
git config --global user.name
git config --global user.email
```

Set if blank:

```powershell
git config --global user.name "Sooyoung Kevin Park"
git config --global user.email "YOUR_GITHUB_EMAIL"
```

---

# 13. Make the first commit

Inspect:

```powershell
git status
git diff
```

Confirm `.env` is absent.

Commit:

```powershell
git add .
git status
git commit -m "chore: initialise ZURU talent copilot repository"
```

Useful commit names:

```text
chore: initialise project structure
feat: add role specification schemas
feat: connect OpenRouter structured output
feat: add discovery state machine
test: add vague executive scenario
fix: preserve unresolved requirement state
docs: document candidate evidence workflow
```

---

# 14. Create a private GitHub repository

On GitHub:

1. Create repository `zuru-talent-copilot`.
2. Select **Private**.
3. Do not initialise it with README, `.gitignore`, or licence.
4. Copy the HTTPS URL.

Then:

```powershell
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/zuru-talent-copilot.git
git push -u origin main
```

Verify:

```powershell
git remote -v
git status
```

If a remote exists:

```powershell
git remote set-url origin https://github.com/YOUR_USERNAME/zuru-talent-copilot.git
```

Daily stable checkpoint:

```powershell
git status
git add .
git commit -m "YOUR MESSAGE"
git push
```

---

# 15. Use Codex in VS Code

Start each substantial task with:

```text
Read these files before proposing changes:

1. docs/ZURU_AI_Integration_Internship_Codex_Context.md
2. docs/02_DEVELOPMENT_PLAN_AND_TIMELINE.md
3. docs/03_TECHNICAL_DESIGN_AND_METHODS.md

Treat [REQUIRED] items in the context pack as acceptance criteria.
Do not edit files yet.
Inspect the repository and propose the smallest next implementation step.
```

## Codex operating loop

1. Ask Codex to inspect.
2. Ask for a plan without editing.
3. Review the plan.
4. Ask for one bounded implementation.
5. Ask it to run relevant tests.
6. Inspect `git diff`.
7. Run the application yourself.
8. Commit only when it works.

## Good prompt structure

```text
Context:
- Project purpose.
- Authoritative files.

Objective:
- One concrete feature.

Constraints:
- Files it may change.
- Libraries it may add.
- Safety and scope restrictions.

Acceptance criteria:
- Observable behaviour.
- Tests that must pass.

Before editing:
- Inspect relevant files.
- State the plan.
```

Example:

```text
Read the context and technical design documents.

Objective:
Implement the initial Pydantic domain models only.

Constraints:
- Do not add UI.
- Do not call OpenRouter.
- Use Pydantic v2.
- Preserve source statements and human-review flags.
- Keep models in src/models.py.
- Add tests in tests/test_models.py.

Acceptance criteria:
- A valid RoleSpecification can be created.
- Invalid confidence values are rejected.
- Requirement priority uses an enum.
- Every requirement preserves a source statement.
- pytest passes.

First inspect the repository and explain your plan. Do not edit until the plan is complete.
```

Avoid:

```text
Build the whole app.
```

Avoid asking for agents, RAG, authentication, databases, deployment, and dashboards at once.

Never give Codex the key.

---

# 16. Use ChatGPT during the build

Use ChatGPT for:

- task interpretation;
- architecture trade-offs;
- schema design review;
- reviewing Codex-generated code;
- explaining errors;
- test-scenario design;
- prompt evaluation;
- output quality review;
- assumptions and risks;
- presentation preparation;
- technical Q&A practice.

ChatGPT does not automatically know the latest state of local files.

Provide:

- the relevant file;
- current Git diff;
- error trace;
- repository tree;
- prompt and redacted output;
- test report.

Use Codex for repository-aware editing. Use ChatGPT for reasoning and review.

```text
You
    Own product decisions and approve changes.

ChatGPT
    Helps reason, research, review, and explain.

Codex
    Implements bounded repository changes and runs tests.

VS Code
    Lets you inspect, edit, debug, and run.

Git
    Records each working increment.

GitHub
    Stores the private remote copy.
```

---

# 17. Daily start routine

```powershell
cd "YOUR_PROJECT_PATH"
git status
git pull
.\.venv\Scripts\Activate.ps1
python --version
pytest
streamlit run app.py
```

Only use `git pull` after the remote is configured.

Then choose exactly one objective from:

```text
docs/02_DEVELOPMENT_PLAN_AND_TIMELINE.md
```

---

# 18. Daily end routine

1. Run tests:

```powershell
pytest
```

2. Run the changed flow:

```powershell
streamlit run app.py
```

3. Inspect:

```powershell
git status
git diff
```

4. Confirm staged content:

```powershell
git diff --cached
```

5. Commit and push:

```powershell
git add .
git commit -m "DESCRIBE THE WORKING CHANGE"
git push
```

6. Record in an existing document or GitHub issue:
   - what works;
   - what failed;
   - what remains;
   - exact next step.

Do not create a new markdown file for every session.

---

# 19. Immediate setup checklist

## Repository location

- [ ] Decide between `Documents\GitHub` and `C:\dev`.
- [ ] Move the repository.
- [ ] Delete and recreate `.venv`.
- [ ] Confirm `git status`.

## Environment

- [ ] Activate `.venv`.
- [ ] Select it in VS Code.
- [ ] Install dependencies.
- [ ] Create `requirements.txt`.
- [ ] Confirm `pytest`.
- [ ] Confirm Streamlit.

## Security

- [ ] Create `.gitignore`.
- [ ] Create `.env.example`.
- [ ] Create local `.env`.
- [ ] Confirm `.env` is ignored.
- [ ] Never expose the key.

## Documentation

- [ ] Place the context pack in `docs/`.
- [ ] Add the three implementation documents.
- [ ] Keep only four main project documents.

## Git and GitHub

- [ ] Make first local commit.
- [ ] Create private GitHub repository.
- [ ] Add remote.
- [ ] Push `main`.
- [ ] Confirm privacy.

## Codex

- [ ] Open repository root in VS Code.
- [ ] Ask Codex to read documentation.
- [ ] Ask for a plan first.
- [ ] Implement only the first schema phase.
- [ ] Inspect diff and tests yourself.

---

# 20. First implementation milestone

The first milestone is:

```text
Manager enters the Marketing Intern statement
    -> application sends one structured request
    -> model returns schema-valid JSON
    -> Pydantic validates it
    -> application displays extracted requirements and one next question
```

Do not build CV upload, dashboards, authentication, or visual polish before this vertical slice works.

---

# 21. References

- OpenAI Help Center, **Using Codex with your ChatGPT plan**.
- OpenRouter, **Quickstart Guide**.
- OpenRouter, **Structured Outputs**.
