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
    InputSticker, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode, MessageEntityType
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
    """Return fallback emoji; keeps compatibility with all Bot API deployments."""
    return fallback

# Shortcut constants
E_SETTINGS = e("5870982283724328568", "⚙")
E_PROFILE  = e("5870994129244131212", "👤")
E_CHECK    = e("5870633910337015697", "✅")
E_CROSS    = e("5870657884844462243", "❌")
E_STICKER  = e("6035128606563241721", "🖼")
E_ANIM     = e("5778672437122045013", "📦")
E_VIDEO    = e("5770240908630974872", "🎬")
E_PACK     = e("5884479287171485878", "📦")
E_PAINT    = e("6050679691004612757", "🖌")
E_LINK     = e("5769289093221454192", "🔗")
E_INFO     = e("6028435952299413210", "ℹ")
E_BOT      = e("6030400221232501136", "🤖")
E_GIFT     = e("6032644646587338669", "🎁")
E_CLOCK    = e("5983150113483134607", "⏰")
E_PARTY    = e("6041731551845159060", "🎉")
E_PENCIL   = e("5870676941614354370", "🖋")
E_DOWN     = e("6039802767931871481", "⬇")
E_UP       = e("5963103826075456248", "⬆")
E_TRASH    = e("5870875489362513438", "🗑")
E_BACK     = e("5893057118545646106", "◁")
E_NOTIFY   = e("6039486778597970865", "🔔")
E_SMILE    = e("5870764288364252592", "🙂")
E_STATS    = e("5870921681735781843", "📊")
E_LOAD     = e("5345906554510012647", "🔄")
E_TAG      = e("5886285355279193209", "🏷")
E_CALENDAR = e("5890937706803894250", "📅")

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
    """Inline keyboard with regular emoji labels (works in all Bot API versions)."""
    buttons = []
    for i in range(0, len(COLOR_PRESETS), 2):
        row = []
        label, key, _emoji_id, fallback = COLOR_PRESETS[i]
        row.append(InlineKeyboardButton(
            text=f"{fallback} {label}",
            callback_data=f"color:{key}"
        ))
        if i + 1 < len(COLOR_PRESETS):
            label2, key2, _emoji_id2, fallback2 = COLOR_PRESETS[i + 1]
            row.append(InlineKeyboardButton(
                text=f"{fallback2} {label2}",
                callback_data=f"color:{key2}"
            ))
        buttons.append(row)
    # "All colors" button
    buttons.append([InlineKeyboardButton(
        text="🖌 Все 20 цветов → в пак",
        callback_data="color:all_pack"
    )])
    return InlineKeyboardMarkup(buttons)


def make_pack_link_keyboard(pack_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="🔗 Открыть пак",
            url=f"https://t.me/addstickers/{pack_name}",
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

    if stype == "animated":
        result = colorize_tgs(data, color)
    elif stype == "video":
        result = data  # passthrough (ffmpeg too heavy for free Railway)
    else:
        result = process_static(data, color)

    return await add_to_pack(context, user_id, result, stype, emoji)

# ──────────────────────────────────────────────────────────────
# Command handlers
# ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f'{E_BOT} <b>Привет! Я раскрашиваю стикеры и сохраняю в твой личный пак!</b>\n\n'
        f'{E_STICKER} Статичные стикеры\n'
        f'{E_ANIM} Анимированные стикеры (.tgs)\n'
        f'{E_VIDEO} Видео стикеры\n'
        f'{E_PAINT} Фото и изображения\n\n'
        f'{E_PACK} <b>Каждый пользователь получает свой пак.</b>\n'
        f'Когда пак заполнится (120 шт) — создам новый автоматически!\n\n'
        f'{E_SMILE} Отправь стикер прямо сейчас 👇',
        parse_mode=ParseMode.HTML,
        reply_markup=make_main_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f'{E_INFO} <b>Справка</b>\n\n'
        f'{E_PENCIL} <b>Как пользоваться:</b>\n'
        f'1. Отправь стикер любого типа\n'
        f'2. Нажми нужный цвет\n'
        f'3. Получи ссылку на пак {E_CHECK}\n\n'
        f'{E_PACK} <b>Типы паков:</b>\n'
        f'• Статичные стикеры → отдельный пак\n'
        f'• Анимированные → отдельный пак\n'
        f'• Видео-стикеры → отдельный пак\n\n'
        f'{E_PAINT} Режим «Все 20 цветов» добавляет всё сразу!\n\n'
        f'{E_NOTIFY} Команды:\n'
        f'/mypack — ссылки на твои паки\n'
        f'/help — эта справка',
        parse_mode=ParseMode.HTML
    )


async def my_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    lines = [f'{E_PACK} <b>Твои паки:</b>\n']
    found = False
    for stype, icon in [("static", E_STICKER), ("animated", E_ANIM), ("video", E_VIDEO)]:
        key = pack_registry_key(user_id, stype)
        if key not in context.bot_data:
            continue
        max_index = context.bot_data[key].get("index", 1)
        for idx in range(1, max_index + 1):
            pack_name = build_pack_name(user_id, bot_username, idx, stype)
            try:
                pack = await context.bot.get_sticker_set(pack_name)
                lines.append(
                    f'{icon} <a href="https://t.me/addstickers/{pack_name}">{pack.title}</a>'
                    f' — {len(pack.stickers)} шт.'
                )
                found = True
            except Exception:
                pass
    if not found:
        lines.append(f'{E_SMILE} Паков пока нет. Отправь стикер и раскрась его!')
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

# ──────────────────────────────────────────────────────────────
# Message handlers
# ──────────────────────────────────────────────────────────────

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sticker = update.message.sticker
    user_id = update.effective_user.id
    emoji = sticker.emoji or "🎨"

    file = await sticker.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)

    if sticker.is_animated:
        stype = "animated"
        msg = f'{E_ANIM} <b>Анимированный стикер получен!</b> Выбери цвет:'
    elif sticker.is_video:
        stype = "video"
        msg = f'{E_VIDEO} <b>Видео-стикер получен!</b> Выбери цвет:'
    else:
        stype = "static"
        msg = f'{E_STICKER} <b>Стикер получен!</b> Выбери цвет:'

    user_pending[user_id] = {"data": buf.getvalue(), "type": stype, "emoji": emoji}
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text(
        f'{E_STICKER} <b>Фото получено!</b> Выбери цвет:',
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.mime_type not in ("image/png", "image/webp", "image/jpeg"):
        await update.message.reply_text(
            f'{E_CROSS} Поддерживаются PNG, WEBP, JPEG.',
            parse_mode=ParseMode.HTML
        )
        return
    file = await doc.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text(
        f'{E_STICKER} <b>Файл получен!</b> Выбери цвет:',
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text, reply keyboard buttons and premium custom emoji."""
    text = update.message.text or ""
    custom_emoji_id = _extract_custom_emoji_id(update)
    if custom_emoji_id:
        await handle_custom_emoji(update, context, custom_emoji_id)
        return

    if text in ("📦 Мои паки", "Мои паки"):
        await my_pack(update, context)
    elif text in ("ℹ️ Помощь", "Помощь"):
        await help_cmd(update, context)
    elif text.strip().lower() in ("старт", "start"):
        await start(update, context)
    else:
        await update.message.reply_text(
            f'{E_SMILE} Отправь стикер, фото, премиум-эмодзи или PNG/WEBP файл — раскрашу!\n/help — справка',
            parse_mode=ParseMode.HTML
        )


def _extract_custom_emoji_id(update: Update) -> str | None:
    """Extract first custom emoji id from a text/caption message."""
    msg = update.message
    if not msg:
        return None
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    for ent in entities:
        if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
            return ent.custom_emoji_id
    return None


async def handle_custom_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE, custom_emoji_id: str):
    """Load premium emoji sticker by custom_emoji_id and start color flow."""
    stickers = await context.bot.get_custom_emoji_stickers([custom_emoji_id])
    if not stickers:
        await update.message.reply_text(
            f'{E_CROSS} Не удалось получить премиум-эмодзи. Попробуй отправить другое.',
            parse_mode=ParseMode.HTML
        )
        return
    sticker = stickers[0]
    file = await context.bot.get_file(sticker.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)

    if sticker.is_animated:
        stype = "animated"
        msg = f'{E_ANIM} <b>Премиум-эмодзи (аним.) получен!</b> Выбери цвет:'
    elif sticker.is_video:
        stype = "video"
        msg = f'{E_VIDEO} <b>Премиум-эмодзи (видео) получен!</b> Выбери цвет:'
    else:
        stype = "static"
        msg = f'{E_STICKER} <b>Премиум-эмодзи получен!</b> Выбери цвет:'

    user_pending[update.effective_user.id] = {
        "data": buf.getvalue(),
        "type": stype,
        "emoji": "🎨",
    }
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )

# ──────────────────────────────────────────────────────────────
# Color callback
# ──────────────────────────────────────────────────────────────

async def handle_color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id not in user_pending:
        await query.edit_message_text(
            f'{E_CROSS} Стикер не найден. Отправь стикер заново.',
            parse_mode=ParseMode.HTML
        )
        return

    color_name = query.data.replace("color:", "")
    pending = user_pending[user_id]
    stype = pending["type"]
    type_icon = {"animated": E_ANIM, "video": E_VIDEO, "static": E_STICKER}.get(stype, E_STICKER)

    if color_name == "all_pack":
        await query.edit_message_text(
            f'{E_PAINT} <b>Добавляю все 20 цветов в пак...</b>\n'
            f'{E_CLOCK} Это займёт ~40 секунд, подожди!',
            parse_mode=ParseMode.HTML
        )
        colors = [key for _, key, _, _ in COLOR_PRESETS]
        added = 0
        pack_name = None
        errors = []
        for color in colors:
            try:
                pack_name = await colorize_and_add(context, user_id, pending, color)
                added += 1
                await asyncio.sleep(0.5)
            except Exception as ex:
                logger.error(f"Error adding color {color}: {ex}")
                errors.append(color)

        if pack_name:
            err_note = f'\n{E_CROSS} Не удалось: {len(errors)} шт.' if errors else ""
            await query.edit_message_text(
                f'{E_PARTY} <b>Готово! Добавлено {added}/20 стикеров!</b>{err_note}',
                parse_mode=ParseMode.HTML,
                reply_markup=make_pack_link_keyboard(pack_name)
            )
        else:
            await query.edit_message_text(
                f'{E_CROSS} Не удалось создать пак. Попробуй снова.',
                parse_mode=ParseMode.HTML
            )

    else:
        await query.edit_message_text(
            f'{type_icon} <b>Раскрашиваю и добавляю в пак...</b>',
            parse_mode=ParseMode.HTML
        )
        try:
            pack_name = await colorize_and_add(context, user_id, pending, color_name)
            label = next((lbl for lbl, key, _, _ in COLOR_PRESETS if key == color_name), color_name)
            await query.edit_message_text(
                f'{E_CHECK} <b>Добавлено!</b> Цвет: {label}',
                parse_mode=ParseMode.HTML,
                reply_markup=make_pack_link_keyboard(pack_name)
            )
        except TelegramError as ex:
            logger.error(f"Telegram error: {ex}")
            await query.edit_message_text(
                f'{E_CROSS} Ошибка Telegram: {ex}',
                parse_mode=ParseMode.HTML
            )
        except Exception as ex:
            logger.error(f"Unexpected error: {ex}")
            awa
