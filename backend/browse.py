"""Private, stable links to Nira projects and issues."""

from html import escape
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse
from .markdown import render_markdown


def document(title, body, prefix, *, script=False):
    login_js = f'<script src="{escape(prefix)}/browse/login.js" defer></script>' if script else ''
    nav = '' if script else f'''<header class="record-nav"><a href="{escape(prefix)}/"
        aria-label="Open Nira workspace"><span class="nira-mark" aria-hidden="true">n</span>Nira HQ</a></header>'''
    mode = "sign-in" if script else "record"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#24212d"><title>{escape(title)} · Nira</title>
<link rel="stylesheet" href="{escape(prefix)}/browse/nira.css">{login_js}</head>
<body class="{mode}">{nav}<main>{body}</main></body></html>'''


def browse_login(prefix, next_url, passkeys, message="", status=200):
    primary = ""
    if passkeys:
        sign_in = prefix + "/auth/sign-in?" + urlencode({"next": next_url})
        primary = f'<a class="signin-primary" href="{escape(sign_in)}">Sign in with a passkey</a>'
    recovery = f'''<form id="login" action="{escape(prefix)}/api/login" method="post"
        data-next="{escape(next_url)}">
    <label>Username <input name="username" autocomplete="username" required></label>
    <label>Password <input type="password" name="password" autocomplete="current-password" required></label>
    <button type="submit" class="signin-secondary">Sign in</button><p id="error" role="alert"></p></form>'''
    if passkeys:
        recovery = f'<details><summary>Use a password</summary>{recovery}</details>'
    error = f'<p class="signin-error" role="alert">{escape(message)}</p>' if message else ""
    body = f'''<span class="nira-mark" aria-hidden="true">n</span>
    <h1>Sign in to Nira HQ</h1>{error}{primary}{recovery}'''
    return HTMLResponse(document("Sign in", body, prefix, script=True), status_code=status)


login_script = '''document.querySelector('#login').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.querySelector('#error');
  const button = form.querySelector('button');
  button.disabled = true;
  error.textContent = '';
  try {
    const response = await fetch(form.action, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.fromEntries(new FormData(form)))
    });
    if (response.ok) window.location.assign(form.dataset.next);
    else error.textContent = response.status === 429 ? 'Try again in 15 minutes.' : 'Sign-in failed.';
  } catch {
    error.textContent = 'Could not reach Nira. Try again.';
  } finally {
    button.disabled = false;
  }
});'''


def label(value):
    return escape(value.replace('_', ' ').capitalize())


def issue_link(issue):
    return f'<a href="{escape(issue["url"])}">{escape(issue["key"])} · {escape(issue["title"])}</a> ({label(issue["status"])})'


def issue_list(title, issues):
    if not issues:
        return ''
    rows = ''.join(f'<li>{issue_link(issue)}</li>' for issue in issues)
    return f'<h2>{title}</h2><ul>{rows}</ul>'


def browse_project(project, prefix):
    counts = ' · '.join(f'{label(status)} {count}' for status, count in sorted(project['counts'].items()))
    next_issue = issue_list('Next open issue', [project['next_issue']] if project['next_issue'] else [])
    return document(project['name'], f'''<h1>{escape(project['name'])}</h1>
<p class="record-meta"><strong>{escape(project['key'])}</strong> <span>{label(project['status'])}</span> {escape(project['owner'])}</p>
<div class="record-description">{render_markdown(project['description'])}</div><p>{project['total']} issues. {counts}</p>
{next_issue}{issue_list('Epics', project['epics'])}''', prefix)


def browse_issue(issue, prefix):
    resolution = '<h2>Resolution</h2>' + render_markdown(issue['resolution']) if issue['resolution'] else ''
    return document(issue['title'], f'''<p class="record-crumb"><a href="{escape(issue['project_url'])}">{escape(issue['project_name'])}</a></p>
<h1>{escape(issue['title'])}</h1><p class="record-meta"><strong>{escape(issue['key'])}</strong>
<span>{label(issue['type'])}</span> <span>{label(issue['status'])}</span> {escape(issue['owner'])}</p>
<div class="record-description">{render_markdown(issue['description'])}</div>{resolution}
{issue_list('Child issues', issue['subtasks'])}''', prefix)
