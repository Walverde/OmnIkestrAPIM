# OmnIkestrAPIM
AI-powered autonomous PIM &amp; e-commerce orchestrator (NAS Watcher, AI Vision, Price Monitor &amp; Dashboard)

## Local development

For the default Linux / NAS setup, start the stack from the repository root:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

The backend and watcher read the product roots from environment configuration. Set `PRODUCT_ROOTS` to a comma-separated list of directories, or keep the default single-root setup with `PRODUCTS_ROOT=/data/products`. The legacy Windows helper remains available as an optional tool, but it is not required for the default flow.

Example environment values:

```bash
export PRODUCT_ROOTS=/data/products,/mnt/nas/products
export WATCH_DIRS=/data/products,/mnt/nas/products
export PUID=1000
export PGID=1000
```

After startup, visit [http://localhost:8000/settings](http://localhost:8000/settings) to review the indexed catalog. The Docker volume is mounted read-only in the backend and writable in the watcher.

The API indexes product folders and their files in PostgreSQL. Available routes include:

* `GET /api/products` — list the indexed catalog.
* `POST /api/products/scan` — rescan the selected directory and reconcile the index.
* `GET /api/products/events` — read persisted watcher events.
* `GET /api/health` — check API availability.

Product event history and the catalog index are stored in PostgreSQL's persistent `postgres_data` volume. Tables are created at backend startup. The default database credentials are for local development only; set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in the environment before deployment. The Windows folder picker requires Docker Desktop and PowerShell on the host; the current setup does not include authentication and is intended for local development.
