import json

import pytest

from backend.config_store import load_config, load_public_config, save_public_config, save_secrets
from backend.schemas import PublicConfig, SecretsPayload
from backend.stations import ALL_STATIONS, supports_ktx_route


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / 'web_config.json'
    monkeypatch.setattr('backend.config_store.CONFIG_PATH', path)
    return path


@pytest.mark.parametrize('fields', [
    {'departure_date': '20260230'}, {'departure_time': '240000'},
    {'max_departure_time': '236000'}, {'adult_count': 0},
    {'adult_count': 4, 'child_count': 1},
    {'departure_station': '서울', 'arrival_station': '서울'},
    {'check_special': False, 'check_normal': False},
])
def test_public_boundary_rejects_invalid_journey(fields):
    with pytest.raises(ValueError):
        PublicConfig(**fields)


def test_unified_defaults_and_station_aliases():
    config = PublicConfig(departure_station='신경주', arrival_station='사천')
    assert config.schema_version == 2
    assert config.connection_mode == 'korail'
    assert (config.departure_station, config.arrival_station) == ('경주', '여천')
    assert supports_ktx_route('수서', '동탄')
    names = [s['name'] for s in ALL_STATIONS]
    assert '신경주' not in names and '사천' not in names
    assert len(names) == len(set(names))


def test_legacy_migration_selects_explicit_enabled_ktx_route(config_file):
    raw = {'departure_station': '수서', 'arrival_station': '동탄',
           'monitor_ktx': True, 'ktx_departure_station': '서울',
           'ktx_arrival_station': '부산', 'ktx_check_special': False,
           'senior_count': 1, 'adult_count': 1, 'poll_interval': 47,
           'srt_id': 's', 'srt_password': 'p'}
    config_file.write_text(json.dumps(raw), encoding='utf-8')
    config = load_config()
    assert (config.departure_station, config.arrival_station) == ('서울', '부산')
    assert config.check_special is False and config.senior_count == 1
    assert config.poll_interval == 47 and config.srt_password == 'p'
    assert config.korail_id == '' and config.connection_mode == 'korail'
    assert config.migration_notice
    save_public_config(PublicConfig(schema_version=2, departure_station='동탄', arrival_station='부산'))
    backup = config_file.with_suffix('.json.v1.bak')
    assert json.loads(backup.read_text(encoding='utf-8')) == raw
    assert load_config().departure_station == '동탄'
    save_secrets(SecretsPayload(korail_id='new'))
    assert json.loads(backup.read_text(encoding='utf-8')) == raw


def test_legacy_enabled_without_route_preserves_main(config_file):
    config_file.write_text(json.dumps({'monitor_ktx': True, 'departure_station': '수서',
                                      'arrival_station': '동탄'}), encoding='utf-8')
    assert load_public_config().departure_station == '수서'


def test_v2_never_migrates_stale_ktx_fields():
    config = PublicConfig(schema_version=2, departure_station='수서', arrival_station='동탄',
                          monitor_ktx=True, ktx_departure_station='서울', ktx_arrival_station='부산')
    assert config.departure_station == '수서'


def test_partial_public_save_preserves_unedited_options(config_file):
    config_file.write_text(json.dumps({'schema_version': 2, 'senior_count': 1, 'adult_count': 1,
                                      'heartbeat_interval': 7200, 'korail_password': 'secret'}), encoding='utf-8')
    saved = save_public_config(PublicConfig(schema_version=2, departure_station='서울', arrival_station='부산'))
    assert saved.senior_count == 1 and saved.heartbeat_interval == 7200
    assert load_config().korail_password == 'secret'


def test_failed_atomic_replace_leaves_original(config_file, monkeypatch):
    raw = json.dumps({'schema_version': 2, 'korail_id': 'original'})
    config_file.write_text(raw, encoding='utf-8')
    def fail(*args):
        raise OSError('disk error')
    monkeypatch.setattr('os.replace', fail)
    with pytest.raises(OSError):
        save_secrets(SecretsPayload(korail_id='replacement'))
    assert config_file.read_text(encoding='utf-8') == raw


@pytest.mark.parametrize('departure,arrival', [('청량리', '강릉'), ('청량리', '안동'), ('판교', '문경')])
def test_high_speed_branch_routes_are_supported(departure, arrival):
    assert supports_ktx_route(departure, arrival)
    assert PublicConfig(departure_station=departure, arrival_station=arrival).arrival_station == arrival


def test_jinbu_alias_uses_official_station_name():
    config = PublicConfig(departure_station='청량리', arrival_station='진부')
    assert config.arrival_station == '진부(오대산)'
