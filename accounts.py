"""Persistent accounts and fixed-role sessions for the single-bus app."""
import os
import database
import hashlib
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from pathlib import Path

DB = Path(__file__).with_name('accounts.sqlite3')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()


def connect():
    return database.connect(DB, DATABASE_URL)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()

def public(row):
    return {key: row[key] for key in ('id', 'name', 'role', 'mobile', 'stop', 'status', 'review_note')}

def create_schema(con):
    existing = con.table_exists("accounts")
    if not con.postgres and existing and 'status' not in [row['name'] for row in con.execute('PRAGMA table_info(accounts)')]:
        con.execute('ALTER TABLE accounts RENAME TO accounts_legacy')
    con.execute("""CREATE TABLE IF NOT EXISTS accounts (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('employee','driver','admin')),
      mobile TEXT NOT NULL DEFAULT '', stop INTEGER, salt TEXT NOT NULL, password TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','suspended')),
      review_note TEXT NOT NULL DEFAULT '')""")
    if not con.postgres and con.table_exists("accounts_legacy"):
        con.execute("INSERT INTO accounts (id,name,role,mobile,stop,salt,password) SELECT id,name,role,mobile,stop,salt,password FROM accounts_legacy")
        con.execute('DROP TABLE accounts_legacy')
    con.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)')
    con.execute(f'CREATE TABLE IF NOT EXISTS account_audit (id {con.identity}, actor TEXT NOT NULL, account TEXT NOT NULL, action TEXT NOT NULL, at DOUBLE PRECISION NOT NULL)')
    import preferences
    preferences.initialize(con)


def initialize(route, driver_password):
    with connect() as con:
        create_schema(con)
        if con.execute("SELECT 1 FROM accounts WHERE id='DRIVER02'").fetchone():
            return False
        salt = secrets.token_hex(16)
        con.execute('INSERT INTO accounts (id,name,role,mobile,stop,salt,password) VALUES (?,?,?,?,?,?,?)', ('DRIVER02', route['driver'], 'driver', route['phone'], None, salt, password_hash(driver_password, salt)))
        return True

def register(data, stop_count):
    role = data.get('role', 'employee')
    if role not in ('employee', 'driver') or set(data) - {'id','name','password','stop','mobile','role'}:
        raise ValueError('Only employee and driver profile applications are allowed.')
    mobile = str(data.get('mobile', '')).strip()
    if (role == 'driver' or mobile) and not re.fullmatch(r'[0-9]{10}', mobile):
        raise ValueError('Enter a 10-digit mobile number.')
    identity = str(data.get('id', '')).strip().upper()
    name = str(data.get('name', '')).strip()
    password = data.get('password', '')
    stop = data.get('stop')
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{2,39}', identity) or identity.startswith('ADMIN') or (role == 'employee' and identity.startswith('DRIVER')):
        raise ValueError('Use a valid employee ID (3-40 letters, numbers, underscore or hyphen).')
    if not 2 <= len(name) <= 80:
        raise ValueError('Enter your full name (2-80 characters).')
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ValueError('Use a password of 10-128 characters.')
    if role == 'driver':
        stop = None
    elif type(stop) is not int or not 0 <= stop < stop_count:
        raise ValueError('Choose a valid pickup stop.')
    salt = secrets.token_hex(16)
    with connect() as con:
        try:
            con.execute('INSERT INTO accounts (id,name,role,mobile,stop,salt,password) VALUES (?,?,?,?,?,?,?)', (identity, name, role, mobile, stop, salt, password_hash(password, salt)))
        except sqlite3.IntegrityError:
            raise ValueError('That account ID is already registered. Sign in instead.')
    return identity

def login(identity, password):
    if not isinstance(identity, str) or not isinstance(password, str) or len(password)>128:
        raise ValueError('Account ID or password is incorrect.')
    with connect() as con:
        row = con.execute('SELECT * FROM accounts WHERE id=?', (identity.strip().upper(),)).fetchone()
        computed = password_hash(password, row['salt'] if row else 'unregistered-account')
        if not row or not secrets.compare_digest(row['password'], computed):
            raise ValueError('Account ID or password is incorrect.')
        token = secrets.token_urlsafe(32)
        con.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        con.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time()+604800))
        return token, public(row)

def cookie_token(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie', ''))
        return cookie['commute_session'].value if 'commute_session' in cookie else ''
    except Exception:
        return ''

def session(headers):
    token = cookie_token(headers)
    if not token:
        return None
    with connect() as con:
        row = con.execute('SELECT accounts.* FROM sessions JOIN accounts ON accounts.id=sessions.account WHERE sessions.token=? AND sessions.expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return public(row) if row else None

def logout(headers):
    with connect() as con:
        con.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(cookie_token(headers).encode()).hexdigest(),))

def save_stop(identity, stop, count):
    if type(stop) is not int or not 0 <= stop < count:
        raise ValueError('Choose a valid pickup stop.')
    with connect() as con:
        con.execute("UPDATE accounts SET stop=? WHERE id=? AND role='employee'", (stop, identity))


def initialize_admin(password):
    with connect() as con:
        if con.execute("SELECT 1 FROM accounts WHERE id='ADMIN'").fetchone():
            return False
        salt = secrets.token_hex(16)
        con.execute("INSERT INTO accounts (id,name,role,mobile,stop,salt,password,status) VALUES (?,?,?,?,?,?,?,?)", ('ADMIN','Transport Admin','admin','',None,salt,password_hash(password,salt),'approved'))
        return True

def list_profiles():
    with connect() as con:
        return [public(row) for row in con.execute("SELECT * FROM accounts WHERE role!='admin' ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, LOWER(name)")]

def edit_profile(actor, data, stop_count):
    if set(data) - {'id','name','mobile','stop','status','review_note'}:
        raise ValueError('Roles and account IDs cannot be changed.')
    with connect() as con:
        admin = con.execute("SELECT * FROM accounts WHERE id=? AND role='admin' AND status='approved'", (actor,)).fetchone()
        if not admin:
            raise ValueError('Admin access required.')
        row = con.execute("SELECT * FROM accounts WHERE id=? AND role!='admin'", (data.get('id'),)).fetchone()
        if not row:
            raise ValueError('Employee or driver profile not found.')
        name = str(data.get('name',row['name'])).strip()
        mobile = str(data.get('mobile',row['mobile'])).strip()
        stop = data.get('stop',row['stop'])
        status = data.get('status',row['status'])
        note = str(data.get('review_note',row['review_note'])).strip()
        if not 2 <= len(name) <= 80 or len(note)>400:
            raise ValueError('Name must be 2-80 characters; review note may be up to 400 characters.')
        if (row['role']=='driver' or mobile) and not re.fullmatch(r'[0-9]{10}',mobile):
            raise ValueError('Enter a 10-digit mobile number.')
        if row['role']=='employee' and (type(stop) is not int or not 0<=stop<stop_count):
            raise ValueError('Choose a valid pickup stop.')
        if row['role']=='driver':
            stop=None
        if status not in ('pending','approved','rejected','suspended'):
            raise ValueError('Invalid approval status.')
        if status in ('rejected','suspended') and not note:
            raise ValueError('Add a reason for rejection or suspension.')
        con.execute('UPDATE accounts SET name=?,mobile=?,stop=?,status=?,review_note=? WHERE id=?',(name,mobile,stop,status,note,row['id']))
        if row['role']=='employee' and mobile != row['mobile']:
            import json
            pref=con.execute('SELECT value FROM preferences WHERE account=?',(row['id'],)).fetchone()
            if pref:
                values=json.loads(pref['value']);values['previous_stop_call']=False
                con.execute('UPDATE preferences SET value=? WHERE account=?',(json.dumps(values),row['id']))
        changes={key:{'before':row[key],'after':value} for key,value in {'name':name,'mobile':mobile,'stop':stop,'status':status,'review_note':note}.items() if row[key]!=value}
        import json
        con.execute('INSERT INTO account_audit (actor,account,action,at) VALUES (?,?,?,?)',(actor,row['id'],json.dumps(changes),time.time()))
        return public(con.execute('SELECT * FROM accounts WHERE id=?',(row['id'],)).fetchone())


def driver_contact(identity='DRIVER02'):
    with connect() as con:
        row=con.execute("SELECT id,name,mobile FROM accounts WHERE id=? AND role='driver'",(identity,)).fetchone()
        return dict(row) if row else None
