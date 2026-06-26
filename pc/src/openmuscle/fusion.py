"""Madgwick AHRS orientation fusion (IMU-only: gyro + accel, no magnetometer).

Hub-side fusion for the orientation-viz workstream (overseer #0201 decision 1:
device stays ~22Hz, orientation comes from a hub-side Madgwick filter). This is
the CANONICAL, tested reference of the locked algorithm; the PC (JS) and phone
(Kotlin) gizmos port the same math so all hubs agree (board #0206).

update(q, gyro_rad_s, accel, dt, beta) -> new quaternion (w, x, y, z).

INPUTS:
- gyro in RAD/S. The device emits raw counts; converting counts -> rad/s needs the
  per-variant SCALE (TOKMAS), a firmware dependency (board #0200). Pass 0 gyro to
  run ACCEL-ONLY (the filter still levels to gravity for pitch/roll; yaw is then
  unobservable and holds). The viz uses accel-only until the scale lands.
- accel in any unit (normalized internally). The direction is what matters.

Earth frame: gravity reference is +Z (standard Madgwick). The quaternion is the
orientation of the sensor in the earth frame.
"""

import math


def estimated_gravity(q):
    """Gravity direction in the SENSOR/body frame implied by orientation q.
    After convergence this matches the normalized measured accel."""
    q0, q1, q2, q3 = q
    return (2.0 * (q1 * q3 - q0 * q2),
            2.0 * (q0 * q1 + q2 * q3),
            q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3)


def update(q, gyro_rad_s, accel, dt, beta=0.1):
    """One Madgwick IMU update. q = (w,x,y,z); returns the new unit quaternion."""
    q0, q1, q2, q3 = q
    gx, gy, gz = gyro_rad_s
    ax, ay, az = accel

    # Quaternion rate of change from the gyroscope.
    qDot0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz)
    qDot1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
    qDot2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
    qDot3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)

    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n > 1e-9:
        ax, ay, az = ax / n, ay / n, az / n
        # Gradient-descent correction step (objective: q aligns the earth +Z
        # gravity reference with the measured accel).
        _2q0, _2q1, _2q2, _2q3 = 2 * q0, 2 * q1, 2 * q2, 2 * q3
        _4q0, _4q1, _4q2 = 4 * q0, 4 * q1, 4 * q2
        _8q1, _8q2 = 8 * q1, 8 * q2
        q0q0, q1q1, q2q2, q3q3 = q0 * q0, q1 * q1, q2 * q2, q3 * q3
        s0 = _4q0 * q2q2 + _2q2 * ax + _4q0 * q1q1 - _2q1 * ay
        s1 = (_4q1 * q3q3 - _2q3 * ax + 4.0 * q0q0 * q1 - _2q0 * ay
              - _4q1 + _8q1 * q1q1 + _8q1 * q2q2 + _4q1 * az)
        s2 = (4.0 * q0q0 * q2 + _2q0 * ax + _4q2 * q3q3 - _2q3 * ay
              - _4q2 + _8q2 * q1q1 + _8q2 * q2q2 + _4q2 * az)
        s3 = 4.0 * q1q1 * q3 - _2q1 * ax + 4.0 * q2q2 * q3 - _2q2 * ay
        ns = math.sqrt(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3)
        if ns > 1e-9:
            s0, s1, s2, s3 = s0 / ns, s1 / ns, s2 / ns, s3 / ns
            qDot0 -= beta * s0
            qDot1 -= beta * s1
            qDot2 -= beta * s2
            qDot3 -= beta * s3

    q0 += qDot0 * dt
    q1 += qDot1 * dt
    q2 += qDot2 * dt
    q3 += qDot3 * dt
    nq = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
    if nq < 1e-9:
        return (1.0, 0.0, 0.0, 0.0)
    return (q0 / nq, q1 / nq, q2 / nq, q3 / nq)
