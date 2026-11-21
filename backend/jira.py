"""Jira Cloud REST v3 for the portfolio's project and issue workflows."""

import json
import re
from urllib.parse import urlencode

from fastapi import Request, Response

from .store import DomainError, PRIORITIES, STATUSES

BASE = "/rest/api/3"
JIRA_TYPES = ("epic", "task", "bug", "research", "sub-task")
STATUSES_NAME = {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                 "blocked": "Blocked", "done": "Done"}


def jira_document(text):
    paragraphs = []
    for paragraph in text.split("\n\n") if text else []:
        content = []
        for index, line in enumerate(paragraph.splitlines()):
            if index:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        paragraphs.append({"type": "paragraph", "content": content})
    return {"version": 1, "type": "doc", "content": paragraphs}


def document_text(value):
    if value is None:
        return ""
    if not isinstance(value, dict) or value.get("type") != "doc" or value.get("version") != 1:
        raise DomainError("Use an Atlassian Document Format v1 document.")
    paragraphs = []
    for block in value.get("content", []):
        if block.get("type") != "paragraph":
            raise DomainError("Only paragraphs and hard breaks are supported in this document.")
        text = []
        for node in block.get("content", []):
            if node.get("type") == "hardBreak":
                text.append("\n")
            elif node.get("type") == "text" and isinstance(node.get("text"), str) and not node.get("marks"):
                text.append(node["text"])
            else:
                raise DomainError("Only plain text and hard breaks are supported in this document.")
        paragraphs.append("".join(text))
    return "\n\n".join(paragraphs)


def named_id(values, name):
    return str(values.index(name) + 1)


def named_value(values, field, label):
    if isinstance(field, dict):
        if "id" in field:
            value = str(field["id"])
            if value in {named_id(values, name) for name in values}:
                return values[int(value) - 1]
        name = field.get("name")
        if isinstance(name, str) and name.lower() in values:
            return name.lower()
    raise DomainError(f"Invalid {label}.")


def identifier(field, label, *keys):
    if isinstance(field, dict):
        for key in keys:
            value = field.get(key)
            if isinstance(value, str) and value:
                return value
    raise DomainError(f"Invalid {label}.")


def status_record(status):
    category = ("done", "Done") if status == "done" else (
        ("indeterminate", "In Progress") if status in {"in_progress", "blocked"} else ("new", "To Do")
    )
    return {"id": named_id(STATUSES, status), "name": STATUSES_NAME[status],
            "statusCategory": {"key": category[0], "name": category[1]}}


def author(name):
    if not name:
        return None
    return {"accountId": name, "displayName": name, "active": True, "accountType": "atlassian"}


def clause_match(jql):
    if not isinstance(jql, str) or len(jql) > 1000:
        raise DomainError("Invalid JQL.")
    jql = re.sub(r"\s+ORDER\s+BY\s+updated\s+DESC\s*$", "", jql.strip(), flags=re.I)
    pattern = re.compile(r'(project|status|text|summary|key)\s*(=|~)\s*("(?:[^"\\]|\\.)*"|[\w-]+)', re.I)
    query = {}
    while jql:
        match = pattern.match(jql)
        if not match:
            raise DomainError("Unsupported JQL. Use project, status, text, summary or key with AND.")
        field, operator, value = match.groups()
        field = field.lower()
        try:
            value = json.loads(value) if value.startswith('"') else value
        except ValueError:
            raise DomainError("Invalid JQL string.") from None
        if (operator == "~") != (field in {"text", "summary"}):
            raise DomainError("Unsupported JQL operator.")
        name = {"key": "exact_key", "text": "q"}.get(field, field)
        if name in query:
            raise DomainError("Multiple conditions on this field are unsupported.")
        if field == "status":
            value = value.lower().replace(" ", "_")
            if value not in STATUSES:
                raise DomainError("Unknown status.")
        query[name] = value
        jql = jql[match.end():].strip()
        if jql:
            connector = re.match(r"AND\s+", jql, re.I)
            if not connector:
                raise DomainError("Only AND is supported in JQL.")
            jql = jql[connector.end():]
    return query


def register_jira(app, store, origin, prefix):
    root = origin + prefix + BASE

    async def payload(request):
        try:
            data = await request.json()
        except (ValueError, TypeError):
            raise DomainError("Send a JSON object.") from None
        if not isinstance(data, dict):
            raise DomainError("Send a JSON object.")
        return data

    def project_record(project):
        return {"self": f"{root}/project/{project['id']}", "id": project["id"], "key": project["key"],
                "name": project["name"], "projectTypeKey": "software",
                "description": project.get("description", "")}

    def issue_link(issue):
        return {"self": f"{root}/issue/{issue['id']}", "id": issue["id"], "key": issue["key"]}

    def comment_record(issue, comment):
        who = author(comment["author"])
        return {"id": comment["id"], "self": f"{root}/issue/{issue['id']}/comment/{comment['id']}",
                "author": who, "updateAuthor": who, "body": jira_document(comment["body"]),
                "created": comment["created_at"], "updated": comment["created_at"]}

    def issue_record(issue, selected=None):
        kind = "sub-task" if issue["parent_id"] and issue["type"] == "task" else issue["type"]
        fields = {
            "summary": issue["title"], "description": jira_document(issue["description"]) if issue["description"] else None,
            "status": status_record(issue["status"]),
            "priority": {"id": named_id(PRIORITIES, issue["priority"]), "name": issue["priority"].title()},
            "issuetype": {"id": named_id(JIRA_TYPES, kind), "name": kind.title(), "subtask": kind == "sub-task"},
            "project": project_record({"id": issue["project_id"], "key": issue["project_key"],
                                       "name": issue["project_name"]}),
            "assignee": author(issue["owner"]), "duedate": issue["due_date"], "labels": issue["labels"],
            "created": issue["created_at"], "updated": issue["updated_at"],
        }
        if issue["parent_id"] and (not selected or "parent" in selected):
            with store.Session() as db:
                fields["parent"] = issue_link(store.issue_dict(db, store.issue(db, issue["parent_id"])))
        if "subtasks" in issue:
            fields["subtasks"] = [issue_link(child) for child in issue["subtasks"]]
        if selected and "*all" not in selected and "*navigable" not in selected:
            fields = {name: value for name, value in fields.items() if name in selected}
        return {**issue_link(issue), "fields": fields}

    def issue_changes(fields, *, creating=False):
        mapping = {"summary": "title", "description": "description", "assignee": "owner",
                   "duedate": "due_date", "labels": "labels", "priority": "priority", "issuetype": "type"}
        allowed = set(mapping) | ({"project", "parent"} if creating else set())
        if not isinstance(fields, dict) or set(fields) - allowed:
            raise DomainError("Unsupported or missing issue fields.")
        changes = {mapping[k]: v for k, v in fields.items() if k in mapping}
        if "description" in changes:
            try:
                changes["description"] = document_text(changes["description"])
            except (AttributeError, TypeError):
                raise DomainError("Invalid Atlassian Document Format.") from None
        if "owner" in changes:
            changes["owner"] = (identifier(changes["owner"], "assignee", "accountId")
                                if changes["owner"] is not None else "")
        if "priority" in changes:
            changes["priority"] = named_value(PRIORITIES, changes["priority"], "priority")
        if "type" in changes:
            value = named_value(JIRA_TYPES, changes["type"], "issue type")
            if value == "sub-task":
                if not creating or "parent" not in fields:
                    raise DomainError("A sub-task requires a parent when created.")
                value = "task"
            changes["type"] = value
        return changes

    @app.get(BASE + "/project/search")
    def projects(startAt: int = 0, maxResults: int = 50, query: str = ""):
        if startAt < 0 or not 1 <= maxResults <= 100:
            raise DomainError("Invalid project pagination.")
        matches = [project_record(project) for project in store.list_projects()
                   if query.casefold() in (project["name"] + " " + project["key"]).casefold()]
        result = {"startAt": startAt, "maxResults": maxResults, "total": len(matches),
                  "isLast": startAt + maxResults >= len(matches), "values": matches[startAt:startAt + maxResults]}
        if not result["isLast"]:
            result["nextPage"] = root + "/project/search?" + urlencode(
                {"startAt": startAt + maxResults, "maxResults": maxResults, "query": query})
        return result

    @app.get(BASE + "/project/{projectIdOrKey}")
    def project(projectIdOrKey: str):
        return project_record(store.get_project(projectIdOrKey.upper() if len(projectIdOrKey) < 17 else projectIdOrKey))

    @app.get(BASE + "/issue/{issueIdOrKey}")
    def get_issue(issueIdOrKey: str, fields: str = ""):
        return issue_record(store.get_issue(issueIdOrKey), fields.split(",") if fields else None)

    @app.post(BASE + "/issue", status_code=201)
    async def create_issue(request: Request):
        data = await payload(request)
        fields = data.get("fields")
        if set(data) != {"fields"}:
            raise DomainError("Supply issue fields.")
        changes = issue_changes(fields, creating=True)
        project = identifier(fields.get("project"), "project", "id", "key")
        if "parent" in fields:
            changes["parent_id"] = identifier(fields["parent"], "parent", "id", "key")
        return issue_link(store.create_issue(project, changes))

    @app.put(BASE + "/issue/{issueIdOrKey}", status_code=204)
    async def edit_issue(issueIdOrKey: str, request: Request):
        data = await payload(request)
        if set(data) != {"fields"}:
            raise DomainError("Supply the fields to update.")
        existing = store.get_issue(issueIdOrKey)
        store.update_issue(issueIdOrKey, issue_changes(data["fields"]), existing["version"])
        return Response(status_code=204)

    @app.get(BASE + "/issue/{issueIdOrKey}/comment")
    def list_comments(issueIdOrKey: str, startAt: int = 0, maxResults: int = 50):
        if startAt < 0 or not 1 <= maxResults <= 100:
            raise DomainError("Invalid comment pagination.")
        issue = store.get_issue(issueIdOrKey)
        comments = issue["comments"]
        return {"startAt": startAt, "maxResults": maxResults, "total": len(comments),
                "comments": [comment_record(issue, item) for item in comments[startAt:startAt + maxResults]]}

    @app.post(BASE + "/issue/{issueIdOrKey}/comment", status_code=201)
    async def comment(issueIdOrKey: str, request: Request):
        data = await payload(request)
        if set(data) != {"body"}:
            raise DomainError("Supply the comment body in Atlassian Document Format.")
        issue = store.get_issue(issueIdOrKey)
        try:
            body = document_text(data["body"])
        except (AttributeError, TypeError):
            raise DomainError("Invalid Atlassian Document Format.") from None
        return comment_record(issue, store.add_comment(issueIdOrKey, body))

    @app.get(BASE + "/issue/{issueIdOrKey}/transitions")
    def transitions(issueIdOrKey: str):
        current = store.get_issue(issueIdOrKey)["status"]
        return {"transitions": [{"id": named_id(STATUSES, status), "name": STATUSES_NAME[status],
                                 "to": status_record(status), "hasScreen": False, "isAvailable": True}
                                for status in STATUSES if status != current]}

    @app.post(BASE + "/issue/{issueIdOrKey}/transitions", status_code=204)
    async def transition(issueIdOrKey: str, request: Request):
        data = await payload(request)
        if set(data) != {"transition"}:
            raise DomainError("Supply the transition ID.")
        current = store.get_issue(issueIdOrKey)
        status = named_value(STATUSES, data["transition"], "transition")
        store.update_issue(issueIdOrKey, {"status": status}, current["version"])
        return Response(status_code=204)

    @app.post(BASE + "/issueLink", status_code=201)
    async def link_issue(request: Request):
        data = await payload(request)
        kind = identifier(data.get("type"), "link type", "name")
        if set(data) != {"type", "inwardIssue", "outwardIssue"} or kind not in {"Blocks", "Relates"}:
            raise DomainError("Only Blocks and Relates issue links are supported.")
        inward = identifier(data["inwardIssue"], "inward issue", "id", "key")
        outward = identifier(data["outwardIssue"], "outward issue", "id", "key")
        store.link_issues(inward, outward, "blocks" if kind == "Blocks" else "related")
        return Response(status_code=201)

    def search(jql, size, token, fields):
        if (type(size) is not int or not 1 <= size <= 100 or not isinstance(token, str)
                or not re.fullmatch(r"[0-9]{1,9}", token)):
            raise DomainError("Invalid search pagination.")
        if fields is not None and (not isinstance(fields, list) or any(not isinstance(f, str) for f in fields)):
            raise DomainError("Invalid field selection.")
        offset = int(token)
        with_query = clause_match(jql)
        found = store.list_issues(**with_query, limit=size, offset=offset)
        final = offset + size >= found["total"]
        result = {"issues": [issue_record(issue, fields) for issue in found["items"]], "isLast": final}
        if not final:
            result["nextPageToken"] = str(offset + size)
        return result

    @app.get(BASE + "/search/jql")
    def search_issues(jql: str = "", maxResults: int = 50, nextPageToken: str = "0", fields: str = ""):
        return search(jql, maxResults, nextPageToken, fields.split(",") if fields else None)

    @app.post(BASE + "/search/jql")
    async def search_with_body(request: Request):
        data = await payload(request)
        if set(data) - {"jql", "maxResults", "nextPageToken", "fields"}:
            raise DomainError("Unsupported search request fields.")
        return search(data.get("jql", ""), data.get("maxResults", 50), data.get("nextPageToken", "0"),
                      data.get("fields"))
