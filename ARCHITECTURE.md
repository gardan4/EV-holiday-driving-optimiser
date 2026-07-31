# Architecture

This file is a pointer. The living architecture docs are:

- **[CLAUDE.md](CLAUDE.md)** — the working map of the codebase (backend, frontend,
  infra) and the norms for changing it. Start here.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the layers and request flow.
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — Azure / GHCR / Bicep + CI.
- **[docs/ADDING_A_RESOURCE.md](docs/ADDING_A_RESOURCE.md)** — how to add your own
  tenant-scoped resource by copying the `Item` slice.

Keep these in sync with the code — when they disagree, trust the file tree.
