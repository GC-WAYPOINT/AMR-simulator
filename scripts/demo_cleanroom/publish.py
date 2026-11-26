"""cleanroom 시연 프리셋을 작업 게시판에 올린다 (FMS 가 빈 로봇에 자동 배정).

사용 (AMR-simulator 루트):
  python3 scripts/demo_cleanroom/publish.py            # 시연1~5 를 한 번에 (로봇 5대 동시 출발)
  python3 scripts/demo_cleanroom/publish.py 1 4 6      # 번호로 골라서
  python3 scripts/demo_cleanroom/publish.py --list     # 프리셋 목록만
"""
import argparse
import json
import urllib.error
import urllib.request

SITE = 'cleanroom'


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
    ap.add_argument('numbers', nargs='*', type=int, help='시연 번호 (기본 1~5)')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--fms', default='http://127.0.0.1:8000')
    args = ap.parse_args()
    base = args.fms + f'/api/v1/sites/{SITE}'

    presets = {}
    for p in call(base, 'GET', '/presets'):
        if p['name'].startswith('시연'):
            presets[int(p['name'][2:].split()[0])] = p
    if args.list:
        for n, p in sorted(presets.items()):
            print(n, p['name'])
        return
    for n in args.numbers or [1, 2, 3, 4, 5]:
        if n not in presets:
            raise SystemExit(f'시연{n} 프리셋 없음 — setup.py 먼저')
        job = call(base, 'POST', f'/presets/{presets[n]["preset_id"]}/publish', {})
        print(f'게시 {presets[n]["name"]} -> job {job["job_id"]} ({job["status"]})')


if __name__ == '__main__':
    main()
