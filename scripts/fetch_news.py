#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
제약 개발/연구/품질/GMP 뉴스 클리핑 수집 스크립트

국내 소스 (약업신문, 데일리팜, 메디파나, 팜뉴스):
  - 각 사이트 자체 RSS가 불안정/비공개인 경우가 많아 Google News RSS의
    site: 필터 + 키워드 조합으로 수집한다. (사이트 구조 변경에 안전)

해외 소스 (FDA, EMA, ICH, PIC/S):
  - FDA, EMA는 공식 RSS 피드를 직접 사용
  - ICH, PIC/S는 공식 RSS가 없어 Google News RSS의 site: 필터로 수집

필터링:
  - NOISE_KEYWORDS(주가/실적/인사 등)는 검색 쿼리 단계에서 -제외어 로 걸러낸다.
  - 국내 기사는 DOMESTIC_MAX_AGE_DAYS(기본 31일=1개월)보다 오래된 것은 제외한다.
    (해외는 절대량이 적어 기간 제한 없음)
  - 약업신문 기사는 Google 뉴스가 주는 날짜가 실제 게재일과 다를 때가 있어(오래된
    기사를 최근 날짜로 잘못 표시), news_print.html 원문 페이지의 "기사입력" 날짜를
    다시 조회해 정확한 날짜로 덮어쓴다. (다른 3개 사이트는 아직 이런 문제가 보고되지
    않아 우선 약업신문에만 적용)

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
from functools import lru_cache

import feedparser

# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------

# 국내 소스: (표시이름, 도메인)
DOMESTIC_SOURCES = [
    ("약업신문", "yakup.com"),
    ("데일리팜", "dailypharm.com"),
    ("메디파나", "medipana.com"),
    ("팜뉴스", "pharmnews.com"),
]

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

# 한국 표준시(약업신문 원문의 "기사입력" 시각은 KST 기준으로 표기됨)
KST = timezone(timedelta(hours=9))

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


def fetch_url_text(url: str) -> str:
    """URL의 HTML을 텍스트로 가져온다. (날짜 검증 등 가벼운 원문 대조용)"""
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
YAKUP_DATE_RE = re.compile(r"기사입력\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")


@lru_cache(maxsize=None)
def fetch_yakup_real_date(nid: str):
    """nid로 news_print.html을 조회해 실제 게재일(UTC ISO)을 반환. 실패 시 None.
    같은 기사가 여러 키워드 카테고리에서 중복으로 걸릴 수 있어 nid 기준으로 캐시한다."""
    html = fetch_url_text(f"https://www.yakup.com/news/news_print.html?nid={nid}")
    time.sleep(0.2)  # 원문 서버 과부하 방지 (캐시 미스일 때만 실행됨)
    if not html:
        return None
    m_date = YAKUP_DATE_RE.search(html)
    if not m_date:
        return None
    try:
        dt_kst = datetime.strptime(f"{m_date.group(1)} {m_date.group(2)}", "%Y-%m-%d %H:%M")
        dt_kst = dt_kst.replace(tzinfo=KST)
        return dt_kst.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def verify_yakup_date(article: dict) -> None:
    """약업신문 기사만, 원문 인쇄용 페이지(news_print.html)의 '기사입력' 날짜로
    published 값을 다시 맞춘다. Google 뉴스가 오래된 기사를 최근 날짜로 잘못
    표시하는 경우가 있어서(예: 2019년 기사가 오늘 날짜로 표시), 원문을 직접
    대조해 바로잡는다. 원문을 못 가져오거나 날짜를 못 찾으면 기존 값을 유지한다."""
    if article.get("source") != "약업신문":
        return
    m_nid = YAKUP_NID_RE.search(article["link"])
    if not m_nid:
        return
    real_date = fetch_yakup_real_date(m_nid.group(1))
    if real_date:
        article["published"] = real_date


# ----------------------------------------------------------------------
# 수집 로직
# ----------------------------------------------------------------------

def collect_domestic() -> list:
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
    print("국내 소스 수집 중 (약업신문/데일리팜/메디파나/팜뉴스)...")
    all_articles += collect_domestic()
    print("해외 공식 피드 수집 중 (FDA/EMA)...")
    all_articles += collect_foreign_official()
    print("해외 소스 수집 중 (ICH/PIC/S)...")
    all_articles += collect_foreign_google_news()

    print("약업신문 기사 날짜 원문 대조 중...")
    for a in all_articles:
        if a["region"] == "국내" and a.get("source") == "약업신문":
            verify_yakup_date(a)

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
