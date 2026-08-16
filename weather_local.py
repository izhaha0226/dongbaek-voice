#!/usr/bin/env python3
"""날씨 — open-meteo 직접 조회 (0 토큰, API 키 없음).

"오늘 날씨 어때" 는 지금까지 답할 곳이 없었다. 클로드는 날씨 도구가 없어
모른다고 하고, 게이트키퍼도 실데이터가 필요하니 "[업무]" 로 물러난다.
위경도로 open-meteo 를 직접 부르면 0.5초·0원에 끝난다.

실패(네트워크·형식)는 None — 호출한 쪽이 클로드로 폴백한다. 동백의 다른
로컬 모듈들과 같은 계약이다.
"""

import json
import time
import urllib.request

import config

# WMO 날씨 코드 → 귀로 듣는 말. 세분류를 다 옮기면 어색해서 큰 덩이만.
_SKY = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "안개",
    51: "이슬비", 53: "이슬비", 55: "이슬비", 56: "이슬비", 57: "이슬비",
    61: "비", 63: "비", 65: "강한 비", 66: "비", 67: "비",
    71: "눈", 73: "눈", 75: "강한 눈", 77: "눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "소낙눈", 86: "소낙눈",
    95: "뇌우", 96: "뇌우", 99: "뇌우",
}


def _sky(code) -> str:
    try:
        return _SKY.get(int(code), "흐림")
    except (TypeError, ValueError):
        return "흐림"


_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,weather_code"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max"
    "&forecast_days=2&timezone=auto"
)

# 한 질문 뒤에 브리핑이 잇따르는 식으로 짧은 간격 재조회가 흔하다.
# 날씨는 10분 안에 안 변한다 — 캐시로 네트워크 왕복을 줄인다.
_cache: tuple[float, dict] | None = None
_CACHE_SEC = 600


def _fetch() -> dict:
    """예보 JSON 한 판. 실패는 예외로 — 판단은 부르는 쪽이 한다."""
    global _cache
    if _cache and time.time() - _cache[0] < _CACHE_SEC:
        return _cache[1]
    url = _URL.format(lat=config.WEATHER_LAT, lon=config.WEATHER_LON)
    with urllib.request.urlopen(url, timeout=config.WEATHER_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    _cache = (time.time(), data)
    return data


def _nun(word: str) -> str:
    """은/는 조사 — 받침 있으면 '은'. "서울는" 이라고 읽는 순간 기계 티가 난다."""
    ch = word[-1] if word else ""
    if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28:
        return "은"
    return "는"


def _rain_tail(prob) -> str:
    """비 올 확률은 들을 가치가 있을 때만 붙인다 — 5% 까지 읽으면 잔소리다."""
    try:
        p = int(prob)
    except (TypeError, ValueError):
        return ""
    return f", 비 올 확률 {p}%" if p >= 20 else ""


def today() -> str | None:
    """"오늘 날씨 어때" — 지금 기온·하늘과 오늘 범위."""
    try:
        d = _fetch()
        cur = d["current"]
        daily = d["daily"]
        now_t = round(cur["temperature_2m"])
        lo = round(daily["temperature_2m_min"][0])
        hi = round(daily["temperature_2m_max"][0])
        tail = _rain_tail(daily["precipitation_probability_max"][0])
        return (f"지금 {config.WEATHER_CITY} {now_t}도, {_sky(cur['weather_code'])}. "
                f"오늘은 {lo}도에서 {hi}도{tail}입니다.")
    except Exception:
        return None


def tomorrow() -> str | None:
    """"내일 날씨 어때" / "내일 비 와?"."""
    try:
        daily = _fetch()["daily"]
        lo = round(daily["temperature_2m_min"][1])
        hi = round(daily["temperature_2m_max"][1])
        tail = _rain_tail(daily["precipitation_probability_max"][1])
        return (f"내일 {config.WEATHER_CITY}{_nun(config.WEATHER_CITY)} "
                f"{lo}도에서 {hi}도, {_sky(daily['weather_code'][1])}{tail}입니다.")
    except Exception:
        return None
