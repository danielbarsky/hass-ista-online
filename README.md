# ISTA Online (DK) — Home Assistant integration

Reads meter data from the ista Denmark portal (istaonline.dk) into Home
Assistant, so district-heating and water consumption show up on the Energy
dashboard.

> **This is a modified fork.** The original is
> [JeppeLeth/hass-ista-online](https://github.com/JeppeLeth/hass-ista-online)
> by Jeppe Leth, Apache-2.0. See [Changes from upstream](#changes-from-upstream)
> for what differs, and [NOTICE](NOTICE) for attribution.

## Read this before installing

The ista portal offers only an OAuth2 **password grant**, so
**two-factor authentication must be disabled** on your ista account for this
integration to work. That is a real reduction in the security of an account
holding your address and consumption history. Decide that deliberately.

Your password is stored in plain text in `.storage/core.config_entries`, which
is how Home Assistant stores credentials for every cloud integration.

## What you get

Per meter, one device with:

| Entity | State class | Purpose |
|---|---|---|
| Last meter reading | `total_increasing` | The meter register. **Use this one in the Energy dashboard.** |
| Last meter consumption | none | Consumption for the last billing period, for reference. |
| Diagnostics | — | Reading date, activation/deactivation dates, meter type and code, address. |

Device class is set to water or energy automatically, so the reading sensor can
be selected directly as an Energy dashboard source.

### Expect monthly data

ista publishes readings roughly **monthly, lagging by weeks**. The integration
polls every 6 hours, but that is only to notice new data promptly — it will not
give you live consumption. If you want live figures you need to read the meter's
wireless M-Bus telegrams directly, which is a different project.

## Installation

### HACS (custom repository)

1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/danielbarsky/hass-ista-online`, type **Integration**.
3. Install **ISTA Online (DK)**, then restart Home Assistant.

### Manual

```bash
cp -R custom_components/ista_online /config/custom_components/
```

Restart Home Assistant, then add the integration under
**Settings → Devices & services → Add integration → ISTA Online**.

## Configuration

| Field | Notes |
|---|---|
| Country | Only Denmark is implemented. |
| Username / Password | Your istaonline.dk login, with 2FA disabled. |
| API base URL | Defaults to the working endpoint. Change only if it moves. |

### About the endpoint

The default is `https://service.istaonlinebeta.dk`. Despite the name, this is
the live backend serving the Danish portal's API — verified responding with
correct OAuth2 semantics. The hostnames under `istaonline.dk` are wildcard DNS
pointing at the ASP.NET web portal and expose no API. The base URL is
configurable so that a future endpoint change needs no code edit.

## Changes from upstream

| Area | Upstream | This fork |
|---|---|---|
| Credentials per poll | Username and password re-sent **every hour** | Bearer token cached until shortly before expiry; a mid-poll 401 refreshes once and retries |
| Poll interval | 1 hour | 6 hours (the data is monthly) |
| HTTP | Blocking `requests` in an executor, dependency unpinned | `aiohttp` via Home Assistant's shared session; no third-party requirements |
| Reauthentication | Broken — read `context["entry"]`, which HA does not set, so it always aborted and the integration had to be deleted and re-added | Working `reauth_confirm` flow |
| Options flow | Instance method with the wrong signature; HA passed the entry in as `self` | Proper `@staticmethod` |
| Duplicate accounts | Nothing prevented adding the same account twice | Config entry has a unique ID |
| Numeric values | Raw API string passed through; a Danish decimal comma makes HA reject the state | Coerced to float, handling `1.234,5` and `1234.5` |
| Device class | Matched four literal strings; anything else yielded `None` and the sensor could not be used in the Energy dashboard | Wider vocabulary plus a unit-based fallback (m³ → water, kWh/MWh/GJ → energy) |
| State classes | Register was `total`; the period consumption delta was `total_increasing`, so each new period looked like a meter reset and corrupted long-term statistics | Register is `total_increasing`; the delta carries no state class |
| Error reporting | All failures shown as one generic error | Bad credentials distinguished from connection failures |

## Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.ista_online: debug
```

Debug logs include each meter's raw `MeterType` and `Unit`, which is what you
need if a sensor comes through without a device class.

## Known limitations

- Meters are enumerated once at setup; a newly added meter needs a reload.
- Only the Danish portal is implemented. Germany has an official
  [`ista_ecotrend`](https://www.home-assistant.io/integrations/ista_ecotrend/)
  integration in Home Assistant core; the Netherlands and Poland have their own
  community ones. The portals are not mutually compatible.
- Untested against a live account by the fork author — the upstream API contract
  was preserved, but please report anything that misbehaves.

## License

Apache-2.0, inherited from upstream. See [LICENSE.md](LICENSE.md) and
[NOTICE](NOTICE).
