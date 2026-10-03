"""Run the dependency-free station selection rules without browser or accounts."""
import base64
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[1] / "frontend" / "station-model.js"
PRELUDE = """
import assert from 'node:assert/strict';
const { createCatalog, matchingStations, readFavorites, reconcileJourney } = await import(MODEL_URL);
const stations = [
  {name: '수원', aliases: ['SUWON']}, {name: '부산'}, {name: '동탄'},
  {name: '서대전'}, {name: '경주', aliases: ['신경주', 'Gyeongju']},
  {name: '진부(오대산)', aliases: ['진부']}, {name: '울산(통도사)', aliases: ['울산']},
];
const catalog = createCatalog({stations, destinations: {
  korail: {'수원': ['부산'], '부산': ['수원'], '동탄': ['서대전'], '서대전': ['수원']},
  legacy_srt: {'동탄': ['부산'], '부산': ['동탄']},
}});
"""


@pytest.mark.skipif(NODE is None, reason="Node.js is needed for frontend logic checks")
@pytest.mark.parametrize("checks", [
    """
    assert.equal(catalog.allows('korail', '수원', '동탄'), false);
    assert.equal(catalog.allows('korail', '동탄', '서대전'), true);
    assert.equal(catalog.allows('korail', '서대전', '동탄'), false);
    assert.deepEqual(catalog.arrivals('korail', '수원').map(s => s.name), ['부산']);
    assert.deepEqual(reconcileJourney(catalog, 'korail', '수원', '동탄'),
      {departure: '수원', arrival: '', departureCleared: false, arrivalCleared: true});
    assert.deepEqual(reconcileJourney(catalog, 'legacy_srt', '수원', '부산'),
      {departure: '', arrival: '', departureCleared: true, arrivalCleared: true});
    """,
    """
    assert.deepEqual(matchingStations(stations, ' 신 경 주 ', new Set()).map(s => s.name), ['경주']);
    assert.deepEqual(matchingStations(stations, ' gyeongJU ', new Set()).map(s => s.name), ['경주']);
    assert.deepEqual(matchingStations(stations, '진 부', new Set()).map(s => s.name), ['진부(오대산)']);
    assert.deepEqual(matchingStations(stations, '울산', new Set()).map(s => s.name), ['울산(통도사)']);
    assert.deepEqual(matchingStations(stations, '없는역', new Set()), []);
    assert.equal(matchingStations(stations, '', new Set(['부산']))[0].name, '부산');
    assert.deepEqual(matchingStations(catalog.arrivals('korail', '수원'), '동탄', new Set(['동탄'])), []);
    """,
    """
    assert.deepEqual([...readFavorites({getItem: () => '["부산","없는역",42,"부산"]'}, catalog.names)], ['부산']);
    assert.equal(readFavorites({getItem: () => '{broken'}, catalog.names).size, 0);
    assert.equal(readFavorites({getItem: () => '{"부산":true}'}, catalog.names).size, 0);
    assert.equal(readFavorites({getItem: () => { throw new Error('denied'); }}, catalog.names).size, 0);
    assert.throws(() => createCatalog({stations, destinations: {korail: {'수원':['없는역']}, legacy_srt: {'부산':['동탄']}}}));
    """,
], ids=["directed_routes_and_clearing", "alias_search_and_favorite_filter", "storage_recovery_and_bad_catalog"])
def test_station_selection_model(checks):
    url = "data:text/javascript;base64," + base64.b64encode(SOURCE.read_bytes()).decode("ascii")
    script = PRELUDE.replace("MODEL_URL", repr(url)) + checks
    result = subprocess.run([NODE, "--input-type=module"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
