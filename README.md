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

- Python 3.11 or newer

Nira has no runtime Python dependencies outside the standard library.

## Quick Start

Initialize Nira inside the repo or project directory you want to track:

```bash
cd ~/Code/emh
~/Code/nira/nira init
```

By default, `init` derives the ticket prefix from the folder name. For example:

- `~/Code/emh` becomes `EMH`
- `~/Code/my-app` becomes `MY-APP`

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
```

The web UI is local-only by default.

The interface includes:

- a ticket list with filtering by status
- sorting by clicking table headers or using the sort controls
- a create-ticket screen with title, body, source, type, and priority
- a ticket detail page with inline editing
- automatic saves when status, priority, or type dropdowns change
- related-ticket linking
- a WYSIWYG Markdown editor for ticket bodies

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
- the default ticket prefix and next ticket number
- ticket metadata
- Markdown bodies
- Markdown resolution notes
- related ticket links

## Development

Set up a local dev environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Run the full integration suite:

```bash
python3 -m unittest discover -s tests -v
```

Run static analysis:

```bash
.venv/bin/flake8 nira nira_app tests
.venv/bin/mypy
.venv/bin/pyright
```

Configuration files:

- `.flake8`
- `mypy.ini`
- `pyrightconfig.json`

The repo ignores workspace state and local tooling output through `.gitignore`, including `.nira/`.

## Project Layout

```text
nira                 CLI entrypoint
nira_app/cli.py      CLI behavior
nira_app/storage.py  SQLite storage and ticket operations
nira_app/web.py      Local server-rendered web UI
nira_app/markdown.py Minimal Markdown renderer for previews
tests/               Integration tests for CLI and HTTP flows
```
