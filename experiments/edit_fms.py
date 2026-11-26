#!/usr/bin/env python3
"""현장 편집용 FMS: factory 맵 3종을 현장으로 올려 두고 웹 UI 에서 점유 존·목적지·충전기를 찍는다.

    .venv/bin/python experiments/edit_fms.py            # 리포 루트에서. Ctrl+C 로 종료
    -> 브라우저 http://127.0.0.1:8790/  (현장: cleanroom / angled / textile, 맵 floor-1)

- 데이터는 리포 밖 ../exp_fms_edit/ 에 남는다 (다시 띄워도 편집 내용 유지). 처음 띄울 때만 현장을 등록한다.
- MQTT 는 아무도 안 듣는 포트(18890)로 둔다: 1883 의 실제 로봇·브로커와 섞이지 않게. 로봇 없이 편집만 한다.
- 다 찍었으면: .venv/bin/python experiments/export_site.py cleanroom   (스펙 저장 + 규칙 검사)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision import Fms, ProvisionError, provision  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FMS_DIR = Path(os.environ.get("AMR_FMS_DIR", REPO.parent / "AMR-fms"))
DATA = Path(os.environ.get("EXP_EDIT_DATA", REPO.parent / "exp_fms_edit"))
SITES = ("cleanroom", "angled", "textile")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--check", action="store_true", help="띄우고 등록만 확인한 뒤 종료 (자동 시험용)")
    a = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    env = dict(os.environ, FMS_DATA_ROOT=str(DATA), FMS_MQTT_HOST="127.0.0.1", FMS_MQTT_PORT="18890",
               FMS_PUBLIC_URL=base, FMS_ELEVATOR_SIM="0", FMS_DOOR_SIM="0")
    proc = subprocess.Popen([str(FMS_DIR / ".venv/bin/uvicorn"), "fms_server.main:app", "--host", "127.0.0.1",
                             "--port", str(a.port), "--log-level", "warning"], cwd=FMS_DIR, env=env)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("FMS 기동 실패")
            return 1
        fms = Fms(base)
        have = {s["site_id"] for s in fms.get("/sites")}
        for name in SITES:
            if name in have:
                continue
            provision(base, REPO / "experiments" / "sites" / f"factory_{name}.json", name, [], zones=False)
            print(f"현장 {name} 등록 (맵 floor-1)")
        print(f"편집용 FMS: {base}/   데이터 {DATA}")
        print("현장마다: 실시간 맵에서 구역(점유 존 + gate)·목적지·도킹 스테이션을 추가한다. 끝나면 Ctrl+C.")
        if a.check:
            return 0
        proc.wait()
    except (ProvisionError, OSError) as e:
        print("등록 실패:", e)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
