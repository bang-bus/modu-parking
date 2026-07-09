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
