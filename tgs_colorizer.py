"""
tgs_colorizer.py — Colorize animated Telegram stickers (.tgs / Lottie JSON).
"""

import gzip
import json
import copy
import random
from typing import Any


def _rgb(r, g, b):
    return [r / 255, g / 255, b / 255]


THEMES = {
    "red":      {"dark": _rgb(20,0,0),    "mid": _rgb(220,50,50),   "light": _rgb(255,200,200)},
    "orange":   {"dark": _rgb(25,8,0),    "mid": _rgb(230,120,20),  "light": _rgb(255,220,160)},
    "yellow":   {"dark": _rgb(30,25,0),   "mid": _rgb(240,210,30),  "light": _rgb(255,255,180)},
    "green":    {"dark": _rgb(0,20,0),    "mid": _rgb(40,180,60),   "light": _rgb(180,255,180)},
    "blue":     {"dark": _rgb(0,0,30),    "mid": _rgb(40,80,220),   "light": _rgb(180,200,255)},
    "purple":   {"dark": _rgb(15,0,25),   "mid": _rgb(140,40,200),  "light": _rgb(220,180,255)},
    "pink":     {"dark": _rgb(25,0,10),   "mid": _rgb(240,80,150),  "light": _rgb(255,200,230)},
    "cyan":     {"dark": _rgb(0,15,20),   "mid": _rgb(30,200,230),  "light": _rgb(180,245,255)},
    "brown":    {"dark": _rgb(15,8,0),    "mid": _rgb(140,80,30),   "light": _rgb(210,170,120)},
    "grayscale":{"dark": _rgb(10,10,10),  "mid": _rgb(128,128,128), "light": _rgb(240,240,240)},
    "sunset":   {"dark": _rgb(20,5,30),   "mid": _rgb(200,60,60),   "light": _rgb(255,220,100)},
    "ocean":    {"dark": _rgb(0,10,40),   "mid": _rgb(0,100,180),   "light": _rgb(180,240,255)},
    "forest":   {"dark": _rgb(5,20,5),    "mid": _rgb(30,120,40),   "light": _rgb(200,240,160)},
    "fire":     {"dark": _rgb(10,0,0),    "mid": _rgb(220,80,0),    "light": _rgb(255,240,100)},
    "ice":      {"dark": _rgb(10,20,40),  "mid": _rgb(80,160,220),  "light": _rgb(240,250,255)},
    "sakura":   {"dark": _rgb(30,5,15),   "mid": _rgb(220,100,140), "light": _rgb(255,230,240)},
    "gold":     {"dark": _rgb(20,15,0),   "mid": _rgb(180,130,0),   "light": _rgb(255,245,180)},
    "galaxy":   {"dark": _rgb(5,0,20),    "mid": _rgb(100,40,180),  "light": _rgb(220,160,255)},
}


def _lerp(a, b, t):
    return a + (b - a) * t


def _lum(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def _map_theme(r, g, b, theme):
    l = _lum(r, g, b)
    dark, mid, light = theme["dark"], theme["mid"], theme["light"]
    if l < 0.5:
        t = l / 0.5
        return [max(0., min(1., _lerp(dark[i], mid[i], t))) for i in range(3)]
    else:
        t = (l - 0.5) / 0.5
        return [max(0., min(1., _lerp(mid[i], light[i], t))) for i in range(3)]


def _hsl_to_rgb(h, s, l):
    if s == 0:
        return [l, l, l]
    def hue2rgb(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q-p)*6*t
        if t < 1/2: return q
        if t < 2/3: return p + (q-p)*(2/3-t)*6
        return p
    q = l*(1+s) if l < 0.5 else l+s-l*s
    p = 2*l - q
    return [hue2rgb(p,q,h+1/3), hue2rgb(p,q,h), hue2rgb(p,q,h-1/3)]


def _is_color(val):
    if not isinstance(val, list) or len(val) not in (3, 4):
        return False
    return all(isinstance(v, (int, float)) and -0.01 <= v <= 1.5 for v in val[:3])


def _recolor(val: Any, color: str, theme, rnd_hue: float) -> Any:
    if isinstance(val, dict):
        return {k: _recolor(v, color, theme, rnd_hue) for k, v in val.items()}
    if isinstance(val, list):
        if _is_color(val):
            r = max(0., min(1., float(val[0])))
            g = max(0., min(1., float(val[1])))
            b = max(0., min(1., float(val[2])))
            alpha = val[3] if len(val) == 4 else None
            if color == "rainbow":
                l = _lum(r, g, b)
                sat = 0.9 - l * 0.3
                light = 0.25 + l * 0.5
                nr, ng, nb = _hsl_to_rgb(l, sat, light)
                result = [nr, ng, nb]
            elif color == "random":
                l = _lum(r, g, b)
                nr, ng, nb = _hsl_to_rgb(rnd_hue, 0.85, 0.2 + l * 0.6)
                result = [nr, ng, nb]
            else:
                result = _map_theme(r, g, b, theme)
            if alpha is not None:
                result.append(float(alpha))
            return result
        return [_recolor(item, color, theme, rnd_hue) for item in val]
    return val


def colorize_tgs(tgs_bytes: bytes, color: str) -> bytes:
    try:
        json_bytes = gzip.decompress(tgs_bytes)
    except Exception as e:
        raise ValueError(f"Cannot decompress .tgs: {e}")

    try:
        lottie = json.loads(json_bytes)
    except Exception as e:
        raise ValueError(f"Cannot parse Lottie JSON: {e}")

    rnd_hue = random.random()
    theme = THEMES.get(color)

    if theme is None and color not in ("rainbow", "random"):
        theme = THEMES["blue"]

    if color == "random" and theme is None:
        dark  = _hsl_to_rgb(rnd_hue, 0.9, 0.15)
        mid   = _hsl_to_rgb(rnd_hue, 0.85, 0.50)
        light = _hsl_to_rgb(rnd_hue, 0.5, 0.85)
        theme = {"dark": dark, "mid": mid, "light": light}

    colored = _recolor(copy.deepcopy(lottie), color, theme, rnd_hue)
    out_json = json.dumps(colored, separators=(',', ':')).encode('utf-8')
    return gzip.compress(out_json, compresslevel=9)
    
