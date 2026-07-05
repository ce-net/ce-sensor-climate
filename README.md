# ce-sensor-climate

A modular, cap-scoped **temperature + humidity** sensor for the CE mesh, written as a
Python `script`-tier ceapp. It is a pure producer: it publishes readings and answers
cap-gated subscriptions, and knows nothing about who consumes them. Add a tenth climate
sensor by running one install; consumers pick it up by topic and never change.

Part of the building-telemetry mesh (`PLAN/ce-building-sensors.md`). Sibling:
[`ce-sensor-camera`](../ce-sensor-camera). Uses the shared Python client
[`ce-py`](../ce-py) (`ce.py` is vendored here for the zero-install script tier).

## How it works

- **Discovery, no address.** The sensor announces itself on `ce.sensor/announce` — a
  consumer learns it exists (service, node id, control topic, required capability) by
  listening to that topic, never by an IP address.
- **Access, by capability.** A consumer presents a capability on the control topic
  `ce.sensor/climate/ctl`. The sensor verifies it grants `building:climate:read` rooted at
  the building-org root (via `ce-iam verify`, fail-closed) before serving. "Provide a
  capability, and whoever has clearance can use it."
- **Data.** Once subscribed, readings are pushed to the cleared consumer on
  `ce.sensor/climate/data`. The lease expires (default 60s); re-subscribe to keep it alive.

### Control protocol (`ce.sensor/climate/ctl`, request/reply, JSON)

Every request carries `{"cap": "<token>"}`. Ops: `read` (one reading), `subscribe` /
`unsubscribe` (start/stop the pushed stream), `status`.

### Reading schema (`ce.sensor.reading/1`)

```json
{"schema":"ce.sensor.reading/1","sensor":"ce-sensor-climate","node":"<hex>",
 "instance":"climate-lobby","ts":1720000000.0,
 "readings":[{"metric":"temperature","value":21.4,"unit":"C"},
             {"metric":"humidity","value":43.2,"unit":"%RH"}]}
```

## Real hardware (plug and play)

`climate/driver.py` is the only hardware-specific file. Mock data ships by default; to read
a real I2C SHT31/BME280 on a UNO Q, implement `I2cDriver.read()` against `/dev/i2c-*` and
swap it in `main.py`. Nothing else — schema, service, consumers — changes.

## Configuration (env, no flags)

| Var | Default | Meaning |
|---|---|---|
| `CE_SENSOR_INSTANCE` | `climate` | Name for this physical unit (e.g. `climate-lobby`). |
| `CE_SENSOR_INTERVAL` | `5` | Seconds between pushed readings. |
| `CE_SENSOR_AUTH` | `capiam` | `capiam` (real `ce-iam verify`) / `allowlist` / `allow` (dev) / `deny`. |
| `CE_SENSOR_ALLOW` | – | Comma-separated NodeIds for `allowlist` mode. |

## Develop & test

```bash
pytest            # pure unit tests: cap-gating, ops, lease expiry, announce, driver, schema
```

No node, no ce-iam, no hardware required — the service logic is pure (`handle`/`tick`).

## Deploy

```bash
ce app install ./ce-sensor-climate --on node=<sensor-board>
```

The single `ce` supervisor runs and restarts it. `CE_SENSOR_AUTH=capiam` requires the
building-org root to be accepted on the node (`ce-iam root add`).
