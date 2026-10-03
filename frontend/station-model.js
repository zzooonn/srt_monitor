// Station names and directed routes come exclusively from the server catalogue.
export const FAVORITES_KEY = 'rail-monitor.station-favorites.v1';
export const searchKey = value => String(value).normalize('NFKC').toLocaleLowerCase('ko-KR').replace(/\s/g, '');

export function createCatalog(payload) {
  if (!payload || !Array.isArray(payload.stations) || !payload.stations.length) throw new Error('역·직통 구간 자료를 읽을 수 없습니다.');
  const stations = payload.stations;
  const names = new Set();
  for (const station of stations) {
    if (!station || typeof station.name !== 'string' || !station.name || names.has(station.name)
      || (station.aliases !== undefined && (!Array.isArray(station.aliases) || station.aliases.some(alias => typeof alias !== 'string')))) {
      throw new Error('서버의 역 목록 형식을 확인할 수 없습니다.');
    }
    names.add(station.name);
  }
  const routes = payload.destinations;
  for (const mode of ['korail', 'legacy_srt']) {
    const map = routes?.[mode];
    if (!map || typeof map !== 'object' || Array.isArray(map) || !Object.values(map).some(items => Array.isArray(items) && items.length)) throw new Error('서버의 직통 구간 목록을 확인할 수 없습니다.');
    for (const [departure, arrivals] of Object.entries(map)) {
      if (!names.has(departure) || !Array.isArray(arrivals) || arrivals.some(name => !names.has(name) || name === departure)) throw new Error('서버의 직통 구간 목록을 확인할 수 없습니다.');
    }
  }
  return {
    stations, names, source: payload.source || {},
    departures: mode => stations.filter(station => routes[mode]?.[station.name]?.length),
    arrivals: (mode, departure) => stations.filter(station => routes[mode]?.[departure]?.includes(station.name)),
    allows: (mode, departure, arrival) => names.has(departure) && names.has(arrival) && Boolean(routes[mode]?.[departure]?.includes(arrival)),
  };
}

export function matchingStations(stations, query, favorites) {
  const key = searchKey(query);
  return stations.filter(station => [station.name, ...(station.aliases || [])].some(value => searchKey(value).includes(key)))
    .sort((a, b) => Number(favorites.has(b.name)) - Number(favorites.has(a.name)));
}

export function readFavorites(storage, names) {
  try {
    const stored = JSON.parse(storage.getItem(FAVORITES_KEY) || '[]');
    return new Set(Array.isArray(stored) ? stored.filter(name => typeof name === 'string' && names.has(name)) : []);
  } catch { return new Set(); }
}

export function reconcileJourney(catalog, mode, departure, arrival) {
  const departureValid = catalog.departures(mode).some(station => station.name === departure);
  const nextDeparture = departureValid ? departure : '';
  const nextArrival = catalog.allows(mode, nextDeparture, arrival) ? arrival : '';
  return { departure: nextDeparture, arrival: nextArrival, departureCleared: Boolean(departure && !nextDeparture), arrivalCleared: Boolean(arrival && !nextArrival) };
}
