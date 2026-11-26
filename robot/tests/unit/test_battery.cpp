// 배터리 소모 모델 (battery.hpp): 기본값은 예전 공식과 같고, 적재·가감속·개체 편차 항이 더해진다.
#include <cmath>

#include "battery.hpp"
#include "check.hpp"

using namespace fms;

namespace {
bool near(double a, double b, double eps = 1e-9) { return std::abs(a - b) <= eps; }
}  // namespace

TEST(battery_default_matches_legacy_formula) {
    BatterySim b(80.0, 0.01, 0.05, 1.0, 1.2);
    CHECK(near(b.drain_rate(0.0), 0.01));
    CHECK(near(b.drain_rate(0.6), 0.01 + 0.05 * 0.5));
    CHECK(near(b.drain_rate(2.0), 0.06));            // 최대 속도 비율은 1 로 자른다
    b.step(1.0, 0.6);                                 // 첫 step 은 가감속 기준이 없다
    CHECK(near(b.charge, 80.0 - 0.035));
    b.step(1.0, 0.0);                                 // 가감속 항이 꺼져 있으면 속도 변화는 무관
    CHECK(near(b.charge, 80.0 - 0.035 - 0.01));
}

TEST(battery_load_factor_scales_motion_not_idle) {
    BatterySim b(80.0, 0.01, 0.05, 1.0, 1.0);
    b.load_factor = 0.3;
    b.loads = 0;
    const double empty = b.drain_rate(1.0);
    b.loads = 2;
    CHECK(near(b.drain_rate(1.0), 0.01 + 0.05 * 1.6));
    CHECK(b.drain_rate(1.0) > empty);
    CHECK(near(b.drain_rate(0.0), 0.01));             // 서 있으면 적재와 무관
}

TEST(battery_accel_term_uses_velocity_change) {
    BatterySim b(80.0, 0.01, 0.0, 1.0, 1.0);
    b.max_acceleration = 0.5;
    b.accel_drain = 0.04;
    CHECK(near(b.drain_rate(0.0, 0.25), 0.01 + 0.04 * 0.5));
    CHECK(near(b.drain_rate(0.0, 9.0), 0.05));        // 비율 1 로 자름
    b.step(0.1, 0.0);
    b.step(0.1, 0.025);                               // a = 0.25 m/s²
    CHECK(near(b.charge, 80.0 - 0.01 * 0.1 - (0.01 + 0.02) * 0.1, 1e-9));
}

TEST(battery_efficiency_and_charging) {
    BatterySim b(50.0, 0.01, 0.05, 1.0, 1.0);
    b.efficiency = 1.2;
    CHECK(near(b.drain_rate(1.0), 1.2 * 0.06));
    b.charging = true;
    b.step(2.0, 0.0);
    CHECK(near(b.charge, 52.0));
    CHECK(!b.estimated_runtime().has_value());
}
