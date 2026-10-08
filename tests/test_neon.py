"""Optional real PostgreSQL checks. TEST_DATABASE_URL must be a disposable test DB."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounts
import database
import migrate_to_neon
import pickup_alerts
import preferences

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
HAS_DRIVER = importlib.util.find_spec("psycopg") is not None
ROUTE = json.loads((Path(__file__).resolve().parents[1] / "route-data.js").read_text(encoding="utf-8").removeprefix("window.BUS_ROUTE = ").strip().removesuffix(";"))


@unittest.skipUnless(TEST_URL and HAS_DRIVER, "Set TEST_DATABASE_URL and install requirements.txt for real PostgreSQL integration tests")
class NeonTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        self.schema = "bus_test_" + uuid.uuid4().hex
        options = conninfo_to_dict(TEST_URL)
        options["sslmode"] = "require"
        self.base_url = make_conninfo(**options)
        with psycopg.connect(self.base_url) as con:
            con.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        options["options"] = "-c search_path=" + self.schema
        self.url = make_conninfo(**options)
        self.url_patch = patch.object(accounts, "DATABASE_URL", self.url)
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)

    def drop_schema(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.base_url) as con:
            con.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def seed(self):
        accounts.initialize(ROUTE, "driver-test-password")
        accounts.initialize_admin("admin-test-password")
        accounts.register({"id":"EMP001", "name":"Neon Employee", "password":"employee-password", "stop":1},16)
        accounts.edit_profile("ADMIN", {"id":"EMP001", "status":"approved"},16)

    def test_profiles_settings_alerts_and_restart_persistence(self):
        self.seed()
        token,user = accounts.login("EMP001", "employee-password")
        cookie = {"Cookie":"commute_session=" + token}
        preferences.save(user, {"preferences":{"previous_stop_notification":True}},16)
        preferences.save(user, {"preferences":{"follow_bus":False}},16)
        self.assertTrue(preferences.read(user)["preferences"]["previous_stop_notification"])
        self.assertFalse(preferences.read(user)["preferences"]["follow_bus"])
        admin_token,admin = accounts.login("ADMIN", "admin-test-password")
        preferences.save(admin,{"service":{"helpdesk":"9876543210"}},16)
        preferences.save(admin,{"service":{"gps_stale_seconds":30}},16)
        self.assertEqual(preferences.service()["helpdesk"],"9876543210")
        pickup_alerts.queue("test-trip",0,ROUTE["stops"],"02")
        pickup_alerts.queue("test-trip",0,ROUTE["stops"],"02")
        with accounts.connect() as con:
            rows=con.execute("SELECT * FROM pickup_alerts").fetchall()
            self.assertEqual(len(rows),1)
            self.assertIsInstance(rows[0]["id"],int)
            self.assertGreater(rows[0]["created"],1700000000)
        self.assertFalse(accounts.initialize(ROUTE,"ignored-password"))
        self.assertEqual(accounts.session(cookie)["stop"],1)
        self.assertEqual(len(accounts.list_profiles()),2)
        with self.assertRaises(ValueError):
            accounts.register({"id":"EMP001", "name":"Duplicate", "password":"employee-password", "stop":1},16)
        preferences.change_password(user,{"current_password":"employee-password","new_password":"changed-password"})
        self.assertIsNone(accounts.session(cookie))
        self.assertEqual(accounts.login("EMP001","changed-password")[1]["status"],"approved")

    def test_transaction_rollback(self):
        self.seed()
        with self.assertRaises(ValueError):
            with accounts.connect() as con:
                con.execute("UPDATE accounts SET name=? WHERE id=?",("Wrong Name","EMP001"))
                raise ValueError("Abort transaction")
        self.assertEqual(accounts.login("EMP001","employee-password")[1]["name"],"Neon Employee")

    def test_migration_preserves_passwords_and_refuses_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"source.sqlite3"
            with patch.object(accounts,"DATABASE_URL",""), patch.object(accounts,"DB",source):
                self.seed()
                _,user=accounts.login("EMP001","employee-password")
                preferences.save(user,{"preferences":{"previous_stop_notification":True}},16)
                pickup_alerts.queue("old-trip",0,ROUTE["stops"],"02")
            original=source.read_bytes()
            counts=migrate_to_neon.migrate(source,self.url)
            self.assertEqual(counts["accounts"],3)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(accounts.login("EMP001","employee-password")[1]["status"],"approved")
            with self.assertRaises(ValueError):
                migrate_to_neon.migrate(source,self.url)
            # Imported serial IDs must not collide with later audit/alert inserts.
            accounts.edit_profile("ADMIN",{"id":"EMP001","name":"Updated Name"},16)
            pickup_alerts.queue("new-trip",0,ROUTE["stops"],"02")
            with accounts.connect() as con:
                self.assertEqual(len(con.execute("SELECT id FROM pickup_alerts").fetchall()),2)


if __name__ == "__main__":
    unittest.main()
