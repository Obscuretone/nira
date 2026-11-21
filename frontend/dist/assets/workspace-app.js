import {
  React,
  elements,
  _ as ReactDOM,
  request,
  jsonRequest,
  Icon,
  StatusPill,
  statusName,
  projectStatuses,
  issueStatuses,
  Empty,
  Login,
  ProjectForm,
  IssueForm,
  Modal,
  IssueDetails,
  Settings,
  setCsrf,
} from "./index-4f857a4591a2.js";
const basePath =
  document.querySelector('meta[name="nira-base"]')?.content ?? "";
const personalWorkspace = "personal";
const currentLocation = () => location.pathname + location.search;

function WorkspaceForm({ onCreated }) {
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const workspace = await request(
        "/workspaces",
        jsonRequest("POST", { name }),
      );
      await onCreated(workspace);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  return elements.jsxs("form", {
    className: "stack",
    onSubmit: submit,
    children: [
      elements.jsxs("label", {
        children: [
          "Workspace name",
          elements.jsx("input", {
            autoFocus: true,
            value: name,
            onChange: (event) => setName(event.target.value),
            required: true,
            maxLength: 80,
          }),
        ],
      }),
      error &&
        elements.jsx("p", {
          className: "error",
          role: "alert",
          children: error,
        }),
      elements.jsx("button", {
        className: "primary",
        disabled: saving || !name.trim(),
        children: "Create workspace",
      }),
    ],
  });
}

function WorkspaceApp() {
  const [session, setSession] = React.useState(null);
  const [booting, setBooting] = React.useState(true);
  const [workspaces, setWorkspaces] = React.useState([]);
  const [workspaceKey, setWorkspaceKey] = React.useState(personalWorkspace);
  const [route, setRoute] = React.useState(currentLocation);
  const [resolvedRoute, setResolvedRoute] = React.useState(null);
  const [projects, setProjects] = React.useState([]);
  const [issues, setIssues] = React.useState([]);
  const [issueCount, setIssueCount] = React.useState(0);
  const [loadedIssueScope, setLoadedIssueScope] = React.useState(null);
  const [projectId, setProjectId] = React.useState(null);
  const [view, setView] = React.useState("portfolio");
  const [search, setSearch] = React.useState("");
  const [issueStatus, setIssueStatus] = React.useState("all");
  const [projectStatus, setProjectStatus] = React.useState("all");
  const [modal, setModal] = React.useState(null);
  const [createProjectId, setCreateProjectId] = React.useState(null);
  const [issueId, setIssueId] = React.useState(null);
  const [copyResult, setCopyResult] = React.useState(null);
  const [error, setError] = React.useState("");
  const [loadingIssues, setLoadingIssues] = React.useState(false);
  const [movingProject, setMovingProject] = React.useState(false);
  const projectNavigation = React.useRef(null);
  const routeLoading = route !== resolvedRoute;
  const issueScope = JSON.stringify([
    workspaceKey,
    projectId,
    issueStatus,
    search,
  ]);
  const visibleIssues = loadedIssueScope === issueScope ? issues : [];
  const visibleIssueCount = loadedIssueScope === issueScope ? issueCount : 0;

  function changeLocation(next) {
    if (next !== currentLocation()) history.pushState(null, "", next);
    setRoute(next);
  }

  function navigationPath(
    nextProject = null,
    nextView = "list",
    workspace = workspaceKey,
    query = "",
  ) {
    const params = new URLSearchParams();
    if (!nextProject && workspace !== personalWorkspace)
      params.set("workspace", workspace);
    if (nextView !== (nextProject ? "list" : "portfolio"))
      params.set("view", nextView);
    if (query) params.set("q", query);
    const path = nextProject
      ? `/browse/project/${encodeURIComponent(nextProject)}`
      : "/";
    return basePath + path + (params.size ? `?${params}` : "");
  }

  function navigate(...destination) {
    changeLocation(navigationPath(...destination));
  }

  function followBreadcrumb(event) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    changeLocation(event.currentTarget.getAttribute("href"));
  }

  function openIssue(ref) {
    changeLocation(`${basePath}/browse/issue/${encodeURIComponent(ref)}`);
  }

  function closeIssue() {
    navigate(projectId);
  }

  function startIssue() {
    setCreateProjectId(projectId);
    setModal("issue");
  }

  async function copyIssueLink() {
    try {
      await navigator.clipboard.writeText(location.origin + route.split("?")[0]);
      setCopyResult({ route, message: "Link copied" });
    } catch {
      setCopyResult({ route, message: "Copy the URL from the address bar" });
    }
  }

  async function loadWorkspaces(signal) {
    setWorkspaces(await request("/workspaces", { signal }));
  }

  async function loadProjects(signal) {
    const query = new URLSearchParams({ workspace: workspaceKey });
    const records = await request(`/projects?${query}`, { signal });
    setProjects(records);
    return records;
  }

  async function loadIssues(signal) {
    setLoadingIssues(true);
    try {
      const query = new URLSearchParams({
        workspace: workspaceKey,
        limit: "500",
      });
      if (projectId) query.set("project", projectId);
      if (issueStatus !== "all") query.set("status", issueStatus);
      if (search) query.set("q", search);
      const result = await request(`/issues?${query}`, { signal });
      setIssues(result.items);
      setIssueCount(result.total);
      setLoadedIssueScope(issueScope);
    } catch (failure) {
      if (!signal?.aborted) setError(failure.message);
    } finally {
      if (!signal?.aborted) setLoadingIssues(false);
    }
  }

  async function refresh() {
    setError("");
    if (!workspaces.length) {
      await loadWorkspaces().catch((failure) => setError(failure.message));
      return;
    }
    await Promise.all([
      loadProjects().catch((failure) => setError(failure.message)),
      loadIssues(),
    ]);
  }

  React.useEffect(() => {
    request("/me")
      .then((owner) => {
        setCsrf(owner.csrf);
        setSession(owner);
      })
      .catch(() => {})
      .finally(() => setBooting(false));
  }, []);

  React.useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    loadWorkspaces(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [session]);

  React.useEffect(() => {
    const restoreLocation = () => setRoute(currentLocation());
    window.addEventListener("popstate", restoreLocation);
    return () => window.removeEventListener("popstate", restoreLocation);
  }, []);

  React.useEffect(() => {
    if (!session || !workspaces.length) return;
    const controller = new AbortController();
    async function resolveRoute() {
      const link = new URL(route, location.origin);
      const match = /^\/browse\/(project|issue)\/([^/]+)$/.exec(
        link.pathname.slice(basePath.length),
      );
      const workspaceParam = link.searchParams.get("workspace");
      const workspace =
        workspaces.find((item) => item.key === workspaceParam)?.key ??
        personalWorkspace;
      const viewParam = link.searchParams.get("view");
      const nextView =
        viewParam === "list" || viewParam === "board"
          ? viewParam
          : match
            ? "list"
            : "portfolio";
      setError("");
      try {
        const record =
          match &&
          (await request(`/${match[1]}s/${match[2]}`, {
            signal: controller.signal,
          }));
        if (controller.signal.aborted) return;
        setWorkspaceKey(record?.workspace_key ?? workspace);
        setProjectId(record ? (record.project_id ?? record.id) : null);
        setIssueId(match?.[1] === "issue" ? record.id : null);
        setView(nextView);
        setSearch(match?.[1] === "issue" ? "" : link.searchParams.get("q") ?? "");
        setIssueStatus("all");
      } catch (failure) {
        if (controller.signal.aborted) return;
        setWorkspaceKey(workspace);
        setProjectId(null);
        setIssueId(null);
        setView("portfolio");
        setError(failure.message);
      } finally {
        if (!controller.signal.aborted) setResolvedRoute(route);
      }
    }
    resolveRoute();
    return () => controller.abort();
  }, [session, route, workspaces]);

  React.useEffect(() => {
    if (!session || routeLoading) return;
    const controller = new AbortController();
    loadProjects(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [session, workspaceKey, routeLoading]);

  React.useEffect(() => {
    const navigation = projectNavigation.current;
    const current = navigation?.querySelector('[aria-current="page"]');
    if (!current) return;
    const frame = navigation.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    if (item.top < frame.top) navigation.scrollTop += item.top - frame.top;
    else if (item.bottom > frame.bottom) navigation.scrollTop += item.bottom - frame.bottom;
  }, [projectId, projects, routeLoading]);

  React.useEffect(() => {
    if (!session || routeLoading || issueId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => loadIssues(controller.signal), 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [
    session,
    workspaceKey,
    projectId,
    search,
    issueStatus,
    routeLoading,
    issueId,
  ]);

  React.useEffect(() => {
    function keyboard(event) {
      if (event.key === "Escape") {
        if (modal) setModal(null);
        else if (issueId) closeIssue();
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        document.getElementById("search")?.focus();
      }
    }
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, [modal, issueId, projectId, workspaceKey]);

  const workspace = workspaces.find((item) => item.key === workspaceKey);
  const visibleProjects = projects.filter(
    (item) => item.workspace_key === workspaceKey,
  );
  const project = visibleProjects.find((item) => item.id === projectId);
  const filteredProjects = visibleProjects.filter(
    (item) =>
      (projectStatus === "all" || item.status === projectStatus) &&
      (!search ||
        [item.name, item.key, item.description]
          .join(" ")
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
  const counts = {};
  for (const item of visibleProjects) {
    for (const [status, count] of Object.entries(item.counts ?? {})) {
      counts[status] = (counts[status] ?? 0) + count;
    }
  }

  async function changeProjectStatus(status) {
    try {
      await request(
        `/projects/${project.id}`,
        jsonRequest("PATCH", {
          status,
          expected_version: project.version,
        }),
      );
      await loadProjects();
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function moveProject(nextWorkspace) {
    setMovingProject(true);
    try {
      const moved = await request(
        `/projects/${project.id}`,
        jsonRequest("PATCH", {
          workspace_key: nextWorkspace,
          expected_version: project.version,
        }),
      );
      setWorkspaceKey(moved.workspace_key);
      setError("");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setMovingProject(false);
    }
  }
  return booting
    ? elements.jsx(`div`, {
        className: `boot`,
        children: `Opening your workspace…`,
      })
    : session
      ? elements.jsxs(`div`, {
          className: `app`,
          children: [
            elements.jsxs(`aside`, {
              className: `sidebar`,
              "aria-label": "Workspace sidebar",
              children: [
                elements.jsxs(`button`, {
                  className: `brand`,
                  onClick: () => navigate(null, `portfolio`),
                  children: [
                    elements.jsx(`b`, {
                      children: `n`,
                    }),
                    elements.jsxs(`span`, {
                      children: [
                        `nira`,
                        elements.jsx(`span`, {
                          className: `brand-suffix`,
                          children: `HQ`,
                        }),
                      ],
                    }),
                  ],
                }),
                elements.jsxs("label", {
                  className: "workspace-switch workspace-picker",
                  children: [
                    elements.jsx("span", { children: "Workspace" }),
                    elements.jsxs("select", {
                      "aria-label": "Workspace",
                      value: workspaceKey,
                      onChange: (event) => {
                        const key = event.target.value;
                        if (key === "__new__") setModal("workspace");
                        else navigate(null, "portfolio", key);
                      },
                      children: [
                        workspaces.map((item) =>
                          elements.jsx(
                            "option",
                            { value: item.key, children: item.name },
                            item.key,
                          ),
                        ),
                        elements.jsx("option", {
                          value: "__new__",
                          children: "+ New workspace",
                        }),
                      ],
                    }),
                  ],
                }),
                elements.jsxs(`nav`, {
                  "aria-label": "Workspace navigation",
                  children: [
                    elements.jsxs(`button`, {
                      className: view === `portfolio` ? `active` : ``,
                      onClick: () => navigate(null, `portfolio`),
                      children: [
                        elements.jsx(Icon, {
                          name: `grid`,
                        }),
                        ` Overview `,
                        elements.jsx(`span`, {
                          children: visibleProjects.length,
                        }),
                      ],
                    }),
                    elements.jsxs(`button`, {
                      className: !projectId && view === `list` ? `active` : ``,
                      onClick: () => navigate(null, `list`),
                      children: [
                        elements.jsx(Icon, {
                          name: `list`,
                        }),
                        ` All issues `,
                        elements.jsx(`span`, {
                          children: Object.values(counts).reduce(
                            (e, t) => e + t,
                            0,
                          ),
                        }),
                      ],
                    }),
                    elements.jsxs(`button`, {
                      className: !projectId && view === `board` ? `active` : ``,
                      onClick: () => navigate(null, `board`),
                      children: [
                        elements.jsx(Icon, {
                          name: `board`,
                        }),
                        ` Board`,
                      ],
                    }),
                  ],
                }),
                elements.jsxs(`div`, {
                  className: `sidebar-label`,
                  children: [
                    `PROJECTS`,
                    elements.jsx(`button`, {
                      "aria-label": `Create project`,
                      className: `iconbtn`,
                      onClick: () => setModal(`project`),
                      children: elements.jsx(Icon, {
                        name: `plus`,
                        size: 15,
                      }),
                    }),
                  ],
                }),
                elements.jsx(`div`, {
                  className: `project-nav`,
                  ref: projectNavigation,
                  children: visibleProjects.map((e, t) =>
                    elements.jsxs(
                      `button`,
                      {
                        className: e.id === projectId ? `active` : ``,
                        onClick: () => navigate(e.id),
                        title: e.name,
                        "aria-label": e.name,
                        "aria-current": e.id === projectId ? "page" : undefined,
                        children: [
                          elements.jsx(`i`, {
                            className: `project-dot c` + (t % 6),
                          }),
                          elements.jsx(`span`, {
                            className: `project-full-name`,
                            children: e.name,
                          }),
                          elements.jsx(`span`, {
                            className: `project-shortcut`,
                            "aria-hidden": !0,
                            children: (e.key || e.name)
                              .slice(0, 3)
                              .toUpperCase(),
                          }),
                          e.counts?.blocked > 0 &&
                            elements.jsx(`small`, {
                              children: e.counts.blocked,
                            }),
                        ],
                      },
                      e.id,
                    ),
                  ),
                }),
                elements.jsxs(`div`, {
                  className: `sidebar-footer`,
                  children: [
                    elements.jsxs(`button`, {
                      onClick: () => setModal(`settings`),
                      children: [
                        elements.jsx(Icon, {
                          name: `settings`,
                        }),
                        ` Workspace & MCP`,
                      ],
                    }),
                    elements.jsxs(`button`, {
                      onClick: async () => {
                        (await request(`/logout`, {
                          method: `POST`,
                        }),
                          window.location.assign(basePath + "/"));
                      },
                      children: [
                        elements.jsx(Icon, {
                          name: `logout`,
                        }),
                        ` Sign out`,
                      ],
                    }),
                    elements.jsxs(`div`, {
                      className: `profile`,
                      children: [
                        elements.jsx(`span`, {
                          className: `avatar`,
                          children: `ED`,
                        }),
                        elements.jsxs(`div`, {
                          children: [
                            elements.jsx(`strong`, {
                              children: `Evan d'Entremont`,
                            }),
                            elements.jsx(`small`, {
                              children: `Workspace owner`,
                            }),
                          ],
                        }),
                      ],
                    }),
                  ],
                }),
              ],
            }),
            elements.jsxs(`main`, {
              className: `main`,
              children: [
                elements.jsxs(`header`, {
                  className: `topbar`,
                  children: [
                    elements.jsxs(`nav`, {
                      className: `breadcrumbs`,
                      "aria-label": "Current location",
                      children: [
                        elements.jsx("a", {
                          href: navigationPath(null, "portfolio"),
                          onClick: followBreadcrumb,
                          children: workspace?.name ?? "Workspace",
                        }),
                        elements.jsx(Icon, {
                          name: `chevron`,
                          size: 13,
                        }),
                        ` `,
                        project
                          ? elements.jsx("a", {
                              className: "breadcrumb-project",
                              href: navigationPath(projectId),
                              onClick: followBreadcrumb,
                              children: project.name,
                            })
                          : elements.jsx("strong", {
                              children: { portfolio: "Overview", board: "Board", list: "All issues" }[view],
                            }),
                        project &&
                          elements.jsx(`span`, {
                            className: `crumb-project`,
                            children: project.key,
                          }),
                      ],
                    }),
                    elements.jsxs("div", {
                      className: "topbar-actions",
                      children: [
                        elements.jsxs("form", {
                          className: "search",
                          role: "search",
                          onSubmit: (event) => {
                            event.preventDefault();
                            if (issueId && search.trim()) navigate(null, "list", workspaceKey, search.trim());
                          },
                          children: [
                            elements.jsx(Icon, { name: "search", size: 17 }),
                            elements.jsx("input", {
                              id: "search",
                              type: "search",
                              "aria-label": issueId ? "Search issues in this workspace" : "Search projects and issues",
                              placeholder: issueId ? "Search issues…" : "Search your work…",
                              value: search,
                              onChange: (event) => setSearch(event.target.value),
                            }),
                            elements.jsx("kbd", { children: "⌘ K" }),
                          ],
                        }),
                        elements.jsxs("button", {
                          className: "primary create-issue-button",
                          onClick: startIssue,
                          disabled: routeLoading || !visibleProjects.length,
                          children: [elements.jsx(Icon, { name: "plus" }), " Create issue"],
                        }),
                        issueId && elements.jsxs(elements.Fragment, {
                          children: [
                            elements.jsx("button", {
                              type: "button", className: "ghost", onClick: copyIssueLink,
                              children: "Copy link",
                            }),
                            copyResult?.route === route && elements.jsx("span", {
                              className: "copy-result", role: "status", children: copyResult.message,
                            }),
                          ],
                        }),
                      ],
                    }),
                  ],
                }),
                routeLoading && !error
                  ? elements.jsx("p", {
                      className: "page muted",
                      role: "status",
                      children: "Opening workspace…",
                    })
                  : issueId
                    ? elements.jsx(
                        IssueDetails,
                        {
                          id: issueId,
                          inline: true,
                          close: closeIssue,
                          onChange: refresh,
                          onSelect: openIssue,
                        },
                        issueId,
                      )
                    : elements.jsxs(`div`, {
                        className: `page`,
                        children: [
                          elements.jsxs(`div`, {
                            className: `page-heading`,
                            children: [
                              elements.jsxs(`div`, {
                                children: [
                                  elements.jsx(`span`, {
                                    className: `eyebrow`,
                                    children: project
                                      ? `PROJECT WORKSPACE`
                                      : view === `portfolio`
                                        ? `THE BIG PICTURE`
                                        : `YOUR WORKSPACE`,
                                  }),
                                  elements.jsx(`h1`, {
                                    children:
                                      project?.name ||
                                      {
                                        portfolio: `Everything, in view.`,
                                        board: `Move work forward.`,
                                        list: `All issues.`,
                                      }[view],
                                  }),
                                  elements.jsx(`p`, {
                                    children: project
                                      ? project.description.split(`
`)[0]
                                      : view === `portfolio`
                                        ? `A home for every project. A clear next step for each one.`
                                        : `One place for the work across all your projects.`,
                                  }),
                                ],
                              }),
                              view === `portfolio` && elements.jsx(`div`, {
                                className: `heading-actions`,
                                children: elements.jsxs(`button`, {
                                        className: `primary`,
                                        onClick: () => setModal(`project`),
                                        children: [
                                          elements.jsx(Icon, {
                                            name: `plus`,
                                          }),
                                          ` New project`,
                                        ],
                                      }),
                              }),
                            ],
                          }),
                          error &&
                            elements.jsxs(`div`, {
                              className: `notice error`,
                              role: `alert`,
                              children: [
                                error,
                                elements.jsx(`button`, {
                                  className: `linkbtn`,
                                  onClick: refresh,
                                  children: `Retry`,
                                }),
                              ],
                            }),
                          view === `portfolio`
                            ? elements.jsxs(elements.Fragment, {
                                children: [
                                  elements.jsxs(`div`, {
                                    className: `stats`,
                                    children: [
                                      elements.jsxs(`div`, {
                                        children: [
                                          elements.jsxs(`span`, {
                                            children: [
                                              `Active projects `,
                                              elements.jsx(Icon, {
                                                name: `arrow`,
                                                size: 16,
                                              }),
                                            ],
                                          }),
                                          elements.jsx(`strong`, {
                                            children: visibleProjects.filter(
                                              (e) => e.status === `active`,
                                            ).length,
                                          }),
                                          elements.jsx(`small`, {
                                            children: `Across your workspace`,
                                          }),
                                        ],
                                      }),
                                      elements.jsxs(`div`, {
                                        children: [
                                          elements.jsxs(`span`, {
                                            children: [
                                              `In progress `,
                                              elements.jsx(`i`, {
                                                className: `dot violet`,
                                              }),
                                            ],
                                          }),
                                          elements.jsx(`strong`, {
                                            children: counts.in_progress || 0,
                                          }),
                                          elements.jsx(`small`, {
                                            children: `Issues moving forward`,
                                          }),
                                        ],
                                      }),
                                      elements.jsxs(`div`, {
                                        children: [
                                          elements.jsxs(`span`, {
                                            children: [
                                              `Blocked `,
                                              elements.jsx(`i`, {
                                                className: `dot amber`,
                                              }),
                                            ],
                                          }),
                                          elements.jsx(`strong`, {
                                            children: counts.blocked || 0,
                                          }),
                                          elements.jsx(`small`, {
                                            children: `Waiting on a decision or dependency`,
                                          }),
                                        ],
                                      }),
                                      elements.jsxs(`div`, {
                                        children: [
                                          elements.jsxs(`span`, {
                                            children: [
                                              `Completed `,
                                              elements.jsx(Icon, {
                                                name: `check`,
                                                size: 16,
                                              }),
                                            ],
                                          }),
                                          elements.jsx(`strong`, {
                                            children: counts.done || 0,
                                          }),
                                          elements.jsx(`small`, {
                                            children: `Progress worth keeping`,
                                          }),
                                        ],
                                      }),
                                    ],
                                  }),
                                  elements.jsxs(`div`, {
                                    className: `portfolio-toolbar`,
                                    children: [
                                      elements.jsxs(`div`, {
                                        children: [
                                          elements.jsxs(`h2`, {
                                            children: [
                                              `Your projects `,
                                              elements.jsx(`span`, {
                                                children:
                                                  filteredProjects.length,
                                              }),
                                            ],
                                          }),
                                          elements.jsx(`p`, {
                                            children: `Product work, research, and everything in between.`,
                                          }),
                                        ],
                                      }),
                                      elements.jsxs(`select`, {
                                        "aria-label": `Filter project status`,
                                        value: projectStatus,
                                        onChange: (e) =>
                                          setProjectStatus(e.target.value),
                                        children: [
                                          elements.jsx(`option`, {
                                            value: `all`,
                                            children: `All statuses`,
                                          }),
                                          projectStatuses.map((e) =>
                                            elements.jsx(
                                              `option`,
                                              {
                                                value: e,
                                                children: statusName(e),
                                              },
                                              e,
                                            ),
                                          ),
                                        ],
                                      }),
                                    ],
                                  }),
                                  elements.jsxs(`div`, {
                                    className: `project-table`,
                                    children: [
                                      elements.jsxs(`div`, {
                                        className: `project-table-head`,
                                        children: [
                                          elements.jsx(`span`, {
                                            children: `PROJECT`,
                                          }),
                                          elements.jsx(`span`, {
                                            children: `STATUS`,
                                          }),
                                          elements.jsx(`span`, {
                                            children: `PROGRESS`,
                                          }),
                                          elements.jsx(`span`, {
                                            children: `NEXT ACTION`,
                                          }),
                                          elements.jsx(`span`, {}),
                                        ],
                                      }),
                                      filteredProjects.map((e, t) =>
                                        elements.jsxs(
                                          `button`,
                                          {
                                            className: `project-table-row`,
                                            onClick: () => navigate(e.id),
                                            children: [
                                              elements.jsxs(`div`, {
                                                className: `project-name`,
                                                children: [
                                                  elements.jsx(`span`, {
                                                    className:
                                                      `project-icon c` +
                                                      (t % 6),
                                                    children: e.key.slice(0, 2),
                                                  }),
                                                  elements.jsxs(`span`, {
                                                    children: [
                                                      elements.jsx(`strong`, {
                                                        children: e.name,
                                                      }),
                                                      elements.jsxs(`small`, {
                                                        children: [
                                                          e.category,
                                                          ` `,
                                                          elements.jsx(`i`, {
                                                            children: `·`,
                                                          }),
                                                          ` `,
                                                          e.key,
                                                        ],
                                                      }),
                                                    ],
                                                  }),
                                                ],
                                              }),
                                              elements.jsx(`div`, {
                                                children: elements.jsx(
                                                  StatusPill,
                                                  {
                                                    status: e.status,
                                                  },
                                                ),
                                              }),
                                              elements.jsxs(`div`, {
                                                className: `progress-cell`,
                                                children: [
                                                  elements.jsx(`progress`, {
                                                    className: `progress-track`,
                                                    max: e.total || 1,
                                                    value: e.done,
                                                    "aria-label": `Completed issues`,
                                                  }),
                                                  elements.jsxs(`small`, {
                                                    children: [
                                                      e.done,
                                                      ` / `,
                                                      e.total,
                                                    ],
                                                  }),
                                                ],
                                              }),
                                              elements.jsx(`div`, {
                                                className: `next-action`,
                                                children:
                                                  e.next_action ||
                                                  `No open issues`,
                                              }),
                                              elements.jsx(Icon, {
                                                name: `chevron`,
                                                size: 16,
                                              }),
                                            ],
                                          },
                                          e.id,
                                        ),
                                      ),
                                      !filteredProjects.length &&
                                        elements.jsx(Empty, {
                                          title: `No matching projects`,
                                          body: `Try a different filter, or start a new project.`,
                                        }),
                                    ],
                                  }),
                                  elements.jsxs(`div`, {
                                    className: `provenance-note`,
                                    children: [
                                      elements.jsx(`i`, {
                                        className: `dot violet`,
                                      }),
                                      elements.jsxs(`p`, {
                                        children: [
                                          `Imported work retains its source. `,
                                          elements.jsx(`strong`, {
                                            children: `Needs review`,
                                          }),
                                          ` means current status hasn't been verified—not that work is blocked.`,
                                        ],
                                      }),
                                    ],
                                  }),
                                ],
                              })
                            : elements.jsxs(elements.Fragment, {
                                children: [
                                  project &&
                                    elements.jsxs(`div`, {
                                      className: `project-summary`,
                                      children: [
                                        elements.jsx(StatusPill, {
                                          status: project.status,
                                        }),
                                        elements.jsxs("label", {
                                          children: [
                                            "Project workspace ",
                                            elements.jsx("select", {
                                              "aria-label": "Project workspace",
                                              value: workspaceKey,
                                              disabled: movingProject,
                                              onChange: (event) =>
                                                moveProject(event.target.value),
                                              children: workspaces.map((item) =>
                                                elements.jsx(
                                                  "option",
                                                  {
                                                    value: item.key,
                                                    children: item.name,
                                                  },
                                                  item.key,
                                                ),
                                              ),
                                            }),
                                          ],
                                        }),
                                        elements.jsxs(`label`, {
                                          children: [
                                            `Project status `,
                                            elements.jsx(`select`, {
                                              "aria-label": `Project status`,
                                              value: project.status,
                                              onChange: (e) =>
                                                changeProjectStatus(
                                                  e.target.value,
                                                ),
                                              children: projectStatuses.map(
                                                (e) =>
                                                  elements.jsx(
                                                    `option`,
                                                    {
                                                      value: e,
                                                      children: statusName(e),
                                                    },
                                                    e,
                                                  ),
                                              ),
                                            }),
                                          ],
                                        }),
                                        elements.jsxs(`span`, {
                                          children: [
                                            project.done,
                                            ` of `,
                                            project.total,
                                            ` issues done`,
                                          ],
                                        }),
                                      ],
                                    }),
                                  elements.jsxs(`div`, {
                                    className: `issue-toolbar`,
                                    children: [
                                      elements.jsxs(`div`, {
                                        className: `view-switch`,
                                        children: [
                                          elements.jsxs(`button`, {
                                            className:
                                              view === `list` ? `selected` : ``,
                                            onClick: () =>
                                              navigate(projectId, "list"),
                                            children: [
                                              elements.jsx(Icon, {
                                                name: `list`,
                                                size: 16,
                                              }),
                                              ` List`,
                                            ],
                                          }),
                                          elements.jsxs(`button`, {
                                            className:
                                              view === `board`
                                                ? `selected`
                                                : ``,
                                            onClick: () =>
                                              navigate(projectId, "board"),
                                            children: [
                                              elements.jsx(Icon, {
                                                name: `board`,
                                                size: 16,
                                              }),
                                              ` Board`,
                                            ],
                                          }),
                                        ],
                                      }),
                                      elements.jsxs(`div`, {
                                        className: `toolbar-right`,
                                        children: [
                                          elements.jsxs(`span`, {
                                            children: [
                                              visibleIssueCount,
                                              ` issues`,
                                              visibleIssueCount > 500
                                                ? ` · first 500 shown`
                                                : ``,
                                            ],
                                          }),
                                          elements.jsxs(`select`, {
                                            "aria-label": `Filter issue status`,
                                            value: issueStatus,
                                            onChange: (e) =>
                                              setIssueStatus(e.target.value),
                                            children: [
                                              elements.jsx(`option`, {
                                                value: `all`,
                                                children: `All statuses`,
                                              }),
                                              issueStatuses.map((e) =>
                                                elements.jsx(
                                                  `option`,
                                                  {
                                                    value: e,
                                                    children: statusName(e),
                                                  },
                                                  e,
                                                ),
                                              ),
                                            ],
                                          }),
                                        ],
                                      }),
                                    ],
                                  }),
                                  loadingIssues &&
                                    elements.jsx(`p`, {
                                      className: `muted`,
                                      "aria-live": `polite`,
                                      children: `Updating…`,
                                    }),
                                  view === `board`
                                    ? elements.jsx(`div`, {
                                        className: `board`,
                                        children: issueStatuses.map((e) =>
                                          elements.jsxs(
                                            `section`,
                                            {
                                              className: `column`,
                                              onDragOver: (e) =>
                                                e.preventDefault(),
                                              onDrop: async (t) => {
                                                t.preventDefault();
                                                let n = visibleIssues.find(
                                                  (e) =>
                                                    e.id ===
                                                    t.dataTransfer.getData(
                                                      `text/plain`,
                                                    ),
                                                );
                                                if (n && n.status !== e)
                                                  try {
                                                    (await request(
                                                      `/issues/` + n.id,
                                                      jsonRequest(`PATCH`, {
                                                        status: e,
                                                        expected_version:
                                                          n.version,
                                                      }),
                                                    ),
                                                      await refresh());
                                                  } catch (e) {
                                                    setError(e.message);
                                                  }
                                              },
                                              children: [
                                                elements.jsxs(`header`, {
                                                  children: [
                                                    elements.jsx(StatusPill, {
                                                      status: e,
                                                    }),
                                                    elements.jsx(`span`, {
                                                      children:
                                                        visibleIssues.filter(
                                                          (t) => t.status === e,
                                                        ).length,
                                                    }),
                                                  ],
                                                }),
                                                issues
                                                  .filter((t) => t.status === e)
                                                  .map((e) =>
                                                    elements.jsxs(
                                                      `button`,
                                                      {
                                                        className: `issue-card`,
                                                        draggable: !0,
                                                        onDragStart: (t) =>
                                                          t.dataTransfer.setData(
                                                            `text/plain`,
                                                            e.id,
                                                          ),
                                                        onClick: () =>
                                                          openIssue(e.id),
                                                        children: [
                                                          elements.jsxs(`div`, {
                                                            children: [
                                                              elements.jsx(
                                                                `code`,
                                                                {
                                                                  children:
                                                                    e.key,
                                                                },
                                                              ),
                                                              elements.jsx(
                                                                `span`,
                                                                {
                                                                  className:
                                                                    `priority ` +
                                                                    e.priority,
                                                                  children:
                                                                    e.priority,
                                                                },
                                                              ),
                                                            ],
                                                          }),
                                                          elements.jsx(`h3`, {
                                                            children: e.title,
                                                          }),
                                                          elements.jsxs(
                                                            `footer`,
                                                            {
                                                              children: [
                                                                elements.jsx(
                                                                  `small`,
                                                                  {
                                                                    children:
                                                                      e.project_name,
                                                                  },
                                                                ),
                                                                elements.jsx(
                                                                  `span`,
                                                                  {
                                                                    className: `mini-avatar`,
                                                                    children:
                                                                      e.owner.slice(
                                                                        0,
                                                                        1,
                                                                      ) || `–`,
                                                                  },
                                                                ),
                                                              ],
                                                            },
                                                          ),
                                                        ],
                                                      },
                                                      e.id,
                                                    ),
                                                  ),
                                              ],
                                            },
                                            e,
                                          ),
                                        ),
                                      })
                                    : elements.jsxs(`div`, {
                                        className: `issues-table`,
                                        children: [
                                          elements.jsxs(`div`, {
                                            className: `issues-table-head`,
                                            children: [
                                              elements.jsx(`span`, {
                                                children: `ISSUE`,
                                              }),
                                              elements.jsx(`span`, {
                                                children: `STATUS`,
                                              }),
                                              elements.jsx(`span`, {
                                                children: `PRIORITY`,
                                              }),
                                              elements.jsx(`span`, {
                                                children: `OWNER`,
                                              }),
                                              elements.jsx(`span`, {
                                                children: `DUE`,
                                              }),
                                            ],
                                          }),
                                          visibleIssues.map((e) =>
                                            elements.jsxs(
                                              `button`,
                                              {
                                                className: `issues-table-row`,
                                                onClick: () => openIssue(e.id),
                                                children: [
                                                  elements.jsxs(`div`, {
                                                    children: [
                                                      elements.jsx(`code`, {
                                                        children: e.key,
                                                      }),
                                                      elements.jsx(`strong`, {
                                                        children: e.title,
                                                      }),
                                                      e.parent_id &&
                                                        elements.jsx(`small`, {
                                                          children: `↳ Subtask`,
                                                        }),
                                                    ],
                                                  }),
                                                  elements.jsx(StatusPill, {
                                                    status: e.status,
                                                  }),
                                                  elements.jsx(`span`, {
                                                    className:
                                                      `priority ` + e.priority,
                                                    children: e.priority,
                                                  }),
                                                  elements.jsx(`span`, {
                                                    className: `muted`,
                                                    children:
                                                      e.owner || `Unassigned`,
                                                  }),
                                                  elements.jsx(`small`, {
                                                    className:
                                                      e.due_date &&
                                                      e.due_date <
                                                        new Date()
                                                          .toISOString()
                                                          .slice(0, 10) &&
                                                      e.status !== `done`
                                                        ? `overdue`
                                                        : `muted`,
                                                    children: e.due_date || `—`,
                                                  }),
                                                ],
                                              },
                                              e.id,
                                            ),
                                          ),
                                          !visibleIssues.length &&
                                            elements.jsx(Empty, {}),
                                        ],
                                      }),
                                  project?.source &&
                                    elements.jsxs(`details`, {
                                      className: `project-source`,
                                      children: [
                                        elements.jsx(`summary`, {
                                          children: `Project source & context`,
                                        }),
                                        elements.jsx(`p`, {
                                          className: `prewrap`,
                                          children: project.description,
                                        }),
                                        elements.jsx(`p`, {
                                          className: `source prewrap`,
                                          children: project.source,
                                        }),
                                      ],
                                    }),
                                ],
                              }),
                        ],
                      }),
                elements.jsxs(`footer`, {
                  className: `page-footer`,
                  children: [
                    elements.jsx(`span`, {
                      children: `NIRA HQ`,
                    }),
                    elements.jsx(`span`, {
                      children: `Your work. Your infrastructure.`,
                    }),
                    elements.jsxs(`span`, {
                      className: `live`,
                      children: [
                        elements.jsx(`i`, {}),
                        ` Connected to workspace`,
                      ],
                    }),
                  ],
                }),
              ],
            }),
            modal === `settings` &&
              elements.jsx(Settings, {
                close: () => setModal(null),
              }),
            ` `,
            modal === "workspace" &&
              elements.jsx(Modal, {
                title: "Create a workspace",
                close: () => setModal(null),
                children: elements.jsx(WorkspaceForm, {
                  onCreated: async (item) => {
                    await loadWorkspaces();
                    setModal(null);
                    navigate(null, "portfolio", item.key);
                  },
                }),
              }),
            modal === `project` &&
              elements.jsx(Modal, {
                title: `Create a project`,
                close: () => setModal(null),
                children: elements.jsx(ProjectForm, {
                  workspaceKey,
                  onCreated: async (e) => {
                    (setModal(null), await loadProjects(), navigate(e.id));
                  },
                }),
              }),
            modal === `issue` &&
              elements.jsx(Modal, {
                title: `Create an issue`,
                close: () => setModal(null),
                children: createProjectId
                  ? elements.jsx(IssueForm, {
                      project: createProjectId,
                      onCreated: async (e) => {
                        (setModal(null), await refresh(), openIssue(e.id));
                      },
                    })
                  : elements.jsx(`div`, {
                      className: `stack`,
                      children: elements.jsxs(`label`, {
                        children: [
                          `Choose project`,
                          elements.jsxs(`select`, {
                            autoFocus: !0,
                            defaultValue: ``,
                            onChange: (e) => setCreateProjectId(e.target.value),
                            children: [
                              elements.jsx(`option`, {
                                value: ``,
                                disabled: !0,
                                children: `Select a project…`,
                              }),
                              visibleProjects.map((e) =>
                                elements.jsx(
                                  `option`,
                                  {
                                    value: e.id,
                                    children: e.name,
                                  },
                                  e.id,
                                ),
                              ),
                            ],
                          }),
                        ],
                      }),
                    }),
              }),
          ],
        })
      : elements.jsx(Login, {
          onLogin: setSession,
        });
}
ReactDOM.createRoot(document.getElementById(`root`)).render(
  elements.jsx(WorkspaceApp, {}),
);
