"""
tgs_colorizer.py — Colorize animated Telegram stickers (.tgs)

.tgs files are gzip-compressed Lottie JSON animations.
We decompress, walk the JSON tree, recolor all color values,
then recompress back to .tgs

Lottie color values are stored as arrays [R, G, B] or [R, G, B, A]
with values in 0.0–1.0 range.
"""

import gzip
import json
import copy
import random
import math
from typing import Any


# ──────────────────────────────────────────────────────────────
# Color theme definitions (same names as colorizer.py)
# Each theme: {"dark": [r,g,b], "mid": [r,g,b], "light": [r,g,b]}
# Values 0.0–1.0 (Lottie format)
# ──────────────────────────────────────────────────────────────

def _rgb(r, g, b):
    return [r / 255, g / 255, b / 255]


THEMES = {
    "red":      {"dark": _rgb(20, 0, 0),    "mid": _rgb(220, 50, 50),   "light": _rgb(255, 200, 200)},
    "orange":   {"dark": _rgb(25, 8, 0),    "mid": _rgb(230, 120, 20),  "light": _rgb(255, 220, 160)},
    "yellow":   {"dark": _rgb(30, 25, 0),   "mid": _rgb(240, 210, 30),  "light": _rgb(255, 255, 180)},
    "green":    {"dark": _rgb(0, 20, 0),    "mid": _rgb(40, 180, 60),   "light": _rgb(180, 255, 180)},
    "blue":     {"dark": _rgb(0, 0, 30),    "mid": _rgb(40, 80, 220),   "light": _rgb(180, 200, 255)},
    "purple":   {"dark": _rgb(15, 0, 25),   "mid": _rgb(140, 40, 200),  "light": _rgb(220, 180, 255)},
    "pink":     {"dark": _rgb(25, 0, 10),   "mid": _rgb(240, 80, 150),  "light": _rgb(255, 200, 230)},
    "cyan":     {"dark": _rgb(0, 15, 20),   "mid": _rgb(30, 200, 230),  "light": _rgb(180, 245, 255)},
    "brown":    {"dark": _rgb(15, 8, 0),    "mid": _rgb(140, 80, 30),   "light": _rgb(210, 170, 120)},
    "grayscale":{"dark": _rgb(10, 10, 10),  "mid": _rgb(128, 128, 128), "light": _rgb(240, 240, 240)},
    "sunset":   {"dark": _rgb(20, 5, 30),   "mid": _rgb(200, 60, 60),   "light": _rgb(255, 220, 100)},
    "ocean":    {"dark": _rgb(0, 10, 40),   "mid": _rgb(0, 100, 180),   "light": _rgb(180, 240, 255)},
    "forest":   {"dark": _rgb(5, 20, 5),    "mid": _rgb(30, 120, 40),   "light": _rgb(200, 240, 160)},
    "fire":     {"dark": _rgb(10, 0, 0),    "mid": _rgb(220, 80, 0),    "light": _rgb(255, 240, 100)},
    "ice":      {"dark": _rgb(10, 20, 40),  "mid": _rgb(80, 160, 220),  "light": _rgb(240, 250, 255)},
    "sakura":   {"dark": _rgb(30, 5, 15),   "mid": _rgb(220, 100, 140), "light": _rgb(255, 230, 240)},
    "gold":     {"dark": _rgb(20, 15, 0),   "mid": _rgb(180, 130, 0),   "light": _rgb(255, 245, 180)},
    "galaxy":   {"dark": _rgb(5, 0, 20),    "mid": _rgb(100, 40, 180),  "light": _rgb(220, 160, 255)},
    "rainbow":  None,  # special handling
    "random":   None,  # special handling
}


def _lerp(a, b, t):
    return a + (b - a) * t


def map_color_to_theme(r: float, g: float, b: float, theme: dict) -> tuple:
    """
    Map a Lottie color [r,g,b] (0-1) to theme colors using luminance.
    """
    lum = 0.299 * r + 0.587 * g + 0.114 * b  # 0.0–1.0

    dark = theme["dark"]
    mid = theme["mid"]
    light = theme["light"]

    if lum < 0.5:
        t = lum / 0.5
        nr = _lerp(dark[0], mid[0], t)
        ng = _lerp(dark[1], mid[1], t)
        nb = _lerp(dark[2], mid[2], t)
    else:
        t = (lum - 0.5) / 0.5
        nr = _lerp(mid[0], light[0], t)
        ng = _lerp(mid[1], light[1], t)
        nb = _lerp(mid[2], light[2], t)

    return (
        max(0.0, min(1.0, nr)),
        max(0.0, min(1.0, ng)),
        max(0.0, min(1.0, nb))
    )


def hsl_to_rgb_float(h, s, l):
    """HSL (0-1) → RGB (0-1)."""
    if s == 0:
        return l, l, l
    def hue2rgb(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return hue2rgb(p, q, h + 1/3), hue2rgb(p, q, h), hue2rgb(p, q, h - 1/3)


def map_color_rainbow(r: float, g: float, b: float) -> tuple:
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    sat = 0.9 - lum * 0.3
    light = 0.25 + lum * 0.5
    nr, ng, nb = hsl_to_rgb_float(lum, sat, light)
    return nr, ng, nb


# ──────────────────────────────────────────────────────────────
# JSON tree walker
# ──────────────────────────────────────────────────────────────

def is_lottie_color(val: Any) -> bool:
    """
    Check if a value looks like a Lottie color array.
    Lottie colors: list of 3 or 4 floats all in [0, 1] range (or slightly over for some exporters)
    """
    if not isinstance(val, list):
        return False
    if len(val) not in (3, 4):
        return False
    return all(isinstance(v, (int, float)) and -0.01 <= v <= 1.5 for v in val[:3])


def recolor_value(val: Any, color: str, theme: dict, random_hue: float) -> Any:
    """Recursively walk JSON and recolor color arrays."""
    if isinstance(val, dict):
        return {k: recolor_value(v, color, theme, random_hue) for k, v in val.items()}

    if isinstance(val, list):
        # Check if this whole list is a color
        if is_lottie_color(val):
            r = max(0.0, min(1.0, float(val[0])))
            g = max(0.0, min(1.0, float(val[1])))
            b = max(0.0, min(1.0, float(val[2])))
            alpha = val[3] if len(val) == 4 else None

            if color == "rainbow":
                nr, ng, nb = map_color_rainbow(r, g, b)
            elif color == "random":
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                sat = 0.85
                light = 0.2 + lum * 0.6
                nr, ng, nb = hsl_to_rgb_float(random_hue, sat, light)
            else:
                nr, ng, nb = map_color_to_theme(r, g, b, theme)

            result = [nr, ng, nb]
            if alpha is not None:
                result.append(float(alpha))
            return result

        # Otherwise recurse into list elements
        return [recolor_value(item, color, theme, random_hue) for item in val]

    return val


def recolor_lottie_json(data: dict, color: str) -> dict:
    """Apply color theme to entire Lottie JSON."""
    theme = THEMES.get(color)

    # Handle special cases
    if color == "random" and theme is None:
        random_hue = random.random()
        # Build a theme dynamically
        dark = list(hsl_to_rgb_float(random_hue, 0.9, 0.15))
        mid = list(hsl_to_rgb_float(random_hue, 0.85, 0.50))
        light = list(hsl_to_rgb_float(random_hue, 0.5, 0.85))
        theme = {"dark": dark, "mid": mid, "light": light}
        color_mode = "random_theme"
    elif color == "rainbow":
        random_hue = 0
        color_mode = "rainbow"
    else:
        random_hue = 0
        color_mode = color

    result = copy.deepcopy(data)
    result = recolor_value(result, color_mode if color_mode != "random_theme" else color, theme, random_hue)
    return result


# ──────────────────────────────────────────────────────────────
# Also handle keyframe color arrays (nested in "k" fields)
# Lottie animated colors: {"a": 1, "k": [[t, [r,g,b,a], ...], ...]}
# We handle these naturally since recolor_value recurses into all lists
# ──────────────────────────────────────────────────────────────

def colorize_tgs(tgs_bytes: bytes, color: str) -> bytes:
    """
    Main entry point.
    Takes raw .tgs bytes, returns colorized .tgs bytes.
    """
    # Decompress
    try:
        json_bytes = gzip.decompress(tgs_bytes)
    except Exception as e:
        raise ValueError(f"Failed to decompress .tgs: {e}")

    # Parse JSON
    try:
        lottie = json.loads(json_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse Lottie JSON: {e}")

    # Recolor
    colored = recolor_lottie_json(lottie, color)

    # Re-serialize
    out_json = json.dumps(colored, separators=(',', ':')).encode('utf-8')

    # Recompress (Telegram requires gzip level 9 for .tgs)
    out_tgs = gzip.compress(out_json, compresslevel=9)

    return out_tgs
                    
