# Bus 02 - admin approval, employee and driver accounts

## Render + Neon PostgreSQL

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Render Blueprint, Neon connection settings, and migration of existing local accounts. Set `DATABASE_URL` to enable PostgreSQL; without it, local development uses SQLite. Install PostgreSQL support with `py -m pip install -r requirements.txt`.

## Start

Install Python 3.10 or newer, then open PowerShell and run:

```powershell
cd "D:\USERS\gokul.pawar\Downloads\Bus Track"
py server.py
```

Open the **Open Bus Track** URL printed in PowerShell. The default is http://localhost:8000; if that port is occupied or blocked, local startup automatically chooses another port. Keep PowerShell open while using the app; press Ctrl+C to stop it. SQLite-only local use needs no extra packages. Neon PostgreSQL requires `py -m pip install -r requirements.txt`. Use the system Python with `py server.py`. If `py` is unavailable but Python is installed, use `python server.py`.

Use the Python server, not Live Server or a direct double-click on `index.html`, because sign-in and tracking require the backend. A JSON `{"detail":"Not Found"}` page on port 8000 belongs to another service; use the URL printed by Bus Track. To choose a fixed port, run `$env:BUS_PORT="8001"` before `py server.py`, then open http://localhost:8001.

Employee: select Apply for access and enter your name, employee ID, password (10+ characters), and pickup stop. Driver applicants select Driver and provide their name, ID, password, and a 10-digit mobile number. Every application starts pending. Signing in shows the approval screen until an admin accepts it. After approval, later logins restore the same profile and saved pickup. Use Change stop / Save pickup to update it. This cancels the old waiting selection so the employee can mark themselves waiting at the new stop.

Driver: sign in with ID `DRIVER02`. On the first server start, the terminal prints a generated password; save it privately. Set `BUS_DRIVER_PASSWORD` before the first start if you want to choose that initial password. Subsequent starts keep the existing credentials. The initial driver name and mobile come from the supplied Route Bus 02 timetable. DRIVER02 also requires admin approval. Do not share driver credentials with employees.

Admin: sign in with ID `ADMIN`. The first startup after this update prints a generated admin password in the terminal. Save it privately. Set `BUS_ADMIN_PASSWORD` before that first startup to choose the initial password. Subsequent starts retain the existing admin credentials. Admin accounts cannot be registered through the public application form.

In the admin screen, search/filter profiles, open Review / Edit, check the name, mobile and employee pickup, and choose Approved before saving. Rejected and Suspended require a reason shown to the account holder. Approval, rejection, suspension and edits persist in the configured database; changes are recorded in `account_audit` with the admin ID and timestamp. A suspended driver running the trip stops sharing; employee suspension clears their waiting entry. Pending accounts can check approval status and sign out, but cannot read live service data or perform trip/pickup actions. Existing sessions are checked on every service request.

**Existing installation:** restart the Python server to run the database migration. Names, password hashes, sessions and pickup preferences are preserved, but all pre-approval employee/driver accounts become Pending until accepted by ADMIN. Back up `accounts.sqlite3` before deploying changes. Approval is not required again on subsequent restarts.

Accounts have a fixed role. There is no role switch. The backend rejects employee requests to start/stop trips, advance stops, or upload GPS. It rejects driver requests to edit employee pickup preferences or mark themselves waiting. Waiting entries use the authenticated employee ID, not a submitted browser ID.

For a new database only, choose initial passwords with `$env:BUS_ADMIN_PASSWORD="your-admin-password"` and `$env:BUS_DRIVER_PASSWORD="your-driver-password"` before starting. These variables do not reset existing passwords. Change an existing password in the signed-in profile Settings.


## Saved data and sign-in

With `DATABASE_URL` set, PostgreSQL stores accounts, password hashes, saved pickup stops, and expiring sessions. Without it, the same data lives in local `accounts.sqlite3`. The following file-backup notes apply to SQLite; see DEPLOYMENT.md for Neon. It is excluded from Git and cannot be downloaded through the web server. Passwords use salted PBKDF2-SHA256; session cookies are HttpOnly and SameSite=Strict. Sessions expire after seven days; signing out revokes the current session. Keep a backup of the database. Trip location and waiting counts are still in memory and reset when the server restarts.

Employees and drivers may apply themselves, but an admin must verify and approve each profile before service access. Employee IDs and driver details are not automatically checked against a company roster; the admin review is the verification step. Roles and account IDs cannot be changed through profile editing.

Opening index.html as a file only shows the sign-in page with server startup instructions. A static-file preview alone cannot provide account isolation. Use the Python server for the application.

## Settings for each profile

Open **Settings** beside Sign out. Preferences are stored in the configured database per account and restored on login.

- Employee: saved pickup, default map (automatic / route / live), follow-bus preference, and password change. A changed pickup clears the current waiting selection.
- Driver: default map, follow-bus preference, confirmation before advancing a stop (enabled by default), and password change. Settings never start GPS automatically.
- Admin: default profile filter; whether new applications are accepted; transport helpdesk number; GPS stale threshold (20-120 seconds, default 20); inactive waiting expiry (60-600 seconds, default 120); and password change. Helpdesk is shown as a call link to employees/drivers. Service thresholds take effect on subsequent updates. Closing registration does not block existing approved accounts.

Location polling remains fixed at five seconds. Admin approval cannot be disabled. Names, roles, account IDs and approval decisions remain admin-managed. Pending/rejected/suspended users can change their own password but cannot save service preferences or use the service.

Changing a password requires the current password, a new password of 10-128 characters and matching confirmation. Other sessions are revoked; the session making the change receives a replacement cookie. Passwords are never included in settings responses or audit entries. Failed password-change attempts are rate-limited.

## Optional one-stop-away notification and voice call

Employee Settings has two separate, default-off switches: Notify me when the bus reaches the previous stop, and Give me an automated voice call. For a pickup at OASIS Chowk, the trigger is the driver's Reached stop confirmation at UACPL - B-34. The message says Bus 02 has reached the previous stop and is one stop away from the employee's pickup. It never says the bus has reached the employee's own stop. The first stop has no previous-stop alert.

The event is based on the driver's explicit stop confirmation, not an unverified GPS geofence. It applies to approved employees assigned to the next stop, even if they have not pressed I'm waiting. Both channels are independently optional. Events are deduplicated per trip, employee and channel. Pausing and resuming does not send the same event again. A completed route's next Start creates a new trip ID. Trip state remains in memory, so restarting the server starts a new trip lifecycle.

Notifications appear inside the open application within the normal five-second refresh. The switch requests browser notification permission, and device notifications are also shown when supported and allowed. Denial leaves in-app notifications available. This is not closed-app push delivery: browser suspension or closing the app can prevent notification delivery. Alerts expire after two minutes, and are no longer shown once the bus has processed the employee's own stop. Browser notifications are deduplicated in local browser storage; another device may show its own notification.

### Enable real automated calls

The adapter uses Twilio Programmable Voice. Before starting `py server.py`, configure these environment variables privately on the server:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER` (your Twilio calling number in international +country format)

The Twilio account must be able to call the target numbers. This adapter uses the admin-approved employee mobile from the profile and prefixes +91, matching this India-based route. The employee cannot supply an arbitrary call destination in settings. Admin mobile edits switch voice opt-in off so the employee must enable it again. The Settings screen disables new call opt-ins until a provider is configured and a 10-digit approved mobile exists. No account secrets are sent to browsers. See https://www.twilio.com/docs/voice/api/call-resource for provider setup and outbound calling details.

The background worker checks consent, approval, pickup assignment, destination, active trip and event age again before placing a call. Opting out, suspension, changing stops, passing the pickup, or stopping the trip cancels queued calls. An already submitted provider call cannot be recalled by this app. Queued work older than two minutes is cancelled.

The database `pickup_alerts` table records event and call-attempt states. Submitted means accepted by the provider, not answered by the employee. Calls are attempted at most once; ambiguous network failures are not retried to avoid duplicate calls. A crash during submission can leave an attempting record for review; it is not automatically retried. Provider delivery callbacks and closed-app push are not implemented. Testing uses mocked call delivery and never makes real calls.

## Maps and tracking

Route & stops displays all 16 full stop names and revised morning times, with next-stop and saved-pickup highlights. It is a route-order diagram, not geographic positioning. Live bus map displays the driver's actual GPS fix on OpenStreetMap. Fresh GPS automatically opens this map unless you have manually chosen a map view. Saved pickup changes also sync to the employee's other open sessions at the next refresh. It needs internet access. Expand map enlarges either display; Escape closes it. Mobile route cards use two columns and scroll within the map to keep labels readable.

The return trip departs UACPL - Auric Plant at 5:40 PM and follows the morning stops in reverse. The driver selects Return before starting location sharing; the stop list follows that direction. Return stop arrival times are not estimated. The timetable does not provide exact pickup coordinates, and stop coordinates remain null in `route-data.js`; supply and verify stop pins before expecting stop markers or stop-by-stop ETAs. The map shows the driver's latest reported GPS fix with its reported accuracy, not an inferred or traffic-adjusted position. The app does not invent roads, coordinates, or GPS-based ETAs.

Driver: tap Start sharing bus location, allow precise location permission for the site, and keep the page open and the phone awake. On the phone, enable system Location and the browser's Precise location setting; a clear view of the sky and disabling battery saver can improve GPS reception. Employees fetch shared state every five seconds. The map shows the phone's reported fix and its reported horizontal accuracy radius; a fresh fix with a reported radius over 100 metres is flagged as weak. This radius is a device estimate, not a guarantee of the bus's exact position. GPS samples preserve their original timestamp and become visibly stale after the admin-configured threshold (20 seconds by default). Mobile browsers can suspend background pages; reliable locked-phone tracking needs a native driver app.

A native Android tracker is not included in this project. Web tracking requires the driver page to stay open; phone access to GPS requires HTTPS (localhost is allowed for local testing).

Employee: tap I'm waiting when at the saved pickup. Duplicate taps cannot add multiple passengers. Waiting entries expire after the configured inactivity period (two minutes by default) without heartbeat or when the driver completes the stop. Driver: Reached stop clears that stop's count and advances to the next. After the last stop, the trip completes. A new Start resets a completed trip. Driver logout stops location sharing; employee logout clears that employee's waiting entry.

## Host the website on the internet

This is a server-backed Python website, not a static site: deploy the whole project to an always-on Linux VPS or server. The example below uses Ubuntu, systemd, a domain name, and Caddy for HTTPS. A static hosting service alone will not run the Python API or save accounts.

1. **Prepare a server and domain.** Create an Ubuntu VPS with a public IP address and a DNS `A` record for your domain pointing to that IP. Allow inbound SSH, HTTP (port 80), and HTTPS (port 443) in the provider firewall and the server firewall (if UFW is enabled). Connect over SSH.
2. **Install the runtime and HTTPS proxy.** Install Python 3.10 or newer and Caddy using their official Ubuntu installation instructions. Caddy will obtain and renew the TLS certificate automatically once the domain resolves and ports 80/443 are reachable.
3. **Copy the application.** From PowerShell in the parent directory containing this project, upload the complete project folder, including `server.py`, `accounts.py`, `pickup_alerts.py`, `preferences.py`, `route-data.js`, and the website files:

   ```powershell
   scp -r ".\Bus Track" deploy@YOUR_SERVER_IP:/tmp/bus-track-upload
   ```

   On the server, create a dedicated account and install the files:

   ```sh
   sudo useradd --system --home-dir /opt/bus-track --create-home --shell /usr/sbin/nologin busapp
   sudo cp -a /tmp/bus-track-upload/. /opt/bus-track/
   sudo chown -R busapp:busapp /opt/bus-track
   ```

   If migrating an existing installation, copy its `accounts.sqlite3` into `/opt/bus-track/` before starting the service and preserve its ownership. This database holds account profiles, password hashes, sessions, and preferences. Do not place it in a public static directory or overwrite it during later code updates. Back it up securely. In-memory trip/location and waiting state resets when the Python process restarts.
4. **Set server secrets.** Create a root-only environment file:

   ```sh
   sudo install -m 600 -o root -g root /dev/null /etc/bus-track.env
   sudo nano /etc/bus-track.env
   ```

   Add `BUS_HOST=127.0.0.1`, `BUS_PORT=8000`, and `BUS_HTTPS=1`. Before the first-ever start with a new database, also set `BUS_ADMIN_PASSWORD` and `BUS_DRIVER_PASSWORD` to strong, private initial passwords; save them in your password manager. These initial-password variables do not change passwords for accounts already created in the database. If you omit them on first start, the generated passwords appear in the service log. Never commit secrets or share them with drivers/employees. Add the `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` variables only if enabling automated calls.
5. **Run Python under systemd.** Create `/etc/systemd/system/bus-track.service` as root with:

   ```ini
   [Unit]
   Description=Bus 02 tracking website
   After=network.target

   [Service]
   Type=simple
   User=busapp
   Group=busapp
   WorkingDirectory=/opt/bus-track
   EnvironmentFile=/etc/bus-track.env
   ExecStart=/usr/bin/python3 -B /opt/bus-track/server.py
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

   Enable and start it with `sudo systemctl daemon-reload`, `sudo systemctl enable --now bus-track`, then check `sudo systemctl status bus-track` and `sudo journalctl -u bus-track -n 100 --no-pager`.
6. **Configure Caddy.** Set `/etc/caddy/Caddyfile` to the domain and local proxy:

   ```caddy
   bus.example.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```

   Replace `bus.example.com` with your domain, then run `sudo systemctl reload caddy`. Keep port 8000 private; expose only SSH, 80, and 443 to the internet.
7. **Verify and operate.** Visit `https://bus.example.com`, sign in with the ADMIN profile, and approve the intended employee/driver accounts. Test sign-in, map loading, and driver location from a phone using that same HTTPS URL. Browser GPS requires HTTPS and a reachable server. Check logs with `sudo journalctl -u bus-track -f`. For code updates, stop the service, back up `accounts.sqlite3`, replace the application code without deleting the database, restore `busapp` ownership, and restart the service.

If TLS terminates at a different reverse proxy, keep the Python server bound to localhost when the proxy runs on the same machine, and retain `BUS_HTTPS=1` so session cookies are marked secure. Only set `BUS_HOST=0.0.0.0` when a proxy on another machine must connect to the Python server, and restrict port 8000 at the firewall to that proxy. Never expose the Python server directly to the public internet.

## Phones and HTTPS

Use the same HTTPS host for all phones. Browser GPS requires HTTPS except on localhost. The setup above uses `BUS_HTTPS=1` so session cookies require HTTPS. HTTP on a LAN IP alone is not sufficient for phone GPS. Public hosting and TLS must be configured by the deployer.

## Timetable

`route-data.js` is shared by the browser and Python server. Bus 02: MH 20 GZ 7053. Driver: Pundlik Gangaram Jadhav, 7972557053. Revised pickups run from UACPL - B-34 at 07:50 AM to UACPL - Auric Plant at 08:50 AM (IST). The return departs at 5:40 PM from UACPL - Auric Plant, following the morning stops in reverse. No return stop times or stop coordinates were supplied, so return ETAs are not shown.

## Verification

`py -B -m unittest discover -s tests -v` tests permissions in both directions, password/session behavior, persistent pickup changes, approval/rejection/suspension, old-database migration, role-specific settings, effective transport controls, password-change session revocation, waiting identity/counts, GPS age, and the entire revised route using a temporary database.

`py -B tests/browser_smoke.py` uses installed Windows Chrome to check actual sign-in, pending access restrictions, admin reviews for employees and drivers, account-specific interfaces, saved pickups across login, readable map labels, expanded maps, and server denials. It never creates real employee accounts.
