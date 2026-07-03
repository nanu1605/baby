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
