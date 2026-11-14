'use strict';
const $ = id => document.getElementById(id);
const ROMANIA = [20.2, 43.6, 29.7, 48.3];   // what the map shows before a place is picked
const PICK_FIRST = 'Pick a place and a time period first.';
let placeName = 'Custom box';   // shown above the map once a place is set; series.js names a picked city or county
let config, selection, overlay, drawing = false, firstCorner, drawPreview;
let activeJob = null, pollTimer, visibleResult = null;
const map = L.map('map', {zoomControl: true, minZoom: 2, maxZoom: 17, worldCopyJump: false, maxBounds: [[-85,-180],[85,180]]});
const light = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>', maxZoom:19}).addTo(map);
const terrain = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {attribution:'Tiles &copy; Esri — Sources: Esri, USGS, NOAA', maxZoom:17});
let isTerrain = false;
light.on('tileerror', () => { $('map-coordinates').textContent = 'Basemap unavailable · Coordinate selection still works'; });
function bbox() { return ['west','south','east','north'].map(id => Number($(id).value)); }
function boundsOf(box) { return [[box[1],box[0]],[box[3],box[2]]]; }
// A place is set when the four fields hold a box the map can draw.
function placeReady() { const box = bbox(); return ['west','south','east','north'].every(id => $(id).value !== '') && box.every(Number.isFinite) && box[0]<box[2] && box[1]<box[3] && box[1]>=-85 && box[3]<=85; }
function updateSelection(fit = false) {
  const ready = placeReady(), box = bbox();
  if (selection) { map.removeLayer(selection); selection = null; }
  if (ready) selection = L.rectangle(boundsOf(box), {color:'#6c8750', weight:1.5, fillColor:'#b7cc8c', fillOpacity:0.07, dashArray:'6 5', interactive:false}).addTo(map);
  if (ready && fit) map.fitBounds(boundsOf(box), {padding:[45,45]});
  $('map-title').textContent = ready ? placeName : 'Choose a place';
  if (ready && visibleResult) $('map-title').textContent += ' · new selection';
  document.dispatchEvent(new CustomEvent('terra-box'));   // the place changed: series.js forgets a replaced city or county and refreshes the steps
}
function setBounds(box) { ['west','south','east','north'].forEach((id,i)=>$(id).value=box[i]); updateSelection(true); }
map.fitBounds(boundsOf(ROMANIA));
['west','south','east','north'].forEach(id=>$(id).addEventListener('change',()=>{placeName='Custom box';updateSelection(true);}));
$('fit-button').onclick = ()=>visibleResult ? map.fitBounds(visibleResult.result.bounds,{padding:[25,25]}) : placeReady() && map.fitBounds(boundsOf(bbox()),{padding:[45,45]});
// The steps are taken in order: a place, then a period, then a variable. The variable list is asked from the
// server only once the first two are set, and again when either changes. series.js calls this for its fields.
function periodReady() { const series = document.body.dataset.mode === 'series'; return Boolean($(series ? 'series-start' : 'start').value && $(series ? 'series-end' : 'end').value); }
function refreshSteps() {
  const place = placeReady(), both = place && periodReady();
  $('step-period').classList.toggle('locked', !place); $('period-hint').hidden = place;
  ['start','end','series-start','series-end','series-whole'].forEach(id => $(id).disabled = !place);
  $('step-variable').classList.toggle('locked', !both);
  ['variable','series-filter','series-variable'].forEach(id => $(id).disabled = !both);
  if (document.body.dataset.mode === 'series') loadVariables(both); else loadHeatVariables(both);
}
$('basemap-button').onclick = ()=>{isTerrain=!isTerrain;map.removeLayer(isTerrain?light:terrain);(isTerrain?terrain:light).addTo(map);$('basemap-button').textContent=isTerrain?'Light map':'Terrain';};
function stopDrawing() {
  drawing=false;firstCorner=null;if(drawPreview){map.removeLayer(drawPreview);drawPreview=null;}
  $('draw-button').classList.remove('active');$('draw-button').textContent='⌖  Select two corners on map';$('map').style.cursor='';
  $('map-hint').textContent='Selected area • Adjust the region to begin';
}
$('draw-button').onclick = ()=>{
  if(drawing){stopDrawing();return;}
  drawing=true;firstCorner=null;$('draw-button').classList.add('active');$('draw-button').textContent='Cancel map selection';
  $('map-hint').textContent='Click the first corner, then the opposite corner';$('map').style.cursor='crosshair';
  $('empty-card').hidden=true;$('map').scrollIntoView({behavior:'smooth',block:'center'});
};
map.on('click',event=>{
  if(!drawing)return;
  if(!firstCorner){firstCorner=event.latlng;$('map-hint').textContent='Now click the opposite corner';return;}
  const a=firstCorner,b=event.latlng;
  const box=[Math.min(a.lng,b.lng),Math.min(a.lat,b.lat),Math.max(a.lng,b.lng),Math.max(a.lat,b.lat)].map(v=>Number(v.toFixed(4)));
  stopDrawing();placeName='Custom box';setBounds(box);
});
map.on('mousemove',event=>{
  $('map-coordinates').textContent=`${event.latlng.lat.toFixed(3)}° latitude / ${event.latlng.lng.toFixed(3)}° longitude`;
  if(drawing&&firstCorner){if(drawPreview)map.removeLayer(drawPreview);drawPreview=L.rectangle([firstCorner,event.latlng],{color:'#456d43',weight:1,interactive:false}).addTo(map);}
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&drawing)stopDrawing();});

async function api(path, options={}) {
  let response;
  try { response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Earth-Request':'1',...options.headers}}); }
  catch { throw new Error('The platform is temporarily unreachable. Please reload or try again shortly.'); }
  const data=await response.json();
  if(!response.ok){
    const detail=Array.isArray(data.detail)?data.detail.map(e=>e.msg.replace('Value error, ','')).join(' '):data.detail;
    throw new Error(detail||`Request failed (${response.status}).`);
  }
  return data;
}
function updateService(provider){
  config.provider=provider;
  $('service-note').hidden=provider.available;
  $('service-note').textContent=provider.message;
  if(!activeJob) setStatus(provider.available?'Ready to explore':'Plan your exploration',provider.message);
}
$('about-button').onclick=()=>$('about-dialog').showModal();
$('method-button').onclick=()=>$('about-dialog').showModal();
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
// The heat-map list follows the dates: the server says which variables have data in them. Nothing is chosen
// for the visitor: the list opens on "Choose a variable…".
let heatVariables=[],heatTotal=0,heatChoice=null,heatReason='',heatRun=0;   // heatChoice: the variable the visitor means, as last listed
function updateVariable(){
  const variable=heatVariables.find(v=>v.id===$('variable').value);
  $('variable-tags').hidden=!variable;$('generate-button').classList.toggle('locked',!variable);
  $('variable-count').textContent=heatVariables.length<heatTotal?`${heatVariables.length} of ${heatTotal} variables have data in these dates.`:'';
  $('variable-count').hidden=!$('variable-count').textContent;
  if(!variable){$('variable-note').textContent=heatReason||(heatChoice?`${heatChoice.label} has no data in these dates. NASA's record runs from ${heatChoice.first} to ${heatChoice.last||'today'}.`:'');return;}
  $('resolution').textContent=`${variable.resolution} source`;$('cadence').textContent=variable.cadence;$('unit').textContent=variable.unit;$('variable-note').textContent=variable.note;
}
async function loadHeatVariables(ready){
  const run=++heatRun;
  if(!ready){heatVariables=[];heatTotal=0;heatReason=PICK_FIRST;}
  else{
    try{const data=await api('/api/variables?'+new URLSearchParams({start:$('start').value,end:$('end').value}));if(run!==heatRun)return;heatVariables=data.variables;heatTotal=data.total;heatReason='';}
    catch(error){if(run!==heatRun)return;heatVariables=[];heatTotal=0;heatReason=error.message;}
  }
  const themes=new Map(),first=new Option('Choose a variable…','');
  first.disabled=true;
  for(const v of heatVariables)themes.set(v.theme,[...(themes.get(v.theme)||[]),v]);
  $('variable').replaceChildren(first,...[...themes].map(([theme,list])=>{const group=document.createElement('optgroup');group.label=theme;group.append(...list.map(v=>new Option(v.label,v.id)));return group;}));
  // Keep the visitor's variable. When the dates leave it without data, choose nothing rather than another one.
  heatChoice=heatVariables.find(v=>v.id===heatChoice?.id)||heatChoice;
  $('variable').value=heatVariables.includes(heatChoice)?heatChoice.id:'';
  updateVariable();
}
$('variable').onchange=()=>{heatChoice=heatVariables.find(v=>v.id===$('variable').value);updateVariable();};
$('start').addEventListener('change',refreshSteps);$('end').addEventListener('change',refreshSteps);
function setStatus(title,message,state=''){$('status-title').textContent=title;$('status-message').textContent=message;$('status-icon').textContent=state==='error'?'!':state==='busy'?'◌':'↗';document.querySelector('.status-row').classList.toggle('error',state==='error');document.querySelector('.status-row').classList.toggle('busy',state==='busy');}
function resetResult(){visibleResult=null;activeJob=null;if(overlay){map.removeLayer(overlay);overlay=null;}$('result-panel').hidden=true;$('legend').hidden=true;$('empty-card').hidden=false;$('task-link').hidden=true;$('generate-button').disabled=false;}
function validateQuery(query){
  if(!query.variable)throw new Error('Choose a variable first.');
  const [w,s,e,n]=query.bbox;
  if(!(w>=-180&&w<e&&e<=180&&s>=-85&&s<n&&n<=85))throw new Error('Enter valid bounds: west < east, south < north, latitude within ±85°.');
  if((e-w)*(n-s)>100||e-w>20||n-s>20)throw new Error('Select a smaller area: at most 100 square degrees and 20° per side.');
  if(e-w<.01||n-s<.01)throw new Error('Each side of the region must span at least 0.01°.');
  const days=(new Date(query.end)-new Date(query.start))/86400000;
  if(days<0||days>92)throw new Error('Select an ordered date range of at most 93 inclusive days.');
}
$('explore-form').onsubmit=async event=>{
  event.preventDefault();
  const query={variable:$('variable').value,start:$('start').value,end:$('end').value,bbox:bbox()};
  try{validateQuery(query);const current=await api('/api/config');updateService(current.provider);if(!current.provider.available){setStatus('Data service unavailable',current.provider.message);return;}$('generate-button').disabled=true;const job=await api('/api/jobs',{method:'POST',body:JSON.stringify(query)});clearTimeout(pollTimer);resetResult();activeJob=job.id;$('generate-button').disabled=true;$('empty-card').hidden=true;showJob(job);await refreshHistory();pollJob(job.id);}
  catch(error){$('generate-button').disabled=false;setStatus('Check your request',error.message,'error');}
};
function showJob(job){
  const titles={submitting:'Sending your selection to NASA',pending:'In the NASA processing queue',processing:'Extracting satellite observations',rendering:'Preparing your heat map',done:'Your heat map is ready',error:'Extraction needs attention'};
  setStatus(titles[job.status]||job.status,job.message,job.status==='error'?'error':job.status==='done'?'':'busy');
  $('task-link').hidden=!job.task_id;
  if(job.task_id)$('task-link').textContent=`NASA task: ${job.task_id} ↗`;
  if(job.status==='done')showResult(job);
  if(job.status==='done'||job.status==='error')$('generate-button').disabled=false;
}
async function pollJob(id){
  if(id!==activeJob)return;
  try{const job=await api(`/api/jobs/${id}`);if(id!==activeJob)return;showJob(job);if(!['done','error'].includes(job.status))pollTimer=setTimeout(()=>pollJob(id),5000);else await refreshHistory();}
  catch(error){setStatus('Status check interrupted',error.message,'error');$('generate-button').disabled=false;pollTimer=setTimeout(()=>pollJob(id),15000);}
}
function fileURL(job,name,download=false){return `/api/jobs/${job.id}/files/${name}${download?'?download=true':''}`;}
function number(value,variable){return value===null?'No data':`${value.toFixed(variable.digits)}${variable.unit==='°C'?' °C':''}`;}
function showStats(stats,variable){for(const key of ['mean','min','max'])$(`stat-${key}`).textContent=number(stats[key],variable);$('stat-coverage').textContent=`${stats.coverage.toFixed(1)}%`;}
function showResult(job){
  visibleResult=job;const result=job.result;
  $('empty-card').hidden=true;$('result-panel').hidden=false;$('legend').hidden=false;
  $('map-title').textContent=result.variable.label;
  $('map-hint').textContent=`${result.query.start} — ${result.query.end} • ${result.frames.length} composites`;
  $('legend-title').textContent=result.variable.short;$('legend-unit').textContent=result.variable.unit;
  $('gradient').style.background=`linear-gradient(to right, ${result.legend.colors.join(',')})`;
  $('legend-min').textContent=number(result.legend.min,result.variable);$('legend-max').textContent=number(result.legend.max,result.variable);
  $('frame').replaceChildren(new Option('Period mean · all composites','mean'),...result.frames.map((frame,i)=>new Option(`${frame.date} · composite start`,String(i))));
  $('download-tif').href=fileURL(job,'mean.tif',true);$('download-json').href=fileURL(job,'metadata.json',true);
  showFrame();map.fitBounds(result.bounds,{padding:[25,25]});
}
function showFrame(){
  const job=visibleResult;if(!job)return;const result=job.result;const mean=$('frame').value==='mean';const frame=mean?null:result.frames[Number($('frame').value)];const name=frame?frame.file:'mean.png';
  if(overlay)map.removeLayer(overlay);overlay=L.imageOverlay(fileURL(job,name),result.bounds,{opacity:Number($('opacity').value),interactive:false}).addTo(map);
  overlay.on('error',()=>setStatus('Map image unavailable','Reload this page while the local server is running.','error'));
  showStats(frame?frame.stats:result.stats,result.variable);$('download-png').href=fileURL(job,name,true);
  $('download-tif').textContent=mean?'GeoTIFF ↓':'Mean GeoTIFF ↓';
}
$('frame').onchange=showFrame;$('opacity').oninput=()=>overlay?.setOpacity(Number($('opacity').value));
async function refreshHistory(){
  const jobs=await api('/api/jobs');$('job-count').textContent=jobs.length;$('history-list').replaceChildren();
  if(!jobs.length){const p=document.createElement('p');p.textContent='No extractions yet. Your first request will appear here.';$('history-list').append(p);}
  [...jobs].reverse().forEach(job=>{const row=document.createElement('div');row.className='history-item';const copy=document.createElement('div');const title=document.createElement('strong');title.textContent=job.variable_info.label;const note=document.createElement('p');note.textContent=`${job.query.start} → ${job.query.end} · ${job.status}`;copy.append(title,note);const button=document.createElement('button');button.className='outline-button';button.textContent='Open →';button.onclick=()=>{clearTimeout(pollTimer);resetResult();activeJob=job.id;showJob(job);$('history-dialog').close();if(!['done','error'].includes(job.status)){$('generate-button').disabled=true;pollJob(job.id);}};row.append(copy,button);$('history-list').append(row);});return jobs;
}
$('history-button').onclick=async()=>{try{await refreshHistory();$('history-dialog').showModal();}catch(error){setStatus('Cannot load extractions',error.message,'error');}};
async function init(){
  const now=new Date();const today=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  $('start').max=today;$('end').max=today;
  try{config=await api('/api/config');updateService(config.provider);const jobs=await refreshHistory();if(jobs.length){const job=jobs.at(-1);activeJob=job.id;showJob(job);if(!['done','error'].includes(job.status)){$('generate-button').disabled=true;pollJob(job.id);}}}
  catch(error){setStatus('Server connection needed',error.message,'error');}
}
init();
