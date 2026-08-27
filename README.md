# 제약 R&D · 품질 · GMP 뉴스 클리핑

개발/연구/품질/GMP 관련 국내외 제약 뉴스를 매일 자동으로 모아 보여주는 정적 사이트입니다.
[참고 사이트](https://hey933.github.io/yakup-korean-united-pharma-news/)와 동일한 구조(GitHub Actions로 매일 수집 → GitHub Pages로 배포)로 만들었습니다.

## 수집 대상

- **국내**: 약업신문(yakup.com), 데일리팜(dailypharm.com), 메디파나(medipana.com), 팜뉴스(pharmnews.com)
  - 각 사이트 자체 RSS가 불안정하거나 비공개라, Google 뉴스 RSS의 `site:` 검색 기능을 이용해
    "GMP", "품질", "임상", "연구개발" 등 키워드로 수집합니다. (사이트 개편에 영향을 덜 받는 방식)
- **해외**: FDA(보도자료 RSS), EMA(뉴스/실사/가이드라인 공식 RSS), ICH·PIC/S(Google 뉴스 RSS)

## 배포 방법 (5분)

1. 이 폴더 전체를 본인 GitHub 계정에 새 저장소로 올립니다.
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<내계정>/<저장소명>.git
   git push -u origin main
   ```
2. 저장소 **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 설정합니다.
3. **Actions** 탭에서 `Update pharma news clipping` 워크플로우를 한 번 수동 실행(`Run workflow`)하면
   - `scripts/fetch_news.py`가 뉴스를 수집해 `data/articles.json`을 갱신하고
   - 결과를 GitHub Pages로 배포합니다.
4. 이후 매일 자정(UTC, 한국시간 오전 9시)에 자동으로 재수집됩니다.

## 로컬에서 테스트하기

```bash
pip install -r requirements.txt
python scripts/fetch_news.py
# data/articles.json 이 갱신됩니다
python -m http.server 8000
# http://localhost:8000 에서 확인
```

## 커스터마이징

- **키워드 추가/수정**: `scripts/fetch_news.py`의 `DOMESTIC_KEYWORDS`, `FOREIGN_KEYWORDS` 리스트 수정
- **소스 추가**: `DOMESTIC_SOURCES`(국내), `FOREIGN_OFFICIAL_FEEDS`(공식 RSS 보유 기관),
  `FOREIGN_GOOGLE_NEWS_SOURCES`(공식 RSS 없는 기관)에 항목 추가
- **수집 주기 변경**: `.github/workflows/update-news.yml`의 `cron` 값 수정
  (예: 하루 2회 → `"0 0,12 * * *"`)
- **디자인 변경**: `index.html` 상단 `<style>`의 CSS 변수(`:root`) 값만 바꿔도 색상 전체가 바뀝니다.

## 알아두면 좋은 점

- Google 뉴스 RSS는 비공식적으로 널리 쓰이는 방식이라 완전히 보장되지는 않습니다.
  특정 사이트의 최신 기사가 빠진다면 키워드를 늘리거나, 해당 사이트의 실제 RSS 주소를
  찾아 `FOREIGN_OFFICIAL_FEEDS`와 같은 방식으로 직접 추가하는 것이 가장 안정적입니다.
- 기사 본문은 저작권 보호 대상이므로, 이 사이트는 제목 · 링크 · 출처만 클리핑하고
  본문은 원문 링크로 연결합니다.
- 첫 배포 직후에는 `data/articles.json`이 비어 있습니다. Actions가 한 번 실행되고 나면
  채워집니다.
