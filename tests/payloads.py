"""Sample GoBolt API payloads shared by the test modules.

The shape follows a confirmed delivered-parcel response; the code and every
place name are placeholders. Keep them in one module rather than inline in
each test — when the payload shape turns out to be different from what we
assumed, there is then exactly one place to fix.
"""
from __future__ import annotations

ACTIVE_CODE = "EXAMPLE999999"
DELIVERED_CODE = "EXAMPLE123456"

TRANSIT_FLOW = [
    {"name": "label_created", "description": "Shipment Created", "sequence": 1},
    {"name": "label_scanned", "description": "Processing Shipment", "sequence": 2},
    {"name": "label_out_for_delivery", "description": "Out for Delivery", "sequence": 3},
    {"name": "label_delivered", "description": "Delivered", "sequence": 4},
]


def step(name: str, created_at, description: str, location="Cityville, ON, CA") -> dict:
    """One entry of ``transit_steps``."""
    return {
        "name": name,
        "description": description,
        "location": location,
        "created_at": created_at,
        "updated_at": created_at,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel; ``transit_steps`` arrive newest first."""
    return {
        "tracking_number": code,
        "label_status": "label_delivered",
        "label_delivered_at": "2026-04-29T09:12:42.373-04:00",
        "label_description": "Package delivered",
        "active_transit_sequence": 4,
        "transit_flow": TRANSIT_FLOW,
        "transit_steps": [
            step("label_delivered", "2026-04-29T09:12:42.000-04:00", "Package delivered"),
            step(
                "label_out_for_delivery",
                "2026-04-29T04:46:00.000-04:00",
                "Package has left warehouse",
            ),
            step(
                "label_received_at_destination",
                "2026-04-28T20:15:55.000-04:00",
                "Package received in local facility",
                "Cityville, ON, Canada",
            ),
            step(
                "label_scanned",
                "2026-04-28T11:52:17.000-04:00",
                "Package received in Cityville",
                "Cityville, ON, Canada",
            ),
            step(
                "label_created",
                "2026-04-27T22:03:58.864-04:00",
                "Shipment created",
                None,
            ),
        ],
        "estimated_delivery_date": "2026-04-29T12:00:00.000-04:00",
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel: no delivery timestamp, an ETA."""
    sample = delivered_sample(code)
    del sample["label_delivered_at"]
    sample.update(
        {
            "label_status": "label_out_for_delivery",
            "label_description": "Package has left warehouse",
            "active_transit_sequence": 3,
            "transit_steps": sample["transit_steps"][1:],
        }
    )
    return sample
