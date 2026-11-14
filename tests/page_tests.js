'use strict';
// The page's own logic. Runs static/app.js and static/series.js as they are, against a small stand-in for the
// browser: page elements, a clock that only moves when told to, canned server answers and a stub of the map.
// Nothing here reaches a network.   node tests/page_tests.js --list | <scenario name>
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const STATIC = path.join(__dirname, '..', 'static');

const PLACES = {
  cluj: [{id: 'county:Cluj', kind: 'county', name: 'Cluj', county: 'Cluj', bbox: [22.69, 46.43, 24.21, 47.35]},
         {id: '681290', kind: 'town', name: 'Cluj-Napoca', county: 'Cluj', lat: 46.77, lon: 23.6, bbox: [23.35, 46.52, 23.85, 47.02]}],
  sibiu: [{id: 'county:Sibiu', kind: 'county', name: 'Sibiu', county: 'Sibiu', bbox: [23.6, 45.5, 24.9, 46.3]}],
};
const SERIES = [   // soil moisture ends in 2014; ozone starts in 2002
  {id: 'gio:soil', name: 'Soil moisture', first: '1948-01', last: '2014-12'},
  {id: 'gio:ozone', name: 'Ozone', first: '2002-09', last: '2026-10'},
].map(v => ({...v, theme: 'test', product: 'TEST 1', source: 'NASA Giovanni', kind: 'model', unit: 'u', grid: '1 x 1 degree', unit_hint: '', cells: 1, too_large: false}));

const HEAT = [   // Aqua starts in July 2002; evapotranspiration is published to the end of 2025
  {id: 'lst_day', label: 'Temperature · Terra', short: 'Surface temperature', theme: 'Temperature', unit: '°C', digits: 1, cadence: '8-day composite', resolution: '1 km', note: 'The ground, not the air.', first: '2000-02-18', last: null},
  {id: 'lst_day_aqua', label: 'Temperature · Aqua', short: 'Surface temperature', theme: 'Temperature', unit: '°C', digits: 1, cadence: '8-day composite', resolution: '1 km', note: 'The ground, not the air.', first: '2002-07-04', last: null},
  {id: 'et', label: 'Evapotranspiration', short: 'Evapotranspiration', theme: 'Water', unit: 'kg/m²/8 days', digits: 1, cadence: '8-day total', resolution: '500 m', note: 'Water leaving the land.', first: '2000-01-01', last: '2025-12-31'},
];
const FINISHED = {   // a heat map that is already drawn when the page opens
  id: 'job0', status: 'done', message: 'ready', task_id: 'task0', query: {variable: 'et', start: '2024-07-01', end: '2024-07-31', bbox: [23, 44, 24, 45]}, variable_info: HEAT[2],
  result: {variable: HEAT[2], query: {start: '2024-07-01', end: '2024-07-31'}, bounds: [[44, 23], [45, 24]], stats: {mean: 24.46, min: 3.04, max: 61.27, coverage: 88.2},
           legend: {min: 3.04, max: 61.27, colors: ['#ffffff', '#000000']}, frames: [{date: '2024-07-03', file: 'frame-0.png', stats: {mean: 20, min: 1, max: 50, coverage: 80}}]},
};

function answer(url, options, jobs) {
  const [route, query] = url.split('?'), asked = new URLSearchParams(query), post = options && options.method === 'POST';
  if (route === '/api/config') return [200, {variables: {}, provider: {state: 'ready', available: true, message: 'ready'}, series_login: true, public: false}];
  if (route === '/api/jobs') return post ? [202, {id: 'job1', status: 'submitting', message: 'sent', query: JSON.parse(options.body), variable_info: {label: 'x'}}] : [200, jobs];
  if (route === '/api/variables') {
    const start = asked.get('start') || '', end = asked.get('end') || '9999-12-31';
    if (start > end) return [400, {detail: 'The start date is after the end date.'}];
    return [200, {total: HEAT.length, variables: HEAT.filter(v => v.first <= end && (v.last === null || v.last >= start))}];
  }
  if (route.startsWith('/api/jobs/')) return [200, {id: 'job1', status: 'pending', message: 'waiting'}];
  if (route === '/api/places') return [200, {places: PLACES[asked.get('q')] || []}];
  if (route === '/api/series/variables') {
    const start = asked.get('start') || '', end = asked.get('end') || '9999-12';
    return [200, {reason: null, variables: SERIES.filter(v => v.first <= end && v.last >= start)
      .map(v => ({...v, available_from: v.first, available_to: v.last, full_years: 20}))}];
  }
  if (route === '/api/series') return [202, {status: 'fetching', cells_total: 1, cells_saved: 0}];
  return [404, {detail: 'This address has no canned answer.'}];
}

class El {
  constructor(id, tag = 'div') {
    Object.assign(this, {id, tagName: tag, textContent: '', hidden: false, disabled: false, children: [], listeners: {}, style: {}, dataset: {}, picked: null, held: '', dropDown: false});
    const classes = new Set();
    this.classList = {toggle: (name, on) => { (on === undefined ? !classes.has(name) : on) ? classes.add(name) : classes.delete(name); },
                      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)};
  }
  get options() { return this.children.flatMap(child => child.tagName === 'optgroup' ? child.children : [child]); }
  // A drop-down shows its first entry until told otherwise; a list box and an index of -1 show nothing.
  get chosen() { return this.picked === 'none' ? null : this.picked || (this.dropDown && this.options[0]) || null; }
  get value() { return this.tagName === 'select' ? (this.chosen ? this.chosen.held : '') : this.held; }
  set value(v) { if (this.tagName === 'select') this.picked = this.options.find(o => o.held === String(v)) || 'none'; else this.held = String(v); }
  get selectedIndex() { return this.options.indexOf(this.chosen); }
  set selectedIndex(i) { this.picked = this.options[i] || 'none'; }
  get childElementCount() { return this.children.length; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, extra = {}) {   // listeners first, then the on<type> handler, as capture comes before the handler
    const event = {type, target: this, defaultPrevented: false, stopped: false, preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra};
    for (const fn of this.listeners[type] || []) if (!event.stopped) fn(event);
    if (!event.stopped && this['on' + type]) this['on' + type](event);
    return event;
  }
  replaceChildren(...kids) { this.children = kids; this.picked = null; }
  append(...kids) { this.children.push(...kids); }
  setAttribute(name, value) { this[name] = value; }
  scrollIntoView() {} showModal() {} close() {} closest() { return this; }
}

function world(jobs = []) {
  const elements = new Map(), selects = ['series-variable', 'variable', 'frame'];
  const $ = id => elements.get(id) || elements.set(id, new El(id, selects.includes(id) ? 'select' : 'div')).get(id);
  $('opacity').value = 0.85;   // the page opens with no place, no period and no variable
  $('variable').dropDown = $('frame').dropDown = true;
  const onDocument = {}, statusRow = new El('status-row');
  const document = {
    getElementById: $, body: new El('body'), hidden: false,
    createElement: tag => new El(null, tag), createElementNS: (space, tag) => new El(null, tag),
    addEventListener: (type, fn) => (onDocument[type] ||= []).push(fn),
    dispatchEvent: event => { (onDocument[event.type] || []).forEach(fn => fn(event)); return true; },
    querySelector: () => statusRow, querySelectorAll: () => [],
  };
  let now = 0, timers = [], nextTimer = 1;
  const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
  const sent = [];
  const fetch = async (url, options) => {
    sent.push(`${(options && options.method) || 'GET'} ${url}`);
    const [status, body] = answer(url, options, jobs);
    return {ok: status < 400, status, json: async () => body};
  };
  const onMap = {}, layers = new Set();
  const layer = kind => ({kind, addTo() { layers.add(this); return this; }, on() { return this; }, setOpacity() {}});
  const map = {on(type, fn) { onMap[type] = fn; return this; }, removeLayer: gone => layers.delete(gone), fitBounds() {}, setView() {}, getZoom: () => 7};
  const L = {map: () => map, tileLayer: () => layer('tiles'), rectangle: () => layer('rectangle'), circleMarker: () => layer('marker'), imageOverlay: () => layer('image')};
  const context = vm.createContext({
    document, L, fetch, URLSearchParams, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    Option: function (text, value) { return Object.assign(new El(null, 'option'), {text, held: String(value)}); },
    setTimeout: (fn, ms = 0) => { timers.push({id: nextTimer, at: now + ms, fn}); return nextTimer++; },
    clearTimeout: id => { timers = timers.filter(timer => timer.id !== id); },
    setInterval: () => 0, clearInterval() {},
  });
  for (const name of ['app.js', 'series.js']) new vm.Script(fs.readFileSync(path.join(STATIC, name), 'utf8'), {filename: name}).runInContext(context);
  const page = {
    $, sent, look: text => vm.runInContext(text, context),
    async wait(ms) {   // let the clock run for ms, firing what falls due
      for (const end = now + ms; ; ) {
        await settle();
        const due = timers.filter(timer => timer.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
        if (!due) { now = end; break; }
        timers = timers.filter(timer => timer !== due); now = due.at; due.fn();
      }
      await settle();
    },
    type(id, text) { $(id).value = text; $(id).fire('input'); },
    change(id, value) { $(id).value = value; $(id).fire('change'); },
    click(id) { $(id).fire('click'); },
    enter(id) {   // a browser sends the form on Enter in a text field unless the page stops the key
      if (!$(id).fire('keydown', {key: 'Enter'}).defaultPrevented) $('explore-form').fire('submit');
    },
    clickMap(lat, lng) { onMap.click({latlng: {lat, lng}}); },
    results: () => $('place-results').children.map(child => child.textContent),
    pick(index) { $('place-results').children[index].fire('click'); },
    async search(text, index = 0) { page.type('place-search', text); await page.wait(400); page.pick(index); await page.wait(400); },
    asked: start => sent.filter(line => line.startsWith(start)),
  };
  return page;
}

// A page where the first two steps are already taken.
async function heatPage(start = '2024-07-01', end = '2024-07-31') {
  const page = world(); await page.wait(50);
  await page.search('cluj'); page.change('start', start); page.change('end', end); await page.wait(50);
  return page;
}
async function seriesPage() {
  const page = world(); await page.wait(50);
  page.click('mode-series'); await page.search('cluj'); page.click('series-whole'); await page.wait(400);
  return page;
}
const disabled = (page, ...ids) => ids.map(id => page.$(id).disabled);
const locked = (page, id) => page.$(id).classList.contains('locked');
const choose = (page, id, value) => { page.$(id).value = value; page.$(id).fire('change'); };

const SCENARIOS = {
  async the_steps_unlock_in_order_and_the_variables_are_asked_only_once_place_and_dates_are_picked() {
    const page = world(); await page.wait(50);
    assert.deepEqual(disabled(page, 'start', 'end', 'variable'), [true, true, true]);
    assert.equal(page.$('period-hint').hidden, false);
    assert.equal(page.$('variable-note').textContent, 'Pick a place and a time period first.');
    await page.search('cluj');                                   // step 1: a place
    assert.deepEqual(disabled(page, 'start', 'end', 'variable'), [false, false, true]);
    assert.equal(page.$('period-hint').hidden, true);
    page.change('start', '2024-07-01'); await page.wait(50);     // half of step 2
    assert.equal(page.$('variable').disabled, true);
    assert.deepEqual(page.asked('GET /api/variables'), []);
    page.change('end', '2024-07-31'); await page.wait(50);       // step 2: both dates
    assert.deepEqual(page.asked('GET /api/variables'), ['GET /api/variables?start=2024-07-01&end=2024-07-31']);
    assert.equal(page.$('variable').disabled, false);
    assert.equal(page.$('variable').value, '');                  // step 3 is the visitor's to take
    assert.equal(locked(page, 'generate-button'), true);
    choose(page, 'variable', 'et');
    assert.equal(locked(page, 'generate-button'), false);
  },
  async in_time_series_mode_the_variables_are_asked_only_once_place_and_months_are_picked() {
    const page = world(); await page.wait(50); page.click('mode-series'); await page.wait(400);
    assert.deepEqual(disabled(page, 'series-start', 'series-end', 'series-whole', 'series-filter', 'series-variable'), [true, true, true, true, true]);
    assert.equal(page.$('series-variable-note').textContent, 'Pick a place and a time period first.');
    await page.search('cluj');
    assert.deepEqual(disabled(page, 'series-start', 'series-end', 'series-whole', 'series-variable'), [false, false, false, true]);
    assert.deepEqual(page.asked('GET /api/series/variables'), []);
    page.change('series-start', '2000-01'); page.change('series-end', '2020-12'); await page.wait(400);
    assert.deepEqual(page.asked('GET /api/series/variables'), ['GET /api/series/variables?place=county%3ACluj&start=2000-01&end=2020-12']);
    assert.equal(page.$('series-variable').disabled, false);
    assert.equal(page.$('series-variable').value, '');
    assert.equal(page.$('series-variable-note').textContent, 'Choose a variable.');
    assert.equal(locked(page, 'series-button'), true);
    choose(page, 'series-variable', 'gio:ozone');
    assert.equal(locked(page, 'series-button'), false);
  },
  async whole_record_fills_both_months_and_counts_as_the_period() {
    const page = await seriesPage();
    assert.equal(page.$('series-start').value, '1948-01');
    assert.match(page.$('series-end').value, /^20\d\d-(0[1-9]|1[0-2])$/);
    assert.equal(page.asked('GET /api/series/variables').length, 1);
    assert.equal(page.$('series-variable').disabled, false);
  },
  async emptying_a_date_locks_the_variables_again_and_the_choice_returns_with_the_date() {
    const page = await heatPage(), list = page.$('variable');
    choose(page, 'variable', 'et');
    page.change('end', ''); await page.wait(50);
    assert.equal(list.disabled, true);
    assert.equal(list.value, '');
    assert.equal(locked(page, 'generate-button'), true);
    assert.equal(page.$('variable-note').textContent, 'Pick a place and a time period first.');
    page.change('end', '2024-07-31'); await page.wait(50);
    assert.equal(list.disabled, false);
    assert.equal(list.value, 'et');
  },
  async a_box_that_cannot_be_drawn_locks_the_later_steps_and_forgets_the_chosen_place() {
    const page = await heatPage();
    page.change('west', '30'); await page.wait(50);   // east of the box's own east edge
    assert.deepEqual(disabled(page, 'start', 'end', 'variable'), [true, true, true]);
    assert.equal(page.look('seriesPlace'), null);
    assert.equal(page.$('map-title').textContent, 'Choose a place');
    page.change('west', '22.5'); await page.wait(50);   // a box again: the dates are still there, so the list comes back
    assert.deepEqual(disabled(page, 'start', 'end', 'variable'), [false, false, false]);
    assert.equal(page.$('map-title').textContent, 'Custom box');
  },
  async enter_in_the_place_search_picks_the_first_result_and_sends_no_heat_map() {
    const page = world(); await page.wait(50);
    page.type('place-search', 'cluj'); await page.wait(400);
    assert.deepEqual(page.results(), ['Cluj county · the box around it', 'Cluj-Napoca, Cluj']);
    page.enter('place-search'); await page.wait(400);
    assert.deepEqual(page.asked('POST'), []);
    assert.equal(page.look('seriesPlace && seriesPlace.id'), 'county:Cluj');
    assert.equal(page.$('map-title').textContent, 'Cluj county');
  },
  async enter_before_the_results_arrive_sends_nothing_and_picks_nothing() {
    const page = world(); await page.wait(50);
    page.type('place-search', 'cluj'); page.enter('place-search'); await page.wait(400);
    assert.deepEqual(page.asked('POST'), []);
    assert.equal(page.look('seriesPlace'), null);
  },
  async enter_in_the_place_search_never_asks_a_series_for_the_place_chosen_before() {
    const page = await seriesPage();
    choose(page, 'series-variable', 'gio:ozone');
    page.type('place-search', 'sibiu'); await page.wait(400);
    page.enter('place-search'); await page.wait(400);
    assert.deepEqual(page.asked('GET /api/series?'), []);
    assert.equal(page.look('seriesPlace.id'), 'county:Sibiu');
  },
  async picking_a_place_ends_a_map_selection_in_progress() {
    const page = world(); await page.wait(50);
    page.click('draw-button'); page.clickMap(45, 25);   // one corner set, the second still owed
    await page.search('cluj');
    assert.equal(page.look('drawing'), false);
    page.clickMap(45.5, 25.5); await page.wait(400);     // a later click on the map must not make a box
    assert.equal(page.look('seriesPlace.id'), 'county:Cluj');
    assert.equal(page.$('west').value, '22.69');
  },
  async a_chosen_variable_that_loses_its_data_is_not_swapped_for_another() {
    const page = await seriesPage(), list = page.$('series-variable');
    choose(page, 'series-variable', 'gio:soil');
    page.change('series-start', '2015-01'); await page.wait(400);
    assert.deepEqual(list.options.map(option => option.held), ['gio:ozone']);
    assert.equal(list.value, '');
    assert.match(page.$('series-variable-note').textContent, /no data for this place and period/);
    assert.equal(locked(page, 'series-button'), true);
    page.click('series-button'); await page.wait(50);
    assert.deepEqual(page.asked('GET /api/series?'), []);
    page.change('series-start', '1948-01'); await page.wait(400);   // the period is widened again: the choice comes back
    assert.equal(list.value, 'gio:soil');
  },
  async searching_narrows_the_list_without_choosing_for_the_visitor() {
    const page = await seriesPage(), list = page.$('series-variable');
    assert.equal(list.value, '');
    choose(page, 'series-variable', 'gio:soil');
    page.type('series-filter', 'ozone');
    assert.deepEqual(list.options.map(option => option.held), ['gio:ozone']);
    assert.equal(list.value, '');
    assert.equal(page.$('series-variable-note').textContent, 'Choose a variable.');
    page.type('series-filter', '');
    assert.equal(list.value, 'gio:soil');
  },
  async the_heat_map_list_is_asked_for_the_dates_and_follows_them() {
    const page = await heatPage(), list = page.$('variable'), ids = () => list.options.map(option => option.held).filter(Boolean);
    assert.deepEqual(page.asked('GET /api/variables'), ['GET /api/variables?start=2024-07-01&end=2024-07-31']);
    assert.deepEqual(ids(), ['lst_day', 'lst_day_aqua', 'et']);
    assert.deepEqual(list.children.map(child => child.label || child.text), ['Choose a variable…', 'Temperature', 'Water']);
    assert.equal(page.$('variable-tags').hidden, true);
    choose(page, 'variable', 'lst_day');
    assert.deepEqual(['resolution', 'cadence', 'unit', 'variable-note'].map(id => page.$(id).textContent), ['1 km source', '8-day composite', '°C', 'The ground, not the air.']);
    assert.equal(page.$('variable-count').hidden, true);
    page.change('start', '2001-06-01'); page.change('end', '2001-06-30'); await page.wait(50);   // before Aqua
    assert.deepEqual(ids(), ['lst_day', 'et']);
    assert.equal(page.$('variable-count').textContent, '2 of 3 variables have data in these dates.');
    assert.equal(list.value, 'lst_day');
  },
  async a_heat_map_variable_without_data_in_the_new_dates_is_not_swapped_for_another() {
    const page = await heatPage(), list = page.$('variable');
    choose(page, 'variable', 'et');
    assert.deepEqual(['resolution', 'cadence', 'unit'].map(id => page.$(id).textContent), ['500 m source', '8-day total', 'kg/m²/8 days']);
    page.change('start', '2026-03-01'); page.change('end', '2026-03-31'); await page.wait(50);   // not published for 2026 yet
    assert.deepEqual(list.options.map(option => option.held).filter(Boolean), ['lst_day', 'lst_day_aqua']);
    assert.equal(list.value, '');
    assert.equal(page.$('variable-note').textContent, "Evapotranspiration has no data in these dates. NASA's record runs from 2000-01-01 to 2025-12-31.");
    assert.equal(page.$('variable-tags').hidden, true);
    page.$('explore-form').fire('submit'); await page.wait(50);
    assert.deepEqual(page.asked('POST'), []);
    assert.equal(page.$('status-message').textContent, 'Choose a variable first.');
    page.change('start', '2025-07-01'); page.change('end', '2025-07-31'); await page.wait(50);   // back inside the record
    assert.equal(list.value, 'et');
    assert.equal(page.$('variable-tags').hidden, false);
  },
  async dates_in_the_wrong_order_empty_the_heat_map_list_and_say_so() {
    const page = await heatPage();
    page.change('start', '2024-08-01'); await page.wait(50);
    assert.deepEqual(page.$('variable').options.map(option => option.held).filter(Boolean), []);
    assert.equal(page.$('variable-note').textContent, 'The start date is after the end date.');
  },
  async a_finished_heat_map_takes_its_legend_title_and_decimals_from_the_variable() {
    const page = world([FINISHED]); await page.wait(50);
    assert.equal(page.$('legend-title').textContent, 'Evapotranspiration');
    assert.equal(page.$('legend-unit').textContent, 'kg/m²/8 days');
    assert.deepEqual(['stat-mean', 'stat-min', 'stat-max', 'legend-min', 'legend-max'].map(id => page.$(id).textContent), ['24.5', '3.0', '61.3', '3.0', '61.3']);
  },
};

(async () => {
  const name = process.argv[2];
  if (name === '--list') { console.log(Object.keys(SCENARIOS).join('\n')); return; }
  for (const scenario of name ? [name] : Object.keys(SCENARIOS)) await SCENARIOS[scenario]();
})().catch(error => { console.error(error.stack || error); process.exit(1); });
