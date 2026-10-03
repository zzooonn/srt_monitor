import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.stations import supports_ktx_route, supports_srt_route
from backend.routes import pairs_from_trains, validate_route
from backend.schemas import MonitorConfig, PublicConfig, PublicConfigUpdate
from backend.unified_monitor_service import UnifiedMonitorService
from tools.update_station_routes import parse_train_rows, workbook_rows


@pytest.mark.parametrize('departure,arrival', [
    ('수원', '동탄'), ('동탄', '수원'), ('수원', '수서'), ('수서', '수원'),
    ('부산', '목포'), ('광명', '동탄'), ('공주', '서대전'), ('수원', '수원'),
])
def test_impossible_direct_routes(departure, arrival):
    assert not supports_ktx_route(departure, arrival)


@pytest.mark.parametrize('departure,arrival', [
    ('수원', '서울'), ('서울', '수원'), ('수원', '부산'), ('부산', '수원'),
    ('청량리', '강릉'), ('판교', '문경'), ('수서', '동탄'), ('신경주', '부산'),
])
def test_actual_direct_routes(departure, arrival):
    assert supports_ktx_route(departure, arrival)


def test_legacy_routes_do_not_connect_branches():
    assert supports_srt_route('수서', '부산')
    assert supports_srt_route('오송', '동탄')
    assert not supports_srt_route('부산', '목포')
    assert not supports_srt_route('서울', '부산')
    assert not supports_srt_route('동탄', '동탄')


@pytest.fixture
def route_client(tmp_path, monkeypatch):
    path = tmp_path / 'web_config.json'
    # Reading an existing impossible route must remain possible for UI repair.
    path.write_text(json.dumps({'schema_version': 2, 'departure_station': '수원',
                               'arrival_station': '동탄'}), encoding='utf-8')
    monkeypatch.setattr('backend.config_store.CONFIG_PATH', path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.ktx_service.start = AsyncMock()
        app.state.srt_service.start = AsyncMock()
        yield client, path


def test_api_catalog_and_metadata(route_client):
    client, _ = route_client
    response = client.get('/api/station-routes')
    assert response.status_code == 200
    body = response.json()
    assert body['stations'] == client.get('/api/stations').json()
    assert body['source']['effective_date'] == '2026-10-01'
    assert body['source']['official_url'].startswith('https://www.korail.com/')
    assert '동탄' not in body['destinations']['korail']['수원']
    assert '부산' in body['destinations']['korail']['수원']
    assert body['destinations']['legacy_srt']['수원'] == []
    assert client.get('/api/config/public').json()['arrival_station'] == '동탄'


@pytest.mark.parametrize('endpoint,status', [('/api/config/public', 422), ('/api/start', 400)])
@pytest.mark.parametrize('body', [
    {'schema_version': 2},  # must validate merged stored route
    {'schema_version': 2, 'departure_station': '동탄', 'arrival_station': '수원'},
    {'schema_version': 2, 'connection_mode': 'legacy_srt',
     'departure_station': '부산', 'arrival_station': '목포'},
])
def test_invalid_route_rejected_before_writing_or_starting(route_client, endpoint, status, body):
    client, path = route_client
    before = path.read_bytes()
    response = client.post(endpoint, json=body)
    assert response.status_code == status
    assert path.read_bytes() == before
    app.state.ktx_service.start.assert_not_called()
    app.state.srt_service.start.assert_not_called()


def test_repair_invalid_stored_route(route_client):
    client, _ = route_client
    response = client.post('/api/config/public', json={'schema_version': 2, 'arrival_station': '부산'})
    assert response.status_code == 200
    assert response.json()['departure_station'] == '수원'


def test_no_transitive_union_and_no_automatic_reverse():
    pairs = pairs_from_trains([{'stops': ['서울', '대전']}, {'stops': ['대전', '부산']}])
    assert ('서울', '대전') in pairs
    assert ('대전', '부산') in pairs
    assert ('서울', '부산') not in pairs
    assert ('대전', '서울') not in pairs


def test_parser_skips_zero_passes_and_preserves_independent_train_halves():
    rows = [(1, {0: '열차번호', 1: '편성', 2: '서울', 3: '수원', 4: '부산', 5: '비고',
                 7: '열차번호', 8: '편성', 9: '부산', 10: '대전', 11: '수서', 12: '비고'}),
            (2, {0: 121.0, 1: 'KTX', 2: .25, 3: 0.0, 4: 1.01, 5: '금토일',
                 7: 302.0, 8: 'KTX-산천', 9: .5, 10: .7, 11: .8, 12: '매일'}),
            (3, {0: '※ 주의사항', 2: .5, 3: .6, 4: .7})]
    trains = parse_train_rows('test', rows)
    assert len(trains) == 2
    assert trains[0]['stops'] == ['서울', '부산']
    assert trains[0]['days'] == '금토일'
    assert trains[1]['stops'] == ['부산', '대전', '수서']
    assert ('서울', '수서') not in pairs_from_trains(trains)


def test_parser_rejects_unrecognized_layout():
    with pytest.raises(ValueError, match='missing remarks boundary'):
        parse_train_rows('test', [(1, {0: '열차번호', 1: '편성', 2: '서울'})])


def test_xlsx_reader_uses_cached_formula_values_and_shared_strings(tmp_path):
    from zipfile import ZipFile
    path = tmp_path / 'sample.xlsx'
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="테스트" r:id="r1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/sharedStrings.xml', f'<sst xmlns="{namespace}"><si><t>서울</t></si></sst>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{namespace}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1"><f>TIME(6,0,0)</f><v>0.25</v></c><c r="AA1" t="inlineStr"><is><t>부산</t></is></c></row></sheetData></worksheet>')
    assert list(workbook_rows(path)) == [('테스트', [(1, {0: '서울', 2: .25, 26: '부산'})])]


def test_service_validation_rejects_impossible_route_before_credentials():
    config = MonitorConfig(departure_station='수원', arrival_station='동탄')
    with pytest.raises(ValueError, match='직통'):
        UnifiedMonitorService(None, None, None).validate_start(config)


def test_new_official_stops_and_aliases(route_client):
    client, _ = route_client
    catalog = client.get('/api/station-routes').json()
    stations = {station['name']: station for station in catalog['stations']}
    assert '신경주' in stations['경주']['aliases']
    assert '진부' in stations['진부(오대산)']['aliases']
    for name in ('상봉', '덕소', '의성', '영천', '북울산', '태화강', '남창', '기장',
                 '신해운대', '센텀', '영덕', '울진', '삼척', '동해', '묵호', '정동진',
                 '가남', '감곡장호원', '앙성온천'):
        assert stations[name]['code'] is None
        assert catalog['destinations']['korail'][name]
    assert supports_ktx_route('수서', '여수엑스포')


@pytest.mark.parametrize('endpoint', ['/api/config/public', '/api/start'])
@pytest.mark.parametrize('stored,patch', [
    ({'departure_station': '수원', 'arrival_station': '부산'}, {'departure_station': '동탄'}),
    ({'departure_station': '서울', 'arrival_station': '부산'}, {'arrival_station': '오송'}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'adult_count': 1}, {'senior_count': 3}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'max_departure_time': '234500'},
     {'departure_time': '231500'}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'departure_time': '060000'},
     {'max_departure_time': '080000'}),
])
def test_partial_request_validates_relationships_after_merge(route_client, endpoint, stored, patch):
    client, path = route_client
    raw = {'schema_version': 2, 'korail_id': 'test', 'korail_password': 'test', **stored}
    path.write_text(json.dumps(raw), encoding='utf-8')
    response = client.post(endpoint, json={'schema_version': 2, **patch})
    assert response.status_code == 200, response.text
    saved = json.loads(path.read_text(encoding='utf-8'))
    for field, value in {**stored, **patch}.items():
        assert saved[field] == value


@pytest.mark.parametrize('endpoint', ['/api/config/public', '/api/start'])
@pytest.mark.parametrize('stored,patch', [
    ({'departure_station': '수원', 'arrival_station': '부산'}, {'arrival_station': '동탄'}),
    ({'departure_station': '서울', 'arrival_station': '부산'}, {'departure_station': '부산'}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'adult_count': 2, 'senior_count': 2},
     {'child_count': 1}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'check_special': False},
     {'check_normal': False}),
    ({'departure_station': '서울', 'arrival_station': '부산', 'max_departure_time': '210000'},
     {'departure_time': '220000'}),
])
def test_invalid_merged_relationships_do_not_write_or_start(route_client, endpoint, stored, patch):
    client, path = route_client
    raw = {'schema_version': 2, 'korail_id': 'test', 'korail_password': 'test', **stored}
    path.write_text(json.dumps(raw), encoding='utf-8')
    before = path.read_bytes()
    response = client.post(endpoint, json={'schema_version': 2, **patch})
    assert response.status_code in (400, 422)
    assert path.read_bytes() == before
    app.state.ktx_service.start.assert_not_called()
    app.state.srt_service.start.assert_not_called()


@pytest.mark.parametrize('model', [PublicConfig, MonitorConfig])
def test_complete_models_keep_relationship_validation(model):
    with pytest.raises(ValueError, match='서로 달라야'):
        model(departure_station='동탄', arrival_station='동탄')
    with pytest.raises(ValueError, match='총 인원'):
        model(adult_count=2, senior_count=3)
    with pytest.raises(ValueError, match='좌석종류'):
        model(check_normal=False, check_special=False)
    with pytest.raises(ValueError, match='빠릅니다'):
        model(departure_time='200000', max_departure_time='190000')


def test_request_patch_preserves_aliases_omission_field_checks_and_secret_boundary():
    patch = PublicConfigUpdate(schema_version=2, arrival_station='신경주', korail_password='not-public')
    assert patch.model_dump(exclude_unset=True) == {'schema_version': 2, 'arrival_station': '경주'}
    with pytest.raises(ValueError):
        PublicConfigUpdate(poll_interval=1)
    with pytest.raises(ValueError):
        PublicConfigUpdate(adult_count=5)
    with pytest.raises(ValueError):
        PublicConfigUpdate(departure_time='201000')


@pytest.mark.parametrize('endpoint', ['/api/config/public', '/api/start'])
@pytest.mark.parametrize('mode,prefix', [('legacy_srt', 'srt'), ('korail', 'korail')])
def test_unversioned_partial_request_preserves_connection_and_selected_credentials(route_client, endpoint, mode, prefix):
    client, path = route_client
    original = {'schema_version': 2, 'connection_mode': mode, 'departure_station': '수서',
                'arrival_station': '부산', 'migration_notice': '기존 안내',
                f'{prefix}_id': 'private-test-id', f'{prefix}_password': 'private-test-password'}
    path.write_text(json.dumps(original), encoding='utf-8')
    response = client.post(endpoint, json={'poll_interval': 60})
    assert response.status_code == 200, response.text
    saved = json.loads(path.read_text(encoding='utf-8'))
    for key, value in original.items():
        assert saved[key] == value
    assert saved['poll_interval'] == 60
    assert saved['connection_mode'] == mode
    assert saved['migration_notice'] == '기존 안내'
    assert saved[f'{prefix}_id'] == original[f'{prefix}_id']
    assert saved[f'{prefix}_password'] == original[f'{prefix}_password']
    assert 'private-test' not in response.text
    if endpoint == '/api/start':
        selected = app.state.srt_service if mode == 'legacy_srt' else app.state.ktx_service
        other = app.state.ktx_service if mode == 'legacy_srt' else app.state.srt_service
        assert selected.start.await_count == 1
        other.start.assert_not_called()
        assert getattr(selected.start.await_args.args[0], f'{prefix}_password') == 'private-test-password'


def test_unversioned_partial_alias_normalizes_without_injecting_mode():
    patch = PublicConfigUpdate(arrival_station='신경주')
    assert patch.model_dump(exclude_unset=True) == {'schema_version': 2, 'arrival_station': '경주'}


def test_explicit_v1_request_still_migrates():
    patch = PublicConfigUpdate(schema_version=1, monitor_ktx=True,
                               ktx_departure_station='서울', ktx_arrival_station='부산')
    fields = patch.model_dump(exclude_unset=True)
    assert fields['schema_version'] == 2
    assert fields['connection_mode'] == 'korail'
    assert fields['departure_station'] == '서울'
    assert fields['arrival_station'] == '부산'
    assert fields['migration_notice']
