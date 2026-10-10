"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gobolt.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.gobolt.parcels import (
    _unmapped_statuses_logged,
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import active_sample, delivered_sample, step

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_warning_state():
    """Each test starts with no status already warned about."""
    _unmapped_statuses_logged.clear()


@pytest.mark.parametrize(
    "code,expected",
    [
        ("label_created", ParcelStatus.REGISTERED),
        ("label_scanned", ParcelStatus.IN_TRANSIT),
        ("label_received_at_destination", ParcelStatus.IN_TRANSIT),
        ("label_out_for_delivery", ParcelStatus.OUT_FOR_DELIVERY),
        ("label_delivered", ParcelStatus.DELIVERED),
        ("label_failed", ParcelStatus.PROBLEM),
        ("label_returned_to_warehouse", ParcelStatus.RETURNING),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_map_parcel_status_missing_is_unknown_and_silent(caplog):
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN
    assert "Unrecognised" not in caplog.text


def test_map_parcel_status_unmapped_is_unknown_and_warns_once(caplog):
    assert map_parcel_status("label_teleported") == ParcelStatus.UNKNOWN
    assert map_parcel_status("label_teleported") == ParcelStatus.UNKNOWN
    assert caplog.text.count("label_teleported") == 1
    assert "issues/new" in caplog.text


def test_unmapped_status_falls_back_to_newest_mappable_history_event(caplog):
    """Newest first: the first mappable name wins, unmapped ones are skipped."""
    status = map_parcel_status(
        "label_teleported",
        ["label_unheard_of", "label_received_at_destination", "label_created"],
    )
    assert status == ParcelStatus.IN_TRANSIT
    # the rescue still warns: those are exactly the names still missing
    assert caplog.text.count("label_teleported") == 1
    assert "in_transit" in caplog.text


def test_map_event_status_missing_and_unmapped_are_none_and_silent(caplog):
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("label_something_new") is None
    assert map_event_status("label_delivered") == ParcelStatus.DELIVERED
    assert "Unrecognised" not in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_reverses_newest_first_steps():
    history = build_history(delivered_sample()["transit_steps"])
    assert len(history) == 5
    assert [entry["raw_status"] for entry in history] == [
        "label_created",
        "label_scanned",
        "label_received_at_destination",
        "label_out_for_delivery",
        "label_delivered",
    ]
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    assert history[0]["timestamp"] == "2026-04-27T22:03:58.864-04:00"


def test_build_history_carries_no_description_or_location():
    """Both can name a place; they stay under ``raw`` only."""
    for entry in build_history(delivered_sample()["transit_steps"]):
        assert set(entry) == {"timestamp", "status", "raw_status"}
        assert "Cityville" not in str(entry)


def test_build_history_keeps_the_newest_events_when_capped():
    steps = [
        step("label_scanned", f"2026-04-{day:02d}T10:00:00.000-04:00", "moved", None)
        for day in range(25, 0, -1)
    ]
    history = build_history(steps, max_events=20)
    assert len(history) == 20
    assert history[-1]["timestamp"].startswith("2026-04-25")
    assert history[0]["timestamp"].startswith("2026-04-06")


def test_build_history_default_cap_is_twenty():
    steps = [
        step("label_scanned", f"2026-04-{day:02d}T10:00:00.000-04:00", "x", None)
        for day in range(28, 0, -1)
    ]
    assert len(build_history(steps)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"name": "label_scanned"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            step("label_scanned", "not-a-date", "odd"),
            step("label_created", "2026-04-24T10:00:00.000-04:00", "fine"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == [
        "label_created",
        "label_scanned",
    ]


def test_build_history_unmapped_name_keeps_null_status_and_warns_once(caplog):
    steps = [
        step("label_weird", "2026-04-25T10:00:00.000-04:00", "x"),
        step("label_weird", "2026-04-24T10:00:00.000-04:00", "x"),
        step(None, "2026-04-23T10:00:00.000-04:00", "x"),
    ]
    history = build_history(steps)
    assert [entry["status"] for entry in history] == [None, None, None]
    assert history[0]["raw_status"] is None
    assert caplog.text.count("label_weird") == 1


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample;
    everything not declared (or only pending) is a literal ``None``."""
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    assert CAPABILITIES == {"history", "delivery_window"}
    assert with_history["history"] is not None
    assert active["planned_from"] is not None
    assert not PENDING_CAPABILITIES
    for parcel in (delivered, active):
        for key in ("sender", "receiver", "pickup_point", "url", "weight", "dimensions"):
            assert parcel[key] is None, key
        assert parcel["planned_to"] is None
        assert parcel["pickup"] is False


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "GoBolt"
    assert parcel["barcode"] == "EXAMPLE123456"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "label_delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T09:12:42.373-04:00"
    # A delivered parcel drops its ETA — it is meaningless once it has arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_delivered_without_top_level_stamp_uses_the_delivery_step():
    raw = delivered_sample()
    del raw["label_delivered_at"]
    assert normalize_parcel(raw)["delivered_at"] == "2026-04-29T09:12:42.000-04:00"


def test_delivered_without_any_timestamp_has_none():
    raw = delivered_sample()
    del raw["label_delivered_at"]
    raw["transit_steps"] = ["junk", {"name": "label_scanned"}]
    parcel = normalize_parcel(raw)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 5
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_a_point_eta_only():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["planned_from"] == "2026-04-29T12:00:00.000-04:00"
    assert parcel["planned_to"] is None


def test_normalize_without_optional_fields():
    """Only a delivered parcel has been seen, so both fields are optional."""
    raw = active_sample()
    del raw["estimated_delivery_date"]
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] is None
    assert parcel["delivered_at"] is None


def test_unmapped_status_uses_history_fallback_and_never_marks_delivered_by_label():
    raw = active_sample()
    raw["label_status"] = "label_on_a_boat"
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == "label_on_a_boat"


def test_unmapped_status_without_history_is_unknown(caplog):
    raw = active_sample()
    raw["label_status"] = "label_on_a_boat"
    raw["transit_steps"] = None
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert caplog.text.count("label_on_a_boat") == 1
    # the warning names the status only, never an event's place or text
    assert "Cityville" not in caplog.text


def test_normalize_pending_placeholder():
    """A tracked-but-unknown code still yields a full parcel dict."""
    parcel = normalize_parcel({"tracking_number": "EXAMPLE000001"})
    assert parcel["barcode"] == "EXAMPLE000001"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None


def test_configured_code_wins_as_barcode():
    parcel = normalize_parcel(active_sample("echoed"), tracking_code="CONFIGURED")
    assert parcel["barcode"] == "CONFIGURED"


def test_normalize_keeps_the_complete_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw, include_history=True)["raw"] is raw
    assert "transit_flow" in raw and "label_description" in raw


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
