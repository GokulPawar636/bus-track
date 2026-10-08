"""Validated, persistent per-account and transport settings."""
import json
import re
import secrets
import time
import accounts
import pickup_alerts

SERVICE_DEFAULTS={'registration_open':True,'helpdesk':'','gps_stale_seconds':20,'waiting_expiry_seconds':120}

def initialize(con):
    pickup_alerts.initialize(con)
    con.execute('CREATE TABLE IF NOT EXISTS preferences (account TEXT PRIMARY KEY, value TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS service_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')

def defaults(role):
    return {'default_filter':'pending'} if role=='admin' else {'default_map':'auto','follow_bus':True,**({'confirm_stop':True} if role=='driver' else {'previous_stop_notification':False,'previous_stop_call':False})}

def service():
    with accounts.connect() as con:
        row=con.execute('SELECT value FROM service_settings WHERE id=1').fetchone()
        return {**SERVICE_DEFAULTS,**(json.loads(row['value']) if row else {})}

def read(user):
    with accounts.connect() as con:
        row=con.execute('SELECT value FROM preferences WHERE account=?',(user['id'],)).fetchone()
        prefs={**defaults(user['role']),**(json.loads(row['value']) if row else {})}
    return {'user':user,'preferences':prefs,'service':service(),'refresh_seconds':5,'voice_calls_configured':pickup_alerts.configured()}

def save(user,data,stop_count):
    if set(data)-{'preferences','service','stop'}:
        raise ValueError('Unsupported settings field.')
    prefs=data.get('preferences',{})
    if not isinstance(prefs,dict) or set(prefs)-set(defaults(user['role'])):
        raise ValueError('Those settings are not available to your role.')
    for key,value in prefs.items():
        if key in ('follow_bus','confirm_stop','previous_stop_notification','previous_stop_call') and type(value) is not bool:
            raise ValueError('Use true or false for switches.')
        if key=='default_map' and value not in ('auto','route','street'):
            raise ValueError('Invalid default map.')
        if key=='default_filter' and value not in ('pending','all','approved','rejected','suspended'):
            raise ValueError('Invalid profile filter.')
    if 'service' in data:
        if user['role']!='admin':
            raise ValueError('Only admins can change transport settings.')
        values=data['service']
        if not isinstance(values,dict) or set(values)-set(SERVICE_DEFAULTS):
            raise ValueError('Unsupported transport setting.')
        if 'registration_open' in values and type(values['registration_open']) is not bool:
            raise ValueError('Invalid registration setting.')
        if 'helpdesk' in values and (not isinstance(values['helpdesk'],str) or values['helpdesk'] and not re.fullmatch(r'[0-9]{10}',values['helpdesk'])):
            raise ValueError('Helpdesk must be empty or a 10-digit mobile number.')
        for key,low,high in [('gps_stale_seconds',20,120),('waiting_expiry_seconds',60,600)]:
            if key in values and (type(values[key]) is not int or not low<=values[key]<=high):
                raise ValueError(f'{key} must be {low}-{high} seconds.')
    if 'stop' in data and (user['role']!='employee' or type(data['stop']) is not int or not 0<=data['stop']<stop_count):
        raise ValueError('Only employees may choose a valid pickup stop.')
    with accounts.connect() as con:
        current=con.execute('SELECT * FROM accounts WHERE id=?',(user['id'],)).fetchone()
        if not current or current['status']!='approved':
            raise ValueError('Admin approval is required to save service preferences.')
        row=con.execute('SELECT value FROM preferences WHERE account=?',(user['id'],)).fetchone()
        merged={**defaults(user['role']),**(json.loads(row['value']) if row else {}),**prefs}
        if merged.get('previous_stop_call') and (not re.fullmatch(r'[0-9]{10}',current['mobile']) or not pickup_alerts.configured()):
            raise ValueError('Voice calls require an admin-approved 10-digit mobile number and a configured calling service. Turn calls off to save other preferences.')
        con.execute('INSERT INTO preferences (account,value) VALUES (?,?) ON CONFLICT(account) DO UPDATE SET value=excluded.value',(user['id'],json.dumps(merged)))
        if 'stop' in data:
            con.execute('UPDATE accounts SET stop=? WHERE id=?',(data['stop'],user['id']))
        if 'service' in data:
            row=con.execute('SELECT value FROM service_settings WHERE id=1').fetchone()
            merged_service={**SERVICE_DEFAULTS,**(json.loads(row['value']) if row else {}),**data['service']}
            con.execute('INSERT INTO service_settings (id,value) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(json.dumps(merged_service),))
        con.execute('INSERT INTO account_audit (actor,account,action,at) VALUES (?,?,?,?)',(user['id'],user['id'],'Settings updated: '+json.dumps(data),time.time()))

def change_password(user,data):
    if set(data)!={'current_password','new_password'}:
        raise ValueError('Current and new password are required.')
    old,new=data['current_password'],data['new_password']
    if not isinstance(old,str) or not isinstance(new,str) or len(old)>128 or not 10<=len(new)<=128:
        raise ValueError('New password must contain 10-128 characters.')
    if old==new:
        raise ValueError('Choose a different password.')
    with accounts.connect() as con:
        row=con.execute('SELECT * FROM accounts WHERE id=?',(user['id'],)).fetchone()
        if not secrets.compare_digest(accounts.password_hash(old,row['salt']),row['password']):
            raise ValueError('Current password is incorrect.')
        salt=secrets.token_hex(16)
        con.execute('UPDATE accounts SET salt=?,password=? WHERE id=?',(salt,accounts.password_hash(new,salt),user['id']))
        con.execute('DELETE FROM sessions WHERE account=?',(user['id'],))
        con.execute('INSERT INTO account_audit (actor,account,action,at) VALUES (?,?,?,?)',(user['id'],user['id'],'Password changed; previous sessions revoked',time.time()))
