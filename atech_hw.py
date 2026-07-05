"""The atech "port system": board port→pin maps, module catalog, wiring, and allocation.

Vendored (like ce.py) into every module ceapp and the ce-arduino coordinator so they share one
hardware model. All values come EXACTLY from the atech SDK catalog (ce-atech/sdk/catalog) — see
docs/atech-modules.md. This is what lets an app tell you how to connect a module (`wiring`) and
lets the coordinator hand out non-conflicting ports (`free_port`).

A board port exposes two lines — Line A (`signal`) and Line B (`signal_b`) — each a GPIO pin.
A module occupies `size` adjacent ports and assigns a role to each line (e.g. AHT20: SDA on A,
SCL on B). `wiring()` turns (board, module, port) into the exact pin-by-pin connection.
"""

from __future__ import annotations

from typing import Optional

# board id -> {port_number: (gpio_line_a, gpio_line_b)}, reserved ports, adjacency pairs.
BOARDS = {
    "8port": {
        "mcu": "ESP32-S3",
        "ports": {1: (5, 4), 2: (7, 6), 3: (9, 10), 4: (1, 2), 5: (43, 44), 7: (15, 16)},
        "reserved": [6, 8],           # 6 = Restart button, 8 = USB-C jack
        "adjacent": [(1, 2), (3, 4)],  # the only pairs for double-width modules
    },
    "14port": {
        "mcu": "ESP32-S3",
        "ports": {1: (9, 8), 2: (5, 4), 3: (17, 18), 4: (16, 15), 5: (11, 10), 6: (13, 12),
                  7: (6, 7), 9: (40, 41), 10: (1, 2), 11: (43, 44), 13: (39, 38), 14: (36, 35)},
        "reserved": [8, 12],          # 8 = USB-C jack, 12 = Reset button
        "adjacent": [(1, 2), (3, 4), (5, 6), (9, 10), (13, 14)],
    },
}
DEFAULT_BOARD = "8port"

# module id -> spec (from the atech catalog). `pins` maps a line to its wiring role.
MODULES = {
    "aht20": {"name": "AHT20 Temperature & Humidity", "category": "sensor", "interface": "i2c",
              "size": 1, "i2c_address": 0x38, "pins": {"A": "SDA", "B": "SCL"}},
    "neopixel": {"name": "NeoPixel 3x3 RGB LED", "category": "led", "interface": "gpio",
                 "size": 1, "pins": {"A": "DIN (data / RGB)", "B": "DIN (RGBW variant)"}},
    "pir": {"name": "PIR Motion (AM312)", "category": "sensor", "interface": "gpio",
            "size": 1, "pins": {"A": "OUT (digital)"}},
    "button": {"name": "Button", "category": "input", "interface": "gpio",
               "size": 1, "pins": {"A": "signal"}},
    "speaker": {"name": "I2S Speaker (MAX98357A)", "category": "audio", "interface": "gpio",
                "size": 2, "pins": {"A": "LRCLK", "B": "BCLK", "B2": "DIN"}},
    "st7735_tft": {"name": "ST7735 TFT (160x80)", "category": "display", "interface": "spi",
                   "size": 2, "pins": {"A": "DC", "B": "CS", "A2": "MOSI", "B2": "SCLK"}},
}


class HwError(ValueError):
    pass


def board_map(board_id: str) -> dict:
    if board_id not in BOARDS:
        raise HwError(f"unknown board {board_id!r}; known: {list(BOARDS)}")
    return BOARDS[board_id]


def module_spec(module_id: str) -> dict:
    if module_id not in MODULES:
        raise HwError(f"unknown module {module_id!r}; known: {list(MODULES)}")
    return MODULES[module_id]


def _ordered_ports(board: dict) -> list:
    return sorted(p for p in board["ports"] if p not in board["reserved"])


def _adjacent_of(board: dict, port: int) -> Optional[int]:
    for a, b in board["adjacent"]:
        if a == port:
            return b
    return None


def wiring(board_id: str, module_id: str, port: int) -> list:
    """Return the exact pin-by-pin connection for `module_id` placed at `port` on `board_id`.

    Each entry is {port, line, gpio, role}. Size-2 modules also use the adjacent port's lines.
    """
    board = board_map(board_id)
    mod = module_spec(module_id)
    if port not in board["ports"] or port in board["reserved"]:
        raise HwError(f"port {port} is not a usable slot on {board_id}")
    ga, gb = board["ports"][port]
    pins = mod["pins"]
    out = []
    if "A" in pins:
        out.append({"port": port, "line": "A", "gpio": ga, "role": pins["A"]})
    if "B" in pins:
        out.append({"port": port, "line": "B", "gpio": gb, "role": pins["B"]})
    if mod["size"] == 2:
        adj = _adjacent_of(board, port)
        if adj is None or adj not in board["ports"]:
            raise HwError(f"{module_id} needs two adjacent ports; {port} has no free adjacent slot")
        aga, agb = board["ports"][adj]
        if "A2" in pins:
            out.append({"port": adj, "line": "A", "gpio": aga, "role": pins["A2"]})
        if "B2" in pins:
            out.append({"port": adj, "line": "B", "gpio": agb, "role": pins["B2"]})
    return out


def ports_used(board_id: str, module_id: str, port: int) -> list:
    """The set of physical ports a module occupies at `port` (2 for double-width modules)."""
    board = board_map(board_id)
    if module_spec(module_id)["size"] == 1:
        return [port]
    adj = _adjacent_of(board, port)
    return [port, adj] if adj is not None else [port]


def free_port(board_id: str, module_id: str, taken) -> Optional[int]:
    """Lowest usable port that fits `module_id` and does not overlap `taken` (a set of port ints).

    Returns None if the board is full for this module. This is the allocation rule that keeps two
    apps from claiming the same port.
    """
    board = board_map(board_id)
    taken = set(taken)
    size = module_spec(module_id)["size"]
    for port in _ordered_ports(board):
        used = ports_used(board_id, module_id, port)
        if size == 2 and (len(used) != 2 or any(u in board["reserved"] for u in used)):
            continue
        if not any(u in taken for u in used):
            return port
    return None
