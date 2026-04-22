"""
bot.py — Telegram Sticker Colorizer Bot
Поддерживает: статичные стикеры, анимированные (.tgs), видео, фото,
              премиум эмодзи (статичные и анимированные).
"""

import logging
import io
import os
import asyncio

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputSticker, ReplyKeyboardMarkup, MessageEntity
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
# Emoji shortcuts
# ──────────────────────────────────────────────────────────────
E_BOT    = "🤖"
E_CHECK  = "✅"
E_CROSS  = "❌"
E_STICKER= "🖼"
E_ANIM   = "✨"
E_VIDEO  = "🎬"
E_PACK   = "📦"
E_PAINT  = "🖌"
E_LINK   = "🔗"
E_INFO   = "ℹ️"
E_CLOCK  = "⏰"
E_PARTY  = "🎉"
E_PENCIL = "🖋"
E_NOTIFY = "🔔"
E_SMILE  = "🙂"
E_STAR   = "⭐"

# ──────────────────────────────────────────────────────────────
# Color palette  (label, color_key, emoji)
# ──────────────────────────────────────────────────────────────
COLOR_PRESETS = [
    ("Красный",    "red",       "🔴"),
    ("Оранжевый",  "orange",    "🟠"),
    ("Жёлтый",     "yellow",    "🟡"),
    ("Зелёный",    "green",     "🟢"),
    ("Синий",      "blue",      "🔵"),
    ("Фиолетовый", "purple",    "🟣"),
    ("Розовый",    "pink",      "🩷"),
    ("Голубой",    "cyan",      "🩵"),
    ("Коричневый", "brown",     "🤎"),
    ("Ч/Б",        "grayscale", "🖤"),
    ("Радуга",     "rainbow",   "🌈"),
    ("Случайный",  "random",    "✨"),
    ("Закат",      "sunset",    "🌅"),
    ("Океан",      "ocean",     "🌊"),
    ("Лес",        "forest",    "🌿"),
    ("Огонь",      "fire",      "🔥"),
    ("Лёд",        "ice",       "❄️"),
    ("Сакура",     "sakura",    "🌸"),
    ("Галактика",  "galaxy",    "🪐"),
    ("Золото",     "gold",      "☀️"),
]

# {user_id: {"data": bytes, "type": str, "emoji": str}}
user_pending = {}


# ──────────────────────────────────────────────────────────────
# Keyboards
# ──────────────────────────────────────────────────────────────

def make_color_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0, len(COLOR_PRESETS), 2):
        label, key, emoji = COLOR_PRESETS[i]
        row = [InlineKeyboardButton(
            text=f"{emoji} {label}",
            callback_data=f"color:{key}"
        )]
        if i + 1 < len(COLOR_PRESETS):
            label2, key2, emoji2 = COLOR_PRESETS[i + 1]
            row.append(InlineKeyboardButton(
                text=f"{emoji2} {label2}",
                callback_data=f"color:{key2}"
            ))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        text="🖌 Все 20 цветов → в пак",
        callback_data="color:all_pack"
    )])
    return InlineKeyboardMarkup(buttons)


def make_pack_link_keyboard(pack_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="🔗 Открыть пак",
            url=f"https://t.me/addstickers/{pack_name}"
        )
    ]])


def make_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[["📦 Мои паки", "ℹ️ Помощь"]],
        resize_keyboard=True,
        input_field_placeholder="Отправь стикер или премиум эмодзи 🎨"
    )


# ──────────────────────────────────────────────────────────────
# Pack management
# ──────────────────────────────────────────────────────────────

def pack_key(user_id: int, stype: str) -> str:
    return f"pack_{user_id}_{stype}"


def build_pack_name(user_id: int, bot_username: str, index: int, stype: str) -> str:
    suffix = {"animated": "a", "video": "v", "static": "s"}.get(stype, "s")
    idx = "" if index == 1 else str(index)
    return f"u{user_id}_{suffix}{idx}_by_{bot_username}"


def build_pack_title(user_id: int, index: int, stype: str) -> str:
    label = {"animated": "Анимации", "video": "Видео", "static": "Стикеры"}.get(stype, "Стикеры")
    idx = f" · {index}" if index > 1 else ""
    return f"🎨 {label} {user_id}{idx}"


async def get_pack_info(context: ContextTypes.DEFAULT_TYPE, user_id: int, stype: str) -> dict:
    bot_username = (await context.bot.get_me()).username
    key = pack_key(user_id, stype)
    if key not in context.bot_data:
        context.bot_data[key] = {"index": 1}

    index = context.bot_data[key]["index"]
    pname = build_pack_name(user_id, bot_username, index, stype)
    ptitle = build_pack_title(user_id, index, stype)

    try:
        pack = await context.bot.get_sticker_set(pname)
        count = len(pack.stickers)
        if count >= MAX_STICKERS_PER_PACK:
            index += 1
            context.bot_data[key]["index"] = index
            pname = build_pack_name(user_id, bot_username, index, stype)
            ptitle = build_pack_title(user_id, index, stype)
            return {"name": pname, "title": ptitle, "exists": False}
        return {"name": pname, "title": ptitle, "exists": True}
    except TelegramError:
        return {"name": pname, "title": ptitle, "exists": False}


async def add_to_pack(context, user_id: int, data: bytes, stype: str, emoji: str = "🎨") -> str:
    info = await get_pack_info(context, user_id, stype)
    fmt = {"animated": "animated", "video": "video", "static": "static"}.get(stype, "static")
    fname = {"animated": "sticker.tgs", "video": "sticker.webm", "static": "sticker.png"}.get(stype, "sticker.png")

    buf = io.BytesIO(data)
    buf.name = fname
    sticker_obj = InputSticker(sticker=buf, emoji_list=[emoji], format=fmt)

    if not info["exists"]:
        await context.bot.create_new_sticker_set(
            user_id=user_id,
            name=info["name"],
            title=info["title"],
            stickers=[sticker_obj],
        )
        logger.info(f"Created pack {info['name']} for user {user_id}")
    else:
        await context.bot.add_sticker_to_set(
            user_id=user_id,
            name=info["name"],
            sticker=sticker_obj,
        )
    return info["name"]


# ──────────────────────────────────────────────────────────────
# Processing helpers
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


async def colorize_and_add(context, user_id: int, pending: dict, color: str) -> str:
    stype = pending["type"]
    emoji = pending.get("emoji", "🎨")
    data = pending["data"]

    if stype == "animated":
        result = colorize_tgs(data, color)
    elif stype == "video":
        result = data
    else:
        result = process_static(data, color)

    return await add_to_pack(context, user_id, result, stype, emoji)


# ──────────────────────────────────────────────────────────────
# Premium emoji loader
# ──────────────────────────────────────────────────────────────

def _extract_custom_emoji_ids(message) -> list[str]:
    """
    Extract all unique custom_emoji_id values from message entities.
    Works for both text messages and captions.
    """
    ids = []
    seen = set()
    entities = list(message.entities or []) + list(message.caption_entities or [])
    for ent in entities:
        if ent.type == MessageEntityType.CUSTOM_EMOJI:
            eid = getattr(ent, "custom_emoji_id", None)
            if eid and eid not in seen:
                ids.append(eid)
                seen.add(eid)
    return ids


async def load_premium_emoji(context, emoji_id: str) -> dict | None:
    """
    Fetch sticker data for a custom emoji id.
    Returns {"data": bytes, "type": str, "emoji": str} or None.
    """
    try:
        stickers = await context.bot.get_custom_emoji_stickers([emoji_id])
    except Exception as ex:
        logger.error(f"get_custom_emoji_stickers failed for {emoji_id}: {ex}")
        return None

    if not stickers:
        return None

    sticker = stickers[0]
    try:
        file = await context.bot.get_file(sticker.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
    except Exception as ex:
        logger.error(f"Failed to download emoji {emoji_id}: {ex}")
        return None

    if sticker.is_animated:
        stype = "animated"
    elif sticker.is_video:
        stype = "video"
    else:
        stype = "static"

    return {
        "data": data,
        "type": stype,
        "emoji": sticker.emoji or "🎨",
    }


# ──────────────────────────────────────────────────────────────
# Command handlers
# ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{E_BOT} <b>Привет! Я раскрашиваю стикеры и премиум эмодзи!</b>\n\n"
        f"{E_STICKER} Статичные стикеры\n"
        f"{E_ANIM} Анимированные стикеры (.tgs)\n"
        f"{E_VIDEO} Видео стикеры\n"
        f"{E_STAR} Премиум эмодзи (статичные и анимированные)\n"
        f"{E_PAINT} Фото и изображения\n\n"
        f"{E_PACK} <b>У каждого пользователя свой пак.</b>\n"
        f"Когда пак заполнится (120 шт) — создам новый автоматически!\n\n"
        f"<b>Как отправить премиум эмодзи:</b>\n"
        f"Просто напиши сообщение с любым премиум эмодзи — я его поймаю и раскрашу!\n\n"
        f"{E_SMILE} Попробуй прямо сейчас 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=make_main_keyboard()
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{E_INFO} <b>Справка</b>\n\n"
        f"{E_PENCIL} <b>Как пользоваться:</b>\n"
        f"1. Отправь стикер или премиум эмодзи\n"
        f"2. Нажми нужный цвет\n"
        f"3. Получи ссылку на пак {E_CHECK}\n\n"
        f"{E_STAR} <b>Премиум эмодзи:</b>\n"
        f"Просто напиши любое премиум эмодзи в чат — бот автоматически его распознает и предложит раскрасить.\n"
        f"Работает и со статичными, и с анимированными!\n\n"
        f"{E_PACK} <b>Паки:</b>\n"
        f"• Статичные → отдельный пак\n"
        f"• Анимированные → отдельный пак\n"
        f"• Видео → отдельный пак\n\n"
        f"{E_PAINT} «Все 20 цветов» добавляет всё сразу!\n\n"
        f"{E_NOTIFY} Команды:\n"
        f"/mypack — ссылки на твои паки\n"
        f"/help — эта справка",
        parse_mode=ParseMode.HTML
    )


async def cmd_mypack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    lines = [f"{E_PACK} <b>Твои паки:</b>\n"]
    found = False

    for stype, icon in [("static", E_STICKER), ("animated", E_ANIM), ("video", E_VIDEO)]:
        key = pack_key(user_id, stype)
        if key not in context.bot_data:
            continue
        max_index = context.bot_data[key].get("index", 1)
        for idx in range(1, max_index + 1):
            pname = build_pack_name(user_id, bot_username, idx, stype)
            try:
                pack = await context.bot.get_sticker_set(pname)
                lines.append(
                    f'{icon} <a href="https://t.me/addstickers/{pname}">{pack.title}</a>'
                    f" — {len(pack.stickers)} шт."
                )
                found = True
            except Exception:
                pass

    if not found:
        lines.append(f"{E_SMILE} Паков пока нет. Отправь стикер и раскрась его!")

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
        stype, msg = "animated", f"{E_ANIM} <b>Анимированный стикер получен!</b> Выбери цвет:"
    elif sticker.is_video:
        stype, msg = "video", f"{E_VIDEO} <b>Видео-стикер получен!</b> Выбери цвет:"
    else:
        stype, msg = "static", f"{E_STICKER} <b>Стикер получен!</b> Выбери цвет:"

    user_pending[user_id] = {"data": buf.getvalue(), "type": stype, "emoji": emoji}
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=make_color_keyboard())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text(
        f"{E_STICKER} <b>Фото получено!</b> Выбери цвет:",
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.mime_type not in ("image/png", "image/webp", "image/jpeg"):
        await update.message.reply_text(f"{E_CROSS} Поддерживаются PNG, WEBP, JPEG.")
        return
    file = await doc.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text(
        f"{E_STICKER} <b>Файл получен!</b> Выбери цвет:",
        parse_mode=ParseMode.HTML,
        reply_markup=make_color_keyboard()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles text messages.
    If the message contains premium emoji (custom_emoji entity) — intercepts and colorizes.
    Otherwise handles keyboard buttons or shows hint.
    """
    message = update.message
    text = message.text or ""
    user_id = update.effective_user.id

    # ── Check for premium emoji entities ──
    emoji_ids = _extract_custom_emoji_ids(message)
    if emoji_ids:
        # Take the first premium emoji from the message
        emoji_id = emoji_ids[0]
        count = len(emoji_ids)

        status_msg = await message.reply_text(
            f"{E_STAR} <b>Премиум эмодзи найдено!</b> Загружаю...",
            parse_mode=ParseMode.HTML
        )

        pending = await load_premium_emoji(context, emoji_id)
        if pending is None:
            await status_msg.edit_text(
                f"{E_CROSS} Не удалось загрузить это эмодзи. Попробуй другое.",
                parse_mode=ParseMode.HTML
            )
            return

        user_pending[user_id] = pending
        stype = pending["type"]

        if stype == "animated":
            type_label = "Анимированное премиум эмодзи"
            type_icon = E_ANIM
        elif stype == "video":
            type_label = "Видео премиум эмодзи"
            type_icon = E_VIDEO
        else:
            type_label = "Премиум эмодзи"
            type_icon = E_STAR

        extra = f"\n<i>В сообщении {count} эмодзи — раскрашу первое</i>" if count > 1 else ""
        await status_msg.edit_text(
            f"{type_icon} <b>{type_label} получен!</b> Выбери цвет:{extra}",
            parse_mode=ParseMode.HTML,
            reply_markup=make_color_keyboard()
        )
        return

    # ── Keyboard buttons ──
    if text in ("📦 Мои паки", "Мои паки"):
        await cmd_mypack(update, context)
    elif text in ("ℹ️ Помощь", "Помощь"):
        await cmd_help(update, context)
    else:
        await message.reply_text(
            f"{E_SMILE} Отправь стикер, фото, PNG/WEBP файл или <b>премиум эмодзи</b> — раскрашу!\n"
            f"/help — справка",
            parse_mode=ParseMode.HTML
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
            f"{E_CROSS} Стикер не найден. Отправь стикер или эмодзи заново.",
            parse_mode=ParseMode.HTML
        )
        return

    color_name = query.data.replace("color:", "")
    pending = user_pending[user_id]
    stype = pending["type"]
    type_icon = {"animated": E_ANIM, "video": E_VIDEO, "static": E_STICKER}.get(stype, E_STICKER)

    if color_name == "all_pack":
        await query.edit_message_text(
            f"{E_PAINT} <b>Добавляю все 20 цветов в пак...</b>\n"
            f"{E_CLOCK} Это займёт ~40 секунд, подожди!",
            parse_mode=ParseMode.HTML
        )
        added = 0
        pack_name_result = None
        errors = []

        for _, key, _ in COLOR_PRESETS:
            try:
                pack_name_result = await colorize_and_add(context, user_id, pending, key)
                added += 1
                await asyncio.sleep(0.5)
            except Exception as ex:
                logger.error(f"Error adding color {key}: {ex}")
                errors.append(key)

        if pack_name_result:
            err_note = f"\n{E_CROSS} Не удалось: {len(errors)} шт." if errors else ""
            await query.edit_message_text(
                f"{E_PARTY} <b>Готово! Добавлено {added}/20 стикеров!</b>{err_note}",
                parse_mode=ParseMode.HTML,
                reply_markup=make_pack_link_keyboard(pack_name_result)
            )
        else:
            await query.edit_message_text(
                f"{E_CROSS} Не удалось создать пак. Попробуй снова."
            )
        return

    # Single color
    await query.edit_message_text(
        f"{type_icon} <b>Раскрашиваю и добавляю в пак...</b>",
        parse_mode=ParseMode.HTML
    )
    try:
        pack_name_result = await colorize_and_add(context, user_id, pending, color_name)
        label = next((lbl for lbl, k, _ in COLOR_PRESETS if k == color_name), color_name)
        await query.edit_message_text(
            f"{E_CHECK} <b>Добавлено!</b> Цвет: {label}",
            parse_mode=ParseMode.HTML,
            reply_markup=make_pack_link_keyboard(pack_name_result)
        )
    except TelegramError as ex:
        logger.error(f"Telegram error: {ex}")
        await query.edit_message_text(
            f"{E_CROSS} <b>Ошибка Telegram:</b> {ex}\n\nПопробуй ещё раз.",
            parse_mode=ParseMode.HTML
        )
    except Exception as ex:
        logger.error(f"Processing error: {ex}")
        await query.edit_message_text(
            f"{E_CROSS} <b>Ошибка обработки:</b> {ex}",
            parse_mode=ParseMode.HTML
        )


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("mypack", cmd_mypack))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(M
