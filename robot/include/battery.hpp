// 배터리 시뮬레이션 (robot_client/sim/battery.py 포팅)
#pragma once

#include <algorithm>
#include <cmath>
#include <optional>

#include "schemas.hpp"

namespace fms {

class BatterySim {
public:
    double charge;
    double idle_drain, move_drain, charge_rate, max_velocity;
    bool charging = false;

    // 현실화 항 (기본값이면 예전 공식 idle + move·|v|/vmax 과 같다). 실험·학습 데이터에서 배터리 예측이
    // 공식 맞추기가 되지 않게 적재·가감속·개체 편차를 넣는다 (--battery-load-factor 등, main.cpp).
    double max_acceleration = 0.0;   // > 0 이면 가감속 항을 쓴다 (|a| / max_acceleration 비율, 1 로 자름)
    double accel_drain = 0.0;        // 최대 가감속일 때 더해지는 소모 (%/s)
    double load_factor = 0.0;        // 적재물 1개당 주행·가감속 소모 증가 비율 (0.3 = +30 %)
    double efficiency = 1.0;         // 로봇 개체 편차: 모든 소모에 곱한다 (seed 로 정한다)
    int loads = 0;                   // 지금 싣고 있는 적재물 수 (executor 가 PICK/DROP 완료 때 맞춘다)

    explicit BatterySim(double charge_ = 100.0, double idle_drain_ = 0.01, double move_drain_ = 0.05,
                         double charge_rate_ = 1.0, double max_velocity_ = 1.2)
        : charge(std::max(0.0, std::min(100.0, charge_))), idle_drain(idle_drain_),
          move_drain(move_drain_), charge_rate(charge_rate_), max_velocity(max_velocity_) {}

    void step(double dt, double velocity = 0.0) {
        // 가감속은 연속한 step 의 속도 차이로 본다. 정지 분기(속도 0 으로 부름)로 바뀌는 첫 tick 은
        // 그 차이가 커서 제동 소모 한 번으로 계산된다 (비율은 1 로 자른다).
        const double accel = (have_prev_ && dt > 0) ? std::abs(velocity - prev_velocity_) / dt : 0.0;
        prev_velocity_ = velocity;
        have_prev_ = true;
        if (charging) {
            charge = std::min(100.0, charge + charge_rate * dt);
            last_drain_ = 0.0;
            return;
        }
        last_drain_ = drain_rate(velocity, accel);
        charge = std::max(0.0, charge - last_drain_ * dt);
    }

    // 소모율 (%/s). 단위 테스트와 step 이 같이 쓴다.
    double drain_rate(double velocity, double accel = 0.0) const {
        const double speed_ratio = max_velocity > 0 ? std::min(1.0, std::abs(velocity) / max_velocity) : 0.0;
        const double accel_ratio = max_acceleration > 0 ? std::min(1.0, accel / max_acceleration) : 0.0;
        const double load_mult = 1.0 + load_factor * std::max(0, loads);
        return efficiency * (idle_drain + load_mult * (move_drain * speed_ratio + accel_drain * accel_ratio));
    }

    std::optional<double> estimated_runtime() const {
        if (charging || last_drain_ <= 0) return std::nullopt;
        return charge / last_drain_;
    }

    BatteryInfo info() const {
        BatteryInfo b;
        b.charge = std::round(charge * 100) / 100.0;
        b.charging = charging;
        b.estimated_runtime = estimated_runtime();
        return b;
    }

private:
    double last_drain_ = idle_drain;
    double prev_velocity_ = 0.0;
    bool have_prev_ = false;
};

}  // namespace fms
