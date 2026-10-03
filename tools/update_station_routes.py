"""Rebuild direct routes from the official Korail XLSX (standard library only).

Run: python -m tools.update_station_routes --input timetable.xlsx
Without --input, downloads the pinned public attachment (no login required).
Only a single train's ordered, nonzero time cells contribute directed pairs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from backend.stations import normalize_station

OFFICIAL_URL = 'https://www.korail.com/com/userBoard.do?mode=list&schBcid=ticketTable'
DOWNLOAD_URL = 'https://www.korail.com/file/cubedata/COMMON/jfile/202609/23/202609231a0cb38a2c5200.xlsx'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def column_index(address):
    value = 0
    for letter in re.match(r'[A-Z]+', address).group():
        value = value * 26 + ord(letter) - ord('A') + 1
    return value - 1


def workbook_rows(path):
    """Read cached cell values; never execute workbook formulas or macros."""
    with ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in node.findall('.//s:t', NS))
                       for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        relations = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(
            archive.read('xl/_rels/workbook.xml.rels'))}
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in workbook.findall('s:sheets/s:sheet', NS):
            relationship = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = relations[relationship]
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            rows = []
            for row in ET.fromstring(archive.read(target)).findall('s:sheetData/s:row', NS):
                values = {}
                for cell in row.findall('s:c', NS):
                    value = cell.find('s:v', NS)
                    kind = cell.get('t')
                    if kind == 'inlineStr':
                        decoded = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    elif value is None or value.text is None:
                        continue
                    elif kind == 's':
                        decoded = strings[int(value.text)]
                    elif kind in ('str', 'e', 'b'):
                        decoded = value.text
                    else:
                        decoded = float(value.text)
                    values[column_index(cell.attrib['r'])] = decoded
                rows.append((int(row.attrib['r']), values))
            yield sheet.attrib['name'], rows


def is_stop(value):
    # This workbook formats numeric zero as a blank/non-stop, including formulas.
    # Values >=1 are next-day times; retain them rather than dropping late trains.
    if isinstance(value, (int, float)):
        return 0 < value < 2
    return isinstance(value, str) and bool(re.fullmatch(r'(?:[0-2]?\d):[0-5]\d(?::[0-5]\d)?', value.strip()))


def parse_train_rows(sheet, rows):
    headers = []
    trains = []
    for row_number, row in rows:
        starts = [col for col, value in row.items() if str(value).replace(' ', '') == '열차번호']
        if starts:
            headers = []
            for start in sorted(starts):
                if row.get(start + 1) != '편성':
                    raise ValueError(f'{sheet}: unsupported formation header at row {row_number}')
                end = start + 2
                while row.get(end) and not str(row[end]).startswith('비고'):
                    end += 1
                if not str(row.get(end, '')).startswith('비고'):
                    raise ValueError(f'{sheet}: missing remarks boundary')
                headers.append((start, end, [(c, normalize_station(str(row[c])))
                                            for c in range(start + 2, end)]))
            continue
        for start, end, stations in headers:
            number, formation = row.get(start), row.get(start + 1)
            if not isinstance(number, (int, float)) or int(number) != number:
                continue
            if not isinstance(formation, str) or not formation.startswith('KTX'):
                raise ValueError(f'{sheet} row {row_number}: unexpected train type {formation!r}')
            stops = [name for col, name in stations if is_stop(row.get(col))]
            if len(stops) < 2:
                raise ValueError(f'{sheet} row {row_number}: too few stops')
            trains.append({'sheet': sheet, 'row': row_number, 'train': str(int(number)),
                           'formation': formation, 'days': str(row.get(end, '')),
                           'stops': stops})
    if not trains:
        raise ValueError(f'No trains found in {sheet}')
    return trains


def build_document(path):
    trains = [train for sheet, rows in workbook_rows(path) for train in parse_train_rows(sheet, rows)]
    sheets = sorted({train['sheet'] for train in trains})
    return {'source': {
        'effective_date': '2026-10-01', 'retrieved_date': '2026-10-03',
        'title': 'KTX 시간표(2026. 10. 1. 기준)', 'official_url': OFFICIAL_URL,
        'download_url': DOWNLOAD_URL, 'board_id': 26041,
        'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        'sheets': sheets, 'train_rows': len(trains),
        'method': '개별 열차의 실제 정차 순서에서 앞역→뒷역 쌍만 추출; 환승 연결 제외',
        'note': '요일별 열차를 포함한 직통 운행 범위입니다. 선택한 날짜·시간의 운행과 잔여석은 조회 시 확인합니다.',
        'legacy_srt_scope': '동일 시간표의 수서 출발·도착 개별 열차를 기존 SRT 역 코드 지원 범위로 제한한 호환 목록입니다. 구형 SRT 연결의 실제 운영을 보장하지 않습니다.',
    }, 'trains': trains}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('backend/data/station_routes.json'))
    args = parser.parse_args()
    path = args.input
    if path is None:
        path = Path('tools/source/korail-2026-10-01.xlsx')
        path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(DOWNLOAD_URL, timeout=60) as response:
            path.write_bytes(response.read())
    document = build_document(path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Parsed {len(document['trains'])} train rows from {len(document['source']['sheets'])} sheets")


if __name__ == '__main__':
    main()
