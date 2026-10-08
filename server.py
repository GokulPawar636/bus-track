"""Small single-bus pilot server. Run: py server.py (SQLite or Neon PostgreSQL)."""
import accounts
import database
import preferences
import pickup_alerts
import json
import math
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ROUTE = json.loads((ROOT / "route-data.js").read_text(encoding="utf-8").removeprefix("window.BUS_ROUTE = ").strip().removesuffix(";"))
STOPS = ROUTE["stops"]
LOGIN_ATTEMPTS = {}
LOCK = threading.RLock()
STATE = {"active": False, "next_stop": 0, "location": None, "direction": "morning"}
WAITING = {}

def now():
    return int(time.time() * 1000)

def trip_stops(direction):
    return STOPS if direction == "morning" else list(reversed(STOPS))

def expire_waiting():
    cutoff = now() - preferences.service()['waiting_expiry_seconds'] * 1000
    for client in list(WAITING):
        if WAITING[client]["at"] < cutoff:
            del WAITING[client]

def snapshot(client_id):
    with LOCK:
        expire_waiting()
        counts = [0] * len(STOPS)
        for entry in WAITING.values():
            counts[entry["stop"]] += 1
        return {**STATE, "counts": counts, "waiting": WAITING.get(client_id, {}).get("stop"), "stops": trip_stops(STATE["direction"]), "route": ROUTE, "server_time": now()}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log driver keys or employee identifiers.

    def send_json(self, status, data, cookie=None):
        payload = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            secure = "; Secure" if os.environ.get("BUS_HTTPS") == "1" else ""
            self.send_header("Set-Cookie", "commute_session=" + cookie + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + ("604800" if cookie else "0") + secure)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        try:
            return self.get_request()
        except database.DatabaseError:
            return self.send_json(503, {"error": "Database temporarily unavailable. Please try again."})

    def get_request(self):
        url = urlsplit(self.path)
        if url.path == "/healthz":
            with accounts.connect() as con:
                con.execute("SELECT 1").fetchone()
            return self.send_json(200, {"ok": True})
        if url.path in {"/api/state", "/api/me", "/api/admin/accounts", "/api/settings"}:
            user = accounts.session(self.headers)
            if not user:
                return self.send_json(401, {"error": "Please sign in."})
            if url.path == '/api/settings':
                return self.send_json(200, preferences.read(user))
            if url.path == "/api/me":
                return self.send_json(200, {"user": user})
            if user['status'] != 'approved':
                return self.send_json(403, {'error': 'Admin approval is required to use the service.', 'user': user})
            if url.path == '/api/admin/accounts':
                if user['role'] != 'admin':
                    return self.send_json(403, {'error': 'Admin access required.'})
                return self.send_json(200, {'accounts': accounts.list_profiles()})
            if user['role'] == 'admin':
                return self.send_json(403, {'error': 'Use the admin profile management interface.'})
            data = snapshot(user["id"])
            data["service"] = preferences.service()
            data["profile"] = user
            data["pickup_alerts"] = pickup_alerts.notifications(user, STATE)
            data["driver"] = accounts.driver_contact(STATE.get("driver_id") or "DRIVER02")
            if user["role"] == "employee":
                data["counts"] = [0] * len(STOPS)
            return self.send_json(200, data)
        files = {"/pickup-notifications.js": ("pickup-notifications.js", "text/javascript"),"/notification-worker.js": ("notification-worker.js", "text/javascript"),"/settings.js": ("settings.js", "text/javascript"),"/admin.js": ("admin.js", "text/javascript"),"/auth.js": ("auth.js", "text/javascript"),"/route-data.js": ("route-data.js", "text/javascript"),"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if url.path not in files:
            return self.send_json(404, {"error": "Not found"})
        name, content_type = files[url.path]
        payload = (ROOT / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        try:
            return self.post_request()
        except database.DatabaseError:
            return self.send_json(503, {"error": "Database temporarily unavailable. Please try again."})

    def post_request(self):
        path = urlsplit(self.path).path
        if path not in {"/api/start", "/api/stop", "/api/reached", "/api/location", "/api/waiting", "/api/register", "/api/login", "/api/logout", "/api/profile", "/api/admin/update", "/api/settings", "/api/password"}:
            return self.send_json(404, {"error": "Not found"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_json(415, {"error": "JSON required"})
        user = accounts.session(self.headers)
        if path not in {"/api/login", "/api/register"}:
            if not user:
                return self.send_json(401, {"error": "Please sign in."})
            if path not in ('/api/logout','/api/password') and user['status'] != 'approved':
                return self.send_json(403, {'error': 'Admin approval is required to use the service.', 'user': user})
            required = "employee" if path in {"/api/waiting", "/api/profile"} else "admin" if path == "/api/admin/update" else "driver"
            if path not in ("/api/logout","/api/settings","/api/password") and user["role"] != required:
                return self.send_json(403, {"error": "Your account cannot access this role's actions."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            if path == '/api/password':
                with LOCK:
                    key='password:'+user['id']
                    attempts=[t for t in LOGIN_ATTEMPTS.get(key,[]) if time.time()-t<60]
                    if len(attempts)>=6:
                        return self.send_json(429,{'error':'Too many password attempts. Wait a minute.'})
                    LOGIN_ATTEMPTS[key]=attempts+[time.time()]
                    preferences.change_password(user,data)
                    token, profile=accounts.login(user['id'],data['new_password'])
                return self.send_json(200,{'user':profile},cookie=token)
            if path == '/api/settings':
                with LOCK:
                    preferences.save(user,data,len(STOPS))
                    if 'stop' in data and data['stop']!=user['stop']:
                        WAITING.pop(user['id'],None)
                return self.send_json(200,preferences.read(accounts.session(self.headers)))
            if path in {"/api/login", "/api/register"}:
                with LOCK:
                    address = self.client_address[0]
                    attempts = [t for t in LOGIN_ATTEMPTS.get(address, []) if time.time()-t<60]
                    if len(attempts)>=12:
                        return self.send_json(429, {"error": "Too many sign-in attempts. Wait one minute."})
                    LOGIN_ATTEMPTS[address] = attempts + [time.time()]
                if path == "/api/register":
                    if not preferences.service()['registration_open']:
                        return self.send_json(403, {'error':'New registration is paused. Contact your transport admin.'})
                    identity = accounts.register(data, len(STOPS))
                else:
                    identity = data.get("id", "")
                token, profile = accounts.login(identity, data.get("password", ""))
                return self.send_json(200, {"user": profile}, cookie=token)
            if path == "/api/logout":
                accounts.logout(self.headers)
                with LOCK:
                    WAITING.pop(user["id"], None)
                    if user["role"] == "driver" and STATE.get("driver_id") == user["id"]:
                        STATE["active"] = False
                return self.send_json(200, {"ok": True}, cookie="")
            if path == '/api/admin/update':
                with LOCK:
                    updated = accounts.edit_profile(user['id'], data, len(STOPS))
                    WAITING.pop(updated['id'], None)
                    if updated['role']=='driver' and updated['status']!='approved' and STATE.get('driver_id')==updated['id']:
                        STATE['active']=False
                return self.send_json(200, {'user': updated})
            if path == "/api/profile":
                if set(data) != {"stop"}:
                    raise ValueError("Only your pickup stop can be changed here.")
                with LOCK:
                    accounts.save_stop(user["id"], data["stop"], len(STOPS))
                    WAITING.pop(user["id"], None)
                return self.send_json(200, {"user": accounts.session(self.headers)})
            with LOCK:
                user = accounts.session(self.headers)
                if not user or user['status']!='approved':
                    return self.send_json(403, {'error': 'Admin approval is required to use the service.', 'user': user})
                if user['role']=='driver' and STATE['active'] and STATE.get('driver_id') not in (None,user['id']):
                    return self.send_json(403, {'error': 'Another driver is currently running this trip.'})
                expire_waiting()
                if path == "/api/waiting":
                    client = user["id"]
                    saved_stop = user["stop"]
                    if "stop" in data and data["stop"] != saved_stop:
                        raise ValueError("Change your saved pickup before waiting at another stop.")
                    flag = data.get("waiting")
                    if type(saved_stop) is not int or not 0 <= saved_stop < len(STOPS) or type(flag) is not bool:
                        raise ValueError("Invalid waiting update")
                    stop = len(STOPS) - 1 - saved_stop if STATE["direction"] == "return" else saved_stop
                    if flag:
                        # A heartbeat must never recreate a passenger cleared at a reached stop.
                        if data.get("heartbeat"):
                            if client in WAITING and stop == WAITING[client]["stop"]:
                                WAITING[client]["at"] = now()
                        else:
                            if stop < STATE["next_stop"]:
                                raise ValueError("The bus has already passed this stop.")
                            WAITING[client] = {"stop": stop, "at": now()}
                    else:
                        WAITING.pop(client, None)
                elif path == "/api/start":
                    direction = data.get("direction", "morning")
                    if direction not in ("morning", "return"):
                        raise ValueError("Choose the morning or return route.")
                    if STATE["active"] and direction != STATE["direction"]:
                        raise ValueError("Stop the current trip before changing its direction.")
                    existing_trip = bool(STATE.get("trip_id"))
                    new_trip = not existing_trip or STATE["next_stop"] == len(STOPS) or direction != STATE["direction"]
                    if new_trip:
                        STATE['trip_id'] = secrets.token_hex(16)
                        STATE["next_stop"] = 1 if direction == "return" else 0
                        STATE["location"] = None
                        if existing_trip:
                            WAITING.clear()
                        STATE["direction"] = direction
                    STATE["active"] = True
                    STATE["driver_id"] = user['id']
                elif path == "/api/stop":
                    STATE["active"] = False
                elif path == "/api/reached":
                    if not STATE["active"] or STATE["next_stop"] >= len(STOPS):
                        raise ValueError("No active pickup stop.")
                    current = STATE["next_stop"]
                    for client in list(WAITING):
                        if WAITING[client]["stop"] == current:
                            del WAITING[client]
                    if STATE["direction"] == "morning":
                        pickup_alerts.queue(STATE['trip_id'],current,STOPS,ROUTE['number'])
                    STATE["next_stop"] += 1
                    if STATE["next_stop"] == len(STOPS):
                        STATE["active"] = False
                elif path == "/api/location":
                    if not STATE["active"]:
                        raise ValueError("Start location sharing first.")
                    for field in ("lat", "lng", "accuracy", "timestamp"):
                        if type(data.get(field)) not in (int, float) or not math.isfinite(data[field]):
                            raise ValueError("Invalid GPS coordinates or timestamp")
                    if not -90 <= data["lat"] <= 90 or not -180 <= data["lng"] <= 180 or not 0 <= data["accuracy"] <= 100000:
                        raise ValueError("GPS coordinates out of range")
                    stamp = data["timestamp"]
                    if stamp > now() + 10000 or stamp < now() - 120000:
                        raise ValueError("GPS sample is too old or the device clock is incorrect.")
                    if STATE["location"] and stamp < STATE["location"]["timestamp"]:
                        raise ValueError("Out-of-order GPS sample")
                    STATE["location"] = {key: data[key] for key in ("lat", "lng", "accuracy", "timestamp")}
            return self.send_json(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})

class BusHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can bind an occupied port and serve the wrong app.
    allow_reuse_address = False


def bind_server(host, configured_port=None):
    """Keep explicit deployment ports fixed; recover local default conflicts."""
    import errno
    try:
        port = int(configured_port) if configured_port is not None else 8000
        if not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise SystemExit("BUS_PORT must be a number between 1 and 65535.")
    local = host in ("127.0.0.1", "localhost")
    candidates = [port, 8001, 8080, 0] if configured_port is None and local else [port]
    for candidate in candidates:
        try:
            return BusHTTPServer((host, candidate), Handler)
        except OSError as exc:
            conflict = exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(exc, "winerror", None) in (10013, 10048)
            if not conflict or candidate == candidates[-1]:
                raise SystemExit(f"Cannot start Bus Track on {host}:{candidate}: {exc}. "
                                 'Choose another port in PowerShell with $env:BUS_PORT="8001" and run py server.py again.') from None
            print(f"Port {candidate} is occupied or blocked; trying another port.", flush=True)


def main():
    if (os.environ.get("BUS_REQUIRE_DATABASE") == "1" or os.environ.get("RENDER") == "true") and not accounts.DATABASE_URL:
        raise SystemExit("DATABASE_URL is required on Render. Configure Neon before starting; local SQLite would not persist.")
    host = os.environ.get("BUS_HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    server = bind_server(host, os.environ.get("BUS_PORT") or os.environ.get("PORT"))
    port = server.server_port
    first_password = os.environ.get("BUS_DRIVER_PASSWORD") or secrets.token_urlsafe(16)
    admin_password = os.environ.get('BUS_ADMIN_PASSWORD') or secrets.token_urlsafe(16)
    try:
        created = accounts.initialize(ROUTE, first_password)
        admin_created = accounts.initialize_admin(admin_password)
    except database.DatabaseError as exc:
        server.server_close()
        raise SystemExit(str(exc)) from None
    print("Database: " + ("PostgreSQL" if accounts.DATABASE_URL else "local SQLite"), flush=True)
    browser_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    print(f"Open Bus Track: http://{browser_host}:{port}/", flush=True)
    print("Admin login ID: ADMIN", flush=True)
    if admin_created:
        print("Initial admin password (save privately): " + admin_password, flush=True)
    print("Driver login ID: DRIVER02 (requires admin approval)", flush=True)
    if created:
        print("Initial driver password (save privately): " + first_password, flush=True)
    else:
        print("Use the existing driver password. Accounts and pickup preferences are saved.", flush=True)
    print("Route Bus 02, revised pickup schedule. In-memory state resets on restart. Ctrl+C to stop.", flush=True)
    alert_worker = pickup_alerts.start_worker(STATE, LOCK)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        alert_worker.set()
        server.server_close()

if __name__ == "__main__":
    main()
