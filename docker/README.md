# FRT database container

One container that runs:

- **MySQL 8.4**, which holds the FRT data.
- **An admin UI** (Node), which with one button downloads the latest FRT from rcmp.ca, parses it and loads it.
  You can watch each stage's progress live.
- **The FRT scripts** from the repository root (`download_frt.py`, `parse_frt.py`, `split_frn.py`, `createDb.py`, `importIntoDb.py`).

## Quick start

```bash
cd docker
cp .env.example .env        # set MYSQL_ROOT_PASSWORD and UI_PASSWORD
docker compose up -d --build
```

Open <http://localhost:8080> and sign in with `UI_USERNAME` / `UI_PASSWORD`. Then click **Download & ingest latest FRT**.

MySQL listens on `127.0.0.1:3306` on the host. The live database is `FRT` (set with `FRT_DB_NAME`).

## Zero-downtime ingest

The live database is never written to during an ingest.

| Stage | What happens | Where |
|---|---|---|
| Download | `download_frt.py` fetches the newest `frt-*.pdf`. If that edition is already live, the run stops here (tick *Re-ingest* to force it). | `/data/pdf` |
| Parse + split | `parse_frt.py` and `split_frn.py` run **at the same time** on the PDF. | `/data/work/<job>` |
| Schema | `createDb.py` builds an empty `FRT_staging` database. | `FRT_staging` |
| Import | `importIntoDb.py` loads the records, then the per-FRN PDFs. | `FRT_staging` |
| Validate + swap | `swap_db.py` checks staging, then runs **one atomic `RENAME TABLE`**: live tables move to `FRT_previous` and staging tables move to `FRT`. | `FRT` |
| Clean up | Deletes the work files and superseded PDFs. | |

MySQL runs the multi-table `RENAME TABLE` as a single atomic operation, so clients querying `FRT` see either the complete old data or the complete new data. They never see a missing or half-loaded table. At most, a query waits a moment for the rename's metadata lock.

Safety checks:

- **Validation:** before swapping, staging must contain every table and at least `MIN_ROW_RATIO` (default 90%) as many FRNs as live. Otherwise the swap is refused and `FRT_staging` is kept so you can inspect it.
- **Failure or cancel:** if any stage fails or you click **Cancel**, the live database is unchanged. Cancel is disabled only during the few-second swap itself.
- **Silent parser errors:** `parse_frt.py` exits successfully even when it fails or a worker crashes. The UI watches its output and treats those messages as failures.
- **Interrupted runs:** if the container stops mid-ingest, the run is recorded as *interrupted* on the next start, leftovers are removed, and the live triggers are checked.

### Rollback

With `KEEP_PREVIOUS=true` (the default), the replaced data stays in `FRT_previous`. **Roll back** swaps it with live using the same atomic rename, so you can switch back again.

### Triggers

MySQL can't move a table that has triggers into another database. `swap_db.py` drops the triggers, renames the tables, and then recreates the `fulltextify` function and triggers on the live tables. The definitions are imported from `createDb.py`, so the schema stays defined in one place.

## Configuration (`docker/.env`)

| Variable | Default | |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | — | **Required.** Applied only when the `mysql-data` volume is first created. |
| `UI_USERNAME` | `admin` | Admin UI login (HTTP Basic). |
| `UI_PASSWORD` | — | **Required.** |
| `FRT_DB_NAME` | `FRT` | Live database. Staging and previous copies are `<name>_staging` and `<name>_previous`. |
| `FRT_READONLY_USER` / `FRT_READONLY_PASSWORD` | — | Optional MySQL account with `SELECT` on the live database, for apps that read the data. Created or updated on every start. The grant is database-level, so it keeps working across swaps. |
| `UI_PORT` / `UI_BIND` | `8080` / `0.0.0.0` | Host port and interface for the UI. |
| `MYSQL_PORT` / `MYSQL_BIND` | `3306` / `127.0.0.1` | Host port and interface for MySQL. Use `MYSQL_BIND=0.0.0.0` for remote access. |
| `PARSE_WORKERS` | `1` | `parse_frt.py` worker processes. `0` means one per CPU core: faster, but it uses more memory and its progress is approximate. |
| `SPLIT_WRITERS` | `2` | `split_frn.py` writer threads. |
| `MIN_ROW_RATIO` | `0.9` | Swap guard; see above. |
| `KEEP_PREVIOUS` | `true` | Keep the replaced version for rollback. |
| `TZ` | `UTC` | Container time zone. |

MySQL settings are in [mysql/frt.cnf](mysql/frt.cnf). Binary logging is off because the ingest bulk-loads several GB. `innodb_buffer_pool_size` is 512M; raise it if the host has memory to spare.

## Disk and time

- A full FRT is about 240 MB of PDF and produces a database of several GB.
- During an ingest, live, staging and previous can all exist at once, so allow about **3×** the database size, plus about 1 GB of work files. Set `KEEP_PREVIOUS=false` to bring that down to 2×.
- A full parse takes about 2 minutes with `PARSE_WORKERS=1` (about 20 seconds with `0` on a 16-core machine). You can close the browser: the run continues in the container, and the page catches up when you reopen it.

## Volumes

| Volume | Mounted at | Contents |
|---|---|---|
| `mysql-data` | `/var/lib/mysql` | All databases |
| `frt-data` | `/data` | `pdf/` (current edition), `work/` (temporary), `logs/` (one log per run), `state.json` (current/previous edition, history) |

## Security notes

- The UI uses HTTP Basic auth over plain HTTP. If it is reachable beyond a trusted network, put it behind a TLS reverse proxy.
- State-changing API calls require a JSON body. That forces a CORS preflight, so other sites can't trigger actions through your browser's cached credentials.
- The UI process runs as an unprivileged `frt` user and connects to MySQL as root, which the schema scripts need.

## API

All endpoints except `/healthz` require Basic auth.

| Method | Path | |
|---|---|---|
| GET | `/healthz` | Liveness and DB check (used by the Docker healthcheck) |
| GET | `/api/status` | Job, history, live/previous database stats |
| GET | `/api/events` | Server-Sent Events: `state` and `log` |
| POST | `/api/ingest` | Start an ingest. Body: `{"force": false}` |
| POST | `/api/ingest/cancel` | Cancel the running ingest |
| POST | `/api/rollback` | Swap live with previous |
| GET | `/api/search?q=` | Search by FRN, or by make/model/manufacturer prefix |
| GET | `/api/frn/:frn` | Full record |
| GET | `/api/frn/:frn/pdf` | That FRN's pages from the FRT |
| GET | `/api/jobs/:id/log` | Full log of a run |

Because ingest is a plain POST, you can schedule it from cron, for example:

```bash
curl -u admin:$UI_PASSWORD -X POST -H 'Content-Type: application/json' -d '{}' http://localhost:8080/api/ingest
```

If that edition is already live, the run finishes as *up to date* without changing anything.
