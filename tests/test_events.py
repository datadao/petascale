"""Tests for event models and presence state machine."""

import pytest

from petascale.events import (
    DetectedEvent,
    EventType,
    PresenceState,
    PresenceStateMachine,
    SensorReading,
    SensorType,
)


def make_reading(value: float, ts: int = 0, sensor_id: str = "sensor.test") -> SensorReading:
    return SensorReading(
        sensor_id=sensor_id,
        sensor_type=SensorType.WEIGHT,
        value=value,
        timestamp=ts,
    )


def feed(sm: PresenceStateMachine, readings: list[tuple[int, float]]) -> list[DetectedEvent]:
    """Feed (timestamp_ms, value) pairs and collect all events."""
    events = []
    for ts, val in readings:
        evt = sm.process_reading(make_reading(val, ts))
        if evt:
            events.append(evt)
    return events


class TestPresenceStateMachine:
    def test_initial_state_is_idle(self):
        sm = PresenceStateMachine("sensor.test")
        assert sm.state == PresenceState.IDLE

    def test_no_event_below_threshold(self):
        sm = PresenceStateMachine("sensor.test")
        events = feed(sm, [(i * 1000, 50.0) for i in range(10)])
        assert events == []
        assert sm.state == PresenceState.IDLE

    def test_full_session(self):
        """IDLE → CAT_ENTERING → CAT_PRESENT event → CAT_LEAVING → CAT_LEFT event."""
        # window=2s, min_samples=2 keeps timing simple
        sm = PresenceStateMachine("sensor.test", stability_window_s=2, min_samples=2)

        # t=0,1s: stable above threshold → IDLE→CAT_ENTERING, then settled → CAT_PRESENT
        readings = [(0, 410.0), (1000, 410.0), (2000, 410.0)]
        # t=3,4,5s: stable below exit threshold → CAT_LEAVING → CAT_LEFT
        # Need 2 readings in CAT_PRESENT state below threshold to enter CAT_LEAVING,
        # then 2 more while in CAT_LEAVING (old high readings must have scrolled out).
        readings += [(3000, 20.0), (4000, 20.0), (5000, 20.0), (6000, 20.0)]

        events = feed(sm, readings)
        event_types = [e.event_type for e in events]

        assert EventType.CAT_PRESENT in event_types
        assert EventType.CAT_LEFT in event_types

        left = next(e for e in events if e.event_type == EventType.CAT_LEFT)
        assert left.duration_ms is not None and left.duration_ms > 0

    def test_false_alarm_drops_back_to_idle(self):
        """Weight spikes then drops; once the high readings scroll out, state returns to IDLE."""
        sm = PresenceStateMachine("sensor.test", stability_window_s=2, min_samples=2)

        # Two readings above threshold → CAT_ENTERING at t=1s
        readings = [(0, 400.0), (1000, 400.0)]
        # Low readings; high readings scroll out of window after 2s gap
        # At t=3000 cutoff=1000, so (1000,400) still present → not yet IDLE
        # At t=4000 cutoff=2000, readings=[(3000,10),(4000,10)] → all_below=True → IDLE
        readings += [(3000, 10.0), (4000, 10.0)]

        events = feed(sm, readings)
        assert all(e.event_type != EventType.CAT_PRESENT for e in events)
        assert sm.state == PresenceState.IDLE

    def test_weight_returns_during_leaving(self):
        """If weight returns while CAT_LEAVING, transition back to CAT_PRESENT."""
        sm = PresenceStateMachine("sensor.test", stability_window_s=2, min_samples=2)

        # Settle into CAT_PRESENT: t=0,1,2s at 410g
        feed(sm, [(0, 410.0), (1000, 410.0), (2000, 410.0)])
        assert sm.state == PresenceState.CAT_PRESENT

        # Low readings with enough gap so high readings scroll out of window
        # window=2s; cutoff at t=4000 is 2000 → (2000,410) still in → need t=5000
        sm.process_reading(make_reading(10.0, 3000))
        sm.process_reading(make_reading(10.0, 4000))
        sm.process_reading(make_reading(10.0, 5000))  # cutoff=3000, only low readings → CAT_LEAVING
        assert sm.state == PresenceState.CAT_LEAVING

        # Weight returns — need enough high readings to push low ones out of the window
        sm.process_reading(make_reading(410.0, 6000))
        sm.process_reading(make_reading(410.0, 7000))
        sm.process_reading(make_reading(410.0, 8000))  # cutoff=6000, only high readings now
        assert sm.state == PresenceState.CAT_PRESENT

    def test_non_weight_reading_ignored(self):
        sm = PresenceStateMachine("sensor.test")
        motion = SensorReading(
            sensor_id="sensor.test",
            sensor_type=SensorType.MOTION,
            value=1.0,
            timestamp=0,
        )
        result = sm.process_reading(motion)
        assert result is None
        assert sm.state == PresenceState.IDLE
