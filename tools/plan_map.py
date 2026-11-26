#!/usr/bin/env python3
"""
평면도(plan JSON)에서 맵 그리기
===============================

synth_map.py 의 레이아웃은 1 m 격자라 사선/곡선 벽을 못 그린다. 여기서는 실제 평면도에서 딴
다각형·꺾은선 좌표를 그대로 래스터화하고, 질감(벽/테두리/광선 노이즈)은 synth_map.render 를 쓴다.

    .venv/bin/python tools/plan_map.py tools/map_plans/factory_textile.json --out maps/factory_textile   # 리포 루트에서

plan JSON (좌표는 모두 원본 도면 px, 길이는 m)
    scale    도면 1 px 이 몇 m 인지
    source   도면 출처 (gen_info.json 에 기록)
    outline  주행 가능한 바닥 다각형 [[x,y], ...]
    solid    막힌 곳 다각형 목록 (중정, 탱크, 기계)
    walls    [{"p": [[x,y],...], "t": 두께 m(기본 0.15), "d": [[x,y,폭 m], ...]}]  꺾은선. d 는 문(벽을 그 폭만큼 뚫는다)
    rooms    [[x0,y0,x1,y1,"문"], ...]  사각형 방. 문 = "S" "N1.8" "E W" 처럼 변(N/S/E/W)+폭 m(기본 1.0).
             변 가운데에 낸다. "S@0.2" 는 변의 20% 위치
    doors    [[x,y,폭 m], ...]  가장 가까운 벽에 문을 낸다 (벽/방을 다 그린 뒤)
    columns  [{"x": [..], "y": [..], "size": 0.4}]  기둥
    fills    [{"rect": [x0,y0,x1,y1], "block": [길이,폭], "aisle": m, "main": m, "every": n,
               "cross": n, "angle": 0|90}]
             설비/랙 배치. 길이 방향 블록을 aisle 간격으로 놓고 every 줄마다 main 통로, cross 블록마다
             main 통로를 가로로 낸다. 벽/기존 장애물에서 aisle 이상 떨어진 자리에만 놓는다

world 좌표 = 도면 px × scale - (outline 최소점 × scale) + 1 m. y 는 아래로 증가 (이미지 그대로).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from clean_map import inflate_free, label
from synth_map import FREE_THRESH, OCCUPIED_THRESH, render

SIDES = {"N": (0, 1, 2, 1), "S": (0, 3, 2, 3), "W": (0, 1, 0, 3), "E": (2, 1, 2, 3)}  # rect 인덱스


class Canvas:
    """world m 좌표로 그리는 통로 마스크 (255 = 통로)."""

    def __init__(self, w_m: float, h_m: float, res: float):
        self.res = res
        self.img = Image.new("L", (int(np.ceil(w_m / res)), int(np.ceil(h_m / res))), 0)
        self.d = ImageDraw.Draw(self.img)

    def polygon(self, pts, value: int) -> None:
        self.d.polygon([(x / self.res - 0.5, y / self.res - 0.5) for x, y in pts], fill=value)

    def segment(self, a, b, thick: float, value: int, extend: float = 0.0) -> None:
        """선분 a-b 를 두께 thick 사각형으로. 각도를 그대로 지킨다."""
        a, b = np.array(a, float), np.array(b, float)
        v = b - a
        n = np.linalg.norm(v)
        if n < 1e-9:
            return
        u = v / n
        side = np.array([-u[1], u[0]]) * thick / 2
        a, b = a - u * extend, b + u * extend
        self.polygon([a + side, b + side, b - side, a - side], value)

    def array(self) -> np.ndarray:
        return np.asarray(self.img) > 127


def nearest_segment(segs, p):
    """(거리, 선분 위 가장 가까운 점, 방향 단위벡터, 두께)"""
    best = None
    for a, b, t in segs:
        v = b - a
        k = np.clip((p - a) @ v / max(v @ v, 1e-9), 0, 1)
        q = a + k * v
        dist = np.linalg.norm(p - q)
        if best is None or dist < best[0]:
            best = (dist, q, v / max(np.linalg.norm(v), 1e-9), t)
    return best


def build(plan: dict, res: float):
    s = plan["scale"]
    outline = np.array(plan["outline"], float)
    off = outline.min(0) * s - 1.0
    W = lambda p: np.array(p, float) * s - off  # 도면 px -> world m
    size = outline.max(0) * s - off + 1.0
    cv = Canvas(size[0], size[1], res)
    cv.polygon(W(outline), 255)
    for poly in plan.get("solid", []):
        cv.polygon(W(poly), 0)

    segs, doors = [], []
    for wall in plan.get("walls", []):
        pts, t = W(wall["p"]), wall.get("t", 0.15)
        segs += [(a, b, t) for a, b in zip(pts[:-1], pts[1:])]
        doors += [(W(d[:2]), d[2]) for d in wall.get("d", [])]
    for room in plan.get("rooms", []):
        x0, y0, x1, y1 = room[:4]
        c = W([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        segs += [(c[i], c[(i + 1) % 4], 0.15) for i in range(4)]
        for tok in (room[4] if len(room) > 4 else "").split():
            side, _, at = tok.partition("@")
            ax, ay, bx, by = (room[i] for i in SIDES[side[0]])
            a, b = W([ax, ay]), W([bx, by])
            doors.append((a + (b - a) * float(at or 0.5), float(side[1:] or 1.0)))
    doors += [(W(d[:2]), d[2]) for d in plan.get("doors", [])]

    for a, b, t in segs:
        cv.segment(a, b, t, 0, extend=t / 2)
    # 문: 그 자리의 벽을 폭만큼 뚫는다. 맞닿은 방처럼 벽이 겹쳐도 사각형 하나로 같이 뚫린다
    floor = Canvas(size[0], size[1], res)
    floor.polygon(W(outline), 255)
    opened = Canvas(size[0], size[1], res)
    for p, width in doors:
        dist, q, u, t = nearest_segment(segs, p)
        if dist > 1.5:
            print(f"경고: 문 {((p + off) / s).round()} 근처에 벽이 없다 ({dist:.1f} m)", file=sys.stderr)
            continue
        opened.segment(q - u * width / 2, q + u * width / 2, t + 0.3, 255)
    free = cv.array() | (opened.array() & floor.array())
    for poly in plan.get("solid", []):  # 문이 solid 를 뚫지 않게
        c = Canvas(size[0], size[1], res)
        c.polygon(W(poly), 255)
        free &= ~c.array()

    place_fills(free, plan.get("fills", []), W, res)

    for col in plan.get("columns", []):
        half = col.get("size", 0.4) / 2
        for x in col["x"]:
            for y in col["y"]:
                px, py = W([x, y])
                i0, i1 = int((py - half) / res), int((py + half) / res)
                j0, j1 = int((px - half) / res), int((px + half) / res)
                # 기둥이 통로를 좁혀 막지 않게, 주변 0.8 m 가 비어 있는 자리에만 세운다
                m = int(0.8 / res)
                if free[i0 - m:i1 + m, j0 - m:j1 + m].all():
                    free[i0:i1, j0:j1] = False
    return free, off


def place_fills(free: np.ndarray, fills, W, res: float) -> None:
    for f in fills:
        x0, y0, x1, y1 = f["rect"]
        (ax, ay), (bx, by) = W([x0, y0]), W([x1, y1])
        vertical = f.get("angle", 0) == 90
        length, width = f["block"]
        aisle, main = f["aisle"], f.get("main", f["aisle"])
        every, cross = f.get("every", 0), f.get("cross", 0)
        # u = 블록 길이 방향, v = 줄이 쌓이는 방향
        u0, u1, v0, v1 = (ay, by, ax, bx) if vertical else (ax, bx, ay, by)
        base = free.copy()
        v, row = v0, 0
        while v + width <= v1 + 1e-6:
            u, n = u0, 0
            while u + 2.0 <= u1:
                ln = min(length, u1 - u)
                bu0, bu1 = u - aisle, u + ln + aisle
                box = (v - aisle, v + width + aisle, bu0, bu1)
                r0, r1, c0, c1 = (box[2], box[3], box[0], box[1]) if vertical else box
                # 길이 방향 끝은 res 만큼 여유 (rect 가장자리 반올림)
                region = base[max(0, int(r0 / res)):int(r1 / res), max(0, int(c0 / res)):int(c1 / res)]
                if region.size and region.all():
                    p = (v, v + width, u, u + ln)
                    a0, a1, b0, b1 = (p[2], p[3], p[0], p[1]) if vertical else p
                    free[int(a0 / res):int(a1 / res), int(b0 / res):int(b1 / res)] = False
                    u += ln
                    n += 1
                    u += main if cross and n % cross == 0 else 0.0
                else:
                    u += 1.0  # 벽/장애물을 피해 조금씩 밀어 본다
            row += 1
            v += width + (main if every and row % every == 0 else aisle)


def report(img: np.ndarray, radius_px: int, res: float, off: np.ndarray, s: float):
    """planner 규칙으로 팽창해 연결 성분을 본다. 가장 큰 성분 비율과 떨어진 큰 구역을 돌려준다."""
    labels, _ = label(inflate_free(img >= FREE_THRESH, radius_px))
    ids, counts = np.unique(labels[labels > 0], return_counts=True)
    order = np.argsort(counts)[::-1]
    ratio = counts[order[0]] / counts.sum()
    islands = []
    for i in order[1:]:
        area = counts[i] * res * res
        if area < 2.0:
            break
        rr, cc = np.nonzero(labels == ids[i])
        k = len(rr) // 2
        islands.append((round(float(area), 1), np.round((np.array([cc[k], rr[k]]) * res + off) / s).astype(int).tolist()))
    return ratio, islands, labels == ids[order[0]]


def main() -> int:
    ap = argparse.ArgumentParser(description="평면도 plan JSON 에서 맵 생성")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--resolution", type=float, default=0.05, help="m/px (기본 0.05, 대형 맵용)")
    ap.add_argument("--margin-m", type=float, default=10.0, help="사방 회색(미탐색) 여백 m")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--robot-width", type=float, default=0.5)
    ap.add_argument("--wall-px", type=int, default=2)
    ap.add_argument("--fringe-px", type=int, default=1)
    ap.add_argument("--fringe-prob", type=float, default=0.4)
    ap.add_argument("--wobble-px", type=float, default=0.0, help="벽 흔들림 px (0 = 도면 각도 그대로)")
    ap.add_argument("--dash-gap", type=float, default=0.15)
    ap.add_argument("--dash-px", type=int, default=4)
    ap.add_argument("--fur-prob", type=float, default=0.05)
    ap.add_argument("--fur-len", type=int, default=8)
    ap.add_argument("--ray-prob", type=float, default=0.008)
    ap.add_argument("--ray-len", type=int, default=100)
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    res = args.resolution
    base, off = build(plan, res)
    m = round(args.margin_m / res)
    img = render(np.pad(base, m, constant_values=False), args, np.random.default_rng(args.seed))
    off_img = off - m * res  # 이미지 좌상단 px 의 도면 좌표(m)
    radius_px = int(np.ceil((args.robot_width / 2) / res))
    ratio, islands, main_mask = report(img, radius_px, res, off_img, plan["scale"])

    args.out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, "L").save(args.out / "grid.png")
    edited = args.out / "edited_navi_gridmap.png"
    created = not edited.exists()
    if created:
        shutil.copyfile(args.out / "grid.png", edited)
    elif Image.open(edited).size != (img.shape[1], img.shape[0]):
        print(f"경고: 기존 {edited.name} 크기가 새 grid.png 와 다르다. 지우고 다시 생성하라", file=sys.stderr)
    meta = {
        "name": args.out.name, "image": edited.name, "resolution": res,
        "origin_x": round(-m * res, 6), "origin_y": round(-m * res, 6),
        "width_px": Image.open(edited).size[0], "height_px": Image.open(edited).size[1],
        "occupied_thresh": OCCUPIED_THRESH, "free_thresh": FREE_THRESH,
    }
    (args.out / "fms_map.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(args.plan, args.out / "plan.json")
    info = {"source": plan.get("source"), "generator": "plan_map.py", "seed": args.seed,
            "connected_ratio": round(float(ratio), 4), "isolated_areas": len(islands),
            "params": {k: v for k, v in vars(args).items() if k not in ("plan", "out", "seed")}}
    (args.out / "gen_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    H, Wd = img.shape
    print(f"저장: {args.out}/fms_map.json  {Wd * res:.1f} × {H * res:.1f} m (여백 {args.margin_m:g} m, {Wd}×{H} px)")
    print(f"주행 가능 면적 {main_mask.sum() * res * res:.0f} m², 가장 큰 연결 구역 비율 {ratio:.4f}")
    for area, px in islands[:25]:
        print(f"  떨어진 구역 {area} m² (도면 px {px})")
    # 시작 좌표 예시: 가장 큰 구역 안에서 주변 1 m 가 비어 있는 점
    wide = inflate_free(img >= FREE_THRESH, round(1.0 / res)) & main_mask
    rr, cc = np.nonzero(wide)
    spots = [(cc[k] * res - m * res, rr[k] * res - m * res) for k in np.linspace(0, len(rr) - 1, 7).astype(int)[1:-1]]
    print("주행 가능 좌표 예시:", ", ".join(f"({x:.1f}, {y:.1f})" for x, y in spots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
