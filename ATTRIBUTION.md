# Foundations

Hosted Nira builds on Evan d'Entremont's Nira and Dive projects.

- Nira Legacy: [Obscuretone/nira-legacy](https://github.com/Obscuretone/nira-legacy), inspected commit `015d9270f99f539d6a9287bba6e1877caa35e55c`. `backend/markdown.py` is copied from the legacy renderer. Issue keys, source provenance, Markdown bodies, resolutions and related-ticket semantics follow the legacy model. Nira's GPLv3 license is preserved as `LICENSE`.
- Dive: [Obscuretone/dive](https://github.com/Obscuretone/dive), inspected commit `ce841966c7b8b7f086d547433825eb602b6f6f05`. Frontend `TaskForm`/`TaskRow` patterns were adapted into the unified UI. Parent task relationships and concrete smaller actions follow Dive's workflow. No Mistral configuration, API key or external provider was copied.
- MCP uses the official Python SDK, pinned in `requirements.lock`. Protocol transport and schemas are SDK implementations rather than a handwritten JSON-RPC approximation.

Hosted persistence uses a separate MariaDB database on the existing VPS. SQLite exists only in original read-only source snapshots and local tests/preview.
