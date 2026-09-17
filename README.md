# 아침 브리핑

매일 아침 8시 5분(한국시간)에 뉴스를 모아 웹페이지로 만듭니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `config.py` | 카드 제목, 검색어, 제외 단어, 표시 개수 설정 (평소엔 이것만 수정) |
| `news.py` | 뉴스 수집 → 정리 → HTML 생성 |
| `template.html` | 화면 모양 (색상, 배치) |
| `.github/workflows/daily.yml` | 자동 실행 시간 설정 |
| `docs/index.html` | 결과 페이지 (자동 생성, 직접 수정 X) |

## 처음 세팅

1. GitHub에서 **Public** 저장소 생성 후 이 파일들을 업로드 (`.github` 폴더 포함)
2. Settings → Pages → Branch: `main`, 폴더: `/docs` → Save
3. Actions 탭 → "아침 브리핑" → Run workflow
4. 1~2분 뒤 `https://<아이디>.github.io/<저장소이름>/` 접속 → 즐겨찾기

## 로컬에서 테스트

```bash
pip install -r requirements.txt
python news.py
# docs/index.html 을 브라우저로 열기
```

## 자주 바꾸는 것

- 검색어 조정: `config.py` 의 `query`
- 원치 않는 기사 제외: `config.py` 의 `exclude`
- 실행 시간: `daily.yml` 의 `cron` (UTC 기준, 한국시간 - 9시간)
