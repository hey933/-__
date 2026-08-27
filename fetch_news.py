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

결과: data/articles.json 에 저장 (list of dict)
  { title, link, source, region, published, matched_keywords }
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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

# 국내 검색 키워드 (개발/연구/품질/GMP 관련) - 이 중 하나라도 매칭되면 수집
DOMESTIC_KEYWORDS = [
    "GMP", "cGMP", "품질관리", "품질보증", "품질", "밸리데이션",
    "연구개발", "R&D", "신약개발", "임상시험", "임상", "허가",
    "식약처", "실사", "품목허가", "제조소",
]

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


def matched_keywords(text: str, keywords) -> list:
    text_low = text.lower()
    return [kw for kw in keywords if kw.lower() in text_low]


def clean_google_title(title: str, source_name_hint: str = "") -> str:
    """Google News 제목 끝의 ' - 언론사명' 부분을 제거."""
    return re.sub(r"\s*-\s*[^-]+$", "", title).strip()


# ----------------------------------------------------------------------
# 수집 로직
# ----------------------------------------------------------------------

def collect_domestic() -> list:
    articles = []
    for name, domain in DOMESTIC_SOURCES:
        for kw in DOMESTIC_KEYWORDS:
            query = f"{kw} site:{domain}"
            url = google_news_rss_url(query, GOOGLE_NEWS_LANG_KR)
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
                    "region": "국내",
                    "published": to_iso(entry),
                    "matched_keywords": [kw],
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
                    "matched_keywords": [kw],
                })
            time.sleep(0.3)
    return articles


def dedupe(articles: list) -> list:
    seen = set()
    result = []
    for a in articles:
        key = a["link"].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result


def main():
    all_articles = []
    print("국내 소스 수집 중 (약업신문/데일리팜/메디파나/팜뉴스)...")
    all_articles += collect_domestic()
    print("해외 공식 피드 수집 중 (FDA/EMA)...")
    all_articles += collect_foreign_official()
    print("해외 소스 수집 중 (ICH/PIC/S)...")
    all_articles += collect_foreign_google_news()

    all_articles = dedupe(all_articles)
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
