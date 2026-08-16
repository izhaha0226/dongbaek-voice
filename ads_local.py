"""광고플랫폼 광고 실적 — Railway Postgres 직접 조회.

Claude 세션 기록을 뒤지거나 메일을 검색하는 게 아니라,
원본 DB 를 바로 읽는다. 토큰 0, 1초 안쪽.

psycopg 를 새로 깔지 않는다. psql 이 이미 있고, 읽기 전용 집계
한 방이면 드라이버를 얹을 이유가 없다.
"""

import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

# 광고플랫폼 저장소의 .env 를 그대로 읽는다.
# 동백에 접속정보를 복사해두면 비밀번호가 두 군데로 갈라진다.
ENV_PATH = Path("~/projects/my-ads/.env")

TIMEOUT = 20

# 광고주 이름은 DB 에 "(주)한빛리조트" 처럼 들어있다.
# 사장님은 "한빛리조트" 라고 부른다. 말한 대로 찾히게 별칭을 둔다.
ALIASES = {
    "한빛리조트": "한빛리조트",
    "한빛": "한빛리조트",
    "심플리케어": "심플리케어",
    "심플리": "심플리케어",
    "가나기획": "가나기획",
    "부띠크아지트": "부띠크아지트",
    "부티크아지트": "부띠크아지트",
    "아지트": "부띠크아지트",
    "서울베스트": "서울베스트",
    "미라클": "미라클",
    "멜로우": "멜로우",
    "이유빈": "이유빈",
}


def _db_url() -> str | None:
    # 키체인 먼저 (PLAN-unify 3단계 — 비밀은 키체인에 산다). 없으면
    # adsplatform 의 .env 폴백 — 그 파일은 그 프로젝트 소유라 안 지운다.
    import secrets_local

    url = secrets_local.get("railway-db")
    if url:
        return url
    try:
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _query(sql: str) -> list[list[str]] | None:
    url = _db_url()
    if not url:
        return None
    try:
        out = subprocess.run(
            ["psql", url, "-At", "-F", "\x1f", "-c", sql],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [r.split("\x1f") for r in out.stdout.strip().splitlines() if r]


# ─────────────────────────────────────────────────────────
# 기간 해석 — CLAUDE.md 의 일정 규칙과 다르다.
# 일정은 "앞으로", 실적은 "지나온 만큼"이다.
# "이번주 매출"은 앞으로 7일이 아니라 최근 7일이어야 말이 된다.
# ─────────────────────────────────────────────────────────
def period(text: str) -> tuple[date, date, str] | None:
    t = text.replace(" ", "")
    today = date.today()

    if "지난달" in t or "저번달" in t:
        first = today.replace(day=1)
        last_end = first - timedelta(days=1)
        return last_end.replace(day=1), last_end, "지난달"
    if "이번달" in t or "이달" in t or "당월" in t:
        return today.replace(day=1), today, "이번달"
    if "어제" in t:
        y = today - timedelta(days=1)
        return y, y, "어제"
    if "오늘" in t:
        return today, today, "오늘"
    if "이번주" in t or "최근일주일" in t or "일주일" in t:
        return today - timedelta(days=6), today, "최근 7일"
    return None


def advertiser(text: str) -> str | None:
    t = text.replace(" ", "")
    for word, canon in ALIASES.items():
        if word in t:
            return canon
    return None


# ─────────────────────────────────────────────────────────
# 숫자 읽기 — TTS 가 읽을 수 있게.
# "1618880원" 을 그대로 넘기면 못 알아듣는다. 만 단위로 끊는다.
# ─────────────────────────────────────────────────────────
def money(won: int) -> str:
    if won >= 100_000_000:
        return f"{won / 100_000_000:.1f}억원"
    if won >= 10_000:
        man = round(won / 10_000)
        return f"{man}만원"
    return f"{won}원"


def _rows(start: date, end: date, name: str | None):
    where = f"f.date between date '{start}' and date '{end}'"
    if name:
        # 별칭 표에서 온 값만 들어온다. 그래도 한 번 더 막는다.
        safe = re.sub(r"[^\w가-힣]", "", name)
        where += f" and a.name like '%{safe}%'"
    return _query(
        "select a.name, coalesce(sum(f.cost),0), coalesce(sum(f.conversion_revenue),0), "
        "coalesce(sum(f.conversions),0) "
        "from facts.daily_campaign_facts f "
        "join advertising.advertisers a on a.id = f.advertiser_id "
        f"where {where} group by a.name order by 3 desc, 2 desc"
    )


def speak(text: str) -> str | None:
    """실적 질문이면 읽어줄 문장, 아니면 None → Claude 로 넘어간다."""
    p = period(text)
    if p is None:
        return None
    start, end, label = p
    name = advertiser(text)

    rows = _rows(start, end, name)
    if rows is None:
        return None
    if not rows:
        who = name or "전체"
        return f"{who} {label} 데이터가 없습니다."

    if name:
        _, cost, rev, conv = rows[0]
        return _one(name, label, int(cost), int(rev), float(conv))

    cost = sum(int(r[1]) for r in rows)
    rev = sum(int(r[2]) for r in rows)
    top = rows[0][0]
    line = f"{label} 전체 광고비 {money(cost)}, 전환매출 {money(rev)}입니다."
    if rev > 0:
        line += f" 매출 1위는 {_short(top)}입니다."
    return line


def _short(db_name: str) -> str:
    return db_name.replace("(주)", "").strip()


def _one(name: str, label: str, cost: int, rev: int, conv: float) -> str:
    if rev == 0:
        return f"{name} {label} 광고비 {money(cost)}, 전환은 아직 없습니다."
    roas = round(rev / cost * 100) if cost else 0
    return (
        f"{name} {label} 광고비 {money(cost)}, 전환매출 {money(rev)}, "
        f"로아스 {roas}퍼센트입니다."
    )


# ─────────────────────────────────────────────────────────
# 브리핑용 분석 — 전체 → 매출 top2 광고주 → 캠페인 → 그룹 → 키워드 순.
# 전부 읽기 전용 집계 SQL 이다. 숫자를 LLM 에 맡기면 창작한다 —
# 숫자는 DB 가 만들고, 문장 틀은 여기서 고정한다.
# ─────────────────────────────────────────────────────────
def _top_level(table: str, join: str, name_col: str, start: date, end: date,
               limit: int, advertiser: str | None = None) -> list[tuple[str, int, int]] | None:
    """(이름, 광고비, 매출) 상위 — 매출 내림차순."""
    where = f"f.date between date '{start}' and date '{end}'"
    if advertiser:
        safe = re.sub(r"[^\w가-힣]", "", advertiser)
        where += ("and f.advertiser_id in (select id from advertising.advertisers "
                  f"where name like '%{safe}%') ".replace("and", " and", 1))
    rows = _query(
        f"select {name_col}, coalesce(sum(f.cost),0), coalesce(sum(f.conversion_revenue),0) "
        f"from {table} f join {join} where {where} "
        "group by 1 having coalesce(sum(f.cost),0) > 0 or coalesce(sum(f.conversion_revenue),0) > 0 "
        f"order by 3 desc, 2 desc limit {int(limit)}"
    )
    if rows is None:
        return None
    # "-" 는 매체가 키워드 없음을 적는 자리표시자다 — 읽어봐야 소음이다.
    return [(r[0], int(float(r[1])), int(float(r[2])))
            for r in rows if r[0].strip() not in ("-", "")]


def _top_campaigns(start, end, limit=2, advertiser=None):
    return _top_level("facts.daily_campaign_facts",
                      "advertising.campaigns c on c.id = f.campaign_id",
                      "c.name", start, end, limit, advertiser)


def _top_adgroups(start, end, limit=2, advertiser=None):
    return _top_level("facts.daily_adgroup_facts",
                      "advertising.adgroups g on g.id = f.adgroup_id",
                      "g.name", start, end, limit, advertiser)


def _top_keywords(start, end, limit=3, advertiser=None):
    return _top_level("facts.normalized_keyword_facts",
                      "advertising.keywords k on k.id = f.keyword_id",
                      "k.keyword_text", start, end, limit, advertiser)


def _latest_date(table: str, end: date) -> date | None:
    """그 테이블에 데이터가 있는 가장 최근 날짜 (end 이하)."""
    r = _query(f"select max(date) from facts.{table} where date <= date '{end}'")
    if not r or not r[0][0]:
        return None
    try:
        return date.fromisoformat(r[0][0])
    except ValueError:
        return None


def analysis(start: date, end: date, label: str) -> str | None:
    """성과 분석 한 문단 — 전체, 매출 top2 광고주(금액), 캠페인·그룹·키워드 순.

    그룹·키워드 팩트는 캠페인보다 이틀쯤 늦게 들어온다 (실측: 캠페인은 당일,
    키워드는 최신이 이틀 전). 조용히 생략하는 대신 가장 최근 날짜로 내려가되
    기준일을 함께 말한다 — 어제 걸 오늘 것처럼 말하는 것보다 낫다.
    """
    rows = _rows(start, end, None)
    if rows is None:
        return None
    if not rows:
        return f"{label} 광고 데이터가 없습니다."
    cost = sum(int(float(r[1])) for r in rows)
    rev = sum(int(float(r[2])) for r in rows)
    parts = [f"{label} 전체 광고비 {money(cost)}, 전환매출 {money(rev)}."]
    tops = [r for r in rows if int(float(r[2])) > 0][:2]
    if tops:
        parts.append("매출 상위는 "
                     + ", ".join(f"{_short(r[0])} {money(int(float(r[2])))}" for r in tops)
                     + "입니다.")
    for label2, fetch, table in (
        ("캠페인은", _top_campaigns, "daily_campaign_facts"),
        ("그룹은", _top_adgroups, "daily_adgroup_facts"),
        ("키워드는", _top_keywords, "normalized_keyword_facts"),
    ):
        top = fetch(start, end)
        note = ""
        if not top:
            d = _latest_date(table, end)
            if d is not None and not (start <= d <= end):
                top = fetch(d, d)
                note = f"{d.month}월 {d.day}일 기준 "
        if top:
            # 매출이 전부 0이면 광고비 순으로 — "0원 순" 은 보고가 아니다.
            use_rev = any(rev_ > 0 for _n, _c, rev_ in top)
            vals = ", ".join(f"{n} {money(rev_ if use_rev else c)}"
                             for n, c, rev_ in top)
            tail = " 순입니다." if use_rev else " 순입니다 (광고비 기준)."
            parts.append(f"{label2} {note}{vals}{tail}")
    return " ".join(parts)


def anomalies(day: date) -> list[str]:
    """그날이 평소(직전 7일)와 크게 다른 광고주 — 아침 브리핑의 경보 한 줄들.

    데이터가 일 단위라 '실시간 급락' 은 원리적으로 불가능하다 — 일 대비만
    본다. 로아스 반토막과 매출 두 배만 알린다. 그 사이는 소음이다.
    """
    today_rows = _rows(day, day, None)
    prev_rows = _rows(day - timedelta(days=7), day - timedelta(days=1), None)
    if not today_rows or not prev_rows:
        return []
    prev = {r[0]: (int(float(r[1])) / 7, int(float(r[2])) / 7) for r in prev_rows}
    out: list[str] = []
    for name, cost_s, rev_s, _c in today_rows:
        cost, rev = int(float(cost_s)), int(float(rev_s))
        if cost < 10_000 or name not in prev:
            continue                   # 소액은 흔들림이 커서 경보 가치가 없다
        p_cost, p_rev = prev[name]
        if p_cost <= 0 or p_rev <= 0:
            continue
        roas, p_roas = rev / cost, p_rev / p_cost
        short = _short(name)
        if roas < p_roas * 0.5:
            out.append(f"{short} 로아스가 평소의 절반 아래입니다 "
                       f"({round(p_roas*100)}에서 {round(roas*100)}퍼센트).")
        elif rev > p_rev * 2:
            out.append(f"{short} 매출이 평소의 두 배를 넘었습니다 ({money(rev)}).")
        if len(out) >= 2:
            break
    return out


def detail(start: date, end: date, label: str) -> str | None:
    """업체별 상세 — "자세히 보고해" 용. 광고주마다 로아스와 주력 캠페인·키워드."""
    rows = _rows(start, end, None)
    if rows is None:
        return None
    if not rows:
        return f"{label} 광고 데이터가 없습니다."
    out = [f"{label} 업체별 성과입니다."]
    # 키워드 팩트는 늦게 들어온다 — 기간에 없으면 가장 최근 날짜로.
    kw_start, kw_end = start, end
    if not _top_keywords(start, end, limit=1):
        d = _latest_date("normalized_keyword_facts", end)
        if d is not None:
            kw_start = kw_end = d
    for name, cost_s, rev_s, _conv in rows[:6]:
        cost, rev = int(float(cost_s)), int(float(rev_s))
        short = _short(name)
        roas = round(rev / cost * 100) if cost else 0
        line = f"{short}, 광고비 {money(cost)}에 매출 {money(rev)}"
        line += f", 로아스 {roas}퍼센트." if rev else ", 전환 없음."
        camps = _top_campaigns(start, end, limit=1, advertiser=short)
        kws = _top_keywords(kw_start, kw_end, limit=1, advertiser=short)
        if camps:
            line += f" 주력 캠페인 {camps[0][0]}."
        if kws:
            line += f" 상위 키워드 {kws[0][0]}."
        out.append(line)
    return " ".join(out)
