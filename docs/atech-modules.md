# atech hardware modules — exact specifications (from the atech SDK)

Source of truth: the atech SDK catalog at `ce-atech/sdk/catalog/` (the CE/Rust port of the
atech.dev open Python SDK — "the catalog IS the low-level documentation"). Values below are
copied EXACTLY from that catalog and the vendor wire schema confirmed from PyPI `atech`
1.0.0a3. Do not guess these — update this file from the catalog if the SDK changes.

The sensor ceapps read atech module data over the atech USB-serial envelope (see §3); the
values, units, event keys, and ranges here are the contract they parse.

## 1. Boards

atech motherboards are **ESP32-S3** (`esp32-s3-devkitc-1`, Arduino framework), in two sizes:

| Board | id | Ports | Notes |
|---|---|---|---|
| Atech 8-Port | `8port` | 8 | adjacency pairs `[1,2] [3,4] …` |
| Atech 14-Port | `14port` | 14 | port 7 isolated top-middle; port 8 = USB-C (reserved); port 12 = Reset (reserved) |

A module occupies one or more adjacent **ports**; each port maps to two MCU lines (Line A /
Line B → GPIO pins, per `catalog/boards/*.yaml`). Modules spanning two ports (speaker, display)
need an adjacent pair.

## 2. Modules (the full catalog)

### Sensors

**AHT20 — Temperature & Humidity** (`aht20`) — THE temp/humidity module for the building sensors.
- Interface: **I2C, address `0x38`**, 1 port (SDA = Line A, SCL = Line B, bus @ 400 kHz).
- Range: **−40 to +125 °C**, **0 to 100 %RH**. ~75 ms per measurement, cached (getters never block).
- Emits every **2 s**, two events, `source: "aht20"`:
  - `key: <instance>_temperature`, `value_type: float`, `unit: "C"`.
  - `key: <instance>_humidity`, `value_type: float`, `unit: "%"`.
- Health: `isConnected()` — the sensor ACKs at `0x38` only if wired + powered.

**PIR — Motion (AM312)** (`pir`) — the obvious next sensor to add (motion → "is someone there").
- Interface: GPIO digital (Line A only), 1 port. Cone ~3–5 m / 100°. Hardware-debounced.
- OUT goes HIGH for ~2 s on motion, then LOW. **~30 s warm-up** after power-on (`isWarmedUp()`).
- Emits, `source: "pir"`: `key: <instance>` (i.e. `motion`), `value_type: int` — `1` on rising
  edge (motion start), `0` when it clears (~2 s later). Warm-up triggers suppressed.

### Inputs
- **button** — momentary push button. **rotary_encoder** — quadrature knob with push.

### Outputs / feedback (for the "ce is alive" feedback on the board — Leif's ask)
- **neopixel** — 3×3 grid of WS2812B / SK6812 RGB LEDs (GPIO, Adafruit_NeoPixel). Actions:
  `<inst>_fill {"r":0-255,"g":..,"b":..}`, `<inst>_pixel {"index":0-8,"r":..,"g":..,"b":..}`,
  `<inst>_clear`. → a heartbeat blink = "ce is up".
- **speaker** — I2S 3W Class-D amp **MAX98357A** (GPIO, 2 adjacent ports). Actions:
  `<inst>_play_rtttl "<RTTTL string>"` (e.g. `"tune:d=4,o=5,b=140:c,e,g,2c6"`, plays in the
  background), `<inst>_set_volume 0.0–1.0` (0.3–0.5 comfortable; 1.0 clips), `<inst>_stop`.
  → a short RTTTL beep on startup = "ce is running".
- **st7735_tft** — 160×80 colour TFT, **ST7735** SPI controller, RGB565, 2 adjacent ports
  (canvas + direct modes; Adafruit GFX + ST7735 libs). → status/dashboard display.
- **dc_motor** — `<inst>_speed` int −255..255 (neg = reverse), `<inst>_stop`, `<inst>_brake`;
  25 kHz PWM. **stepper_motor** — stepper driver.

## 3. Wire protocol (open SDK = USB-serial ONLY)

The open atech SDK transport is **USB-serial only** (the WiFi/WebSocket layer is in the closed
hosted product). One compact JSON per line/frame:

- **Host → device action:** `{"action":"<instance>_<verb>","value":"<ALWAYS A STRING>"}`. The
  `value` is a STRING even for numbers/objects (`"200"`, `"{\"r\":255,...}"`) — firmware parses
  it as `char*`. Sending a bare number/object may be silently unparseable by stock firmware.
- **Device → host event (wrapped):** `{"type":"event","payload":{"event_type":"sensor|button|
  state|log","key":...,"value":...,"unit":...,"source":"<module>"}}`.
- **Boot:** one `{"type":"boot","payload":{"reset_reason":...,"free_heap":...,"modules":[...]}}`
  at the end of `setup()`. Non-JSON lines are log noise.

The climate ceapp's atech driver opens the board's serial device, reads these lines, and caches
the latest `source:"aht20"` `_temperature` / `_humidity` events — this is the exact, SDK-faithful
integration (vs. reading the raw I2C chip directly, which is the alternative when an AHT20 is
wired straight to the UNO Q's own I2C header).

## 4. Open question (do NOT guess — confirm with Leif)

How the atech modules physically attach to the **Arduino UNO Q**: (a) an atech ESP32 board with
modules, connected to the UNO Q over USB-serial (then the ceapp reads the envelope above — the
default assumption here), or (b) individual I2C modules (e.g. AHT20) wired to the UNO Q's own
I2C header (then the ceapp reads I2C directly). The driver supports both; the physical wiring
decides which `CE_SENSOR_DRIVER` mode to use (`atech` serial vs `i2c`).
