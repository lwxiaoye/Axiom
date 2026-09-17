"""R1-3：瞬时帧超过 PG NOTIFY 上限时分片到达且顺序正确。"""
from app.services.tasks import runtime_event_bus as bus


def setup_function():
    bus._frag_buf.clear()


def test_small_ephemeral_frame_is_not_sharded():
    envelopes = bus.build_ephemeral_envelopes("run-1", "hello")
    assert len(envelopes) == 1
    assembled = bus.ingest_ephemeral_notification(envelopes[0])
    assert assembled == ("run-1", "hello")


def test_oversize_reasoning_burst_arrives_complete_and_in_order():
    payload = "思考" * 4000
    envelopes = bus.build_ephemeral_envelopes("run-big", payload)
    assert len(envelopes) > 1
    assert all(len(item.encode("utf-8")) < bus.EPHEMERAL_NOTIFY_LIMIT for item in envelopes)

    seen = None
    for item in envelopes:
        seen = bus.ingest_ephemeral_notification(item)
    assert seen == ("run-big", payload)


def test_sharded_envelopes_are_ordered_by_index():
    payload = "A" * 12000
    envelopes = bus.build_ephemeral_envelopes("run-ord", payload)
    reversed_envelopes = list(reversed(envelopes))
    seen = None
    for item in reversed_envelopes:
        seen = bus.ingest_ephemeral_notification(item)
    assert seen == ("run-ord", payload)
