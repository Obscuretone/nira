from __future__ import annotations

import csv
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.text import Text
from rich.prompt import Prompt, Confirm

from nira_app.models import TicketData, TicketDetails
from nira_app.storage import (
    UNSET,
    NiraError,
    NiraStore,
    ValidationError,
    find_root,
)
from nira_app.services import TicketService
from nira_app.web import serve as run_server
from nira_app.logging import setup_logging

SOURCE_ROOT = Path(__file__).resolve().parents[1]
RELOAD_POLL_SECONDS = 0.5

app = typer.Typer(
    help="Local issue tracker with CLI and web UI.",
    no_args_is_help=True,
    add_completion=False,
)

# In test environments, fallback to a dumb terminal to avoid escape codes
_is_test = "PYTEST_CURRENT_TEST" in os.environ or "NO_COLOR" in os.environ
console = Console(force_terminal=False if _is_test else None, no_color=True if _is_test else None)
stderr_console = Console(stderr=True, force_terminal=False if _is_test else None, no_color=True if _is_test else None)


def resolve_store(root_arg: Optional[Path], *, create: bool) -> NiraStore:
    if root_arg:
        root: Path | None = root_arg.resolve()
    else:
        root = find_root()
        if root is None:
            root = Path.cwd().resolve() if create else None

    if root is None:
        raise ValidationError("No .nira directory found. Run `nira init` first.")

    store = NiraStore(root)
    if not create and not store.state_dir.exists():
        raise ValidationError("No .nira directory found. Run `nira init` first.")
    if store.state_dir.exists():
        store.ensure_schema()
    return store


@app.callback()
def main_callback(
    ctx: typer.Context,
    root: Annotated[Optional[Path], typer.Option(help="Project root that contains .nira")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose logging.")] = False,
):
    """
    Nira: A local issue tracker for your project.
    """
    setup_logging(level=logging.DEBUG if verbose else logging.INFO)
    # We store the root in the context so commands can access it
    ctx.obj = {"root": root}


@app.command()
def help(
    ctx: typer.Context,
    topic: Annotated[Optional[str], typer.Argument(help="Topic to show help for.")] = None,
):
    """
    Show help for a command.
    """
    if not topic:
        # Show top level help
        app(["--help"], prog_name="nira")
    else:
        # Show specific command help
        app([topic, "--help"], prog_name="nira")


@app.command()
def init(
    ctx: typer.Context,
    project_key: Annotated[Optional[str], typer.Option(help="Default ticket prefix for this workspace.")] = None,
):
    """
    Create the local Nira database in the current project.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=True)
        store.initialize(default_project=project_key)
        console.print(
            f"[green]Initialized[/green] Nira in [bold]{store.state_dir}[/bold] "
            f"with default project [blue]{store.get_default_project()}[/blue]"
        )
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}", style="red")
        raise typer.Exit(1)


@app.command(name="new")
def new_ticket(
    ctx: typer.Context,
    title_parts: Annotated[
        list[str], typer.Argument(help="Title of the ticket (can be multiple words).", show_default=False)
    ] = [],
    project: Annotated[Optional[str], typer.Option(help="Override default project prefix.")] = None,
    source: Annotated[str, typer.Option(help="Source of the ticket.")] = "",
    type: Annotated[str, typer.Option(help="Type of the ticket (e.g., task, bug).")] = "task",
    priority: Annotated[str, typer.Option(help="Priority level.")] = "medium",
    labels: Annotated[str, typer.Option(help="Comma-separated labels.")] = "",
    due: Annotated[Optional[str], typer.Option(help="Due date (YYYY-MM-DD).")] = None,
    parent: Annotated[Optional[str], typer.Option(help="Parent ticket ID (e.g. NIRA-1).")] = None,
    body: Annotated[Optional[str], typer.Option("--body", "-m", help="Initial body content (Markdown).")] = None,
    edit: Annotated[bool, typer.Option(help="Open $EDITOR to write the body.")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i", help="Run interactive wizard.")] = False,
):
    """
    Create a new ticket.
    """
    create_ticket_logic(
        ctx, title_parts, project, source, type, priority, labels, due, parent, body, edit, interactive=interactive
    )


@app.command(name="create", hidden=True)
def create_ticket_alias(
    ctx: typer.Context,
    title_parts: Annotated[list[str], typer.Argument(help="Title of the ticket.")] = [],
    project: Annotated[Optional[str], typer.Option()] = None,
    source: Annotated[str, typer.Option()] = "",
    type: Annotated[str, typer.Option()] = "task",
    priority: Annotated[str, typer.Option()] = "medium",
    labels: Annotated[str, typer.Option()] = "",
    due: Annotated[Optional[str], typer.Option()] = None,
    parent: Annotated[Optional[str], typer.Option()] = None,
    body: Annotated[Optional[str], typer.Option("--body", "-m")] = None,
    edit: Annotated[bool, typer.Option()] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i")] = False,
):
    create_ticket_logic(
        ctx, title_parts, project, source, type, priority, labels, due, parent, body, edit, interactive=interactive
    )


def create_ticket_logic(
    ctx, title_parts, project, source, type, priority, labels, due, parent, body, edit, interactive=False
):
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)

        title = " ".join(title_parts).strip()

        # Trigger interactive if explicitly requested OR if no title is provided and it's a TTY
        if interactive or (not title and sys.stdin.isatty()):
            title, source, type, priority, labels, due, parent, body_md = run_interactive_wizard(
                store, title, source, type, priority, labels, due, parent, body
            )
        else:
            body_md = read_markdown_input(body=body, edit=edit)

        parent_db_id = None
        if parent:
            parent_ticket = store.get_ticket(parent)
            parent_db_id = parent_ticket["db_id"]

        ticket = service.create_ticket(
            project or "",
            title,
            source=source,
            ticket_type=type,
            priority=priority,
            labels=labels,
            due_date=due,
            parent_id=parent_db_id,
            body_md=body_md,
        )
        console.print(f"Created [bold blue]{ticket['id']}[/bold blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


def run_interactive_wizard(store, title, source, type, priority, labels, due, parent, body):
    console.print(Panel.fit("[bold blue]Nira Ticket Wizard[/bold blue]", border_style="blue"))

    if not title:
        title = Prompt.ask("[bold]Title[/bold]")
        if not title:
            console.print("[red]Error: Title is required.[/red]")
            raise typer.Exit(1)

    priority = Prompt.ask(
        "[bold]Priority[/bold]",
        choices=["low", "medium", "high", "urgent"],
        default=priority,
    )

    type = Prompt.ask(
        "[bold]Type[/bold]",
        choices=["task", "bug", "feature", "enhancement"],
        default=type,
    )

    # Autocomplete labels
    existing_labels = store.get_all_labels()
    label_msg = "[bold]Labels[/bold] (comma-separated)"
    if existing_labels:
        label_msg += f" [dim]Common: {', '.join(existing_labels[:5])}[/dim]"
    labels = Prompt.ask(label_msg, default=labels)

    due = Prompt.ask("[bold]Due Date[/bold] (YYYY-MM-DD or empty)", default=due or "")
    if not due:
        due = None

    parent = Prompt.ask("[bold]Parent Ticket ID[/bold] (e.g. NIRA-1 or empty)", default=parent or "")
    if not parent:
        parent = None

    source = Prompt.ask("[bold]Source[/bold] (optional)", default=source)

    body_md = ""
    if body:
        body_md = body
    else:
        if Confirm.ask("[bold]Add a description?[/bold]"):
            if Confirm.ask("Open $EDITOR?"):
                body_md = launch_editor("")
            else:
                console.print("Enter description (Ctrl-D to finish):")
                body_md = sys.stdin.read()

    return title, source, type, priority, labels, due, parent, body_md


@app.command(name="show")
def show_ticket(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to show.")],
    json_output: Annotated[bool, typer.Option("--json", help="Output in JSON format.")] = False,
):
    """
    Show a ticket's details.
    """
    show_ticket_logic(ctx, ticket_id, json_output)


@app.command(name="get", hidden=True)
def get_ticket_alias(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    show_ticket_logic(ctx, ticket_id, json_output)


def show_ticket_logic(ctx, ticket_id, json_output=False):
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        details = service.ticket_details(ticket_id)
        if json_output:
            print(json.dumps(details, indent=2))
        else:
            print_ticket(details, store)
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command(name="list")
def list_tickets(
    ctx: typer.Context,
    project: Annotated[Optional[str], typer.Option(help="Filter by project.")] = None,
    status: Annotated[
        Optional[str], typer.Option(help="Filter by status (defaults to 'not_closed', use 'all' to show all).")
    ] = None,
    priority: Annotated[Optional[str], typer.Option(help="Filter by priority.")] = None,
    type: Annotated[Optional[str], typer.Option(help="Filter by type.")] = None,
    search: Annotated[Optional[str], typer.Option(help="Search query.")] = None,
    label: Annotated[Optional[str], typer.Option(help="Filter by label.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output in JSON format.")] = False,
):
    """
    List tickets in the current workspace.
    """
    try:
        if status is None and not search and not label:
            status = "not_closed"
        elif status == "all":
            status = None

        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        tickets = service.list_tickets(
            project=project,
            status=status,
            priority=priority,
            ticket_type=type,
            search=search,
            label=label,
        )
        if json_output:
            print(json.dumps(tickets, indent=2))
        else:
            print_ticket_list(tickets, store)
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def update(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to update.")],
    title: Annotated[Optional[str], typer.Option(help="New title.")] = None,
    status: Annotated[Optional[str], typer.Option(help="New status.")] = None,
    type: Annotated[Optional[str], typer.Option(help="New type.")] = None,
    priority: Annotated[Optional[str], typer.Option(help="New priority.")] = None,
    source: Annotated[Optional[str], typer.Option(help="New source.")] = None,
    labels: Annotated[Optional[str], typer.Option(help="New comma-separated labels.")] = None,
    due: Annotated[Optional[str], typer.Option(help="New due date (YYYY-MM-DD).")] = None,
    parent: Annotated[Optional[str], typer.Option(help="New parent ticket ID.")] = None,
    story_points: Annotated[Optional[int], typer.Option(help="New story points.")] = None,
    body: Annotated[Optional[str], typer.Option("--body", "-m", help="New body content.")] = None,
    resolution_reason: Annotated[Optional[str], typer.Option(help="New resolution reason.")] = None,
):
    """
    Update ticket metadata.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        updates: dict[str, Any] = {
            "title": title if title is not None else UNSET,
            "status": status if status is not None else UNSET,
            "ticket_type": type if type is not None else UNSET,
            "priority": priority if priority is not None else UNSET,
            "source": source if source is not None else UNSET,
            "labels": labels if labels is not None else UNSET,
            "due_date": due if due is not None else UNSET,
            "story_points": story_points if story_points is not None else UNSET,
            "body_md": body if body is not None else UNSET,
            "resolution_reason": resolution_reason if resolution_reason is not None else UNSET,
        }
        if parent is not None:
            if not parent.strip():
                updates["parent_id"] = None
            else:
                parent_ticket = store.get_ticket(parent)
                updates["parent_id"] = parent_ticket["db_id"]

        ticket = service.update_ticket(ticket_id, **updates)
        console.print(f"Updated [bold blue]{ticket['id']}[/bold blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def edit(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to edit.")],
    field: Annotated[str, typer.Option(help="Field to edit (body or resolution).")] = "body",
):
    """
    Edit the ticket body or resolution notes in $EDITOR.
    """
    if field not in {"body", "resolution"}:
        console.print("[red]Error:[/red] field must be 'body' or 'resolution'")
        raise typer.Exit(1)

    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        details = service.ticket_details(ticket_id)
        if field == "body":
            field_name = "body_md"
            initial_text = details["ticket"]["body_md"]
        else:
            field_name = "resolution_md"
            initial_text = details["ticket"]["resolution_md"] or ""

        updated_text = launch_editor(initial_text)
        updates: dict[str, Any] = {field_name: updated_text}
        ticket = service.update_ticket(ticket_id, **updates)
        console.print(f"Updated [bold blue]{ticket['id']}[/bold blue] {field}")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def close(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to close.")],
    notes: Annotated[
        Optional[str], typer.Option("--notes", "-n", "--resolution", "-r", "-m", help="Resolution notes (Markdown).")
    ] = None,
    edit: Annotated[bool, typer.Option(help="Open $EDITOR to write the resolution notes.")] = False,
):
    """
    Close a ticket with resolution notes.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        resolution_md = read_markdown_input(body=notes, edit=edit)
        if not resolution_md.strip():
            console.print("[yellow]Warning:[/yellow] Empty resolution notes. Aborting.")
            raise typer.Exit(1)
        ticket = service.close_ticket(ticket_id, resolution_md=resolution_md)
        console.print(f"Closed [bold blue]{ticket['id']}[/bold blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def comment(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to comment on.")],
    body: Annotated[Optional[str], typer.Option("--body", "-m", help="Comment body text.")] = None,
    edit: Annotated[bool, typer.Option(help="Open $EDITOR to write the comment.")] = False,
):
    """
    Add a comment to a ticket.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        body_md = read_markdown_input(body=body, edit=edit)
        if not body_md.strip():
            console.print("[yellow]Warning:[/yellow] Empty comment. Aborting.")
            raise typer.Exit(1)
        service = TicketService(store)
        comment_record = service.add_comment(ticket_id, body_md)
        console.print(
            f"Added comment [bold blue]#{comment_record['id']}[/bold blue] to [bold blue]{ticket_id}[/bold blue]"
        )
    except NiraError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def reopen(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to reopen.")],
):
    """
    Reopen a closed ticket.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        ticket = service.reopen_ticket(ticket_id)
        console.print(f"Reopened [bold blue]{ticket['id']}[/bold blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def delete(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket to delete.")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation.")] = False,
):
    """
    Permanently delete a ticket.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        if not force:
            if not typer.confirm(f"Are you sure you want to permanently delete {ticket_id}?"):
                console.print("Aborted.")
                return

        service = TicketService(store)
        service.delete_ticket(ticket_id)
        console.print(f"Deleted [bold blue]{ticket_id}[/bold blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def attach(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="ID of the ticket.")],
    file_path: Annotated[Path, typer.Argument(help="Path to the file to attach.")],
):
    """
    Attach a file to a ticket.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)

        if not file_path.exists() or not file_path.is_file():
            console.print(f"[red]Error:[/red] File {file_path} does not exist or is not a file.")
            raise typer.Exit(1)

        import mimetypes

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"

        content = file_path.read_bytes()
        attachment = service.add_attachment(ticket_id, file_path.name, content, content_type)

        console.print(
            f"Attached [bold blue]{attachment['filename']}[/bold blue] to {ticket_id} ({attachment['file_size']} bytes)"
        )
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def link(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="First ticket ID.")],
    other_ticket_id: Annotated[str, typer.Argument(help="Second ticket ID.")],
):
    """
    Mark two tickets as related.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        service.link_tickets(ticket_id, other_ticket_id)
        console.print(f"Linked [blue]{ticket_id}[/blue] and [blue]{other_ticket_id}[/blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def links(
    ctx: typer.Context,
    ticket_id: Annotated[Optional[str], typer.Argument(help="Show links for this ticket only.")] = None,
):
    """
    Show related ticket links.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        resolved_ticket_id = store.get_ticket(ticket_id)["id"] if ticket_id else None
        links_data = service.list_links(ticket_id)
        print_links(links_data, ticket_id=resolved_ticket_id)
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def unlink(
    ctx: typer.Context,
    ticket_id: Annotated[str, typer.Argument(help="First ticket ID.")],
    other_ticket_id: Annotated[str, typer.Argument(help="Second ticket ID.")],
):
    """
    Remove a relationship between two tickets.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        service.unlink_tickets(ticket_id, other_ticket_id)
        console.print(f"Unlinked [blue]{ticket_id}[/blue] and [blue]{other_ticket_id}[/blue]")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def config(
    ctx: typer.Context,
    key: Annotated[Optional[str], typer.Argument(help="Setting key to get or set.")] = None,
    value: Annotated[Optional[str], typer.Argument(help="New value for the setting.")] = None,
):
    """
    View or update workspace settings.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        if not key:
            # Show all settings
            settings = store.get_settings()
            table = Table(title=f"Configuration: {store.root}", box=None)
            table.add_column("Key", style="cyan")
            table.add_column("Value")
            for k, v in settings.items():
                table.add_row(k, str(v))
            console.print(table)
            return

        if value is None:
            # Get specific setting
            settings = store.get_settings()
            if key in settings:
                console.print(f"{key} = {settings[key]}")
            else:
                console.print(f"[red]Error:[/red] Setting '{key}' not found.")
                raise typer.Exit(1)
        else:
            # Set specific setting
            store.update_settings({key: value})
            console.print(f"[green]Updated[/green] {key} to {value}")
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def export(
    ctx: typer.Context,
    filename: Annotated[Path, typer.Argument(help="Filename to export to.")],
    project: Annotated[Optional[str], typer.Option(help="Filter by project.")] = None,
    status: Annotated[Optional[str], typer.Option(help="Filter by status.")] = None,
    priority: Annotated[Optional[str], typer.Option(help="Filter by priority.")] = None,
    type: Annotated[Optional[str], typer.Option(help="Filter by type.")] = None,
    search: Annotated[Optional[str], typer.Option(help="Search query.")] = None,
    label: Annotated[Optional[str], typer.Option(help="Filter by label.")] = None,
):
    """
    Export tickets to a CSV file.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        tickets = service.list_tickets(
            project=project,
            status=status,
            priority=priority,
            ticket_type=type,
            search=search,
            label=label,
        )

        if not tickets:
            console.print("[yellow]No tickets found to export.[/yellow]")
            return

        # Filter out internal/database fields for a cleaner export
        exclude = {"db_id", "parent_id", "parent_number"}
        fieldnames = [k for k in tickets[0].keys() if k not in exclude]

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tickets)

        console.print(f"[green]Exported {len(tickets)} tickets to {filename}[/green]")

    except (NiraError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command(name="import")
def import_tickets(
    ctx: typer.Context,
    filename: Annotated[Path, typer.Argument(help="CSV file to import from.")],
):
    """
    Import tickets from a CSV file.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        if not filename.exists():
            console.print(f"[red]Error:[/red] File {filename} not found.")
            raise typer.Exit(1)

        with open(filename, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            tickets_data = list(reader)

        if not tickets_data:
            console.print("[yellow]No tickets found in CSV to import.[/yellow]")
            return

        count = service.import_tickets(tickets_data)
        console.print(f"[green]Imported {count} tickets from {filename}[/green]")
    except (NiraError, OSError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def board(
    ctx: typer.Context,
    project: Annotated[Optional[str], typer.Option(help="Filter by project.")] = None,
):
    """
    Show a summary Kanban board in the terminal.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        statuses = store.get_statuses()
        # Fetch 100 most recent tickets
        tickets = service.list_tickets(project=project, limit=100)

        from rich.columns import Columns

        cols = []
        for status in statuses:
            status_tickets = [t for t in tickets if t["status"] == status]
            table = Table(title=f"{status.replace('_', ' ').title()} ({len(status_tickets)})", box=None)
            table.add_column("ID", style="blue")
            table.add_column("Title")
            for t in status_tickets[:10]:  # Only show top 10 per column
                table.add_row(t["id"], t["title"])
            if len(status_tickets) > 10:
                table.add_row("...", f"and {len(status_tickets) - 10} more")
            cols.append(Panel(table, border_style="dim"))

        console.print(Columns(cols))
    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def dashboard(ctx: typer.Context):
    """
    Show a workspace overview.
    """
    try:
        store = resolve_store(ctx.obj["root"], create=False)
        service = TicketService(store)
        stats = service.get_dashboard_stats()

        console.print(Panel(f"[bold]Dashboard: {store.get_default_project()}[/bold]", style="blue"))

        # Status Table
        table = Table(title="Tickets by Status")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        table.add_column("Points", justify="right")

        for status, count in stats["status_counts"].items():
            points = stats["status_points"].get(status, 0)
            status_color = "white"
            if status == "open":
                status_color = "yellow"
            elif status == "in_progress":
                status_color = "blue"
            elif status == "closed":
                status_color = "green"
            table.add_row(Text(status.replace("_", " ").title(), style=status_color), str(count), str(points))

        table.add_section()
        table.add_row("Total", str(stats["total_tickets"]), str(stats["total_points"]), style="bold")
        console.print(table)

        # Recent Tickets
        if stats["recent_tickets"]:
            print_ticket_list(stats["recent_tickets"], store, title="Recent Tickets")

        # Recent Activity
        if stats["recent_history"]:
            hist_table = Table(title="Recent Activity")
            hist_table.add_column("Date", style="dim")
            hist_table.add_column("Ticket")
            hist_table.add_column("Activity")

            for item in stats["recent_history"]:
                hist_table.add_row(
                    item["created_at"], item["ticket_id"], f"{item['field']} -> {item['new_value'] or 'None'}"
                )
            console.print(hist_table)

    except NiraError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command()
def serve(  # pragma: no cover
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Host to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind to.")] = 8765,
    reload: Annotated[bool, typer.Option(help="Restart the server when Nira source files change.")] = False,
):
    """
    Serve the local web UI.
    """
    try:
        if reload:
            serve_with_reload(ctx.obj["root"], host, port)
        else:
            store = resolve_store(ctx.obj["root"], create=False)
            run_server(store, host, port)
    except NiraError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    except OSError as exc:
        if exc.errno == 48:  # Address already in use
            stderr_console.print(
                f"[red]Error:[/red] Could not start Nira server on http://{host}:{port}: Address already in use."
            )
            raise typer.Exit(1)
        raise


def launch_editor(initial_text: str) -> str:
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w+", encoding="utf-8", delete=False) as handle:
        handle.write(initial_text)
        handle.flush()
        temp_path = Path(handle.name)

    try:
        command = [*shlex.split(editor), str(temp_path)]
        subprocess.run(command, check=True)
        return temp_path.read_text(encoding="utf-8")
    except subprocess.CalledProcessError as exc:
        stderr_console.print(f"[red]Error:[/red] Editor command failed with status {exc.returncode}.")
        raise typer.Exit(1)
    finally:
        temp_path.unlink(missing_ok=True)


def read_markdown_input(*, body: str | None, edit: bool) -> str:
    if edit:
        return launch_editor(body or "")
    if body is not None:
        return body
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def print_ticket(details: TicketDetails, store: NiraStore) -> None:
    ticket = details["ticket"]
    related = details["related"]
    comments = details.get("comments", [])

    statuses = store.get_statuses()

    status_color = "yellow"
    if ticket["status"] == statuses[-1]:
        status_color = "green"
    elif len(statuses) > 1 and ticket["status"] == statuses[1]:
        status_color = "blue"

    header_text = Text()
    header_text.append(f"{ticket['id']}", style="bold blue")
    header_text.append(f" {ticket['title']}", style="bold")

    metadata_table = Table.grid(padding=(0, 2))
    metadata_table.add_column(style="dim")
    metadata_table.add_column()

    metadata_table.add_row("Status:", Text(ticket["status"], style=status_color))
    metadata_table.add_row("Type:", ticket["type"])

    priority_style = ""
    if ticket["priority"] == "critical":
        priority_style = "bold red"
    elif ticket["priority"] == "high":
        priority_style = "red"
    elif ticket["priority"] == "low":
        priority_style = "dim"
    metadata_table.add_row("Priority:", Text(ticket["priority"], style=priority_style))
    if ticket.get("labels"):
        metadata_table.add_row("Labels:", ticket["labels"])
    metadata_table.add_row("Source:", ticket["source"] or "[dim]none[/dim]")
    if ticket.get("due_date"):
        metadata_table.add_row("Due Date:", cast(str, ticket["due_date"]))
    if details.get("parent"):
        parent = cast(TicketData, details["parent"])
        metadata_table.add_row("Parent:", f"[blue]{parent['id']}[/blue] {parent['title']}")
    metadata_table.add_row("Created:", ticket["created_at"])
    metadata_table.add_row("Updated:", ticket["updated_at"])

    if ticket.get("resolution_reason"):
        metadata_table.add_row("Resolution:", cast(str, ticket["resolution_reason"]))

    console.print(Panel(metadata_table, title=header_text, title_align="left", expand=False))

    if ticket["body_md"].strip():
        console.print("\n[bold]Body[/bold]")
        console.print(Markdown(ticket["body_md"]))

    if ticket.get("resolution_md") and cast(str, ticket["resolution_md"]).strip():
        console.print("\n[bold]Resolution Notes[/bold]")
        console.print(Markdown(cast(str, ticket["resolution_md"])))

    if related:
        console.print("\n[bold]Related Tickets[/bold]")
        for item in related:
            console.print(f"• [blue]{item['id']}[/blue] {item['title']}")

    if details.get("sub_tasks"):
        console.print("\n[bold]Sub-tasks[/bold]")
        for item in details["sub_tasks"]:
            console.print(f"• [blue]{item['id']}[/blue] {item['title']}")

    if comments:
        console.print("\n[bold]Comments[/bold]")
        for comment in comments:
            console.print(f"\n[dim]#{comment['id']} • {comment['created_at']}[/dim]")
            console.print(Markdown(comment["body_md"]))


def print_ticket_list(tickets: list[TicketData], store: NiraStore, *, title: str | None = None) -> None:
    if not tickets:
        if title:
            console.print(f"[bold]{title}[/bold]")
        console.print("[dim]No tickets found.[/dim]")
        return

    statuses = store.get_statuses()

    table = Table(title=title, box=None, header_style="bold cyan", title_style="bold", title_justify="left")
    table.add_column("ID", style="blue")
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Type")
    table.add_column("Title")

    for ticket in tickets:
        status_style = ""
        if ticket["status"] == statuses[-1]:
            status_style = "green"
        elif len(statuses) > 1 and ticket["status"] == statuses[1]:
            status_style = "blue"

        priority_style = ""
        if ticket["priority"] == "critical":
            priority_style = "bold red"
        elif ticket["priority"] == "high":
            priority_style = "red"
        elif ticket["priority"] == "low":
            priority_style = "dim"

        table.add_row(
            ticket["id"],
            Text(ticket["status"], style=status_style),
            Text(ticket["priority"], style=priority_style),
            ticket["type"],
            ticket["title"],
        )

    console.print(table)


def print_links(links: list[dict], *, ticket_id: str | None = None) -> None:
    if ticket_id:
        if not links:
            console.print(f"[dim]No related tickets found for {ticket_id}.[/dim]")
            return

        table = Table(title=f"Related tickets for {ticket_id}", box=None)
        table.add_column("ID", style="blue")
        table.add_column("Title")

        for link in links:
            if link["ticket_a"] == ticket_id:
                table.add_row(link["ticket_b"], link["ticket_b_title"])
            else:
                table.add_row(link["ticket_a"], link["ticket_a_title"])
        console.print(table)
        return

    if not links:
        console.print("[dim]No links found.[/dim]")
        return

    table = Table(title="All Links", box=None)
    table.add_column("Ticket A", style="blue")
    table.add_column("↔", justify="center")
    table.add_column("Ticket B", style="blue")

    for link in links:
        table.add_row(link["ticket_a"], "↔", link["ticket_b"])
    console.print(table)


def should_watch_reload_path(path: Path) -> bool:
    if not path.is_file():
        return False
    if "__pycache__" in path.parts:
        return False
    return path.suffix in {".py", ".html"} or path.name == "nira"


def build_reload_snapshot(source_root: Path | None = None) -> dict[str, tuple[int, int]]:
    root = (source_root or SOURCE_ROOT).resolve()
    snapshot: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if not should_watch_reload_path(path):
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def describe_reload_change(
    previous: dict[str, tuple[int, int]],
    current: dict[str, tuple[int, int]],
) -> str:
    previous_paths = set(previous)
    current_paths = set(current)

    added = sorted(current_paths - previous_paths)
    if added:
        return f"{added[0]} added"

    removed = sorted(previous_paths - current_paths)
    if removed:
        return f"{removed[0]} removed"

    for path in sorted(previous_paths & current_paths):
        if previous[path] != current[path]:
            return f"{path} changed"

    return "files changed"


def build_serve_command(root_arg: Path | None, host: str, port: int) -> list[str]:
    command = [sys.executable, "-m", "nira_app.cli"]
    if root_arg:
        command.extend(["--root", str(root_arg)])
    command.extend(["serve", "--host", host, "--port", str(port)])
    return command


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def serve_with_reload(root_arg: Path | None, host: str, port: int) -> None:
    snapshot = build_reload_snapshot()
    command = build_serve_command(root_arg, host, port)
    console.print(f"[dim]Watching [bold]{SOURCE_ROOT}[/bold] for changes...[/dim]")
    child = subprocess.Popen(command)
    waiting_for_changes = False

    try:
        while True:
            time.sleep(RELOAD_POLL_SECONDS)
            current_snapshot = build_reload_snapshot()
            if current_snapshot != snapshot:
                change = describe_reload_change(snapshot, current_snapshot)
                snapshot = current_snapshot
                if child.poll() is None:
                    console.print(f"[bold yellow]Detected {change}; reloading Nira...[/bold yellow]")
                    stop_process(child)
                else:
                    console.print(f"[bold yellow]Detected {change}; restarting Nira...[/bold yellow]")
                child = subprocess.Popen(command)
                waiting_for_changes = False
                continue

            if child.poll() is not None and not waiting_for_changes:
                console.print("[dim]Nira server exited. Waiting for changes to restart...[/dim]")
                waiting_for_changes = True
    except KeyboardInterrupt:
        pass
    finally:
        stop_process(child)


def main(args: list[str] | None = None) -> int:
    try:
        app(args=args)
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0


if __name__ == "__main__":  # pragma: no cover
    main()
