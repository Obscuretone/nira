# Nira

Nira is a self-hosted, single-owner project and issue tracker with a browser workspace and MCP tools. It grew out of [Nira Legacy](https://github.com/Obscuretone/nira-legacy), the standalone CLI experiment. The browser source lives in `frontend/`. The workspace shell uses the recovered React components in `frontend/components.jsx`.

![Nira issue view with a project sidebar, release checklist, and issue controls](docs/images/issue-workspace.png)

The screenshot shows a direct issue link in a local demo with fictional data. See how Nira fits into the [self-hosted stack](https://obscuretone.com/posts/en/self_hosted_tools).

## Run locally

The checkout includes a ready-to-serve browser build.

Install the locked requirements, create a private local configuration, then run the preview:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
python scripts/local_config.py
NIRA_CONFIG=.private/local-config.json python scripts/preview.py
```

Open http://localhost:8865. Local credentials are generated under `.private/`.

## Rebuild the browser workspace

Use Node.js 22.12 or later in the 22.x series, or a supported newer Node.js release. The backend serves the build from `frontend/dist/`.

```sh
cd frontend
npm ci --ignore-scripts
npm run build
```

When changing browser source, commit the regenerated `frontend/dist/` files with it.

## Run the tests

```sh
python -m unittest discover -s tests
```

## Hosted data and access

Use MariaDB behind an HTTPS proxy. Set the externally visible origin and cookie settings in a private configuration. Existing MariaDB installations should back up the database and apply `deploy/expand-text-mariadb.sql` before launching this version. Keep the production configuration, credentials and issue data outside this repository.

Use the owner browser session to create and revoke delegated API keys. Write-scoped keys can edit workspace records. Manage the keys from the browser workspace. See [MCP usage](docs/mcp.md) for transport details.

## Workspaces and direct links

Use the sidebar’s Workspace menu to switch or create a workspace. New projects belong to the selected workspace. In a project, use the Project workspace menu to move it and its issues. Workspace grouping uses the existing owner and delegated-token access model.

At startup Nira creates the Personal and Plugin company workspaces. Existing projects categorized as Plugin business go into Plugin company. Other existing projects appear in Personal until moved. Issue IDs and links survive workspace changes. Signed-in browser users opening a direct project or issue link see the same sidebar as regular navigation.
