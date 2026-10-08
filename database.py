"""SQLite for local use; PostgreSQL when DATABASE_URL is configured."""
from contextlib import contextmanager
import sqlite3


class DatabaseError(RuntimeError):
    """Safe message: never expose database URLs or provider details to clients."""


class Connection:
    def __init__(self, raw, postgres=False):
        self.raw = raw
        self.postgres = postgres
        self.identity = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY"

    def execute(self, sql, params=None):
        if self.postgres:
            import psycopg
            # Application queries use ? placeholders and no question marks in SQL literals.
            if params is not None:
                sql = sql.replace("%", "%%").replace("?", "%s")
            try:
                return self.raw.execute(sql, params)
            except psycopg.errors.UniqueViolation:
                raise sqlite3.IntegrityError("Duplicate record") from None
            except psycopg.Error:
                raise DatabaseError("Database operation failed. Check server database configuration.") from None
        return self.raw.execute(sql, params or ())

    def table_exists(self, name):
        if self.postgres:
            return bool(self.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?", (name,)).fetchone())
        return bool(self.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


@contextmanager
def connect(path, url=""):
    if url:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.conninfo import conninfo_to_dict
        except ImportError:
            raise DatabaseError("Install PostgreSQL support with: py -m pip install -r requirements.txt") from None
        try:
            options = conninfo_to_dict(url)
            # Neon requires encrypted connections. Preserve stronger supplied settings.
            if options.get("sslmode") not in ("require", "verify-ca", "verify-full"):
                options["sslmode"] = "require"
            options.setdefault("connect_timeout", "15")
            options.setdefault("application_name", "bus-track")
            with psycopg.connect(**options, row_factory=dict_row, prepare_threshold=None) as raw:
                yield Connection(raw, postgres=True)
        except psycopg.Error:
            raise DatabaseError("PostgreSQL unavailable. Check DATABASE_URL, network access, and Neon status.") from None
    else:
        raw = sqlite3.connect(path, timeout=10)
        raw.row_factory = sqlite3.Row
        try:
            with raw:
                yield Connection(raw)
        finally:
            raw.close()
