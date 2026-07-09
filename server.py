#!/usr/bin/env python3
"""
모두의공영주차장 — 로컬 서버 (표준 라이브러리만 사용)
실행:  python3 server.py   →  http://localhost:8765

역할:
  1) index.html 정적 서빙
  2) /api/parking — 서울 열린데이터광장 API 프록시 (CORS 우회 + 5분 캐시)
     · GetParkingInfo : 실시간 주차대수 (시영주차장 ~123곳)
     · GetParkInfo    : 전체 공영주차장 정적 정보 (~2,204곳, 좌표 포함)
     두 데이터를 이름 정규화로 매칭해 통합 JSON 반환.

config.json 의 seoulKey 가 "sample" 이면 API별 5건만 반환됨(서울시 정책).
실제 키 발급(무료·즉시): https://data.seoul.go.kr → 로그인 → 인증키 신청
"""
import json, re, time, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
KEY = CONFIG.get("seoulKey", "sample") or "sample"
DATAGO_KEY = CONFIG.get("dataGoKrKey", "")
PORT = CONFIG.get("port", 8765)
CACHE_TTL = 300  # 5분

# 좌표 보정 테이블: GetParkingInfo(실시간)에는 좌표가 없어 정적 API와 매칭 실패 시 사용
COORDS_OVERRIDE = json.loads((BASE / "coords_override.json").read_text(encoding="utf-8"))

_cache = {"ts": 0, "payload": None}
# 전국표준데이터는 느려서(페이지당 수~수십 초) 백그라운드 스레드로 수집
_datago = {"rows": [], "done": False, "loading": False, "ts": 0}
DATAGO_TTL = 24 * 3600  # 하루 1회 갱신이면 충분 (월 단위 데이터)


def norm(name: str) -> str:
    """주차장명 정규화: 공백/괄호표기/접미어 반복 제거 (예: '종묘주차장 공영주차장(시)' → '종묘')"""
    n = re.sub(r"\(.*?\)", "", name or "").replace(" ", "")
    prev = None
    while prev != n:
        prev = n
        n = re.sub(r"(공영주차장|공영|주차장|주차빌딩)$", "", n)
    return n


def fetch_rows(service: str, per=1000, hard_limit=5000):
    """서울 열린데이터광장 페이지네이션 수집 (sample 키는 5건 제한)"""
    rows, start = [], 1
    limit = 5 if KEY == "sample" else per
    while start <= hard_limit:
        url = f"http://openapi.seoul.go.kr:8088/{KEY}/json/{service}/{start}/{start + limit - 1}/"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[warn] {service} {start}~: {e}")
            break
        body = j.get(service) or {}
        batch = body.get("row") or []
        rows.extend(batch)
        total = body.get("list_total_count", 0)
        if KEY == "sample" or start + limit > total:
            break
        start += limit
    return rows


def fetch_datago_rows(max_pages=40, per=500):
    """공공데이터포털 전국주차장정보표준데이터 (수도권: 서울·경기·인천). API가 느려서 백그라운드에서만 호출."""
    if not DATAGO_KEY:
        return []
    rows = []
    for p in range(1, max_pages + 1):
        url = (f"https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"
               f"?serviceKey={DATAGO_KEY}&pageNo={p}&numOfRows={per}&type=json")
        j = None
        for attempt in (1, 2):  # 타임아웃 잦음 → 1회 재시도
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    j = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:
                print(f"[warn] data.go.kr p{p} (시도{attempt}): {e}")
        if j is None:
            break
        header = (j.get("response") or {}).get("header") or {}
        if header.get("resultCode") not in ("00", "NORMAL SERVICE."):
            print(f"[info] data.go.kr 미연동: {header.get('resultMsg')} (활용신청 승인 후 자동 연동됩니다)")
            break
        batch = ((j.get("response") or {}).get("body") or {}).get("items") or []
        if not batch:
            break
        for it in batch:
            addr = it.get("rdnmadr") or it.get("lnmadr") or ""
            # 수도권 필터: 서울 + 경기 + 인천
            if not addr.startswith(("서울", "경기", "인천")):
                continue
            # GetParkInfo와 같은 구조로 변환해 정적 인덱스에 합류
            rows.append({
                "PKLT_CD": f"dg-{it.get('prkplceNo') or len(rows)}",
                "PKLT_NM": it.get("prkplceNm"),
                "ADDR": addr,
                "PKLT_KND": "NS" if "노상" in (it.get("prkplceSe") or "") else "NW",
                "TPKCT": it.get("prkcmprt") or 0,
                "PRK_HM": it.get("basicTime") or 0, "PRK_CRG": it.get("basicCharge") or 0,
                "ADD_UNIT_TM_MNT": it.get("addUnitTime") or 0, "ADD_CRG": it.get("addUnitCharge") or 0,
                "DLY_MAX_CRG": 0,
                "LAT": it.get("latitude") or 0, "LOT": it.get("longitude") or 0,
                "WD_OPER_BGNG_TM": (it.get("weekdayOperOpenHhmm") or "").replace(":", ""),
                "WD_OPER_END_TM": (it.get("weekdayOperColseHhmm") or "").replace(":", ""),
                "TELNO": it.get("phoneNumber") or "-",
            })
        print(f"[info] 전국표준데이터 p{p} 수신 ({len(batch)}건, 누적 수도권 {len(rows)}곳)")
        if len(batch) < per:
            break
    return rows


def datago_worker():
    """백그라운드에서 전국표준데이터 수집 → 완료 시 통합 캐시 무효화"""
    _datago["loading"] = True
    try:
        rows = fetch_datago_rows()
        if rows:
            _datago["rows"] = rows
            print(f"[info] 전국표준데이터 수집 완료: 수도권 {len(rows)}곳")
    finally:
        _datago["done"] = True
        _datago["loading"] = False
        _datago["ts"] = time.time()
        _cache["ts"] = 0  # 다음 요청에서 통합 재빌드


def ensure_datago():
    if _datago["loading"]:
        return
    if _datago["done"] and time.time() - _datago["ts"] < DATAGO_TTL:
        return
    _datago["done"] = False
    threading.Thread(target=datago_worker, daemon=True).start()


def hours_text(b, e):
    b, e = (b or "").strip(), (e or "").strip()
    if b == "0000" and e == "2400":
        return "24시간"
    if b and e:
        return f"{b[:2]}:{b[2:]}~{e[:2]}:{e[2:]}"
    return "-"


def build_payload():
    live_rows = fetch_rows("GetParkingInfo")   # 실시간 (시영, 서울시 키) — 빠름
    static_rows = fetch_rows("GetParkInfo")    # 전체 정적 (서울시 키, 좌표 보유) — 빠름
    datago_rows = list(_datago["rows"])        # 전국표준데이터 서울분 — 백그라운드 수집분 사용

    # 정적 인덱스: 정규화명 → row (서울시 정적 우선, 표준데이터로 보강)
    static_idx = {}
    for r in static_rows + datago_rows:
        k = norm(r.get("PKLT_NM"))
        if k not in static_idx or not float(static_idx[k].get("LAT") or 0):
            static_idx[k] = r

    lots, matched_keys = [], set()

    # 1) 실시간 주차장 (핵심)
    for r in live_rows:
        key = norm(r.get("PKLT_NM"))
        matched_keys.add(key)
        s = static_idx.get(key)
        lat = float((s or {}).get("LAT") or 0) or COORDS_OVERRIDE.get(key, [0, 0])[0]
        lng = float((s or {}).get("LOT") or 0) or COORDS_OVERRIDE.get(key, [0, 0])[1]
        total = int(float(r.get("TPKCT") or 0))
        occupied = int(float(r.get("NOW_PRK_VHCL_CNT") or 0))
        lots.append({
            "id": f"live-{r.get('PKLT_CD')}",
            "name": r.get("PKLT_NM"),
            "type": "노상" if r.get("PKLT_TYPE") == "NS" else "노외",
            "lat": lat, "lng": lng, "addr": r.get("ADDR"),
            "basic": [int(float(r.get("BSC_PRK_HR") or 0)), int(float(r.get("BSC_PRK_CRG") or 0))],
            "add": [int(float(r.get("ADD_PRK_HR") or 0)), int(float(r.get("ADD_PRK_CRG") or 0))],
            "dayMax": int(float(r.get("DAY_MAX_CRG") or 0)),
            "total": total, "cur": max(0, total - occupied), "live": True,
            "resident": False,
            "hours": hours_text(r.get("WD_OPER_BGNG_TM"), r.get("WD_OPER_END_TM")),
            "tel": r.get("TELNO") or "-",
            "updatedAt": r.get("NOW_PRK_VHCL_UPDT_TM"),
        })

    # 2) 실시간 미제공 주차장 (정적 전용, 좌표 있는 것만)
    for key, r in static_idx.items():
        if key in matched_keys:
            continue
        lat, lng = float(r.get("LAT") or 0), float(r.get("LOT") or 0)
        if not lat or not lng:
            continue  # 좌표 없는 정적 주차장은 제외 (지도 표시 불가)
        lots.append({
            "id": f"st-{r.get('PKLT_CD')}",
            "name": r.get("PKLT_NM"),
            "type": "노상" if r.get("PKLT_KND") == "NS" else "노외",
            "lat": lat, "lng": lng, "addr": r.get("ADDR"),
            "basic": [int(float(r.get("PRK_HM") or 0)), int(float(r.get("PRK_CRG") or 0))],
            "add": [int(float(r.get("ADD_UNIT_TM_MNT") or 0)), int(float(r.get("ADD_CRG") or 0))],
            "dayMax": int(float(r.get("DLY_MAX_CRG") or 0)),
            "total": int(float(r.get("TPKCT") or 0)), "cur": None, "live": False,
            "resident": False,
            "hours": hours_text(r.get("WD_OPER_BGNG_TM"), r.get("WD_OPER_END_TM")),
            "tel": r.get("TELNO") or "-",
            "updatedAt": None,
        })

    # 거주자우선 표시: build_static.py가 생성한 스냅샷의 매칭 결과 재사용 (로컬 개발용)
    try:
        sj = json.loads((BASE / "data" / "static-lots.json").read_text(encoding="utf-8"))
        rmap = {norm(l["name"]): l.get("residentInfo") for l in sj.get("lots", []) if l.get("resident")}
        for l in lots:
            info = rmap.get(norm(l["name"]))
            if info is not None or norm(l["name"]) in rmap:
                l["resident"], l["residentInfo"] = True, info
    except Exception:
        pass

    src = "서울 열린데이터광장" + (" [sample 키: 5건 제한 — README 참고]" if KEY == "sample" else "")
    if datago_rows:
        src += f" + 전국주차장표준데이터({len(datago_rows)}곳)"
    print(f"[info] 통합 완료: 실시간 {len(live_rows)} · 서울정적 {len(static_rows)} · 전국표준 {len(datago_rows)} → 표시 {len(lots)}곳")
    return {
        "lots": lots,
        "counts": {"live": len(live_rows), "static": len(static_rows) + len(datago_rows), "shown": len(lots)},
        "source": src,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "datagoPending": not _datago["done"],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE), **kw)

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        if self.path.startswith("/api/parking"):
            ensure_datago()
            now = time.time()
            if now - _cache["ts"] > CACHE_TTL or _cache["payload"] is None:
                try:
                    _cache["payload"] = build_payload()
                    _cache["ts"] = now
                except Exception as e:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
                    return
            body = json.dumps(_cache["payload"], ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}]", fmt % args)


if __name__ == "__main__":
    print(f"● 모두의공영주차장 로컬 서버: http://localhost:{PORT}")
    print(f"● 서울시 인증키: {'sample (5건 제한)' if KEY == 'sample' else KEY[:6] + '…'}")
    ensure_datago()  # 전국표준데이터는 시작하자마자 백그라운드 수집
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
