"""
colorizer.py — Core image colorization engine for sticker bot.

Supports:
- Solid hue shifts (red, green, blue, etc.)
- Gradient color maps (sunset, ocean, fire, etc.)
- Rainbow mode
- Grayscale / B&W
- Special effects: galaxy, sakura, ice, gold
- Random color
"""

import random
import math
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np


# ──────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────

def to_numpy(img: Image.Image) -> np.ndarray:
    return np.array(img, dtype=np.float32)


def from_numpy(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def luminance(r, g, b):
    """Perceived luminance (0–1)."""
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def hsl_to_rgb(h, s, l):
    """Convert HSL (0–1 range each) to RGB (0–255)."""
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
    r = hue2rgb(p, q, h + 1/3)
    g = hue2rgb(p, q, h)
    b = hue2rgb(p, q, h - 1/3)
    return int(r * 255), int(g * 255), int(b * 255)


# ──────────────────────────────────────────────────────────────
# Color map definitions
# Each entry: list of (luminance_threshold, (R, G, B)) tuples
# Pixels are mapped based on their brightness
# ──────────────────────────────────────────────────────────────

COLOR_MAPS = {
    # ── Solid hue tints ──
    "red":      [(0.0, (20, 0, 0)),    (0.5, (220, 50, 50)),   (1.0, (255, 200, 200))],
    "orange":   [(0.0, (25, 8, 0)),    (0.5, (230, 120, 20)),  (1.0, (255, 220, 160))],
    "yellow":   [(0.0, (30, 25, 0)),   (0.5, (240, 210, 30)),  (1.0, (255, 255, 180))],
    "green":    [(0.0, (0, 20, 0)),    (0.5, (40, 180, 60)),   (1.0, (180, 255, 180))],
    "blue":     [(0.0, (0, 0, 30)),    (0.5, (40, 80, 220)),   (1.0, (180, 200, 255))],
    "purple":   [(0.0, (15, 0, 25)),   (0.5, (140, 40, 200)),  (1.0, (220, 180, 255))],
    "pink":     [(0.0, (25, 0, 10)),   (0.5, (240, 80, 150)),  (1.0, (255, 200, 230))],
    "cyan":     [(0.0, (0, 15, 20)),   (0.5, (30, 200, 230)),  (1.0, (180, 245, 255))],
    "brown":    [(0.0, (15, 8, 0)),    (0.5, (140, 80, 30)),   (1.0, (210, 170, 120))],

    # ── Gradient themes ──
    "sunset":   [(0.0, (20, 5, 30)),   (0.35, (180, 40, 80)),  (0.65, (240, 130, 30)), (1.0, (255, 230, 180))],
    "ocean":    [(0.0, (0, 10, 40)),   (0.4, (0, 80, 160)),    (0.7, (0, 160, 200)),   (1.0, (180, 240, 255))],
    "forest":   [(0.0, (5, 20, 5)),    (0.4, (20, 100, 30)),   (0.7, (80, 160, 50)),   (1.0, (200, 240, 160))],
    "fire":     [(0.0, (10, 0, 0)),    (0.3, (180, 20, 0)),    (0.6, (240, 140, 0)),   (1.0, (255, 240, 180))],
    "ice":      [(0.0, (10, 20, 40)),  (0.4, (80, 160, 220)),  (0.75, (180, 220, 245)),(1.0, (240, 250, 255))],
    "sakura":   [(0.0, (30, 5, 15)),   (0.4, (220, 100, 140)), (0.75, (250, 180, 200)),(1.0, (255, 230, 240))],
    "gold":     [(0.0, (20, 15, 0)),   (0.35, (160, 110, 0)),  (0.65, (230, 190, 30)), (1.0, (255, 245, 180))],
    "galaxy":   [(0.0, (5, 0, 20)),    (0.3, (60, 20, 120)),   (0.6, (120, 60, 200)),  (0.85, (200, 140, 255)), (1.0, (255, 240, 255))],
}


def interpolate_color_map(lum: float, cmap: list) -> tuple:
    """Linearly interpolate between color stops based on luminance."""
    # cmap is list of (threshold, (R,G,B))
    if lum <= cmap[0][0]:
        return cmap[0][1]
    if lum >= cmap[-1][0]:
        return cmap[-1][1]
    for i in range(len(cmap) - 1):
        t0, c0 = cmap[i]
        t1, c1 = cmap[i + 1]
        if t0 <= lum <= t1:
            ratio = (lum - t0) / (t1 - t0)
            r = int(c0[0] + ratio * (c1[0] - c0[0]))
            g = int(c0[1] + ratio * (c1[1] - c0[1]))
            b = int(c0[2] + ratio * (c1[2] - c0[2]))
            return r, g, b
    return cmap[-1][1]


# ──────────────────────────────────────────────────────────────
# Grayscale
# ──────────────────────────────────────────────────────────────

def apply_grayscale(img: Image.Image) -> Image.Image:
    arr = to_numpy(img)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    lum = (0.299 * r + 0.587 * g + 0.114 * b)
    result = np.stack([lum, lum, lum, a], axis=-1)
    return from_numpy(result)


# ──────────────────────────────────────────────────────────────
# Rainbow mode
# ──────────────────────────────────────────────────────────────

def apply_rainbow(img: Image.Image) -> Image.Image:
    arr = to_numpy(img)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    # Map luminance to hue cycle (full rainbow)
    hue = lum  # 0=red → 0.33=green → 0.66=blue → 1=red

    new_r = np.zeros_like(r)
    new_g = np.zeros_like(g)
    new_b = np.zeros_like(b)

    for y in range(arr.shape[0]):
        for x in range(arr.shape[1]):
            if a[y, x] > 10:  # skip transparent
                h = lum[y, x]
                # Saturation varies: brightest pixels stay slightly pastel
                sat = 0.9 - lum[y, x] * 0.3
                light = 0.25 + lum[y, x] * 0.5
                nr, ng, nb = hsl_to_rgb(h, sat, light)
                new_r[y, x] = nr
                new_g[y, x] = ng
                new_b[y, x] = nb

    result = np.stack([new_r, new_g, new_b, a], axis=-1)
    return from_numpy(result)


# ──────────────────────────────────────────────────────────────
# Random color
# ──────────────────────────────────────────────────────────────

def apply_random(img: Image.Image) -> Image.Image:
    hue = random.random()
    # Build a random gradient color map
    dark = hsl_to_rgb(hue, 0.9, 0.15)
    mid = hsl_to_rgb(hue, 0.85, 0.50)
    light = hsl_to_rgb(hue, 0.5, 0.85)
    cmap = [(0.0, dark), (0.5, mid), (1.0, light)]
    return apply_color_map(img, cmap)


# ──────────────────────────────────────────────────────────────
# Generic color map application
# ──────────────────────────────────────────────────────────────

def apply_color_map(img: Image.Image, cmap: list) -> Image.Image:
    arr = to_numpy(img)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    lum_map = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    new_r = np.zeros_like(r)
    new_g = np.zeros_like(g)
    new_b = np.zeros_like(b)

    for y in range(arr.shape[0]):
        for x in range(arr.shape[1]):
            if a[y, x] > 10:
                lum = float(lum_map[y, x])
                nr, ng, nb = interpolate_color_map(lum, cmap)
                new_r[y, x] = nr
                new_g[y, x] = ng
                new_b[y, x] = nb

    result = np.stack([new_r, new_g, new_b, a], axis=-1)
    return from_numpy(result)


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────

def apply_color(img: Image.Image, color: str) -> Image.Image:
    """
    Apply a named color effect to an RGBA image.
    Returns a new RGBA image.
    """
    img = img.convert("RGBA")

    if color == "grayscale":
        return apply_grayscale(img)

    if color == "rainbow":
        return apply_rainbow(img)

    if color == "random":
        return apply_random(img)

    if color in COLOR_MAPS:
        return apply_color_map(img, COLOR_MAPS[color])

    # Fallback: treat as grayscale
    return apply_grayscale(img)
                   
