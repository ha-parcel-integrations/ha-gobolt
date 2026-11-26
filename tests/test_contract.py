"""The parcel contract's checks, run over every normalised sample."""

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.gobolt import parcels
from custom_components.gobolt.const import ParcelStatus
from custom_components.gobolt.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import active_sample, delivered_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _with_status(label: str) -> dict:
    sample = active_sample()
    sample["label_status"] = label
    return sample


def _with_eta(value) -> dict:
    sample = active_sample()
    sample["estimated_delivery_date"] = value
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "returning": lambda: _with_status("label_returned_to_warehouse"),
    "problem": lambda: _with_status("label_failed"),
    "no_eta": lambda: _with_eta(None),
    "bare": lambda: {"tracking_number": "EXAMPLE000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_a_delivered_parcel_has_its_delivery_time():
    assert normalize_parcel(delivered_sample())["delivered_at"] is not None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done(parcel):
    if parcel["status"] in (ParcelStatus.DELIVERED, ParcelStatus.RETURNING):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_a_returning_parcel_keeps_no_window_although_the_payload_has_one():
    raw = _with_status("label_returned_to_warehouse")
    assert raw["estimated_delivery_date"]
    parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_no_pickup_point_is_seen_on_the_samples(parcel):
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_pickup_follows_a_pickup_point_status_or_history(monkeypatch):
    sample = active_sample()
    assert normalize_parcel(sample)["pickup"] is False
    monkeypatch.setattr(
        parcels,
        "map_event_status",
        lambda name: ParcelStatus.AT_PICKUP_POINT if name else None,
    )
    assert normalize_parcel(sample)["pickup"] is True


def test_an_eta_covers_the_whole_day_in_its_own_offset():
    parcel = normalize_parcel(_with_eta("2026-04-29T12:00:00.000-04:00"))
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date() == datetime(2026, 4, 29).date()
    assert (
        start.utcoffset()
        == end.utcoffset()
        == datetime.fromisoformat("2026-04-29T12:00:00-04:00").utcoffset()
    )


def test_an_eta_without_an_offset_still_covers_one_day():
    parcel = normalize_parcel(_with_eta("2026-04-29T12:00:00"))
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, end.hour, end.second) == (0, 23, 59)
    assert start.date() == end.date()


def test_an_epoch_eta_is_dated_in_utc():
    parcel = normalize_parcel(_with_eta(4102488000000))  # 2100-01-01 12:00 UTC
    assert parcel["planned_from"].startswith("2100-01-01T00:00:00")
    assert parcel["planned_to"].startswith("2100-01-01T23:59:59")


def test_an_unparseable_eta_gives_no_window():
    parcel = normalize_parcel(_with_eta("soon"))
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_planned_window_shapes():
    zone = timezone(timedelta(hours=-4))
    window = planned_window(
        "2099-01-01T10:00:00-04:00", "2099-01-01T12:00:00-04:00", zone=zone
    )
    assert window == ("2099-01-01T10:00:00-04:00", "2099-01-01T12:00:00-04:00")
    assert planned_window("2099-01-02", "2099-01-01", zone=zone) == (
        "2099-01-01T00:00:00-04:00",
        "2099-01-02T23:59:59-04:00",
    )
    assert planned_window(date(2099, 1, 1), None, zone=zone)[1] == (
        "2099-01-01T23:59:59-04:00"
    )
    assert planned_window(None, "2099-01-01T10:00:00", zone=zone)[0] == (
        "2099-01-01T10:00:00-04:00"
    )
    assert planned_window("2099-01-01T23:59:59-04:00", None, zone=zone)[1] is None
    assert planned_window(None, None, zone=zone) == (None, None)
    assert planned_window(5, None, zone=zone) == (None, None)
    assert planned_window("garbage", None, zone=zone) == (None, None)


def test_is_pickup_is_sticky():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT) is True
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(None, ParcelStatus.DELIVERED, [None]) is False
