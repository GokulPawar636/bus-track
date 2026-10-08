import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('bus_server', ROOT / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
# Never run local tests against a configured production database.
app.accounts.DATABASE_URL = ""

class BusServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.accounts.DB = pathlib.Path(cls.temp.name) / 'accounts.sqlite3'
        app.accounts.initialize(app.ROUTE, 'driver-test-password')
        app.accounts.initialize_admin('admin-test-password')
        cls.http = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        with app.LOCK:
            app.STATE.update(active=False, next_stop=0, location=None, direction='morning', trip_id=None, driver_id=None)
            app.WAITING.clear()
            app.LOGIN_ATTEMPTS.clear()
        with app.accounts.connect() as con:
            con.execute('DELETE FROM sessions')
            con.execute('DELETE FROM pickup_alerts')
            con.execute('DELETE FROM preferences')
            con.execute('DELETE FROM service_settings')
            con.execute("DELETE FROM accounts WHERE role='employee'")
        app.accounts.register({'id': 'EMP001', 'name': 'Test Employee', 'password': 'employee-test-password', 'stop': 0}, 16)
        app.accounts.register({'id': 'EMP002', 'name': 'Second Employee', 'password': 'employee-test-password', 'stop': 1}, 16)
        for identity in ['EMP001','EMP002','DRIVER02']:
            app.accounts.edit_profile('ADMIN',{'id':identity,'status':'approved'},16)
        self.tokens = {role: app.accounts.login(identity, password)[0] for role, identity, password in [('admin','ADMIN','admin-test-password'),('employee','EMP001','employee-test-password'),('second','EMP002','employee-test-password'),('driver','DRIVER02','driver-test-password')]}

    def request(self, path, data=None, role='employee'):
        headers = {'Content-Type': 'application/json'}
        if role:
            headers['Cookie'] = 'commute_session=' + self.tokens[role]
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_database_outage_returns_safe_503(self):
        from unittest.mock import patch
        with patch.object(app.accounts, 'connect', side_effect=app.database.DatabaseError('private connection detail')):
            for path,data in [('/healthz',None),('/api/me',None),('/api/login',{'id':'ADMIN','password':'test-password'})]:
                status,body=self.request(path,data)
                self.assertEqual(status,503)
                self.assertNotIn('private',body['error'])
        self.assertEqual(self.request('/healthz')[0],200)

    def test_roles_enforced_both_directions_and_anonymous_blocked(self):
        self.assertEqual(self.request('/api/state', role=None)[0], 401)
        for endpoint in ['start', 'stop', 'reached', 'location']:
            self.assertEqual(self.request('/api/'+endpoint, {}, 'employee')[0], 403)
        for endpoint in ['waiting', 'profile']:
            self.assertEqual(self.request('/api/'+endpoint, {}, 'driver')[0], 403)
        self.assertEqual(self.request('/api/profile', {'stop': 0, 'role': 'driver'})[0], 400)
        self.assertEqual(self.request('/api/register', {'id':'ESCALATE','name':'Test','password':'test-password','stop':0,'role':'driver'},None)[0],400)
        self.assertEqual(self.request('/api/me')[1]['user']['role'], 'employee')
        self.assertEqual(self.request('/api/me', role='driver')[1]['user']['mobile'], '7972557053')

    def test_pickup_and_identity_persist_after_logout_login_and_reinitialize(self):
        self.assertEqual(self.request('/api/profile', {'stop': 9})[0], 200)
        self.request('/api/logout', {})
        self.assertEqual(self.request('/api/me')[0],401)
        self.assertFalse(app.accounts.initialize(app.ROUTE, 'ignored-new-password'))
        self.tokens['employee'], user = app.accounts.login('EMP001', 'employee-test-password')
        self.assertEqual(user['stop'],9)
        self.assertEqual(user['name'],'Test Employee')
        self.assertEqual(self.request('/api/me')[1]['user']['stop'],9)
        with app.accounts.connect() as con:
            stored=con.execute("SELECT password FROM accounts WHERE id='EMP001'").fetchone()[0]
        self.assertNotEqual(stored,'employee-test-password')

    def test_waiting_deduplicates_uses_identity_and_saved_stop(self):
        for _ in range(3):
            self.assertEqual(self.request('/api/waiting', {'waiting':True,'client_id':'spoofed'})[0],200)
        self.assertEqual(self.request('/api/state', role='driver')[1]['counts'], [1]+[0]*15)
        self.assertEqual(self.request('/api/waiting', {'waiting':True,'stop':1})[0],400)
        self.request('/api/profile', {'stop':1})
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'],[0]*16)
        self.request('/api/waiting', {'waiting':True})
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'],[0,1]+[0]*14)
        self.request('/api/waiting', {'waiting':False})
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'],[0]*16)

    def test_reached_stop_clears_passengers_without_heartbeat_restore(self):
        self.request('/api/waiting', {'waiting':True})
        self.request('/api/waiting', {'waiting':True},'second')
        self.request('/api/start', {},'driver')
        self.request('/api/reached', {},'driver')
        self.request('/api/waiting', {'waiting':True,'heartbeat':True})
        self.assertIsNone(self.request('/api/state')[1]['waiting'])
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'],[0,1]+[0]*14)

    def test_gps_age_is_preserved_and_validation_enforced(self):
        self.request('/api/start', {},'driver')
        fix={'lat':19.875,'lng':75.35,'accuracy':10,'timestamp':app.now()-25000}
        for _ in range(2):
            self.assertEqual(self.request('/api/location',fix,'driver')[0],200)
        self.assertEqual(self.request('/api/state')[1]['location']['timestamp'],fix['timestamp'])
        self.assertEqual(self.request('/api/location',{**fix,'lat':100},'driver')[0],400)
        self.request('/api/stop',{},'driver')
        self.assertEqual(self.request('/api/location',fix,'driver')[0],400)

    def test_complete_revised_route_and_final_stop_counts(self):
        expected=['07:50','07:53','07:57','08:01','08:04','08:08','08:10','08:13','08:15','08:17','08:20','08:21','08:23','08:34','08:42','08:50']
        self.assertEqual([s['pickup'] for s in self.request('/api/state')[1]['stops']],expected)
        self.request('/api/profile',{'stop':15})
        self.request('/api/waiting',{'waiting':True})
        self.request('/api/start',{},'driver')
        for index in range(15):
            self.assertEqual(self.request('/api/state')[1]['next_stop'],index)
            self.request('/api/reached',{},'driver')
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'][15],1)
        self.request('/api/reached',{},'driver')
        self.assertFalse(self.request('/api/state')[1]['active'])
        self.request('/api/start',{},'driver')
        self.assertEqual(self.request('/api/state')[1]['next_stop'],0)

    def test_return_trip_reverses_stops_and_maps_employee_waiting(self):
        self.assertEqual(self.request('/api/start',{'direction':'return'},'driver')[0],200)
        self.request('/api/profile',{'stop':3})
        self.request('/api/waiting',{'waiting':True})
        state=self.request('/api/state',role='driver')[1]
        self.assertEqual(state['direction'],'return')
        self.assertEqual(state['next_stop'],1)
        self.assertEqual([stop['name'] for stop in state['stops']],[stop['name'] for stop in reversed(app.STOPS)])
        self.assertEqual(self.request('/api/state',role='employee')[1]['waiting'],len(app.STOPS)-1-3)
        self.assertEqual(state['counts'][len(app.STOPS)-1-3],1)
        self.assertEqual(self.request('/api/start',{'direction':'morning'},'driver')[0],400)
        self.assertEqual(self.request('/api/reached',{},'driver')[0],200)
        self.assertEqual(self.request('/api/state',role='driver')[1]['next_stop'],2)
        self.request('/api/stop',{},'driver')
        self.assertEqual(self.request('/api/start',{'direction':'morning'},'driver')[0],200)
        morning=self.request('/api/state',role='driver')[1]
        self.assertEqual(morning['direction'],'morning')
        self.assertEqual(morning['next_stop'],0)
        self.assertIsNone(morning['location'])

    def test_pending_and_revoked_users_cannot_use_service(self):
        app.accounts.register({'id':'PENDING01','name':'Pending Employee','password':'pending-password','stop':2},16)
        self.tokens['pending']=app.accounts.login('PENDING01','pending-password')[0]
        self.assertEqual(self.request('/api/me',role='pending')[1]['user']['status'],'pending')
        for path,data in [('/api/state',None),('/api/waiting',{'waiting':True}),('/api/profile',{'stop':3})]:
            self.assertEqual(self.request(path,data,'pending')[0],403)
        self.assertEqual(self.request('/api/admin/accounts',role='employee')[0],403)
        self.assertEqual(self.request('/api/admin/update',{'id':'PENDING01','status':'approved'},'employee')[0],403)
        self.assertEqual(self.request('/api/admin/update',{'id':'PENDING01','status':'approved'},'admin')[0],200)
        self.assertEqual(self.request('/api/state',role='pending')[0],200)
        self.request('/api/waiting',{'waiting':True},'pending')
        self.request('/api/admin/update',{'id':'PENDING01','status':'suspended','review_note':'Assignment review'},'admin')
        self.assertEqual(self.request('/api/state',role='pending')[0],403)
        self.assertNotIn('PENDING01',app.WAITING)
        self.assertEqual(self.request('/api/me',role='pending')[1]['user']['review_note'],'Assignment review')
        self.assertEqual(self.request('/api/admin/update',{'id':'PENDING01','role':'admin'},'admin')[0],400)

    def test_driver_application_and_admin_profile_edits(self):
        response=self.request('/api/register',{'id':'DRIVER03','name':'New Driver','role':'driver','mobile':'9876543210','password':'driver-three-password'},None)
        self.assertEqual(response[0],200)
        self.assertEqual(response[1]['user']['status'],'pending')
        self.tokens['newdriver']=app.accounts.login('DRIVER03','driver-three-password')[0]
        self.assertEqual(self.request('/api/start',{},'newdriver')[0],403)
        self.assertEqual(self.request('/api/admin/update',{'id':'DRIVER03','status':'rejected','review_note':''},'admin')[0],400)
        self.request('/api/admin/update',{'id':'DRIVER03','status':'approved','name':'Approved Driver','mobile':'9876543211'},'admin')
        self.assertEqual(self.request('/api/start',{},'newdriver')[0],200)
        self.assertEqual(self.request('/api/state')[1]['driver']['mobile'],'9876543211')
        self.request('/api/admin/update',{'id':'DRIVER03','status':'suspended','review_note':'Duty ended'},'admin')
        self.assertFalse(app.STATE['active'])
        self.assertEqual(self.request('/api/location',{},'newdriver')[0],403)
        with app.accounts.connect() as con:
            self.assertGreater(con.execute('SELECT count(*) FROM account_audit').fetchone()[0],0)

    def test_role_settings_persist_and_reject_privilege_changes(self):
        saved=self.request('/api/settings',{'stop':5,'preferences':{'default_map':'route','follow_bus':False}})
        self.assertEqual(saved[0],200)
        self.assertEqual(saved[1]['user']['stop'],5)
        self.assertFalse(self.request('/api/settings')[1]['preferences']['follow_bus'])
        self.assertEqual(self.request('/api/settings',{'preferences':{'confirm_stop':False}})[0],400)
        self.assertEqual(self.request('/api/settings',{'service':{'registration_open':False}})[0],400)
        self.assertEqual(self.request('/api/settings',{'preferences':{'confirm_stop':False}},'driver')[0],200)
        self.assertEqual(self.request('/api/settings',{'stop':3},'driver')[0],400)
        self.assertEqual(self.request('/api/settings',{'role':'admin'})[0],400)
        self.request('/api/logout',{})
        self.tokens['employee']=app.accounts.login('EMP001','employee-test-password')[0]
        self.assertEqual(self.request('/api/settings')[1]['preferences']['default_map'],'route')

    def test_admin_transport_settings_have_real_effect(self):
        settings={'registration_open':False,'helpdesk':'9876543210','gps_stale_seconds':45,'waiting_expiry_seconds':60}
        self.assertEqual(self.request('/api/settings',{'service':settings},'admin')[0],200)
        self.assertEqual(self.request('/api/register',{'id':'BLOCKED','name':'Blocked Person','password':'valid-password','stop':0},None)[0],403)
        self.assertEqual(self.request('/api/state')[1]['service']['gps_stale_seconds'],45)
        app.WAITING['EMP001']={'stop':0,'at':app.now()-61000}
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'][0],0)
        self.assertEqual(self.request('/api/settings',{'service':{'gps_stale_seconds':1}},'admin')[0],400)
        self.assertEqual(self.request('/api/settings',{'service':{'registration_open':True}},'admin')[0],200)
        self.assertEqual(self.request('/api/register',{'id':'ALLOWED','name':'Allowed Person','password':'valid-password','stop':0},None)[1]['user']['status'],'pending')

    def test_password_change_requires_current_password_and_revokes_sessions(self):
        other=app.accounts.login('EMP001','employee-test-password')[0]
        self.assertEqual(self.request('/api/password',{'current_password':'wrong','new_password':'new-employee-password'})[0],400)
        self.assertEqual(self.request('/api/password',{'current_password':'employee-test-password','new_password':'new-employee-password'})[0],200)
        self.assertEqual(self.request('/api/me')[0],401)
        self.assertIsNone(app.accounts.session({'Cookie':'commute_session='+other}))
        with self.assertRaises(ValueError):
            app.accounts.login('EMP001','employee-test-password')
        self.tokens['employee']=app.accounts.login('EMP001','new-employee-password')[0]
        self.assertEqual(self.request('/api/me')[0],200)

    def test_previous_stop_notifications_are_opt_in_and_deduplicated(self):
        self.request('/api/profile',{'stop':1})
        self.request('/api/settings',{'preferences':{'previous_stop_notification':True}})
        self.request('/api/start',{},'driver')
        self.request('/api/reached',{},'driver')
        alerts=self.request('/api/state')[1]['pickup_alerts']
        self.assertEqual(len(alerts),1)
        self.assertIn('one stop away',alerts[0]['message'])
        self.assertIn('UACPL - B-34',alerts[0]['message'])
        self.assertIn('OASIS Chowk',alerts[0]['message'])
        self.assertEqual(self.request('/api/state',role='second')[1]['pickup_alerts'],[])
        self.assertEqual(app.pickup_alerts.notifications({'id':'EMP001','role':'employee','stop':2},app.STATE),[])
        app.pickup_alerts.queue(app.STATE['trip_id'],0,app.STOPS,'02')
        self.assertEqual(len(self.request('/api/state')[1]['pickup_alerts']),1)
        self.request('/api/reached',{},'driver')
        self.assertEqual(self.request('/api/state')[1]['pickup_alerts'],[])

    def test_call_requires_configured_service_and_approved_phone(self):
        from unittest.mock import patch
        with patch.object(app.pickup_alerts,'configured',return_value=False):
            self.assertEqual(self.request('/api/settings',{'preferences':{'previous_stop_call':True}})[0],400)
        with patch.object(app.pickup_alerts,'configured',return_value=True):
            self.assertEqual(self.request('/api/settings',{'preferences':{'previous_stop_call':True}})[0],400)
        self.assertEqual(self.request('/api/settings',{'preferences':{'previous_stop_call':True}},'driver')[0],400)

    def test_voice_call_attempt_once_and_opt_out_cancels_queue(self):
        from unittest.mock import patch
        self.request('/api/admin/update',{'id':'EMP001','mobile':'9876543210'},'admin')
        self.request('/api/profile',{'stop':1})
        with patch.object(app.pickup_alerts,'configured',return_value=True), patch.object(app.pickup_alerts,'place_call',return_value='CA-test') as call:
            self.assertEqual(self.request('/api/settings',{'preferences':{'previous_stop_call':True}})[0],200)
            self.request('/api/start',{},'driver');self.request('/api/reached',{},'driver')
            app.pickup_alerts.deliver_once(app.STATE,app.LOCK)
            app.pickup_alerts.deliver_once(app.STATE,app.LOCK)
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.args[0],'+919876543210')
            app.pickup_alerts.queue(app.STATE['trip_id'],0,app.STOPS,'02')
            app.pickup_alerts.deliver_once(app.STATE,app.LOCK)
            self.assertEqual(call.call_count,1)
            with app.accounts.connect() as con:
                con.execute("UPDATE pickup_alerts SET status='queued'")
            self.request('/api/settings',{'preferences':{'previous_stop_call':False}})
            app.pickup_alerts.deliver_once(app.STATE,app.LOCK)
            self.assertEqual(call.call_count,1)
            with app.accounts.connect() as con:
                self.assertEqual(con.execute('SELECT status FROM pickup_alerts').fetchone()[0],'cancelled')

    def test_unanswered_provider_error_is_not_retried(self):
        from unittest.mock import patch
        self.request('/api/admin/update',{'id':'EMP001','mobile':'9876543210'},'admin')
        self.request('/api/profile',{'stop':1})
        with patch.object(app.pickup_alerts,'configured',return_value=True), patch.object(app.pickup_alerts,'place_call',side_effect=TimeoutError) as call:
            self.request('/api/settings',{'preferences':{'previous_stop_call':True}})
            self.request('/api/start',{},'driver');self.request('/api/reached',{},'driver')
            app.pickup_alerts.deliver_once(app.STATE,app.LOCK);app.pickup_alerts.deliver_once(app.STATE,app.LOCK)
            self.assertEqual(call.call_count,1)
            with app.accounts.connect() as con:
                self.assertEqual(con.execute('SELECT status FROM pickup_alerts').fetchone()[0],'failed_or_unknown')

    def test_legacy_database_migrates_without_losing_profiles(self):
        import sqlite3
        original=app.accounts.DB
        try:
            app.accounts.DB=pathlib.Path(self.temp.name)/'legacy.sqlite3'
            with sqlite3.connect(app.accounts.DB) as con:
                con.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY,name TEXT,role TEXT CHECK(role IN ('employee','driver')),mobile TEXT,stop INTEGER,salt TEXT,password TEXT)")
                salt='legacy-salt'
                con.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?)',('LEGACY','Legacy Person','employee','',9,salt,app.accounts.password_hash('legacy-password',salt)))
            con.close()
            app.accounts.initialize(app.ROUTE,'new-driver-password')
            app.accounts.initialize_admin('legacy-admin-password')
            _, user=app.accounts.login('LEGACY','legacy-password')
            self.assertEqual(user['stop'],9)
            self.assertEqual(user['name'],'Legacy Person')
            self.assertEqual(user['status'],'pending')
            self.assertEqual(app.accounts.login('ADMIN','legacy-admin-password')[1]['status'],'approved')
        finally:
            app.accounts.DB=original

    def test_expiry_private_database_and_login_failures(self):
        app.WAITING['EMP001']={'stop':0,'at':app.now()-121000}
        self.assertEqual(self.request('/api/state',role='driver')[1]['counts'],[0]*16)
        for name in ['server.py','accounts.py','accounts.sqlite3','../accounts.sqlite3']:
            self.assertEqual(self.request('/'+name)[0],404)
        self.assertEqual(self.request('/api/login',{'id':'DRIVER02','password':'wrong'},None)[0],400)

if __name__ == '__main__':
    unittest.main(verbosity=2)
