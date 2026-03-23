from __future__ import annotations

import html
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .markdown import render_markdown
from .storage import (
    NiraError,
    NiraStore,
    TicketNotFoundError,
    ValidationError,
    normalize_list_direction,
    normalize_list_sort,
)


BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"
BOOTSTRAP_JS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js"
BOOTSTRAP_CSS_INTEGRITY = "sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
BOOTSTRAP_JS_INTEGRITY = "sha384-FKyoEForCGlyvwx9Hj09JcYn3nv7wiPVlz7YYwJrWVcXK/BmnVDxM+D2scQbITxI"
TOAST_UI_EDITOR_CSS = "https://uicdn.toast.com/editor/3.2.2/toastui-editor.min.css"
TOAST_UI_EDITOR_JS = "https://uicdn.toast.com/editor/3.2.2/toastui-editor-all.min.js"
TEMPLATES_DIR = Path(__file__).with_name("templates")
ASSETS_DIR = Path(__file__).with_name("assets")


def h(value: str | None) -> str:
    return html.escape(value or "")


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_time(value: str) -> str:
    moment = parse_timestamp(value)
    if moment is None:
        return value
    now = datetime.now(UTC)
    delta = now - moment.astimezone(UTC)
    seconds = max(0, int(delta.total_seconds()))

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    weeks = seconds // 604800
    return f"{weeks} week{'s' if weeks != 1 else ''} ago"


def format_time(value: str, *, relative: bool = False) -> str:
    display = relative_time(value) if relative else value
    return f'<time datetime="{h(value)}" title="{h(value)}">{h(display)}</time>'


def status_badge(status: str) -> str:
    variants = {
        "open": "text-bg-secondary",
        "in_progress": "text-bg-primary",
        "closed": "text-bg-success",
    }
    labels = {
        "open": "open",
        "in_progress": "in progress",
        "closed": "completed",
    }
    return f'<span class="badge {variants.get(status, "text-bg-light border")}">{h(labels.get(status, status))}</span>'


def status_select_classes(status: str) -> str:
    variants = {
        "open": "border-secondary bg-secondary-subtle text-secondary-emphasis",
        "in_progress": "border-primary bg-primary-subtle text-primary-emphasis",
        "closed": "border-success bg-success-subtle text-success-emphasis",
    }
    default_variant = "border-secondary bg-secondary-subtle text-secondary-emphasis"
    return f"form-select form-select-lg fw-semibold {variants.get(status, default_variant)}"


def priority_badge(priority: str) -> str:
    variants = {
        "low": "text-bg-light border",
        "medium": "text-bg-info",
        "high": "text-bg-warning",
        "critical": "text-bg-danger",
    }
    return f'<span class="badge {variants.get(priority, "text-bg-light border")}">{h(priority)}</span>'


@lru_cache(maxsize=None)
def load_template(name: str) -> Template:
    path = TEMPLATES_DIR / name
    return Template(path.read_text(encoding="utf-8"))


def render_template(name: str, **context: object) -> str:
    normalized_context = {key: "" if value is None else str(value) for key, value in context.items()}
    return load_template(name).substitute(normalized_context)


@dataclass
class Response:
    status: str
    body: str | bytes
    content_type: str = "text/html; charset=utf-8"
    headers: list[tuple[str, str]] | None = None

    def to_wsgi(self, start_response):
        response_headers = [("Content-Type", self.content_type)]
        if self.headers:
            response_headers.extend(self.headers)
        payload = self.body.encode("utf-8") if isinstance(self.body, str) else self.body
        response_headers.append(("Content-Length", str(len(payload))))
        start_response(self.status, response_headers)
        return [payload]


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class NiraWebApp:
    def __init__(self, store: NiraStore):
        self.store = store

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "") or "/"
        query = self.parse_query(environ.get("QUERY_STRING", ""))
        form = self.parse_form(environ) if method == "POST" else {}

        try:
            response = self.route(method, path, query, form)
        except TicketNotFoundError as exc:
            response = self.error_page("404 Not Found", str(exc), "404 Not Found")
        except ValidationError as exc:
            response = self.error_page("400 Bad Request", str(exc), "400 Bad Request")
        except NiraError as exc:
            response = self.error_page("500 Internal Server Error", str(exc), "500 Internal Server Error")
        except Exception as exc:  # pragma: no cover
            response = self.error_page(
                "500 Internal Server Error",
                f"Unexpected error: {exc}",
                "500 Internal Server Error",
            )
        return response.to_wsgi(start_response)

    def route(self, method: str, path: str, query: dict[str, str], form: dict[str, str]) -> Response:
        parts = [part for part in path.split("/") if part]

        if method == "GET" and parts[:1] == ["assets"]:
            return self.asset_response(parts[1:])

        if method == "GET" and path == "/":
            return self.list_page(query)

        if method == "GET" and path == "/tickets/new":
            return self.new_ticket_page(query)

        if method == "GET" and path == "/settings":
            return self.settings_page(query)

        if method == "POST" and path == "/tickets":
            ticket = self.store.create_ticket(
                form.get("project", ""),
                form.get("title", ""),
                source=form.get("source", ""),
                ticket_type=form.get("type", "task"),
                priority=form.get("priority", "medium"),
                body_md=form.get("body_md", ""),
                resolution_md=form.get("resolution_md", ""),
            )
            return self.redirect(f"/tickets/{ticket['id']}")

        if method == "POST" and path == "/settings":
            self.store.rename_default_project(form.get("default_project", ""))
            return self.redirect("/settings?saved=1")

        if method == "POST" and path == "/preview":
            return Response(
                "200 OK",
                render_markdown(form.get("markdown", "")),
            )

        if len(parts) == 2 and parts[0] == "tickets" and method == "GET":
            return self.ticket_detail_page(parts[1])

        if len(parts) == 3 and parts[0] == "tickets" and method == "POST":
            ticket_id = parts[1]
            action = parts[2]
            if action == "edit":
                updates = {}
                for field_name in (
                    "title",
                    "status",
                    "priority",
                    "source",
                    "resolution_reason",
                    "body_md",
                    "resolution_md",
                ):
                    if field_name in form:
                        updates[field_name] = form[field_name]
                if "type" in form:
                    updates["ticket_type"] = form["type"]
                self.store.update_ticket(ticket_id, **updates)
                return self.redirect(f"/tickets/{ticket_id}")
            if action == "close":
                reason = (form.get("resolution_reason", "") or "").strip()
                if not reason:
                    reason = self.store.get_ticket(ticket_id)["resolution_reason"] or "completed"
                self.store.close_ticket(ticket_id, reason=reason)
                return self.redirect(f"/tickets/{ticket_id}")
            if action == "reopen":
                self.store.reopen_ticket(ticket_id)
                return self.redirect(f"/tickets/{ticket_id}")
            if action == "link":
                self.store.link_tickets(ticket_id, form.get("other_ticket_id", ""))
                return self.redirect(f"/tickets/{ticket_id}")
            if action == "unlink":
                self.store.unlink_tickets(ticket_id, form.get("other_ticket_id", ""))
                return self.redirect(f"/tickets/{ticket_id}")

        return self.error_page("404 Not Found", "Page not found.", "404 Not Found")

    def list_page(self, query: dict[str, str]) -> Response:
        selected_status = query.get("status") or "not_closed"
        selected_sort = normalize_list_sort(query.get("sort"))
        selected_direction = normalize_list_direction(query.get("direction"))
        tickets = self.store.list_tickets(
            status=None if selected_status == "all" else selected_status,
            sort_by=selected_sort,
            direction=selected_direction,
        )
        rows = "".join(
            render_template(
                "ticket_table_row.html",
                ticket_id=h(ticket["id"]),
                title=h(ticket["title"]),
                status_badge=status_badge(ticket["status"]),
                priority_badge=priority_badge(ticket["priority"]),
                updated_time=format_time(ticket["updated_at"], relative=True),
            )
            for ticket in tickets
        )
        if not rows:
            rows = render_template("ticket_table_empty_row.html")

        table = render_template(
            "ticket_table.html",
            ticket_header=self.sort_header_link(
                "Ticket", "ticket_id", selected_sort, selected_direction, selected_status
            ),
            status_header=self.sort_header_link(
                "Status", "status", selected_sort, selected_direction, selected_status
            ),
            priority_header=self.sort_header_link(
                "Priority", "priority", selected_sort, selected_direction, selected_status
            ),
            updated_header=self.sort_header_link(
                "Updated", "updated", selected_sort, selected_direction, selected_status
            ),
            rows=rows,
        )

        filters = render_template(
            "list_filters.html",
            status_options=self.status_filter_options(selected_status),
            sort_options=self.list_sort_options(selected_sort),
            direction_options=self.sort_direction_options(selected_direction),
        )

        content = render_template("list_page.html", filters=filters, table=table)
        return self.page("Nira", content)

    def new_ticket_page(self, query: dict[str, str]) -> Response:
        default_project = query.get("project", "") or self.store.get_default_project()
        content = render_template(
            "new_ticket_page.html",
            default_project=h(default_project),
            body_editor=self.rich_editor("body_md", "", height_px=380),
            ticket_type_options=self.ticket_type_options("task", include_existing=False),
            priority_options=self.priority_options("medium"),
        )
        return self.page("Create Ticket", content)

    def settings_page(self, query: dict[str, str]) -> Response:
        settings = self.store.get_settings()
        save_notice = ""
        if query.get("saved") == "1":
            save_notice = (
                '<div class="alert alert-success" role="alert">'
                "Workspace settings updated."
                "</div>"
            )

        content = render_template(
            "settings_page.html",
            save_notice=save_notice,
            default_project=h(str(settings["default_project"])),
            ticket_count=str(settings["ticket_count"]),
        )
        return self.page("Settings", content)

    def ticket_detail_page(self, ticket_id: str) -> Response:
        details = self.store.ticket_details(ticket_id)
        ticket = details["ticket"]
        related = details["related"]

        related_items = "".join(
            render_template(
                "related_item.html",
                ticket_id=h(ticket["id"]),
                related_ticket_id=h(item["id"]),
            )
            for item in related
        )
        if not related_items:
            related_items = '<li class="list-group-item text-body-secondary">No related tickets yet.</li>'

        content = render_template(
            "ticket_detail_page.html",
            project=h(ticket["project"]),
            ticket_id=h(ticket["id"]),
            title=h(ticket["title"]),
            status_badge=status_badge(ticket["status"]),
            priority_badge=priority_badge(ticket["priority"]),
            body_editor=self.rich_editor("body_md", ticket["body_md"], height_px=420),
            resolution_md=h(ticket["resolution_md"]),
            related_items=related_items,
            status_select_classes=status_select_classes(ticket["status"]),
            ticket_status_options=self.ticket_status_options(ticket["status"]),
            priority_options=self.priority_options(ticket["priority"]),
            source=h(ticket["source"]),
            ticket_type_options=self.ticket_type_options(ticket["type"]),
            created_time=format_time(ticket["created_at"]),
            updated_time=format_time(ticket["updated_at"], relative=True),
        )
        return self.page(ticket["id"], content)

    def status_filter_options(self, selected: str) -> str:
        options = [
            ("not_closed", "not closed"),
            ("open", "open"),
            ("in_progress", "in progress"),
            ("closed", "completed"),
            ("all", "all"),
        ]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def list_sort_options(self, selected: str) -> str:
        options = [
            ("updated", "updated"),
            ("ticket_id", "ticket ID"),
            ("priority", "priority"),
            ("status", "status"),
        ]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def sort_direction_options(self, selected: str) -> str:
        options = [("desc", "descending"), ("asc", "ascending")]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def sort_header_link(
        self,
        label: str,
        sort_key: str,
        selected_sort: str,
        selected_direction: str,
        selected_status: str,
    ) -> str:
        is_active = sort_key == selected_sort
        next_direction = "asc" if is_active and selected_direction == "desc" else "desc"
        indicator = ""
        if is_active:
            indicator = " ↓" if selected_direction == "desc" else " ↑"

        href = "/?" + urlencode(
            {
                "status": selected_status,
                "sort": sort_key,
                "direction": next_direction,
            }
        )
        return (
            f'<a class="link-dark text-decoration-none d-inline-flex align-items-center gap-1" href="{h(href)}">'
            f"{h(label)}<span>{h(indicator)}</span></a>"
        )

    def ticket_status_options(self, selected: str) -> str:
        options = [("open", "open"), ("in_progress", "in progress"), ("closed", "completed")]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def priority_options(self, selected: str) -> str:
        options = [("low", "low"), ("medium", "medium"), ("high", "high"), ("critical", "critical")]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def ticket_type_options(self, selected: str, *, include_existing: bool = True) -> str:
        options = [("task", "task"), ("bug", "bug")]
        normalized_selected = (selected or "task").strip() or "task"
        if include_existing and normalized_selected not in {value for value, _ in options}:
            options = [(normalized_selected, normalized_selected), *options]
        return "".join(
            f'<option value="{h(value)}"{" selected" if value == normalized_selected else ""}>{h(label)}</option>'
            for value, label in options
        )

    def rich_editor(self, field_name: str, value: str, *, height_px: int) -> str:
        return render_template(
            "rich_editor.html",
            field_name=h(field_name),
            height_px=height_px,
            value=h(value),
        )

    def page(self, title: str, content: str) -> Response:
        return Response(
            "200 OK",
            render_template(
                "base.html",
                title=h(title),
                brand_logo_url="/assets/nira.png",
                favicon_url="/assets/nira.png",
                bootstrap_css=BOOTSTRAP_CSS,
                bootstrap_css_integrity=BOOTSTRAP_CSS_INTEGRITY,
                toast_ui_editor_css=TOAST_UI_EDITOR_CSS,
                bootstrap_js=BOOTSTRAP_JS,
                bootstrap_js_integrity=BOOTSTRAP_JS_INTEGRITY,
                toast_ui_editor_js=TOAST_UI_EDITOR_JS,
                content=content,
            ),
        )

    def redirect(self, location: str) -> Response:
        return Response("303 See Other", "", headers=[("Location", location)])

    def error_page(self, title: str, message: str, status: str) -> Response:
        content = render_template("error_page.html", title=h(title), message=h(message))
        return Response(status, self.page(title, content).body)

    def asset_response(self, asset_parts: list[str]) -> Response:
        if not asset_parts:
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        asset_path = (ASSETS_DIR / Path(*asset_parts)).resolve()
        try:
            asset_path.relative_to(ASSETS_DIR.resolve())
        except ValueError:
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        if not asset_path.is_file():
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        content_type, _ = mimetypes.guess_type(asset_path.name)
        return Response(
            "200 OK",
            asset_path.read_bytes(),
            content_type=content_type or "application/octet-stream",
        )

    def parse_form(self, environ) -> dict[str, str]:
        content_length = environ.get("CONTENT_LENGTH", "") or "0"
        length = int(content_length)
        payload = environ["wsgi.input"].read(length).decode("utf-8")
        parsed = parse_qs(payload, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}

    def parse_query(self, query_string: str) -> dict[str, str]:
        parsed = parse_qs(query_string, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}


def serve(store: NiraStore, host: str, port: int) -> None:
    app = NiraWebApp(store)
    try:
        with make_server(host, port, app, handler_class=QuietRequestHandler) as server:
            print(f"Serving Nira on http://{host}:{port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                return
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise ValidationError(
            f"Could not start Nira server on http://{host}:{port}: {reason}."
        ) from exc
