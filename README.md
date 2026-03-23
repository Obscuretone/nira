<p align="center">
  <img src="nira_app/assets/nira.png" alt="Nira logo" width="360">
</p>

# Nira

Nira is a local issue tracker for a single workspace.

It gives you:

- a shell-first CLI for automation and tools like Codex
- a local web UI for human editing and browsing
- SQLite storage in `.nira/nira.db`
- Markdown bodies and resolution notes
- lightweight linking between related tickets

It is intentionally closer to "near Jira" than "mini Jira".

## Requirements

- Python 3.13 or newer
- Jinja2
- SQLAlchemy 2.0+

## Installation

Nira is a standard Python package. You can install it globally, with `pipx`, or into a virtual environment.

```bash
# Clone the repository
git clone https://github.com/Obscuretone/nira.git
cd nira

# Install using pipx (recommended for CLIs)
pipx install .

# Or install in editable mode for development
python3 -m pip install -e .
```

Alternatively, you can run the `./nira` shim script directly from the source directory without installing.

## Quick Start

Initialize Nira inside the repo or project directory you want to track:

```bash
cd ~/Code/emh
~/Code/nira/nira init
```

By default, `init` derives the ticket prefix from the folder name. Single-word folders stay uppercased, and multi-word folder names collapse into an acronym. For example:

- `~/Code/emh` becomes `EMH`
- `~/Code/employment-matching-hub` becomes `EMH`
- `~/Code/EmploymentMatchingHub` becomes `EMH`

You can override that at init time:

```bash
~/Code/nira/nira init --project-key EMH
```

Create and inspect a ticket:

```bash
~/Code/nira/nira new "Evaluate Tortoise alternatives" --source "architecture review"
~/Code/nira/nira list
~/Code/nira/nira show EMH-1
```

Start the web UI:

```bash
~/Code/nira/nira serve
```

Then open `http://127.0.0.1:8765`.

Ticket labels like `EMH-1` are display keys derived from the current workspace prefix and ticket number. Internally, tickets use integer primary keys in SQLite.

## How Nira Finds Your Workspace

Most commands work from the current directory or any child directory inside a Nira workspace.

Nira walks upward until it finds `.nira/`.

That means these both work:

```bash
cd ~/Code/emh
~/Code/nira/nira list
```

```bash
cd ~/Code/emh/app/services
~/Code/nira/nira list
```

If you want to point at a different workspace explicitly, use `--root`:

```bash
~/Code/nira/nira --root ~/Code/emh list
```

## CLI Reference

Show built-in help:

```bash
./nira help
./nira help new
```

Initialize a workspace:

```bash
./nira init
./nira init --project-key EMH
```

Create tickets:

```bash
./nira new "Write release notes"
./nira new "Fix login redirect" --source "user report" --type bug --priority high
./nira new "Draft migration plan" --body "## Summary\nOutline the cutover.\n"
printf '## Summary\nCreated from stdin.\n' | ./nira new "Pipeline-created ticket"
```

List and show tickets:

```bash
./nira list
./nira list --status open
./nira list --status in_progress --priority high
./nira list --type bug
./nira show EMH-1
```

Update tickets:

```bash
./nira update EMH-1 --status in_progress
./nira update EMH-1 --priority critical --type bug
./nira update EMH-1 --source "architecture review"
```

Edit long-form content in `$EDITOR`:

```bash
./nira edit EMH-1
./nira edit EMH-1 --field resolution
```

Close and reopen:

```bash
./nira close EMH-1 --reason completed
./nira reopen EMH-1
```

Link related tickets:

```bash
./nira link EMH-1 EMH-2
./nira links
./nira links EMH-1
./nira unlink EMH-1 EMH-2
```

Aliases:

- `create` is an alias for `new`
- `get` is an alias for `show`

## Web Interface

Start the server:

```bash
./nira serve
./nira serve --host 127.0.0.1 --port 8765
./nira serve --reload
```

The web UI is local-only by default.
`--reload` is intended for local development and restarts the server when Nira source or template files change.

The interface includes:

- a ticket list with filtering by status
- sorting by clicking table headers or using the sort controls
- a create-ticket screen with title, body, source, type, and priority
- a ticket detail page with inline editing
- a settings page for renaming the workspace ticket prefix
- automatic saves when status, priority, or type dropdowns change
- related-ticket linking
- a WYSIWYG Markdown editor for ticket bodies

Changing the workspace prefix updates displayed ticket labels everywhere. For example, renaming the prefix from `EMH` to `NIRA` turns `EMH-4` into `NIRA-4` without changing the underlying ticket record.

Current browser-supported ticket types:

- `task`
- `bug`

Current ticket statuses:

- `open`
- `in_progress`
- `closed`

In the web UI these are labeled as:

- `open`
- `in progress`
- `completed`

## Storage

Nira stores all data in:

```text
.nira/nira.db
```

SQLite is the source of truth. There is no separate export or sync layer.

The database stores:

- workspace settings
- the default ticket prefix
- integer ticket primary keys plus user-facing ticket numbers
- ticket metadata
- Markdown bodies
- Markdown resolution notes
- related ticket links

## Development

Set up a local dev environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Run the full test suite:

```bash
pytest
```

This runs both the focused unit tests in `tests/test_unit.py` and the end-to-end integration coverage in `tests/test_integration.py`.

Run static analysis:

```bash
ruff check .
mypy --config-file mypy.ini nira_app tests
pyright
```

Configuration files:

- `pyproject.toml`
- `ruff.toml`
- `mypy.ini`
- `pyrightconfig.json`

The repo ignores workspace state and local tooling output through `.gitignore`, including `.nira/`.

## Project Layout

```text
pyproject.toml       Package definition and dependencies
nira                 Optional CLI entrypoint shim
nira_app/cli.py      CLI behavior and entrypoint
nira_app/storage.py  SQLite storage and ticket operations
nira_app/web.py      Local server-rendered web UI and routing
nira_app/templates/  Jinja2 HTML templates for the web interface
nira_app/markdown.py Minimal Markdown renderer for previews
tests/               Integration tests for CLI and HTTP flows
```
