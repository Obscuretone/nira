from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .storage import NiraError, NiraStore, UNSET, ValidationError, find_root
from .web import serve

SOURCE_ROOT = Path(__file__).resolve().parents[1]
RELOAD_POLL_SECONDS = 0.5


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

    links_parser = register_parser(
        "links",
        subparsers.add_parser("links", help="Show related ticket links."),
    )
    links_parser.add_argument("ticket_id", nargs="?")

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
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the server when Nira source files change.",
    )

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
    if store.state_dir.exists():
        store.ensure_schema()
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
    return explicit_project, " ".join(parts).strip()


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

    if ticket["body_md"].strip():
        lines.extend(["", "Body:", "", ticket["body_md"]])

    if ticket["resolution_md"].strip():
        lines.extend(["", "Resolution Notes:", "", ticket["resolution_md"]])

    if related:
        lines.append("")
        for item in related:
            lines.append(f"Related: {item['id']}")

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


def print_links(links: list[dict], *, ticket_id: str | None = None) -> None:
    if ticket_id:
        if not links:
            print(f"No related tickets found for {ticket_id}.")
            return

        print(f"Related tickets for {ticket_id}")
        print("-" * (20 + len(ticket_id)))
        for link in links:
            if link["ticket_a"] == ticket_id:
                print(f"{link['ticket_b']:<10} {link['ticket_b_title']}")
            else:
                print(f"{link['ticket_a']:<10} {link['ticket_a_title']}")
        return

    if not links:
        print("No links found.")
        return

    print("Links")
    print("-----")
    for link in links:
        print(f"{link['ticket_a']} <-> {link['ticket_b']}")


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


def build_serve_command(root_arg: str | None, host: str, port: int) -> list[str]:
    command = [sys.executable, str(SOURCE_ROOT / "nira")]
    if root_arg:
        command.extend(["--root", root_arg])
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


def serve_with_reload(root_arg: str | None, host: str, port: int) -> None:
    snapshot = build_reload_snapshot()
    command = build_serve_command(root_arg, host, port)
    print(f"Watching {SOURCE_ROOT} for changes...", flush=True)
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
                    print(f"Detected {change}; reloading Nira...", flush=True)
                    stop_process(child)
                else:
                    print(f"Detected {change}; restarting Nira...", flush=True)
                child = subprocess.Popen(command)
                waiting_for_changes = False
                continue

            if child.poll() is not None and not waiting_for_changes:
                print("Nira server exited. Waiting for changes to restart...", flush=True)
                waiting_for_changes = True
    except KeyboardInterrupt:
        pass
    finally:
        stop_process(child)


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

        if command == "links":
            resolved_ticket_id = store.get_ticket(args.ticket_id)["id"] if args.ticket_id else None
            print_links(store.list_links(args.ticket_id), ticket_id=resolved_ticket_id)
            return 0

        if command == "unlink":
            store.unlink_tickets(args.ticket_id, args.other_ticket_id)
            print(f"Unlinked {args.ticket_id} and {args.other_ticket_id}")
            return 0

        if command == "serve":
            if args.reload:
                serve_with_reload(args.root, args.host, args.port)
            else:
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
if __name__ == "__main__":
    raise SystemExit(main())
