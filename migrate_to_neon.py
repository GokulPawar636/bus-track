"""One-time, all-or-nothing copy of local profiles into an empty PostgreSQL DB."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import accounts
import database

TABLES = {
    "accounts": "id,name,role,mobile,stop,salt,password,status,review_note",
    "preferences": "account,value",
    "service_settings": "id,value",
    "account_audit": "id,actor,account,action,at",
    "pickup_alerts": "id,trip,account,stop,channel,message,phone,created,status,provider_id",
}


def migrate(source, url):
    source = Path(source).resolve()
    if not url:
        raise ValueError("Set DATABASE_URL to your Neon connection string first.")
    if not source.is_file():
        raise ValueError("Source SQLite database does not exist.")
    counts = {}
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        src.execute("BEGIN")  # Consistent read snapshot, without modifying the source.
        columns = {row["name"] for row in src.execute("PRAGMA table_info(accounts)")}
        if not {"status", "review_note"} <= columns:
            raise ValueError("Update the local SQLite schema by running the app locally before migrating.")
        with database.connect(source, url) as dst:
            accounts.create_schema(dst)
            # Avoid racing an accidentally started application during the copy.
            dst.execute("LOCK TABLE accounts,preferences,service_settings,account_audit,pickup_alerts,sessions IN ACCESS EXCLUSIVE MODE")
            for table in (*TABLES, "sessions"):
                if dst.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise ValueError("Destination is not empty. Migration stopped without overwriting records. Use an empty Neon database before first deployment.")
            for table, column_list in TABLES.items():
                if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    counts[table] = 0
                    continue
                columns = column_list.split(",")
                rows = src.execute(f"SELECT {column_list} FROM {table}")
                counts[table] = 0
                for row in rows:
                    values = dict(row)
                    # Old trips cannot resume on the new host; never replay old calls.
                    if table == "pickup_alerts" and values["status"] in ("queued", "attempting"):
                        values["status"] = "cancelled"
                    dst.execute(f"INSERT INTO {table} ({column_list}) VALUES ({','.join('?' for _ in columns)})", tuple(values[c] for c in columns))
                    counts[table] += 1
            for table in ("account_audit", "pickup_alerts"):
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE(MAX(id),0)+1, false) FROM {table}")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=accounts.DB)
    args = parser.parse_args()
    try:
        counts = migrate(args.source, accounts.DATABASE_URL)
    except (ValueError, database.DatabaseError, sqlite3.Error) as exc:
        # SQLite messages contain no PostgreSQL URL; adapter errors are sanitized.
        raise SystemExit(str(exc)) from None
    print("Migration committed. Source unchanged. Existing passwords and approvals preserved.")
    print("Sessions were not copied; sign in again. Old queued calls were cancelled.")
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
