#!/usr/bin/env python3
"""
정적 데이터 생성 스크립트 — PC(국내 IP)에서 실행.
data.go.kr(해외 IP 차단)과 서울시 정적 API를 모아 data/static-lots.json 생성.
배포(Vercel) 환경에서는 이 파일을 읽기만 하므로 월 1회 정도 재실행 후 git push.

실행:  python3 scripts/build_static.py
"""
import json, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import server  # fetch_rows / fetch_datago_rows / norm / hours_text 재사용

OUT = BASE / "data" / "static-lots.json"
RES_API = "https://api.data.go.kr/openapi/tn_pubr_public_residnt_prior_parkng_api"
RES_RADIUS_KM = 0.12  # 주차장 반경 120m 내 거주자우선 구획이 있으면 배지


def pick(it, *subs):
    """필드명 변형에 대비한 키 탐색 (부분일치, 값 있는 것만)"""
    for k, v in it.items():
        kl = k.lower()
        if any(s in kl for s in subs) and v and str(v).strip():
            return str(v).strip()
    return ""


def fetch_resident_zones(max_pages=200, per=1000):
    """전국거주자우선주차정보표준데이터 → 수도권 구획 목록"""
    import urllib.request
    zones, shown_keys = [], False
    for p in range(1, max_pages + 1):
        url = f"{RES_API}?serviceKey={server.DATAGO_KEY}&pageNo={p}&numOfRows={per}&type=json"
        j = None
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    j = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:
                print(f"[warn] 거주자우선 p{p} (시도{attempt}): {e}")
        if j is None:
            break
        header = (j.get("response") or {}).get("header") or {}
        if header.get("resultCode") != "00":
            print(f"[info] 거주자우선 API: {header.get('resultMsg')}")
            break
        items = ((j.get("response") or {}).get("body") or {}).get("items") or []
        if not items:
            break
        if not shown_keys:
            print(f"  (필드: {list(items[0].keys())[:12]}…)")
            shown_keys = True
        for it in items:
            addr = it.get("rdnmadr") or it.get("lnmadr") or ""
            if not addr.startswith(("서울", "경기", "인천")):
                continue
            try:
                lat, lng = float(it.get("latitude") or 0), float(it.get("longitude") or 0)
            except ValueError:
                continue
            if not lat or not lng:
                continue
            name = next((str(v) for k, v in it.items()
                         if k.endswith("Nm") and "instt" not in k.lower() and v), "")
            zones.append({
                "lat": lat, "lng": lng, "name": name,
                "time": pick(it, "time"),
                "fee": pick(it, "fare", "fee", "chrge"),
                "discount": pick(it, "dscnt", "discount"),
            })
        print(f"[info] 거주자우선 p{p} 수신 (누적 수도권 {len(zones)}구획)")
        if len(items) < per:
            break
    return zones


def mark_resident(lots, zones):
    """그리드 해싱으로 반경 내 구획 매칭 (수만 구획 × 수천 주차장 고속 처리)"""
    import math
    grid = {}
    for z in zones:
        grid.setdefault((int(z["lat"] * 100), int(z["lng"] * 100)), []).append(z)

    def dist_km(a, b, c, d):
        R, dla, dlo = 6371, math.radians(c - a), math.radians(d - b)
        x = math.sin(dla / 2) ** 2 + math.cos(math.radians(a)) * math.cos(math.radians(c)) * math.sin(dlo / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))

    marked = 0
    for l in lots:
        if not l["lat"]:
            continue
        gy, gx = int(l["lat"] * 100), int(l["lng"] * 100)
        best = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for z in grid.get((gy + dy, gx + dx), []):
                    d = dist_km(l["lat"], l["lng"], z["lat"], z["lng"])
                    if d <= RES_RADIUS_KM and (best is None or d < best[0]):
                        best = (d, z)
        if best:
            l["resident"] = True
            z = best[1]
            l["residentInfo"] = {k: z[k] for k in ("name", "time", "fee", "discount") if z.get(k)}
            marked += 1
    return marked


def main():
    print("● 서울시 전체 주차장(GetParkInfo) 수집…")
    static_rows = server.fetch_rows("GetParkInfo")
    print(f"  → {len(static_rows)}곳")

    print("● 전국표준데이터(수도권) 수집… (수 분 소요)")
    datago_rows = server.fetch_datago_rows()
    print(f"  → 수도권 {len(datago_rows)}곳")

    seen, lots = set(), []
    for r in static_rows + datago_rows:
        key = server.norm(r.get("PKLT_NM"))
        if key in seen:
            continue
        seen.add(key)
        lat = float(r.get("LAT") or 0)
        lng = float(r.get("LOT") or 0)
        lots.append({
            "id": f"st-{r.get('PKLT_CD')}",
            "name": r.get("PKLT_NM"),
            "type": "노상" if r.get("PKLT_KND") == "NS" else "노외",
            "lat": lat, "lng": lng, "addr": r.get("ADDR"),
            "basic": [int(float(r.get("PRK_HM") or 0)), int(float(r.get("PRK_CRG") or 0))],
            "add": [int(float(r.get("ADD_UNIT_TM_MNT") or 0)), int(float(r.get("ADD_CRG") or 0))],
            "dayMax": int(float(r.get("DLY_MAX_CRG") or 0)),
            "total": int(float(r.get("TPKCT") or 0)),
            "hours": server.hours_text(r.get("WD_OPER_BGNG_TM"), r.get("WD_OPER_END_TM")),
            "tel": r.get("TELNO") or "-",
            "resident": False,
        })

    print("● 거주자우선주차 구획(수도권) 수집…")
    zones = fetch_resident_zones()
    marked = mark_resident(lots, zones) if zones else 0
    print(f"  → 구획 {len(zones)}개, 거주자우선 표시 주차장 {marked}곳")

    OUT.parent.mkdir(exist_ok=True)
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "coordsOverride": server.COORDS_OVERRIDE,
        "lots": lots,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with_coords = sum(1 for l in lots if l["lat"])
    print(f"✅ 완료: {len(lots)}곳 (좌표 보유 {with_coords}곳) → {OUT}")
    print("   git add data/static-lots.json && git commit -m 'data: 정적 주차장 갱신' && git push")


if __name__ == "__main__":
    main()
