// Portfolio shell + Nira issue conventions; TaskForm/TaskRow adapted from Evan's Dive frontend.
import * as React from "react";
import { useEffect, useState } from "react";
import * as ReactDOM from "react-dom/client";
import * as elements from "react/jsx-runtime";

const basePath =
  document.querySelector('meta[name="nira-base"]')?.content || "";

const BASE = basePath + "/api",
  STATES = ["backlog", "ready", "in_progress", "blocked", "done"],
  PRIORITIES = ["critical", "high", "medium", "low"],
  PSTATES = ["active", "paused", "idea", "completed", "needs_review"];
const label = (s) =>
  ({
    in_progress: "In progress",
    needs_review: "Needs review",
    backlog: "Backlog",
    ready: "Ready",
    blocked: "Blocked",
    done: "Done",
    active: "Active",
    paused: "Paused",
    idea: "Idea",
    completed: "Completed",
  })[s] || s;
let csrf = "";
async function api(path, options = {}) {
  const response = await fetch(BASE + path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const e = Error(data.error || "Request failed");
    e.status = response.status;
    throw e;
  }
  return data;
}
const send = (method, data) => ({ method, body: JSON.stringify(data) });
function Icon({ name, size = 19 }) {
  const d =
    {
      grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
      list: "M8 5h13 M8 12h13 M8 19h13 M3 5h.01 M3 12h.01 M3 19h.01",
      board: "M3 4h5v16H3z M10 4h5v11h-5z M17 4h5v14h-5z",
      search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
      plus: "M12 5v14 M5 12h14",
      chevron: "M9 5l7 7-7 7",
      close: "M6 6l12 12 M18 6L6 18",
      arrow: "M7 17L17 7 M7 7h10v10",
      check: "M5 12l4 4L19 6",
      settings:
        "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
      logout: "M9 4H4v16h5 M10 12h11 M17 8l4 4-4 4",
      download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
    }[name] || "M12 3v18 M3 12h18";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}
function Pill({ status }) {
  return (
    <span className={"pill " + status}>
      <i />
      {label(status)}
    </span>
  );
}
function Empty({
  title = "Nothing here yet",
  body = "Add an issue to make the next step clear.",
}) {
  return (
    <div className="empty">
      <Icon name="list" size={32} />
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}
function Modal({ title, children, close, wide = false }) {
  return (
    <div
      className="overlay"
      onClick={(e) => e.target === e.currentTarget && close()}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={"modal " + (wide ? "wide" : "")}
      >
        <header>
          <h2>{title}</h2>
          <button className="iconbtn" aria-label="Close dialog" onClick={close}>
            <Icon name="close" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function Login({ onLogin }) {
  const [password, setPassword] = useState(""),
    [username, setUsername] = useState("evan"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function login(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api("/login", send("POST", { username, password }));
      csrf = r.csrf;
      onLogin(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-art">
        <div className="brand large">
          <b>n</b>
          <span>
            nira<span className="brand-suffix">HQ</span>
          </span>
        </div>
        <h1>
          All the work.
          <br />
          One clear picture.
        </h1>
        <p>
          Your projects, next actions, and decisions.
          <br />
          Nira's issue tracking. Dive's smaller steps.
        </p>
        <div className="login-stack">
          <div>
            <i className="dot violet" />
            Projects, connected <span>01</span>
          </div>
          <div>
            <i className="dot green" />
            Progress, visible <span>02</span>
          </div>
          <div>
            <i className="dot amber" />
            Next steps, clear <span>03</span>
          </div>
        </div>
        <small>YOUR PRIVATE WORKSPACE</small>
      </div>
      <form onSubmit={login} className="login-form">
        <span className="eyebrow">WELCOME BACK</span>
        <h2>Let's bring it into view.</h2>
        <p>Sign in to your private project workspace.</p>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Open workspace"} <Icon name="arrow" />
        </button>
        <a className="record-passkey-link" href={basePath + "/"}>
          Back to sign-in options
        </a>
      </form>
    </main>
  );
}
function TaskForm({ project, parentId, onCreated, compact = false }) {
  const [title, setTitle] = useState(""),
    [description, setDescription] = useState(""),
    [priority, setPriority] = useState("medium"),
    [type, setType] = useState("task"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api(
        `/projects/${project}/issues`,
        send("POST", {
          title,
          description,
          priority,
          type,
          parent_id: parentId || null,
        }),
      );
      setTitle("");
      setDescription("");
      await onCreated(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="stack" onSubmit={save}>
      <label>
        {compact ? "A smaller next step" : "Issue title"}
        <input
          autoFocus={!compact}
          required
          maxLength={240}
          placeholder={
            compact
              ? "What is the next concrete action?"
              : "What needs to happen?"
          }
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      {!compact && (
        <>
          <label>
            Description
            <textarea
              rows="6"
              placeholder="Context, constraints, and definition of done. Markdown supported."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <div className="formrow">
            <label>
              Type
              <select value={type} onChange={(e) => setType(e.target.value)}>
                {["task", "bug", "epic", "research"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
            <label>
              Priority
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              >
                {PRIORITIES.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
          </div>
        </>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button className="primary" disabled={busy || !title.trim()}>
        {busy ? "Adding…" : compact ? "Add subtask" : "Create issue"}
      </button>
    </form>
  );
}
function ProjectForm({ onCreated, workspaceKey }) {
  const [d, setD] = useState({
      name: "",
      key: "",
      description: "",
      category: "Product",
    }),
    [error, setError] = useState("");
  async function save(e) {
    e.preventDefault();
    try {
      await onCreated(
        await api(
          "/projects",
          send("POST", { ...d, workspace_key: workspaceKey }),
        ),
      );
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <form className="stack" onSubmit={save}>
      <label>
        Project name
        <input
          autoFocus
          required
          maxLength={160}
          value={d.name}
          onChange={(e) => setD({ ...d, name: e.target.value })}
        />
      </label>
      <div className="formrow">
        <label>
          Project key
          <input
            required
            pattern="[A-Z][A-Z0-9_]{1,15}"
            placeholder="NIRA"
            value={d.key}
            onChange={(e) => setD({ ...d, key: e.target.value.toUpperCase() })}
          />
        </label>
        <label>
          Category
          <select
            value={d.category}
            onChange={(e) => setD({ ...d, category: e.target.value })}
          >
            {["Product", "Research", "Studio", "Personal", "Operations"].map(
              (x) => (
                <option key={x}>{x}</option>
              ),
            )}
          </select>
        </label>
      </div>
      <label>
        Description
        <textarea
          rows="5"
          value={d.description}
          onChange={(e) => setD({ ...d, description: e.target.value })}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="primary">Create project</button>
    </form>
  );
}
function TaskRow({ task, onSelect, onToggle }) {
  return (
    <div className="subtask-row">
      <input
        aria-label={`Mark ${task.title} done`}
        type="checkbox"
        checked={task.status === "done"}
        onChange={(e) => onToggle(task, e.target.checked)}
      />
      <button onClick={() => onSelect(task.id)}>
        <code>{task.key}</code>
        <span className={task.status === "done" ? "strike" : ""}>
          {task.title}
        </span>
      </button>
      <Pill status={task.status} />
    </div>
  );
}
function IssueDetail({ id, close, onChange, onSelect, inline = false }) {
  const [t, setT] = useState(null),
    [error, setError] = useState(""),
    [comment, setComment] = useState(""),
    [editing, setEditing] = useState(false),
    [draft, setDraft] = useState({}),
    [tab, setTab] = useState("details"),
    [linkTarget, setLinkTarget] = useState(""),
    [linkKind, setLinkKind] = useState("related");
  async function load() {
    try {
      const r = await api("/issues/" + id);
      setT(r);
      setDraft(r);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }
  useEffect(() => {
    load();
    setEditing(false);
    setTab("details");
  }, [id]);
  async function patch(changes, issue = t) {
    try {
      await api(
        "/issues/" + issue.id,
        send("PATCH", { ...changes, expected_version: issue.version }),
      );
      await load();
      await onChange();
    } catch (e) {
      setError(e.message);
    }
  }
  const Container = inline ? "article" : "aside";
  return (
    <div
      className={inline ? "issue-page-wrap" : "drawer-wrap"}
      onClick={
        inline ? undefined : (e) => e.target === e.currentTarget && close()
      }
    >
      <Container
        className={inline ? "drawer issue-page" : "drawer"}
        role={inline ? undefined : "dialog"}
        aria-modal={inline ? undefined : "true"}
        aria-label="Issue details"
      >
        <header className="drawer-top">
          <span>
            <Icon name="list" />
            {t?.key || "Issue"}
          </span>
          <button className="iconbtn" aria-label="Close issue" onClick={close}>
            <Icon name="close" />
          </button>
        </header>
        {error && (
          <p className="error notice" role="alert">
            {error} <button onClick={load}>Reload</button>
          </p>
        )}
        {t && (
          <div className="drawer-body">
            <small className="muted">{t.project_name}</small>
            {editing ? (
              <input
                aria-label="Issue title"
                className="title-input"
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            ) : (
              <h1>{t.title}</h1>
            )}
            <div className="issue-controls">
              <select
                aria-label="Issue status"
                value={t.status}
                onChange={(e) => patch({ status: e.target.value })}
              >
                {STATES.map((s) => (
                  <option key={s} value={s}>
                    {label(s)}
                  </option>
                ))}
              </select>
              <select
                aria-label="Issue priority"
                value={t.priority}
                onChange={(e) => patch({ priority: e.target.value })}
              >
                {PRIORITIES.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
              <span className="issue-type">{t.type}</span>
              <button className="linkbtn" onClick={() => setEditing(!editing)}>
                {editing ? "Cancel editing" : "Edit issue"}
              </button>
            </div>
            <div className="issue-meta">
              <label>
                Owner
                <input
                  aria-label="Issue owner"
                  key={t.owner}
                  defaultValue={t.owner}
                  onBlur={(e) =>
                    e.target.value !== t.owner &&
                    patch({ owner: e.target.value })
                  }
                />
              </label>
              <label>
                Due date
                <input
                  aria-label="Issue due date"
                  type="date"
                  value={t.due_date || ""}
                  onChange={(e) => patch({ due_date: e.target.value || null })}
                />
              </label>
            </div>
            <nav className="tabs">
              <button
                className={tab === "details" ? "selected" : ""}
                onClick={() => setTab("details")}
              >
                Details
              </button>
              <button
                className={tab === "activity" ? "selected" : ""}
                onClick={() => setTab("activity")}
              >
                Activity <span>{t.activity.length}</span>
              </button>
            </nav>
            {tab === "details" ? (
              <>
                <section className="detail-section">
                  <div className="section-title">
                    <h3>Description</h3>
                    <small>Markdown</small>
                  </div>
                  {editing ? (
                    <>
                      <textarea
                        aria-label="Issue description"
                        rows="12"
                        value={draft.description}
                        onChange={(e) =>
                          setDraft({ ...draft, description: e.target.value })
                        }
                      />
                      <label>
                        Labels
                        <input
                          value={(draft.labels || []).join(", ")}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              labels: e.target.value
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            })
                          }
                        />
                      </label>
                      <button
                        className="primary"
                        onClick={async () => {
                          await patch({
                            title: draft.title,
                            description: draft.description,
                            labels: draft.labels,
                          });
                          setEditing(false);
                        }}
                      >
                        Save changes
                      </button>
                    </>
                  ) : t.description ? (
                    <div
                      className="markdown"
                      dangerouslySetInnerHTML={{ __html: t.description_html }}
                    />
                  ) : (
                    <p className="muted">No description yet.</p>
                  )}
                  <div className="labels">
                    {t.labels.map((l) => (
                      <span key={l}>{l}</span>
                    ))}
                  </div>
                </section>
                {t.resolution && (
                  <section className="detail-section">
                    <h3>Resolution</h3>
                    <p className="prewrap">{t.resolution}</p>
                  </section>
                )}
                <section className="detail-section">
                  <div className="section-title">
                    <h3>Smaller steps</h3>
                    <span>
                      {t.subtasks.filter((s) => s.status === "done").length}/
                      {t.subtasks.length}
                    </span>
                  </div>
                  {t.subtasks.map((s) => (
                    <TaskRow
                      key={s.id}
                      task={s}
                      onSelect={onSelect}
                      onToggle={(s, done) =>
                        patch({ status: done ? "done" : "ready" }, s)
                      }
                    />
                  ))}
                  <TaskForm
                    project={t.project_id}
                    parentId={t.id}
                    compact
                    onCreated={async () => {
                      await load();
                      await onChange();
                    }}
                  />
                </section>
                <section className="detail-section">
                  <h3>Related work</h3>
                  {(t.links || []).map((l) => (
                    <div className="subtask-row" key={l.id}>
                      <span className="muted">
                        {l.kind === "related"
                          ? "Related"
                          : l.issue_a === t.id
                            ? "Blocks"
                            : "Blocked by"}
                      </span>
                      <button onClick={() => onSelect(l.other.id)}>
                        <code>{l.other.key}</code>
                        <span>{l.other.title}</span>
                      </button>
                    </div>
                  ))}
                  <form
                    className="link-form"
                    onSubmit={async (e) => {
                      e.preventDefault();
                      try {
                        await api(
                          "/issues/" + id + "/links",
                          send("POST", { target: linkTarget, kind: linkKind }),
                        );
                        setLinkTarget("");
                        await load();
                      } catch (e) {
                        setError(e.message);
                      }
                    }}
                  >
                    <input
                      aria-label="Related issue key"
                      placeholder="Issue key, e.g. EMH-1"
                      value={linkTarget}
                      onChange={(e) => setLinkTarget(e.target.value)}
                    />
                    <select
                      aria-label="Issue relationship"
                      value={linkKind}
                      onChange={(e) => setLinkKind(e.target.value)}
                    >
                      <option value="related">Related to</option>
                      <option value="blocks">Blocks</option>
                    </select>
                    <button className="secondary" disabled={!linkTarget.trim()}>
                      Link
                    </button>
                  </form>
                </section>
                {t.parent_id && (
                  <button
                    className="linkbtn"
                    onClick={() => onSelect(t.parent_id)}
                  >
                    ↑ Open parent issue
                  </button>
                )}
                <section className="detail-section">
                  <h3>Source & provenance</h3>
                  <p className="prewrap source">
                    {t.source || "Created in Nira HQ."}
                  </p>
                  <small className="muted">
                    Created {new Date(t.created_at).toLocaleDateString()} ·
                    Version {t.version}
                  </small>
                </section>
                <section className="detail-section">
                  <h3>Conversation</h3>
                  {t.comments.map((c) => (
                    <article className="comment" key={c.id}>
                      <strong>{c.author}</strong>
                      <small>{new Date(c.created_at).toLocaleString()}</small>
                      <p>{c.body}</p>
                    </article>
                  ))}
                  <form
                    className="stack"
                    onSubmit={async (e) => {
                      e.preventDefault();
                      try {
                        await api(
                          "/issues/" + id + "/comments",
                          send("POST", { body: comment }),
                        );
                        setComment("");
                        await load();
                      } catch (e) {
                        setError(e.message);
                      }
                    }}
                  >
                    <textarea
                      aria-label="Add a comment"
                      placeholder="Add a progress note, decision, or blocker…"
                      rows="3"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                    />
                    <button className="secondary" disabled={!comment.trim()}>
                      Add comment
                    </button>
                  </form>
                </section>
              </>
            ) : (
              <div className="timeline">
                {t.activity.map((a) => (
                  <article key={a.id}>
                    <i />
                    <div>
                      <strong>{a.actor}</strong> {a.action.replaceAll("_", " ")}
                      <small>{new Date(a.created_at).toLocaleString()}</small>
                      {a.detail && <pre>{a.detail}</pre>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        )}
      </Container>
    </div>
  );
}
function Settings({ close }) {
  const [tokens, setTokens] = useState([]),
    [name, setName] = useState(""),
    [scope, setScope] = useState("read"),
    [secret, setSecret] = useState(""),
    [error, setError] = useState("");
  const load = () =>
    api("/tokens")
      .then(setTokens)
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);
  return (
    <Modal title="Workspace & MCP access" close={close} wide>
      <div className="stack">
        <p className="muted">
          Connect your agents to this same workspace. Tokens can be revoked
          independently; read-only is the default.
        </p>
        <label>
          MCP endpoint
          <input readOnly value={location.origin + basePath + "/mcp/"} />
        </label>
        <code className="codeblock">Authorization: Bearer YOUR_TOKEN</code>
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const t = await api("/tokens", send("POST", { name, scope }));
              setSecret(t.token);
              setName("");
              await load();
            } catch (e) {
              setError(e.message);
            }
          }}
        >
          <div className="formrow">
            <label>
              Token name
              <input
                required
                placeholder="My MCP client"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              Access
              <select value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="read">Read only</option>
                <option value="write">Read and write</option>
              </select>
            </label>
          </div>
          <button className="primary">Generate token</button>
        </form>
        {secret && (
          <div className="secret">
            <strong>Copy now. This token is shown only once.</strong>
            <textarea aria-label="New MCP token" readOnly value={secret} />
            <button
              className="secondary"
              onClick={() => navigator.clipboard.writeText(secret)}
            >
              Copy token
            </button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div className="token-list">
          {tokens.map((t) => (
            <div key={t.id}>
              <span>
                <strong>{t.name}</strong>
                <small>
                  {t.scope === "write" ? "Read & write" : "Read only"} ·{" "}
                  {t.revoked ? "Revoked" : "Active"}
                </small>
              </span>
              {!t.revoked && (
                <button
                  className="linkbtn danger"
                  onClick={async () => {
                    try {
                      await api("/tokens/" + t.id, { method: "DELETE" });
                      await load();
                    } catch (e) {
                      setError(e.message);
                    }
                  }}
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
        <button
          className="secondary"
          onClick={async () => {
            const data = await api("/export");
            const a = document.createElement("a");
            a.href = URL.createObjectURL(
              new Blob([JSON.stringify(data, null, 2)], {
                type: "application/json",
              }),
            );
            a.download =
              "nira-backup-" + new Date().toISOString().slice(0, 10) + ".json";
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
          }}
        >
          <Icon name="download" /> Export all projects and issues
        </button>
        <small className="muted">
          Nira HQ · single-owner workspace · MariaDB on your VPS
        </small>
      </div>
    </Modal>
  );
}

function setCsrf(value) {
  csrf = value || "";
}

export {
  React,
  ReactDOM,
  elements,
  api as request,
  send as jsonRequest,
  Icon,
  Pill as StatusPill,
  label as statusName,
  PSTATES as projectStatuses,
  STATES as issueStatuses,
  Empty,
  Login,
  ProjectForm,
  TaskForm as IssueForm,
  Modal,
  IssueDetail as IssueDetails,
  Settings,
  setCsrf,
};
