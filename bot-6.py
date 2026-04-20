"""
Lottie Premium Emoji Generator Bot
aiogram 3.7.0+  |  Python 3.8+
.env: BOT_TOKEN, ONLYSQ_API_KEY
"""

import asyncio
import glob as _glob
import gzip
import json
import logging
import mimetypes
import re
import signal
import time
from io import BytesIO
from typing import List, Optional, Tuple

# Telegram requires Content-Type: application/x-tgsticker for animated TGS.
# Without this, aiohttp sends application/octet-stream → "wrong file type"
mimetypes.add_type("application/x-tgsticker", ".tgs")

from openai import OpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, InputSticker, Message
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ONLYSQ_API_KEY: str = os.getenv("ONLYSQ_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")

# ─── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Lottie JSON generator for Telegram premium emoji (TGS stickers).
Output ONLY raw valid JSON. No markdown, no backticks, no explanation. Start with { end with }.

STRUCTURE: {"v":"5.7.1","fr":60,"ip":0,"op":120,"w":512,"h":512,"ddd":0,"assets":[],"layers":[...]}

LAYER RULES:
- Layer 1: ROOT null ty=3 ind=1, ks.a.k=[0,0], ks.p.k=[256,256], with bounce+pulse scale animation
- Child layers: ty=4, parent=1, ks.a.k=[0,0], ks.p.k=[0,0]
- Vertices centered around [0,0] (range -200 to +200), NOT absolute canvas coords
- Groups: {"ty":"gr","it":[...shape..., fill_or_stroke, transform]}
- Fill: {"ty":"fl","c":{"a":0,"k":[R,G,B,1]},"o":{"a":0,"k":100},"r":1}
- Stroke: {"ty":"st","c":{"a":0,"k":[0.05,0.05,0.05,1]},"o":{"a":0,"k":100},"w":{"a":0,"k":14},"lc":2,"lj":2}
- Transform (ALWAYS LAST in group): {"ty":"tr","a":{"a":0,"k":[0,0]},"p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},"r":{"a":0,"k":0},"o":{"a":0,"k":100},"sk":{"a":0,"k":0},"sa":{"a":0,"k":0}}

ROOT SCALE ANIMATION (bounce in + idle pulse):
{"a":1,"k":[{"t":0,"s":[0,0],"h":0,"o":{"x":[0.175],"y":[0.885]},"i":{"x":[0.32],"y":[1.275]}},{"t":20,"s":[115,115],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}},{"t":28,"s":[100,100],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}},{"t":55,"s":[106,106],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}},{"t":80,"s":[100,100],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}},{"t":105,"s":[106,106],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}},{"t":120,"s":[100,100],"h":0}]}

MINIMUM 5 LAYERS: ROOT null, shadow ellipse (offset [12,14] dark semi-transparent), main body (vivid fill + dark stroke), rim light (white stroke 60% opacity), gloss (white ellipse top-left 50% opacity).
Make subject fill 70-80% of canvas. Use vivid appropriate colors for the requested emoji."""

# ─── BOT ───────────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ai_client = OpenAI(
    api_key=ONLYSQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

BOT_USERNAME: str = ""  # filled at startup

# ─── FONT RENDERING ENGINE ─────────────────────────────────────────────────────

def _find_bold_font() -> Optional[str]:
    """Locate a bold TTF font on the system."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Fallback: any bold TTF
    for pattern in ["/usr/share/fonts/**/*.ttf", "/system/fonts/*.ttf"]:
        for p in _glob.glob(pattern, recursive=True):
            if "bold" in p.lower():
                return p
    # Any TTF at all
    for pattern in ["/usr/share/fonts/**/*.ttf", "/system/fonts/*.ttf"]:
        found = _glob.glob(pattern, recursive=True)
        if found:
            return found[0]
    return None


_FONT_PATH: Optional[str] = _find_bold_font()
_FONT_OBJ = None  # lazy-loaded TTFont


def _load_font():
    global _FONT_OBJ
    if _FONT_OBJ is not None:
        return _FONT_OBJ
    if not _FONT_PATH:
        return None
    try:
        from fontTools.ttLib import TTFont
        _FONT_OBJ = TTFont(_FONT_PATH)
        log.info("Font loaded: %s", _FONT_PATH)
        return _FONT_OBJ
    except Exception as e:
        log.warning("fonttools unavailable (%s) — text will be drawn by AI", e)
        return None


def _ttf_contours_to_lottie(text: str, target_height: float) -> Tuple[List[dict], float]:
    """
    Convert a text string to Lottie path contours using a system TTF font.
    Returns (contours, total_advance_width).
    """
    font = _load_font()
    if font is None:
        return [], 0.0

    try:
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError:
        return [], 0.0

    upem: int = font["head"].unitsPerEm
    scale: float = target_height / upem
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"].metrics

    contours: List[dict] = []
    x_cur: float = 0.0

    for char in text:
        code = ord(char)
        if code == 32:  # space
            adv = hmtx.get("space", hmtx.get("uni0020", (upem // 3, 0)))[0]
            x_cur += adv * scale
            continue

        gname = cmap.get(code) or cmap.get(ord(char.upper()))
        if not gname or gname not in glyph_set:
            x_cur += (upem // 2) * scale
            continue

        pen = RecordingPen()
        try:
            glyph_set[gname].draw(pen)
        except Exception:
            x_cur += (upem // 2) * scale
            continue

        adv: float = hmtx.get(gname, (upem // 2, 0))[0] * scale

        v: List = []
        i_t: List = []
        o_t: List = []

        def _flush_contour() -> None:
            if len(v) >= 2:
                contours.append({
                    "c": True,
                    "v": [list(p) for p in v],
                    "i": [list(t) for t in i_t],
                    "o": [list(t) for t in o_t],
                })

        for op_name, args in pen.value:
            if op_name == "moveTo":
                _flush_contour()
                v.clear(); i_t.clear(); o_t.clear()
                px, py = args[0]
                v.append([px * scale + x_cur, -py * scale])
                i_t.append([0.0, 0.0])
                o_t.append([0.0, 0.0])

            elif op_name == "lineTo":
                px, py = args[0]
                v.append([px * scale + x_cur, -py * scale])
                i_t.append([0.0, 0.0])
                o_t.append([0.0, 0.0])

            elif op_name == "qCurveTo":
                pts = list(args)
                off_pts, on_end = pts[:-1], pts[-1]
                if not v:
                    continue
                prev_fx = (v[-1][0] - x_cur) / scale
                prev_fy = -v[-1][1] / scale
                ends: List = []
                for k in range(len(off_pts) - 1):
                    ends.append(((off_pts[k][0] + off_pts[k + 1][0]) / 2,
                                 (off_pts[k][1] + off_pts[k + 1][1]) / 2))
                ends.append(on_end)
                for ctrl, end_pt in zip(off_pts, ends):
                    cx_f, cy_f = ctrl
                    ex_f, ey_f = end_pt
                    out_x = 2 / 3 * (cx_f - prev_fx) * scale
                    out_y = -2 / 3 * (cy_f - prev_fy) * scale
                    in_x = 2 / 3 * (cx_f - ex_f) * scale
                    in_y = -2 / 3 * (cy_f - ey_f) * scale
                    o_t[-1] = [out_x, out_y]
                    v.append([ex_f * scale + x_cur, -ey_f * scale])
                    i_t.append([in_x, in_y])
                    o_t.append([0.0, 0.0])
                    prev_fx, prev_fy = ex_f, ey_f

            elif op_name == "curveTo":
                if not v or len(args) < 3:
                    continue
                cp1, cp2, ep = args[0], args[1], args[-1]
                px, py = v[-1]
                cp1x = cp1[0] * scale + x_cur
                cp1y = -cp1[1] * scale
                cp2x = cp2[0] * scale + x_cur
                cp2y = -cp2[1] * scale
                ex = ep[0] * scale + x_cur
                ey = -ep[1] * scale
                o_t[-1] = [cp1x - px, cp1y - py]
                v.append([ex, ey])
                i_t.append([cp2x - ex, cp2y - ey])
                o_t.append([0.0, 0.0])

            elif op_name in ("closePath", "endPath"):
                _flush_contour()
                v.clear(); i_t.clear(); o_t.clear()

        _flush_contour()
        v.clear(); i_t.clear(); o_t.clear()
        x_cur += adv

    return contours, x_cur


def render_text_as_layer(
    text: str,
    canvas_x: float,
    canvas_y: float,
    target_height: float,
    fill_rgba: List[float],
    stroke_rgba: Optional[List[float]] = None,
    stroke_w: float = 0.0,
    parent_ind: int = 1,
    layer_ind: int = 200,
    op: int = 120,
) -> Optional[dict]:
    """
    Build a complete Lottie shape layer with text rendered as real font bezier paths.
    Returns None if fonttools is unavailable.
    """
    # FIX: was calling ttf_contours_to_lottie (missing underscore)
    contours, total_w = _ttf_contours_to_lottie(text, target_height)
    if not contours:
        return None

    offset_x = -total_w / 2
    offset_y = target_height * 0.28

    shifted: List[dict] = []
    for cont in contours:
        shifted.append({
            "c": cont["c"],
            "v": [[p[0] + offset_x, p[1] + offset_y] for p in cont["v"]],
            "i": cont["i"],
            "o": cont["o"],
        })

    items: List[dict] = [{"ty": "sh", "ks": {"a": 0, "k": s}} for s in shifted]
    items.append({"ty": "fl", "c": {"a": 0, "k": fill_rgba}, "o": {"a": 0, "k": 100}, "r": 1})
    if stroke_rgba:
        items.append({
            "ty": "st", "c": {"a": 0, "k": stroke_rgba},
            "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": stroke_w},
            "lc": 2, "lj": 2,
        })
    items.append({
        "ty": "tr",
        "a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
        "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
        "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0},
    })

    return {
        "ddd": 0, "ty": 4, "ind": layer_ind,
        "nm": f"font_{text[:20]}", "st": 0, "ip": 0, "op": op, "ao": 0,
        "parent": parent_ind,
        "ks": {
            "a": {"a": 0, "k": [0, 0]},
            "p": {"a": 0, "k": [canvas_x, canvas_y]},
            "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100},
        },
        "shapes": [{"ty": "gr", "nm": "font_gr", "it": items}],
    }


def inject_text(lottie: dict) -> dict:
    """
    Post-process: if AI placed a __text__ spec in the JSON,
    replace it with a real font-rendered layer.
    """
    spec = lottie.pop("__text__", None)
    if not spec:
        return lottie

    if isinstance(spec, str):
        spec = {"text": spec}

    text = str(spec.get("text", "")).strip()
    canvas_x = float(spec.get("x", 256))
    canvas_y = float(spec.get("y", 400))
    height = float(spec.get("height", 90))
    fill = spec.get("fill", [1.0, 1.0, 1.0, 1.0])
    stroke = spec.get("stroke", None)
    stroke_w = float(spec.get("stroke_width", 12))

    if not text:
        return lottie

    layers = lottie.setdefault("layers", [])
    existing_inds = {la.get("ind", 0) for la in layers}
    new_ind = (max(existing_inds) + 1) if existing_inds else 200

    parent_ind = next((la["ind"] for la in layers if la.get("ty") == 3), 1)
    op = lottie.get("op", 120)

    layer = render_text_as_layer(
        text=text, canvas_x=canvas_x, canvas_y=canvas_y,
        target_height=height, fill_rgba=fill,
        stroke_rgba=stroke, stroke_w=stroke_w,
        parent_ind=parent_ind, layer_ind=new_ind, op=op,
    )
    if layer:
        log.info("Font text '%s' injected via fonttools (%s)", text, _FONT_PATH)
        lottie["layers"] = [layer] + layers  # text renders on top
    else:
        log.warning("fonttools not available — AI hand-drawn text will be used")

    return lottie


async def create_emoji_pack(tgs_bytes: bytes, user_id: int, title: str) -> str:
    """Upload TGS → create custom emoji pack → return t.me/addemoji link."""
    sticker_file = await bot.upload_sticker_file(
        user_id=user_id,
        sticker=BufferedInputFile(tgs_bytes, filename="emoji.tgs"),
        sticker_format="animated",
    )

    suffix = f"by_{BOT_USERNAME}"
    uid_part = re.sub(r"[^a-z0-9]", "", str(user_id % 100000).lower())
    ts_part = re.sub(r"[^a-z0-9]", "", str(int(time.time()) % 100000))
    pack_name = f"e{uid_part}t{ts_part}_{suffix}"[:64]

    await bot.create_new_sticker_set(
        user_id=user_id,
        name=pack_name,
        title=title[:64],
        stickers=[
            InputSticker(
                sticker=sticker_file.file_id,
                emoji_list=["⭐"],
                format="animated",
            )
        ],
        sticker_type="custom_emoji",
    )

    return f"https://t.me/addemoji/{pack_name}"


def _collect_shape_points(shapes: list) -> List[List[float]]:
    """Recursively collect all static vertex coordinates from shape items."""
    pts: List[List[float]] = []
    for item in shapes:
        ty = item.get("ty")
        if ty == "gr":
            # Group — recurse into "it"
            pts.extend(_collect_shape_points(item.get("it", [])))
        elif ty == "sh":
            # Path shape
            ks = item.get("ks", {})
            k = ks.get("k", ks) if isinstance(ks, dict) else ks
            if isinstance(k, dict):
                for v in k.get("v", []):
                    if isinstance(v, list) and len(v) >= 2:
                        pts.append(v)
        elif ty == "el":
            # Ellipse — center point
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                s = item.get("s", {})
                sk = s.get("k") if isinstance(s, dict) else None
                if isinstance(sk, list) and len(sk) >= 2:
                    hw, hh = sk[0] / 2, sk[1] / 2
                    pts.append([k[0] - hw, k[1] - hh])
                    pts.append([k[0] + hw, k[1] + hh])
                else:
                    pts.append(k)
        elif ty == "rc":
            # Rectangle — center point
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                s = item.get("s", {})
                sk = s.get("k") if isinstance(s, dict) else None
                if isinstance(sk, list) and len(sk) >= 2:
                    hw, hh = sk[0] / 2, sk[1] / 2
                    pts.append([k[0] - hw, k[1] - hh])
                    pts.append([k[0] + hw, k[1] + hh])
                else:
                    pts.append(k)
    return pts


def _shift_shape_points(shapes: list, dx: float, dy: float) -> None:
    """Recursively shift all static vertex/position coordinates in shapes."""
    for item in shapes:
        ty = item.get("ty")
        if ty == "gr":
            _shift_shape_points(item.get("it", []), dx, dy)
        elif ty == "sh":
            ks = item.get("ks", {})
            k = ks.get("k", ks) if isinstance(ks, dict) else ks
            if isinstance(k, dict):
                for v in k.get("v", []):
                    if isinstance(v, list) and len(v) >= 2:
                        v[0] += dx
                        v[1] += dy
        elif ty in ("el", "rc"):
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                k[0] += dx
                k[1] += dy
        # Also shift gradient start/end points
        elif ty == "gf":
            for key in ("s", "e"):
                obj = item.get(key, {})
                k = obj.get("k") if isinstance(obj, dict) else None
                if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                    k[0] += dx
                    k[1] += dy


def recenter_lottie(d: dict) -> dict:
    """Auto-fix centering: detect if all shapes are offset from [0,0] and shift them back.

    The ROOT null at anchor=[256,256] maps local [0,0] to canvas center.
    If the AI placed shapes around [256,256] instead of [0,0],
    this function detects the offset and corrects it.
    """
    layers = d.get("layers", [])

    # Collect ALL shape points from ALL shape layers (ty=4)
    all_pts: List[List[float]] = []
    for layer in layers:
        if layer.get("ty") != 4:
            continue
        shapes = layer.get("shapes", [])
        all_pts.extend(_collect_shape_points(shapes))

    if not all_pts:
        return d

    # Calculate bounding box center
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    # If the center is far from [0,0] (more than 50px), shapes are likely
    # in absolute canvas coordinates — shift them back to local [0,0]
    THRESHOLD = 50.0
    if abs(center_x) < THRESHOLD and abs(center_y) < THRESHOLD:
        log.info("Shapes already centered (center=[%.1f, %.1f])", center_x, center_y)
        return d

    dx = -center_x
    dy = -center_y
    log.warning(
        "Shapes off-center (center=[%.1f, %.1f]). Shifting by [%.1f, %.1f] to re-center.",
        center_x, center_y, dx, dy,
    )

    for layer in layers:
        if layer.get("ty") != 4:
            continue
        _shift_shape_points(layer.get("shapes", []), dx, dy)

    return d


def normalize_lottie(d: dict) -> dict:
    """Force Telegram TGS requirements: 512×512, 60 fps, max 180 frames.

    Strips unknown top-level keys, forces ddd=0 on every layer,
    ensures assets are empty (TGS can't use external resources),
    and clamps op to 180 frames max.
    """
    # 1) Strip any top-level keys Telegram doesn't expect
    allowed_top = {"v", "fr", "ip", "op", "w", "h", "nm", "ddd", "assets", "layers", "markers"}
    for key in list(d.keys()):
        if key not in allowed_top:
            d.pop(key)

    # 2) Force mandatory values (assign, NOT setdefault)
    d["v"] = "5.7.1"
    d["w"] = 512
    d["h"] = 512
    d["fr"] = 60
    d["ip"] = 0
    d["ddd"] = 0
    d["assets"] = []          # TGS doesn't support precomp / image assets
    d.setdefault("nm", "emoji")

    op = min(int(d.get("op", 120)), 180)
    d["op"] = op

    # 3) Fix ROOT null anchor: MUST be [0,0], NOT [256,256]!
    #    With anchor=[0,0] and position=[256,256], the transform is:
    #    canvas = local * scale + [256,256]  →  local [0,0] = canvas center.
    #    With anchor=[256,256] (WRONG), transform becomes identity at scale 100%
    #    and local [0,0] maps to canvas [0,0] = TOP-LEFT corner!
    for layer in d.get("layers", []):
        if layer.get("ty") == 3:  # NULL layer (ROOT)
            ks = layer.get("ks", {})
            a = ks.get("a", {})
            if isinstance(a, dict) and a.get("a", 0) == 0:  # static (not animated)
                old_anchor = a.get("k", [])
                a["k"] = [0, 0]
                if old_anchor != [0, 0]:
                    log.warning("Fixed ROOT anchor: %s → [0, 0]", old_anchor)
            break  # only fix the first NULL layer (ROOT)

    # 4) Sanitise every layer
    for layer in d.get("layers", []):
        layer["ddd"] = 0       # 3D layers are NOT supported — force off
        layer["ao"] = 0
        layer.setdefault("st", 0)
        layer.setdefault("ip", 0)
        if "op" in layer:
            layer["op"] = min(int(layer["op"]), op)
        # Remove unsupported per-layer keys (e.g. expressions)
        for bad_key in ("ef", "hasMask", "masksProperties"):
            layer.pop(bad_key, None)

    return d


def _fix_shape_items(items: list) -> list:
    """Recursively fix broken shape items generated by AI.
    - Converts wrong 'fill' key to proper ty='fl' shape item
    - Ensures every group has ty='tr' as last item
    - Removes unknown keys from shape items
    """
    non_tr = []
    tr_item = None

    for item in items:
        if not isinstance(item, dict):
            continue
        ty = item.get("ty")

        # Fix: AI sometimes outputs {"fill": {...}} instead of {"ty": "fl", ...}
        if ty is None and "fill" in item:
            fill = item["fill"]
            color = fill.get("k", [1, 0, 0, 1]) if isinstance(fill, dict) else [1, 0, 0, 1]
            item = {"ty": "fl", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": 100}, "r": 1}
            ty = "fl"

        # Fix: AI sometimes outputs {"stroke": {...}} instead of {"ty": "st", ...}
        if ty is None and "stroke" in item:
            st = item["stroke"]
            color = st.get("k", [0, 0, 0, 1]) if isinstance(st, dict) else [0, 0, 0, 1]
            width = item.get("strokeWidth", item.get("stroke_width", 12))
            item = {"ty": "st", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": 100},
                    "w": {"a": 0, "k": width}, "lc": 2, "lj": 2}
            ty = "st"

        # Recurse into groups
        if ty == "gr" and "it" in item:
            item["it"] = _fix_shape_items(item["it"])

        if ty == "tr":
            # Ensure tr has all required fields
            item.setdefault("a", {"a": 0, "k": [0, 0]})
            item.setdefault("p", {"a": 0, "k": [0, 0]})
            item.setdefault("s", {"a": 0, "k": [100, 100]})
            item.setdefault("r", {"a": 0, "k": 0})
            item.setdefault("o", {"a": 0, "k": 100})
            item.setdefault("sk", {"a": 0, "k": 0})
            item.setdefault("sa", {"a": 0, "k": 0})
            tr_item = item
        else:
            non_tr.append(item)

    # tr must always be last
    if tr_item is None:
        tr_item = {
            "ty": "tr",
            "a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
            "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0},
        }

    return non_tr + [tr_item]


def fix_lottie_shapes(d: dict) -> dict:
    """Fix all broken shape items in all layers."""
    for layer in d.get("layers", []):
        if layer.get("ty") == 4 and "shapes" in layer:
            layer["shapes"] = _fix_shape_items(layer["shapes"])
    return d


def json_to_tgs(lottie_dict: dict) -> bytes:
    """Convert Lottie dict → gzip-compressed TGS bytes."""
    raw = json.dumps(lottie_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


def extract_json(text: str) -> dict:
    """Extract the first valid JSON object from AI response text."""
    # FIX: was broken — two statements on one line + bad regex
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first { ... } block
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("Could not extract valid JSON from AI response")


async def _call_ai(messages: list) -> str:
    """Make one API call and return raw text response."""
    loop = asyncio.get_event_loop()
    t_start = time.time()
    response = await loop.run_in_executor(
        None,
        lambda: ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            max_tokens=8000,
        ),
    )
    elapsed = time.time() - t_start
    raw_text = response.choices[0].message.content if response.choices else ""
    finish_reason = response.choices[0].finish_reason if response.choices else "N/A"
    log.info("[AI] %.1fs | finish=%s | len=%d | preview: %s",
             elapsed, finish_reason, len(raw_text), raw_text[:200])
    return raw_text


async def generate_lottie(prompt: str) -> dict:
    """Call AI API and return Lottie JSON dict. Retries with simpler prompt if needed."""

    # ── Attempt 1: full system prompt ────────────────────────────────────
    user_content = (
        f"Generate a Telegram premium emoji Lottie JSON for: {prompt}\n\n"
        "REQUIREMENTS:\n"
        "- Output ONLY raw valid JSON, no markdown, no backticks, no explanation\n"
        "- {\"v\":\"5.7.1\",\"fr\":60,\"ip\":0,\"op\":120,\"w\":512,\"h\":512,\"ddd\":0,\"assets\":[],\"layers\":[...]}\n"
        "- Layer 1: ROOT null ty=3, ind=1, ks.a=[0,0], ks.p=[256,256] with bounce animation\n"
        "- All shape layers: ty=4, parent=1, shapes wrapped in {\"ty\":\"gr\",\"it\":[...shape...,{\"ty\":\"fl\",...},{\"ty\":\"tr\",...}]}\n"
        "- Fill must use: {\"ty\":\"fl\",\"c\":{\"a\":0,\"k\":[R,G,B,1]},\"o\":{\"a\":0,\"k\":100},\"r\":1}\n"
        "- Stroke: {\"ty\":\"st\",\"c\":{\"a\":0,\"k\":[0,0,0,1]},\"o\":{\"a\":0,\"k\":100},\"w\":{\"a\":0,\"k\":14},\"lc\":2,\"lj\":2}\n"
        "- Transform MUST be last in every group: {\"ty\":\"tr\",\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[100,100]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100},\"sk\":{\"a\":0,\"k\":0},\"sa\":{\"a\":0,\"k\":0}}\n"
        "- Shape vertices centered around [0,0] (range -200 to +200), NOT absolute canvas coords\n"
        "- Include at least 5 layers: ROOT null + shadow + main body + rim light + gloss\n"
        "- ROOT scale animation: frames 0→20 scale 0→115, frames 20→28 scale 115→100\n"
        "- Idle pulse on ROOT scale: frames 30→55 100→106, 55→80 106→100, repeat\n"
        "- START JSON WITH { and END WITH }. Nothing before or after."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    log.info("[AI] Attempt 1 | model=%s | prompt=%r", AI_MODEL, prompt)

    for attempt in range(1, 4):
        try:
            raw_text = await _call_ai(messages)
            if not raw_text:
                raise ValueError("Empty response from AI")
            lottie = extract_json(raw_text)
            log.info("[AI] Attempt %d OK → %d layers", attempt, len(lottie.get("layers", [])))
            lottie = inject_text(lottie)
            return lottie
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("[AI] Attempt %d failed: %s", attempt, e)
            if attempt == 1:
                # Retry with even simpler direct prompt, no system prompt
                log.info("[AI] Retrying with simplified prompt (no system prompt)")
                messages = [
                    {
                        "role": "user",
                        "content": (
                            f"Output ONLY a valid JSON object for a Lottie animation of: {prompt}\n"
                            "Rules:\n"
                            "1. Start with { end with }. No text before or after. No markdown.\n"
                            "2. Top level: {\"v\":\"5.7.1\",\"fr\":60,\"ip\":0,\"op\":120,\"w\":512,\"h\":512,\"ddd\":0,\"assets\":[],\"layers\":[...]}\n"
                            "3. First layer: {\"ddd\":0,\"ty\":3,\"ind\":1,\"nm\":\"ROOT\",\"st\":0,\"ip\":0,\"op\":120,\"ao\":0,\"ks\":{\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[256,256]},\"s\":{\"a\":1,\"k\":[{\"t\":0,\"s\":[0,0],\"h\":0,\"o\":{\"x\":[0.175],\"y\":[0.885]},\"i\":{\"x\":[0.32],\"y\":[1.275]}},{\"t\":20,\"s\":[115,115],\"h\":0,\"o\":{\"x\":[0.33],\"y\":[0]},\"i\":{\"x\":[0.67],\"y\":[1]}},{\"t\":28,\"s\":[100,100],\"h\":0}]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100}},\"shapes\":[]}\n"
                            "4. Shape layer example: {\"ddd\":0,\"ty\":4,\"ind\":2,\"nm\":\"body\",\"st\":0,\"ip\":0,\"op\":120,\"ao\":0,\"parent\":1,\"ks\":{\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[100,100]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100}},\"shapes\":[{\"ty\":\"gr\",\"nm\":\"g\",\"it\":[{\"ty\":\"el\",\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[360,360]}},{\"ty\":\"fl\",\"c\":{\"a\":0,\"k\":[1,0,0,1]},\"o\":{\"a\":0,\"k\":100},\"r\":1},{\"ty\":\"tr\",\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[100,100]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100},\"sk\":{\"a\":0,\"k\":0},\"sa\":{\"a\":0,\"k\":0}}]}]}\n"
                            f"Make it look like: {prompt}. Use appropriate colors and shape. Output JSON only."
                        )
                    }
                ]
            elif attempt == 2:
                # Last attempt: minimal fallback heart/circle
                log.info("[AI] Attempt 3: requesting minimal valid JSON")
                messages = [
                    {
                        "role": "user",
                        "content": (
                            "Output ONLY this exact JSON with no changes except the fill color "
                            f"to match '{prompt}' (change k:[1,0,0,1] to appropriate RGB):\n"
                            "{\"v\":\"5.7.1\",\"fr\":60,\"ip\":0,\"op\":120,\"w\":512,\"h\":512,\"ddd\":0,\"assets\":[],\"layers\":["
                            "{\"ddd\":0,\"ty\":3,\"ind\":1,\"nm\":\"ROOT\",\"st\":0,\"ip\":0,\"op\":120,\"ao\":0,\"ks\":{\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[256,256]},\"s\":{\"a\":1,\"k\":[{\"t\":0,\"s\":[0,0],\"h\":0,\"o\":{\"x\":[0.175],\"y\":[0.885]},\"i\":{\"x\":[0.32],\"y\":[1.275]}},{\"t\":20,\"s\":[115,115],\"h\":0,\"o\":{\"x\":[0.33],\"y\":[0]},\"i\":{\"x\":[0.67],\"y\":[1]}},{\"t\":28,\"s\":[100,100],\"h\":0}]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100}},\"shapes\":[]},"
                            "{\"ddd\":0,\"ty\":4,\"ind\":2,\"nm\":\"body\",\"st\":0,\"ip\":0,\"op\":120,\"ao\":0,\"parent\":1,\"ks\":{\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[100,100]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100}},\"shapes\":[{\"ty\":\"gr\",\"nm\":\"g\",\"it\":[{\"ty\":\"el\",\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[380,380]}},{\"ty\":\"fl\",\"c\":{\"a\":0,\"k\":[1,0,0,1]},\"o\":{\"a\":0,\"k\":100},\"r\":1},{\"ty\":\"st\",\"c\":{\"a\":0,\"k\":[0.05,0.05,0.05,1]},\"o\":{\"a\":0,\"k\":100},\"w\":{\"a\":0,\"k\":14},\"lc\":2,\"lj\":2},{\"ty\":\"tr\",\"a\":{\"a\":0,\"k\":[0,0]},\"p\":{\"a\":0,\"k\":[0,0]},\"s\":{\"a\":0,\"k\":[100,100]},\"r\":{\"a\":0,\"k\":0},\"o\":{\"a\":0,\"k\":100},\"sk\":{\"a\":0,\"k\":0},\"sa\":{\"a\":0,\"k\":0}}]}]}]}"
                        )
                    }
                ]
        except Exception as e:
            log.error("[AI] Attempt %d unexpected error: %s", attempt, e)
            if attempt == 3:
                raise

    raise ValueError("Failed to generate valid Lottie JSON after 3 attempts")


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Генератор премиум эмодзи</b>\n\n"
        "Опиши что хочешь — я сгенерирую анимированное премиум эмодзи и создам пак прямо в Telegram.\n\n"
        "Примеры:\n"
        "• <code>красное сердце с пульсацией</code>\n"
        "• <code>зелёная галочка draw-on анимация</code>\n"
        "• <code>огонь с языками пламени</code>\n"
        "• <code>золотая звезда sparkle эффект</code>\n"
        "• <code>синяя молния</code>\n\n"
        "Получишь: <code>.json</code> файл + ссылку на пак премиум эмодзи в Telegram.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text)
async def handle_prompt(message: Message) -> None:
    prompt = message.text.strip()
    if not prompt:
        return

    status = await message.answer("Генерирую эмодзи...")

    try:
        # ── Generate Lottie JSON via AI ──────────────────────────────────────
        lottie_dict = await generate_lottie(prompt)

        # ── Validate basic structure ────────────────────────────────────────
        required_keys = {"v", "fr", "ip", "op", "w", "h", "layers"}
        missing = required_keys - lottie_dict.keys()
        if missing:
            await status.edit_text(
                f"ИИ вернул неполный JSON. Отсутствуют поля: {missing}\nПопробуй ещё раз."
            )
            return

        # FIX: normalize_lottie() was NEVER CALLED — this is the main bug!
        # Without this, Lottie may have wrong w/h/fr/op and Telegram rejects it.
        lottie_dict = normalize_lottie(lottie_dict)

        # Fix broken shape items: wrong 'fill'/'stroke' keys, missing 'tr' transforms
        lottie_dict = fix_lottie_shapes(lottie_dict)

        # Auto-fix centering: if AI placed shapes at canvas coords instead of local [0,0]
        lottie_dict = recenter_lottie(lottie_dict)

        # ── Build files ─────────────────────────────────────────────────────
        json_bytes = json.dumps(lottie_dict, ensure_ascii=False, indent=2).encode("utf-8")
        tgs_bytes = json_to_tgs(lottie_dict)

        safe_name = re.sub(r"[^\w\-]", "_", prompt[:40])
        layer_count = len(lottie_dict.get("layers", []))
        duration = lottie_dict.get("op", 0)
        fps = lottie_dict.get("fr", 60)

        # ── Send JSON file ──────────────────────────────────────────────────
        await message.answer_document(
            BufferedInputFile(json_bytes, filename=f"{safe_name}.json"),
            caption=(
                f"<b>Lottie JSON</b>\n"
                f"Запрос: <i>{prompt}</i>\n"
                f"Слоёв: {layer_count} | {duration} фреймов @ {fps}fps"
            ),
            parse_mode=ParseMode.HTML,
        )

        # ── Create premium emoji pack & send link ───────────────────────────
        await status.edit_text("Создаю пак премиум эмодзи в Telegram...")
        pack_title = prompt[:50]
        try:
            pack_link = await create_emoji_pack(tgs_bytes, message.from_user.id, pack_title)
            await status.edit_text(
                f"<b>Готово!</b> Пак премиум эмодзи создан:\n{pack_link}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as pack_err:
            log.warning("Emoji pack creation failed: %s", pack_err)
            # Fallback: send TGS file
            await message.answer_document(
                BufferedInputFile(tgs_bytes, filename=f"{safe_name}.tgs"),
                caption=(
                    f"<b>TGS файл</b> (пак не удалось создать: <code>{pack_err}</code>)\n"
                    f"Размер: {len(tgs_bytes):,} байт"
                ),
                parse_mode=ParseMode.HTML,
            )
            await status.delete()

    except (ValueError, json.JSONDecodeError) as e:
        log.exception("JSON parse error")
        await status.edit_text(
            f"ИИ вернул невалидный JSON: <code>{e}</code>\nПопробуй перефразировать запрос.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("VoidAI / unexpected error")
        # Truncate & escape error message so Telegram doesn't choke on HTML from 502 pages
        err_text = str(e)[:200].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        try:
            await status.edit_text(f"Ошибка: <code>{err_text}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            await status.edit_text(f"Ошибка API. Попробуй ещё раз через пару минут.")


async def main() -> None:
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    log.info("Starting bot @%s | model=%s", BOT_USERNAME, AI_MODEL)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        log.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass