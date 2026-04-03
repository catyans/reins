"""Tests for circuit breaker."""

from datetime import timedelta

from reins.budget.circuit_breaker import CircuitBreaker


def test_allows_normal_calls():
    cb = CircuitBreaker(max_calls_per_minute=5)
    for _ in range(5):
        assert not cb.check("agent1")


def test_trips_on_excess():
    cb = CircuitBreaker(max_calls_per_minute=3)
    for _ in range(3):
        cb.check("agent1")
    assert cb.check("agent1")  # 4th call trips it


def test_reset():
    cb = CircuitBreaker(max_calls_per_minute=2)
    cb.check("agent1")
    cb.check("agent1")
    assert cb.check("agent1")  # tripped
    cb.reset("agent1")
    assert not cb.check("agent1")  # reset


def test_independent_agents():
    cb = CircuitBreaker(max_calls_per_minute=2)
    cb.check("agent1")
    cb.check("agent1")
    assert cb.check("agent1")  # agent1 tripped
    assert not cb.check("agent2")  # agent2 fine
