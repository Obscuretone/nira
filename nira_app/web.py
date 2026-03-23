from __future__ import annotations

import html
from datetime import UTC, datetime
from dataclasses import dataclass
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


@dataclass
class Response:
    status: str
    body: str
    content_type: str = "text/html; charset=utf-8"
    headers: list[tuple[str, str]] | None = None

    def to_wsgi(self, start_response):
        response_headers = [("Content-Type", self.content_type)]
        if self.headers:
            response_headers.extend(self.headers)
        payload = self.body.encode("utf-8")
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

        if method == "GET" and path == "/":
            return self.list_page(query)

        if method == "GET" and path == "/tickets/new":
            return self.new_ticket_page(query)

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
        rows = []
        for ticket in tickets:
            rows.append(
                f"""
                <tr>
                  <td><a class="link-dark fw-semibold" href="/tickets/{h(ticket['id'])}">{h(ticket['id'])}</a></td>
                  <td>{h(ticket['title'])}</td>
                  <td>{status_badge(ticket['status'])}</td>
                  <td>{priority_badge(ticket['priority'])}</td>
                  <td>{format_time(ticket['updated_at'], relative=True)}</td>
                </tr>
                """
            )

        table = (
            """
            <div class="card shadow-sm">
              <div class="card-body p-0">
                <div class="table-responsive">
                  <table class="table table-hover align-middle mb-0">
                    <thead class="table-light">
                      <tr>
                        <th scope="col">"""
            + self.sort_header_link("Ticket", "ticket_id", selected_sort, selected_direction, selected_status)
            + """</th>
                        <th scope="col">Title</th>
                        <th scope="col">"""
            + self.sort_header_link("Status", "status", selected_sort, selected_direction, selected_status)
            + """</th>
                        <th scope="col">"""
            + self.sort_header_link("Priority", "priority", selected_sort, selected_direction, selected_status)
            + """</th>
                        <th scope="col">"""
            + self.sort_header_link("Updated", "updated", selected_sort, selected_direction, selected_status)
            + """</th>
                      </tr>
                    </thead>
                    <tbody>
            """
            + "".join(rows or ['<tr><td colspan="5" class="text-body-secondary p-4">No tickets yet.</td></tr>'])
            + """
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
            """
        )

        filters = f"""
        <form class="row g-3 mb-4" method="get" action="/">
          <div class="col-md-3">
            <label class="form-label" for="status">Status</label>
            <select class="form-select" id="status" name="status">
              {self.status_filter_options(selected_status)}
            </select>
          </div>
          <div class="col-md-3">
            <label class="form-label" for="sort">Sort By</label>
            <select class="form-select" id="sort" name="sort">
              {self.list_sort_options(selected_sort)}
            </select>
          </div>
          <div class="col-md-2">
            <label class="form-label" for="direction">Direction</label>
            <select class="form-select" id="direction" name="direction">
              {self.sort_direction_options(selected_direction)}
            </select>
          </div>
          <div class="col-md-4 d-flex align-items-end gap-2">
            <button class="btn btn-dark" type="submit">Filter</button>
            <a class="btn btn-outline-secondary" href="/">Reset</a>
            <a class="btn btn-primary ms-auto" href="/tickets/new">Create Ticket</a>
          </div>
        </form>
        """

        content = f"""
        <div class="d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">
          <div>
            <p class="text-uppercase text-body-secondary small mb-1">Local issue tracker</p>
            <h1 class="h2 mb-0">Nira</h1>
          </div>
          <div class="text-body-secondary">SQLite-backed tickets for the current project.</div>
        </div>
        {filters}
        {table}
        """
        return self.page("Nira", content)

    def new_ticket_page(self, query: dict[str, str]) -> Response:
        default_project = query.get("project", "") or self.store.get_default_project()
        content = f"""
        <div class="d-flex justify-content-between align-items-center mb-4">
          <div>
            <p class="text-uppercase text-body-secondary small mb-1">New Ticket</p>
            <h1 class="h2 mb-0">Create Ticket</h1>
          </div>
          <a class="btn btn-outline-secondary" href="/">Back to Tickets</a>
        </div>
        <form method="post" action="/tickets" class="row g-4">
          <div class="col-lg-8">
            <div class="card shadow-sm">
              <div class="card-body">
                <div class="mb-3">
                  <label class="form-label" for="title">Title</label>
                  <input class="form-control" id="title" name="title" required>
                </div>
                <h2 class="h5 mb-3">Body</h2>
                {self.rich_editor("body_md", "", height_px=380)}
              </div>
            </div>
          </div>
          <div class="col-lg-4">
            <div class="card shadow-sm h-100">
              <div class="card-body">
                <div class="mb-3">
                  <label class="form-label" for="project_display">Ticket Prefix</label>
                  <input class="form-control" id="project_display" value="{h(default_project)}-" readonly>
                  <input type="hidden" name="project" value="{h(default_project)}">
                </div>
                <div class="mb-3">
                  <label class="form-label" for="source">Source</label>
                  <input class="form-control" id="source" name="source" placeholder="why this ticket exists">
                </div>
                <div class="mb-3">
                  <label class="form-label" for="type">Type</label>
                  <select class="form-select" id="type" name="type">
                    {self.ticket_type_options("task", include_existing=False)}
                  </select>
                </div>
                <div class="mb-3">
                  <label class="form-label" for="priority">Priority</label>
                  <select class="form-select" id="priority" name="priority">
                    {self.priority_options("medium")}
                  </select>
                </div>
                <button class="btn btn-primary w-100" type="submit">Create Ticket</button>
              </div>
            </div>
          </div>
        </form>
        """
        return self.page("Create Ticket", content)

    def ticket_detail_page(self, ticket_id: str) -> Response:
        details = self.store.ticket_details(ticket_id)
        ticket = details["ticket"]
        related = details["related"]

        related_items = "".join(
            f"""
            <li class="list-group-item d-flex justify-content-between align-items-center">
              <a class="link-dark fw-semibold" href="/tickets/{h(item['id'])}">{h(item['id'])}</a>
              <form method="post" action="/tickets/{h(ticket['id'])}/unlink" class="m-0">
                <input type="hidden" name="other_ticket_id" value="{h(item['id'])}">
                <button class="btn btn-sm btn-outline-danger" type="submit">Remove</button>
              </form>
            </li>
            """
            for item in related
        )
        if not related_items:
            related_items = '<li class="list-group-item text-body-secondary">No related tickets yet.</li>'

        content = f"""
        <div class="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
          <div>
            <p class="text-uppercase text-body-secondary small mb-1">{h(ticket['project'])}</p>
            <h1 class="h2 mb-1">{h(ticket['id'])} {h(ticket['title'])}</h1>
            <div class="d-flex flex-wrap gap-2">
              {status_badge(ticket['status'])}
              {priority_badge(ticket['priority'])}
            </div>
          </div>
          <div class="d-flex gap-2">
            <a class="btn btn-outline-secondary" href="/">Back to Tickets</a>
            <a class="btn btn-primary" href="/tickets/new">New Ticket</a>
          </div>
        </div>

        <div class="row g-4">
          <div class="col-xl-8">
            <form id="ticket-edit-form" method="post" action="/tickets/{h(ticket['id'])}/edit" class="mb-4">
              <div class="card shadow-sm mb-4">
                <div class="card-body">
                  <label class="form-label" for="title">Title</label>
                  <input
                    class="form-control form-control-lg mb-4"
                    id="title"
                    name="title"
                    value="{h(ticket['title'])}"
                    required
                  >
                  <h2 class="h5 mb-3">Body</h2>
                  {self.rich_editor("body_md", ticket["body_md"], height_px=420)}
                  <div class="d-flex justify-content-end mt-3">
                    <button class="btn btn-primary" type="submit">Save</button>
                  </div>
                </div>
              </div>

              <div class="card shadow-sm mb-4">
              <div class="card-body">
                <h2 class="h5 mb-3">Resolution Notes</h2>
                  <textarea
                    class="form-control font-monospace"
                    id="resolution_md"
                    name="resolution_md"
                    rows="10"
                    placeholder="Optional closeout notes, decisions, or follow-up context."
                  >{h(ticket['resolution_md'])}</textarea>
                  <div class="d-flex justify-content-end mt-3">
                    <button class="btn btn-primary" type="submit">Save</button>
                  </div>
                </div>
              </div>
            </form>

            <div class="card shadow-sm mb-4">
              <div class="card-body">
                <h2 class="h5 mb-3">Related</h2>
                <form method="post" action="/tickets/{h(ticket['id'])}/link" class="mb-3">
                  <div class="input-group">
                    <input class="form-control" type="text" name="other_ticket_id" placeholder="EMH-2" required>
                    <button class="btn btn-outline-dark" type="submit">Link</button>
                  </div>
                </form>
                <ul class="list-group list-group-flush">{related_items}</ul>
              </div>
            </div>
          </div>

          <div class="col-xl-4">
            <div class="card shadow-sm mb-4">
              <div class="card-body">
                <h2 class="h5 mb-3">Status</h2>
                <div class="row g-3 align-items-end">
                  <div class="col-12">
                    <label class="form-label" for="status">Status</label>
                    <select
                      class="{status_select_classes(ticket['status'])}"
                      id="status"
                      name="status"
                      form="ticket-edit-form"
                      data-status-select
                      data-auto-submit
                    >
                      {self.ticket_status_options(ticket['status'])}
                    </select>
                  </div>
                  <div class="col-12">
                    <label class="form-label" for="priority">Priority</label>
                    <select class="form-select" id="priority" name="priority" form="ticket-edit-form" data-auto-submit>
                      {self.priority_options(ticket['priority'])}
                    </select>
                  </div>
                  <div class="col-12">
                    <button class="btn btn-primary w-100" type="submit" form="ticket-edit-form">Save</button>
                  </div>
                </div>
              </div>
            </div>

            <div class="card shadow-sm mb-4">
              <div class="card-body">
                <h2 class="h5 mb-3">Details</h2>
                <div class="row g-3">
                  <div class="col-12">
                    <label class="form-label" for="source">Source</label>
                    <input
                      class="form-control"
                      id="source"
                      name="source"
                      value="{h(ticket['source'])}"
                      form="ticket-edit-form"
                    >
                  </div>
                  <div class="col-12">
                    <label class="form-label" for="type">Type</label>
                    <select class="form-select" id="type" name="type" form="ticket-edit-form" data-auto-submit>
                      {self.ticket_type_options(ticket['type'])}
                    </select>
                  </div>
                  <div class="col-12">
                    <button class="btn btn-primary w-100" type="submit" form="ticket-edit-form">Save</button>
                  </div>
                </div>
              </div>
            </div>

            <div class="card shadow-sm mb-4">
              <div class="card-body">
                <h2 class="h5 mb-3">Metadata</h2>
                <dl class="row mb-0">
                  <dt class="col-sm-4">Created</dt><dd class="col-sm-8">{format_time(ticket['created_at'])}</dd>
                  <dt class="col-sm-4">Updated</dt>
                  <dd class="col-sm-8">{format_time(ticket['updated_at'], relative=True)}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>
        """
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
        return f"""
        <div class="rich-editor-shell" data-rich-editor="{h(field_name)}" data-editor-height="{height_px}px">
          <textarea
            class="form-control font-monospace rich-editor-source"
            name="{h(field_name)}"
            rows="12"
          >{h(value)}</textarea>
          <div class="rich-editor-target d-none"></div>
        </div>
        """

    def page(self, title: str, content: str) -> Response:
        return Response(
            "200 OK",
            f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{h(title)} · Nira</title>
    <link href="{BOOTSTRAP_CSS}" rel="stylesheet" integrity="{BOOTSTRAP_CSS_INTEGRITY}" crossorigin="anonymous">
    <link href="{TOAST_UI_EDITOR_CSS}" rel="stylesheet">
    <style>
      body {{
        background:
          radial-gradient(circle at top left, rgba(13, 110, 253, 0.08), transparent 35%),
          linear-gradient(180deg, #f8f9fa 0%, #eef1f4 100%);
        min-height: 100vh;
      }}
      .page-shell {{
        padding-block: 2.5rem 4rem;
      }}
      .markdown-output h1,
      .markdown-output h2,
      .markdown-output h3 {{
        margin-top: 1rem;
        margin-bottom: 0.75rem;
      }}
      .markdown-output pre {{
        background: #111827;
        color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.75rem;
        overflow-x: auto;
      }}
      .rich-editor-source {{
        min-height: 12rem;
      }}
      .toastui-editor-defaultUI {{
        border-radius: 0.75rem;
      }}
      .toastui-editor-toolbar {{
        border-top-left-radius: 0.75rem;
        border-top-right-radius: 0.75rem;
      }}
    </style>
  </head>
  <body>
    <main class="container page-shell">
      {content}
    </main>
    <script src="{BOOTSTRAP_JS}" integrity="{BOOTSTRAP_JS_INTEGRITY}" crossorigin="anonymous"></script>
    <script src="{TOAST_UI_EDITOR_JS}"></script>
    <script>
      const statusSelectClasses = {{
        open: "form-select form-select-lg fw-semibold border-secondary bg-secondary-subtle text-secondary-emphasis",
        in_progress: "form-select form-select-lg fw-semibold border-primary bg-primary-subtle text-primary-emphasis",
        closed: "form-select form-select-lg fw-semibold border-success bg-success-subtle text-success-emphasis",
      }};

      document.querySelectorAll(".rich-editor-shell").forEach((shell) => {{
        const source = shell.querySelector(".rich-editor-source");
        const target = shell.querySelector(".rich-editor-target");
        const form = shell.closest("form");
        if (!window.toastui || !window.toastui.Editor) {{
          return;
        }}

        const editor = new window.toastui.Editor({{
          el: target,
          height: shell.dataset.editorHeight || "320px",
          initialEditType: "wysiwyg",
          initialValue: source.value,
          previewStyle: "vertical",
          usageStatistics: false,
        }});

        target.classList.remove("d-none");
        source.classList.add("d-none");

        const syncEditor = () => {{
          source.value = editor.getMarkdown();
        }};

        form?.addEventListener("submit", syncEditor);
      }});

      document.querySelectorAll("[data-status-select]").forEach((select) => {{
        const applyStatusClass = () => {{
          select.className = statusSelectClasses[select.value] || statusSelectClasses.open;
        }};

        applyStatusClass();
        select.addEventListener("change", applyStatusClass);
      }});

      document.querySelectorAll("[data-auto-submit]").forEach((input) => {{
        input.addEventListener("change", () => {{
          input.form?.requestSubmit();
        }});
      }});
    </script>
  </body>
</html>""",
        )

    def redirect(self, location: str) -> Response:
        return Response("303 See Other", "", headers=[("Location", location)])

    def error_page(self, title: str, message: str, status: str) -> Response:
        content = f"""
        <div class="row justify-content-center">
          <div class="col-lg-8">
            <div class="card shadow-sm">
              <div class="card-body p-5 text-center">
                <p class="text-uppercase text-body-secondary small mb-2">Nira</p>
                <h1 class="h3 mb-3">{h(title)}</h1>
                <p class="text-body-secondary mb-4">{h(message)}</p>
                <a class="btn btn-primary" href="/">Back to Tickets</a>
              </div>
            </div>
          </div>
        </div>
        """
        return Response(status, self.page(title, content).body)

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
    with make_server(host, port, app, handler_class=QuietRequestHandler) as server:
        print(f"Serving Nira on http://{host}:{port}", flush=True)
        server.serve_forever()
