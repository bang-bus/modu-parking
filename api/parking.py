"""
Vercel 서버리스 함수: GET /api/parking
 - 서울시 실시간(GetParkingInfo)을 직접 호출 (해외에서도 호출 가능 확인됨)
 - 정적 데이터는 저장소에 커밋된 data/static-lots.json 사용 (data.go.kr은 해외 IP 차단이라 로컬 생성)
 - 환경변수 SEOUL_KEY 필요 (Vercel 프로젝트 설정에서 등록, 미설정 시 sample 5건)
"""
import json, os, re, time, urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

SEOUL_KEY = os.environ.get("SEOUL_KEY", "sample")
CACHE_TTL = 300
_cache = {"ts": 0, "body": None}


def norm(name):
    n = re.sub(r"\(.*?\)", "", name or "").replace(" ", "")
    prev = None
    while prev != n:
        prev = n
        n = re.sub(r"(공영주차장|공영|주차장|주차빌딩)$", "", n)
    return n


def load_static():
    here = Path(__file__).resolve().parent
    for p in (here.parent / "data" / "static-lots.json", Path("data/static-lots.json")):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {"lots": [], "coordsOverride": {}, "generated": None}


def fetch_live():
    rows, start, limit = [], 1, (5 if SEOUL_KEY == "sample" else 1000)
    while start <= 3000:
        url = f"http://openapi.seoul.go.kr:8088/{SEOUL_KEY}/json/GetParkingInfo/{start}/{start + limit - 1}/"
        with urllib.request.urlopen(url, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        body = j.get("GetParkingInfo") or {}
        rows.extend(body.get("row") or [])
        total = body.get("list_total_count", 0)
        if SEOUL_KEY == "sample" or start + limit > total:
            break
        start += limit
    return rows


def hours_text(b, e):
    b, e = (b or "").strip(), (e or "").strip()
    if b == "0000" and e == "2400":
        return "24시간"
    return f"{b[:2]}:{b[2:]}~{e[:2]}:{e[2:]}" if b and e else "-"


def build():
    static = load_static()
    coords = static.get("coordsOverride", {})
    idx = {norm(l["name"]): l for l in static.get("lots", [])}

    lots, matched = [], set()
    try:
        live_rows = fetch_live()
    except Exception:
        live_rows = []

    for r in live_rows:
        key = norm(r.get("PKLT_NM"))
        matched.add(key)
        s = idx.get(key) or {}
        lat = s.get("lat") or coords.get(key, [0, 0])[0]
        lng = s.get("lng") or coords.get(key, [0, 0])[1]
        total = int(float(r.get("TPKCT") or 0))
        occ = int(float(r.get("NOW_PRK_VHCL_CNT") or 0))
        lots.append({
            "id": f"live-{r.get('PKLT_CD')}", "name": r.get("PKLT_NM"),
            "type": "노상" if r.get("PKLT_TYPE") == "NS" else "노외",
            "lat": lat, "lng": lng, "addr": r.get("ADDR"),
            "basic": [int(float(r.get("BSC_PRK_HR") or 0)), int(float(r.get("BSC_PRK_CRG") or 0))],
            "add": [int(float(r.get("ADD_PRK_HR") or 0)), int(float(r.get("ADD_PRK_CRG") or 0))],
            "dayMax": int(float(r.get("DAY_MAX_CRG") or 0)),
            "total": total, "cur": max(0, total - occ), "live": True,
            "resident": bool(s.get("resident")), "residentInfo": s.get("residentInfo"),
            "hours": hours_text(r.get("WD_OPER_BGNG_TM"), r.get("WD_OPER_END_TM")),
            "tel": r.get("TELNO") or "-", "updatedAt": r.get("NOW_PRK_VHCL_UPDT_TM"),
        })

    for key, l in idx.items():
        if key in matched or not l.get("lat"):
            continue
        lots.append({**l, "cur": None, "live": False, "updatedAt": None})

    return {
        "lots": lots,
        "counts": {"live": len(live_rows), "static": len(static.get("lots", [])), "shown": len(lots)},
        "source": "서울 실시간 + 정적스냅샷(" + str(static.get("generated") or "없음") + ")"
                  + (" [SEOUL_KEY 미설정: 5건 제한]" if SEOUL_KEY == "sample" else ""),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "datagoPending": False,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        now = time.time()
        if now - _cache["ts"] > CACHE_TTL or _cache["body"] is None:
            try:
                _cache["body"] = json.dumps(build(), ensure_ascii=False).encode()
                _cache["ts"] = now
            except Exception as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
                return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "s-maxage=300, stale-while-revalidate=600")
        self.end_headers()
        self.wfile.write(_cache["body"])
