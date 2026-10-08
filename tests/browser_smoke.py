"""Real browser smoke test against the authenticated server and a temporary DB."""
import importlib.util
import pathlib
import re
import subprocess
import sys
import tempfile
import threading

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('bus_browser_server',ROOT/'server.py')
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
# Never run local tests against a configured production database.
app.accounts.DATABASE_URL = ""
MOBILE='--mobile' in sys.argv
CHROME=pathlib.Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
harness=r'''
<script>
(async()=>{
 const checks=[];
 const check=(ok,label)=>{checks.push((ok?'PASS: ':'FAIL: ')+label);if(!ok)throw Error(label)};
 const denied=async(path,data)=>{try{await api(path,data);return false}catch(e){return e.message.includes('account cannot access')}};
 try{
  await bootstrapAuth();
  check(!currentUser&&!$('auth-screen').hidden&&$('app-main').hidden,'anonymous user sees sign-in only');
  $('register-id').value='EMP100';$('register-name').value='Asha Patil';$('register-password').value='employee-browser-password';$('register-stop').value='9';
  await submitAuth($('register-form'),'/api/register');await refresh();
  check(currentUser?.role==='employee','registration creates employee role');
  check(currentUser.status==='pending'&&!$('approval-screen').hidden&&$('app-main').hidden,'new employee waits for approval');
  let pendingBlocked=false;try{await api('/api/state')}catch(e){pendingBlocked=e.message.includes('approval')}
  check(pendingBlocked,'pending employee cannot access service data');
  await $('logout-button').onclick();
  $('login-id').value='ADMIN';$('login-password').value='admin-browser-password';await submitAuth($('login-form'),'/api/login');
  check(currentUser.role==='admin'&&!$('admin-main').hidden&&$('app-main').hidden,'admin sees profile management only');
  check(adminProfiles.some(p=>p.id==='EMP100'&&p.status==='pending'),'admin sees pending employee');
  editProfile('EMP100');$('edit-status').value='approved';await $('profile-form').onsubmit({preventDefault(){},target:$('profile-form')});
  check(adminProfiles.find(p=>p.id==='EMP100').status==='approved','admin approves employee in review dialog');
  editProfile('DRIVER02');$('edit-status').value='approved';await $('profile-form').onsubmit({preventDefault(){},target:$('profile-form')});
  check(adminProfiles.find(p=>p.id==='DRIVER02').status==='approved','admin approves driver in review dialog');
  await $('logout-button').onclick();
  $('login-id').value='EMP100';$('login-password').value='employee-browser-password';await submitAuth($('login-form'),'/api/login');await refresh();

  check($('profile-name').textContent==='Asha Patil'&&$('profile-id').textContent.includes('EMP100'),'employee name and ID displayed');
  check(selectedStop===9&&$('pickup-title').textContent==='CIDCO Bus Stand','initial pickup is saved');
  await openSettings();
  check(!$('settings-employee').hidden&&$('settings-driver').hidden&&$('settings-admin').hidden,'employee settings show only employee controls');
  check(!$('settings-pickup-notification').checked&&!$('settings-pickup-call').checked,'pickup alerts are off by default');$('settings-pickup-notification').checked=true;$('settings-map-mode').value='route';$('settings-follow').checked=false;
  await $('settings-form').onsubmit({preventDefault(){},target:$('settings-form')});
  check(userPreferences.default_map==='route'&&!following,'employee map preferences are applied');check(userPreferences.previous_stop_notification,'employee notification preference is saved');processPickupAlerts([{id:999,message:'Bus 02 is one stop away.'}]);check(!$('pickup-alert').hidden,'in-app pickup notification is displayed');$('dismiss-pickup-alert').click();processPickupAlerts([{id:999,message:'Bus 02 is one stop away.'}]);check($('pickup-alert').hidden,'repeated refresh does not repeat notification');
  $('close-settings').click();

  check($('driver-panel').hidden&&!$('employee-panel').hidden&&!document.querySelector('.role-switch'),'employee cannot switch to driver view');
  check(await denied('/api/start',{}),'server rejects employee driver action');
  $('change-stop').click();$('stop-select').value='12';await $('save-stop').onclick();
  check(currentUser.stop===12&&$('stop-editor').hidden,'explicit pickup change saves and closes editor');
  await $('logout-button').onclick();
  $('login-id').value='EMP100';$('login-password').value='employee-browser-password';await submitAuth($('login-form'),'/api/login');await refresh();
  check(selectedStop===12&&$('pickup-title').textContent==='Dhoot Hospital','saved pickup restored on future login');
  check(document.querySelectorAll('#stop-list li').length===16,'all revised stops remain available');
  check($('fallback').textContent.includes('Cambridge Chowk')&&$('fallback').textContent.includes('08:34'),'map shows full stop names and revised times');
  $('expand-map').click();check(document.querySelector('.map-card').classList.contains('expanded'),'map expands');$('expand-map').click();
  await act('waiting',{waiting:true});check(state.waiting===12,'employee can wait at saved stop');
  await $('logout-button').onclick();
  $('login-id').value='DRIVER02';$('login-password').value='driver-browser-password';await submitAuth($('login-form'),'/api/login');await refresh();
  check(currentUser.role==='driver'&&$('employee-panel').hidden&&!$('driver-panel').hidden,'driver sees driver interface only');
  await openSettings();check(!$('settings-driver').hidden&&$('settings-employee').hidden&&$('settings-admin').hidden,'driver has driver-only settings');$('close-settings').click();
  check($('profile-name').textContent==='Pundlik Gangaram Jadhav'&&$('profile-mobile').textContent.includes('7972557053'),'driver name and mobile displayed');
  check(await denied('/api/profile',{stop:1}),'server rejects driver employee action');
  await api('/api/start',{direction:'return'});setState(await api('/api/state'));
  check(state.direction==='return'&&$('next-name').textContent==='Kumbephal','return trip departs the plant and advances to the first reverse stop');
  check($('stop-list').textContent.includes('No ETA'),'return route does not invent stop ETAs');
  check($('route-direction-label').textContent==='UACPL - Auric Plant → UACPL - B-34','return direction is shown on the map');
  await api('/api/stop',{});await api('/api/start',{direction:'morning'});setState(await api('/api/state'));
  await act('start');await act('reached');check($('next-name').textContent==='OASIS Chowk','driver trip control advances stop');
  await $('logout-button').onclick();
  $('login-id').value='EMP100';$('login-password').value='employee-browser-password';await submitAuth($('login-form'),'/api/login');await refresh();
  check(!document.cookie.includes('commute_session'),'session cookie is not readable by JavaScript');
  check(!$('employee-driver-contact').hidden&&$('employee-driver-contact').textContent.includes('7972557053'),'employee sees driver contact beside pickup');
  check(document.documentElement.scrollWidth<=innerWidth,'layout fits viewport without horizontal overflow');
  const original=state;
  const fakeLayer=(position,options={})=>{const classes=new Set();return{position,options,radius:options.radius,element:{classList:{toggle(name,force){if(force)classes.add(name);else classes.delete(name)},contains(name){return classes.has(name)}}},addTo(){return this},bindTooltip(){return this},getElement(){return this.element},setLatLng(value){this.position=value;return this},setRadius(value){this.radius=value;return this},setStyle(value){this.options={...this.options,...value};return this},getRadius(){return this.radius}}};
  window.L={map(){return{zoom:12,setView(){return this},getZoom(){return this.zoom},on(){return this},remove(){},removeLayer(){}}},control:{zoom(){return{addTo(){}}}},tileLayer(){return{addTo(){return{on(){return this}}}}},divIcon(){return{}},marker(position,options){return fakeLayer(position,options)},circle(position,options){return fakeLayer(position,options)}};
  setState({...state,profile:{...currentUser,stop:9}});
  check(selectedStop===9&&$('pickup-title').textContent==='CIDCO Bus Stand','saved pickup syncs from another session');
  setState(original);
  mapChosen=false;
  setState({...state,active:true,server_time:Date.now(),location:{lat:19.87,lng:75.35,accuracy:10,timestamp:Date.now()}});
  check(mapMode==='street','fresh GPS automatically opens live bus map');
  check($('location-age').textContent.includes('Device accuracy ±10m'),'live location shows device-reported accuracy');
  check(accuracyCircle&&accuracyCircle.getRadius()===10,'live map accuracy circle matches reported GPS accuracy');
  setState({...state,server_time:Date.now(),location:{...state.location,accuracy:250,timestamp:Date.now()}});
  check($('location-status').textContent.includes('GPS accuracy is weak'),'fresh low-accuracy fix is reported as weak, not precise');
  check($('signal').classList.contains('weak'),'weak GPS fix has a distinct signal indicator');
  check(busMarker.getElement().classList.contains('weak'),'map bus marker flags a weak GPS fix');
  check(accuracyCircle.getRadius()===250,'weak fix remains visible with its full reported accuracy radius');
  setState({...state,server_time:Date.now(),location:{...state.location,timestamp:Date.now()-30000}});
  check(accuracyCircle.options.color==='#89968d','stale GPS accuracy circle is visibly marked stale');
  $('route-map-button').click();
  setState({...state,server_time:Date.now()});
  check(mapMode==='route','manual map choice is respected during refresh');
  setState(original);
  await $('logout-button').onclick();
  $('login-id').value='ADMIN';$('login-password').value='admin-browser-password';await submitAuth($('login-form'),'/api/login');
  $('admin-status').value='all';renderProfiles();
  check(document.querySelectorAll('.admin-profile').length===3,'admin lists approved and pending profiles');
  await openSettings();
  check(!$('settings-admin').hidden&&$('settings-map').hidden,'admin sees transport settings');
  $('settings-helpdesk').value='9876543210';$('settings-stale').value='45';
  await $('settings-form').onsubmit({preventDefault(){},target:$('settings-form')});
  check(serviceSettings.helpdesk==='9876543210'&&serviceSettings.gps_stale_seconds===45,'admin transport preferences are saved');
  $('close-settings').click();

  check(document.documentElement.scrollWidth<=innerWidth,'admin layout fits viewport');


 }catch(e){checks.push('ERROR: '+e.message)}
 const out=document.createElement('pre');out.id='test-results';out.textContent=checks.join('\n');document.body.append(out);
})();
</script>
'''
class Handler(app.Handler):
    def do_GET(self):
        if self.path=='/browser-check':
            html=(ROOT/'index.html').read_text(encoding='utf-8-sig')
            html=re.sub(r'<script async.*?</script>','',html)
            html=re.sub(r'<link rel="stylesheet" href="https:.*?>','',html)
            payload=html.replace('</body>',harness+'</body>').encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
        else:
            super().do_GET()

with tempfile.TemporaryDirectory(prefix='commute-account-check-') as temp:
    folder=pathlib.Path(temp)
    app.accounts.DB=folder/'accounts.sqlite3'
    app.accounts.initialize(app.ROUTE,'driver-browser-password')
    app.accounts.initialize_admin('admin-browser-password')
    app.accounts.register({'id':'DRIVER04','role':'driver','name':'New Driver Application','mobile':'9876543210','password':'pending-browser-password'},16)
    http=app.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    screenshot=pathlib.Path(tempfile.gettempdir())/('commute-account-mobile.png' if MOBILE else 'commute-account-desktop.png')
    try:
        result=subprocess.run([str(CHROME),'--headless','--disable-gpu','--no-first-run','--no-default-browser-check',('--window-size=500,1350' if MOBILE else '--window-size=1440,1250'),'--user-data-dir='+str(folder/'profile'),'--screenshot='+str(screenshot),'--dump-dom','--virtual-time-budget=25000','http://127.0.0.1:'+str(http.server_port)+'/browser-check'],capture_output=True,timeout=50,encoding='utf-8',errors='replace')
        match=re.search(r'<pre id="test-results">(.*?)</pre>',result.stdout,re.S)
        if not match:
            (pathlib.Path(tempfile.gettempdir())/'commute-account-failed-dom.txt').write_text(result.stdout,encoding='utf-8')
            print(result.stderr[-1000:]);raise SystemExit('FAIL: Browser did not finish. DOM saved to temp.')
        print(match.group(1));print('Screenshot: '+str(screenshot))
        if 'FAIL:' in match.group(1) or 'ERROR:' in match.group(1):
            raise SystemExit(1)
    finally:
        http.shutdown();http.server_close();thread.join()
