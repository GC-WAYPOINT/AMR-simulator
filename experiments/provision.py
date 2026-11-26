#!/usr/bin/env python3
"""실험 현장을 FMS 에 등록한다 (현장 → 맵 업로드·활성화 → 점유 존 → 충전기 → 목적지 → 로봇).

    .venv/bin/python experiments/provision.py experiments/sites/synth3.json --fms http://127.0.0.1:8765 \\
        --site exp --robots AMR-01,AMR-02            # 리포 루트에서
    ... --no-zones                                   # 조건 ① (개별 주행): 점유 존 없이 같은 맵·목적지

표준 라이브러리만 쓴다 (러너가 그대로 import 한다). 존·충전기는 맵 새 버전을 만들므로 응답의 version 을
다음 요청의 base_version 으로 이어 쓴다. 실패하면 HTTP 상태와 본문을 담아 ProvisionError 를 던진다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class ProvisionError(RuntimeError):
    pass


class Fms:
    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/") + "/api/v1"
        self.timeout = timeout

    def _send(self, method: str, path: str, data: bytes | None, ctype: str | None):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise ProvisionError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:500]}") from None
        return json.loads(body) if body else None

    def get(self, path):
        return self._send("GET", path, None, None)

    def post(self, path, body):
        return self._send("POST", path, json.dumps(body).encode(), "application/json")

    def post_files(self, path, fields: dict[str, str], files: list[Path]):
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for f in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"\r\n'
                         f'Content-Type: application/octet-stream\r\n\r\n'.encode() + f.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self._send("POST", path, b"".join(parts), f"multipart/form-data; boundary={boundary}")


def provision(fms_url: str, spec_path: Path, site: str, robots: list[str], *, zones: bool = True) -> dict:
    """등록하고 {site, map_id, version, zones, locations, chargers, robots} 를 돌려준다."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    fms = Fms(fms_url)
    m = spec["map"]
    map_id = m["map_id"]
    fms.post("/sites", {"site_id": site, "name": f"실험 {site}"})
    folder = REPO / m["dir"]
    up = fms.post_files(f"/sites/{site}/maps", {"map_id": map_id, "activate": "true"},
                        [folder / name for name in m["files"]])
    version = up["version"]
    base = f"/sites/{site}/maps/{map_id}"
    zone_ids, zone_names = [], {}   # FMS 가 zone_id 를 새로 만든다 (운행마다 다름) — 데이터셋은 이름으로 묶는다
    if zones:
        for z in spec.get("zones", []):
            body = {k: z[k] for k in ("name", "type", "x", "y", "width", "height", "gates", "rotation", "speed_limit")
                    if k in z}
            r = fms.post(base + "/zones", dict(body, base_version=version))
            version = r["version"]
            zone_ids.append(r["zone_id"])
            zone_names[r["zone_id"]] = z["name"]
    for c in spec.get("chargers", []):
        r = fms.post(base + "/docking-stations", dict(c, base_version=version))
        version = r["version"]
    for loc in spec.get("locations", []):
        fms.post(f"/sites/{site}/locations", dict(loc, map_id=map_id))
    for rid in robots:
        fms.post(f"/sites/{site}/robots", {"robot_id": rid, "map_id": map_id})
    return {"site": site, "map_id": map_id, "version": version, "zones": zone_ids, "zone_names": zone_names,
            "locations": [l["name"] for l in spec.get("locations", [])],
            "chargers": [c["name"] for c in spec.get("chargers", [])], "robots": robots}


def main():
    ap = argparse.ArgumentParser(description="실험 현장을 FMS 에 등록한다")
    ap.add_argument("spec", type=Path, help="현장 스펙 JSON (experiments/sites/*.json)")
    ap.add_argument("--fms", default="http://127.0.0.1:8765", help="FMS 주소")
    ap.add_argument("--site", default="exp", help="만들 현장 ID")
    ap.add_argument("--robots", default="", help="등록할 로봇 ID (쉼표)")
    ap.add_argument("--no-zones", action="store_true", help="점유 존 없이 등록 (조건 ① 개별 주행)")
    a = ap.parse_args()
    try:
        out = provision(a.fms, a.spec, a.site, [r for r in a.robots.split(",") if r], zones=not a.no_zones)
    except (ProvisionError, OSError) as e:
        print("등록 실패:", e, file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
