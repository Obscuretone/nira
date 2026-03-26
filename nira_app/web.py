from __future__ import annotations

import html
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode
from .router import Router, Response
from wsgiref.simple_server import WSGIRequestHandler, make_server

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .i18n import get_translator
from .markdown import render_markdown
from .storage import (
    DashboardStats,
    NiraError,
    NiraStore,
    TicketData,
    TicketDetails,
    TicketNotFoundError,
    ValidationError,
    normalize_list_direction,
    normalize_list_sort,
    utc_now,
)


BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"
BOOTSTRAP_JS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js"
BOOTSTRAP_CSS_INTEGRITY = "sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
BOOTSTRAP_JS_INTEGRITY = "sha384-FKyoEForCGlyvwx9Hj09JcYn3nv7wiPVlz7YYwJrWVcXK/BmnVDxM+D2scQbITxI"
TOAST_UI_EDITOR_CSS = "https://uicdn.toast.com/editor/3.2.2/toastui-editor.min.css"
TOAST_UI_EDITOR_DARK_CSS = "https://uicdn.toast.com/editor/3.2.2/theme/toastui-editor-dark.min.css"
TOAST_UI_EDITOR_JS = "https://uicdn.toast.com/editor/3.2.2/toastui-editor-all.min.js"
HTMX_JS = "https://unpkg.com/htmx.org@1.9.10"
HTMX_JS_INTEGRITY = "sha384-D1Kt99CQMDuVetoL1lrYwg5t+9QdHe7NLX/SoJYkXDFfX37iInKRy5xLSi8nO7UC"
SORTABLE_JS = "https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"
TEMPLATES_DIR = Path(__file__).with_name("templates")
ASSETS_DIR = Path(__file__).with_name("assets")


def h(value: str | None) -> str:
    return html.escape(value or "")


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_time(value: str, _=lambda x: x) -> str:
    moment = parse_timestamp(value)
    if moment is None:
        return value
    now = datetime.now(UTC)
    delta = now - moment.astimezone(UTC)
    seconds = max(0, int(delta.total_seconds()))

    if seconds < 60:
        return _("just now")
    if seconds < 3600:
        minutes = seconds // 60
        return _("{count} minute(s) ago").replace("{count}", str(minutes))
    if seconds < 86400:
        hours = seconds // 3600
        return _("{count} hour(s) ago").replace("{count}", str(hours))
    if seconds < 604800:
        days = seconds // 86400
        return _("{count} day(s) ago").replace("{count}", str(days))
    weeks = seconds // 604800
    return _("{count} week(s) ago").replace("{count}", str(weeks))


def highlight(text: str, query: str | None) -> str:
    if not query or not query.strip():
        return h(text)

    import re

    # Strip some FTS5 operators that might be in the query words
    words = [re.escape(word.strip('"').rstrip("*")) for word in query.split() if word]
    words = [w for w in words if w]
    if not words:
        return h(text)

    pattern = re.compile(f"({'|'.join(words)})", re.IGNORECASE)
    parts = pattern.split(text)

    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Match
            result.append(f'<mark class="p-0">{h(part)}</mark>')
        else:
            result.append(h(part))

    return "".join(result)


def format_time(value: str, *, relative: bool = False, _=lambda x: x) -> str:
    display = relative_time(value, _) if relative else value
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


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class NiraWebApp:
    def __init__(self, store: NiraStore):
        self.store = store
        self.router = Router()
        self.jinja_env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._setup_jinja()
        self._register_routes()

    def _setup_jinja(self):
        self.jinja_env.globals.update(
            {
                "htmx_js": HTMX_JS,
                "htmx_js_integrity": HTMX_JS_INTEGRITY,
                "sortable_js": SORTABLE_JS,
                "bootstrap_css": BOOTSTRAP_CSS,
                "bootstrap_css_integrity": BOOTSTRAP_CSS_INTEGRITY,
                "bootstrap_js": BOOTSTRAP_JS,
                "bootstrap_js_integrity": BOOTSTRAP_JS_INTEGRITY,
                "toast_ui_editor_css": TOAST_UI_EDITOR_CSS,
                "toast_ui_editor_dark_css": TOAST_UI_EDITOR_DARK_CSS,
                "toast_ui_editor_js": TOAST_UI_EDITOR_JS,
                "brand_logo_url": "/assets/nira.png",
                "favicon_url": "/assets/nira.png",
                "h": h,
                "utc_now": utc_now,
                "status_badge": status_badge,
                "priority_badge": priority_badge,
                "status_select_classes": status_select_classes,
                "urlencode": urlencode,
                "sort_header_link": self.sort_header_link,
                "highlight": highlight,
            }
        )

    def render(self, template_name: str, **context: Any) -> str:
        template = self.jinja_env.get_template(template_name)
        settings = self.store.get_settings()
        translator = get_translator(getattr(self, "_current_lang", "en"))

        def format_time_wrapper(value: str, *, relative: bool = False) -> str:
            return format_time(value, relative=relative, _=translator)

        full_context = {
            "current_theme": settings.get("theme", "auto"),
            "_": translator,
            "format_time": format_time_wrapper,
            "render_markdown": render_markdown,
            **context,
        }
        return template.render(**full_context)

    def _register_routes(self):
        # Asset delivery
        self.router.add("GET", r"/assets/(?P<asset_path>.*)", self.asset_response)

        # Main pages
        self.router.add("GET", "/", self.dashboard_page)
        self.router.add("GET", "/list", self.list_page)
        self.router.add("GET", "/board", self.board_page)
        self.router.add("GET", "/tickets/new", self.new_ticket_page)
        self.router.add("GET", "/tickets/search_dropdown", self.ticket_search_dropdown)
        self.router.add("GET", "/tickets/editor_autocomplete", self.editor_autocomplete)
        self.router.add("GET", "/settings", self.settings_page)
        self.router.add("POST", "/tickets", self.create_ticket_action)
        self.router.add("POST", "/settings", self.save_settings_action)
        self.router.add("POST", "/preview", self.preview_markdown_action)

        # Ticket details and actions
        self.router.add("GET", "/tickets/{ticket_id}", self.ticket_detail_page)
        self.router.add("POST", "/tickets/{ticket_id}/edit", self.edit_ticket_action)
        self.router.add("POST", "/tickets/{ticket_id}/close", self.close_ticket_action)
        self.router.add("POST", "/tickets/{ticket_id}/reopen", self.reopen_ticket_action)
        self.router.add("POST", "/tickets/{ticket_id}/comment", self.add_comment_action)
        self.router.add("POST", "/tickets/{ticket_id}/link", self.link_ticket_action)
        self.router.add("POST", "/tickets/{ticket_id}/unlink", self.unlink_ticket_action)

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "") or "/"
        query = self.parse_query(environ.get("QUERY_STRING", ""))
        form = self.parse_form(environ) if method == "POST" else {}

        # Determine language for this request
        settings = self.store.get_settings()
        lang_setting = str(settings.get("language", "auto"))
        if lang_setting == "auto":
            accept_lang = environ.get("HTTP_ACCEPT_LANGUAGE", "en")
            self._current_lang = accept_lang.split(",")[0].split("-")[0].lower() if accept_lang else "en"
        else:
            self._current_lang = lang_setting

        try:
            match = self.router.match(method, path)
            if match:
                handler, params = match
                response = handler(query=query, form=form, **params)
            else:
                response = self.error_page("404 Not Found", "Page not found.", "404 Not Found")
        except TicketNotFoundError as exc:
            response = self.error_page("404 Not Found", str(exc), "404 Not Found")
        except ValidationError as exc:
            response = self.error_page("400 Bad Request", str(exc), "400 Bad Request")
        except NiraError as exc:
            response = self.error_page("500 Internal Server Error", str(exc), "500 Internal Server Error")
        except Exception as exc:  # pragma: no cover
            response = self.error_page(
                "500 Internal Server Error",
                f"Unexpected error: {exc.__class__.__name__} {exc}. {__import__('traceback').format_exc()}",
                "500 Internal Server Error",
            )
        return response.to_wsgi(start_response)

    def dashboard_page(self, query: dict[str, str], form: dict[str, str]) -> Response:
        stats: DashboardStats = self.store.get_dashboard_stats()
        body = self.render("dashboard_page.html", stats=stats)
        return Response("200 OK", body)

    def list_page(self, query: dict[str, str], form: dict[str, str]) -> Response:
        search_query = query.get("search")
        label_filter = query.get("label")
        # Default to 'all' if filtering by search or label, otherwise 'not_closed'
        selected_status = query.get("status")
        if not selected_status:
            if search_query or label_filter:
                selected_status = "all"
            else:
                selected_status = "not_closed"
        selected_sort = normalize_list_sort(query.get("sort"))
        selected_direction = normalize_list_direction(query.get("direction"))
        overdue_filter = query.get("overdue") == "1"
        try:
            page = int(query.get("page", 1))
            if page < 1:
                page = 1
        except ValueError:
            page = 1
        limit = 20
        offset = (page - 1) * limit

        status_filter = None if selected_status == "all" else selected_status

        tickets: list[TicketData] = self.store.list_tickets(
            status=status_filter,
            sort_by=selected_sort,
            direction=selected_direction,
            limit=limit,
            offset=offset,
            search=search_query,
            label=label_filter,
            overdue=overdue_filter,
        )
        filtered_tickets_count = self.store.count_tickets(
            status=status_filter, search=search_query, label=label_filter, overdue=overdue_filter
        )
        total_project_tickets = self.store.count_tickets()
        total_pages = (filtered_tickets_count + limit - 1) // limit

        body = self.render(
            "list_page.html",
            tickets=tickets,
            selected_status=selected_status,
            selected_sort=selected_sort,
            selected_direction=selected_direction,
            search_query=search_query,
            label_filter=label_filter,
            overdue_filter=overdue_filter,
            status_options=self.status_filter_options(),
            sort_options=self.list_sort_options(),
            direction_options=self.sort_direction_options(),
            page=page,
            total_pages=total_pages,
            total_tickets=total_project_tickets,
            filtered_tickets_count=filtered_tickets_count,
            statuses=self.store.get_statuses(),
        )
        return Response("200 OK", body)

    def board_page(self, query: dict[str, str], form: dict[str, str]) -> Response:
        search_query = query.get("search")
        label_filter = query.get("label")
        # Fetch a reasonable number of recent tickets for the board
        tickets: list[TicketData] = self.store.list_tickets(search=search_query, label=label_filter, limit=100)

        statuses = self.store.get_statuses()
        tickets_by_status: dict[str, list[TicketData]] = {status: [] for status in statuses}
        for t in tickets:
            if t["status"] in tickets_by_status:
                tickets_by_status[t["status"]].append(t)

        body = self.render(
            "board_page.html",
            search_query=search_query,
            label_filter=label_filter,
            statuses=statuses,
            tickets_by_status=tickets_by_status,
        )
        return Response("200 OK", body)

    def new_ticket_page(self, query: dict[str, str], form: dict[str, str]) -> Response:
        default_project = query.get("project", "") or self.store.get_default_project()
        body = self.render(
            "new_ticket_page.html",
            default_project=default_project,
            parent_id=query.get("parent"),
            ticket_type_options=self.ticket_type_options("task", include_existing=False),
            priority_options=self.priority_options(),
        )
        return Response("200 OK", body)

    def ticket_search_dropdown(self, query: dict[str, str], form: dict[str, str]) -> Response:
        search_term = (query.get("q") or query.get("parent") or query.get("other_ticket_id") or "").strip()
        exclude_id = query.get("exclude")

        # When empty, show most recent 5
        tickets = self.store.list_tickets(search=search_term if search_term else None, limit=5, sort_by="updated")

        if exclude_id:
            tickets = [t for t in tickets if t["id"] != exclude_id]

        body = self.render(
            "partials/ticket_dropdown.html",
            tickets=tickets,
            search_term=search_term,
        )
        return Response("200 OK", body)

    def editor_autocomplete(self, query: dict[str, str], form: dict[str, str]) -> Response:
        search_term = query.get("q", "").strip()
        tickets = self.store.list_tickets(search=search_term if search_term else None, limit=5, sort_by="updated")

        body = self.render(
            "partials/editor_autocomplete.html",
            tickets=tickets,
        )
        return Response("200 OK", body)

    def settings_page(self, query: dict[str, str], form: dict[str, str]) -> Response:
        settings = self.store.get_settings()
        body = self.render(
            "settings_page.html",
            saved=query.get("saved") == "1",
            default_project=settings["default_project"],
            theme=settings["theme"],
            language=settings["language"],
            ticket_count=settings["ticket_count"],
        )
        return Response("200 OK", body)

    def create_ticket_action(self, query: dict[str, str], form: dict[str, str]) -> Response:
        parent_db_id = None
        parent_id = form.get("parent")
        if parent_id:
            parent_ticket = self.store.get_ticket(parent_id)
            parent_db_id = parent_ticket["db_id"]

        ticket = self.store.create_ticket(
            form.get("project", ""),
            form.get("title", ""),
            source=form.get("source", ""),
            ticket_type=form.get("type", "task"),
            priority=form.get("priority", "medium"),
            labels=form.get("labels", ""),
            due_date=form.get("due_date") or None,
            parent_id=parent_db_id,
            story_points=int(form["story_points"]) if form.get("story_points") else None,
            body_md=form.get("body_md", ""),
            resolution_md=form.get("resolution_md", ""),
        )
        return self.redirect(f"/tickets/{ticket['id']}")

    def save_settings_action(self, query: dict[str, str], form: dict[str, str]) -> Response:
        updates = {}
        if "default_project" in form:
            updates["default_project"] = form["default_project"]
        if "theme" in form:
            updates["theme"] = form["theme"]
        if "language" in form:
            updates["language"] = form["language"]

        if updates:
            self.store.update_settings(updates)

        # If it's an HTMX request from the theme toggle, we don't need a full redirect
        if "HX-Request" in query or "theme" in form and len(form) == 1:
            return Response("204 No Content", "")

        return self.redirect("/settings?saved=1")

    def preview_markdown_action(self, query: dict[str, str], form: dict[str, str]) -> Response:
        return Response(
            "200 OK",
            render_markdown(form.get("markdown", "")),
        )

    def ticket_detail_page(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        details: TicketDetails = self.store.ticket_details(ticket_id)

        comments = details.get("comments", [])
        history = details.get("history", [])

        timeline: list[dict[str, Any]] = []
        for c in comments:
            timeline.append({"type": "comment", "data": c, "created_at": c["created_at"]})
        for h in history:
            timeline.append({"type": "history", "data": h, "created_at": h["created_at"]})

        timeline.sort(key=lambda x: str(x["created_at"]), reverse=True)

        body = self.render(
            "ticket_detail_page.html",
            ticket=details["ticket"],
            parent=details.get("parent"),
            related=details.get("related", []),
            sub_tasks=details.get("sub_tasks", []),
            timeline=timeline,
            ticket_status_options=self.ticket_status_options(),
            priority_options=self.priority_options(),
            ticket_type_options=self.ticket_type_options(details["ticket"]["type"]),
        )
        return Response("200 OK", body)

    def edit_ticket_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        updates: dict[str, Any] = {}
        for field_name in (
            "title",
            "status",
            "priority",
            "source",
            "labels",
            "due_date",
            "story_points",
            "resolution_reason",
            "body_md",
            "resolution_md",
        ):
            if field_name in form:
                val = form[field_name]
                if field_name == "due_date" and not val:
                    updates[field_name] = None
                elif field_name == "story_points":
                    updates[field_name] = int(val) if val else None
                else:
                    updates[field_name] = val

        if "type" in form:
            updates["ticket_type"] = form["type"]
        if "parent" in form:
            parent_val = form["parent"].strip()
            if not parent_val:
                updates["parent_id"] = None
            else:
                try:
                    parent_ticket = self.store.get_ticket(parent_val)
                    updates["parent_id"] = parent_ticket["db_id"]
                except TicketNotFoundError:
                    raise ValidationError(f"Parent ticket '{parent_val}' not found.")

        self.store.update_ticket(ticket_id, **updates)
        return self.redirect(f"/tickets/{ticket_id}")

    def close_ticket_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        resolution_md = (form.get("resolution_md", "") or "").strip()
        if not resolution_md:
            resolution_md = self.store.get_ticket(ticket_id)["resolution_md"] or "Closed via web UI."
        self.store.close_ticket(ticket_id, resolution_md=resolution_md)
        return self.redirect(f"/tickets/{ticket_id}")

    def reopen_ticket_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        self.store.reopen_ticket(ticket_id)
        return self.redirect(f"/tickets/{ticket_id}")

    def add_comment_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        body_md = form.get("body_md", "")
        self.store.add_comment(ticket_id, body_md)
        return self.redirect(f"/tickets/{ticket_id}")

    def link_ticket_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        self.store.link_tickets(ticket_id, form.get("other_ticket_id", ""))
        return self.redirect(f"/tickets/{ticket_id}")

    def unlink_ticket_action(self, query: dict[str, str], form: dict[str, str], ticket_id: str) -> Response:
        self.store.unlink_tickets(ticket_id, form.get("other_ticket_id", ""))
        return self.redirect(f"/tickets/{ticket_id}")

    def status_filter_options(self) -> list[tuple[str, str]]:
        options = [
            ("not_closed", "not closed"),
        ]
        for status in self.store.get_statuses():
            label = "completed" if status == "closed" else status.replace("_", " ")
            options.append((status, label))
        options.append(("all", "all"))
        return options

    def list_sort_options(self) -> list[tuple[str, str]]:
        return [
            ("updated", "updated"),
            ("ticket_id", "ticket ID"),
            ("priority", "priority"),
            ("status", "status"),
        ]

    def sort_direction_options(self) -> list[tuple[str, str]]:
        return [("desc", "descending"), ("asc", "ascending")]

    def sort_header_link(
        self,
        label: str,
        sort_key: str,
        selected_sort: str,
        selected_direction: str,
        selected_status: str,
        search_query: str | None = None,
        label_filter: str | None = None,
    ) -> str:
        is_active = sort_key == selected_sort
        next_direction = "asc" if is_active and selected_direction == "desc" else "desc"
        indicator = ""
        if is_active:
            indicator = " ↓" if selected_direction == "desc" else " ↑"

        params = {
            "status": selected_status,
            "sort": sort_key,
            "direction": next_direction,
            "page": "1",  # Reset to page 1 on sort change
        }
        if search_query:
            params["search"] = search_query
        if label_filter:
            params["label"] = label_filter

        href = "/list?" + urlencode(params)
        return (
            f'<a class="link-body-emphasis text-decoration-none d-inline-flex align-items-center gap-1" '
            f'href="{h(href)}" hx-get="{h(href)}" hx-target="body" hx-push-url="true">{h(label)}{indicator}</a>'
        )

    def ticket_status_options(self) -> list[tuple[str, str]]:
        statuses = self.store.get_statuses()
        options = []
        for status in statuses:
            label = "completed" if status == "closed" else status.replace("_", " ")
            options.append((status, label))
        return options

    def priority_options(self) -> list[tuple[str, str]]:
        return [("low", "low"), ("medium", "medium"), ("high", "high"), ("critical", "critical")]

    def ticket_type_options(self, selected: str, *, include_existing: bool = True) -> list[tuple[str, str]]:
        options = [("task", "task"), ("bug", "bug")]
        normalized_selected = (selected or "task").strip() or "task"
        if include_existing and normalized_selected not in {value for value, _ in options}:
            options = [(normalized_selected, normalized_selected), *options]
        return options

    def redirect(self, location: str) -> Response:
        return Response("303 See Other", "", headers=[("Location", location)])

    def error_page(self, title: str, message: str, status: str) -> Response:
        body = self.render("error_page.html", error_title=title, message=message)
        return Response(status, body)

    def asset_response(self, query: dict[str, str], form: dict[str, str], asset_path: str) -> Response:
        if not asset_path:
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        # Normalize and resolve the path to prevent directory traversal
        try:
            full_path = (ASSETS_DIR / asset_path).resolve()
            full_path.relative_to(ASSETS_DIR.resolve())
        except (ValueError, OSError):  # pragma: no cover
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        if not full_path.is_file():
            return Response("404 Not Found", "Asset not found.", content_type="text/plain; charset=utf-8")

        content_type, _ = mimetypes.guess_type(full_path.name)
        return Response(
            "200 OK",
            full_path.read_bytes(),
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
        raise ValidationError(f"Could not start Nira server on http://{host}:{port}: {reason}.") from exc
