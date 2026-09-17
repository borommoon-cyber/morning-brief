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
# 1. 뉴스 수집
# =====================================================================
def fetch_feed(url: str, label: str) -> list[dict]:
    """
    RSS 주소에서 기사를 가져온다. (구글 뉴스 검색 결과 / 섹션 피드 공통)

    구글이 보내준 순서를 그대로 유지한다.
    검색·섹션 피드 모두 구글이 중요하다고 판단한 기사를 앞에 놓기 때문에,
    이 순서 자체가 '주요 뉴스' 정보가 된다.

    반환값: [{"title", "link", "source", "when", "order"}, ...]
            order = 구글이 매긴 순위 (0이 가장 위)
    """
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"[실패] {label}: {e}")
        return []

    articles = []
    for order, entry in enumerate(feed.entries):
        published = entry.get("published_parsed")
        if not published:
            continue

        title = html.unescape(entry.get("title", "")).strip()
        source = entry.get("source", {}).get("title", "")

        # 구글 뉴스 제목은 "기사 제목 - 언론사" 형태라 뒤의 언론사 부분을 뗀다.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]

        articles.append({
            "title": title,
            "link": entry.get("link", ""),
            "source": source,
            "when": datetime(*published[:6], tzinfo=timezone.utc).astimezone(KST),
            "order": order,
        })

    print(f"[수집] {len(articles):3d}건  {label}")
    return articles


def search_url(query: str, days: int) -> str:
    """키워드 검색 RSS 주소를 만든다."""
    return ("https://news.google.com/rss/search?q="
            + quote(f"{query} when:{days}d")
            + "&hl=ko&gl=KR&ceid=KR:ko")


def topic_url(browser_url: str) -> str:
    """
    구글 뉴스 섹션 주소를 RSS 주소로 바꾼다.
    브라우저 주소:  https://news.google.com/topics/CAAq....?hl=ko...
    RSS 주소:      https://news.google.com/rss/topics/CAAq....?hl=ko&gl=KR&ceid=KR:ko
    """
    topic_id = browser_url.split("/topics/")[1].split("?")[0].split("/")[0]
    return (f"https://news.google.com/rss/topics/{topic_id}"
            "?hl=ko&gl=KR&ceid=KR:ko")


# ---------------------------------------------------------------------
# 비슷한 기사 묶기 (보도량 = 화제성의 대용 지표)
# ---------------------------------------------------------------------
# 제목 비교에서 무시할 단어 (기사 성격을 나타내는 말들)
STOPWORDS = {"속보", "단독", "종합", "포토", "영상", "사진", "그래픽", "뉴스",
             "기자", "오늘", "내일", "일보", "인터뷰", "칼럼", "사설"}

# 두 기사를 같은 사건으로 볼 기준 (0~1, 높을수록 깐깐하게 묶음)
CLUSTER_THRESHOLD = 0.4


def title_tokens(title: str) -> set:
    """제목에서 두 글자 이상 단어만 뽑는다. (조사·기호는 자연히 걸러짐)"""
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", title)
    return {w for w in words if w not in STOPWORDS}


def cluster_articles(articles: list[dict]) -> list[dict]:
    """
    제목이 비슷한 기사끼리 묶는다.
    같은 사건을 여러 언론사가 보도했다면 그만큼 중요한 뉴스로 본다.

    반환값: [{"items": [기사, ...], "tokens": {단어, ...}}, ...]
    """
    clusters = []

    for a in articles:
        tokens = title_tokens(a["title"])
        if not tokens:
            continue

        # 기존 묶음 중 가장 비슷한 것을 찾는다.
        # 공통 단어 수 ÷ 더 짧은 쪽 단어 수 → 제목 길이가 달라도 비교 가능
        best, best_score = None, 0.0
        for c in clusters:
            common = len(tokens & c["tokens"])
            score = common / min(len(tokens), len(c["tokens"]))
            if score > best_score:
                best, best_score = c, score

        if best and best_score >= CLUSTER_THRESHOLD:
            best["items"].append(a)
        else:
            clusters.append({"items": [a], "tokens": tokens})

    return clusters


def normalize(title: str) -> str:
    """중복 판별용 키. 특수문자·공백을 지우고 앞 30글자만 비교한다."""
    return re.sub(r"[\W_]+", "", title.lower())[:30]


# =====================================================================
# 2. 뉴스 카드 만들기
# =====================================================================
def build_news_cards() -> list[dict]:
    """
    config.NEWS 의 카드마다 기사를 모아 정렬한다.

    정렬 방식(rank)
      "top"     : 구글이 매긴 순서 그대로 (섹션 피드에 적합)
      "cluster" : 여러 언론사가 다룬 기사를 위로 (검색 결과에 적합)
      "recent"  : 최신순

    반환값: [{"title": 카드제목, "items": [기사, ...]}, ...]
    """
    cutoff = NOW - timedelta(hours=config.NEWS_HOURS)
    seen = set()          # 카드 간 중복 방지
    cards = []

    for card in config.NEWS:
        # --- (1) 가져오기: 섹션 주소가 있으면 섹션, 없으면 키워드 검색 ---
        if card.get("topic_url"):
            url, label = topic_url(card["topic_url"]), f'{card["title"]} (섹션)'
        else:
            url, label = search_url(card["query"], days=2), card["query"]
        articles = fetch_feed(url, label)

        # --- (2) 기간·제외 단어로 걸러내기 ---
        kept = [
            a for a in articles
            if a["when"] >= cutoff
            and not any(w in a["title"] for w in card["exclude"])
        ]

        # --- (3) 정렬 ---
        rank = card.get("rank", "cluster")

        if rank == "cluster":
            clusters = cluster_articles(kept)
            # 보도량 많은 순 → 같으면 최신순
            clusters.sort(key=lambda c: (-len(c["items"]),
                                         -max(x["when"].timestamp() for x in c["items"])))
            ordered = []
            for c in clusters:
                # 묶음 안에서는 가장 최신 기사를 대표로 보여준다.
                rep = max(c["items"], key=lambda x: x["when"])
                count = len(c["items"])
                if count > 1:
                    # 몇 곳에서 보도했는지 표시 (화면의 회색 글씨 부분)
                    rep = dict(rep, source=f'{rep["source"]} 외 {count - 1}곳')
                ordered.append(rep)
        elif rank == "recent":
            ordered = sorted(kept, key=lambda a: a["when"], reverse=True)
        else:  # "top" : 구글이 준 순서 유지
            ordered = sorted(kept, key=lambda a: a["order"])

        # --- (4) 중복 제거 후 개수 제한 ---
        picked = []
        for a in ordered:
            key = normalize(a["title"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(a)
            if len(picked) >= config.NEWS_MAX:
                break

        cards.append({"title": card["title"], "items": picked})

    return cards


# =====================================================================
# 3. 야구 경기 결과 만들기
# =====================================================================
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
            fetch_feed(search_url(team["query"], days=config.BASEBALL_DAYS), team["title"]),
            key=lambda a: a["when"],
            reverse=True,
        )

        # 날짜 → 기사 (기사가 최신순이므로 날짜별 첫 기사만 남는다)
        by_date = {}

        for a in articles:
            title = a["title"]

            # (1) 팀 이름이 제목에 없으면 다른 팀 기사일 가능성이 높음
            if team["must"] not in title:
                continue

            # (2) must_any 단어 중 하나도 없으면 제외 (예: "승" 또는 "패")
            if team.get("must_any") and not any(w in title for w in team["must_any"]):
                continue

            # (3) must_digit 패턴(점수)이 없으면 제외
            if team.get("must_digit") and not re.search(team["must_digit"], title):
                continue

            # (4) 경기 결과 기사가 아니면 제외 (인터뷰, 트레이드 소식 등)
            if not RESULT_PATTERN.search(title):
                continue

            date = a["when"].date()
            if date not in by_date:
                by_date[date] = a

        # 최신 날짜부터 N경기만 남긴다.
        recent_dates = sorted(by_date.keys(), reverse=True)[: config.BASEBALL_GAMES]
        teams.append({"title": team["title"],
                      "items": [by_date[d] for d in recent_dates]})

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


def news_item_html(a: dict) -> str:
    """뉴스 카드의 기사 한 줄. (칸을 넘치는 기사는 카드 안에서 스크롤)"""
    return (
        f'<li>'
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
        rows = "".join(news_item_html(a) for a in card["items"]) \
            or '<li class="empty">최근 24시간 동안 새 기사가 없습니다.</li>'
        blocks.append(
            f'<section class="card"><h2>{esc(card["title"])}</h2><ul>{rows}</ul></section>'
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
