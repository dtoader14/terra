'use strict';
// Time-series mode: a variable, a town or a box in Romania and a period give a chart and a trend.
// Uses $, api, setStatus, bbox and map from app.js, which loads first.
const SVG = 'http://www.w3.org/2000/svg';
let seriesVariables = [], seriesPlace = null, placeMarker = null, placeTimer = null, seriesTimer = null, seriesRun = 0;

function setMode(mode) {
  document.body.dataset.mode = mode;
  $('mode-heat').classList.toggle('active', mode === 'heat');
  $('mode-series').classList.toggle('active', mode === 'series');
  $('series-panel').hidden = mode !== 'series' || !$('series-chart').childElementCount;
}
$('mode-heat').onclick = () => setMode('heat');
$('mode-series').onclick = () => setMode('series');

function describeVariable() {
  const v = seriesVariables.find(v => v.id === $('series-variable').value);
  $('series-variable-note').textContent = !v ? 'No variable matches.' :
    `${v.source} · ${v.kind === 'satellite' ? 'satellite measurement' : 'model product'} · ${v.unit || 'no unit'} · ${v.first_month}–${v.last_month} · ${v.grid}${v.unit_hint ? ' · ' + v.unit_hint : ''}`;
}
function showVariables() {
  const text = $('series-filter').value.trim().toLowerCase(), chosen = $('series-variable').value, themes = new Map();
  for (const v of seriesVariables) {
    if (text && !`${v.name} ${v.theme} ${v.id}`.toLowerCase().includes(text)) continue;
    if (!themes.has(v.theme)) themes.set(v.theme, []);
    themes.get(v.theme).push(v);
  }
  $('series-variable').replaceChildren(...[...themes].sort().map(([theme, list]) => {
    const group = document.createElement('optgroup');
    group.label = theme;
    group.append(...list.map(v => new Option(`${v.name} · ${v.product}`, v.id)));
    return group;
  }));
  const options = [...$('series-variable').options];
  if (options.some(option => option.value === chosen)) $('series-variable').value = chosen; else if (options.length) $('series-variable').selectedIndex = 0;
  describeVariable();
}
$('series-filter').oninput = showVariables;
$('series-variable').onchange = describeVariable;

function forgetPlace() {
  seriesPlace = null;
  if (placeMarker) { map.removeLayer(placeMarker); placeMarker = null; }
}
function choosePlace(place) {
  forgetPlace();
  seriesPlace = place;
  $('place-search').value = `${place.name}, ${place.county}`;
  $('place-results').replaceChildren();
  placeMarker = L.circleMarker([place.lat, place.lon], {radius: 7, color: '#244f46', weight: 2, fillColor: '#d5eaaa', fillOpacity: 0.9}).addTo(map);
  map.setView([place.lat, place.lon], Math.max(map.getZoom(), 9));
}
$('place-search').oninput = () => {
  forgetPlace();
  clearTimeout(placeTimer);
  const text = $('place-search').value.trim();
  if (!text) { $('place-results').replaceChildren(); return; }
  placeTimer = setTimeout(async () => {
    try {
      const {places} = await api('/api/places?q=' + encodeURIComponent(text));
      if ($('place-search').value.trim() !== text) return;
      $('place-results').replaceChildren(...places.map(place => {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = `${place.name}, ${place.county}`; button.onclick = () => choosePlace(place);
        return button;
      }));
      if (!places.length) { const note = document.createElement('p'); note.className = 'field-note'; note.textContent = 'No town found. Try another spelling, or use a box.'; $('place-results').append(note); }
    } catch (error) { setStatus('Town search failed', error.message, 'error'); }
  }, 250);
};
// A box and a town are two different choices: touching the box forgets the town.
['west', 'south', 'east', 'north', 'region'].forEach(id => $(id).addEventListener('change', () => { forgetPlace(); $('place-search').value = ''; }));
$('draw-button').addEventListener('click', () => { forgetPlace(); $('place-search').value = ''; });

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
  const where = data.location.type === 'place' ? `${data.location.name}, ${data.location.county}` : 'Selected box';
  chart.replaceChildren();
  $('series-panel').hidden = false;
  $('series-title').textContent = `${data.variable.name} · ${where} · ${cells} grid cell${cells === 1 ? '' : 's'} (${data.location.grid})`;
  const source = `Source: ${data.variable.source} (${data.variable.kind === 'satellite' ? 'satellite measurement' : 'model product'}). ` +
    (cells === 1 ? 'One grid cell.' : `Mean of ${cells} grid cells, weighted by area.`);
  const shown = data.points.filter(point => point.value !== null);
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
      setStatus('Fetching from NASA', `Fetching ${data.cells_saved} of ${data.cells_total} grid cells`, 'busy');
      seriesTimer = setTimeout(() => askSeries(run, query), 5000);
      return;
    }
    drawSeries(data);
    setStatus('Your time series is ready', data.from_saved ? 'Answered from saved data.' : 'Fetched from NASA and saved for next time.');
  } catch (error) {
    if (run === seriesRun) setStatus('Check your request', error.message, 'error');
  }
  if (run === seriesRun) $('series-button').disabled = false;
}
function showSeries() {
  const variable = $('series-variable').value;
  if (!variable) { setStatus('Check your request', 'Choose a variable first.', 'error'); return; }
  const query = new URLSearchParams({variable});
  if (seriesPlace) query.set('place', seriesPlace.id); else query.set('bbox', bbox().join(','));
  if ($('series-start').value) query.set('start', $('series-start').value);
  if ($('series-end').value) query.set('end', $('series-end').value);
  clearTimeout(seriesTimer);
  $('series-button').disabled = true;
  setStatus('Looking up your series', 'Checking what is already saved.', 'busy');
  askSeries(++seriesRun, query);
}
$('series-button').onclick = showSeries;
// Enter inside the form must not start a heat map while the time-series mode is showing.
$('explore-form').addEventListener('submit', event => {
  if (document.body.dataset.mode !== 'series') return;
  event.preventDefault(); event.stopImmediatePropagation(); showSeries();
}, true);

setMode('heat');
api('/api/series/variables').then(data => { seriesVariables = data.variables; showVariables(); })
  .catch(error => setStatus('Server connection needed', error.message, 'error'));
// app.js fetches /api/config; wait for it instead of asking twice.
const publicCheck = setInterval(() => { if (config) { clearInterval(publicCheck); $('admin-link').hidden = Boolean(config.public); } }, 200);
