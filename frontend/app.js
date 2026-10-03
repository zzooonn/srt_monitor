import { api } from './api.js';
import { createCatalog, readFavorites, reconcileJourney, FAVORITES_KEY } from './station-model.js';
import { StationPicker } from './station-picker.js';

const $ = id => document.getElementById(id);
const form = $('config-form');
const passengerFields = ['adult_count', 'child_count', 'senior_count', 'disability_1_to_3_count', 'disability_4_to_6_count'];
const numberFields = [...passengerFields, 'max_reservations', 'poll_interval', 'heartbeat_interval'];
const booleanFields = ['check_normal', 'check_special', 'require_same_row', 'require_same_car', 'require_adjacent_seats', 'prefer_window_seat', 'require_window_seat', 'auto_cancel_if_seat_mismatch', 'notify_if_seat_mismatch'];
const stringFields = ['departure_station', 'arrival_station', 'connection_mode', 'seat_column_order'];
const secretFields = ['korail_id', 'korail_password', 'srt_id', 'srt_password', 'telegram_bot_token', 'telegram_chat_id'];
const phaseNames = { off: '대기', searching: '조회 중', reserving: '예약 중', needs_confirmation: '예약 확인 필요', goal_reached: '목표 달성', error: '오류', stopped: '중지됨' };
const state = { config: null, secrets: null, status: null, fresh: false, loaded: false, busy: '', dirty: false, polling: false, events: new Map(), stream: null, eventFetches: 0, statusSequence: 0 };
let catalog = null;
let favorites = new Set();
let favoritesTimer;
const pickers = {};
for (const name of ['departure_station', 'arrival_station']) {
  pickers[name] = new StationPicker(name, {
    favorites: () => favorites,
    onOpen: current => Object.values(pickers).forEach(picker => { if (picker !== current) picker.close(); }),
    onSelect: () => {
      if (!state.loaded) return;
      clearErrors();
      reconcileStations('change');
      state.dirty = true;
      updateSummary();
    },
    onFavorite: station => {
      if (!state.loaded || state.busy || state.status?.running) return;
      if (favorites.has(station)) favorites.delete(station); else favorites.add(station);
      persistFavorites();
      Object.values(pickers).forEach(picker => { if (!picker.popup.hidden) picker.render(); });
    },
  });
}

function persistFavorites() {
  clearTimeout(favoritesTimer);
  try {
    window.localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favorites]));
    displayMessage('favorites-notice', '');
  } catch {
    displayMessage('favorites-notice', '브라우저에 저장할 수 없어 즐겨찾기는 현재 화면에서만 유지됩니다.');
    favoritesTimer = setTimeout(() => displayMessage('favorites-notice', ''), 7000);
  }
}

function reconcileStations(reason = 'change') {
  if (!catalog) return;
  const mode = $('connection_mode').value;
  const currentDeparture = $('departure_station').value;
  const currentArrival = $('arrival_station').value;
  const next = reconcileJourney(catalog, mode, currentDeparture, currentArrival);
  pickers.departure_station.setOptions(catalog.departures(mode), '이 연결에서 선택할 수 있는 출발역이 없습니다.');
  pickers.arrival_station.setOptions(catalog.arrivals(mode, next.departure), next.departure ? '직통 가능한 도착역이 없습니다.' : '출발역을 먼저 선택해 주세요.');
  pickers.departure_station.setValue(next.departure);
  pickers.arrival_station.setValue(next.arrival);
  let message = '';
  if (next.departureCleared) message = `${reason === 'load' ? '저장된 출발역은' : '기존 출발역은'} 현재 연결에서 지원하지 않아 선택을 비웠습니다. 출발역과 도착역을 다시 선택해 주세요.`;
  else if (next.arrivalCleared) message = `${reason === 'load' ? '저장된 도착역은' : '기존 도착역은'} ${next.departure}에서 직통으로 갈 수 없어 선택을 비웠습니다. 도착역을 다시 선택해 주세요.`;
  else if (next.departure && !next.arrival) message = '직통 가능한 목록에서 도착역을 선택해 주세요.';
  displayMessage('station-notice', message);
  return next.departureCleared || next.arrivalCleared;
}

function displayMessage(id, message) {
  $(id).textContent = message;
  $(id).hidden = !message;
}

function clearErrors() {
  displayMessage('action-error', '');
  form.querySelectorAll('.field-error').forEach(element => element.remove());
  form.querySelectorAll('[aria-invalid]').forEach(element => {
    element.removeAttribute('aria-invalid');
    const errorId = `${element.id}-error`;
    const remaining = (element.getAttribute('aria-describedby') || '').split(' ').filter(id => id && id !== errorId);
    if (remaining.length) element.setAttribute('aria-describedby', remaining.join(' '));
    else element.removeAttribute('aria-describedby');
  });
}

function fieldError(name, message) {
  const input = pickers[name]?.input || $(name);
  if (!input) return;
  input.setAttribute('aria-invalid', 'true');
  const id = `${input.id}-error`;
  let error = $(id);
  if (!error) {
    error = document.createElement('p');
    error.id = id;
    error.className = 'field-error';
    (input.closest('.field') || input.closest('label') || input.parentElement).append(error);
  }
  error.textContent = message;
  const descriptions = new Set((input.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
  descriptions.add(id);
  input.setAttribute('aria-describedby', [...descriptions].join(' '));
}

function reportError(error, prefix = '') {
  for (const detail of error.details || []) {
    const name = detail.loc?.filter(part => typeof part === 'string' && part !== 'body').at(-1);
    if (name) fieldError(name, detail.msg);
  }
  displayMessage('action-error', `${prefix}${error.message || '요청을 완료하지 못했습니다.'}`);
  const first = form.querySelector('[aria-invalid=true]');
  const details = first?.closest('details');
  if (details) details.open = true;
  (first || $('action-error')).focus({ preventScroll: true });
}

function isUncertain() {
  return state.status?.reservation_lock?.reason === 'uncertain' || state.status?.phase === 'needs_confirmation';
}

function updateControls() {
  const running = Boolean(state.status?.running);
  const busy = Boolean(state.busy);
  $('editable').disabled = !state.loaded || running || busy;
  Object.values(pickers).forEach(picker => picker.setDisabled(!state.loaded || running || busy));
  $('save').disabled = !state.loaded || !state.fresh || running || busy;
  $('restore').disabled = !state.loaded || running || busy;
  $('start').disabled = !state.loaded || !state.fresh || running || isUncertain() || busy;
  // When a status request fails, stopping remains available as a precaution.
  $('stop').disabled = !state.status || busy || (!running && state.fresh);
  $('refresh').disabled = busy;
  $('resolve-found').disabled = busy || !isUncertain() || !state.fresh;
  $('resolve-missing').disabled = busy || !isUncertain() || !state.fresh;
  $('run-lock-note').hidden = !running;
  $('start').replaceChildren(document.createTextNode(state.busy === 'start' ? '시작 요청 중…' : '감시 시작 '));
  if (state.busy !== 'start') {
    const arrow = document.createElement('span'); arrow.textContent = '→'; arrow.setAttribute('aria-hidden', 'true'); $('start').append(arrow);
  }
  $('save').textContent = state.busy === 'save' ? '저장 중…' : '설정 저장';
  $('stop').textContent = state.busy === 'stop' ? '중지 요청 중…' : '감시 중지';
  $('edit-state').textContent = !state.loaded ? '설정을 불러와야 저장할 수 있습니다.'
    : running ? '실행 중 · 설정 편집 잠김'
      : state.dirty ? '변경 사항이 있습니다. 시작할 때 저장됩니다.' : '저장된 설정을 표시하고 있습니다.';
}

function serverConnection(connected) {
  state.fresh = connected;
  $('server-state').textContent = connected ? '로컬 서버 연결됨' : '서버 응답 확인 필요';
  $('server-dot').className = `connection-dot ${connected ? 'connected' : 'failed'}`;
  updateControls();
}

function readableTime(value, includeDate = false) {
  if (!value) return '조회 기록 없음';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('ko-KR', {
    ...(includeDate ? { month: '2-digit', day: '2-digit' } : {}),
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function applyStatus(status, sequence = ++state.statusSequence) {
  // An older background request must never overwrite a newer mutation response.
  if (sequence !== state.statusSequence) return;
  if (!status || typeof status.running !== 'boolean' || !status.reservation_lock || typeof status.phase !== 'string') {
    throw new Error('감시 상태 응답을 확인할 수 없습니다. 상태를 다시 불러와 주세요.');
  }
  state.status = status;
  serverConnection(true);
  $('phase-label').textContent = phaseNames[status.phase] || status.phase;
  $('running-label').textContent = status.running ? '실행 중' : '실행 안 함';
  $('phase-dot').className = `phase-dot ${isUncertain() ? 'warning' : status.phase === 'error' ? 'failed' : status.running ? 'active' : ''}`;
  $('status-message').textContent = status.last_message || '아직 감시 메시지가 없습니다.';
  $('reserved-count').textContent = String(status.reserved_count);
  $('reserved-goal').textContent = ` / ${status.reservation_lock.max}건`;
  $('last-checked').textContent = readableTime(status.last_checked_at, true);
  $('last-checked').title = status.last_checked_at || '';
  $('active-connection').textContent = status.connection_mode === 'legacy_srt' ? '이전 SRT' : '코레일';
  displayMessage('monitor-error', status.error || '');
  $('uncertainty').hidden = !isUncertain();
  if (isUncertain()) {
    const legacy = status.reservation_lock.uncertain_service === 'srt'
      || (!status.reservation_lock.uncertain_service && status.connection_mode === 'legacy_srt');
    $('uncertainty-help').textContent = `${legacy ? 'SRT' : '코레일+'}에서 예약 내역과 좌석 조건을 직접 확인한 뒤, 실제 결과에 맞는 버튼을 눌러 주세요. 예약 응답이나 좌석 위치가 확인되지 않아 예약이 남아 있을 수 있습니다.`;
  }
  updateControls();
}

function fillConfig(config) {
  for (const name of [...stringFields, ...numberFields]) $(name).value = config[name];
  for (const name of booleanFields) $(name).checked = Boolean(config[name]);
  $('departure_date').value = `${config.departure_date.slice(0, 4)}-${config.departure_date.slice(4, 6)}-${config.departure_date.slice(6, 8)}`;
  $('departure_time').value = config.departure_time;
  $('max_departure_time').value = config.max_departure_time;
  $('auto-reserve').checked = config.auto_reserve;
  $('notification-only').checked = !config.auto_reserve;
  $('adjacent_seat_pairs').value = config.adjacent_seat_pairs.join(', ');
  $('window_seat_letters').value = config.window_seat_letters.join(', ');
  $('advanced-settings').open = config.connection_mode === 'legacy_srt';
  displayMessage('migration-notice', config.migration_notice || '');
  state.dirty = Boolean(reconcileStations('load'));
  updateSummary();
}

function updateSecretStatus(secrets) {
  state.secrets = secrets;
  for (const name of secretFields) {
    const configured = secrets[`${name}_set`];
    document.querySelector(`[data-secret-status="${name}"]`).textContent = configured ? '저장됨 · 빈칸으로 두면 유지' : '아직 설정되지 않았습니다.';
  }
  $('korail-account-state').textContent = secrets.korail_id_set && secrets.korail_password_set ? '저장된 계정 있음' : '계정 설정 필요';
}

function updateSummary() {
  if (!state.loaded) return;
  const total = passengerFields.reduce((sum, name) => sum + (Number($(name).value) || 0), 0);
  $('passenger-total').textContent = `${total}명`;
  $('summary-departure').textContent = $('departure_station').value || '출발역 선택';
  $('summary-arrival').textContent = $('arrival_station').value || '도착역 선택';
  const date = $('departure_date').value;
  const departure = $('departure_time').selectedOptions[0]?.textContent || '—';
  const arrival = $('max_departure_time').selectedOptions[0]?.textContent || '—';
  $('summary-date').textContent = `${date || '날짜 선택'} · ${departure}–${arrival}`;
  const classes = [$('check_normal').checked ? '일반실' : '', $('check_special').checked ? '특실' : ''].filter(Boolean).join(' · ');
  $('summary-passengers').textContent = `${total}명 / ${classes || '좌석 미선택'}`;
  $('summary-mode').textContent = $('auto-reserve').checked ? '자동 예약' : '알림만 받기';
  const legacy = $('connection_mode').value === 'legacy_srt';
  $('legacy-account').hidden = !legacy;
  $('seat-verification-help').hidden = legacy;
  $('disability-note').hidden = legacy || !(Number($('disability_1_to_3_count').value) + Number($('disability_4_to_6_count').value));
  updateControls();
}

function collectPublic() {
  // Copy the complete loaded public contract, including fields this UI does not own.
  const config = structuredClone(state.config);
  config.schema_version = 2;
  for (const name of stringFields) config[name] = $(name).value;
  for (const name of numberFields) config[name] = Number($(name).value);
  for (const name of booleanFields) config[name] = $(name).checked;
  config.departure_date = $('departure_date').value.replaceAll('-', '');
  config.departure_time = $('departure_time').value;
  config.max_departure_time = $('max_departure_time').value;
  config.auto_reserve = $('auto-reserve').checked;
  for (const name of ['adjacent_seat_pairs', 'window_seat_letters']) {
    config[name] = $(name).value.split(',').map(value => value.trim()).filter(Boolean);
  }
  return config;
}

function validateInputs(start = false) {
  clearErrors();
  let invalid = false;
  for (const input of form.querySelectorAll('input:not([type=password]), select')) {
    if (!input.checkValidity()) { fieldError(input.id, input.validationMessage); invalid = true; }
  }
  const config = collectPublic();
  const fail = (name, message) => { fieldError(name, message); invalid = true; };
  if (!catalog?.departures(config.connection_mode).some(station => station.name === config.departure_station)) fail('departure_station', '목록에서 출발역을 선택해 주세요.');
  if (!config.arrival_station) fail('arrival_station', '목록에서 도착역을 선택해 주세요.');
  else if (!catalog?.allows(config.connection_mode, config.departure_station, config.arrival_station)) fail('arrival_station', '선택한 출발역에서 직통으로 갈 수 있는 도착역을 선택해 주세요.');
  if (config.max_departure_time < config.departure_time) fail('max_departure_time', '마지막 시간은 첫 출발 시간 이후여야 합니다.');
  const total = passengerFields.reduce((sum, name) => sum + config[name], 0);
  if (!Number.isInteger(total) || total < 1 || total > 4) fail('adult_count', '총 승객 수는 1~4명이어야 합니다.');
  if (!config.check_normal && !config.check_special) fail('check_normal', '일반실 또는 특실을 하나 이상 선택해 주세요.');
  if (start) {
    const account = config.connection_mode === 'legacy_srt' ? 'srt' : 'korail';
    for (const suffix of ['id', 'password']) {
      const name = `${account}_${suffix}`;
      if (!$(name).value.trim() && !state.secrets?.[`${name}_set`]) fail(name, `${account === 'srt' ? 'SRT' : '코레일'} ${suffix === 'id' ? '계정을' : '비밀번호를'} 설정해 주세요.`);
    }
    if (config.connection_mode === 'korail' && config.auto_reserve && (config.disability_1_to_3_count || config.disability_4_to_6_count)) {
      fail('disability_1_to_3_count', '현재 코레일 연결은 장애인 운임 자동 예약을 지원하지 않습니다. 알림만 받기를 선택해 주세요.');
    }
  }
  if (invalid) {
    const first = form.querySelector('[aria-invalid=true]');
    const details = first?.closest('details');
    if (details) details.open = true;
    first?.focus();
    throw new Error('표시된 입력 항목을 확인해 주세요.');
  }
  return config;
}

function pendingSecrets() {
  return Object.fromEntries(secretFields.filter(name => $(name).value.trim()).map(name => [name, $(name).value]));
}

async function savePendingSecrets() {
  const payload = pendingSecrets();
  if (!Object.keys(payload).length) return false;
  const saved = await api.saveSecrets(payload);
  updateSecretStatus(saved);
  for (const name of Object.keys(payload)) $(name).value = '';
  return true;
}

async function freshIdleStatus() {
  await pollStatus();
  if (state.status.running) throw new Error('감시가 실행 중입니다. 먼저 중지한 뒤 설정을 변경해 주세요.');
}

async function perform(kind, work) {
  if (state.busy) return;
  state.busy = kind;
  clearErrors();
  displayMessage('notice', '');
  updateControls();
  try { await work(); }
  catch (error) { reportError(error); }
  finally { state.busy = ''; updateControls(); }
}

async function save() {
  if (!state.loaded || !state.fresh || state.status?.running) return;
  // Validate before disabling the fieldset: disabled controls skip browser validation.
  let config;
  try { config = validateInputs(); } catch (error) { reportError(error); return; }
  await perform('save', async () => {
    await freshIdleStatus();
    let publicSaved = false;
    try {
      state.config = await api.savePublic(config);
      publicSaved = true;
      await savePendingSecrets();
      fillConfig(state.config);
      displayMessage('notice', '설정을 저장했습니다. 비밀 정보 입력란은 저장 후 비웠습니다.');
      await pollStatus();
    } catch (error) {
      state.dirty = true;
      if (publicSaved) error.message = `여정 설정은 저장되었지만 계정 저장 또는 상태 확인이 완료되지 않았습니다. 입력한 계정은 저장이 성공한 경우에만 비웠습니다. ${error.message}`;
      else if (!error.status) error.message = `저장 결과를 확인할 수 없습니다. 입력 내용은 유지됩니다. 저장값 복원으로 서버 값을 다시 확인할 수 있습니다. ${error.message}`;
      throw error;
    }
  });
}

async function start() {
  if (!state.loaded || !state.fresh || state.status?.running || isUncertain()) return;
  let config;
  try { config = validateInputs(true); } catch (error) { reportError(error); return; }
  await perform('start', async () => {
    await freshIdleStatus();
    if (isUncertain()) throw new Error('예약 내역을 직접 확인하고 예약 있음 또는 예약 없음으로 확인을 완료해 주세요.');
    let secretsSaved = false;
    try {
      secretsSaved = await savePendingSecrets();
      const status = await api.start(config);
      state.config = config;
      fillConfig(config);
      applyStatus(status);
      displayMessage('notice', '시작 요청을 처리했습니다. 아래 실제 실행 상태를 확인해 주세요.');
      await fetchEvents();
    } catch (error) {
      state.dirty = true;
      const parts = [];
      if (secretsSaved) parts.push('입력한 계정은 저장되었습니다.');
      parts.push('감시 시작 결과를 확인해 주세요. 여정 설정은 서버에 반영되었을 수 있으며 화면의 입력 내용은 유지합니다.');
      error.message = `${parts.join(' ')} ${error.message}`;
      // A lost start response may leave a running engine or an uncertain lock.
      try { applyStatus(await api.status()); } catch { serverConnection(false); }
      throw error;
    }
  });
}

async function restore() {
  if (!state.loaded || state.status?.running) return;
  await perform('restore', async () => {
    await freshIdleStatus();
    const [config, secrets] = await Promise.all([api.publicConfig(), api.secretsStatus()]);
    state.config = config;
    fillConfig(config);
    updateSecretStatus(secrets);
    secretFields.forEach(name => { $(name).value = ''; });
    displayMessage('notice', '서버에 저장된 값을 복원했습니다. 저장하지 않은 계정 입력은 비웠습니다.');
  });
}

function addEvents(events) {
  if (!Array.isArray(events)) throw new Error('이벤트 목록을 읽을 수 없습니다.');
  for (const event of events) {
    if (event && typeof event.id === 'string' && typeof event.message === 'string') state.events.set(event.id, event);
  }
  const sorted = [...state.events.values()].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  state.events = new Map(sorted.slice(-300).map(event => [event.id, event]));
  renderEvents();
}

function renderEvents() {
  const fragment = document.createDocumentFragment();
  const levels = { info: '안내', warning: '주의', warn: '주의', error: '오류', success: '완료', debug: '상세' };
  for (const event of [...state.events.values()].reverse()) {
    const li = document.createElement('li');
    li.className = `event-item ${event.level === 'error' ? 'error' : ['warning', 'warn'].includes(event.level) ? 'warning' : ''}`;
    const time = document.createElement('time');
    time.textContent = readableTime(event.timestamp, true);
    time.dateTime = event.timestamp;
    const level = document.createElement('span');
    level.className = 'event-level'; level.textContent = levels[event.level] || event.level;
    const message = document.createElement('p'); message.textContent = event.message;
    const service = document.createElement('span'); service.className = 'event-service';
    service.textContent = event.service === 'srt' ? '이전 SRT 연결' : '코레일 연결';
    message.append(service); li.append(time, level, message); fragment.append(li);
  }
  $('event-list').replaceChildren(fragment);
  $('events-count').textContent = `${state.events.size}개 이벤트`;
  $('events-empty').hidden = state.events.size > 0;
  $('events-empty').textContent = '아직 감시 기록이 없습니다. 실제 이벤트가 발생하면 여기에 표시됩니다.';
}

async function fetchEvents() {
  try {
    addEvents(await api.events());
    $('event-fetch-state').textContent = `기록 동기화 ${new Date().toLocaleTimeString('ko-KR', { hour12: false })} · 최근 300개까지 표시`;
  } catch (error) {
    $('event-fetch-state').textContent = `기록 동기화 실패 · ${error.message}`;
    if (!state.events.size) $('events-empty').textContent = '기록을 불러오지 못했습니다. 상태 새로고침으로 다시 확인해 주세요.';
  }
}

async function pollStatus() {
  const sequence = ++state.statusSequence;
  try { applyStatus(await api.status(), sequence); }
  catch (error) {
    if (sequence !== state.statusSequence) return;
    serverConnection(false);
    $('phase-label').textContent = '최신 상태 확인 필요';
    $('running-label').textContent = '마지막 응답';
    $('phase-dot').className = 'phase-dot';
    $('status-message').textContent = '최신 상태를 확인하지 못했습니다. 표시된 상태는 마지막 서버 응답입니다.';
    $('server-state').title = error.message;
    throw error;
  }
}

function connectStream() {
  if (state.stream) return;
  if (!('EventSource' in window)) { $('stream-state').textContent = '실시간 연결 미지원 · 주기적으로 기록 조회'; return; }
  const stream = new EventSource('/api/events/stream');
  state.stream = stream;
  $('stream-state').textContent = '연결 중';
  stream.onopen = () => { $('stream-state').textContent = '이벤트 연결됨'; };
  stream.onerror = () => { $('stream-state').textContent = '재연결 중 · 기록은 주기적으로 조회'; };
  stream.onmessage = event => {
    try { addEvents([JSON.parse(event.data)]); }
    catch { $('stream-state').textContent = '이벤트 응답 확인 필요 · 기록 재조회 중'; void fetchEvents(); }
  };
}

function populateStations(routes) {
  catalog = createCatalog(routes);
  // Accessing localStorage itself can throw in restricted browser contexts.
  try { favorites = readFavorites(window.localStorage, catalog.names); } catch { favorites = new Set(); }
  const date = typeof catalog.source.effective_date === 'string' ? `${catalog.source.effective_date} 시간표 기준 · ` : '';
  $('station-source').textContent = `${date}날짜별 운행과 빈 좌석은 실제 조회로 확인합니다.`;
}

async function loadInitial() {
  await perform('load', async () => {
    try {
      const [config, secrets, routes, status] = await Promise.all([api.publicConfig(), api.secretsStatus(), api.stationRoutes(), api.status()]);
      populateStations(routes);
      state.config = config;
      state.loaded = true;
      fillConfig(config);
      updateSecretStatus(secrets);
      applyStatus(status);
      await fetchEvents();
      connectStream();
    } catch (error) {
      state.loaded = false;
      serverConnection(false);
      $('edit-state').textContent = '초기 설정을 불러오지 못했습니다. 새로고침으로 재시도하세요.';
      error.message = `초기 데이터를 불러오지 못했습니다. 저장과 시작은 사용할 수 없습니다. 상태 새로고침으로 다시 시도하세요. ${error.message}`;
      throw error;
    }
  });
}

for (const name of ['departure_time', 'max_departure_time']) {
  const options = [];
  for (let hour = 0; hour < 24; hour++) for (const minute of [0, 15, 30, 45]) {
    const h = String(hour).padStart(2, '0'); const m = String(minute).padStart(2, '0');
    const option = document.createElement('option'); option.value = `${h}${m}00`; option.textContent = `${h}:${m}`; options.push(option);
  }
  $(name).replaceChildren(...options);
}

form.addEventListener('submit', event => { event.preventDefault(); void save(); });
form.addEventListener('input', () => { if (state.loaded) { state.dirty = true; updateSummary(); } });
form.addEventListener('change', event => {
  if (state.loaded) {
    if (event.target.id === 'connection_mode') { clearErrors(); reconcileStations(); }
    state.dirty = true; updateSummary();
  }
});
$('swap-stations').addEventListener('click', () => {
  const departure = $('departure_station').value;
  const arrival = $('arrival_station').value;
  if (!state.loaded || state.busy || state.status?.running) return;
  clearErrors();
  if (!catalog.allows($('connection_mode').value, arrival, departure)) {
    displayMessage('station-notice', '반대 방향의 직통 구간이 없어 역을 바꿀 수 없습니다. 출발역과 도착역을 직접 선택해 주세요.');
    return;
  }
  $('departure_station').value = arrival; $('arrival_station').value = departure;
  reconcileStations(); state.dirty = true; updateSummary();
});
$('restore').addEventListener('click', () => { void restore(); });
$('start').addEventListener('click', () => { void start(); });
$('stop').addEventListener('click', () => { void perform('stop', async () => {
  try { applyStatus(await api.stop()); displayMessage('notice', '중지 요청을 처리했습니다. 예약 확인 필요 상태는 유지됩니다.'); }
  catch (error) { try { await pollStatus(); } catch { /* Preserve failure and allow another stop request. */ } throw error; }
  await fetchEvents();
}); });
$('refresh').addEventListener('click', () => {
  if (!state.loaded) { void loadInitial(); return; }
  void perform('refresh', async () => { await pollStatus(); await fetchEvents(); displayMessage('notice', '상태와 기록을 새로고침했습니다. 편집 중인 설정은 유지됩니다.'); });
});
for (const [id, found] of [['resolve-found', true], ['resolve-missing', false]]) {
  $(id).addEventListener('click', () => { void perform('resolve', async () => {
    await pollStatus();
    if (!isUncertain()) throw new Error('현재 확인이 필요한 예약이 없습니다. 최신 상태를 확인해 주세요.');
    await api.resolve(found);
    await pollStatus();
    displayMessage('notice', found ? '예약이 있는 것으로 확인했습니다. 실제 예약 수와 목표 상태를 확인해 주세요.' : '예약이 없는 것으로 확인했습니다. 필요하면 감시를 다시 시작하세요.');
  }); });
}

void loadInitial();
setInterval(async () => {
  if (state.busy || state.polling || document.hidden) return;
  state.polling = true;
  try {
    try { await pollStatus(); } catch { /* Connection state already explains the failure. */ }
    if (++state.eventFetches % 3 === 0 && state.loaded) await fetchEvents();
  } finally { state.polling = false; }
}, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !state.busy && !state.polling) void pollStatus().catch(() => {}); });
window.addEventListener('pagehide', () => { state.stream?.close(); state.stream = null; });
window.addEventListener('pageshow', () => { if (state.loaded) connectStream(); });
