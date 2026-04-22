"""
bot.py — Telegram Sticker Colorizer Bot
Supports: static stickers, animated stickers (.tgs), video stickers, photos
Creates personal sticker packs per user, auto-creates new pack when full.
Uses premium emoji in all messages, buttons and keyboards.
"""

import logging
import io
import os
import asyncio

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputSticker, KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from PIL import Image

import colorizer
from tgs_colorizer import colorize_tgs

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MAX_STICKERS_PER_PACK = 120


# ──────────────────────────────────────────────────────────────
# Premium emoji helper
# ──────────────────────────────────────────────────────────────

def e(emoji_id: str, fallback: str = "•") -> str:
    """Wrap emoji_id in tg-emoji tag for use in HTML messages."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

# Shortcut constants
E_SETTINGS    = e("5870982283724328568", "⚙")
E_PROFILE     = e("5870994129244131212", "👤")
E_CHECK       = e("5870633910337015697", "✅")
E_CROSS       = e("5870657884844462243", "❌")
E_STICKER     = e("6035128606563241721", "🖼")
E_ANIM        = e("5778672437122045013", "📦")
E_VIDEO       = e("5770240908630974872", "🎬")
E_PACK        = e("5884479287171485878", "📦")
E_PAINT       = e("6050679691004612757", "🖌")
E_LINK        = e("5769289093221454192", "🔗")
E_INFO        = e("6028435952299413210", "ℹ")
E_BOT         = e("6030400221232501136", "🤖")
E_GIFT        = e("6032644646587338669", "🎁")
E_CLOCK       = e("5983150113483134607", "⏰")
E_PARTY       = e("6041731551845159060", "🎉")
E_PENCIL      = e("5870676941614354370", "🖋")
E_DOWN        = e("6039802767931871481", "⬇")
E_UP          = e("5963103826075456248", "⬆")
E_TRASH       = e("5870875489362513438", "🗑")
E_BACK        = e("5893057118545646106", "◁")
E_NOTIFY      = e("6039486778597970865", "🔔")
E_SMILE       = e("5870764288364252592", "🙂")
E_STATS       = e("5870921681735781843", "📊")
E_LOAD        = e("5345906554510012647", "🔄")
E_TAG         = e("5886285355279193209", "🏷")
E_CALENDAR    = e("5890937706803894250", "📅")


# ──────────────────────────────────────────────────────────────
# Color palette
# ──────────────────────────────────────────────────────────────

# (label, color_key, emoji_id, fallback)
COLOR_PRESETS = [
    ("Красный",    "red",       "5870657884844462243", "🔴"),
    ("Оранжевый",  "orange",    "5870657884844462243", "🟠"),
    ("Жёлтый",     "yellow",    "5870633910337015697", "🟡"),
    ("Зелёный",    "green",     "5870633910337015697", "🟢"),
    ("Синий",      "blue",      "5770240908630974872", "🔵"),
    ("Фиолетовый", "purple",    "5884479287171485878", "🟣"),
    ("Розовый",    "pink",      "6032644646587338669", "🩷"),
    ("Голубой",    "cyan",      "6028435952299413210", "🩵"),
    ("Коричневый", "brown",     "5886285355279193209", "🤎"),
    ("Ч/Б",        "grayscale", "6037249452824072506", "🖤"),
    ("Радуга",     "rainbow",   "6041731551845159060", "🌈"),
    ("Случайный",  "random",    "5870982283724328568", "✨"),
    ("Закат",      "sunset",    "6050679691004612757", "🌅"),
    ("Океан",      "ocean",     "5769289093221454192", "🌊"),
    ("Лес",        "forest",    "5870633910337015697", "🌿"),
    ("Огонь",      "fire",      "5870657884844462243", "🔥"),
    ("Лёд",        "ice",       "6028435952299413210", "❄️"),
    ("Сакура",     "sakura",    "6032644646587338669", "🌸"),
    ("Галактика",  "galaxy",    "5884479287171485878", "🪐"),
    ("Золото",     "gold",      "5769289093221454192", "☀️"),
]

# pending stickers: {user_id: {"data": bytes, "type": str, "emoji": str}}
user_pending = {}


# ──────────────────────────────────────────────────────────────
# Keyboards
# ──────────────────────────────────────────────────────────────

def make_color_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with premium emoji icons for each color."""
    buttons = []
    for i in range(0, len(COLOR_PRESETS), 2):
        row = []
        label, key, emoji_id, _ = COLOR_PRESETS[i]
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"color:{key}",
            icon_custom_emoji_id=emoji_id
        ))
        if i + 1 < len(COLOR_PRESETS):
            label2, key2, emoji_id2, _ = COLOR_PRESETS[i + 1]
            row.append(InlineKeyboardButton(
                text=label2,
                callback_data=f"color:{key2}",
                icon_custom_emoji_id=emoji_id2
            ))
        buttons.append(row)

    # "All colors" button
    buttons.append([InlineKeyboardButton(
        text="Все 20 цветов → в пак",
        callback_data="color:all_pack",
        icon_custom_emoji_id="6050679691004612757"  # paint brush
    )])
    return InlineKeyboardMarkup(buttons)


def make_pack_link_keyboard(pack_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="Открыть пак",
            url=f"https://t.me/addstickers/{pack_name}",
            icon_custom_emoji_id="5769289093221454192"  # link
        )
    ]])


def make_main_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard — plain text buttons (icon_custom_emoji_id not supported here)."""
    return ReplyKeyboardMarkup(
        keyboard=[["📦 Мои паки", "ℹ️ Помощь"]],
        resize_keyboard=True,
        input_field_placeholder="Отправь стикер для раскраски 🎨"
    )


# ──────────────────────────────────────────────────────────────
# Pack management
# ──────────────────────────────────────────────────────────────

def pack_registry_key(user_id: int, stype: str) -> str:
    return f"pack_{user_id}_{stype}"


def build_pack_name(user_id: int, bot_username: str, index: int, stype: str) -> str:
    suffix = {"animated": "a", "video": "v", "static": "s"}.get(stype, "s")
    idx_part = "" if index == 1 else str(index)
    return f"u{user_id}_{suffix}{idx_part}_by_{bot_username}"


def build_pack_title(user_id: int, index: int, stype: str) -> str:
    label = {"animated": "Анимации", "video": "Видео", "static": "Стикеры"}.get(stype, "Стикеры")
    idx_part = f" · {index}" if index > 1 else ""
    return f"🎨 {label} {user_id}{idx_part}"


async def find_current_pack(context: ContextTypes.DEFAULT_TYPE, user_id: int, stype: str) -> dict:
    bot_username = (await context.bot.get_me()).username
    key = pack_registry_key(user_id, stype)
    if key not in context.bot_data:
        context.bot_data[key] = {"index": 1}

    index = context.bot_data[key]["index"]
    pack_name = build_pack_name(user_id, bot_username, index, stype)
    pack_title = build_pack_title(user_id, index, stype)

    try:
        pack = await context.bot.get_sticker_set(pack_name)
        count = len(pack.stickers)
        if count >= MAX_STICKERS_PER_PACK:
            index += 1
            context.bot_data[key]["index"] = index
            pack_name = build_pack_name(user_id, bot_username, index, stype)
            pack_title = build_pack_title(user_id, index, stype)
            return {"name": pack_name, "title": pack_title, "exists": False, "count": 0}
        return {"name": pack_name, "title": pack_title, "exists": True, "count": count}
    except TelegramError:
        return {"name": pack_name, "title": pack_title, "exists": False, "count": 0}


async def add_to_pack(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    sticker_bytes: bytes,
    stype: str,
    emoji: str = "🎨"
) -> str:
    pack_info = await find_current_pack(context, user_id, stype)
    fmt = {"animated": "animated", "video": "video", "static": "static"}.get(stype, "static")
    ext = {"animated": "sticker.tgs", "video": "sticker.webm", "static": "sticker.png"}.get(stype, "sticker.png")

    buf = io.BytesIO(sticker_bytes)
    buf.name = ext
    sticker = InputSticker(sticker=buf, emoji_list=[emoji], format=fmt)

    if not pack_info["exists"]:
        await context.bot.create_new_sticker_set(
            user_id=user_id,
            name=pack_info["name"],
            title=pack_info["title"],
            stickers=[sticker],
        )
        logger.info(f"Created pack {pack_info['name']} for user {user_id}")
    else:
        await context.bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_info["name"],
            sticker=sticker,
        )
    return pack_info["name"]


# ──────────────────────────────────────────────────────────────
# Image processing
# ──────────────────────────────────────────────────────────────

def process_static(data: bytes, color: str) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    colored = colorizer.apply_color(img, color)
    w, h = colored.size
    longest = max(w, h)
    if longest != 512:
        factor = 512 / longest
        colored = colored.resize((int(w * factor), int(h * factor)), Image.LANCZOS)
    out = io.BytesIO()
    colored.save(out, format="PNG")
    return out.getvalue()


async def colorize_and_add(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    pending: dict,
    color: str
) -> str:
    stype = pending["type"]
    emoji = pending.get("emoji", "🎨")
    data = pending["data"]
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
    return a + (b - a)
