'use strict';
// The place search (both modes) and the time-series mode: a place in Romania, a period and a variable
// give a chart and a trend. Uses $, api, bbox, setBounds, placeName, refreshSteps and map from app.js, which loads first.
// The mode keeps its own status line: app.js rewrites the heat-map one every 30 seconds.
const SVG = 'http://www.w3.org/2000/svg';
let seriesVariables = [], seriesReason = null, seriesPlace = null, placeMarker = null, placeTimer = null, seriesTimer = null, seriesRun = 0;
let variablesTimer = null, variablesRun = 0;
let seriesChoice = null;   // the variable the visitor means; a new place or period may hide it but never swaps in another
let placeListed = [];      // the places listed for the text as typed now; empty while a search is on its way

function seriesStatus(title, message, state = '') {
  $('series-status-title').textContent = title;
  $('series-status-message').textContent = message;
  $('series-status-icon').textContent = state === 'error' ? '!' : state === 'busy' ? '◌' : '↗';
  $('series-status').classList.toggle('error', state === 'error');
  $('series-status').classList.toggle('busy', state === 'busy');
}
function setMode(mode) {
  document.body.dataset.mode = mode;
  $('mode-heat').classList.toggle('active', mode === 'heat');
  $('mode-series').classList.toggle('active', mode === 'series');
  $('series-panel').hidden = mode !== 'series' || !$('series-chart').childElementCount;
  refreshSteps();   // each mode has its own period fields and its own variable list
}
$('mode-heat').onclick = () => setMode('heat');
$('mode-series').onclick = () => setMode('series');

function describeVariable() {
  const select = $('series-variable'), v = seriesVariables.find(v => v.id === select.value);
  $('series-button').classList.toggle('locked', !v);
  if (!v) {   // nothing is chosen: say why
    const wanted = seriesVariables.find(v => v.id === seriesChoice), listed = [...select.options];
    $('series-variable-note').textContent = seriesReason ? seriesReason :
      !seriesVariables.length ? 'No variable has data for this place and period.' :
      !listed.length ? 'No variable matches your search.' :
      listed.every(option => option.disabled) ? 'Your box is too large for these variables. Draw a smaller box.' :
      seriesChoice === null || (wanted && !wanted.too_large) ? 'Choose a variable.' :
      wanted ? 'Your box is too large for your variable. Draw a smaller box, or choose another variable.' :
      'Your variable has no data for this place and period. Choose another one.';
    return;
  }
  const years = `${v.full_years} full year${v.full_years === 1 ? '' : 's'}${v.full_years < 10 ? ', too short for a trend' : ''}`;
  $('series-variable-note').textContent =
    `${v.source} · ${v.kind === 'satellite' ? 'satellite measurement' : 'model product'} · ${v.unit || 'no unit'} · ${v.grid}${v.unit_hint ? ' · ' + v.unit_hint : ''}` +
    ` · data from ${v.available_from} to ${v.available_to} (${years})`;
}
// The list follows the place and the period: the server says which variables have data there and then.
function placeQuery() {
  const query = new URLSearchParams();
  if (seriesPlace) query.set('place', seriesPlace.id); else query.set('bbox', bbox().join(','));
  if ($('series-start').value) query.set('start', $('series-start').value);
  if ($('series-end').value) query.set('end', $('series-end').value);
  return query;
}
function loadVariables(ready) {
  clearTimeout(variablesTimer);
  if (!ready) { variablesRun++; seriesVariables = []; seriesReason = PICK_FIRST; showVariables(); return; }
  variablesTimer = setTimeout(async () => {
    const run = ++variablesRun;
    try {
      const data = await api('/api/series/variables?' + placeQuery());
      if (run === variablesRun) { seriesVariables = data.variables; seriesReason = data.reason; showVariables(); }
    } catch (error) {
      if (run === variablesRun) { seriesVariables = []; seriesReason = error.message; showVariables(); }
    }
  }, 300);
}
function showVariables() {
  const text = $('series-filter').value.trim().toLowerCase(), themes = new Map();
  for (const v of seriesVariables) {
    if (text && !`${v.name} ${v.theme} ${v.id}`.toLowerCase().includes(text)) continue;
    if (!themes.has(v.theme)) themes.set(v.theme, []);
    themes.get(v.theme).push(v);
  }
  $('series-variable').replaceChildren(...[...themes].sort().map(([theme, list]) => {
    const group = document.createElement('optgroup');
    group.label = theme;
    group.append(...list.map(v => {
      const option = new Option(v.too_large ? `${v.name} · ${v.product} · box too large` : `${v.name} · ${v.product}`, v.id);
      option.disabled = v.too_large;   // more than 800 unsaved grid cells: the server would refuse it
      option.title = v.too_large ? `This box needs ${v.cells} grid cells for this variable. Draw a smaller box.` : '';
      return option;
    }));
    return group;
  }));
  const open = [...$('series-variable').options].filter(option => !option.disabled).map(option => option.value);
  // Nothing is chosen for the visitor. A new place, period or search keeps their variable while it is listed,
  // and otherwise leaves the choice empty and says why.
  $('series-variable').value = open.includes(seriesChoice) ? seriesChoice : '';
  describeVariable();
}
$('series-filter').oninput = showVariables;
$('series-variable').onchange = () => { seriesChoice = $('series-variable').value; describeVariable(); };
$('series-start').onchange = $('series-end').onchange = refreshSteps;
$('series-whole').onclick = () => {   // the widest period any variable has; each variable then shows what it holds inside it
  const now = new Date();
  $('series-start').value = '1948-01'; $('series-end').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  refreshSteps();
};
document.addEventListener('terra-box', () => {   // app.js says the place changed
  // A box made by hand (app.js names it 'Custom box') replaces the chosen city or county.
  if (seriesPlace && placeName !== placeLabel(seriesPlace)) { forgetPlace(); $('place-search').value = ''; }
  refreshSteps();
});

function placeNote(text) {
  const note = document.createElement('p');
  note.className = 'field-note'; note.textContent = text;
  $('place-results').replaceChildren(note);
}
function forgetPlace() {
  seriesPlace = null;
  if (placeMarker) { map.removeLayer(placeMarker); placeMarker = null; }
}
function placeLabel(place) { return place.kind === 'county' ? `${place.name} county` : `${place.name}, ${place.county}`; }
function choosePlace(place) {
  if (drawing) stopDrawing();   // a corner already clicked on the map must not turn a later click into a box
  forgetPlace();
  seriesPlace = place;
  $('place-search').value = placeLabel(place);
  $('place-results').replaceChildren(); placeListed = [];
  if (place.kind === 'town') placeMarker = L.circleMarker([place.lat, place.lon], {radius: 7, color: '#244f46', weight: 2, fillColor: '#d5eaaa', fillOpacity: 0.9}).addTo(map);
  placeName = placeLabel(place);
  setBounds(place.bbox);   // a county is the box around its towns; a town gets half a degree around it for heat maps
}
$('place-search').oninput = () => {   // the chosen place stays until another one is picked
  clearTimeout(placeTimer);
  placeListed = [];
  const text = $('place-search').value.trim();
  if (!text) { $('place-results').replaceChildren(); return; }
  placeTimer = setTimeout(async () => {
    try {
      const {places} = await api('/api/places?q=' + encodeURIComponent(text));
      if ($('place-search').value.trim() !== text) return;
      placeListed = places;
      $('place-results').replaceChildren(...places.map(place => {
        const button = document.createElement('button');
        button.type = 'button'; button.onclick = () => choosePlace(place);
        button.textContent = place.kind === 'county' ? `${place.name} county · the box around it` : `${place.name}, ${place.county}`;
        return button;
      }));
      if (!places.length) placeNote('No city or county found. Try another spelling, or use a box.');
    } catch (error) { placeNote(error.message); }
  }, 250);
};
// Enter picks the first place listed for what is typed. Without this the browser would send the form, and a
// heat map or a series would start for the place chosen before.
$('place-search').addEventListener('keydown', event => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  if (placeListed.length) choosePlace(placeListed[0]);
});

function part(name, attributes, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
function digits(value) { return Number(value.toPrecision(3)).toString(); }
function pText(value) { return value < 0.001 ? 'p < 0.001' : `p = ${digits(value)}`; }

function drawSeries(data) {
  const chart = $('series-chart'), unit = data.variable.unit || '', cells = data.location.cells;
  const where = data.location.type === 'place' ? `${data.location.name}, ${data.location.county}` :
    data.location.type === 'county' ? `${data.location.name} county (the box around it)` : 'Selected box';
  chart.replaceChildren();
  $('series-panel').hidden = document.body.dataset.mode !== 'series';
  const shown = data.points.filter(point => point.value !== null);
  // Say how many cells were really averaged: a box over sea or a border can hold cells with no data.
  const counts = shown.map(point => point.cells_used), fewest = Math.min(...counts), most = Math.max(...counts);
  const averaged = !shown.length || (fewest === cells && most === cells) ? `${cells}` : `${fewest === most ? most : fewest + ' to ' + most} of ${cells}`;
  $('series-title').textContent = `${data.variable.name} · ${where} · ${averaged} grid cell${cells === 1 ? '' : 's'} (${data.location.grid})`;
  const source = `Source: ${data.variable.source} (${data.variable.kind === 'satellite' ? 'satellite measurement' : 'model product'}). ` +
    (cells === 1 ? 'One grid cell.' : `Mean of ${averaged} grid cells, weighted by area${averaged === `${cells}` ? '' : '; the others hold no data'}.`);
  if (!shown.length) { $('series-trend').textContent = 'No data for this place and period.'; $('series-note').textContent = source; return; }

  const W = 800, H = 260, left = 62, right = 14, top = 14, bottom = 30;
  const when = month => Number(month.slice(0, 4)) + (Number(month.slice(5, 7)) - 0.5) / 12;
  const t0 = when(data.points[0].month), t1 = when(data.points.at(-1).month);
  let low = Math.min(...shown.map(p => p.value)), high = Math.max(...shown.map(p => p.value));
  if (low === high) { low -= 0.5; high += 0.5; }
  const X = t => left + (W - left - right) * (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0));
  const Y = value => top + (H - top - bottom) * (1 - (value - low) / (high - low));
  const label = {'font-size': 11, fill: '#75827c'};
  chart.append(
    part('line', {x1: left, y1: H - bottom, x2: W - right, y2: H - bottom, stroke: '#dfe4da'}),
    part('line', {x1: left, y1: top, x2: left, y2: H - bottom, stroke: '#dfe4da'}),
    part('text', {...label, x: left - 8, y: top + 4, 'text-anchor': 'end'}, digits(high)),
    part('text', {...label, x: left - 8, y: H - bottom, 'text-anchor': 'end'}, digits(low)),
    part('text', {...label, x: left, y: H - 9}, data.points[0].month.slice(0, 4)),
    part('text', {...label, x: W - right, y: H - 9, 'text-anchor': 'end'}, data.points.at(-1).month.slice(0, 4)));
  let path = '', drawing = false;                      // the monthly line breaks where a month is missing
  for (const point of data.points) {
    if (point.value === null) { drawing = false; continue; }
    path += `${drawing ? 'L' : 'M'}${X(when(point.month)).toFixed(1)} ${Y(point.value).toFixed(1)}`;
    drawing = true;
  }
  chart.append(part('path', {d: path, fill: 'none', stroke: '#9bb7a0', 'stroke-width': 1}));
  for (const year of data.yearly) chart.append(part('circle', {cx: X(year.year + 0.5).toFixed(1), cy: Y(year.value).toFixed(1), r: 2.6, fill: '#244f46'}));
  if (!data.trend) { $('series-trend').textContent = data.trend_reason; $('series-note').textContent = source; return; }
  const trend = data.trend, [[yearA, valueA], [yearB, valueB]] = trend.line;
  chart.append(part('line', {x1: X(yearA + 0.5).toFixed(1), y1: Y(valueA).toFixed(1), x2: X(yearB + 0.5).toFixed(1), y2: Y(valueB).toFixed(1), stroke: '#b9384c', 'stroke-width': 2}));
  $('series-trend').textContent = `${trend.slope_per_decade < 0 ? '−' : '+'}${digits(Math.abs(trend.slope_per_decade))} ${unit} per decade · ${pText(trend.p_value)} · ${trend.significant ? 'significant' : 'not significant'}`;
  $('series-note').textContent = `${source} Trend fitted on ${trend.years} yearly means (dots), 95% range ${digits(trend.ci95[0])} to ${digits(trend.ci95[1])} ${unit} per decade; Mann-Kendall ${pText(trend.mann_kendall_p)}.`;
}

async function askSeries(run, query) {
  try {
    const data = await api('/api/series?' + query);
    if (run !== seriesRun) return;
    if (data.status === 'fetching') {                 // the server is still getting grid cells from NASA: ask again
      seriesStatus('Fetching from NASA', `Fetching ${data.cells_saved} of ${data.cells_total} grid cells`, 'busy');
      seriesTimer = setTimeout(() => askSeries(run, query), 5000);
      return;
    }
    drawSeries(data);
    seriesStatus('Your time series is ready', data.from_saved ? 'Answered from saved data.' : 'Fetched from NASA and saved for next time.');
  } catch (error) {
    if (run === seriesRun) { seriesStatus('Check your request', error.message, 'error'); $('series-panel').hidden = true; }   // no stale chart beside an error
  }
  if (run === seriesRun) $('series-button').disabled = false;
}
function showSeries() {
  const variable = $('series-variable').value;
  if (!variable) { seriesStatus('Check your request', $('series-variable-note').textContent || 'Choose a variable first.', 'error'); return; }   // the note says why none can be chosen
  const query = placeQuery();
  query.set('variable', variable);
  clearTimeout(seriesTimer);
  $('series-button').disabled = true;
  seriesStatus('Looking up your series', 'Checking what is already saved.', 'busy');
  askSeries(++seriesRun, query);
}
$('series-button').onclick = showSeries;
// Enter inside the form must not start a heat map while the time-series mode is showing.
$('explore-form').addEventListener('submit', event => {
  if (document.body.dataset.mode !== 'series') return;
  event.preventDefault(); event.stopImmediatePropagation(); showSeries();
}, true);

setMode('heat');
// app.js fetches /api/config; wait for it instead of asking twice.
const publicCheck = setInterval(() => { if (config) { clearInterval(publicCheck); $('admin-link').hidden = Boolean(config.public); } }, 200);
