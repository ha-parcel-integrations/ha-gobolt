# Examples

Ready-to-paste Home Assistant snippets for the GoBolt integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automations — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |
| [`dashboards/`](dashboards/) | Lovelace snippets, including [`add_parcel_card.yaml`](dashboards/add_parcel_card.yaml) — track a new parcel straight from a dashboard via the `gobolt.track_parcel` service. |

All examples assume a single GoBolt hub. Adjust entity IDs to match yours.

## Services

| Service | Description |
|---|---|
| `gobolt.track_parcel` | Start tracking a parcel (`tracking_code`). |
| `gobolt.untrack_parcel` | Stop tracking a parcel (`tracking_code`). |

## Events used in the examples

The coordinator fires these on the HA event bus:

| Event | When | Payload |
|---|---|---|
| `gobolt_parcel_registered` | A new parcel appears in the active list | The full normalised parcel dict |
| `gobolt_parcel_status_changed` | A parcel's canonical status changes | Same, plus `old_status` / `new_status` |
| `gobolt_parcel_delivered` | A parcel reaches the delivered status | Same, plus `old_status` / `new_status` (fires *instead of* `status_changed` on that final hop) |
| `gobolt_parcel_delivery_time_changed` | A parcel's expected delivery time changes | Same, plus `old_planned_from` / `new_planned_from` / `old_planned_to` / `new_planned_to` |

Events are suppressed on the first refresh after start-up.
