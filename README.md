# OmnIkestrAPIM
AI-powered autonomous PIM &amp; e-commerce orchestrator (NAS Watcher, AI Vision, Price Monitor &amp; Dashboard)

## Local development

For local development, start the development stack from the repository root:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

On Linux, the one-time bootstrap can create `data/products`, generate a
permission-restricted `.env` with a random PostgreSQL password, and start the
development containers:

```bash
bash setup.sh
```

The backend and watcher read the product roots from environment configuration. Set `PRODUCT_ROOTS` to a comma-separated list of directories, or keep the default single-root setup with `PRODUCTS_ROOT=/data/products`. The legacy Windows helper remains available as an optional tool, but it is not required for the default flow.

Example environment values:

```bash
export PRODUCT_ROOTS=/data/products,/mnt/nas/products
export WATCH_DIRS=/data/products,/mnt/nas/products
export PUID=1000
export PGID=1000
```

After startup, visit [http://localhost:8000/settings](http://localhost:8000/settings) to review the indexed catalog. The development stack mounts product files into the backend and watcher containers.

The API indexes product folders and their files in PostgreSQL. Available routes include:

* `GET /api/products` — list the indexed catalog.
* `POST /api/products/scan` — rescan the selected directory and reconcile the index.
* `GET /api/products/events` — read persisted watcher events.
* `GET /api/health` — check API availability.

Product event history and the catalog index are stored in PostgreSQL's persistent `postgres_data` volume. The backend applies Alembic migrations at startup. The default database credentials are for local development only; set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in the environment before deployment. The API does not include authentication and should not be exposed to the internet. The Windows folder picker requires Docker Desktop and PowerShell on the host; the development setup is intended for local use.

## TrueNAS SCALE

For the initial TrueNAS SCALE 25.10 Custom App deployment, use the production
Compose file and follow the dataset, image publishing, permissions, and security
steps in [the TrueNAS installation guide](docs/deployment/truenas-scale-25.10.md).
Do not use the development Compose file directly on production data.

The GitHub Actions publishing workflow builds the backend and watcher images
for pushes to `main` (updating the mutable `latest` tags) and SemVer tags
(`vMAJOR.MINOR.PATCH`). The TrueNAS Compose currently consumes `latest`; use
version-pinned image tags when reproducible deployments are preferred. GHCR
access on TrueNAS may require a package-read PAT; never store the token in the
Compose file or repository. The publishing workflow
[completed successfully](https://github.com/Walverde/OmnIkestrAPIM/actions/runs/42518491139),
and the initial integrated TrueNAS stack (`db`, `backend`, and `watcher`) was
brought up successfully on 08/11/2026.

On first access to `http://localhost:8000`, the setup wizard lets you choose or
create a product folder inside the mounted product volume. The selection is
stored in PostgreSQL and restored on the next backend start. It cannot mount
arbitrary host folders; Docker/TrueNAS must mount those folders into the
container first. A saved selection in the database takes precedence over the
bootstrap environment default; use the settings screen or update the saved
configuration to change it later.
