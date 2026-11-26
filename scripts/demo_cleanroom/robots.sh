#!/usr/bin/env bash
# cleanroom 시연용 로봇 5대 실행. Ctrl+C 로 5대 모두 종료.
# 출발 위치 = FMS cleanroom 사이트의 충전소-A~E (작업 후 충전소로 돌아오는 시연 흐름과 맞춤).
# 맵은 FMS 가 SET_MAP 으로 내려준다. 5대가 같은 map_cache 에 동시에 풀면 충돌해서 1초 간격으로 띄운다.
# 사용: scripts/demo_cleanroom/robots.sh   (AMR-simulator 루트 기준, 어디서 실행해도 됨)
set -o pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

BIN=./build/bin/robot_client
LOG_DIR=./logs/demo_cleanroom
mkdir -p "$LOG_DIR"

#        id       x        y       theta
ROBOTS=("AMR-101 91.279 102.4 1.5708"
        "AMR-102 93.279 102.4 1.5708"
        "AMR-103 95.279 102.4 1.5708"
        "AMR-104 97.279 102.4 1.5708"
        "AMR-105 99.279 102.4 1.5708")

PIDS=()
cleanup() {
    echo; echo "로봇 종료 중..."
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    wait
    exit 0
}
trap cleanup INT TERM

for row in "${ROBOTS[@]}"; do
    read -r id x y th <<<"$row"
    "$BIN" --id "$id" --x "$x" --y "$y" --theta "$th" --map-cache map_cache \
        >"$LOG_DIR/$id.log" 2>&1 &
    PIDS+=($!)
    echo "$id 시작 (pid $!) @ ($x, $y)  로그: $LOG_DIR/$id.log"
    sleep 1
done

echo "Ctrl+C 로 전부 종료. 로그 합쳐 보기:"
tail -n 0 -F "$LOG_DIR"/AMR-10*.log &
PIDS+=($!)
wait
