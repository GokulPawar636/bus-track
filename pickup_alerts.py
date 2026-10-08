"""Opt-in previous-stop alerts; durable deduplication and one voice attempt per event."""
import base64
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape
import accounts

LOGGER = logging.getLogger(__name__)

def configured():
    return bool(re.fullmatch(r'AC[0-9a-fA-F]{32}',os.environ.get('TWILIO_ACCOUNT_SID','')) and os.environ.get('TWILIO_AUTH_TOKEN') and re.fullmatch(r'\+[1-9][0-9]{7,14}',os.environ.get('TWILIO_FROM_NUMBER','')))

def initialize(con):
    con.execute(f'''CREATE TABLE IF NOT EXISTS pickup_alerts (
      id {con.identity}, trip TEXT NOT NULL, account TEXT NOT NULL, stop INTEGER NOT NULL,
      channel TEXT NOT NULL, message TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
      created DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, provider_id TEXT NOT NULL DEFAULT '',
      UNIQUE(trip,account,channel))''')

def queue(trip, reached, stops, bus):
    target=reached+1
    if target>=len(stops):
        return
    message=f'Bus {bus} has reached {stops[reached]["name"]}. It is one stop away from your pickup at {stops[target]["name"]}. Please be ready at your stop.'
    with accounts.connect() as con:
        rows=con.execute("SELECT a.*,p.value FROM accounts a JOIN preferences p ON p.account=a.id WHERE a.role='employee' AND a.status='approved' AND a.stop=?",(target,)).fetchall()
        for row in rows:
            prefs=json.loads(row['value'])
            for channel,key in [('notification','previous_stop_notification'),('call','previous_stop_call')]:
                if prefs.get(key) is not True:
                    continue
                phone='+91'+row['mobile'] if re.fullmatch(r'[0-9]{10}',row['mobile']) else ''
                status='ready' if channel=='notification' else 'queued' if phone and configured() else 'unavailable'
                con.execute('INSERT INTO pickup_alerts (trip,account,stop,channel,message,phone,created,status) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(trip,account,channel) DO NOTHING',(trip,row['id'],target,channel,message,phone,time.time(),status))

def notifications(user, state):
    if user['role']!='employee' or not state.get('active') or state.get('next_stop')!=user.get('stop'):
        return []
    with accounts.connect() as con:
        row=con.execute('SELECT value FROM preferences WHERE account=?',(user['id'],)).fetchone()
        if not row or not json.loads(row['value']).get('previous_stop_notification'):
            return []
        return [dict(r) for r in con.execute("SELECT id,message,created FROM pickup_alerts WHERE account=? AND trip=? AND stop=? AND channel='notification' AND created>?",(user['id'],state.get('trip_id',''),state['next_stop'],time.time()-120))]

def place_call(phone,message):
    sid=os.environ['TWILIO_ACCOUNT_SID']
    credentials=base64.b64encode((sid+':'+os.environ['TWILIO_AUTH_TOKEN']).encode()).decode()
    payload=urllib.parse.urlencode({'To':phone,'From':os.environ['TWILIO_FROM_NUMBER'],'Twiml':'<Response><Say>'+escape(message)+'</Say></Response>','Timeout':'20'}).encode()
    request=urllib.request.Request('https://api.twilio.com/2010-04-01/Accounts/'+sid+'/Calls.json',data=payload,headers={'Authorization':'Basic '+credentials,'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request,timeout=10) as response:
        result=json.load(response)
    if not result.get('sid'):
        raise ValueError('Call provider did not return a call ID.')
    return result['sid']

def deliver_once(state, lock):
    with lock, accounts.connect() as con:
        row=con.execute("SELECT * FROM pickup_alerts WHERE channel='call' AND status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return False
        row=dict(row)
        user=con.execute('SELECT * FROM accounts WHERE id=?',(row['account'],)).fetchone()
        pref=con.execute('SELECT value FROM preferences WHERE account=?',(row['account'],)).fetchone()
        valid=(configured() and user and user['status']=='approved' and user['stop']==row['stop'] and '+91'+user['mobile']==row['phone'] and pref and json.loads(pref['value']).get('previous_stop_call') is True and state.get('active') and state.get('trip_id')==row['trip'] and state['next_stop']==row['stop'] and time.time()-row['created']<120)
        claimed=con.execute("UPDATE pickup_alerts SET status=? WHERE id=? AND status='queued'",('attempting' if valid else 'cancelled',row['id'])).rowcount
        if not claimed or not valid:
            return True
    try:
        provider_id=place_call(row['phone'],row['message'])
        status='submitted'
    except Exception:
        # A timeout may occur after the provider accepted the call. Never retry automatically.
        status,provider_id='failed_or_unknown',''
    with accounts.connect() as con:
        con.execute('UPDATE pickup_alerts SET status=?,provider_id=? WHERE id=?',(status,provider_id,row['id']))
    return True

def start_worker(state,lock):
    stop=threading.Event()
    def run():
        while not stop.is_set():
            try:
                if configured():
                    deliver_once(state,lock)
            except Exception as exc:
                LOGGER.error('Pickup alert worker iteration failed (%s).',type(exc).__name__)
            stop.wait(1)
    thread=threading.Thread(target=run,daemon=True,name='pickup-calls')
    thread.start()
    return stop
