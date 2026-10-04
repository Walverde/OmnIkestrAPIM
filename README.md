# OmnIkestrAPIM
AI-powered autonomous PIM &amp; e-commerce orchestrator (NAS Watcher, AI Vision, Price Monitor &amp; Dashboard)

## Local development

For a single Windows startup flow, run the launcher from the repository root:

```powershell
.\start-local.cmd
```

This command starts the Docker Compose stack and launches the native Windows folder selector automatically. It also opens the settings page in the browser after startup. If you prefer to run the pieces manually, use:

```powershell
docker compose -f docker/docker-compose.yml up -d --build
```

And in a separate PowerShell window:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\tools\windows_folder_picker.ps1
```

Keep that window open, then visit [http://localhost:8000/settings](http://localhost:8000/settings). Choose a folder and apply it; the helper saves `PRODUCTS_DIR` in the ignored root `.env` file and recreates the backend and watcher containers. The Docker volume is mounted read-only in the backend and writable in the watcher.

The API indexes product folders and their files in PostgreSQL. Available routes include:

* `GET /api/products` — list the indexed catalog.
* `POST /api/products/scan` — rescan the selected directory and reconcile the index.
* `GET /api/products/events` — read persisted watcher events.
* `GET /api/health` — check API availability.

Product event history and the catalog index are stored in PostgreSQL's persistent `postgres_data` volume. Tables are created at backend startup. The default database credentials are for local development only; set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in the environment before deployment. The Windows folder picker requires Docker Desktop and PowerShell on the host; the current setup does not include authentication and is intended for local development.
