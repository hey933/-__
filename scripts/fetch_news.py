#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
제약 개발/연구/품질/GMP 뉴스 클리핑 수집 스크립트

국내 소스 (약업신문, 데일리팜, 메디파나, 팜뉴스):
  - 데일리팜/메디파나/팜뉴스는 자체 RSS가 불안정/비공개라 Google News RSS의
    site: 필터 + 키워드 조합으로 수집한다.
  - 약업신문은 Google 뉴스를 거치면 두 가지 문제가 있었다: (1) 오래된 기사에
    최근 날짜를 잘못 붙이는 경우가 있고 (2) 링크가 news.google.com/rss/articles/...
    형태로 암호화돼 있어 실제 원문 주소를 알아내기가 계속 불안정했다(Google이
    리다이렉트 처리 방식을 자꾸 바꿔서 해석 성공률이 들쭉날쭉했음). 그래서
    약업신문만 Google을 완전히 건너뛰고, 약업신문 자체 검색 페이지
    (www.yakup.com/search/index.html)에서 키워드별로 직접 검색해 실제 링크와
    제목을 가져온다. 이러면 애초에 리다이렉트 자체가 없다.

해외 소스 (FDA, EMA, ICH, PIC/S):
  - FDA, EMA는 공식 RSS 피드를 직접 사용
  - ICH, PIC/S는 공식 RSS가 없어 Google News RSS의 site: 필터로 수집

필터링:
  - NOISE_KEYWORDS(주가/실적/인사 등)는 데일리팜 등 Google 검색 쿼리 단계에서
    -제외어 로, 약업신문 직접 검색 결과에서는 제목에 포함되면 걸러낸다.
  - 국내 기사는 DOMESTIC_MAX_AGE_DAYS(기본 31일=1개월)보다 오래된 것은 제외한다.
    (해외는 절대량이 적어 기간 제한 없음)
  - 약업신문은 실제 링크에서 바로 nid(기사 번호)를 얻을 수 있으므로, 고정된
    기준점(YAKUP_ANCHOR_NID/DATE)에서 오늘까지 며칠 지났는지로 계산한 "예상
    현재 nid"보다 한참 떨어진 기사를 제외한다. 발행일도 같은 방식(nid ->
    날짜 역산)으로 추정한다 — 검색 결과 페이지의 날짜 표기가 들쭉날쭉해도
    안정적으로 동작한다.

결과: data/articles.json 에 저장 (list of dict)
  { title, link, source, region, published, category, matched_keywords }
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------

# Google News로 수집하는 국내 소스: (표시이름, 도메인) — 약업신문은 제외
# (약업신문은 자체 검색 페이지에서 직접 수집한다. 아래 YAKUP_SEARCH_URL 참고)
DOMESTIC_SOURCES = [
    ("데일리팜", "dailypharm.com"),
    ("메디파나", "medipana.com"),
    ("팜뉴스", "pharmnews.com"),
]

# 약업신문 자체 검색 페이지 (모바일판은 robots.txt로 자동화 접근이 막혀 있어
# 데스크톱판을 쓴다 — 데스크톱판은 접근 제한이 없는 것을 확인했다)
YAKUP_SEARCH_URL = "https://www.yakup.com/search/index.html"

# 국내 검색 키워드 - 카테고리별로 정리. 카테고리 단위로 OR 검색을 묶어서
# 요청 수를 줄이고, 어떤 카테고리에 매칭됐는지 결과에 태그로 남긴다.
DOMESTIC_KEYWORD_CATEGORIES = {
    "개발·연구": [
        "신약개발", "제제개발", "개량신약", "제형", "후보물질", "파이프라인",
        "비임상", "임상시험", "임상1상", "임상2상", "임상3상",
        "생동성시험", "생물학적동등성", "기술이전", "라이선스아웃", "L/O",
        "물질특허", "바이오시밀러", "R&D", "위탁연구", "CRO",
    ],
    "품질관리(QC/QA)": [
        "품질관리", "품질보증", "불순물", "니트로사민", "NDMA", "NDMC",
        "유연물질", "함량시험", "시험법", "안정성시험", "규격", "시험성적서",
        "회수", "자진회수", "리콜", "밸리데이션",
    ],
    "생산·GMP": [
        "GMP", "cGMP", "EU-GMP", "GMP실사", "제조소", "제조시설", "무균",
        "원료의약품", "API", "DMF", "CMO", "CDMO", "위탁생산", "제조공정",
        "제조정지", "행정처분",
    ],
    "공통·규제": [
        "식약처", "품목허가", "승인", "허가취소", "실사", "고시",
        "가이드라인", "FDA", "EMA",
    ],
}

# Google Alerts/RSS 스타일 노이즈 제외 키워드 (주가·실적 등 산업 뉴스와 무관한 기사 걸러내기)
NOISE_KEYWORDS = [
    "주가", "목표주가", "실적", "매출", "영업이익", "인사", "임명",
    "인수합병", "투자유치", "후원", "ESG", "채용",
    # 의료기기 신제품 출시, 학술상/시상식 등 R&D·품질·GMP와 무관한 홍보성 기사 제외
    "의료기기 출시", "신제품 출시", "제품 출시", "출시 기념",
    "학술상", "공로상", "시상식", "수상자", "우수논문상",
]

# 검색 쿼리 길이를 적절히 유지하기 위해 카테고리 안에서도 필요시 나눠 검색
KEYWORD_CHUNK_SIZE = 10

# 국내 기사는 양이 많으므로 최근 N일(기본 31일=1개월) 이내 기사만 남긴다.
DOMESTIC_MAX_AGE_DAYS = 31

# 약업신문 nid는 시간순으로 증가한다. "이번 실행에서 수집된 기사 중 최댓값"을
# 기준으로 삼으면, 그날 Google 검색이 하필 진짜 최신 기사를 못 찾아온 경우
# 기준점 자체가 낮아져서 오래된 기사를 걸러내지 못하는 문제가 있었다.
# 그래서 배치와 무관한 고정 기준점(연-nid 쌍)에서 출발해, 오늘 날짜까지의
# 경과일 수만큼 예상 nid를 계산하는 방식으로 바꿨다.
#
# ANCHOR_NID/ANCHOR_DATE: 확인된 최근 정상 기사의 (nid, 날짜) 한 쌍.
#   *** 몇 달에 한 번씩 최근 정상 기사로 이 값을 갱신해주면 정확도가 유지된다 ***
# NID_PER_DAY: 하루 평균 nid 증가폭(실측 기반 추정치, 다소 보수적으로 낮게 잡음)
YAKUP_ANCHOR_NID = 331758
YAKUP_ANCHOR_DATE = datetime(2026, 8, 28, tzinfo=timezone.utc)
YAKUP_NID_PER_DAY = 30
# 추정치 오차를 흡수하기 위한 여유분
YAKUP_NID_SAFETY_BUFFER = 3000

# 해외 공식 RSS 피드 (직접 파싱)
FOREIGN_OFFICIAL_FEEDS = [
    ("FDA", "FDA 보도자료", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-announcements/rss.xml"),
    ("EMA", "EMA 뉴스", "https://www.ema.europa.eu/en/news.xml"),
    ("EMA", "EMA 실사(Inspections)", "https://www.ema.europa.eu/en/inspections.xml"),
    ("EMA", "EMA 규제/절차 가이드라인", "https://www.ema.europa.eu/en/regulatory-and-procedural-guideline.xml"),
]

# 해외 공식 RSS가 없는 소스는 Google News RSS site: 필터로 수집
FOREIGN_GOOGLE_NEWS_SOURCES = [
    ("ICH", "ich.org"),
    ("PIC/S", "picscheme.org"),
]

# 해외 키워드 (공식 피드 필터링 + Google News 검색어 공통 사용)
FOREIGN_KEYWORDS = [
    "GMP", "quality", "manufacturing", "inspection", "warning letter",
    "clinical trial", "approval", "guideline", "R&D", "research",
]

GOOGLE_NEWS_LANG_KR = "hl=ko&gl=KR&ceid=KR:ko"
GOOGLE_NEWS_LANG_EN = "hl=en-US&gl=US&ceid=US:en"

USER_AGENT = "Mozilla/5.0 (compatible; PharmaNewsClipBot/1.0; +https://github.com/)"

OUTPUT_PATH = "data/articles.json"


# ----------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------

def fetch_feed(url: str):
    """URL을 가져와 feedparser로 파싱. 실패 시 빈 feed 반환."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return feedparser.parse(raw)
    except Exception as e:
        print(f"[WARN] fetch 실패: {url} ({e})", file=sys.stderr)
        return feedparser.parse(b"")


def to_iso(entry) -> str:
    """feedparser entry에서 발행일을 ISO 8601 문자열로 변환."""
    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def google_news_rss_url(query: str, lang: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&{lang}"


def fetch_url_text(url: str) -> str:
    """URL의 HTML을 텍스트로 가져온다. 약업신문 직접 검색용."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[WARN] fetch_url_text 실패: {url} ({e})", file=sys.stderr)
        return ""


# 약업신문 검색 결과 안의 기사 링크(nid 포함) + 링크 텍스트(제목)를 뽑는 패턴.
# href 안에 mode=view...nid=숫자 가 있는 <a> 태그만 대상으로 한다 — 메뉴/배너
# 링크 등에는 nid가 없어서 자연히 제외된다.
_YAKUP_RESULT_LINK_RE = re.compile(
    r'<a[^>]+href="(https://www\.yakup\.com/news/index\.html\?[^"]*?nid=(\d+)[^"]*)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


_YAKUP_DEBUG_PRINTED = False


def fetch_yakup_search_results(keyword: str) -> list:
    """약업신문 자체 검색(csearch_type=news)에서 (nid, 실제 URL, 제목) 목록을
    가져온다. Google을 거치지 않으므로 링크 해석 문제 자체가 없다."""
    global _YAKUP_DEBUG_PRINTED
    results = []
    params = urllib.parse.urlencode({"csearch_word": keyword, "csearch_type": "news"})
    url = f"{YAKUP_SEARCH_URL}?{params}"
    html = fetch_url_text(url)

    if not _YAKUP_DEBUG_PRINTED:
        _YAKUP_DEBUG_PRINTED = True
        print(f"[DEBUG] 약업신문 검색 요청: {url}", file=sys.stderr)
        print(f"[DEBUG] 응답 길이: {len(html)}자 / 'nid=' 개수: {html.count('nid=')} / "
              f"'yakup.com/news/index.html' 개수: {html.count('yakup.com/news/index.html')}",
              file=sys.stderr)
        print(f"[DEBUG] 응답 앞 800자:\n{html[:800]}", file=sys.stderr)
        idx = html.find("nid=")
        if idx != -1:
            print(f"[DEBUG] 첫 'nid=' 주변 400자:\n{html[max(0, idx-200):idx+200]}", file=sys.stderr)

    if not html:
        return results
    seen_nid = set()
    for m in _YAKUP_RESULT_LINK_RE.finditer(html):
        link, nid, inner = m.group(1), m.group(2), m.group(3)
        if nid in seen_nid:
            continue
        title = _TAG_RE.sub("", inner)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 4:
            continue
        seen_nid.add(nid)
        results.append({"nid": int(nid), "link": link, "title": title})
    return results


def estimate_yakup_date(nid: int) -> str:
    """nid를 고정 기준점(YAKUP_ANCHOR_NID/DATE) 기반으로 역산해 발행일을
    추정한다. 검색 결과 페이지마다 날짜 표기가 들쭉날쭉해도 안정적으로 동작한다."""
    days_offset = (nid - YAKUP_ANCHOR_NID) / YAKUP_NID_PER_DAY
    dt = YAKUP_ANCHOR_DATE + timedelta(days=days_offset)
    return dt.isoformat()


def matched_keywords(text: str, keywords) -> list:
    text_low = text.lower()
    return [kw for kw in keywords if kw.lower() in text_low]


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build_or_query(keywords, domain: str, noise: list = None) -> str:
    """(kw1 OR kw2 OR ...) site:domain -noise1 -"noise phrase" ... 형태의 쿼리 생성.
    제외어에 공백이 있으면 따옴표로 감싸 구문(phrase) 단위로 제외한다."""
    or_part = "(" + " OR ".join(keywords) + ")"
    query = f"{or_part} site:{domain}"
    if noise:
        excl = []
        for n in noise:
            excl.append(f'-"{n}"' if " " in n else f"-{n}")
        query += " " + " ".join(excl)
    return query


def clean_google_title(title: str, source_name_hint: str = "") -> str:
    """Google News 제목 끝의 ' - 언론사명' 부분을 제거."""
    return re.sub(r"\s*-\s*[^-]+$", "", title).strip()


def normalize_title(title: str) -> str:
    """중복 판정을 위해 제목에서 공백/기호를 제거하고 소문자로 통일."""
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", title.strip().lower())


YAKUP_NID_RE = re.compile(r"[?&]nid=(\d+)")


def filter_yakup_by_nid_recency(articles: list) -> list:
    """약업신문은 Google 뉴스가 오래된 기사에 최근 날짜를 잘못 붙이는 경우가
    있다. 원문을 다시 조회해 대조하고 싶었지만 GitHub Actions 서버 IP가
    약업신문에 막혀 있어(요청이 계속 실패) 그 방식은 쓸 수 없었다.

    이번 실행에서 수집된 기사들 중 최댓값을 기준으로 삼는 방식은, 그날
    Google 검색이 하필 진짜 최신 기사를 못 찾아오면 기준 자체가 낮아져서
    오래된 기사를 놓치는 문제가 있었다. 그래서 배치와 무관하게, 고정된
    기준점(YAKUP_ANCHOR_NID/DATE)에서 오늘까지 경과일 수만큼 nid가
    늘어났으리라고 계산한 "예상 현재 nid"를 기준으로 컷오프를 정한다."""
    days_elapsed = (datetime.now(timezone.utc) - YAKUP_ANCHOR_DATE).days
    expected_current_nid = YAKUP_ANCHOR_NID + YAKUP_NID_PER_DAY * days_elapsed
    cutoff_nid = expected_current_nid - (YAKUP_NID_PER_DAY * DOMESTIC_MAX_AGE_DAYS) - YAKUP_NID_SAFETY_BUFFER

    result = []
    dropped = 0
    for a in articles:
        if a.get("source") == "약업신문":
            m = YAKUP_NID_RE.search(a.get("link", ""))
            if m and int(m.group(1)) < cutoff_nid:
                dropped += 1
                continue
        result.append(a)
    if dropped:
        print(f"  -> 약업신문 오래된 기사(nid 기준) {dropped}건 제외 (기준 nid >= {int(cutoff_nid)})")
    return result


# ----------------------------------------------------------------------
# 수집 로직
# ----------------------------------------------------------------------

def collect_domestic() -> list:
    """Google News RSS로 수집하는 국내 소스(데일리팜/메디파나/팜뉴스).
    약업신문은 collect_yakup_direct()가 별도로 처리한다."""
    articles = []
    for name, domain in DOMESTIC_SOURCES:
        for category, keywords in DOMESTIC_KEYWORD_CATEGORIES.items():
            for kw_chunk in chunked(keywords, KEYWORD_CHUNK_SIZE):
                query = build_or_query(kw_chunk, domain, NOISE_KEYWORDS)
                url = google_news_rss_url(query, GOOGLE_NEWS_LANG_KR)
                feed = fetch_feed(url)
                for entry in feed.entries:
                    title = clean_google_title(entry.get("title", ""))
                    link = entry.get("link", "")
                    if not title or not link:
                        continue
                    mk = matched_keywords(title, kw_chunk) or kw_chunk[:1]
                    articles.append({
                        "title": title,
                        "link": link,
                        "source": name,
                        "region": "국내",
                        "published": to_iso(entry),
                        "category": category,
                        "matched_keywords": mk,
                    })
                time.sleep(0.3)  # Google News 요청 과부하 방지
    return articles


def collect_yakup_direct() -> list:
    """약업신문은 Google을 거치지 않고 자체 검색 페이지에서 직접 수집한다."""
    articles = []
    seen_nid = set()
    for category, keywords in DOMESTIC_KEYWORD_CATEGORIES.items():
        for kw in keywords:
            for r in fetch_yakup_search_results(kw):
                if r["nid"] in seen_nid:
                    continue
                if any(noise.lower() in r["title"].lower() for noise in NOISE_KEYWORDS):
                    continue  # 주가/실적/인사 등 무관한 기사 제외
                seen_nid.add(r["nid"])
                articles.append({
                    "title": r["title"],
                    "link": r["link"],
                    "source": "약업신문",
                    "region": "국내",
                    "published": estimate_yakup_date(r["nid"]),
                    "category": category,
                    "matched_keywords": [kw],
                })
            time.sleep(0.3)  # 약업신문 서버 과부하 방지
    return articles


def collect_foreign_official() -> list:
    articles = []
    for source_label, feed_label, url in FOREIGN_OFFICIAL_FEEDS:
        feed = fetch_feed(url)
        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            if not title or not link:
                continue
            combined = f"{title} {entry.get('summary', '')}"
            mk = matched_keywords(combined, FOREIGN_KEYWORDS)
            # EMA 실사(Inspections)/가이드라인 피드는 그 자체로 GMP/품질 관련이므로
            # 키워드 매칭 없이도 포함한다.
            if not mk and "실사" not in feed_label and "가이드라인" not in feed_label:
                continue
            articles.append({
                "title": title,
                "link": link,
                "source": f"{source_label} ({feed_label})",
                "region": "해외",
                "published": to_iso(entry),
                "category": "공통·규제",
                "matched_keywords": mk or ["(공식 규제 피드)"],
            })
    return articles


def collect_foreign_google_news() -> list:
    articles = []
    for name, domain in FOREIGN_GOOGLE_NEWS_SOURCES:
        for kw in ["GMP", "quality", "guideline", "inspection"]:
            query = f"{kw} site:{domain}"
            url = google_news_rss_url(query, GOOGLE_NEWS_LANG_EN)
            feed = fetch_feed(url)
            for entry in feed.entries:
                title = clean_google_title(entry.get("title", ""))
                link = entry.get("link", "")
                if not title or not link:
                    continue
                articles.append({
                    "title": title,
                    "link": link,
                    "source": name,
                    "region": "해외",
                    "published": to_iso(entry),
                    "category": "공통·규제",
                    "matched_keywords": [kw],
                })
            time.sleep(0.3)
    return articles


def filter_domestic_by_age(articles: list, max_age_days: int) -> list:
    """국내(region == '국내') 기사 중 max_age_days 보다 오래된 기사는 제외한다.
    해외 기사는 절대량이 적으므로 기간 제한을 걸지 않는다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = []
    for a in articles:
        if a["region"] == "국내":
            try:
                pub = datetime.fromisoformat(a["published"])
            except Exception:
                pub = None
            if pub is not None and pub < cutoff:
                continue
        result.append(a)
    return result


def dedupe(articles: list) -> list:
    """같은 기사(같은 제목·같은 출처·같은 발행시각)를 하나로 합친다.

    Google News RSS는 같은 기사라도 검색 쿼리(카테고리)마다 링크가 다르게
    나오는 경우가 많아, 링크만으로는 중복이 걸러지지 않는다. 대신
    (정규화한 제목, 출처, 발행시각-분단위)를 기준으로 판단한다.
    중복인 경우 matched_keywords/category는 합쳐서 정보 손실 없이 보존한다.
    """
    merged = {}
    order = []
    for a in articles:
        minute_key = a["published"][:16]  # 'YYYY-MM-DDTHH:MM'
        key = (normalize_title(a["title"]), a.get("source"), minute_key)
        if key in merged:
            existing = merged[key]
            existing_mk = existing.get("matched_keywords", [])
            for kw in a.get("matched_keywords", []):
                if kw not in existing_mk:
                    existing_mk.append(kw)
            existing["matched_keywords"] = existing_mk
            if a.get("category") and a.get("category") != existing.get("category"):
                cats = {c for c in str(existing.get("category", "")).split(", ") if c}
                cats.add(a["category"])
                existing["category"] = ", ".join(sorted(cats))
            continue
        merged[key] = a
        order.append(key)
    return [merged[k] for k in order]


def main():
    all_articles = []
    print("국내 소스 수집 중 (데일리팜/메디파나/팜뉴스)...")
    all_articles += collect_domestic()
    print("약업신문 직접 수집 중 (자체 검색 페이지)...")
    yakup_articles = collect_yakup_direct()
    print(f"  -> 약업신문 수집: {len(yakup_articles)}건")
    all_articles += yakup_articles
    print("해외 공식 피드 수집 중 (FDA/EMA)...")
    all_articles += collect_foreign_official()
    print("해외 소스 수집 중 (ICH/PIC/S)...")
    all_articles += collect_foreign_google_news()

    print("약업신문 오래된 기사(nid 기준) 필터링 중...")
    all_articles = filter_yakup_by_nid_recency(all_articles)

    all_articles = dedupe(all_articles)
    all_articles = filter_domestic_by_age(all_articles, DOMESTIC_MAX_AGE_DAYS)
    all_articles.sort(key=lambda a: a["published"], reverse=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "count": len(all_articles),
                "articles": all_articles,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"완료: {len(all_articles)}건 저장 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
