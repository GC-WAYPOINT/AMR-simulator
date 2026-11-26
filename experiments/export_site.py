#!/usr/bin/env python3
"""편집용 FMS 에서 찍은 현장(점유 존·gate·목적지·충전기)을 스펙 JSON 으로 저장하고 규칙을 검사한다.

    .venv/bin/python experiments/export_site.py cleanroom            # 리포 루트에서, edit_fms.py 가 떠 있을 때
    .venv/bin/python experiments/export_site.py cleanroom --check    # 저장하지 않고 검사만

저장: experiments/sites/factory_<현장>.json (provision.py 가 운행마다 그대로 다시 등록한다).
검사 (오류가 있으면 종료 코드 1, 저장은 한다 — 고친 뒤 다시 실행):
  오류  gate 대기·출구 node 가 로봇 폭 팽창 후 주행 불가 / 대기·출구 node 가 점유 존 안(차체 반경 0.53 m 포함)
        / 작업 지점·주차가 점유 존 안 (FMS 가 목적지로 거절) / 작업 지점·주차가 주행 불가
        / 대기 node 와 다른 gate 의 출구 node 가 1.1 m 미만 (양방향 교착, 설계서 부록 A.4)
  경고  대기·출구 1.1 ~ 2.0 m / 대기 node 끼리 1.1 m 미만 / 점유 존 10 개 초과 (FMS 경로 계산이 느려진다)
        / gate 가 하나뿐인 점유 존 (일방통행) / 작업 지점 2 개 미만
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision import Fms, ProvisionError  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ROBOT_WIDTH, ROBOT_LENGTH = 0.5, 0.7
RADIUS = math.hypot(ROBOT_WIDTH, ROBOT_LENGTH) / 2 + 0.1     # FMS traffic.radius (0.53 m)
MIN_SEP, GOOD_SEP = 2 * RADIUS, 2.0


def rect_of(polygon):
    """FMS 사각형 구역 다각형 -> (x, y, width, height, rotation°) (rectangle_polygon 의 역)."""
    p = [(q["x"], q["y"]) for q in polygon]
    if len(p) != 4:
        raise ValueError("사각형이 아닌 구역")
    w = math.dist(p[0], p[1])
    h = math.dist(p[0], p[3])
    rot = math.degrees(math.atan2(p[1][1] - p[0][1], p[1][0] - p[0][0]))
    cx = sum(q[0] for q in p) / 4
    cy = sum(q[1] for q in p) / 4
    rot = round(rot, 6) % 360
    return round(cx - w / 2, 4), round(cy - h / 2, 4), round(w, 4), round(h, 4), 0.0 if abs(rot) < 1e-6 or abs(rot - 360) < 1e-6 else rot


def inside(polygon, x, y, margin):
    """traffic.intersects 와 같은 판정: 회전 사각형 + margin 안인가."""
    p = [(q["x"], q["y"]) for q in polygon]
    cx, cy = sum(q[0] for q in p) / 4, sum(q[1] for q in p) / 4
    ux, uy = p[1][0] - p[0][0], p[1][1] - p[0][1]
    hw = math.hypot(ux, uy) / 2
    ux, uy = ux / (2 * hw), uy / (2 * hw)
    hh = math.dist(p[0], p[3]) / 2
    dx, dy = x - cx, y - cy
    return abs(ux * dx + uy * dy) <= hw + margin and abs(-uy * dx + ux * dy) <= hh + margin


class Drivable:
    """맵 이미지 FREE 를 로봇 반폭만큼 팽창 (robot planner 규칙)."""

    def __init__(self, map_dir: Path):
        meta = json.loads((map_dir / "fms_map.json").read_text())
        a = np.asarray(Image.open(map_dir / meta["image"]).convert("L"))
        free = a >= meta["free_thresh"]
        self.res, self.ox, self.oy = meta["resolution"], meta["origin_x"], meta["origin_y"]
        r = math.ceil(ROBOT_WIDTH / 2 / self.res)
        ok = free.copy()
        h, w = free.shape
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r * r:
                    continue
                y0, y1, x0, x1 = max(0, -dy), min(h, h - dy), max(0, -dx), min(w, w - dx)
                ok[y0:y1, x0:x1] &= free[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
        self.ok = ok

    def __call__(self, x, y):
        c, r = int((x - self.ox) / self.res), int((y - self.oy) / self.res)
        return 0 <= r < self.ok.shape[0] and 0 <= c < self.ok.shape[1] and bool(self.ok[r, c])


def export(fms_url: str, site: str) -> tuple[dict, list[str], list[str]]:
    fms = Fms(fms_url)
    spec_path = REPO / "experiments" / "sites" / f"factory_{site}.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    map_id = spec["map"]["map_id"]
    files = f"/sites/{site}/maps/{map_id}/files"
    ann = fms.get(files + "/annotations.json")
    try:
        chargers = fms.get(files + "/chargers.json")
        chargers = chargers.get("chargers", chargers) if isinstance(chargers, dict) else chargers
    except ProvisionError:
        chargers = []
    locs = fms.get(f"/sites/{site}/locations")
    errors, warnings = [], []

    zones = []
    for z in ann.get("zones", []):
        if z.get("door"):
            warnings.append(f"자동문 구역 {z.get('name') or z['zone_id']} 는 내보내지 않는다 (실험 범위 밖)")
            continue
        if z["type"] not in ("occupancy", "slow", "prohibit"):
            continue
        try:
            x, y, w, h, rot = rect_of(z["polygon"])
        except ValueError:
            warnings.append(f"구역 {z.get('name')} 이 사각형이 아니라 건너뛴다")
            continue
        out = {"name": z.get("name") or z["zone_id"], "type": z["type"], "x": x, "y": y, "width": w, "height": h}
        if rot:
            out["rotation"] = rot
        if z["type"] == "occupancy":
            out["gates"] = z.get("gates", [])
        if z["type"] == "slow":
            out["speed_limit"] = z["speed_limit"]
        zones.append((out, z["polygon"]))
    spec["zones"] = [o for o, _ in zones]
    spec["chargers"] = [{"name": c["id"], "x": c["x"], "y": c["y"], "theta": c["theta"], "entry": c.get("entry") or "FORWARD"}
                        for c in chargers]
    charger_names = {c["name"] for c in spec["chargers"]}
    spec["locations"] = [{"name": l["name"], "x": l["x"], "y": l["y"], "theta": l["theta"], "type": l["type"]}
                         for l in locs if l.get("map_id") == map_id and l["name"] not in charger_names and l["type"] != "charger"]
    parking = [l for l in spec["locations"] if l["type"] == "parking"]
    if parking:
        spec["robot_starts"] = {"parking": [[l["x"], l["y"]] for l in parking]}

    # -- 검사 -------------------------------------------------------------
    drive = Drivable(REPO / spec["map"]["dir"])
    occ = [(o, poly) for o, poly in zones if o["type"] == "occupancy"]
    if len(occ) > 10:
        warnings.append(f"점유 존 {len(occ)} 개: FMS 경로 계산 비용이 gate 수 제곱으로 는다 (고속화 전에는 10 개 이하 권장)")
    waits, exits = [], []
    for o, poly in occ:
        if len(o.get("gates", [])) == 0:
            errors.append(f"점유 존 {o['name']}: gate 가 없다 (로봇이 통과할 수 없다)")
        elif len(o["gates"]) == 1:
            warnings.append(f"점유 존 {o['name']}: gate 하나 = 일방통행")
        for g in o.get("gates", []):
            for kind in ("wait", "exit"):
                p = g[kind]
                tag = f"{o['name']}/{g['gate_id']} {kind} ({p['x']:.2f}, {p['y']:.2f})"
                if not drive(p["x"], p["y"]):
                    errors.append(f"{tag}: 로봇 폭 팽창 후 주행 불가")
                for o2, poly2 in occ:
                    if inside(poly2, p["x"], p["y"], RADIUS):
                        errors.append(f"{tag}: 점유 존 {o2['name']} 안 (차체 반경 {RADIUS:.2f} m 포함) — 존 밖으로")
            waits.append((f"{o['name']}/{g['gate_id']}", g["wait"]))
            exits.append((f"{o['name']}/{g['gate_id']}", g["exit"]))
    for wn, w in waits:
        for en, e in exits:
            if wn == en:
                continue
            d = math.hypot(w["x"] - e["x"], w["y"] - e["y"])
            if d < MIN_SEP:
                errors.append(f"대기 {wn} 와 출구 {en} 가 {d:.2f} m: 서로의 자리를 기다리는 교착 (최소 {MIN_SEP:.2f}, 권장 {GOOD_SEP})")
            elif d < GOOD_SEP:
                warnings.append(f"대기 {wn} 와 출구 {en} 가 {d:.2f} m (권장 {GOOD_SEP} m 이상)")
    for i, (an, a) in enumerate(waits):
        for bn, b in waits[i + 1:]:
            if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < MIN_SEP:
                warnings.append(f"대기 node {an} 와 {bn} 가 {MIN_SEP:.2f} m 안: 한쪽이 서 있으면 다른 쪽이 못 들어온다")
    stations = [l for l in spec["locations"] if l["type"] in ("station", "waypoint", "parking", "other")]
    for l in stations:
        if not drive(l["x"], l["y"]):
            errors.append(f"목적지 {l['name']} ({l['x']:.2f}, {l['y']:.2f}): 로봇 폭 팽창 후 주행 불가")
        for o, poly in occ:
            if inside(poly, l["x"], l["y"], RADIUS):
                errors.append(f"목적지 {l['name']}: 점유 존 {o['name']} 안 — FMS 가 작업 목적지로 거절한다")
    if sum(1 for l in spec["locations"] if l["type"] == "station") < 2:
        warnings.append("작업 지점(type station) 이 2 개 미만")
    return spec, errors, warnings


def main():
    ap = argparse.ArgumentParser(description="편집용 FMS 현장 -> 스펙 JSON + 검사")
    ap.add_argument("site", choices=("cleanroom", "angled", "textile"))
    ap.add_argument("--fms", default="http://127.0.0.1:8790")
    ap.add_argument("--check", action="store_true", help="저장하지 않고 검사만")
    a = ap.parse_args()
    try:
        spec, errors, warnings = export(a.fms, a.site)
    except (ProvisionError, OSError) as e:
        print("FMS 읽기 실패 (edit_fms.py 가 떠 있나?):", e)
        return 1
    occ = sum(1 for z in spec["zones"] if z["type"] == "occupancy")
    kinds = {}
    for l in spec["locations"]:
        kinds[l["type"]] = kinds.get(l["type"], 0) + 1
    print(f"{a.site}: 점유 존 {occ}개 (gate {sum(len(z.get('gates', [])) for z in spec['zones'])}), "
          f"목적지 {kinds}, 충전기 {len(spec['chargers'])}개")
    for w in warnings:
        print("  경고:", w)
    for e in errors:
        print("  오류:", e)
    if not a.check:
        path = REPO / "experiments" / "sites" / f"factory_{a.site}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("저장:", path.relative_to(REPO))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
