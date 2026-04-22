"""
colorizer.py — Fast vectorized image colorizer using numpy.
No per-pixel loops — works fast on Railway free tier.
"""

import random
import numpy as np
from PIL import Image


# Color map: list of (threshold 0.0-1.0, (R, G, B))
COLOR_MAPS = {
    "red":      [(0.0, (20, 0, 0)),    (0.5, (220, 50, 50)),   (1.0, (255, 200, 200))],
    "orange":   [(0.0, (25, 8, 0)),    (0.5, (230, 120, 20)),  (1.0, (255, 220, 160))],
    "yellow":   [(0.0, (30, 25, 0)),   (0.5, (240, 210, 30)),  (1.0, (255, 255, 180))],
    "green":    [(0.0, (0, 20, 0)),    (0.5, (40, 180, 60)),   (1.0, (180, 255, 180))],
    "blue":     [(0.0, (0, 0, 30)),    (0.5, (40, 80, 220)),   (1.0, (180, 200, 255))],
    "purple":   [(0.0, (15, 0, 25)),   (0.5, (140, 40, 200)),  (1.0, (220, 180, 255))],
    "pink":     [(0.0, (25, 0, 10)),   (0.5, (240, 80, 150)),  (1.0, (255, 200, 230))],
    "cyan":     [(0.0, (0, 15, 20)),   (0.5, (30, 200, 230)),  (1.0, (180, 245, 255))],
    "brown":    [(0.0, (15, 8, 0)),    (0.5, (140, 80, 30)),   (1.0, (210, 170, 120))],
    "sunset":   [(0.0, (20, 5, 30)),   (0.35,(180, 40, 80)),   (0.65,(240, 130, 30)),  (1.0, (255, 230, 180))],
    "ocean":    [(0.0, (0, 10, 40)),   (0.4, (0, 80, 160)),    (0.7, (0, 160, 200)),   (1.0, (180, 240, 255))],
    "forest":   [(0.0, (5, 20, 5)),    (0.4, (20, 100, 30)),   (0.7, (80, 160, 50)),   (1.0, (200, 240, 160))],
    "fire":     [(0.0, (10, 0, 0)),    (0.3, (180, 20, 0)),    (0.6, (240, 140, 0)),   (1.0, (255, 240, 180))],
    "ice":      [(0.0, (10, 20, 40)),  (0.4, (80, 160, 220)),  (0.75,(180, 220, 245)), (1.0, (240, 250, 255))],
    "sakura":   [(0.0, (30, 5, 15)),   (0.4, (220, 100, 140)), (0.75,(250, 180, 200)), (1.0, (255, 230, 240))],
    "gold":     [(0.0, (20, 15, 0)),   (0.35,(160, 110, 0)),   (0.65,(230, 190, 30)),  (1.0, (255, 245, 180))],
    "galaxy":   [(0.0, (5, 0, 20)),    (0.3, (60, 20, 120)),   (0.6, (120, 60, 200)),  (0.85,(200, 140, 255)), (1.0, (255, 240, 255))],
}


def _build_lut(cmap: list) -> np.ndarray:
    """Build 256-entry RGB lookup table from color map stops."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        # Find surrounding stops
        if t <= cmap[0][0]:
            lut[i] = cmap[0][1]
            continue
        if t >= cmap[-1][0]:
            lut[i] = cmap[-1][1]
            continue
        for j in range(len(cmap) - 1):
            t0, c0 = cmap[j]
            t1, c1 = cmap[j + 1]
            if t0 <= t <= t1:
                ratio = (t - t0) / (t1 - t0)
                lut[i] = (
                    int(c0[0] + ratio * (c1[0] - c0[0])),
                    int(c0[1] + ratio * (c1[1] - c0[1])),
                    int(c0[2] + ratio * (c1[2] - c0[2])),
                )
                break
    return lut


def _hsl_to_rgb(h, s, l):
    """HSL (0-1) to RGB (0-255)."""
    if s == 0:
        v = int(l * 255)
        return v, v, v
    def hue2rgb(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return int(hue2rgb(p, q, h+1/3)*255), int(hue2rgb(p, q, h)*255), int(hue2rgb(p, q, h-1/3)*255)


def apply_color(img: Image.Image, color: str) -> Image.Image:
    img = img.convert("RGBA")
    arr = np.array(img, dtype=np.float32)

    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    # Perceived luminance 0-255
    lum = (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)

    mask = a > 10  # only non-transparent pixels

    if color == "grayscale":
        new_r = np.where(mask, lum, 0).astype(np.uint8)
        new_g = np.where(mask, lum, 0).astype(np.uint8)
        new_b = np.where(mask, lum, 0).astype(np.uint8)

    elif color == "rainbow":
        # Map lum → hue, vectorized via LUT
        rainbow_lut_r = np.zeros(256, dtype=np.uint8)
        rainbow_lut_g = np.zeros(256, dtype=np.uint8)
        rainbow_lut_b = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            h = i / 255.0
            sat = 0.9 - h * 0.3
            light = 0.25 + h * 0.5
            rv, gv, bv = _hsl_to_rgb(h, sat, light)
            rainbow_lut_r[i] = rv
            rainbow_lut_g[i] = gv
            rainbow_lut_b[i] = bv
        new_r = np.where(mask, rainbow_lut_r[lum], 0).astype(np.uint8)
        new_g = np.where(mask, rainbow_lut_g[lum], 0).astype(np.uint8)
        new_b = np.where(mask, rainbow_lut_b[lum], 0).astype(np.uint8)

    elif color == "random":
        hue = random.random()
        dark  = _hsl_to_rgb(hue, 0.9, 0.15)
        mid   = _hsl_to_rgb(hue, 0.85, 0.50)
        light = _hsl_to_rgb(hue, 0.5, 0.85)
        cmap = [(0.0, dark), (0.5, mid), (1.0, light)]
        lut = _build_lut(cmap)
        new_r = np.where(mask, lut[lum, 0], 0).astype(np.uint8)
        new_g = np.where(mask, lut[lum, 1], 0).astype(np.uint8)
        new_b = np.where(mask, lut[lum, 2], 0).astype(np.uint8)

    else:
        cmap = COLOR_MAPS.get(color, COLOR_MAPS["blue"])
        lut = _build_lut(cmap)
        new_r = np.where(mask, lut[lum, 0], 0).astype(np.uint8)
        new_g = np.where(mask, lut[lum, 1], 0).astype(np.uint8)
        new_b = np.where(mask, lut[lum, 2], 0).astype(np.uint8)

    result = np.stack([new_r, new_g, new_b, a.astype(np.uint8)], axis=-1)
    return Image.fromarray(result, "RGBA")
        
