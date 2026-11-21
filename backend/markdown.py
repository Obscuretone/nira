from __future__ import annotations

import html
import re
from urllib.parse import urlparse


def safe_url(url: str) -> str | None:
    if re.search(r"[\x00-\x20\x7f\\]", url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    return html.escape(url, quote=True)


INLINE = re.compile(
    r"`(?P<code>[^`]+)`|\[(?P<label>[^\]]+)\]\((?P<destination>[^)]+)\)"
    r"|\*\*(?P<strong>[^*]+)\*\*|\*(?P<em>[^*]+)\*"
)


def render_inline(text: str, *, links: bool = True) -> str:
    rendered: list[str] = []
    position = 0
    for match in INLINE.finditer(text):
        rendered.append(html.escape(text[position:match.start()], quote=False))
        if match["code"] is not None:
            rendered.append(f'<code>{html.escape(match["code"], quote=False)}</code>')
        elif match["label"] is not None:
            label = render_inline(match["label"], links=False)
            url = safe_url(match["destination"]) if links else None
            rendered.append(f'<a href="{url}">{label}</a>' if url is not None else label)
        else:
            tag = "strong" if match["strong"] is not None else "em"
            rendered.append(f"<{tag}>{render_inline(match[tag], links=links)}</{tag}>")
        position = match.end()
    rendered.append(html.escape(text[position:], quote=False))
    return "".join(rendered)


def render_markdown(markdown: str) -> str:
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

    for line in lines:
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
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{render_inline(stripped[2:])}</li>")
            continue

        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    if in_code_block:
        parts.append("</code></pre>")

    return "\n".join(parts)
