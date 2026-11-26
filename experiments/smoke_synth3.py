#!/usr/bin/env python3
"""synth3 현장 확인: 격리 브로커 + FMS + 로봇 2대로 주 통로 양방향 동시 주문 1회.

    .venv/bin/python experiments/smoke_synth3.py [--viewer] [--no-zones] [--keep]     # 리포 루트에서

- 브로커·FMS HTTP 는 빈 포트를 골라 쓰고 FMS 데이터는 임시 폴더에 둔다 (1883·8000 의 다른 세션과 섞이지 않는다).
- FMS 는 형제 폴더 ../AMR-fms (AMR_FMS_DIR 로 변경) 의 .venv 로 띄운다.
- 검사: 두 작업 성공, 점유 존 진입 허가 2회, 대기 node 도착 2회, 한쪽은 대기(queued_s > 0).
  --no-zones 면 존 없이(조건 ①) 같은 주문을 보내고 결과만 출력한다 (1 m 통로 정면 대치로 막히는 것이 정상).
종료 코드 0 = 통과.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision import Fms, provision  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FMS_DIR = Path(os.environ.get("AMR_FMS_DIR", REPO.parent / "AMR-fms"))
SPEC = REPO / "experiments" / "sites" / "synth3.json"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer", action="store_true", help="플릿 뷰어 창을 띄운다")
    ap.add_argument("--no-zones", action="store_true", help="점유 존 없이 (조건 ①)")
    ap.add_argument("--keep", action="store_true", help="끝난 뒤 Enter 를 누를 때까지 그대로 둔다")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="smoke_synth3_"))
    mport, hport = free_port(), free_port()
    procs = []
    logs = tmp / "logs"
    logs.mkdir()

    def spawn(name, cmd, cwd, env=None):
        f = open(logs / f"{name}.log", "w")
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
        procs.append(p)
        return p

    ok = False
    try:
        conf = tmp / "mosquitto.conf"
        conf.write_text(f"listener {mport} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        spawn("broker", [shutil.which("mosquitto") or "/opt/homebrew/sbin/mosquitto", "-c", str(conf)], REPO)
        env = dict(os.environ, FMS_MQTT_HOST="127.0.0.1", FMS_MQTT_PORT=str(mport), FMS_DATA_ROOT=str(tmp / "fms"),
                   FMS_PUBLIC_URL=f"http://127.0.0.1:{hport}", FMS_ELEVATOR_SIM="0", FMS_DOOR_SIM="0")
        spawn("fms", [str(FMS_DIR / ".venv/bin/uvicorn"), "fms_server.main:app", "--host", "127.0.0.1",
                      "--port", str(hport)], FMS_DIR, env)
        base = f"http://127.0.0.1:{hport}"
        if not wait_http(base + "/health", 30):
            print("FMS 기동 실패:", (logs / "fms.log").read_text()[-2000:])
            return 1
        robots = {"AMR-01": (1.2, 9.0, 0.0), "AMR-02": (39.8, 9.0, 3.14159)}
        info = provision(base, SPEC, "exp", list(robots), zones=not a.no_zones)
        print(f"등록: 맵 버전 {info['version']}, 존 {info['zones']}, 목적지 {len(info['locations'])}개, 충전기 {info['chargers']}")
        for rid, (x, y, t) in robots.items():
            spawn(rid, [str(REPO / "build/bin/robot_client"), "--id", rid, "--host", "127.0.0.1", "--port", str(mport),
                        "--x", str(x), "--y", str(y), "--theta", str(t), "--map-cache", str(tmp / f"cache-{rid}"),
                        "--seed", "1"], REPO)
        if a.viewer:
            spawn("viewer", [str(REPO / ".venv/bin/python"), "tools/fleet_monitor_qt.py", str(REPO / "maps/synth3/fms_map.json"),
                             "--host", "127.0.0.1", "--port", str(mport), "--fms", base], REPO / "viewer")
        fms = Fms(base)
        # 로봇이 활성 맵 버전을 보고할 때까지 (SET_MAP 번들 수신)
        end = time.time() + 60
        while time.time() < end:
            rs = {r["robot_id"]: r for r in fms.get("/robots")["robots"]}
            if all((rs.get(r) or {}).get("map_synced") for r in robots):
                break
            time.sleep(1)
        else:
            print("로봇이 맵 버전을 보고하지 않음:", json.dumps(rs, ensure_ascii=False)[:800])
            return 1
        t0 = time.time()
        jobs = {}
        for rid, target in (("AMR-01", "E-03"), ("AMR-02", "W-03")):
            j = fms.post("/sites/exp/jobs", {"commands": [{"type": "move", "target": target}], "robot_id": rid})
            fms.post(f"/sites/exp/jobs/{j['job_id']}/dispatch", {"robot_id": rid})
            jobs[rid] = j["job_id"]
        print("주문:", jobs)
        end = time.time() + a.timeout
        status = {}
        while time.time() < end:
            status = {rid: fms.get(f"/sites/exp/jobs/{jid}")["status"] for rid, jid in jobs.items()}
            if all(s in ("success", "failed", "canceled") for s in status.values()):
                break
            time.sleep(1)
        elapsed = time.time() - t0
        ev = fms.get(f"/events?site=exp&kind=traffic.,job.&limit=500")["events"][::-1]
        reserve = [e for e in ev if e["kind"] == "traffic.reserve" and e["data"].get("lease_kind") == "zone"]
        at_wait = [e for e in ev if e["kind"] == "traffic.at_wait"]
        stalls = [e for e in ev if e["kind"] in ("traffic.stall", "traffic.deadlock")]
        print(f"결과 ({elapsed:.0f}초): {status}")
        for e in reserve:
            print(f"  존 허가 {e['robot_id']} {e['data'].get('zone_id')}/{e['data'].get('gate_id')} queued_s={e['data'].get('queued_s')}")
        for e in at_wait:
            print(f"  대기 node 도착 {e['robot_id']} {e['data'].get('gate_id')} approach_s={e['data'].get('approach_s')} "
                  f"zone_busy={e['data'].get('zone_busy')}")
        for e in stalls:
            print(f"  {e['kind']} {e['robot_id']} {e['data']}")
        snap = [e["data"].get("snapshot") for e in ev if e["kind"] == "job.assigned"]
        print("  배정 스냅샷 zones:", [[(z["zone_id"], z["gate_id"], z["reserved_ahead"]) for z in (s or {}).get("zones", [])]
                                      for s in snap])
        if a.no_zones:
            ok = True
        else:
            ok = (all(s == "success" for s in status.values()) and len(reserve) == 2 and len(at_wait) == 2
                  and any((e["data"].get("queued_s") or 0) > 0.5 for e in reserve))
        print("통과" if ok else "실패", f"(로그: {logs})")
        if a.keep:
            input("Enter 를 누르면 종료합니다...")
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
