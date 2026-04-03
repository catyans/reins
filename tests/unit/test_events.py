"""Tests for event bus."""

from reins.core.events import EventBus


def test_emit_and_receive():
    bus = EventBus()
    received = []

    bus.on("test.event", lambda value=None: received.append(value))
    bus.emit("test.event", value=42)

    assert received == [42]


def test_multiple_listeners():
    bus = EventBus()
    results = []

    bus.on("e", lambda: results.append("a"))
    bus.on("e", lambda: results.append("b"))
    bus.emit("e")

    assert results == ["a", "b"]


def test_no_listeners():
    bus = EventBus()
    # Should not raise
    bus.emit("nonexistent.event", data="foo")


def test_listener_error_does_not_propagate():
    bus = EventBus()
    results = []

    bus.on("e", lambda: (_ for _ in ()).throw(ValueError("boom")))
    bus.on("e", lambda: results.append("ok"))

    bus.emit("e")
    # Second listener should still fire (first raises but is caught)
    # Note: the lambda raises in a generator, which doesn't execute immediately
    # Let's use a proper function
    results.clear()

    def bad():
        raise ValueError("boom")

    bus2 = EventBus()
    bus2.on("e", bad)
    bus2.on("e", lambda: results.append("ok"))
    bus2.emit("e")
    assert results == ["ok"]


def test_off():
    bus = EventBus()
    results = []

    def handler():
        results.append(1)

    bus.on("e", handler)
    bus.emit("e")
    assert results == [1]

    bus.off("e", handler)
    bus.emit("e")
    assert results == [1]  # No second append


def test_clear():
    bus = EventBus()
    bus.on("e", lambda: None)
    bus.clear()
    # Should have no listeners
    assert len(bus._listeners) == 0
