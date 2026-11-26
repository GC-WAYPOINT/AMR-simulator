"""
맵 주석 (annotations) — location / zone 계약
===========================================

`common/schemas.py` 가 FMS<->로봇의 **메시지** 계약이라면, 이 파일은 두 쪽이
공유하는 **맵에 딸린 정적 현장 데이터** 계약이다.

    location : 이름 붙은 목표 지점 (station, charger, ...). Task 는 좌표가 아니라
               이름으로 목적지를 지정하고, FMS 가 여기서 Pose 로 바꿔 Order 에 싣는다.
    zone     : 다각형 구역 (prohibit, elevator, pickup ...). prohibit 는 FMS 가 서버에서 grid 에
               구워버리므로(navi_gridmap.baked.png) 로봇은 보통 안 읽는다. 하지만
               런타임 동적 zone / 시각화를 위해 계약으로 유지한다.

이건 **맵 하나에 귀속**된다 (`annotations.json`). 시설 단위의 "Site" 개념과
다르다 — Site 는 맵 여러 개 + 로봇을 묶는 상위 개념이고 `fms_server/sites.py` 에
있다. 이 파일은 맵 1개의 주석일 뿐이다.

동기화 대상
-----------
`common/` 이 원본. 바뀌면:
    include/map_model.hpp + src/map_model.cpp (C++ 점유 존 경계 읽기)
    viewer/common/map_annotations.py (파일 복사, fms_server/tests/test_common_sync.py 가 검사)

벤더 원본 (`location_meta.json`, `zone_meta.json`)은 이 스키마와 필드명이
다르다 (pose 가 배열, theta 가 0~2pi 등). 변환은 `fms_server` 의 맵 인제스트가
담당하고, 이 파일은 **정규화된 뒤의** 모양만 정의한다.

Pydantic v2 기준.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.schemas import Point, Pose


def normalize_theta(theta: float) -> float:
    """임의 각도를 -pi ~ pi 로 접는다.

    벤더 location pose 의 theta 는 0~2pi 로 온다 (예: 4.71 = 270도). 그대로는
    `Pose.theta` 의 -3.15~3.15 검증에 걸린다. 인제스트가 이걸 거쳐 저장한다.
    """
    return math.atan2(math.sin(theta), math.cos(theta))


def rectangle_polygon(x: float, y: float, width: float, height: float,
                      rotation: float = 0) -> list[Point]:
    """회전 전 좌상단·크기로 정의한 사각형을 중심 기준으로 회전한다. 각도는 도 단위다."""
    if rotation % 360 == 0:
        return [Point(x=x,y=y),Point(x=x+width,y=y),Point(x=x+width,y=y+height),Point(x=x,y=y+height)]
    cx, cy = x + width / 2, y + height / 2
    angle = math.radians(rotation % 360)
    c, s = math.cos(angle), math.sin(angle)
    return [Point(x=cx+c*dx-s*dy, y=cy+s*dx+c*dy)
            for dx,dy in ((-width/2,-height/2),(width/2,-height/2),
                          (width/2,height/2),(-width/2,height/2))]


def rectangle_frame(polygon) -> tuple[float, float, float, float, float, float]:
    """직사각형의 중심·가로 단위축·반너비·반높이. 회전/꼭짓점 오차를 허용한다."""
    pts = list(polygon)
    if len(pts) == 5 and math.hypot(pts[0].x-pts[-1].x,pts[0].y-pts[-1].y) < 1e-8:
        pts.pop()
    if len(pts) != 4 or not all(math.isfinite(v) for p in pts for v in (p.x,p.y)):
        raise ValueError("직사각형의 꼭짓점 4개가 필요하다")
    a,b,c,d = pts
    ux,uy,vx,vy = b.x-a.x,b.y-a.y,d.x-a.x,d.y-a.y
    width,height = math.hypot(ux,uy),math.hypot(vx,vy)
    if (min(width,height) <= 1e-9 or abs(ux*vx+uy*vy) > 1e-6*width*height
            or math.hypot(c.x-(b.x+d.x-a.x),c.y-(b.y+d.y-a.y)) > 1e-6*max(width,height)):
        raise ValueError("인접한 변이 직각인 직사각형이어야 한다")
    return ((a.x+c.x)/2,(a.y+c.y)/2,ux/width,uy/width,width/2,height/2)


def rectangles_overlap(first, second) -> bool:
    """분리축 검사. 회전 사각형의 경계 접촉도 겹침으로 취급한다."""
    ax,ay,au,av,aw,ah = rectangle_frame(first)
    bx,by,bu,bv,bw,bh = rectangle_frame(second)
    for x,y in ((au,av),(-av,au),(bu,bv),(-bv,bu)):
        ra = aw*abs(au*x+av*y)+ah*abs(-av*x+au*y)
        rb = bw*abs(bu*x+bv*y)+bh*abs(-bv*x+bu*y)
        if abs((bx-ax)*x+(by-ay)*y) > ra+rb+1e-9:
            return False
    return True


# =============================================================================
# Location
# =============================================================================

class LocationType(str, Enum):
    """
    location 의 용도. 벤더 맵은 자유 문자열을 쓰므로 (`station` 등) 모르는 값은
    OTHER 로 떨어뜨린다 — 스키마 검증이 벤더 맵을 거부하면 안 된다.
    """

    STATION = "station"       # 픽업/드롭 등 일반 작업 지점
    CHARGER = "charger"       # 충전 스테이션
    PARKING = "parking"       # 대기/주차 지점
    WAYPOINT = "waypoint"     # 단순 경유점 (작업 없음)
    PICKUP = "pickup"         # 픽업스테이션 node. site location 의 zone_id 로 pickup zone 과 1:1 연결
    TRAY_RETURN = "tray_return"  # 픽업스테이션 반납 node. 배송 후 빈 트레이를 내려놓는다 (zone_id 로 연결)
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "LocationType":
        return cls.OTHER


class Location(BaseModel):
    """이름 붙은 목표 지점 하나 (정규화 완료)."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, description="맵 내 고유 ID (벤더 unique_id)")
    name: str = Field(min_length=1, description="사람이 쓰는 이름. Task 목적지 지정에 쓴다")
    type: LocationType = LocationType.OTHER
    pose: Pose = Field(description="목표 자세. theta 는 -pi~pi 로 정규화되어 있어야 한다")
    marker_id: Optional[int] = Field(default=None, description="벤더 마커 번호 (있으면)")


# =============================================================================
# Zone
# =============================================================================

class ZoneType(str, Enum):
    """
    zone 의 종류. 역시 모르는 값은 PROHIBIT 로 떨어뜨린다 — 안전한 쪽
    (막힌 것으로 취급) 이 기본값이다.
    """

    PROHIBIT = "prohibit"     # 진입 금지. FMS 가 grid 에 OCCUPIED 로 굽는다
    ELEVATOR = "elevator"     # 엘리베이터 구역. 위치 표시용, 호출/탑승 제어는 별도
    PICKUP = "pickup"         # 픽업스테이션 구역. 적재 장치 위치 표시용. 굽지 않고 동작도 없다
    OCCUPANCY = "occupancy"   # 한 대씩 통과하는 점유 존. 정적 이미지에 굽지 않는다
    SLOW = "slow"             # 감속 구역. 로봇이 구역 안에서 speed_limit 이하로 달린다 (굽지 않는다)
    ONEWAY = "oneway"         # 일방통행 (미구현, 예약)

    @classmethod
    def _missing_(cls, value: object) -> "ZoneType":
        return cls.PROHIBIT


class TrafficGate(BaseModel):
    """진입 방향별 대기·출구 node. 좌표는 존 버전에 복사해 보존한다.

    queue 는 대기 node 뒤의 대기열 자리다. queue[0] 이 대기 node 바로 뒤(2번째 자리), 마지막이 줄 끝이다.
    로봇은 줄 끝 -> 대기 node 순서로 지나가므로 진입 방향 뒤쪽으로 한 줄로 놓는다 (FMS 전용, 로봇은 모른다).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    gate_id: str = Field(min_length=1)
    wait: Pose
    exit: Pose
    queue: list[Pose] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def finite_coordinates(self):
        if not all(math.isfinite(v) for p in (self.wait, self.exit, *self.queue) for v in (p.x,p.y,p.theta)):
            raise ValueError("대기·출구·대기열 node는 유한한 좌표여야 한다")
        return self


def queue_poses(points, head) -> list[Pose]:
    """대기열 점들(대기 node 바로 뒤부터 줄 끝까지)의 자세. 각 자리는 앞 자리(마지막엔 head)를 바라본다."""
    poses, ahead = [], head
    for p in points:
        poses.append(Pose(x=p.x, y=p.y, theta=math.atan2(ahead.y - p.y, ahead.x - p.x)))
        ahead = p
    return poses


class AutoDoor(BaseModel):
    """
    자동문 설정. 점유 존(`type=occupancy`) 하나가 문 하나를 뜻한다.

    node_a / node_b 는 문 양쪽의 진입점 위치(x, y)다. A 쪽에서 들어가면 A 가 대기(진입) node, B 가 통과 완료 node 이고
    B 쪽에서 들어가면 반대다. `Zone.gates` 는 이 두 node 로 FMS 가 채운다 (점유 존 교통 제어를 그대로 쓰기 위해).
    로봇(C++)은 이 필드를 모르고 일반 점유 존으로 취급한다 — 문 신호는 FMS 가 보낸다 (`common/door_proto.py`).
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    door_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
                         description="문 제어기 MQTT 토픽 id. 현장 사이에서도 겹치면 안 된다")
    node_a: Point
    node_b: Point
    queue_a: list[Point] = Field(default_factory=list, max_length=16,
                                 description="A 쪽 대기열 (A 바로 뒤부터 줄 끝까지). A 에서 들어가는 로봇이 선다")
    queue_b: list[Point] = Field(default_factory=list, max_length=16, description="B 쪽 대기열")

    @model_validator(mode="after")
    def _check_nodes(self) -> "AutoDoor":
        if math.hypot(self.node_a.x - self.node_b.x, self.node_a.y - self.node_b.y) < 1e-6:
            raise ValueError("자동문 양쪽 node 는 서로 다른 위치여야 한다")
        return self

    def gates(self) -> list[TrafficGate]:
        """진입 방향별 gate. 자세(theta)는 진행 방향(진입점 -> 통과 완료점)으로 정한다."""
        a, b = self.node_a, self.node_b
        ab = math.atan2(b.y - a.y, b.x - a.x)
        ba = math.atan2(a.y - b.y, a.x - b.x)
        return [TrafficGate(gate_id="a2b", wait=Pose(x=a.x, y=a.y, theta=ab), exit=Pose(x=b.x, y=b.y, theta=ab),
                            queue=queue_poses(self.queue_a, a)),
                TrafficGate(gate_id="b2a", wait=Pose(x=b.x, y=b.y, theta=ba), exit=Pose(x=a.x, y=a.y, theta=ba),
                            queue=queue_poses(self.queue_b, b))]


class Zone(BaseModel):
    """다각형 구역 하나 (정규화 완료)."""

    model_config = ConfigDict(extra="forbid")

    zone_id: str = Field(min_length=1)
    name: str = ""
    type: ZoneType = ZoneType.PROHIBIT
    polygon: list[Point] = Field(min_length=3, description="꼭짓점 목록 (닫힘은 암묵적)")
    gates: list[TrafficGate] = Field(default_factory=list)
    speed_limit: Optional[float] = Field(default=None, gt=0, le=10,
                                         description="감속 구역(slow)의 최고 속도 m/s. 다른 구역에는 없다")
    door: Optional[AutoDoor] = Field(default=None, description="자동문. 점유 존에만 지정한다 (gates 는 여기서 파생)")

    @model_validator(mode="after")
    def _check_polygon(self) -> "Zone":
        if self.door is not None:
            if self.type != ZoneType.OCCUPANCY:
                raise ValueError("자동문은 점유 존(occupancy)에만 지정할 수 있다")
            self.gates = self.door.gates()
        if (self.type == ZoneType.SLOW) != (self.speed_limit is not None):
            raise ValueError("speed_limit 은 감속 구역(slow)에만, 그리고 반드시 지정해야 한다")
        if self.type == ZoneType.OCCUPANCY:
            rectangle_frame(self.polygon)
            if len({g.gate_id for g in self.gates}) != len(self.gates):
                raise ValueError("점유 존의 대기·출구 node 쌍은 고유 ID를 가져야 한다")
        elif self.gates:
            raise ValueError("대기·출구 node는 점유 존에만 지정할 수 있다")
        # 첫 점 == 끝 점으로 명시적으로 닫아 보낸 경우는 허용하되 최소 3개는 유지.
        if len(self.polygon) >= 2 and self.polygon[0] == self.polygon[-1] and len(self.polygon) < 4:
            raise ValueError("닫힌 폴리곤이면 꼭짓점이 4개 이상이어야 한다 (첫 점 반복 포함)")
        return self

    @property
    def automatic_gate(self) -> bool:
        """사각형만 저장하고, 작업마다 대기·퇴장 지점을 계산하는 점유구역."""
        return self.type == ZoneType.OCCUPANCY and not self.gates

    def contains(self, x: float, y: float) -> bool:
        """점이 폴리곤 내부인가 (ray casting). 경계는 구현 정의."""
        pts = self.polygon
        n = len(pts)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = pts[i].x, pts[i].y
            xj, yj = pts[j].x, pts[j].y
            if (yi > y) != (yj > y):
                x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < x_cross:
                    inside = not inside
            j = i
        return inside


# =============================================================================
# MapAnnotations — 맵 하나에 딸린 location/zone 전체
# =============================================================================

class MapAnnotations(BaseModel):
    """맵 하나의 정적 현장 데이터. 맵 버전 디렉터리 안에 `annotations.json` 으로 저장한다."""

    model_config = ConfigDict(extra="forbid")

    map_id: str = Field(
        min_length=1,
        description="FMS 전역 고유 맵 id (map_uid). fms_map.json 의 name 과 같은 값이다",
    )
    map_version: str = Field(min_length=1, description="어느 맵 버전 기준으로 만든 좌표인지")
    locations: list[Location] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "MapAnnotations":
        loc_ids = [loc.location_id for loc in self.locations]
        if len(set(loc_ids)) != len(loc_ids):
            raise ValueError("location_id 가 중복되었다")
        loc_names = [loc.name for loc in self.locations]
        if len(set(loc_names)) != len(loc_names):
            raise ValueError("location name 이 중복되었다 (Task 목적지 지정이 모호해진다)")
        zone_ids = [z.zone_id for z in self.zones]
        if len(set(zone_ids)) != len(zone_ids):
            raise ValueError("zone_id 가 중복되었다")
        return self

    def location_by_name(self, name: str) -> Optional[Location]:
        for loc in self.locations:
            if loc.name == name:
                return loc
        return None

    def location_by_id(self, location_id: str) -> Optional[Location]:
        for loc in self.locations:
            if loc.location_id == location_id:
                return loc
        return None

    def zone_by_id(self, zone_id: str) -> Optional[Zone]:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        return None

    def prohibit_zones(self) -> list[Zone]:
        return [z for z in self.zones if z.type == ZoneType.PROHIBIT]
