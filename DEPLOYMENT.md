# Publish Bus Track on Render with Neon PostgreSQL

Neon stores the data; Render runs the Python web application. This is a Web Service, not a Static Site. Use one application instance/process because active trips and waiting counts are in memory.

## 1. Prepare Neon

Create a Neon project/database. In its Connect dialog, copy the PostgreSQL connection string (the pooled connection is supported). Keep its `sslmode=require` and `channel_binding=require` parameters. Save it privately; never place it in JavaScript, Git, or a screenshot. `DATABASE_URL` is read from the process environment. `.env.example` is documentation only; `.env` is not automatically loaded.

The app creates its tables on startup. Neon credentials need permission to create tables in the selected schema. Do not delete/reset the Neon database or change to an unrelated branch if you want to retain saved profiles.

## 2. Preserve existing local profiles (before deploying)

If you want the current ADMIN, DRIVER02, employees, approvals, passwords, settings, and alert/audit history, migrate them before Render starts against Neon. Otherwise skip this step and start with fresh accounts.

Stop the local app and keep a backup of `accounts.sqlite3`. In PowerShell from this project folder:

```powershell
py -m pip install -r requirements.txt
$env:DATABASE_URL='PASTE_YOUR_NEON_POSTGRESQL_CONNECTION_STRING'
py migrate_to_neon.py --source accounts.sqlite3
```

Use the PostgreSQL URI itself, not the `psql` command surrounding it. The migration reads SQLite without changing it, uses one PostgreSQL transaction, and refuses any nonempty destination. It preserves password hashes and approval states. Browser sessions are not copied, so sign in again. Old queued/in-progress voice calls are cancelled. Live GPS and waiting counts are not copied. No migration runs automatically during deployment.

If the destination already has profiles, use a separate empty Neon database; do not delete existing records to make the script run. After successful migration, point Render at this same database.

## 3. Publish the application code

Push the source files to your Git repository, including `database.py`, `requirements.txt`, `render.yaml`, and the website assets. Do not upload `.env`, database files/backups, or passwords. The included `.gitignore` excludes these files; it does not untrack files already committed.

In Render, choose New > Blueprint, connect the repository, and select `render.yaml`. Enter the secret variables when prompted:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Full Neon connection string |
| `BUS_ADMIN_PASSWORD` | Your chosen initial admin password, 10+ characters |
| `BUS_DRIVER_PASSWORD` | A different initial driver password, 10+ characters |

The Blueprint selects the Free plan, installs `requirements.txt`, starts `python -u server.py`, binds `0.0.0.0`, reads Render's `PORT`, sets secure cookies, and enables `/healthz`. Do not set `BUS_PORT` on Render. Do not run Gunicorn workers or additional replicas with the current in-memory trip state.

Alternatively create a Python Web Service manually with:

- Build command: `pip install -r requirements.txt`
- Start command: `python -u server.py`
- Health check path: `/healthz`
- Environment: the three secrets above plus `BUS_HOST=0.0.0.0`, `BUS_HTTPS=1`, `BUS_REQUIRE_DATABASE=1`.

Startup refuses missing `DATABASE_URL` on Render to prevent accidentally saving accounts to its temporary disk. A broken PostgreSQL connection does not fall back to SQLite. The app does not print the connection string.

## 4. Sign in and verify persistence

Open the HTTPS `onrender.com` URL shown by Render. Use ADMIN and its existing password if migrated, or the initial admin password supplied above for a new database. A fresh DRIVER02 uses the initial driver password and requires admin approval. Environment password variables do not reset existing accounts. Change any shared/sample passwords before real use.

Create a test employee, approve it, choose a pickup, and save settings. Restart the Render service, then sign in again and confirm these values remain. Test driver GPS on a phone using HTTPS. `/healthz` should return `{"ok":true}`; a database outage returns a generic 503 without connection details.

## What survives restarts?

Accounts, approval status, password hashes, sessions, pickup choices, preferences, service settings, audit entries, and alert history live in Neon. They survive Render restarts/redeploys when you keep the same database. Active trip state, current GPS, waiting counts, and login rate-limit counters are in memory and reset. The driver must start sharing again after a restart. Historical GPS tracking is not implemented.

Render Free sleeps after inactivity. Neon can suspend idle compute and wake on a connection; this does not delete stored data. This setup does not guarantee zero downtime or unlimited free storage. Review provider quotas and keep database backups/exports; retention and recovery depend on your Neon plan. Automated phone calls require a separately configured calling provider and are not made free by hosting here.

## Local development and tests

Without `DATABASE_URL`, local development still uses `accounts.sqlite3` and Python's standard library. For local HTTP, do not set `BUS_HTTPS=1`.

```powershell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
py server.py
```

Backend and browser tests use temporary SQLite databases even when your shell has a production `DATABASE_URL`. They never migrate or clear the production database. Optional PostgreSQL integration tests use only `TEST_DATABASE_URL` and create an isolated temporary schema; use a disposable Neon test branch with a direct (unpooled) connection and permission to create/drop schemas. Run `py -B -m unittest discover -s tests -p test_neon.py -v` after setting `TEST_DATABASE_URL`. Never use the production database for this test setting.

## Provider documentation

- [Render Blueprint configuration](https://render.com/docs/blueprint-spec)
- [Render free-plan limits](https://render.com/docs/free)
- [Neon connection setup](https://neon.com/docs/connect/connect-from-any-app)
- [Psycopg installation](https://www.psycopg.org/psycopg3/docs/basic/install.html)
