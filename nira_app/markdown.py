from __future__ import annotations

import html
import re
from urllib.parse import urlparse


def safe_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    return html.escape(url, quote=True)


def render_inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    code_spans: list[str] = []

    def replace_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{match.group(1)}</code>")
        return f"@@CODE{len(code_spans) - 1}@@"

    escaped = re.sub(r"`([^`]+)`", replace_code, escaped)

    def replace_link(match: re.Match[str]) -> str:
        url = safe_url(match.group(2))
        if url is None:
            return match.group(1)
        return f'<a href="{url}">{match.group(1)}</a>'

    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+?)\)", replace_link, escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

    for index, replacement in enumerate(code_spans):
        escaped = escaped.replace(f"@@CODE{index}@@", replacement)
    return escaped


def render_markdown(markdown: str, *, ticket_id: str | None = None) -> str:
    lines = markdown.splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        text = " ".join(line.strip() for line in paragraph)
        parts.append(f"<p>{render_inline(text)}</p>")
        paragraph = []

    def flush_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for i, line in enumerate(lines):
        stripped = line.rstrip()

        if in_code_block:
            if stripped.startswith("```"):
                parts.append("</code></pre>")
                in_code_block = False
            else:
                parts.append(html.escape(line))
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            parts.append("<pre><code>")
            in_code_block = True
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h3>{render_inline(stripped[4:])}</h3>")
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h2>{render_inline(stripped[3:])}</h2>")
            continue

        if stripped.startswith("# "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h1>{render_inline(stripped[2:])}</h1>")
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                parts.append('<ul class="list-unstyled">')
                in_list = True

            content = stripped[2:].strip()
            # Task list support
            if content.startswith("[ ] ") or content.startswith("[x] "):
                checked = content.startswith("[x] ")
                task_text = content[4:]
                checkbox_html = ""
                if ticket_id:
                    # Interactive checkbox via HTMX
                    action = "uncheck" if checked else "check"
                    checkbox_html = (
                        f'<input type="checkbox" class="form-check-input me-2" '
                        f"{'checked' if checked else ''} "
                        f'hx-post="/tickets/{ticket_id}/task/{i}/{action}" '
                        f'hx-target="closest div.activity-body, closest div.card-body" '
                        f'hx-swap="outerHTML">'
                    )
                else:
                    # Static checkbox
                    checkbox_html = (
                        f'<input type="checkbox" class="form-check-input me-2" {"checked" if checked else ""} disabled>'
                    )
                parts.append(
                    f'<li class="d-flex align-items-start">{checkbox_html}<span>{render_inline(task_text)}</span></li>'
                )
            else:
                parts.append(f"<li>{render_inline(content)}</li>")
            continue

        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    if in_code_block:
        parts.append("</code></pre>")

    return "\n".join(parts)
