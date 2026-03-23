from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from .storage import NiraError, NiraStore, UNSET, ValidationError, find_root
from .web import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nira", description="Local issue tracker with CLI and web UI.")
    parser.add_argument("--root", help="Project root that contains .nira")

    subparsers = parser.add_subparsers(dest="command", required=True)
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    def register_parser(name: str, parser_obj: argparse.ArgumentParser, *, aliases: list[str] | None = None):
        command_parsers[name] = parser_obj
        for alias in aliases or []:
            command_parsers[alias] = parser_obj
        return parser_obj

    help_parser = register_parser(
        "help",
        subparsers.add_parser("help", help="Show top-level or command-specific help."),
    )
    help_parser.add_argument("topic", nargs="?")

    init_parser = register_parser(
        "init",
        subparsers.add_parser("init", help="Create the local Nira database in the current project."),
    )
    init_parser.add_argument("--project-key", help="Default ticket prefix for this workspace.")

    new_parser = register_parser(
        "new",
        subparsers.add_parser("new", aliases=["create"], help="Create a new ticket."),
        aliases=["create"],
    )
    new_parser.add_argument("parts", nargs="+")
    new_parser.add_argument("--project", dest="project_override")
    new_parser.add_argument("--source", default="")
    new_parser.add_argument("--type", dest="ticket_type", default="task")
    new_parser.add_argument("--priority", default="medium")
    new_parser.add_argument("--body", default=None)
    new_parser.add_argument("--edit", action="store_true")

    show_parser = register_parser(
        "show",
        subparsers.add_parser("show", aliases=["get"], help="Show a ticket."),
        aliases=["get"],
    )
    show_parser.add_argument("ticket_id")

    list_parser = register_parser(
        "list",
        subparsers.add_parser("list", help="List tickets."),
    )
    list_parser.add_argument("--project")
    list_parser.add_argument("--status")
    list_parser.add_argument("--priority")
    list_parser.add_argument("--type", dest="ticket_type")

    update_parser = register_parser(
        "update",
        subparsers.add_parser("update", help="Update ticket metadata."),
    )
    update_parser.add_argument("ticket_id")
    update_parser.add_argument("--title")
    update_parser.add_argument("--status")
    update_parser.add_argument("--type", dest="ticket_type")
    update_parser.add_argument("--priority")
    update_parser.add_argument("--source")
    update_parser.add_argument("--resolution-reason")

    edit_parser = register_parser(
        "edit",
        subparsers.add_parser("edit", help="Edit the ticket body or resolution notes in $EDITOR."),
    )
    edit_parser.add_argument("ticket_id")
    edit_parser.add_argument("--field", choices=["body", "resolution"], default="body")

    close_parser = register_parser(
        "close",
        subparsers.add_parser("close", help="Close a ticket with a resolution reason."),
    )
    close_parser.add_argument("ticket_id")
    close_parser.add_argument("--reason", required=True)

    reopen_parser = register_parser(
        "reopen",
        subparsers.add_parser("reopen", help="Reopen a ticket."),
    )
    reopen_parser.add_argument("ticket_id")

    link_parser = register_parser(
        "link",
        subparsers.add_parser("link", help="Mark two tickets as related."),
    )
    link_parser.add_argument("ticket_id")
    link_parser.add_argument("other_ticket_id")

    unlink_parser = register_parser(
        "unlink",
        subparsers.add_parser("unlink", help="Remove a relationship between two tickets."),
    )
    unlink_parser.add_argument("ticket_id")
    unlink_parser.add_argument("other_ticket_id")

    serve_parser = register_parser(
        "serve",
        subparsers.add_parser("serve", help="Serve the local web UI."),
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    parser.set_defaults(command_parsers=command_parsers)
    return parser


def resolve_store(root_arg: str | None, *, create: bool) -> NiraStore:
    if root_arg:
        root: Path | None = Path(root_arg).resolve()
    else:
        root = find_root()
        if root is None:
            root = Path.cwd().resolve() if create else None

    if root is None:
        raise ValidationError("No .nira directory found. Run `nira init` first.")

    store = NiraStore(root)
    if not create and not store.state_dir.exists():
        raise ValidationError("No .nira directory found. Run `nira init` first.")
    return store


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


def parse_new_command_parts(parts: list[str], explicit_project: str | None) -> tuple[str | None, str]:
    if explicit_project:
        return explicit_project, " ".join(parts).strip()
    if len(parts) >= 2 and parts[0].upper() == parts[0]:
        return parts[0], " ".join(parts[1:]).strip()
    return None, " ".join(parts).strip()


def print_ticket(details: dict) -> None:
    ticket = details["ticket"]
    related = details["related"]

    lines = [
        f"{ticket['id']} {ticket['title']}",
        "",
        f"Status: {ticket['status']}",
        f"Type: {ticket['type']}",
        f"Priority: {ticket['priority']}",
        f"Source: {ticket['source']}",
        f"Created: {ticket['created_at']}",
        f"Updated: {ticket['updated_at']}",
        f"Resolution Reason: {ticket['resolution_reason']}",
    ]

    for item in related:
        lines.append(f"Related: {item['id']}")

    if ticket["body_md"].strip():
        lines.extend(["", "Body:", "", ticket["body_md"]])

    if ticket["resolution_md"].strip():
        lines.extend(["", "Resolution Notes:", "", ticket["resolution_md"]])

    print("\n".join(lines))


def print_ticket_list(tickets: list[dict]) -> None:
    if not tickets:
        print("No tickets found.")
        return

    header = f"{'ID':<10} {'STATUS':<12} {'PRIORITY':<10} {'TYPE':<12} TITLE"
    print(header)
    print("-" * len(header))
    for ticket in tickets:
        print(
            f"{ticket['id']:<10} {ticket['status']:<12} {ticket['priority']:<10} "
            f"{ticket['type']:<12} {ticket['title']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        command = args.command
        if command == "help":
            topic = args.topic
            command_parsers = getattr(args, "command_parsers", {})
            if not topic:
                parser.print_help()
                return 0
            help_parser = command_parsers.get(topic)
            if help_parser is None:
                raise ValidationError(f"Unknown help topic: {topic}")
            help_parser.print_help()
            return 0

        if command == "init":
            store = resolve_store(args.root, create=True)
            store.initialize(default_project=args.project_key)
            print(f"Initialized Nira in {store.state_dir} with default project {store.get_default_project()}")
            return 0

        store = resolve_store(args.root, create=False)

        if command in {"new", "create"}:
            project, title = parse_new_command_parts(args.parts, args.project_override)
            body_md = read_markdown_input(body=args.body, edit=args.edit)
            ticket = store.create_ticket(
                project or "",
                title,
                source=args.source,
                ticket_type=args.ticket_type,
                priority=args.priority,
                body_md=body_md,
            )
            print(f"Created {ticket['id']}")
            return 0

        if command in {"show", "get"}:
            print_ticket(store.ticket_details(args.ticket_id))
            return 0

        if command == "list":
            print_ticket_list(
                store.list_tickets(
                    project=args.project,
                    status=args.status,
                    priority=args.priority,
                    ticket_type=args.ticket_type,
                )
            )
            return 0

        if command == "update":
            updates = {
                "title": args.title if args.title is not None else UNSET,
                "status": args.status if args.status is not None else UNSET,
                "ticket_type": args.ticket_type if args.ticket_type is not None else UNSET,
                "priority": args.priority if args.priority is not None else UNSET,
                "source": args.source if args.source is not None else UNSET,
                "resolution_reason": (
                    args.resolution_reason if args.resolution_reason is not None else UNSET
                ),
            }
            ticket = store.update_ticket(args.ticket_id, **updates)
            print(f"Updated {ticket['id']}")
            return 0

        if command == "edit":
            details = store.ticket_details(args.ticket_id)
            field_name = "body_md" if args.field == "body" else "resolution_md"
            updated_text = launch_editor(details["ticket"][field_name])
            ticket = store.update_ticket(args.ticket_id, **{field_name: updated_text})
            print(f"Updated {ticket['id']} {args.field}")
            return 0

        if command == "close":
            ticket = store.close_ticket(args.ticket_id, reason=args.reason)
            print(f"Closed {ticket['id']}")
            return 0

        if command == "reopen":
            ticket = store.reopen_ticket(args.ticket_id)
            print(f"Reopened {ticket['id']}")
            return 0

        if command == "link":
            store.link_tickets(args.ticket_id, args.other_ticket_id)
            print(f"Linked {args.ticket_id} and {args.other_ticket_id}")
            return 0

        if command == "unlink":
            store.unlink_tickets(args.ticket_id, args.other_ticket_id)
            print(f"Unlinked {args.ticket_id} and {args.other_ticket_id}")
            return 0

        if command == "serve":
            serve(store, args.host, args.port)
            return 0

        parser.error(f"Unknown command: {command}")
    except NiraError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Editor command failed with status {exc.returncode}.", file=sys.stderr)
        return 1

    return 0
