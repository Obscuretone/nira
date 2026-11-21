# Nira HQ

Nira HQ is a hosted single-owner portfolio and issue tracker derived from [standalone Nira](https://github.com/Obscuretone/nira). This repository contains the backend source and the frontend bundle recovered from the deployed release. The editable frontend source was absent from that release, so the bundle is included as a build artifact.

## Run locally

Install the locked requirements, create a private local configuration, then run the preview:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
python scripts/local_config.py
NIRA_CONFIG=.private/local-config.json python scripts/preview.py
```

Open http://localhost:8865. Local credentials are generated under `.private/`.

## Run the tests

```sh
python -m unittest discover -s tests
```

## Hosted data and access

Use MariaDB behind an HTTPS proxy. Set the externally visible origin and cookie settings in a private configuration. Existing MariaDB installations should back up the database and apply `deploy/expand-text-mariadb.sql` before launching this version. Keep the production configuration, credentials and issue data outside this repository.

Use the owner browser session to create and revoke delegated API keys. Write-scoped keys can edit workspace records. Manage the keys from the browser workspace. See [MCP usage](docs/mcp.md) for transport details.
