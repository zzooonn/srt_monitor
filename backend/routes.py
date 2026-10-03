"""Directed direct-train pairs from a reproducible official timetable snapshot."""
from functools import lru_cache
import json
from pathlib import Path

from .stations import ALL_STATIONS, ALL_STATION_NAMES, SRT_STATION_NAMES, normalize_station


def pairs_from_trains(trains):
    pairs = set()
    for train in trains:
        stops = train['stops']
        for index, departure in enumerate(stops):
            pairs.update((departure, arrival) for arrival in stops[index + 1:] if departure != arrival)
    return pairs


@lru_cache(maxsize=1)
def _snapshot():
    document = json.loads((Path(__file__).parent / 'data' / 'station_routes.json').read_text(encoding='utf-8'))
    trains = document['trains']
    unknown = {station for train in trains for station in train['stops']} - ALL_STATION_NAMES
    if unknown:
        raise ValueError(f'시간표에 등록되지 않은 역이 있습니다: {sorted(unknown)}')
    korail = pairs_from_trains(trains)
    # Compatibility scope, explicitly described in source. Do not merge stop
    # lists between trains or connect independent branches through a junction.
    legacy = pairs_from_trains(train for train in trains
                               if '수서' in (train['stops'][0], train['stops'][-1]))
    legacy = {(dep, arr) for dep, arr in legacy if dep in SRT_STATION_NAMES and arr in SRT_STATION_NAMES}
    return document['source'], {'korail': korail, 'legacy_srt': legacy}


def supports_route(mode, departure, arrival):
    _, pairs = _snapshot()
    return (normalize_station(departure), normalize_station(arrival)) in pairs.get(mode, set())


def validate_route(config):
    if not supports_route(config.connection_mode, config.departure_station, config.arrival_station):
        label = '코레일' if config.connection_mode == 'korail' else '이전 SRT'
        raise ValueError(f'{label} 연결에서 {config.departure_station} → {config.arrival_station} 직통 열차가 없습니다. 도착역을 다시 선택해 주세요.')


def station_route_catalog():
    source, pairs = _snapshot()
    names = [station['name'] for station in ALL_STATIONS]
    destinations = {mode: {dep: [arr for arr in names if (dep, arr) in values] for dep in names}
                    for mode, values in pairs.items()}
    return {'stations': ALL_STATIONS, 'destinations': destinations, 'source': source}
