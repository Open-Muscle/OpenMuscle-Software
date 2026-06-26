"""Tests for the Madgwick AHRS fusion (openmuscle.fusion).

Verifies the locked algorithm: the filter levels to gravity (estimated gravity in
the body frame converges to the measured accel direction, the observable 2 DOF),
keeps the quaternion normalized, and integrates pure gyro motion correctly.
"""

import math

from openmuscle.fusion import update, estimated_gravity


def _converge(accel, gyro=(0.0, 0.0, 0.0), q=(1.0, 0.0, 0.0, 0.0),
              n=6000, dt=0.02, beta=0.2):
    for _ in range(n):
        q = update(q, gyro, accel, dt, beta)
    return q


def _close(a, b, tol=0.03):
    return all(abs(a[i] - b[i]) < tol for i in range(len(a)))


def test_quaternion_stays_normalized():
    q = (1.0, 0.0, 0.0, 0.0)
    for _ in range(200):
        q = update(q, (0.1, -0.2, 0.05), (0.1, 0.2, 9.8), 0.02)
    assert abs(math.sqrt(sum(c * c for c in q)) - 1.0) < 1e-6


def test_levels_to_gravity_z():
    g = estimated_gravity(_converge((0.0, 0.0, 1.0)))
    assert _close(g, (0.0, 0.0, 1.0))


def test_levels_to_gravity_x():
    # Sensor tilted so gravity reads along +X; filter must find that orientation.
    g = estimated_gravity(_converge((1.0, 0.0, 0.0)))
    assert _close(g, (1.0, 0.0, 0.0))


def test_levels_to_gravity_y():
    g = estimated_gravity(_converge((0.0, 1.0, 0.0)))
    assert _close(g, (0.0, 1.0, 0.0))


def test_converges_from_wrong_start():
    # Accel feedback pulls the estimate to the measured gravity from a bad start.
    g = estimated_gravity(_converge((0.0, 0.0, 1.0), q=(0.5, 0.5, 0.5, 0.5)))
    assert _close(g, (0.0, 0.0, 1.0))


def test_accel_magnitude_invariant():
    # Direction is what matters; scaling the accel must not change the result.
    g1 = estimated_gravity(_converge((0.6, 0.0, 0.8)))
    g2 = estimated_gravity(_converge((600.0, 0.0, 800.0)))
    assert _close(g1, g2, tol=0.02)


def test_gyro_only_integration():
    # accel=0 -> the accel branch is skipped -> pure gyro integration.
    # Rotate pi/2 about Z over 1 s: q -> (cos(pi/4), 0, 0, sin(pi/4)).
    q = (1.0, 0.0, 0.0, 0.0)
    omega = math.pi / 2.0
    for _ in range(1000):
        q = update(q, (0.0, 0.0, omega), (0.0, 0.0, 0.0), 0.001)
    assert abs(q[0] - math.cos(math.pi / 4)) < 0.02
    assert abs(q[3] - math.sin(math.pi / 4)) < 0.02
