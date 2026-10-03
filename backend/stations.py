SRT_STATIONS = [
    {"name": "수서", "code": "0551", "lines": ["공통"]},
    {"name": "동탄", "code": "0552", "lines": ["공통"]},
    {"name": "평택지제", "code": "0553", "lines": ["공통"]},
    {"name": "천안아산", "code": "0502", "lines": ["공통"]},
    {"name": "오송", "code": "0297", "lines": ["공통"]},
    {"name": "대전", "code": "0010", "lines": ["경부", "경전", "동해"]},
    {"name": "김천(구미)", "code": "0507", "lines": ["경부", "동해"]},
    {"name": "서대구", "code": "0506", "lines": ["경부"]},
    {"name": "동대구", "code": "0015", "lines": ["경부", "경전", "동해"]},
    {"name": "경주", "code": "0508", "lines": ["경부"]},
    {"name": "울산(통도사)", "code": "0509", "lines": ["경부"]},
    {"name": "부산", "code": "0020", "lines": ["경부"]},
    {"name": "공주", "code": "0514", "lines": ["호남", "전라"]},
    {"name": "익산", "code": "0030", "lines": ["호남", "전라"]},
    {"name": "정읍", "code": "0033", "lines": ["호남"]},
    {"name": "광주송정", "code": "0036", "lines": ["호남"]},
    {"name": "나주", "code": "0037", "lines": ["호남"]},
    {"name": "목포", "code": "0041", "lines": ["호남"]},
    {"name": "전주", "code": "0045", "lines": ["전라"]},
    {"name": "남원", "code": "0048", "lines": ["전라"]},
    {"name": "곡성", "code": "0049", "lines": ["전라"]},
    {"name": "구례구", "code": "0050", "lines": ["전라"]},
    {"name": "순천", "code": "0051", "lines": ["전라"]},
    {"name": "여천", "code": "0139", "lines": ["전라"]},
    {"name": "여수EXPO", "code": "0053", "lines": ["전라"]},
    {"name": "밀양", "code": "0017", "lines": ["경전"]},
    {"name": "진영", "code": "0056", "lines": ["경전"]},
    {"name": "창원중앙", "code": "0512", "lines": ["경전"]},
    {"name": "창원", "code": "0057", "lines": ["경전"]},
    {"name": "마산", "code": "0059", "lines": ["경전"]},
    {"name": "진주", "code": "0063", "lines": ["경전"]},
    {"name": "포항", "code": "0515", "lines": ["동해"]},
]

STATION_ALIASES = {"신경주": "경주", "사천": "여천", "진부": "진부(오대산)",
                   "김천구미": "김천(구미)", "울산": "울산(통도사)", "판교(경기)": "판교",
                   "여수엑스포": "여수EXPO"}


def normalize_station(value):
    # Leave malformed input to Pydantic's field type validation at the API boundary.
    if not isinstance(value, str):
        return value
    value = value.strip()
    return STATION_ALIASES.get(value, value)


SRT_STATION_NAMES = {station["name"] for station in SRT_STATIONS}

KTX_STATIONS = [
    # 서울 접근 구간 (공통 — 모든 방면 KTX가 경유)
    {"name": "행신", "code": "0901", "lines": ["공통"]},
    {"name": "서울", "code": "0001", "lines": ["공통"]},
    {"name": "용산", "code": "0002", "lines": ["공통"]},
    {"name": "영등포", "code": "0902", "lines": ["공통"]},
    {"name": "광명", "code": "0004", "lines": ["공통"]},
    {"name": "수원", "code": "0011", "lines": ["경부"]},
    {"name": "천안아산", "code": "0502", "lines": ["공통"]},
    {"name": "오송", "code": "0297", "lines": ["공통"]},
    # 경부선
    {"name": "대전", "code": "0010", "lines": ["경부", "경전", "동해"]},
    {"name": "김천(구미)", "code": "0507", "lines": ["경부", "동해"]},
    {"name": "서대구", "code": "0506", "lines": ["경부"]},
    {"name": "동대구", "code": "0015", "lines": ["경부", "경전", "동해"]},
    {"name": "경산", "code": "0903", "lines": ["경부", "경전"]},
    {"name": "경주", "code": "0508", "lines": ["경부"]},
    {"name": "물금", "code": "0904", "lines": ["경부", "경전"]},
    {"name": "울산(통도사)", "code": "0509", "lines": ["경부"]},
    {"name": "구포", "code": "0905", "lines": ["경부", "경전"]},
    {"name": "부산", "code": "0020", "lines": ["경부"]},
    # 경전선
    {"name": "밀양", "code": "0017", "lines": ["경전"]},
    {"name": "진영", "code": "0056", "lines": ["경전"]},
    {"name": "창원중앙", "code": "0512", "lines": ["경전"]},
    {"name": "창원", "code": "0057", "lines": ["경전"]},
    {"name": "마산", "code": "0059", "lines": ["경전"]},
    {"name": "진주", "code": "0063", "lines": ["경전"]},
    # 호남선
    {"name": "공주", "code": "0514", "lines": ["호남", "전라"]},
    {"name": "익산", "code": "0030", "lines": ["호남", "전라"]},
    {"name": "김제", "code": "0909", "lines": ["호남"]},
    {"name": "정읍", "code": "0033", "lines": ["호남"]},
    {"name": "장성", "code": "0910", "lines": ["호남"]},
    {"name": "광주송정", "code": "0036", "lines": ["호남"]},
    {"name": "나주", "code": "0037", "lines": ["호남"]},
    {"name": "목포", "code": "0041", "lines": ["호남"]},
    {"name": "서대전", "code": "0906", "lines": ["호남"]},
    {"name": "계룡", "code": "0907", "lines": ["호남"]},
    {"name": "논산", "code": "0908", "lines": ["호남"]},
    # 전라선
    {"name": "전주", "code": "0045", "lines": ["전라"]},
    {"name": "남원", "code": "0048", "lines": ["전라"]},
    {"name": "곡성", "code": "0049", "lines": ["전라"]},
    {"name": "구례구", "code": "0050", "lines": ["전라"]},
    {"name": "순천", "code": "0051", "lines": ["전라"]},
    {"name": "여천", "code": "0139", "lines": ["전라"]},

    {"name": "여수EXPO", "code": "0053", "lines": ["전라"]},
    # 동해선
    {"name": "포항", "code": "0515", "lines": ["동해"]},
]

KTX_STATIONS = [*KTX_STATIONS, *[s for s in SRT_STATIONS if s["name"] in {"수서", "동탄", "평택지제"}]]
# Official Korail branch station names; search uses names, so unverified codes
# remain null. Inclusion means queryable stations, not a direct train for every pair.
# https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&key=911&nttNo=7671
# https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&key=911&nttNo=6830
# https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&key=911&nttNo=24210
# https://info.korail.com/info/selectBbsNttView.do?bbsNo=199&key=911&nttNo=25904
# https://www.korea.kr/news/policyNewsView.do?newsId=148936722&pWise=Letter
KTX_STATIONS += [
    {"name": name, "code": None, "lines": [line]}
    for line, names in (
        ("중앙", ["청량리", "양평", "서원주", "원주", "제천", "단양", "풍기", "영주", "안동", "부전"]),
        ("강릉", ["만종", "횡성", "둔내", "평창", "진부(오대산)", "강릉"]),
        ("중부내륙", ["판교", "부발", "충주", "문경", "살미", "수안보온천", "연풍"]),
    )
    for name in names
]
# Additional stops verified in the official 2026-10-01 timetable. Codes are
# intentionally unset: the Korail connector searches by station name.
KTX_STATIONS += [
    {"name": name, "code": None, "lines": [line]}
    for line, names in (
        ("중앙", ["상봉", "덕소", "의성", "영천", "북울산", "태화강", "남창", "기장", "신해운대", "센텀"]),
        ("동해", ["영덕", "울진", "삼척", "동해", "묵호", "정동진"]),
        ("중부내륙", ["가남", "감곡장호원", "앙성온천"]),
    )
    for name in names
]
KTX_STATION_NAMES = {station["name"] for station in KTX_STATIONS}


def _station_line(station: dict) -> str:
    return "·".join(station["lines"])


def _merged_stations() -> list[dict]:
    stations_by_name: dict[str, dict] = {}

    for service, source in (("SRT", SRT_STATIONS), ("KTX", KTX_STATIONS)):
        for station in source:
            name = station["name"]
            merged = stations_by_name.setdefault(
                name,
                {
                    "name": name,
                    "code": station["code"],
                    "line": _station_line(station),
                    "lines": [],
                    "services": [],
                    "codes": {},
                    "aliases": [alias for alias, canonical in STATION_ALIASES.items() if canonical == name],
                },
            )
            merged["codes"][service.lower()] = station["code"]
            if service not in merged["services"]:
                merged["services"].append(service)
            for line in station["lines"]:
                if line not in merged["lines"]:
                    merged["lines"].append(line)
            merged["line"] = _station_line({"lines": merged["lines"]})

    return list(stations_by_name.values())


ALL_STATIONS = _merged_stations()
ALL_STATION_NAMES = {station["name"] for station in ALL_STATIONS}


def supports_srt_route(departure: str, arrival: str) -> bool:
    from .routes import supports_route
    return supports_route("legacy_srt", departure, arrival)


def supports_ktx_route(departure: str, arrival: str) -> bool:
    from .routes import supports_route
    return supports_route("korail", departure, arrival)

