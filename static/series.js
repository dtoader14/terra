'use strict';
const SVG = 'http://www.w3.org/2000/svg';
let seriesVariables = [], seriesReason = null, seriesPlace = null, placeMarker = null, placeTimer = null, seriesTimer = null, seriesRun = 0;
let variablesTimer = null, variablesRun = 0;
let seriesChoice = null;
let placeListed = [];
const trendLayer = L.layerGroup();

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
  if (mode === 'series') trendLayer.addTo(map); else trendLayer.remove();
  refreshSteps();
}
$('mode-heat').onclick = () => setMode('heat');
$('mode-series').onclick = () => setMode('series');

function describeVariable() {
  const select = $('series-variable'), v = seriesVariables.find(v => v.id === select.value);
  $('series-button').classList.toggle('locked', !v);
  if (!v) {
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
function placeQuery() {
  return new URLSearchParams({...(seriesPlace ? {place: seriesPlace.id} : {bbox: bbox().join(',')}), start: $('series-start').value, end: $('series-end').value});
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
      option.disabled = v.too_large;
      option.title = v.too_large ? `This box needs ${v.cells} grid cells for this variable. Draw a smaller box.` : '';
      return option;
    }));
    return group;
  }));
  const open = [...$('series-variable').options].filter(option => !option.disabled).map(option => option.value);
  $('series-variable').value = open.includes(seriesChoice) ? seriesChoice : '';
  describeVariable();
}
$('series-filter').oninput = showVariables;
$('series-variable').onchange = () => { seriesChoice = $('series-variable').value; describeVariable(); };
$('series-start').onchange = $('series-end').onchange = refreshSteps;
$('series-whole').onclick = () => {
  const now = new Date();
  $('series-start').value = '1948-01'; $('series-end').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  refreshSteps();
};
document.addEventListener('terra-box', () => {
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
function choosePlace(place, fit = true) {
  if (drawing) stopDrawing();
  forgetPlace();
  seriesPlace = place;
  $('place-search').value = placeLabel(place);
  $('place-results').replaceChildren(); placeListed = [];
  if (place.kind === 'town') placeMarker = L.circleMarker([place.lat, place.lon], {radius: 7, color: '#244f46', weight: 2, fillColor: '#d5eaaa', fillOpacity: 0.9}).addTo(map);
  placeName = placeLabel(place);
  if (fit) setBounds(place.bbox); else { ['west', 'south', 'east', 'north'].forEach((id, i) => $(id).value = place.bbox[i]); updateSelection(); }
}
$('place-search').oninput = () => {
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
        button.textContent = placeLabel(place) + (place.kind === 'county' ? ' · the box around it' : '');
        return button;
      }));
      if (!places.length) placeNote('No city or county found. Try another spelling, or use a box.');
    } catch (error) { placeNote(error.message); }
  }, 250);
};
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
  let path = '', drawing = false;
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

async function showTrends(variable, unit) {
  const {places} = await api('/api/trends?variable=' + encodeURIComponent(variable));
  const sure = places.filter(place => place.p_value < 0.05), most = sure.length ? Math.abs(sure[0].slope_per_decade) : 1, least = sure.length ? Math.abs(sure.at(-1).slope_per_decade) : 0;
  const size = value => `${digits(Math.abs(value))}${unit ? ' ' + unit : ''}`;
  const why = place => place.p_value >= 0.05 ? `no clear change in ${place.years} years` :
    `${place.slope_per_decade < 0 ? 'fell' : 'rose'} by ${size(place.slope_per_decade * (place.years - 1) / 10)} in ${place.years} years (${size(place.slope_per_decade)} per decade) · ${place.p_value < 0.001 ? 'very strong' : place.p_value < 0.01 ? 'strong' : 'moderate'} evidence`;
  const open = place => {
    choosePlace(place, false);
    if (!$('series-start').value || !$('series-end').value) $('series-whole').click();
    seriesChoice = variable;
    showSeries(variable);
  };
  const tint = place => `rgb(${(place.slope_per_decade > 0 ? [185, 56, 76] : [57, 124, 184]).map(c => Math.round(255 + (c - 255) * (0.25 + 0.75 * (Math.abs(place.slope_per_decade) - least) / (most - least || 1)))).join(',')})`;
  trendLayer.clearLayers();
  for (const place of places) {
    const style = place.p_value < 0.05 ? {radius: 8, color: '#244f46', weight: 1, fillColor: tint(place), fillOpacity: 0.95} : {radius: 5, color: '#9aa39c', weight: 1, fillColor: '#e3e6df', fillOpacity: 0.9};
    L.circleMarker([place.lat, place.lon], style).bindTooltip(`${place.name}: ${why(place)}`).on('click', () => open(place)).addTo(trendLayer);
  }
  const title = document.createElement('p');
  title.className = 'field-note';
  title.textContent = places.length ? `Where it is changing, over each place's whole record: ${sure.length} of ${places.length} county seats show a clear change. On the map red is rising, blue is falling, darker is faster, and small grey dots show no clear change. The evidence label says how unlikely the change is to be chance. Fastest first:` : '';
  $('series-ranking').replaceChildren(title, ...sure.slice(0, 10).map((place, rank) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = `${rank + 1}. ${place.name}, ${place.county} · ${why(place)}`; button.onclick = () => open(place);
    return button;
  }));
  if (places.length) map.fitBounds([[43.6, 20.2], [48.3, 29.7]]);
}

async function askSeries(run, query) {
  try {
    const data = await api('/api/series?' + query);
    if (run !== seriesRun) return;
    if (data.status === 'fetching') {
      seriesStatus('Fetching from NASA', `Fetching ${data.cells_saved} of ${data.cells_total} grid cells`, 'busy');
      seriesTimer = setTimeout(() => askSeries(run, query), 5000);
      return;
    }
    drawSeries(data);
    showTrends(data.variable.id, data.variable.unit || '');
    seriesStatus('Your time series is ready', data.from_saved ? 'Answered from saved data.' : 'Fetched from NASA and saved for next time.');
  } catch (error) {
    if (run === seriesRun) { seriesStatus('Check your request', error.message, 'error'); $('series-panel').hidden = true; }
  }
  if (run === seriesRun) $('series-button').disabled = false;
}
function showSeries(variable = $('series-variable').value) {
  if (!variable) { seriesStatus('Check your request', $('series-variable-note').textContent || 'Choose a variable first.', 'error'); return; }
  const query = placeQuery();
  query.set('variable', variable);
  clearTimeout(seriesTimer);
  trendLayer.clearLayers(); $('series-ranking').replaceChildren();
  $('series-button').disabled = true;
  seriesStatus('Looking up your series', 'Checking what is already saved.', 'busy');
  askSeries(++seriesRun, query);
}
$('series-button').onclick = () => showSeries();
$('ask-form').onsubmit = async event => {
  event.preventDefault();
  const question = $('ask-input').value.trim();
  if (!question) return;
  $('ask-button').disabled = true; $('ask-answer').textContent = 'Thinking…';
  try {
    const reply = await api('/api/chat', {method: 'POST', body: JSON.stringify({question})});
    $('ask-answer').textContent = reply.answer;
    if (reply.place) {
      choosePlace(reply.place, false);
      $('series-whole').click();
      if (reply.start) $('series-start').value = reply.start;
      if (reply.end) $('series-end').value = reply.end;
      $('series-filter').value = '';
      refreshSteps();
      seriesChoice = reply.variable;
      showSeries(reply.variable);
    } else if (reply.variable) showTrends(reply.variable, reply.unit);
  } catch (error) { $('ask-answer').textContent = error.message; }
  $('ask-button').disabled = false;
};
$('explore-form').addEventListener('submit', event => {
  if (document.body.dataset.mode !== 'series') return;
  event.preventDefault(); event.stopImmediatePropagation(); showSeries();
}, true);

setMode('heat');
