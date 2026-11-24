# OmnIkestrAPIM .
AI-powered autonomous PIM &amp; e-commerce orchestrator (NAS Watcher, AI Vision, Price Monitor &amp; Dashboard)

## Local development

For local development, start the development stack from the repository root:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
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

After startup, sign in at [http://localhost:8000/login](http://localhost:8000/login) using `OMNIKESTRAPIM_ADMIN_PASSWORD` from the generated `.env`; then visit [http://localhost:8000/settings](http://localhost:8000/settings) to review the indexed catalog. The development stack mounts product files into the backend and watcher containers.

The API indexes product folders and their files in PostgreSQL. Available routes include:

* `GET /api/products` — list the indexed catalog.
* `POST /api/products/scan` — rescan the selected directory and reconcile the index.
* `GET /api/products/events` — read persisted watcher events.
* `GET /api/health` — check API availability.

Product event history and the catalog index are stored in PostgreSQL's persistent `postgres_data` volume. The backend applies Alembic migrations at startup. The default database credentials are for local development only; set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in the environment before deployment.

The application requires all three security values at startup: `OMNIKESTRAPIM_ADMIN_PASSWORD` (at least 12 characters) for the administrator login, `OMNIKESTRAPIM_SESSION_SECRET` (at least 32 characters) for signed, eight-hour `HttpOnly`/`SameSite=Strict` sessions, and `OMNIKESTRAPIM_API_KEY` (at least 32 characters) for the watcher service. The setup script generates these values in a permission-restricted `.env`; retrieve the initial admin password from that file. Browser API access requires a logged-in session, while the API key is accepted only for watcher event ingestion. The current single-admin authorization is limited to the `default` workspace; other workspace IDs are rejected rather than trusted from request parameters. Missing security configuration prevents backend startup.

For production, set `OMNIKESTRAPIM_COOKIE_SECURE=true` and serve the application over HTTPS. The development setup opts out of the Secure-cookie flag for local HTTP use. Do not expose the application to the internet without HTTPS and a reviewed deployment configuration. The Windows folder picker requires Docker Desktop and PowerShell on the host; the development setup is intended for local use.

Publishing can return `reconciliation_required` when the remote result is ambiguous (including a worker restart during listing creation). Automatic retries are stopped to avoid duplicate listings. Check the Mercado Livre account, then call `POST /api/marketplaces/jobs/{job_id}/reconcile` with either `{"item_id":"<ID externo>"}` to link the found listing or `{"confirmed_not_published":true}` only after verifying no listing was created.

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
