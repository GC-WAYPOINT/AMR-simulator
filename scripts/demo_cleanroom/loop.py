"""cleanroom 시연 연속 운전: 진행 중 작업이 --inflight 개보다 적으면 시연 프리셋을 차례로 게시한다.

사용 (AMR-simulator 루트): python3 scripts/demo_cleanroom/loop.py [--inflight 5] [--presets 1,2,3,4,5,6]
Ctrl+C 로 게시를 멈춘다. 이미 게시된 작업은 끝까지 진행된다 (취소하려면 --cancel-on-exit).
작업을 마친 로봇은 FMS 충전 정책(setup.py)에 따라 충전소로 갔다가 다음 작업을 받는다.
"""
import argparse
import itertools
import json
import time
import urllib.error
import urllib.request

SITE = 'cleanroom'
TERMINAL = ('success', 'failed', 'canceled')


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inflight', type=int, default=5, help='동시에 진행할 시연 작업 수 (기본 5 = 로봇 수)')
    ap.add_argument('--presets', default='1,2,3,4,5,6', help='돌릴 시연 번호 순서')
    ap.add_argument('--interval', type=float, default=3.0)
    ap.add_argument('--cancel-on-exit', action='store_true', help='Ctrl+C 때 진행 중 시연 작업을 취소')
    ap.add_argument('--fms', default='http://127.0.0.1:8000')
    args = ap.parse_args()
    base = args.fms + f'/api/v1/sites/{SITE}'

    presets = {int(p['name'][2:].split()[0]): p for p in call(base, 'GET', '/presets')
               if p['name'].startswith('시연')}
    order = [int(n) for n in args.presets.split(',')]
    missing = [n for n in order if n not in presets]
    if missing:
        raise SystemExit(f'프리셋 없음: {missing} — setup.py 먼저')
    names = {presets[n]['name'] for n in order}
    cycle = itertools.cycle(order)
    seen, published, done, failed = {}, 0, 0, 0
    print(f'연속 운전 시작: 동시 {args.inflight}개, 순서 {order}. Ctrl+C 로 멈춤.', flush=True)
    try:
        while True:
            try:
                jobs = [j for j in call(base, 'GET', '/jobs')
                        if not j.get('system') and j.get('preset_name') in names]
            except (urllib.error.URLError, TimeoutError) as exc:
                print(time.strftime('%H:%M:%S'), 'FMS 연결 실패:', exc, flush=True)
                time.sleep(5)
                continue
            for j in jobs:
                prev = seen.get(j['job_id'])
                if j['job_id'] in seen and prev != j['status'] and j['status'] in TERMINAL:
                    if j['status'] == 'success':
                        done += 1
                    else:
                        failed += 1
                    print(time.strftime('%H:%M:%S'), f"{j['status']:8} {j['job_id']} {j['robot_id']} "
                          f"{j['preset_name']}{' — ' + j['error'] if j.get('error') else ''}"
                          f"  [완료 {done} / 실패 {failed} / 게시 {published}]", flush=True)
                if j['job_id'] in seen or j['status'] not in TERMINAL:
                    seen[j['job_id']] = j['status']
            active = [j for j in jobs if j['status'] not in TERMINAL]
            for _ in range(args.inflight - len(active)):
                n = next(cycle)
                job = call(base, 'POST', f'/presets/{presets[n]["preset_id"]}/publish', {})
                seen[job['job_id']] = job['status']
                published += 1
                print(time.strftime('%H:%M:%S'), f"게시     {job['job_id']} {presets[n]['name']}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f'\n게시 중지. 완료 {done} / 실패 {failed} / 게시 {published}', flush=True)
        if args.cancel_on_exit:
            for j in call(base, 'GET', '/jobs'):
                if not j.get('system') and j.get('preset_name') in names and j['status'] not in TERMINAL:
                    call(base, 'POST', f"/jobs/{j['job_id']}/cancel")
                    print('취소', j['job_id'], flush=True)


if __name__ == '__main__':
    main()
