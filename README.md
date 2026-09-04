# MonKeeper 수집기

공개된 **공공데이터와 웹페이지를 매일 스냅샷으로 남기는** 수집기입니다.
서버도 데이터베이스도 필요 없습니다. **git 커밋 이력이 곧 시계열 DB**입니다.

## 왜 이렇게 만들었나

애드센스가 요구하는 것은 "AI를 안 쓴 글"이 아니라 **원본 데이터**입니다.

> "The problem is not AI — it is thin content at scale"
> 살아남은 사이트의 공통점: *"demonstrable first-hand experience"*

리콜 공고는 나왔다가 밀려나고, 지원금은 접수가 끝나면 페이지가 내려갑니다.
공공기관도 **과거 기록을 시계열로 보여주지는 않습니다.** 매일 모아 둔 스냅샷은
어디서도 복사할 수 없는 1차 자료가 되고, 커밋 해시와 타임스탬프가 붙어 있어
**독자가 직접 검증할 수 있습니다.**

## 비용

| 항목 | 비용 |
|---|---:|
| GitHub Actions (공개 저장소, 표준 러너) | **0원** |
| 저장소 (텍스트만, 변경 시에만 커밋) | 0원 |
| 공공데이터포털 API | 0원 |

> 공식 문서: *"GitHub Actions usage is free for standard GitHub-hosted runners in public repositories"*

## 구조

```
collect.py                      수집 + 변동 감지 (web / api 두 소스)
sources.yaml                    수집 대상
.github/workflows/collect.yml   매일 06:17 KST 실행

data/<slug>.json   메타데이터 (수집시각·상태·해시)
data/<slug>.txt    정규화 본문 → git diff가 변경분을 그대로 보여줌
events/<날짜>-<slug>.json   변동이 감지된 날에만 생성 ← 글감
state/last-run.json         매일 갱신 (하트비트 + 매일 수집했다는 증거)
```

## 두 가지 소스

| type | 대상 | robots.txt | 인증키 |
|---|---|---|---|
| `web` | 공개 웹페이지 | **반드시 확인·준수** | 불필요 |
| `api` | 활용신청으로 허락받은 공공 API | 대상 아님 | 환경변수에서만 |

## 설치 (10분)

```bash
git init && git add -A
git commit -m "init: MonKeeper 수집기"
git remote add origin https://github.com/<사용자명>/monkeeper-collector.git
git push -u origin main
```

1. Settings → Actions → General → Workflow permissions → **Read and write**
2. (선택) Settings → Secrets and variables → Actions → New repository secret
   - Name: `DATA_GO_KR_KEY` / Secret: 공공데이터포털 인증키
   - **없어도 됩니다.** `api` 대상만 건너뛰고 `web` 수집은 정상 작동합니다.
3. Actions 탭 → collect → **Run workflow**

첫 실행 로그에서 `SKIP` 으로 찍힌 항목의 URL을 고치세요.
`needs_render` 는 JS로 내용을 그리는 페이지라 2단계(Playwright)에서 처리합니다.

## 인증키는 어디에도 저장되지 않습니다

- `sources.yaml` 에는 **환경변수 이름만** 적습니다
- 요청 URL과 파라미터는 저장하되 **키는 제외**합니다
- 응답을 저장하기 전 `redact()` 로 키 문자열을 한 번 더 지웁니다

검증 완료: 키를 넣고 수집한 뒤 `data/` `events/` `state/` 전체를 검사해
키 문자열이 한 글자도 남지 않음을 확인했습니다.

## 크롤링 원칙 (반드시 지킬 것)

1. **robots.txt 준수.** 표준 `urllib.robotparser`는 Google 명세(최장 일치)를
   따르지 않아 `Allow: /` 가 `Disallow:` 를 덮어버립니다. 그래서 `protego`를
   씁니다. **이 부분은 절대 되돌리지 마세요.**
2. **판단 불가 = 수집 안 함.** robots.txt를 못 읽으면 건너뜁니다.
   (404는 "robots 없음 = 전체 허용"으로 규격대로 처리합니다.)
3. **공개 페이지만.** 로그인 뒤 데이터·비공개 API는 대상이 아닙니다.
4. **하루 1회.** 같은 호스트는 최소 2.5초 간격, `Crawl-delay` 선언 시 그 값 우선.
5. **원문 전재 금지.** 정규화 텍스트와 수치만 저장합니다.
6. **출처 표시.** 글에는 항상 기관명·확인일·원문 링크를 답니다.

## 발행은 아직 켜지 마세요

이 저장소는 **수집만** 합니다. 자동 발행은 애드센스 승인 이후입니다.

승인 전에 켜면 22일에 127편을 올려 "가치가 별로 없는 콘텐츠"로 거절당한
상황을 그대로 반복합니다. 지금은 시계열을 쌓는 기간입니다.

승인 후 붙일 계층:

```
events/*.json → Claude 초안 → 게이트 5종 → WordPress 발행
```

**게이트 5종 (전부 프로그램 검사, 사람 개입 0)**

1. 글의 모든 숫자가 `data/*.json` 값과 정확히 일치하는가
2. 출처 URL이 현재도 살아 있는가
3. `events/` 에 이벤트가 없으면 발행하지 않음 (억지 생산 차단)
4. 기존 글과 제목·본문 유사도 임계값 초과 시 중단
5. 한글 깨짐·깨진 링크·HTML 규격 검사

숫자를 데이터가 공급하므로 **환각이 구조적으로 불가능합니다.**

## 알아둘 제약

- 예약 워크플로는 저장소가 60일간 비활성이면 자동 중지됩니다.
  `state/last-run.json` 이 매일 커밋되므로 이 문제는 생기지 않습니다.
- GitHub Actions cron은 정시 실행을 보장하지 않습니다.
- 공공 API는 오류 시 JSON 대신 XML/HTML을 돌려주는 경우가 많습니다.
  그 경우 `JSON 아님` 으로 건너뛰고 기존 스냅샷을 덮지 않습니다.
- 인증키는 발급 직후 최대 1시간 반영이 지연될 수 있습니다.

## 다음 단계

1. `sources.yaml` 의 `enabled: false` 항목에 실제 엔드포인트·URL 채우기
2. 첫 7일 수집 후 **변동 이벤트가 3건 이상** 잡히는지 확인
3. `needs_render` 항목에 Playwright 경로 추가
4. 승인 후 발행 계층 연결
