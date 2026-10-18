# GoBolt Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-gobolt.svg)](https://github.com/ha-parcel-integrations/ha-gobolt/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-gobolt/total.svg)](https://github.com/ha-parcel-integrations/ha-gobolt/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [GoBolt](https://www.gobolt.com) parcels, the Canadian last-mile delivery service retailers use to ship to you. No account and no postal code are needed — you enter the tracking code yourself, just like on the GoBolt tracking page.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of GoBolt parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text and the expected delivery day
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery day
- `gobolt.track_parcel` / `gobolt.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history (each scan, oldest first)
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- A GoBolt parcel and its tracking code, as issued by the retailer who sent
  it — no account and no postal code needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-gobolt` as an **Integration**.
3. Install **GoBolt** and restart Home Assistant.

### Manual

Copy `custom_components/gobolt` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → GoBolt**. There is nothing to fill in: the hub is created immediately (GoBolt tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`gobolt.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code comes from the retailer that shipped the parcel.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every GoBolt
  hub out there polls at exactly the same second.

GoBolt only gives a delivery *day*, not a window, so a parcel's expected
delivery moment is shown as midday on that day. A parcel that is out for
delivery moves to the 15-minute cadence an hour before that, or immediately
when no day is known.

## Removal

Standard HA removal applies: **Settings → Devices & Services → GoBolt → ⋮ → Delete**. Nothing is stored on GoBolt's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.gobolt_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.gobolt_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.gobolt_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.gobolt_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.gobolt_last_successful_update` | Diagnostic: when GoBolt was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | The shipment was created |
| `in_transit` | Scanned by GoBolt, or received at the local facility |
| `out_for_delivery` | Out for delivery |
| `delivered` | Delivered |
| `returning` | Returned to the warehouse |
| `problem` | GoBolt reports a failed delivery |
| `unknown` | Not found yet, or a status we have not mapped yet |

GoBolt does not report pickup points, so `at_pickup_point` never occurs. Only
delivered parcels have been confirmed against real data so far: `problem` and
`returning` come from GoBolt's own tracking page and have not been seen live.

GoBolt's own status name is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the GoBolt device):

| Event | When |
|---|---|
| `gobolt_parcel_registered` | A new parcel appears in the active list |
| `gobolt_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `gobolt_parcel_delivered` | A parcel is delivered |
| `gobolt_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `gobolt.track_parcel` | `tracking_code` | Start tracking a parcel |
| `gobolt.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.gobolt: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — GoBolt does not know the code (yet), or it was mistyped. It will pick up automatically once GoBolt registers it.
- **A status logs "Unrecognised GoBolt status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-gobolt/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by GoBolt, Home Assistant, or any other third party referenced in this project. Please don't contact GoBolt for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to GoBolt's terms. Data is sent only to GoBolt's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking lookup as the GoBolt tracking page. It sends only the tracking code you entered.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
