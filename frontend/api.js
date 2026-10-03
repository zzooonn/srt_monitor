export class ApiError extends Error {
  constructor(message, status = 0, details = []) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

export async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      ...options,
      cache: 'no-store',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
    let body;
    try { body = await response.json(); }
    catch { throw new ApiError(`서버 응답을 읽을 수 없습니다. (HTTP ${response.status})`, response.status); }
    if (!response.ok) {
      const details = Array.isArray(body.detail) ? body.detail : [];
      const message = typeof body.detail === 'string' ? body.detail
        : details.length ? details.map(item => item.msg).join(' / ')
          : `요청을 완료하지 못했습니다. (HTTP ${response.status})`;
      throw new ApiError(message, response.status, details);
    }
    return body;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === 'AbortError') throw new ApiError('서버 응답 시간이 초과되었습니다. 결과가 반영되었을 수 있으니 상태를 새로고침해 주세요.');
    throw new ApiError('서버에 연결할 수 없습니다. 실행 상태와 로컬 연결을 확인한 뒤 다시 시도해 주세요.');
  } finally { clearTimeout(timeout); }
}

export const api = {
  publicConfig: () => request('/api/config/public'),
  secretsStatus: () => request('/api/config/secrets'),
  stations: () => request('/api/stations'),
  stationRoutes: () => request('/api/station-routes'),
  status: () => request('/api/status'),
  events: () => request('/api/events'),
  savePublic: data => request('/api/config/public', { method: 'POST', body: JSON.stringify(data) }),
  saveSecrets: data => request('/api/config/secrets', { method: 'POST', body: JSON.stringify(data) }),
  start: data => request('/api/start', { method: 'POST', body: JSON.stringify(data) }),
  stop: () => request('/api/stop', { method: 'POST' }),
  resolve: found => request('/api/reservation-lock/resolve', { method: 'POST', body: JSON.stringify({ found }) }),
};
