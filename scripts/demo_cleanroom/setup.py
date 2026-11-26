"""cleanroom 시연 준비: 로봇 5대 site 등록 + 시연 프리셋 생성 (같은 이름이면 덮어씀).

사용 (AMR-simulator 루트): python3 scripts/demo_cleanroom/setup.py [--fms http://127.0.0.1:8000]
목적지 이름은 FMS cleanroom 사이트에 사용자가 등록한 것(상차지점 A-n, 반납지점n)을 그대로 쓴다.
"""
import argparse
import json
import urllib.error
import urllib.request

SITE, MAP = 'cleanroom', 'floor-1'
ROBOTS = [f'AMR-10{i}' for i in range(1, 6)]
PICK_S, DROP_S = 5, 5   # 상차·하차 작업 시간 (초)
CHARGING = {'enabled': True, 'opportunity': True, 'idle_after': 3, 'low': 25, 'critical': 10,
            'release': 99, 'min_job_charge': 20}


def pick(n):
    return {'type': 'pickup', 'target': f'상차지점 A-{n}', 'duration': PICK_S}


def drop(n):
    return {'type': 'drop', 'target': f'반납지점{n}', 'duration': DROP_S}


PRESETS = {
    '시연1 A-1 상차 → 반납1': [pick(1), drop(1)],
    '시연2 A-3 상차 → 반납2': [pick(3), drop(2)],
    '시연3 A-5 상차 → 반납3': [pick(5), drop(3)],
    '시연4 A-7 상차 → 반납1': [pick(7), drop(1)],
    '시연5 A-2·A-4 연속 상차 → 반납2': [pick(2), pick(4), drop(2)],
    '시연6 A-6 상차 → 반납3': [pick(6), drop(3)],
}


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {path} -> {e.code} {e.read().decode()}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fms', default='http://127.0.0.1:8000')
    base = ap.parse_args().fms + f'/api/v1/sites/{SITE}'

    for rid in ROBOTS:
        r = call(base, 'POST', '/robots', {'robot_id': rid, 'map_id': MAP})
        print(f'로봇 등록 {rid} -> {MAP} (SET_MAP 전송: {r["set_map_sent"]})')

    # 작업을 마친 로봇은 유휴 3초 뒤 가장 가까운 빈 충전소로 간다 (기회 충전).
    # release 99: 작업 한 번이면 거의 항상 99% 아래로 내려가서 매번 충전하러 간다. 충전 속도 1%/s.
    call(base, 'PUT', '/charging', CHARGING)
    print(f'충전 정책 {CHARGING}')

    existing = {p['name']: p['preset_id'] for p in call(base, 'GET', '/presets')}
    for name, commands in PRESETS.items():
        body = {'name': name, 'commands': commands}
        if name in existing:
            call(base, 'PUT', f'/presets/{existing[name]}', body)
            print(f'프리셋 갱신 {name}')
        else:
            call(base, 'POST', '/presets', body)
            print(f'프리셋 생성 {name}')


if __name__ == '__main__':
    main()
