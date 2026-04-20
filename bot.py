#!/usr/bin/env python3
"""
Sticker & Emoji Recolor Bot
Python 3.8+, aiogram 3.7.0+, SQLite
"""

import os
import io
import re
import json
import gzip
import struct
import asyncio
import logging
import sqlite3
import tempfile
import shutil
import colorsys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass, field

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputSticker, StickerSet, Sticker,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode, StickerFormat
from aiogram.client.default import DefaultBotProperties

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TEMP_DIR = Path(tempfile.gettempdir()) / "recolor_bot"
TEMP_DIR.mkdir(exist_ok=True)

# ─── Database ───────────────────────────────────────────────────────────────

class Database:
    def __init__(self, db_path: str = "bot_database.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        c = self.conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_banned INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                first_seen TEXT DEFAULT (datetime('now')),
                last_active TEXT DEFAULT (datetime('now')),
                total_recolors INTEGER DEFAULT 0,
                total_packs_created INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS recolor_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT,
                sticker_set_name TEXT,
                color_hex TEXT,
                items_count INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS admin_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target_user_id INTEGER,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message_text TEXT,
                recipients_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self.conn.commit()

    def upsert_user(self, user_id: int, username: str = None, first_name: str = None,
                    last_name: str = None, language_code: str = None, is_premium: bool = False):
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, language_code, is_premium, last_active)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                language_code=excluded.language_code,
                is_premium=excluded.is_premium,
                last_active=datetime('now')
        """, (user_id, username, first_name, last_name, language_code, int(is_premium)))
        self.conn.commit()

    def is_banned(self, user_id: int) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        return bool(row and row["is_banned"])

    def ban_user(self, user_id: int):
        c = self.conn.cursor()
        c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def unban_user(self, user_id: int):
        c = self.conn.cursor()
        c.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def increment_recolors(self, user_id: int, count: int = 1):
        c = self.conn.cursor()
        c.execute("UPDATE users SET total_recolors = total_recolors + ? WHERE user_id=?", (count, user_id))
        self.conn.commit()

    def increment_packs(self, user_id: int):
        c = self.conn.cursor()
        c.execute("UPDATE users SET total_packs_created = total_packs_created + 1 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def add_recolor_history(self, user_id: int, action_type: str, sticker_set_name: str = None,
                            color_hex: str = None, items_count: int = 1):
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO recolor_history (user_id, action_type, sticker_set_name, color_hex, items_count)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, action_type, sticker_set_name, color_hex, items_count))
        self.conn.commit()

    def add_admin_log(self, admin_id: int, action: str, target_user_id: int = None, details: str = None):
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO admin_log (admin_id, action, target_user_id, details)
            VALUES (?, ?, ?, ?)
        """, (admin_id, action, target_user_id, details))
        self.conn.commit()

    def get_stats(self) -> dict:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM users WHERE is_banned=1")
        banned_users = c.fetchone()["cnt"]
        c.execute("SELECT COALESCE(SUM(total_recolors),0) as cnt FROM users")
        total_recolors = c.fetchone()["cnt"]
        c.execute("SELECT COALESCE(SUM(total_packs_created),0) as cnt FROM users")
        total_packs = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM users WHERE last_active >= datetime('now', '-1 day')")
        active_today = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM users WHERE last_active >= datetime('now', '-7 day')")
        active_week = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM users WHERE is_premium=1")
        premium_users = c.fetchone()["cnt"]
        return {
            "total_users": total_users,
            "banned_users": banned_users,
            "total_recolors": total_recolors,
            "total_packs": total_packs,
            "active_today": active_today,
            "active_week": active_week,
            "premium_users": premium_users,
        }

    def get_all_user_ids(self, exclude_banned: bool = True) -> List[int]:
        c = self.conn.cursor()
        if exclude_banned:
            c.execute("SELECT user_id FROM users WHERE is_banned=0")
        else:
            c.execute("SELECT user_id FROM users")
        return [row["user_id"] for row in c.fetchall()]

    def get_user_info(self, user_id: int) -> Optional[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        if row:
            return dict(row)
        return None

    def get_top_users(self, limit: int = 10) -> List[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM users ORDER BY total_recolors DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

    def get_recent_actions(self, limit: int = 20) -> List[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM recolor_history ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

    def get_admin_logs(self, limit: int = 20) -> List[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM admin_log ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

    def search_users(self, query: str) -> List[dict]:
        c = self.conn.cursor()
        like = f"%{query}%"
        c.execute("""
            SELECT * FROM users
            WHERE username LIKE ? OR first_name LIKE ? OR CAST(user_id AS TEXT) LIKE ?
            LIMIT 20
        """, (like, like, like))
        return [dict(r) for r in c.fetchall()]

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = c.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def add_broadcast(self, admin_id: int, text: str, recipients: int, success: int, fail: int):
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO broadcast_history (admin_id, message_text, recipients_count, success_count, fail_count)
            VALUES (?, ?, ?, ?, ?)
        """, (admin_id, text, recipients, success, fail))
        self.conn.commit()

    def get_user_history(self, user_id: int, limit: int = 10, offset: int = 0) -> List[dict]:
        c = self.conn.cursor()
        c.execute(
            "SELECT * FROM recolor_history WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset)
        )
        return [dict(r) for r in c.fetchall()]

    def get_history_count(self, user_id: int) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM recolor_history WHERE user_id=?", (user_id,))
        return c.fetchone()["cnt"]


db = Database()

# ─── Color Processing ───────────────────────────────────────────────────────

PRESET_COLORS = {
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "orange": "#FF8C00",
    "purple": "#8B00FF",
    "pink": "#FF69B4",
    "cyan": "#00FFFF",
    "white": "#FFFFFF",
    "black": "#000000",
    "gold": "#FFD700",
    "lime": "#32CD32",
    "teal": "#008080",
    "magenta": "#FF00FF",
    "coral": "#FF7F50",
    "navy": "#000080",
    "maroon": "#800000",
    "olive": "#808000",
    "salmon": "#FA8072",
    "violet": "#EE82EE",
}

PRESET_COLORS_RU = {
    "красный": "#FF0000",
    "зеленый": "#00FF00",
    "зелёный": "#00FF00",
    "синий": "#0000FF",
    "жёлтый": "#FFFF00",
    "желтый": "#FFFF00",
    "оранжевый": "#FF8C00",
    "фиолетовый": "#8B00FF",
    "розовый": "#FF69B4",
    "голубой": "#00FFFF",
    "белый": "#FFFFFF",
    "чёрный": "#000000",
    "черный": "#000000",
    "золотой": "#FFD700",
    "бирюзовый": "#008080",
    "пурпурный": "#FF00FF",
    "коралловый": "#FF7F50",
}


def parse_color(text: str) -> Optional[str]:
    """Parse color from text, return HEX string like #RRGGBB or None."""
    text = text.strip().lower()
    if text in PRESET_COLORS:
        return PRESET_COLORS[text]
    if text in PRESET_COLORS_RU:
        return PRESET_COLORS_RU[text]
    # HEX
    match = re.match(r'^#?([0-9a-fA-F]{6})$', text)
    if match:
        return f"#{match.group(1).upper()}"
    match = re.match(r'^#?([0-9a-fA-F]{3})$', text)
    if match:
        h = match.group(1)
        expanded = ''.join([c * 2 for c in h])
        return f"#{expanded.upper()}"
    return None


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_to_hsl(r: int, g: int, b: int) -> Tuple[float, float, float]:
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return h, s, l


def hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


# ─── TGS (Lottie) Recoloring ────────────────────────────────────────────────

def recolor_lottie_value(obj: Any, target_rgb: Tuple[int, int, int], depth: int = 0) -> Any:
    """
    Recursively traverse Lottie JSON and recolor all color properties.
    Colors in Lottie are stored as arrays [r, g, b] or [r, g, b, a] with values 0-1.
    """
    if depth > 100:
        return obj

    tr, tg, tb = target_rgb
    target_r, target_g, target_b = tr / 255.0, tg / 255.0, tb / 255.0

    if isinstance(obj, dict):
        # Check if this is a color property
        # In Lottie, colors appear in "c" (color) with "k" containing the value
        # Also in shapes with "ty": "fl" (fill) or "ty": "st" (stroke)
        new_dict = {}
        for key, value in obj.items():
            if key == "k" and _is_color_array(value, obj):
                new_dict[key] = _recolor_keyframes_or_static(value, target_r, target_g, target_b)
            else:
                new_dict[key] = recolor_lottie_value(value, target_rgb, depth + 1)
        return new_dict
    elif isinstance(obj, list):
        return [recolor_lottie_value(item, target_rgb, depth + 1) for item in obj]
    return obj


def _is_color_array(value: Any, parent: dict) -> bool:
    """Check if a 'k' value in parent dict is a color."""
    # Parent should have "a" key (animated flag) and be inside a color context
    # Color contexts: parent has key "ty" not present but is child of fill/stroke
    # Or parent is a "c" dict
    # Simplification: check if value looks like color data
    if isinstance(value, list) and len(value) in (3, 4):
        if all(isinstance(v, (int, float)) for v in value):
            if all(0 <= v <= 1.0 for v in value[:3]):
                return True
    # Animated color: list of keyframes
    if isinstance(value, list) and len(value) > 0:
        if isinstance(value[0], dict) and "s" in value[0]:
            s = value[0]["s"]
            if isinstance(s, list) and len(s) in (3, 4):
                if all(isinstance(v, (int, float)) for v in s[:3]):
                    if all(0 <= v <= 1.0 for v in s[:3]):
                        return True
    return False


def _recolor_keyframes_or_static(value: Any, r: float, g: float, b: float) -> Any:
    """Recolor static color or animated keyframes."""
    if isinstance(value, list):
        if len(value) in (3, 4) and all(isinstance(v, (int, float)) for v in value):
            # Static color
            alpha = value[3] if len(value) == 4 else 1.0
            # Preserve luminance relationship
            orig_h, orig_s, orig_l = rgb_to_hsl(
                int(value[0] * 255), int(value[1] * 255), int(value[2] * 255)
            )
            target_h, target_s, target_l = rgb_to_hsl(
                int(r * 255), int(g * 255), int(b * 255)
            )
            # Use target hue and saturation, but blend luminance
            new_r, new_g, new_b = hsl_to_rgb(target_h, target_s, orig_l)
            result = [new_r / 255.0, new_g / 255.0, new_b / 255.0]
            if len(value) == 4:
                result.append(alpha)
            return result
        elif len(value) > 0 and isinstance(value[0], dict):
            # Animated keyframes
            new_kf = []
            for kf in value:
                new_kf_item = dict(kf)
                if "s" in kf and isinstance(kf["s"], list) and len(kf["s"]) in (3, 4):
                    s = kf["s"]
                    alpha = s[3] if len(s) == 4 else 1.0
                    orig_h, orig_s_val, orig_l = rgb_to_hsl(
                        int(s[0] * 255), int(s[1] * 255), int(s[2] * 255)
                    )
                    target_h, target_s_val, target_l = rgb_to_hsl(
                        int(r * 255), int(g * 255), int(b * 255)
                    )
                    nr, ng, nb = hsl_to_rgb(target_h, target_s_val, orig_l)
                    new_s = [nr / 255.0, ng / 255.0, nb / 255.0]
                    if len(s) == 4:
                        new_s.append(alpha)
                    new_kf_item["s"] = new_s
                if "e" in kf and isinstance(kf["e"], list) and len(kf["e"]) in (3, 4):
                    e = kf["e"]
                    alpha = e[3] if len(e) == 4 else 1.0
                    orig_h, orig_s_val, orig_l = rgb_to_hsl(
                        int(e[0] * 255), int(e[1] * 255), int(e[2] * 255)
                    )
                    target_h, target_s_val, target_l = rgb_to_hsl(
                        int(r * 255), int(g * 255), int(b * 255)
                    )
                    nr, ng, nb = hsl_to_rgb(target_h, target_s_val, orig_l)
                    new_e = [nr / 255.0, ng / 255.0, nb / 255.0]
                    if len(e) == 4:
                        new_e.append(alpha)
                    new_kf_item["e"] = new_e
                new_kf.append(new_kf_item)
            return new_kf
    return value


def recolor_tgs_data(tgs_bytes: bytes, target_hex: str) -> bytes:
    """Decompress TGS (gzip Lottie JSON), recolor, recompress."""
    target_rgb = hex_to_rgb(target_hex)
    json_data = gzip.decompress(tgs_bytes)
    lottie = json.loads(json_data)
    recolored = recolor_lottie_value(lottie, target_rgb)
    new_json = json.dumps(recolored, separators=(',', ':'))
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        gz.write(new_json.encode('utf-8'))
    return buf.getvalue()


# ─── WEBM Recoloring (basic approach: we can't easily recolor WEBM without ffmpeg) ──

def can_recolor_format(sticker_format: str) -> bool:
    """Check if we support recoloring this format."""
    return sticker_format in ("animated",)  # TGS only for reliable recoloring


# ─── FSM States ──────────────────────────────────────────────────────────────

class RecolorStates(StatesGroup):
    waiting_sticker = State()
    choose_action = State()       # single or whole pack
    choose_color = State()
    custom_color_input = State()
    waiting_pack_name = State()
    processing = State()


class AdminStates(StatesGroup):
    broadcast_text = State()
    ban_user_id = State()
    unban_user_id = State()
    search_user = State()
    user_info_id = State()
    set_setting_key = State()
    set_setting_value = State()


# ─── Session data ────────────────────────────────────────────────────────────

@dataclass
class RecolorSession:
    user_id: int = 0
    sticker_file_id: str = ""
    sticker_set_name: str = ""
    sticker_format: str = ""  # "animated" for TGS
    is_custom_emoji: bool = False
    action: str = ""  # "single" or "pack"
    color_hex: str = ""
    pack_title: str = ""
    pack_name_suffix: str = ""


# ─── Routers ─────────────────────────────────────────────────────────────────

main_router = Router()
admin_router = Router()

# ─── Emoji IDs ───────────────────────────────────────────────────────────────

EMOJI = {
    "settings": "5870982283724328568",
    "profile": "5870994129244131212",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "trash": "5870875489362513438",
    "pencil": "5870676941614354370",
    "stats": "5870921681735781843",
    "growth": "5870930636742595124",
    "info": "6028435952299413210",
    "bot": "6030400221232501136",
    "back": "5345906554510012347",
    "gift": "6032644646587338669",
    "bell": "6039486778597970865",
    "send": "5963103826075456248",
    "download": "6039802767931871481",
    "media": "6035128606563241721",
    "brush": "6050679691004612757",
    "wallet": "5769126056262898415",
    "lock": "6037249452824072506",
    "unlock": "6037496202990194718",
    "eye": "6037397706505195857",
    "hidden": "6037243349675544634",
    "people": "5870772616305839506",
    "smile": "5870764288364252592",
    "link": "5769289093221454192",
    "clock": "5983150113483134607",
    "calendar": "5890937706803894250",
    "tag": "5886285355279193209",
    "money": "5904462880941545555",
    "code": "5940433880585605708",
    "loading": "5345906554510012647",
    "horn": "6039422865189638057",
    "party": "6041731551845159060",
    "box": "5884479287171485878",
    "file": "5870528606328852614",
    "write": "5870753782874246579",
    "color_text": "5771851822897566479",
    "apps": "5778672437122045013",
    "home": "5873147866364514353",
}


def em(emoji_id: str, fallback: str = "") -> str:
    """Create premium emoji HTML tag."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# ─── Keyboards ───────────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Перекрасить стикер/эмодзи",
                callback_data="start_recolor",
                style="primary",
                icon_custom_emoji_id=EMOJI["brush"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Мои перекраски",
                callback_data="my_history",
                style="success",
                icon_custom_emoji_id=EMOJI["calendar"]
            ),
            InlineKeyboardButton(
                text="Помощь",
                callback_data="help",
                icon_custom_emoji_id=EMOJI["info"]
            )
        ],
    ])


def action_choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Один стикер",
                callback_data="action_single",
                style="primary",
                icon_custom_emoji_id=EMOJI["media"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Весь пак",
                callback_data="action_pack",
                style="success",
                icon_custom_emoji_id=EMOJI["box"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data="cancel",
                style="danger",
                icon_custom_emoji_id=EMOJI["cross"]
            )
        ],
    ])


def color_choice_kb() -> InlineKeyboardMarkup:
    colors = [
        ("🔴 Красный", "color_#FF0000", "danger"),
        ("🟢 Зелёный", "color_#00FF00", "success"),
        ("🔵 Синий", "color_#0000FF", "primary"),
        ("🟡 Жёлтый", "color_#FFFF00", None),
        ("🟠 Оранжевый", "color_#FF8C00", None),
        ("🟣 Фиолетовый", "color_#8B00FF", None),
        ("💗 Розовый", "color_#FF69B4", None),
        ("🩵 Голубой", "color_#00FFFF", None),
        ("⚫ Чёрный", "color_#000000", None),
        ("⚪ Белый", "color_#FFFFFF", None),
        ("🥇 Золотой", "color_#FFD700", None),
        ("🩷 Коралловый", "color_#FF7F50", None),
    ]
    rows = []
    for i in range(0, len(colors), 3):
        row = []
        for name, data, style in colors[i:i + 3]:
            btn_kwargs = {"text": name, "callback_data": data}
            if style:
                btn_kwargs["style"] = style
            row.append(InlineKeyboardButton(**btn_kwargs))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="Свой цвет (HEX)",
            callback_data="color_custom",
            style="primary",
            icon_custom_emoji_id=EMOJI["brush"]
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="Отмена",
            callback_data="cancel",
            style="danger",
            icon_custom_emoji_id=EMOJI["cross"]
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отмена",
            callback_data="cancel",
            style="danger",
            icon_custom_emoji_id=EMOJI["cross"]
        )]
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Статистика",
                callback_data="admin_stats",
                style="primary",
                icon_custom_emoji_id=EMOJI["stats"]
            ),
            InlineKeyboardButton(
                text="Пользователи",
                callback_data="admin_users",
                icon_custom_emoji_id=EMOJI["people"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Рассылка",
                callback_data="admin_broadcast",
                style="success",
                icon_custom_emoji_id=EMOJI["horn"]
            ),
            InlineKeyboardButton(
                text="Топ юзеров",
                callback_data="admin_top",
                icon_custom_emoji_id=EMOJI["growth"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Бан",
                callback_data="admin_ban",
                style="danger",
                icon_custom_emoji_id=EMOJI["lock"]
            ),
            InlineKeyboardButton(
                text="Разбан",
                callback_data="admin_unban",
                style="success",
                icon_custom_emoji_id=EMOJI["unlock"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Поиск юзера",
                callback_data="admin_search",
                icon_custom_emoji_id=EMOJI["eye"]
            ),
            InlineKeyboardButton(
                text="Инфо о юзере",
                callback_data="admin_userinfo",
                icon_custom_emoji_id=EMOJI["profile"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Логи админов",
                callback_data="admin_logs",
                icon_custom_emoji_id=EMOJI["file"]
            ),
            InlineKeyboardButton(
                text="Последние действия",
                callback_data="admin_recent",
                icon_custom_emoji_id=EMOJI["clock"]
            )
        ],
        [
            InlineKeyboardButton(
                text="Настройки бота",
                callback_data="admin_settings",
                icon_custom_emoji_id=EMOJI["settings"]
            )
        ],
    ])


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_back",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def download_sticker_file(bot: Bot, file_id: str) -> bytes:
    """Download a file from Telegram by file_id."""
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)
    return buf.getvalue()


def sanitize_pack_name(name: str) -> str:
    """Sanitize pack name suffix for Telegram requirements."""
    name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    if not name:
        name = "pack"
    if name[0].isdigit():
        name = "p" + name
    return name[:40]


async def get_sticker_format_type(sticker: Sticker) -> str:
    """Determine sticker format."""
    if sticker.is_animated:
        return "animated"
    elif sticker.is_video:
        return "video"
    else:
        return "static"


# ─── Main Handlers ──────────────────────────────────────────────────────────

@main_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = message.from_user
    db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_premium=bool(user.is_premium)
    )

    if db.is_banned(user.id):
        await message.answer(
            f"{em(EMOJI['lock'], '🔒')} Вы заблокированы.",
            parse_mode=ParseMode.HTML
        )
        return

    await state.clear()
    text = (
        f"{em(EMOJI['party'], '🎉')} <b>Добро пожаловать в Recolor Bot!</b>\n\n"
        f"{em(EMOJI['brush'], '🖌')} Я могу перекрасить ваши <b>TGS стикеры</b> и "
        f"<b>анимированные эмодзи</b> в любой цвет!\n\n"
        f"{em(EMOJI['info'], 'ℹ️')} <b>Как пользоваться:</b>\n"
        f"1. Отправьте мне анимированный стикер или эмодзи\n"
        f"2. Выберите: перекрасить один или весь пак\n"
        f"3. Выберите цвет\n"
        f"4. Придумайте имя для нового пака\n"
        f"5. Получите результат!\n\n"
        f"{em(EMOJI['tag'], '🏷')} Поддерживаются: <b>TGS</b> (анимированные) стикеры и эмодзи"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())


@main_router.message(Command("help"))
async def cmd_help(message: Message):
    if db.is_banned(message.from_user.id):
        return
    text = (
        f"{em(EMOJI['info'], 'ℹ️')} <b>Справка по боту</b>\n\n"
        f"{em(EMOJI['brush'], '🖌')} <b>Перекраска стикеров:</b>\n"
        f"Отправьте мне анимированный (TGS) стикер или кастомный эмодзи. "
        f"Бот предложит перекрасить один стикер или весь пак.\n\n"
        f"{em(EMOJI['color_text'], '🔡')} <b>Выбор цвета:</b>\n"
        f"• Готовые цвета из палитры\n"
        f"• Свой HEX-код (например: <code>#FF5733</code>)\n"
        f"• Название цвета (красный, blue, gold...)\n\n"
        f"{em(EMOJI['box'], '📦')} <b>Создание пака:</b>\n"
        f"После перекраски бот попросит имя для нового стикерпака. "
        f"Имя должно содержать только латинские буквы, цифры и подчёркивания.\n\n"
        f"{em(EMOJI['check'], '✅')} <b>Команды:</b>\n"
        f"/start — Главное меню\n"
        f"/help — Эта справка\n"
        f"/cancel — Отмена текущего действия\n"
    )
    if is_admin(message.from_user.id):
        text += f"\n{em(EMOJI['settings'], '⚙️')} /admin — Админ-панель\n"
    await message.answer(text, parse_mode=ParseMode.HTML)


@main_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"{em(EMOJI['cross'], '❌')} Действие отменено.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


@main_router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f"{em(EMOJI['cross'], '❌')} Действие отменено.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )
    await callback.answer()


@main_router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    text = (
        f"{em(EMOJI['info'], 'ℹ️')} <b>Справка</b>\n\n"
        f"Отправьте мне анимированный стикер или эмодзи, "
        f"и я перекрашу его в нужный цвет!\n\n"
        f"Поддерживаются только <b>TGS</b> (анимированные) форматы."
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
    await callback.answer()


HISTORY_PAGE_SIZE = 5


def history_kb(page: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="◁ Назад",
            callback_data=f"my_history:{page - 1}"
        ))
    if (page + 1) * HISTORY_PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(
            text="Вперёд ▷",
            callback_data=f"my_history:{page + 1}"
        ))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(
        text="Главное меню",
        callback_data="main_menu",
        icon_custom_emoji_id=EMOJI["home"]
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@main_router.callback_query(F.data == "my_history")
async def cb_my_history(callback: CallbackQuery):
    await show_history_page(callback, 0)


@main_router.callback_query(F.data.startswith("my_history:"))
async def cb_my_history_page(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    await show_history_page(callback, page)


async def show_history_page(callback: CallbackQuery, page: int):
    user_id = callback.from_user.id
    total = db.get_history_count(user_id)
    if total == 0:
        await callback.answer("У вас пока нет перекрасок!", show_alert=True)
        return
    history = db.get_user_history(user_id, limit=HISTORY_PAGE_SIZE, offset=page * HISTORY_PAGE_SIZE)
    text = f"{em(EMOJI['calendar'], '📅')} <b>Ваши перекраски</b> (стр. {page + 1}/{(total - 1) // HISTORY_PAGE_SIZE + 1}):\n\n"
    for h in history:
        color = h.get("color_hex", "?")
        action = h.get("action_type", "?")
        count = h.get("items_count", 1)
        date = h.get("created_at", "?")[:16]
        set_name = h.get("sticker_set_name", "")
        action_text = "Один стикер" if action == "single" else f"Пак ({count} шт.)"
        if set_name:
            link = f"https://t.me/addemoji/{set_name}" if action == "single" else f"https://t.me/addstickers/{set_name}"
            text += f"{em(EMOJI['brush'], '🖌')} {action_text} — <b>{color}</b>\n"
            text += f"   {em(EMOJI['link'], '🔗')} <a href=\"{link}\">{set_name}</a> — {date}\n\n"
        else:
            text += f"{em(EMOJI['brush'], '🖌')} {action_text} — <b>{color}</b> — {date}\n\n"
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=history_kb(page, total),
        disable_web_page_preview=True
    )
    await callback.answer()


@main_router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        f"{em(EMOJI['home'], '🏘')} <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )
    await callback.answer()


# ─── Recolor Flow ────────────────────────────────────────────────────────────

@main_router.callback_query(F.data == "start_recolor")
async def cb_start_recolor(callback: CallbackQuery, state: FSMContext):
    if db.is_banned(callback.from_user.id):
        await callback.answer("Вы заблокированы!", show_alert=True)
        return
    await state.set_state(RecolorStates.waiting_sticker)
    await callback.message.edit_text(
        f"{em(EMOJI['send'], '⬆️')} <b>Отправьте мне анимированный стикер или кастомный эмодзи</b>\n\n"
        f"Я определю пак и предложу варианты перекраски.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    await callback.answer()


@main_router.message(RecolorStates.waiting_sticker, F.sticker)
async def handle_sticker_received(message: Message, state: FSMContext):
    sticker = message.sticker
    fmt = await get_sticker_format_type(sticker)

    if fmt != "animated":
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} <b>К сожалению, я поддерживаю только анимированные (TGS) стикеры.</b>\n\n"
            f"Отправленный стикер имеет формат: <code>{fmt}</code>\n"
            f"Отправьте анимированный стикер или эмодзи.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return

    session_data = {
        "sticker_file_id": sticker.file_id,
        "sticker_set_name": sticker.set_name or "",
        "sticker_format": fmt,
        "is_custom_emoji": bool(sticker.custom_emoji_id),
    }
    await state.update_data(**session_data)

    set_info = ""
    if sticker.set_name:
        set_info = f"\n{em(EMOJI['tag'], '🏷')} Пак: <code>{sticker.set_name}</code>"

    emoji_type = "кастомный эмодзи" if sticker.custom_emoji_id else "стикер"

    await state.set_state(RecolorStates.choose_action)
    await message.answer(
        f"{em(EMOJI['check'], '✅')} <b>Получен анимированный {emoji_type}!</b>{set_info}\n\n"
        f"Что хотите сделать?",
        parse_mode=ParseMode.HTML,
        reply_markup=action_choice_kb()
    )


@main_router.message(RecolorStates.waiting_sticker)
async def handle_not_sticker(message: Message, state: FSMContext, bot: Bot):
    # Проверяем премиум эмодзи в сущностях сообщения
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji" and entity.custom_emoji_id:
                stickers = await bot.get_custom_emoji_stickers([entity.custom_emoji_id])
                if stickers:
                    sticker = stickers[0]
                    fmt = await get_sticker_format_type(sticker)
                    if fmt != "animated":
                        await message.answer(
                            f"{em(EMOJI['cross'], '❌')} Этот эмодзи не анимированный (<code>{fmt}</code>).\n"
                            f"Отправьте анимированный премиум эмодзи.",
                            parse_mode=ParseMode.HTML,
                            reply_markup=cancel_kb()
                        )
                        return
                    session_data = {
                        "sticker_file_id": sticker.file_id,
                        "sticker_set_name": sticker.set_name or "",
                        "sticker_format": fmt,
                        "is_custom_emoji": True,
                    }
                    await state.update_data(**session_data)
                    await state.set_state(RecolorStates.choose_action)
                    set_info = ""
                    if sticker.set_name:
                        set_info = f"\n{em(EMOJI['tag'], '🏷')} Пак: <code>{sticker.set_name}</code>"
                    await message.answer(
                        f"{em(EMOJI['check'], '✅')} <b>Получен премиум эмодзи!</b>{set_info}\n\n"
                        f"Что хотите сделать?",
                        parse_mode=ParseMode.HTML,
                        reply_markup=action_choice_kb()
                    )
                    return
    await message.answer(
        f"{em(EMOJI['cross'], '❌')} Пожалуйста, отправьте <b>стикер</b> или <b>премиум эмодзи</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )


@main_router.callback_query(RecolorStates.choose_action, F.data.in_({"action_single", "action_pack"}))
async def cb_choose_action(callback: CallbackQuery, state: FSMContext):
    action = "single" if callback.data == "action_single" else "pack"

    data = await state.get_data()
    if action == "pack" and not data.get("sticker_set_name"):
        await callback.answer("У этого стикера нет пака! Выберите 'Один стикер'.", show_alert=True)
        return

    await state.update_data(action=action)
    await state.set_state(RecolorStates.choose_color)

    action_text = "один стикер" if action == "single" else "весь пак"
    await callback.message.edit_text(
        f"{em(EMOJI['brush'], '🖌')} Перекрашиваем <b>{action_text}</b>.\n\n"
        f"Выберите цвет:",
        parse_mode=ParseMode.HTML,
        reply_markup=color_choice_kb()
    )
    await callback.answer()


@main_router.callback_query(RecolorStates.choose_color, F.data.startswith("color_#"))
async def cb_choose_preset_color(callback: CallbackQuery, state: FSMContext):
    color_hex = callback.data.replace("color_", "")
    await state.update_data(color_hex=color_hex)
    await state.set_state(RecolorStates.waiting_pack_name)

    r, g, b = hex_to_rgb(color_hex)
    await callback.message.edit_text(
        f"{em(EMOJI['check'], '✅')} Цвет выбран: <b>{color_hex}</b> "
        f"(RGB: {r}, {g}, {b})\n\n"
        f"{em(EMOJI['write'], '✍️')} <b>Введите название для нового стикерпака</b>\n"
        f"(латинские буквы, цифры, подчёркивания):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    await callback.answer()


@main_router.callback_query(RecolorStates.choose_color, F.data == "color_custom")
async def cb_custom_color(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RecolorStates.custom_color_input)
    await callback.message.edit_text(
        f"{em(EMOJI['brush'], '🖌')} <b>Введите цвет</b>\n\n"
        f"Примеры:\n"
        f"• HEX: <code>#FF5733</code> или <code>FF5733</code>\n"
        f"• Название: <code>красный</code>, <code>blue</code>, <code>gold</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )
    await callback.answer()


@main_router.message(RecolorStates.custom_color_input, F.text)
async def handle_custom_color(message: Message, state: FSMContext):
    color = parse_color(message.text)
    if not color:
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Не удалось распознать цвет.\n"
            f"Попробуйте HEX (<code>#FF5733</code>) или название (<code>красный</code>).",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return

    await state.update_data(color_hex=color)
    await state.set_state(RecolorStates.waiting_pack_name)

    r, g, b = hex_to_rgb(color)
    await message.answer(
        f"{em(EMOJI['check'], '✅')} Цвет: <b>{color}</b> (RGB: {r}, {g}, {b})\n\n"
        f"{em(EMOJI['write'], '✍️')} <b>Введите название для нового стикерпака</b>\n"
        f"(латинские буквы, цифры, подчёркивания):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb()
    )


@main_router.message(RecolorStates.waiting_pack_name, F.text)
async def handle_pack_name(message: Message, state: FSMContext, bot: Bot):
    raw_name = message.text.strip()
    pack_suffix = sanitize_pack_name(raw_name)

    if len(pack_suffix) < 1:
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Некорректное имя. Используйте латинские буквы, цифры, подчёркивания.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return

    bot_info = await bot.get_me()
    full_pack_name = f"{pack_suffix}_by_{bot_info.username}"

    # Check if pack name already exists
    try:
        existing = await bot.get_sticker_set(full_pack_name)
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Пак с именем <code>{full_pack_name}</code> уже существует!\n"
            f"Придумайте другое имя:",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb()
        )
        return
    except Exception:
        pass  # Pack doesn't exist, good

    await state.update_data(pack_name_suffix=pack_suffix, pack_title=raw_name)
    await state.set_state(RecolorStates.processing)

    data = await state.get_data()

    progress_msg = await message.answer(
        f"{em(EMOJI['loading'], '🔄')} <b>Обработка...</b>\n"
        f"Это может занять некоторое время.",
        parse_mode=ParseMode.HTML
    )

    try:
        if data["action"] == "single":
            await process_single_sticker(message, bot, data, full_pack_name, progress_msg)
        else:
            await process_full_pack(message, bot, data, full_pack_name, progress_msg)
    except Exception as e:
        logger.exception("Error processing recolor")
        await progress_msg.edit_text(
            f"{em(EMOJI['cross'], '❌')} <b>Ошибка при обработке:</b>\n<code>{str(e)[:500]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
    finally:
        await state.clear()


async def process_single_sticker(message: Message, bot: Bot, data: dict,
                                  full_pack_name: str, progress_msg: Message):
    user_id = message.from_user.id
    color_hex = data["color_hex"]
    file_id = data["sticker_file_id"]
    pack_title = data.get("pack_title", "Recolored")
    is_emoji = data.get("is_custom_emoji", False)

    # Download sticker
    sticker_bytes = await download_sticker_file(bot, file_id)

    # Recolor
    recolored = recolor_tgs_data(sticker_bytes, color_hex)

    # Create sticker set
    sticker_file = BufferedInputFile(recolored, filename="sticker.tgs")

    input_sticker = InputSticker(
        sticker=sticker_file,
        emoji_list=["🎨"],
        format="animated"
    )

    try:
        if is_emoji:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=full_pack_name,
                title=pack_title,
                stickers=[input_sticker],
                sticker_type="custom_emoji"
            )
        else:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=full_pack_name,
                title=pack_title,
                stickers=[input_sticker],
            )
    except Exception as e:
        raise Exception(f"Не удалось создать стикерпак: {e}")

    db.increment_recolors(user_id, 1)
    db.add_recolor_history(user_id, "single", full_pack_name, color_hex, 1)

    pack_link = f"https://t.me/addstickers/{full_pack_name}"
    if is_emoji:
        pack_link = f"https://t.me/addemoji/{full_pack_name}"

    await progress_msg.edit_text(
        f"{em(EMOJI['party'], '🎉')} <b>Готово!</b>\n\n"
        f"{em(EMOJI['check'], '✅')} Стикер перекрашен в <b>{color_hex}</b>\n"
        f"{em(EMOJI['link'], '🔗')} Пак: {pack_link}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


async def process_full_pack(message: Message, bot: Bot, data: dict,
                             full_pack_name: str, progress_msg: Message):
    user_id = message.from_user.id
    color_hex = data["color_hex"]
    set_name = data["sticker_set_name"]
    pack_title = data.get("pack_title", "Recolored Pack")
    is_emoji = data.get("is_custom_emoji", False)

    # Get the sticker set
    try:
        sticker_set = await bot.get_sticker_set(set_name)
    except Exception as e:
        raise Exception(f"Не удалось получить стикерпак '{set_name}': {e}")

    stickers = sticker_set.stickers
    total = len(stickers)

    if total == 0:
        raise Exception("Пак пуст!")

    if total > 120:
        raise Exception(f"Слишком много стикеров ({total}). Максимум 120.")

    await progress_msg.edit_text(
        f"{em(EMOJI['loading'], '🔄')} <b>Обработка пака...</b>\n"
        f"Стикеров: {total}\n"
        f"Цвет: {color_hex}",
        parse_mode=ParseMode.HTML
    )

    recolored_stickers: List[InputSticker] = []
    errors = 0

    for i, sticker in enumerate(stickers):
        fmt = await get_sticker_format_type(sticker)
        if fmt != "animated":
            errors += 1
            continue

        try:
            sticker_bytes = await download_sticker_file(bot, sticker.file_id)
            recolored = recolor_tgs_data(sticker_bytes, color_hex)
            sticker_file = BufferedInputFile(recolored, filename=f"sticker_{i}.tgs")

            emoji_list = [sticker.emoji] if sticker.emoji else ["🎨"]
            if not emoji_list:
                emoji_list = ["🎨"]

            input_sticker = InputSticker(
                sticker=sticker_file,
                emoji_list=emoji_list,
                format="animated"
            )
            recolored_stickers.append(input_sticker)
        except Exception as e:
            logger.error(f"Error recoloring sticker {i}: {e}")
            errors += 1

        # Update progress every 5 stickers
        if (i + 1) % 5 == 0 or i == total - 1:
            try:
                await progress_msg.edit_text(
                    f"{em(EMOJI['loading'], '🔄')} <b>Обработка...</b>\n"
                    f"Прогресс: {i + 1}/{total}\n"
                    f"Ошибок: {errors}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

        await asyncio.sleep(0.1)  # Rate limiting

    if not recolored_stickers:
        raise Exception("Не удалось перекрасить ни одного стикера!")

    # Create pack with first sticker
    try:
        if is_emoji:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=full_pack_name,
                title=pack_title,
                stickers=[recolored_stickers[0]],
                sticker_type="custom_emoji"
            )
        else:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=full_pack_name,
                title=pack_title,
                stickers=[recolored_stickers[0]],
            )
    except Exception as e:
        raise Exception(f"Не удалось создать стикерпак: {e}")

    # Add remaining stickers
    add_errors = 0
    for i, input_sticker in enumerate(recolored_stickers[1:], 1):
        try:
            await bot.add_sticker_to_set(
                user_id=user_id,
                name=full_pack_name,
                sticker=input_sticker
            )
        except Exception as e:
            logger.error(f"Error adding sticker {i} to set: {e}")
            add_errors += 1
        await asyncio.sleep(0.3)  # Rate limiting for adding stickers

        if i % 5 == 0:
            try:
                await progress_msg.edit_text(
                    f"{em(EMOJI['loading'], '🔄')} <b>Добавление в пак...</b>\n"
                    f"Прогресс: {i + 1}/{len(recolored_stickers)}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    success_count = len(recolored_stickers) - add_errors
    db.increment_recolors(user_id, success_count)
    db.increment_packs(user_id)
    db.add_recolor_history(user_id, "pack", full_pack_name, color_hex, success_count)

    pack_link = f"https://t.me/addstickers/{full_pack_name}"
    if is_emoji:
        pack_link = f"https://t.me/addemoji/{full_pack_name}"

    result_text = (
        f"{em(EMOJI['party'], '🎉')} <b>Пак готов!</b>\n\n"
        f"{em(EMOJI['check'], '✅')} Перекрашено: <b>{success_count}</b> из <b>{total}</b>\n"
        f"{em(EMOJI['brush'], '🖌')} Цвет: <b>{color_hex}</b>\n"
        f"{em(EMOJI['link'], '🔗')} Пак: {pack_link}"
    )
    if errors + add_errors > 0:
        result_text += f"\n{em(EMOJI['cross'], '❌')} Ошибок: {errors + add_errors}"

    await progress_msg.edit_text(
        result_text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


# ─── Admin Panel ─────────────────────────────────────────────────────────────

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        f"{em(EMOJI['settings'], '⚙️')} <b>Админ-панель</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_kb()
    )


@admin_router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        f"{em(EMOJI['settings'], '⚙️')} <b>Админ-панель</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_kb()
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = db.get_stats()
    text = (
        f"{em(EMOJI['stats'], '📊')} <b>Статистика бота</b>\n\n"
        f"{em(EMOJI['people'], '👥')} Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"{em(EMOJI['smile'], '🙂')} Активных сегодня: <b>{stats['active_today']}</b>\n"
        f"{em(EMOJI['growth'], '📊')} Активных за неделю: <b>{stats['active_week']}</b>\n"
        f"{em(EMOJI['gift'], '🎁')} Премиум: <b>{stats['premium_users']}</b>\n"
        f"{em(EMOJI['lock'], '🔒')} Забанено: <b>{stats['banned_users']}</b>\n\n"
        f"{em(EMOJI['brush'], '🖌')} Всего перекрасок: <b>{stats['total_recolors']}</b>\n"
        f"{em(EMOJI['box'], '📦')} Создано паков: <b>{stats['total_packs']}</b>"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin_top")
async def cb_admin_top(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    top = db.get_top_users(10)
    text = f"{em(EMOJI['growth'], '📊')} <b>Топ-10 пользователей</b>\n\n"
    for i, u in enumerate(top, 1):
        name = u.get("first_name") or u.get("username") or str(u["user_id"])
        text += (
            f"{i}. <b>{name}</b> (ID: <code>{u['user_id']}</code>)\n"
            f"   Перекрасок: {u['total_recolors']} | Паков: {u['total_packs_created']}\n"
        )
    if not top:
        text += "Пока нет данных."
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = db.get_stats()
    text = (
        f"{em(EMOJI['people'], '👥')} <b>Пользователи</b>\n\n"
        f"Всего: {stats['total_users']}\n"
        f"Активных сегодня: {stats['active_today']}\n"
        f"Активных за неделю: {stats['active_week']}\n"
        f"Забанено: {stats['banned_users']}\n"
        f"Премиум: {stats['premium_users']}"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.broadcast_text)
    await callback.message.edit_text(
        f"{em(EMOJI['horn'], '📣')} <b>Рассылка</b>\n\n"
        f"Введите текст сообщения для рассылки всем пользователям.\n"
        f"Поддерживается HTML-разметка.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await callback.answer()


@admin_router.message(AdminStates.broadcast_text, F.text)
async def handle_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    text = message.text
    user_ids = db.get_all_user_ids(exclude_banned=True)
    total = len(user_ids)

    progress = await message.answer(
        f"{em(EMOJI['loading'], '🔄')} Рассылка... 0/{total}",
        parse_mode=ParseMode.HTML
    )

    success = 0
    fail = 0
    for i, uid in enumerate(user_ids):
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            fail += 1
        if (i + 1) % 20 == 0:
            try:
                await progress.edit_text(
                    f"{em(EMOJI['loading'], '🔄')} Рассылка... {i + 1}/{total}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            await asyncio.sleep(0.5)

    db.add_broadcast(message.from_user.id, text[:200], total, success, fail)
    db.add_admin_log(message.from_user.id, "broadcast", details=f"success={success}, fail={fail}")

    await progress.edit_text(
        f"{em(EMOJI['check'], '✅')} <b>Рассылка завершена!</b>\n\n"
        f"Отправлено: {success}\n"
        f"Ошибок: {fail}\n"
        f"Всего: {total}",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await state.clear()


@admin_router.callback_query(F.data == "admin_ban")
async def cb_admin_ban(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.ban_user_id)
    await callback.message.edit_text(
        f"{em(EMOJI['lock'], '🔒')} <b>Бан пользователя</b>\n\n"
        f"Введите ID пользователя:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await callback.answer()


@admin_router.message(AdminStates.ban_user_id, F.text)
async def handle_ban_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID!", reply_markup=admin_back_kb())
        return

    if uid in ADMIN_IDS:
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Нельзя забанить админа!",
            parse_mode=ParseMode.HTML
        )
        await state.clear()
        return

    db.ban_user(uid)
    db.add_admin_log(message.from_user.id, "ban", uid)
    await message.answer(
        f"{em(EMOJI['lock'], '🔒')} Пользователь <code>{uid}</code> забанен.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await state.clear()


@admin_router.callback_query(F.data == "admin_unban")
async def cb_admin_unban(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.unban_user_id)
    await callback.message.edit_text(
        f"{em(EMOJI['unlock'], '🔓')} <b>Разбан пользователя</b>\n\n"
        f"Введите ID пользователя:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await callback.answer()


@admin_router.message(AdminStates.unban_user_id, F.text)
async def handle_unban_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID!")
        return

    db.unban_user(uid)
    db.add_admin_log(message.from_user.id, "unban", uid)
    await message.answer(
        f"{em(EMOJI['unlock'], '🔓')} Пользователь <code>{uid}</code> разбанен.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await state.clear()


@admin_router.callback_query(F.data == "admin_search")
async def cb_admin_search(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.search_user)
    await callback.message.edit_text(
        f"{em(EMOJI['eye'], '👁')} <b>Поиск пользователя</b>\n\n"
        f"Введите username, имя или ID:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await callback.answer()


@admin_router.message(AdminStates.search_user, F.text)
async def handle_search_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    results = db.search_users(message.text.strip())
    if not results:
        await message.answer("Ничего не найдено.", reply_markup=admin_back_kb())
        await state.clear()
        return

    text = f"{em(EMOJI['eye'], '👁')} <b>Результаты поиска:</b>\n\n"
    for u in results[:10]:
        banned = "🔒" if u.get("is_banned") else ""
        premium = "⭐" if u.get("is_premium") else ""
        text += (
            f"{banned}{premium} <b>{u.get('first_name', '?')}</b> "
            f"(@{u.get('username', '?')}) — <code>{u['user_id']}</code>\n"
            f"  Перекрасок: {u.get('total_recolors', 0)} | "
            f"Последняя активность: {u.get('last_active', '?')}\n\n"
        )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await state.clear()


@admin_router.callback_query(F.data == "admin_userinfo")
async def cb_admin_userinfo(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.user_info_id)
    await callback.message.edit_text(
        f"{em(EMOJI['profile'], '👤')} <b>Информация о пользователе</b>\n\n"
        f"Введите ID пользователя:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_back_kb()
    )
    await callback.answer()


@admin_router.message(AdminStates.user_info_id, F.text)
async def handle_userinfo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("Введите числовой ID!")
        return

    info = db.get_user_info(uid)
    if not info:
        await message.answer("Пользователь не найден.", reply_markup=admin_back_kb())
        await state.clear()
        return

    history = db.get_user_history(uid, 5)
    banned_str = "Да 🔒" if info.get("is_banned") else "Нет"
    premium_str = "Да ⭐" if info.get("is_premium") else "Нет"

    text = (
        f"{em(EMOJI['profile'], '👤')} <b>Пользователь</b>\n\n"
        f"ID: <code>{info['user_id']}</code>\n"
        f"Имя: {info.get('first_name', '?')} {info.get('last_name', '') or ''}\n"
        f"Username: @{info.get('username', '?')}\n"
        f"Язык: {info.get('language_code', '?')}\n"
        f"Премиум: {premium_str}\n"
        f"Забанен: {banned_str}\n"
        f"Первый визит: {info.get('first_seen', '?')}\n"
        f"Последняя активность: {info.get('last_active', '?')}\n"
        f"Перекрасок: {info.get('total_recolors', 0)}\n"
        f"Паков создано: {info.get('total_packs_created', 0)}\n"
    )

    if history:
        text += f"\n{em(EMOJI['clock'], '⏰')} <b>Последние действия:</b>\n"
        for h in history:
            text += f"  • {h.get('action_type', '?')} — {h.get('color_hex', '?')} — {h.get('created_at', '?')}\n"

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await state.clear()


@admin_router.callback_query(F.data == "admin_logs")
async def cb_admin_logs(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    logs = db.get_admin_logs(15)
    text = f"{em(EMOJI['file'], '📁')} <b>Логи админов:</b>\n\n"
    if not logs:
        text += "Пока нет записей."
    else:
        for log in logs:
            target = f" → {log.get('target_user_id', '')}" if log.get('target_user_id') else ""
            details = f" ({log.get('details', '')})" if log.get('details') else ""
            text += f"• [{log.get('created_at', '?')}] Admin {log['admin_id']}: {log['action']}{target}{details}\n"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin_recent")
async def cb_admin_recent(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    actions = db.get_recent_actions(15)
    text = f"{em(EMOJI['clock'], '⏰')} <b>Последние действия пользователей:</b>\n\n"
    if not actions:
        text += "Пока нет данных."
    else:
        for a in actions:
            text += (
                f"• User {a['user_id']}: {a.get('action_type', '?')} — "
                f"{a.get('color_hex', '?')} ({a.get('items_count', 1)} шт.) — "
                f"{a.get('created_at', '?')}\n"
            )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin_settings")
async def cb_admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    maintenance = db.get_setting("maintenance", "off")
    max_pack = db.get_setting("max_pack_size", "120")
    text = (
        f"{em(EMOJI['settings'], '⚙️')} <b>Настройки бота</b>\n\n"
        f"Режим обслуживания: <b>{maintenance}</b>\n"
        f"Макс. размер пака: <b>{max_pack}</b>\n\n"
        f"Для изменения используйте команды:\n"
        f"<code>/set maintenance on</code>\n"
        f"<code>/set maintenance off</code>\n"
        f"<code>/set max_pack_size 50</code>"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_back_kb())
    await callback.answer()


@admin_router.message(Command("set"))
async def cmd_set_setting(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /set <ключ> <значение>")
        return
    key = parts[1]
    value = parts[2]
    db.set_setting(key, value)
    db.add_admin_log(message.from_user.id, "set_setting", details=f"{key}={value}")
    await message.answer(
        f"{em(EMOJI['check'], '✅')} Настройка <code>{key}</code> = <code>{value}</code>",
        parse_mode=ParseMode.HTML
    )


# ─── Catch-all sticker handler (outside FSM) ────────────────────────────────

@main_router.message(F.entities, ~StateFilter(RecolorStates.waiting_sticker))
async def handle_premium_emoji_outside_fsm(message: Message, state: FSMContext, bot: Bot):
    """Обработка премиум эмодзи вне FSM."""
    if db.is_banned(message.from_user.id):
        return
    if not message.entities:
        return
    custom_emoji_entity = None
    for entity in message.entities:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            custom_emoji_entity = entity
            break
    if not custom_emoji_entity:
        return

    user = message.from_user
    db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_premium=bool(user.is_premium)
    )

    stickers = await bot.get_custom_emoji_stickers([custom_emoji_entity.custom_emoji_id])
    if not stickers:
        return
    sticker = stickers[0]
    fmt = await get_sticker_format_type(sticker)
    if fmt != "animated":
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Этот эмодзи не анимированный (<code>{fmt}</code>).",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        return

    session_data = {
        "sticker_file_id": sticker.file_id,
        "sticker_set_name": sticker.set_name or "",
        "sticker_format": fmt,
        "is_custom_emoji": True,
    }
    await state.update_data(**session_data)
    await state.set_state(RecolorStates.choose_action)
    set_info = ""
    if sticker.set_name:
        set_info = f"\n{em(EMOJI['tag'], '🏷')} Пак: <code>{sticker.set_name}</code>"
    await message.answer(
        f"{em(EMOJI['check'], '✅')} <b>Получен премиум эмодзи!</b>{set_info}\n\nЧто хотите сделать?",
        parse_mode=ParseMode.HTML,
        reply_markup=action_choice_kb()
    )


@main_router.message(F.sticker, ~StateFilter(RecolorStates.waiting_sticker))
async def handle_sticker_outside_fsm(message: Message, state: FSMContext):
    """When user sends sticker without being in recolor flow, start the flow."""
    if db.is_banned(message.from_user.id):
        return

    user = message.from_user
    db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_premium=bool(user.is_premium)
    )

    sticker = message.sticker
    fmt = await get_sticker_format_type(sticker)

    if fmt != "animated":
        await message.answer(
            f"{em(EMOJI['cross'], '❌')} Я поддерживаю только <b>анимированные (TGS)</b> стикеры.\n"
            f"Этот стикер: <code>{fmt}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        return

    session_data = {
        "sticker_file_id": sticker.file_id,
        "sticker_set_name": sticker.set_name or "",
        "sticker_format": fmt,
        "is_custom_emoji": bool(sticker.custom_emoji_id),
    }
    await state.update_data(**session_data)
    await state.set_state(RecolorStates.choose_action)

    set_info = ""
    if sticker.set_name:
        set_info = f"\n{em(EMOJI['tag'], '🏷')} Пак: <code>{sticker.set_name}</code>"

    emoji_type = "кастомный эмодзи" if sticker.custom_emoji_id else "стикер"

    await message.answer(
        f"{em(EMOJI['check'], '✅')} <b>Получен анимированный {emoji_type}!</b>{set_info}\n\n"
        f"Что хотите сделать?",
        parse_mode=ParseMode.HTML,
        reply_markup=action_choice_kb()
    )


# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in .env!")
        return

    db.connect()
    logger.info("Database connected.")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(main_router)

    logger.info("Bot starting...")
    bot_info = await bot.get_me()
    logger.info(f"Bot: @{bot_info.username} (ID: {bot_info.id})")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
