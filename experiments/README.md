# 실험 (FMS 연동, AI 학습 데이터 수집)

설계서 3장(데이터 수집)·9장(실험)의 실행 도구. FMS 는 형제 폴더 `../AMR-fms` (`AMR_FMS_DIR` 로 변경)에 있어야 한다.
명령은 전부 리포 루트에서 실행한다.

| 파일 | 내용 |
|---|---|
| `sites/synth3.json` | synth3 현장 스펙: 점유 존 3개(gate 별 대기·출구 node), 작업 지점 W/E-01~05, 주차 P-W/E1~3, 충전기 C-W/C-E, 로봇 시작 자리 |
| `provision.py` | 스펙을 FMS REST 로 등록 (현장 → 맵 업로드·활성화 → 존 → 충전기 → 목적지 → 로봇). `--no-zones` = 조건 ① |
| `smoke_synth3.py` | 격리 브로커·FMS(빈 포트, 임시 데이터 폴더) + 로봇 2대로 주 통로 양방향 동시 주문 1회. 종료 코드 0 = 통과 |

```bash
.venv/bin/python experiments/smoke_synth3.py --viewer            # 점유 존 관제: 한 대가 대기 node 에서 기다렸다 진입
.venv/bin/python experiments/smoke_synth3.py --viewer --no-zones # 조건 ①: 로봇끼리 서로 피한다
.venv/bin/python experiments/provision.py experiments/sites/synth3.json --fms http://127.0.0.1:8765 --site exp --robots AMR-01,AMR-02
```

## synth3 좌표 (world m, 맵 `maps/synth3`, 레이아웃 `tools/map_layouts/synth3.txt`)

- 서쪽 작업 구역 x 0.5~8.5, 동쪽 x 32.5~40.5, y 0.5~21.5. 통로 3개(폭 1 m, 실제 FREE 약 0.86 m, 길이 24 m): 북 y=1, 주 y=11, 남 y=21.
- 점유 존: x 8.5~32.5, 각 통로 축 ±1 m. 대기 node x 7.1 / 33.9 (통로 축 위), 출구 node 는 반대편 작업 구역에서 축에서 2 m 비켜난 곳.
  대기·출구 node 최소 거리 2.0 m (같은 자리면 양방향 교착 — 설계서 부록 A.4).
- 작업 지점 W-01~05 (3, 3·7·11·15·19), E-01~05 (38, 같은 y). 주차 P-W1~3 (1.2, 5·9·13), P-E1~3 (39.8, 같은 y). 충전기 C-W (7.9, 15.5) / C-E (33.1, 15.5), BACKWARD.
- FMS 가 zone_id 를 운행마다 새로 만든다. `provision()` 반환값 `zone_names` 로 이름(Z1 주 통로 등)에 묶는다.

## 확인 결과 (2026-11-26)

- 점유 존 관제: 두 작업 성공(92 초). AMR-01 바로 진입(`queued_s` 0), AMR-02 반대편 대기 node 에서 24.6 초 대기 후 진입. 배정 스냅샷 `reserved_ahead` 0 / 1.
- 조건 ① (`--no-zones`): 두 작업 성공(109 초). 주 통로에서 3 m 거리로 마주치자 둘 다 상대를 장애물로 보고 남쪽 통로로 동시에 우회,
  거기서 다시 마주쳐 또 우회했다. 영구 교착이 아니라 "서로 피하다 시간 낭비"(대칭 우회)다. 대수가 늘면 충돌·교착이 섞일 수 있다(추정).

## factory 맵 3종 현장 편집 (사람이 직접)

`maps/factory_cleanroom`, `factory_angled`, `factory_textile` 은 점유 존·작업 지점을 사람이 FMS 웹 UI 에서 찍는다.

```bash
.venv/bin/python experiments/edit_fms.py                   # 편집용 FMS http://127.0.0.1:8790/ (Ctrl+C 로 종료, 데이터 ../exp_fms_edit/ 에 유지)
.venv/bin/python experiments/export_site.py cleanroom      # 찍은 것을 experiments/sites/factory_cleanroom.json 으로 저장 + 규칙 검사
```

현장 이름은 `cleanroom` / `angled` / `textile`, 맵은 `floor-1`. 웹 UI 의 실시간 맵에서 구역(점유 존 + gate), 목적지, 도킹 스테이션을 추가한다 (웹 입력 각도는 도).
MQTT 는 빈 포트(18890)라 1883 의 실제 로봇과 섞이지 않는다. 로봇 없이 편집만 한다.

찍는 규칙 (`export_site.py` 가 검사한다. 오류가 있으면 고치고 다시 내보낸다):

| 대상 | 규칙 |
|---|---|
| 점유 존 | 문(1.0 / 1.8 m)이나 랙 사이 좁은 통로를 덮는 사각형. 맵당 10 개 이하 (FMS 경로 계산 고속화 전). 양방향이면 gate 2 개 |
| 대기 node | 존 경계에서 0.53 m(차체 반경) 넘게 바깥, 권장 1.0 ~ 1.5 m. 통로 축 위. 존 진입 방향을 본다 |
| 출구 node | 반대편 존 밖. **어떤 대기 node 와도 2 m 이상** (1.1 m 미만이면 양방향 교착 — 오류). 통로 축에서 비켜 둔다 |
| 작업 지점 (station) | 점유 존 밖 (존 안이면 FMS 가 목적지로 거절). 존 양쪽에 고르게 두어 주문이 존을 지나가게 한다 |
| 주차 (parking) | **로봇 대수 이상 (10 대면 10 개 이상)**. 로봇 시작 자리로 쓴다. 통로 입구를 막지 않는 곳 |
| 충전기 (도킹 스테이션) | 벽을 등지고 BACKWARD, 벽면에서 0.4 m 이상 |
| 공통 | 모든 점은 로봇 폭 팽창(0.25 m) 후 주행 가능해야 한다. 벽에 너무 붙이지 않는다 |
