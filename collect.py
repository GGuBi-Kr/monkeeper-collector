#!/usr/bin/env python3
"""
SaaS 가격 시계열 수집기 (MonKeeper)

원칙
  1. robots.txt를 반드시 준수한다. 거부되면 수집하지 않고 이유를 기록한다.
  2. 공개 페이지만 본다. 로그인 뒤 데이터, 비공개 API는 절대 건드리지 않는다.
  3. 하루 1회, 같은 호스트에는 간격을 둔다.
  4. 원문을 전재하지 않는다. 정규화 텍스트와 추출 수치만 저장한다.
  5. 변경이 있을 때만 파일이 바뀐다 -> git 커밋 이력이 곧 시계열 DB가 된다.

산출물
  data/<slug>.json  : 메타데이터(수집 시각, 상태, 추출 가격, 해시)
  data/<slug>.txt   : 정규화 본문 (git diff가 변경분을 그대로 보여줌)
  events/<date>-<slug>.json : 변동이 감지된 경우에만 생성
  state/last-run.json : 매일 갱신(하트비트). 저장소 비활성으로 워크플로가
                        자동 중지되는 것을 막고, 매일 수집했다는 증거가 된다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from protego import Protego  # 파이썬 표준 robotparser는 Google 명세(최장 일치)를
                             # 따르지 않아 Allow:/ 가 Disallow 를 덮어버린다.

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
EVENTS = ROOT / "events"
STATE = ROOT / "state"

CONTACT = os.environ.get("TRACKER_CONTACT", "https://monkeeper.com/")
USER_AGENT = f"MonKeeperPriceTracker/1.0 (+{CONTACT})"

TIMEOUT = 25
DELAY_SAME_HOST = 2.5
MIN_TEXT_LEN = 300  # 이보다 짧으면 JS 렌더링이 필요한 페이지로 표시

PRICE_RE = re.compile(
    r"(?:US)?\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"|€\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?"
    r"|£\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"|₩\s?\d{1,3}(?:,\d{3})*"
    r"|\d{1,3}(?:,\d{3})+\s?원"
)

_robots_cache: dict[str, Protego | None] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_robots(origin: str, session: requests.Session) -> tuple[Protego | None, str]:
    try:
        resp = session.get(f"{origin}/robots.txt", timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, f"robots.txt 조회 실패: {exc.__class__.__name__}"

    if resp.status_code == 200:
        return Protego.parse(resp.text), ""
    if resp.status_code in (401, 403):
        # 접근이 거부되면 전체 사이트를 금지로 본다 (Google과 동일한 보수적 해석)
        return None, f"robots.txt 접근 거부(HTTP {resp.status_code})"
    if 400 <= resp.status_code < 500:
        return Protego.parse(""), ""  # 404 등: robots 없음 = 전체 허용
    return None, f"robots.txt 응답 이상(HTTP {resp.status_code})"


def robots_check(url: str, session: requests.Session) -> tuple[bool, str, float | None]:
    """(허용 여부, 사유, crawl-delay). 판단이 안 되면 수집하지 않는다."""
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"

    if origin not in _robots_cache:
        robots, reason = _load_robots(origin, session)
        _robots_cache[origin] = robots
        if robots is None:
            return False, reason, None

    robots = _robots_cache[origin]
    if robots is None:
        return False, "robots.txt 확인 불가", None

    delay = robots.crawl_delay(USER_AGENT)
    if not robots.can_fetch(url, USER_AGENT):
        return False, "robots.txt가 이 경로를 거부함", delay
    return True, "", delay


def normalize_text(html: str) -> tuple[str, str]:
    """(제목, 정규화 본문). 스크립트/스타일/네비게이션 잡음을 걷어낸다."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    title = (soup.title.get_text(strip=True) if soup.title else "") or ""

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    # 연속 중복 줄 제거(반복 네비게이션 등)
    cleaned: list[str] = []
    for ln in lines:
        if not cleaned or cleaned[-1] != ln:
            cleaned.append(ln)
    return title, "\n".join(cleaned)


def extract_jsonld_prices(html: str) -> list[str]:
    """구조화 데이터(JSON-LD)의 Offer 가격을 추가로 긁는다."""
    found: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text() or ""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                price = item.get("price")
                currency = item.get("priceCurrency") or ""
                if price is not None:
                    found.append(f"{currency}{price}".strip())
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def extract_prices(html: str, text: str) -> list[str]:
    raw = PRICE_RE.findall(text) + extract_jsonld_prices(html)
    norm = {re.sub(r"\s+", "", p) for p in raw if p}
    return sorted(norm)


def load_previous(slug: str) -> dict | None:
    path = DATA / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def diff_lines(slug: str, new_text: str, limit: int = 40) -> tuple[list[str], list[str]]:
    """이전 스냅샷과 비교해 새로 생긴 줄과 사라진 줄을 뽑는다.
    새 파일을 쓰기 전에 호출해야 한다."""
    path = DATA / f"{slug}.txt"
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    new = new_text.splitlines()
    old_set, new_set = set(old), set(new)
    added = [ln for ln in new if ln not in old_set][:limit]
    removed = [ln for ln in old if ln not in new_set][:limit]
    return added, removed


def write_event(
    slug: str,
    name: str,
    url: str,
    before: dict,
    after: dict,
    added: list[str] | None = None,
    removed: list[str] | None = None,
) -> Path:
    prev_prices = set(before.get("prices") or [])
    curr_prices = set(after.get("prices") or [])
    event = {
        "slug": slug,
        "name": name,
        "url": url,
        "category": after.get("category"),
        "lang": after.get("lang", ["en"]),
        "detected_at": after["fetched_at"],
        "previous_fetched_at": before.get("fetched_at"),
        "type": "price_change" if prev_prices != curr_prices else "content_change",
        "prices_before": sorted(prev_prices),
        "prices_after": sorted(curr_prices),
        "prices_added": sorted(curr_prices - prev_prices),
        "prices_removed": sorted(prev_prices - curr_prices),
        "text_hash_before": before.get("text_hash"),
        "text_hash_after": after.get("text_hash"),
        # 새로 등장한 줄. 리콜 목록이면 곧 신규 공고다.
        "lines_added": added or [],
        "lines_removed": removed or [],
    }
    day = after["fetched_at"][:10]
    path = EVENTS / f"{day}-{slug}.json"
    path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def redact(text: str, secrets: list[str]) -> str:
    """저장 전 인증키가 섞여 들어가지 않도록 지운다."""
    for sec in secrets:
        if sec:
            text = text.replace(sec, "***REDACTED***")
    return text


def canonical_json(obj) -> str:
    """키 순서를 고정해 같은 응답이면 같은 문자열이 되게 한다."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def collect_api(target: dict, session: requests.Session, record: dict) -> dict:
    """활용신청으로 허락받은 공공 API. robots.txt 대상이 아니다."""
    key_env = target.get("key_env", "DATA_GO_KR_KEY")
    key = os.environ.get(key_env, "").strip()
    if not key:
        record["skipped_reason"] = f"인증키 없음 (환경변수 {key_env})"
        return record

    params = dict(target.get("params") or {})
    params[target.get("key_param", "serviceKey")] = key

    try:
        resp = session.get(target["url"], params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        record["skipped_reason"] = f"요청 실패: {exc.__class__.__name__}"
        return record

    record["http_status"] = resp.status_code
    if resp.status_code != 200:
        record["skipped_reason"] = f"HTTP {resp.status_code}"
        return record

    try:
        payload = resp.json()
    except ValueError:
        # 공공 API는 오류 시 XML/HTML을 돌려주는 경우가 많다
        record["skipped_reason"] = "JSON 아님 (인증키 미승인 또는 파라미터 오류 가능)"
        return record

    text = redact(canonical_json(payload), [key])
    record.update(
        ok=True,
        title=target.get("name"),
        text_len=len(text),
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        prices=[],
        needs_render=False,
    )
    record["_text"] = text
    return record


def collect_one(target: dict, session: requests.Session) -> dict:
    slug = target["slug"]
    name = target.get("name", slug)
    url = target["url"]

    record: dict = {
        "slug": slug,
        "name": name,
        "url": url,
        "type": target.get("type", "web"),
        "category": target.get("category"),
        # 발행 계층이 이 값을 보고 어떤 언어의 글을 쓸지 정한다.
        "lang": target.get("lang", ["en"]),
        "fetched_at": now_iso(),
        "ok": False,
        "http_status": None,
        "skipped_reason": None,
        "needs_render": False,
        "title": None,
        "text_len": 0,
        "text_hash": None,
        "prices": [],
    }

    if target.get("type") == "api":
        return collect_api(target, session, record)

    allowed, reason, delay = robots_check(url, session)
    record["crawl_delay"] = delay
    if not allowed:
        record["skipped_reason"] = reason
        return record

    try:
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        record["skipped_reason"] = f"요청 실패: {exc.__class__.__name__}"
        return record

    record["http_status"] = resp.status_code
    if resp.status_code != 200:
        record["skipped_reason"] = f"HTTP {resp.status_code}"
        return record

    title, text = normalize_text(resp.text)
    prices = extract_prices(resp.text, text)

    record.update(
        ok=True,
        title=title[:300],
        text_len=len(text),
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        prices=prices,
        needs_render=(
            len(text) < MIN_TEXT_LEN
            or (target.get("expect_prices", False) and not prices)
        ),
    )
    record["_text"] = text
    return record


def main() -> int:
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    targets = [t for t in (cfg.get("sources") or []) if t.get("enabled", True)]
    if not targets:
        print("수집 대상이 없습니다. sources.yaml을 확인하세요.", file=sys.stderr)
        return 1

    for d in (DATA, EVENTS, STATE):
        d.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"}
    )

    # 같은 호스트가 연달아 오지 않도록 섞는다
    by_host: dict[str, list[dict]] = defaultdict(list)
    for t in targets:
        by_host[urlparse(t["url"]).netloc].append(t)
    ordered: list[dict] = []
    while any(by_host.values()):
        for host in list(by_host):
            if by_host[host]:
                ordered.append(by_host[host].pop(0))

    summary = {"ok": 0, "skipped": 0, "changed": 0, "needs_render": []}
    last_host_hit: dict[str, float] = {}

    host_delay: dict[str, float] = {}

    for target in ordered:
        host = urlparse(target["url"]).netloc
        gap = max(DELAY_SAME_HOST, host_delay.get(host, 0.0))
        wait = gap - (time.monotonic() - last_host_hit.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)

        record = collect_one(target, session)
        last_host_hit[host] = time.monotonic()
        # 사이트가 Crawl-delay를 선언했으면 그 값을 따른다
        if record.get("crawl_delay"):
            host_delay[host] = float(record["crawl_delay"])

        slug = record["slug"]
        text = record.pop("_text", None)

        if not record["ok"]:
            summary["skipped"] += 1
            print(f"  SKIP {slug}: {record['skipped_reason']}")
            # 실패는 기존 스냅샷을 덮지 않는다. 로그만 남긴다.
            continue

        summary["ok"] += 1
        if record["needs_render"]:
            summary["needs_render"].append(slug)

        previous = load_previous(slug)
        changed = previous is not None and previous.get("text_hash") != record["text_hash"]

        if changed:
            summary["changed"] += 1
            added, removed = diff_lines(slug, text or "")
            event_path = write_event(
                slug, record["name"], record["url"], previous, record, added, removed
            )
            price_note = ""
            if set(previous.get("prices") or []) != set(record["prices"]):
                price_note = "  [가격 변동]"
            print(f"  CHANGE {slug}{price_note} -> {event_path.name}")

        (DATA / f"{slug}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if text is not None:
            (DATA / f"{slug}.txt").write_text(text + "\n", encoding="utf-8")

    (STATE / "last-run.json").write_text(
        json.dumps(
            {
                "finished_at": now_iso(),
                "targets": len(ordered),
                "ok": summary["ok"],
                "skipped": summary["skipped"],
                "changed": summary["changed"],
                "needs_render": sorted(summary["needs_render"]),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"\n대상 {len(ordered)} / 성공 {summary['ok']} / 건너뜀 {summary['skipped']} "
        f"/ 변동 {summary['changed']}"
    )
    if summary["needs_render"]:
        print("JS 렌더링 필요 추정: " + ", ".join(sorted(summary["needs_render"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
