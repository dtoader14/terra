'use strict';
const $ = id => document.getElementById(id);
let setupRequired = false, authenticated = false, connection = null, editing = false, busy = false;
const labels = {ready:'Connected',unconfigured:'Not configured',paused:'Paused',expired:'Expired',attention:'Needs attention'};

async function api(path, options={}) {
  let response;
  try { response = await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Earth-Request':'1'}}); }
  catch { throw new Error('The server is unreachable. Please try again shortly.'); }
  const body = await response.json();
  if (!response.ok) {
    if(response.status===401 && !path.endsWith('/login')) { authenticated=false; await showAuth(); }
    throw new Error(Array.isArray(body.detail)?body.detail.map(error=>error.msg).join(' '):body.detail||'The request could not be completed.');
  }
  return body;
}
function notice(message,error=false){$('admin-notice').textContent=message;$('admin-notice').hidden=!message;$('admin-notice').classList.toggle('error',error);}
function formatDate(value){return value?new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'—';}
function setBusy(value){busy=value;document.querySelectorAll('#dashboard-view button').forEach(button=>button.disabled=value);if(!value&&connection){$('pause-provider').disabled=!connection.configured;$('remove-provider').disabled=!connection.configured;}}
async function showAuth(){
  $('dashboard-view').hidden=true;$('auth-view').hidden=false;$('logout').hidden=true;
  const state=await api('/api/admin/session');setupRequired=state.setup_required;
  $('auth-title').textContent=setupRequired?'Set up your admin workspace':'Welcome back.';
  $('auth-description').textContent=setupRequired?'Create the administrator password for this local platform. Then add a NASA API token to enable data access for all users.':'Sign in to manage your platform’s data connection and view extraction activity.';
  $('auth-submit').textContent=setupRequired?'Create admin workspace →':'Sign in →';
  $('confirm-field').hidden=!setupRequired;$('confirm-password').required=setupRequired;
  $('admin-password').autocomplete=setupRequired?'new-password':'current-password';
  $('admin-password').minLength=setupRequired?12:1;
  $('password-help').textContent=setupRequired?'Choose at least 12 characters. This first-time setup is only available until an administrator is created.':'Your administrator session lasts up to 8 hours.';
}
function renderConnection(value){
  connection=value;const state=value.state;
  $('metric-connection').textContent=labels[state];$('provider-state').textContent=labels[state];$('provider-state').className=`state-pill ${state}`;
  $('verified-at').textContent=value.verified_at?formatDate(value.verified_at):'Not yet verified';
  $('updated-at').textContent=formatDate(value.updated_at);$('expires-at').textContent=value.expires_at?formatDate(value.expires_at):'Not supplied';
  $('saved-credential').hidden=!value.configured;
  $('token-form').hidden=value.configured&&!editing;
  $('saved-actions').hidden=!value.configured||editing;
  $('cancel-token').hidden=!value.configured;
  $('token-label').textContent=value.configured?'Replacement AppEEARS token':'AppEEARS API token';
  $('save-token').innerHTML=value.configured?'Verify & replace token <span>→</span>':'Verify & save token <span>→</span>';
  $('pause-provider').textContent=value.enabled?'Pause requests':'Resume requests';
  $('pause-provider').disabled=!value.configured||busy;$('remove-provider').disabled=!value.configured||busy;
  const descriptions={ready:'Connection verified · Available to all explorers',unconfigured:'Waiting for an administrator-provided token',paused:'New requests paused · Existing results remain available',expired:'Token has expired · Replace it to restore data access',attention:'NASA rejected the token · An update is needed'};
  $('availability-copy').textContent=descriptions[state];
  $('provider-guidance').textContent=value.error||(state==='expired'?'The supplied expiration date has passed. Save a fresh token with its new expiration.':'Users never need to supply NASA credentials. Their requests use the platform connection.');
}
async function refresh(){
  const data=await api('/api/admin/overview');
  renderConnection(data.connection);
  ['running','completed','failed'].forEach(key=>$(`metric-${key}`).textContent=data.metrics[key]);
  $('activity-empty').hidden=data.activity.length>0;$('activity-table-wrap').hidden=!data.activity.length;$('activity-rows').replaceChildren();
  for(const job of data.activity){
    const row=document.createElement('tr');const variable=document.createElement('td');variable.textContent=job.variable;
    const period=document.createElement('td');period.textContent=`${job.start} → ${job.end}`;
    const status=document.createElement('td');const pill=document.createElement('span');pill.className=`state-pill ${job.status}`;pill.textContent=({done:'Completed',error:'Failed',pending:'Queued',processing:'Processing',submitting:'Submitting',rendering:'Rendering'})[job.status]||job.status;
    status.append(pill);row.append(variable,period,status);$('activity-rows').append(row);
  }
}
async function showDashboard(){authenticated=true;$('auth-view').hidden=true;$('dashboard-view').hidden=false;$('logout').hidden=false;await refresh();}
$('admin-auth-form').onsubmit=async event=>{
  event.preventDefault();$('auth-error').textContent='';
  const password=$('admin-password').value;
  if(setupRequired&&password!==$('confirm-password').value){$('auth-error').textContent='The passwords do not match.';return;}
  $('auth-submit').disabled=true;
  try{await api(setupRequired?'/api/admin/setup':'/api/admin/login',{method:'POST',body:JSON.stringify({password})});$('admin-password').value='';$('confirm-password').value='';await showDashboard();}
  catch(error){$('auth-error').textContent=error.message;}
  finally{$('auth-submit').disabled=false;}
};
$('logout').onclick=async()=>{try{await api('/api/admin/logout',{method:'POST'});authenticated=false;editing=false;$('nasa-token').value='';notice('');await showAuth();}catch(error){notice(error.message,true);}};
$('token-form').onsubmit=async event=>{
  event.preventDefault();$('token-error').textContent='';notice('');setBusy(true);$('save-token').textContent='Verifying with NASA…';
  try{
    const token=$('nasa-token').value.trim();
    const expires_at=$('token-expiry').value?new Date($('token-expiry').value).toISOString():null;
    await api('/api/admin/nasa',{method:'PUT',body:JSON.stringify({token,expires_at})});
    $('nasa-token').value='';$('token-expiry').value='';editing=false;await refresh();notice('NASA connection verified and saved. Users can now generate heat maps.');
  }catch(error){$('token-error').textContent=error.message;}
  finally{setBusy(false);if(connection)renderConnection(connection);}
};
$('replace-token').onclick=()=>{editing=true;$('token-error').textContent='';renderConnection(connection);$('nasa-token').focus();};
$('cancel-token').onclick=()=>{editing=false;$('nasa-token').value='';$('token-expiry').value='';$('token-error').textContent='';renderConnection(connection);};
$('verify-token').onclick=async()=>{
  setBusy(true);notice('Testing the saved token with NASA…');
  try{await api('/api/admin/nasa/verify',{method:'POST'});await refresh();notice('NASA accepted the saved token. Connection checked successfully.');}
  catch(error){try{if(authenticated)await refresh();}catch{}notice(error.message,true);}
  finally{setBusy(false);}
};
$('pause-provider').onclick=async()=>{
  const enabled=!connection.enabled;setBusy(true);notice('');
  try{await api('/api/admin/nasa/state',{method:'PUT',body:JSON.stringify({enabled})});await refresh();notice(enabled?'Requests resumed. The explorer will reflect the current connection status.':'New requests are paused. Extractions already running will continue.');}
  catch(error){notice(error.message,true);}finally{setBusy(false);}
};
$('remove-provider').onclick=()=>$('remove-dialog').showModal();
$('cancel-remove').onclick=$('keep-token').onclick=()=>$('remove-dialog').close();
$('confirm-remove').onclick=async()=>{
  $('confirm-remove').disabled=true;
  try{await api('/api/admin/nasa',{method:'DELETE'});editing=false;$('nasa-token').value='';$('token-expiry').value='';await refresh();$('remove-dialog').close();notice('Token removed. Add a new token to restore data access.');}
  catch(error){$('remove-dialog').close();notice(error.message,true);}finally{$('confirm-remove').disabled=false;}
};
$('refresh-activity').onclick=async()=>{setBusy(true);try{await refresh();notice('Platform activity refreshed.');}catch(error){notice(error.message,true);}finally{setBusy(false);}};
document.querySelectorAll('.admin-nav').forEach(link=>link.onclick=()=>{document.querySelectorAll('.admin-nav').forEach(other=>other.classList.remove('active'));link.classList.add('active');});
async function init(){try{const state=await api('/api/admin/session');if(state.authenticated)await showDashboard();else await showAuth();}catch(error){$('auth-description').textContent='The platform could not be reached.';$('auth-error').textContent=error.message;}}
init();
setInterval(async()=>{if(authenticated&&!busy&&!editing&&!document.hidden){try{await refresh();}catch(error){notice(error.message,true);}}},30000);
