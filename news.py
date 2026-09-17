# =====================================================================
# news.py  ―  아침 브리핑 생성 스크립트
# ---------------------------------------------------------------------
# 하는 일
#   1. 구글 뉴스 RSS에서 카드별 기사를 모은다
#   2. 기간·제외 단어·중복을 걸러낸다
#   3. 야구는 날짜별로 경기 결과 기사 1건씩 고른다
#   4. template.html 에 결과를 끼워 넣어 docs/index.html 로 저장한다
#
# 실행 방법
#   로컬:   pip install -r requirements.txt  →  python news.py
#   자동:   GitHub Actions 가 매일 아침 대신 실행 (.github/workflows/daily.yml)
# =====================================================================

import html                                    # 특수문자 처리 (&amp; → & 등)
import re                                      # 정규식 (제목 정리, 점수 찾기)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote                 # 검색어를 URL 에 넣을 수 있게 변환

import feedparser                              # RSS 읽는 라이브러리

import config                                  # 같은 폴더의 config.py

# ---------------------------------------------------------------------
# 공통 값
# ---------------------------------------------------------------------
# GitHub 서버는 UTC(영국 시간) 기준이라, 한국 시간(UTC+9)을 직접 지정한다.
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

# 일부 사이트는 프로그램 접속을 막기 때문에 브라우저처럼 이름을 붙여 요청한다.
USER_AGENT = "Mozilla/5.0 (morning-brief)"

# 이 스크립트가 있는 폴더 (어디서 실행하든 경로가 꼬이지 않도록)
BASE_DIR = Path(__file__).parent


# =====================================================================
# 1. 구글 뉴스 수집
# =====================================================================
def fetch_google_news(query: str, days: int) -> list[dict]:
    """
    구글 뉴스에서 검색어로 기사를 가져온다.

    query : 검색어 (config.py 에 적은 것)
    days  : 최근 며칠 치를 검색할지 (구글 뉴스의 when:Nd 옵션)

    반환값: [{"title", "link", "source", "when"}, ...]
    """
    # 구글 뉴스는 검색 결과를 RSS 로도 제공한다.
    # hl/gl/ceid 는 한국어·한국 지역 뉴스를 받기 위한 옵션.
    url = (
        "https://news.google.com/rss/search?q="
        + quote(f"{query} when:{days}d")
        + "&hl=ko&gl=KR&ceid=KR:ko"
    )

    # 네트워크 오류가 나도 전체가 멈추지 않도록 try 로 감싼다.
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"[실패] {query}: {e}")
        return []

    articles = []
    for entry in feed.entries:
        # 발행 시각이 없는 기사는 정렬·필터가 불가능하니 건너뛴다.
        published = entry.get("published_parsed")
        if not published:
            continue

        title = html.unescape(entry.get("title", "")).strip()
        source = entry.get("source", {}).get("title", "")

        # 구글 뉴스 제목은 "기사 제목 - 언론사" 형태라 뒤의 언론사 부분을 뗀다.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]

        # published_parsed 는 UTC 기준 → 한국 시간으로 변환
        when = datetime(*published[:6], tzinfo=timezone.utc).astimezone(KST)

        articles.append({
            "title": title,
            "link": entry.get("link", ""),
            "source": source,
            "when": when,
        })

    # GitHub Actions 로그에서 수집 상황을 확인할 수 있도록 출력
    print(f"[수집] {len(articles):3d}건  {query}")
    return articles


def normalize(title: str) -> str:
    """
    중복 판별용 키를 만든다.
    특수문자·공백을 지우고 앞 30글자만 비교해서,
    언론사만 다르고 제목이 거의 같은 기사를 같은 기사로 본다.
    """
    return re.sub(r"[\W_]+", "", title.lower())[:30]


# =====================================================================
# 2. 뉴스 카드 만들기
# =====================================================================
def build_news_cards() -> list[dict]:
    """
    config.NEWS 의 카드마다 기사를 모아 정리한다.

    반환값: [{"title": 카드제목, "items": [기사, ...]}, ...]
    """
    cutoff = NOW - timedelta(hours=config.NEWS_HOURS)  # 이 시각 이전 기사는 버림

    # seen: 이미 다른 카드(또는 같은 카드)에 나온 기사 키 모음
    #       → 경제 카드와 정치 카드에 같은 기사가 두 번 나오지 않게 함
    seen = set()
    cards = []

    for card in config.NEWS:
        # 최신 기사가 위로 오도록 시간 역순 정렬
        # (검색 기간은 2일로 넉넉히 잡고, 아래에서 NEWS_HOURS 로 다시 자름)
        articles = sorted(
            fetch_google_news(card["query"], days=2),
            key=lambda a: a["when"],
            reverse=True,
        )

        picked = []
        for a in articles:
            # (1) 기간 밖이면 제외
            if a["when"] < cutoff:
                continue
            # (2) 제외 단어가 제목에 있으면 제외
            if any(word in a["title"] for word in card["exclude"]):
                continue
            # (3) 이미 나온 기사면 제외
            key = normalize(a["title"])
            if key in seen:
                continue

            seen.add(key)
            picked.append(a)

            # 최대 개수를 채우면 이 카드는 끝
            if len(picked) >= config.NEWS_MAX:
                break

        cards.append({"title": card["title"], "items": picked})

    return cards


# =====================================================================
# 3. 야구 경기 결과 만들기
# =====================================================================
# 제목에 점수가 있는지 찾는 패턴:  "5-3", "5:3", "5대3" 등
SCORE_PATTERN = re.compile(r"\d{1,2}\s*[-:대]\s*\d{1,2}")

# 경기 결과 기사로 볼 만한 단어들
# (점수가 없어도 이런 단어가 있으면 결과 기사로 판단)
RESULT_PATTERN = re.compile(
    r"\d{1,2}\s*[-:대]\s*\d{1,2}"
    r"|꺾|제압|승리|패배|연승|연패|역전|완승|완패|끝내기|무승부|위닝|루징|스윕"
)


def build_baseball() -> list[dict]:
    """
    팀별로 최근 경기 결과 기사를 날짜당 1건씩 골라 최근 N경기를 만든다.

    반환값: [{"title": 팀이름, "items": [기사, ...]}, ...]
    """
    teams = []

    for team in config.BASEBALL:
        articles = sorted(
            fetch_google_news(team["query"], days=config.BASEBALL_DAYS),
            key=lambda a: a["when"],
            reverse=True,
        )

        # 날짜 → (기사, 점수포함여부)
        # 같은 날 기사가 여러 개면 하나만 남긴다.
        by_date = {}

        for a in articles:
            # 팀 이름이 제목에 없으면 다른 팀 기사일 가능성이 높음
            if team["must"] not in a["title"]:
                continue
            # 결과 기사가 아니면 (예: 선수 인터뷰, 트레이드 소식) 제외
            if not RESULT_PATTERN.search(a["title"]):
                continue

            date = a["when"].date()
            has_score = bool(SCORE_PATTERN.search(a["title"]))

            # 그 날짜의 첫 기사이거나,
            # 기존 기사에는 점수가 없는데 이번 기사에는 점수가 있으면 교체
            if date not in by_date or (has_score and not by_date[date][1]):
                by_date[date] = (a, has_score)

        # 최신 날짜부터 정렬해서 N경기만 남긴다.
        recent_dates = sorted(by_date.keys(), reverse=True)[: config.BASEBALL_GAMES]
        items = [by_date[d][0] for d in recent_dates]

        teams.append({"title": team["title"], "items": items})

    return teams


# =====================================================================
# 4. HTML 만들기
# =====================================================================
def esc(text: str) -> str:
    """HTML 안에 넣을 문자열의 <, >, " 등을 안전하게 바꾼다."""
    return html.escape(text or "")


def time_ago(when: datetime) -> str:
    """기사 시각을 '3시간 전' 같은 형태로 바꾼다."""
    minutes = int((NOW - when).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}분 전"
    if minutes < 60 * 24:
        return f"{minutes // 60}시간 전"
    return f"{when.month}/{when.day}"


def news_item_html(a: dict, hidden: bool) -> str:
    """
    뉴스 카드의 기사 한 줄.
    hidden=True 이면 '더보기'를 눌러야 보이는 기사 (class="more").
    """
    cls = ' class="more"' if hidden else ""
    return (
        f'<li{cls}>'
        f'<a href="{esc(a["link"])}" target="_blank" rel="noopener" title="{esc(a["title"])}">'
        f'{esc(a["title"])}</a>'
        f'<span class="meta">{esc(a["source"])} · {time_ago(a["when"])}</span>'
        f'</li>'
    )


def game_item_html(a: dict) -> str:
    """야구 칸의 경기 한 줄: 날짜 + 기사 제목"""
    return (
        f'<li><em>{a["when"].month}/{a["when"].day}</em>'
        f'<a href="{esc(a["link"])}" target="_blank" rel="noopener" title="{esc(a["title"])}">'
        f'{esc(a["title"])}</a></li>'
    )


def render(cards: list[dict], teams: list[dict]) -> str:
    """수집 결과를 template.html 에 끼워 넣어 완성된 HTML 문자열을 만든다."""
    blocks = []

    # --- 뉴스 카드 5개 ---
    for card in cards:
        items = card["items"]
        if items:
            rows = "".join(
                news_item_html(a, hidden=(i >= config.NEWS_COUNT))
                for i, a in enumerate(items)
            )
        else:
            rows = '<li class="empty">최근 24시간 동안 새 기사가 없습니다.</li>'

        # 기본 개수보다 기사가 많을 때만 '더보기' 버튼 표시
        extra = len(items) - config.NEWS_COUNT
        more_btn = f'<button class="more-btn">더보기 {extra}</button>' if extra > 0 else ""

        blocks.append(
            f'<section class="card"><h2>{esc(card["title"])}</h2>'
            f'<ul>{rows}</ul>{more_btn}</section>'
        )

    # --- 6번째 칸: 야구 (위/아래 반씩) ---
    halves = []
    for team in teams:
        rows = "".join(game_item_html(a) for a in team["items"]) \
            or '<li class="empty">최근 경기 기사를 찾지 못했습니다.</li>'
        halves.append(
            f'<div class="half">'
            f'<h2>{esc(team["title"])} <small>최근 {config.BASEBALL_GAMES}경기</small></h2>'
            f'<ul class="games">{rows}</ul></div>'
        )
    blocks.append(f'<section class="card split">{"".join(halves)}</section>')

    # --- 템플릿의 {{...}} 자리를 실제 값으로 바꾸기 ---
    weekday = "월화수목금토일"[NOW.weekday()]
    template = (BASE_DIR / "template.html").read_text(encoding="utf-8")
    return (
        template
        .replace("{{DATE}}", f"{NOW.month}월 {NOW.day}일 ({weekday})")
        .replace("{{UPDATED}}", NOW.strftime("%H:%M"))
        .replace("{{CARDS}}", "\n".join(blocks))
    )


# =====================================================================
# 5. 실행
# =====================================================================
if __name__ == "__main__":
    cards = build_news_cards()
    teams = build_baseball()

    # GitHub Pages 는 docs 폴더의 index.html 을 웹페이지로 보여준다.
    output = BASE_DIR / "docs" / "index.html"
    output.parent.mkdir(exist_ok=True)
    output.write_text(render(cards, teams), encoding="utf-8")

    print(f"[완료] {output} 저장")
