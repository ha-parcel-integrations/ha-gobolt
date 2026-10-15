"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`. Everything else — the
timestamp parsing, the history builder, the sort contract, the delivered
filter, the one-shot warning for unmapped statuses — is suite-wide machinery
and should be left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-gobolt/issues/new"
    "?template=unrecognised_status.yml"
)

# The keys are the source's ``label_*`` names. ``label_failed`` and
# ``label_returned_to_warehouse`` come from the carrier's own vocabulary but
# have not been seen on a live parcel; their canonical status is unambiguous,
# so they are mapped rather than left to the fallback. A name that is not in
# the carrier's vocabulary at all is never guessed — it falls back and warns
# (see :func:`map_parcel_status`).
_STATUS_MAP: dict[str, ParcelStatus] = {
    "label_created": ParcelStatus.REGISTERED,
    "label_scanned": ParcelStatus.IN_TRANSIT,
    "label_received_at_destination": ParcelStatus.IN_TRANSIT,
    "label_out_for_delivery": ParcelStatus.OUT_FOR_DELIVERY,
    "label_delivered": ParcelStatus.DELIVERED,
    "label_failed": ParcelStatus.PROBLEM,
    "label_returned_to_warehouse": ParcelStatus.RETURNING,
}

# Status names we have already warned about, so each is logged only once per
# HA session instead of on every poll. Only the name is logged, never the
# event description or location next to it.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str, reported: str = "unknown") -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised GoBolt status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as '%s'",
        NEW_ISSUE_URL,
        code,
        reported,
    )


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a status name to a canonical status, or ``None`` when unmapped.

    Never warns: whether a gap matters depends on the caller, which owns the
    one-shot warning.
    """
    if not code:
        return None
    return _STATUS_MAP.get(code)


def map_parcel_status(
    code: str | None, history_names: list[str] | None = None
) -> ParcelStatus:
    """Map the current status, else the newest mappable history event.

    ``history_names`` is the ``transit_steps`` names, newest first. An absent
    status reports ``unknown`` silently. An unmapped one warns once even when
    the history rescues it — those are exactly the names still missing.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = map_event_status(code)
    if mapped is not None:
        return mapped
    for name in history_names or []:
        fallback = map_event_status(name)
        if fallback is not None:
            _warn_unmapped_status(code, fallback.value)
            return fallback
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from ``transit_steps``.

    The source delivers the steps newest first; they are reversed here and
    then stably sorted by timestamp, so equal timestamps keep their reversed
    order. Each entry is ``{timestamp, status, raw_status}`` and ``raw_status``
    is the step's status name: the free-text description and location can
    carry a place name, so they stay under ``raw``. Capped to the most recent
    ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    unmapped: set[str] = set()
    for event in reversed(events or []):
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("created_at"))
        if not timestamp:
            continue
        name = event.get("name")
        status = map_event_status(name)
        if name and status is None:
            unmapped.add(name)
        entry = {"timestamp": timestamp, "status": status, "raw_status": name or None}
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    for name in sorted(unmapped):
        _warn_unmapped_status(name)
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _step_names(steps: Any) -> list[str]:
    """Return the names of the well-formed ``transit_steps``, newest first."""
    if not isinstance(steps, list):
        return []
    return [
        step["name"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    ]


def _delivery_time(raw: dict) -> str | None:
    """Return the delivery instant: the top-level stamp, else the delivery step's."""
    stamp = to_iso_timestamp(raw.get("label_delivered_at"))
    if stamp:
        return stamp
    for step in raw.get("transit_steps") or []:
        if isinstance(step, dict) and step.get("name") == "label_delivered":
            return to_iso_timestamp(step.get("created_at"))
    return None


def normalize_parcel(
    raw: dict, *, include_history: bool = False, tracking_code: str | None = None
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    ``tracking_code`` is the code the user configured and wins over the
    response's own echo as ``barcode``. The basic tracker carries no sender,
    receiver, pickup point, weight, dimensions or deep link, so those are
    literal ``None``; ``planned_to`` is always ``None`` because the ETA is a
    day-level estimate expressed as an instant.
    """
    barcode = tracking_code or raw.get("tracking_number")
    label_status = raw.get("label_status")
    status = map_parcel_status(label_status, _step_names(raw.get("transit_steps")))
    delivered = status is ParcelStatus.DELIVERED

    return {
        "carrier": "GoBolt",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": label_status,
        "delivered": delivered,
        "delivered_at": _delivery_time(raw) if delivered else None,
        "planned_from": (
            None if delivered else to_iso_timestamp(raw.get("estimated_delivery_date"))
        ),
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": None,
        "weight": None,
        "dimensions": None,
        "history": (
            build_history(raw.get("transit_steps")) if include_history else None
        ),
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
