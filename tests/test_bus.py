"""EventBus contract: fan-out, drop-oldest, unsubscribe, ordering."""

from __future__ import annotations

from core.bus import EventBus


async def test_fan_out_to_all_subscribers():
    bus = EventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish("status", "cli", text="hello")
    e1, e2 = q1.get_nowait(), q2.get_nowait()
    assert e1.kind == e2.kind == "status"
    assert e1.payload == {"text": "hello"}
    assert e1.channel == "cli"
    assert e1.ts  # stamped


async def test_drop_oldest_on_full_queue():
    bus = EventBus(maxsize=2)
    q = bus.subscribe()
    for i in range(4):
        bus.publish("token", "ui", text=str(i))
    # Queue holds the newest two; oldest were dropped.
    assert [q.get_nowait().payload["text"] for _ in range(2)] == ["2", "3"]
    assert q.empty()


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("status", "cli", text="x")
    assert q.empty()
    bus.unsubscribe(q)  # double-unsubscribe is a no-op


async def test_seq_monotonic_across_kinds():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("turn_start", "ui", conversation_id=1)
    bus.publish("token", "ui", text="a")
    bus.publish("turn_end", "ui", reply="a", status="ok")
    seqs = [q.get_nowait().seq for _ in range(3)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3


async def test_slow_subscriber_does_not_block_publish():
    bus = EventBus(maxsize=1)
    bus.subscribe()  # never drained
    for i in range(100):  # must not raise or block
        bus.publish("token", "ui", text=str(i))


# --- a payload is caller data, and callers own the whole key space -----------
# Found by pulling the network on a clean VM mid-provision. `classify_error`
# returns {kind, message, retryable} and the setup route splats it straight into
# publish(), so the payload's `kind` bound to publish's own parameter and the
# call raised `TypeError: got multiple values for argument 'kind'`. The code
# reporting a failed download was the code that failed: the wizard showed that
# TypeError instead of the retryable message sitting right there. No test caught
# it because every provisioning test mocks downloads that succeed, so a
# classified error had never actually been published.


async def test_a_payload_key_named_kind_does_not_collide():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("setup_progress", "setup", kind="no_network", message="offline", retryable=True)
    e = q.get_nowait()
    assert e.kind == "setup_progress", "the event's own kind was overwritten by the payload"
    assert e.payload["kind"] == "no_network", "the caller's kind was swallowed"
    assert e.payload["retryable"] is True


async def test_a_payload_key_named_channel_does_not_collide():
    """Same hazard, same fix -- `channel` is the other positional-only name."""
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("status", "setup", channel="telegram", text="hi")
    e = q.get_nowait()
    assert e.channel == "setup"
    assert e.payload["channel"] == "telegram"


async def test_the_real_provisioning_error_event_publishes():
    """The exact shape that broke, built from the real classifier rather than a
    hand-written dict -- so a future field named after a parameter fails here."""
    from core.provision import _event, classify_error

    ev = _event("kokoro", "error", status="error", **classify_error("getaddrinfo failed"))
    assert "kind" in ev, "classify_error stopped returning a kind; this guard is now vacuous"

    bus = EventBus()
    q = bus.subscribe()
    bus.publish("setup_progress", "setup", **ev)  # used to raise TypeError
    e = q.get_nowait()
    assert e.kind == "setup_progress"
    assert e.payload["kind"] == "no_network"
    assert e.payload["dep"] == "kokoro"
    assert e.payload["retryable"] is True
