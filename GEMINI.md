# Nira - Gemini CLI Foundation

Nira is a local issue tracker for a single workspace, featuring a shell-first CLI and a local web UI. It prioritizes simplicity, using SQLite for storage and avoiding heavy web frameworks.

## 🛠 Technology Stack

- **Language:** Python 3.13+
- **Database:** SQLite (stored in `.nira/nira.db`)
- **ORM:** SQLAlchemy 2.0+
- **Migrations:** Alembic
- **CLI Interface:** Typer & Rich
- **Web Interface:** Custom WSGI implementation (no Flask/FastAPI), Jinja2 templates, and HTMX for interactivity.
- **Markdown:** Custom renderer in `nira_app/markdown.py`.
- **I18n:** Gettext (`_()` helper), files in `nira_app/locales/`.

## 🏗 Architectural Patterns

- **`NiraStore` (`nira_app/storage.py`):** The central repository for all data operations. Any logic that touches the database MUST reside here. It handles migrations, FTS5 search, and ticket lifecycle.
- **`NiraWebApp` (`nira_app/web.py`):** Handles web requests via a custom `Router`. Uses HTMX for partial page updates.
- **`models.py`:** Defines `TypedDict` structures for passing data between the storage layer and interfaces.
- **Search:** Uses SQLite FTS5 for full-text search, with triggers maintained in `storage.py` to keep the index updated.

## 📓 Issue Tracking & Lifecycle

### 1. Nira is the Source of Truth
The `./nira` CLI tool is the primary issue tracker for this project. You MUST use it to manage tasks, bugs, and features.
- **Consult Tickets:** Use `./nira list` and `./nira show <ID>` to understand current priorities and requirements.
- **Record Resolutions:** Use `./nira close <ID> --reason <REASON> --notes <NOTES>` when a task is completed.
- **Consistency:** Always check the status of relevant tickets before assuming a task is complete.
- **Context:** Remember that Nira is its own issue tracker. Refer to ticket bodies and resolution notes for historical context.

### 2. Verification is Mandatory
Before staging, committing, or proposing any changes, you **MUST** run the following verification suite:
```bash
./check.sh
```
This is also enforced by a pre-commit hook that runs `./check.sh` automatically. If the hook fails, the commit will be blocked until the issues are resolved. Do NOT bypass this hook with `--no-verify`.

**Testing:** ALWAYS search for and update related tests after making a code change. You must add a new test case to the existing test file (if one exists) or create a new test file to verify your changes. A change is incomplete without verification logic.

**Code Review:** You MUST perform a full code review of the `git diff` before committing. Ensure all changes are intentional, follow the architectural patterns, and do not introduce regressions or debugging code.

This script runs:
- `ruff format .`
- `ruff check .`
- `mypy --config-file mypy.ini nira_app tests`
- `pyright`
- `pytest`

### 3. Coding Standards
- **Line Length:** 120 characters (enforced by `ruff`).
- **Typing:** Strict type hints are required. Always run `mypy` and `pyright`.
- **Formatting:** Use `ruff format`.

### 4. Internationalization (i18n)
- All user-facing strings in Python and Templates MUST be wrapped in `_()`.
- Translations are managed in `nira_app/locales/` using `.po` and `.mo` files.

### 5. Database Migrations
- Never modify the schema directly.
- Use Alembic to generate and apply migrations: `alembic revision -m "description"`.
- Migrations are stored in `nira_app/migrations/alembic/versions/`.

### 6. Ticket Identity
- Tickets have an internal integer ID but are presented to the user using a project-prefix-based key (e.g., `NIRA-1`).
- Project keys are typically uppercase.

## 🧪 Testing Strategy
- **Unit & Integration:** Tests are located in `tests/`.
- **Structure:** Tests are organized by module (e.g., `tests/cli/`, `tests/storage/`, `tests/web/`).
- **Coverage:** Maintain high test coverage (aim for 90%+). Coverage reports are generated automatically by `pytest`.

## 📂 Key File Locations
- `nira_app/storage.py`: Core data logic.
- `nira_app/web.py`: Web server and request handlers.
- `nira_app/cli/`: CLI command definitions.
- `nira_app/templates/`: Jinja2 templates.
- `nira_app/locales/`: Translation files.
- `check.sh`: Quality enforcement script.
