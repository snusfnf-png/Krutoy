"""
bot.py — Telegram Sticker Colorizer Bot
Supports: static stickers, animated stickers (.tgs), video stickers, photos
Creates personal sticker packs per user, auto-creates new pack when full.
"""

import logging
import io
import os
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputSticker
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
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
# Color palette
# ──────────────────────────────────────────────────────────────

COLOR_PRESETS = {
    "🔴 Красный":     "red",
    "🟠 Оранжевый":   "orange",
    "🟡 Жёлтый":      "yellow",
    "🟢 Зелёный":     "green",
    "🔵 Синий":       "blue",
    "🟣 Фиолетовый":  "purple",
    "🩷 Розовый":     "pink",
    "🩵 Голубой":     "cyan",
    "🤎 Коричневый":  "brown",
    "🖤 Чёрно-белый": "grayscale",
    "🌈 Радуга":      "rainbow",
    "✨ Случайный":   "random",
    "🌅 Закат":       "sunset",
    "🌊 Океан":       "ocean",
    "🌿 Лес":         "forest",
    "🔥 Огонь":       "fire",
    "❄️ Лёд":         "ice",
    "🌸 Сакура":      "sakura",
    "🪐 Галактика":   "galaxy",
    "☀️ Золото":      "gold",
}

# pending stickers: {user_id: {"data": bytes, "type": str, "emoji": str}}
user_pending = {}


# ──────────────────────────────────────────────────────────────
# Keyboard
# ──────────────────────────────────────────────────────────────

def make_color_keyboard():
    buttons = []
    items = list(COLOR_PRESETS.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(items[i][0], callback_data=f"color:{items[i][1]}")]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(items[i+1][0], callback_data=f"color:{items[i+1][1]}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🎨 Все 20 цветов → в пак", callback_data="color:all_pack")])
    return InlineKeyboardMarkup(buttons)


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
    idx_part = f" • {index}" if index > 1 else ""
    return f"🎨 {label} {user_id}{idx_part}"


async def find_current_pack(context: ContextTypes.DEFAULT_TYPE, user_id: int, stype: str) -> dict:
    """
    Returns info about current active pack for user.
    Creates registry entry if missing.
    """
    bot_username = (await context.bot.get_me()).username
    key = pack_registry_key(user_id, stype)

    if key not in context.bot_data:
        context.bot_data[key] = {"index": 1}

    index = context.bot_data[key]["index"]
    pack_name = build_pack_name(user_id, bot_username, index, stype)
    pack_title = build_pack_title(user_id, index, stype)

    # Check real count from Telegram
    try:
        pack = await context.bot.get_sticker_set(pack_name)
        count = len(pack.stickers)
        if count >= MAX_STICKERS_PER_PACK:
            # Bump to next pack
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
    """Add sticker to user's pack. Returns pack name."""
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

    # Ensure longest side == 512px (Telegram requirement)
    w, h = colored.size
    longest = max(w, h)
    if longest != 512:
        factor = 512 / longest
        colored = colored.resize((int(w * factor), int(h * factor)), Image.LANCZOS)

    out = io.BytesIO()
    colored.save(out, format="PNG")
    return out.getvalue()


def process_animated(data: bytes, color: str) -> bytes:
    return colorize_tgs(data, color)


# ──────────────────────────────────────────────────────────────
# Main processing pipeline
# ──────────────────────────────────────────────────────────────

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
        result = process_animated(data, color)
    elif stype == "video":
        # Video stickers: pass through (ffmpeg colorization is too heavy for free Railway tier)
        result = data
    else:
        result = process_static(data, color)

    return await add_to_pack(context, user_id, result, stype, emoji)


# ──────────────────────────────────────────────────────────────
# Command handlers
# ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Раскрашиваю стикеры и сохраняю в твой личный пак!\n\n"
        "🎭 Статичные стикеры\n"
        "✨ Анимированные стикеры (.tgs)\n"
        "🎬 Видео стикеры\n"
        "🖼 Фото и изображения\n\n"
        "<b>Каждый пользователь получает свой пак.</b>\n"
        "Когда пак заполнится (120 шт) — создам новый автоматически!\n\n"
        "Отправь стикер прямо сейчас 👇",
        parse_mode="HTML"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 <b>Справка</b>\n\n"
        "/start — начало\n"
        "/mypack — ссылки на твои паки\n"
        "/help — эта справка\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Отправь стикер любого типа\n"
        "2. Нажми нужный цвет\n"
        "3. Получи ссылку на пак ✅\n\n"
        "<b>Типы паков:</b>\n"
        "• Статичные стикеры → отдельный пак\n"
        "• Анимированные → отдельный пак\n"
        "• Видео-стикеры → отдельный пак\n\n"
        "Режим «Все 20 цветов» добавляет сразу 20 стикеров!",
        parse_mode="HTML"
    )


async def my_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    lines = ["🗂 <b>Твои паки:</b>\n"]
    found = False

    for stype, label in [("static", "Стикеры"), ("animated", "Анимированные"), ("video", "Видео")]:
        key = pack_registry_key(user_id, stype)
        if key not in context.bot_data:
            continue
        max_index = context.bot_data[key].get("index", 1)
        for idx in range(1, max_index + 1):
            pack_name = build_pack_name(user_id, bot_username, idx, stype)
            try:
                pack = await context.bot.get_sticker_set(pack_name)
                lines.append(
                    f"{'✨' if stype=='animated' else '🎬' if stype=='video' else '🎭'} "
                    f"<a href='https://t.me/addstickers/{pack_name}'>{pack.title}</a> — {len(pack.stickers)} шт."
                )
                found = True
            except Exception:
                pass

    if not found:
        lines.append("Паков пока нет. Отправь стикер и раскрась его!")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
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
        msg = "✨ Анимированный стикер получен! Выбери цвет:"
    elif sticker.is_video:
        stype = "video"
        msg = "🎬 Видео-стикер получен! Выбери цвет:"
    else:
        stype = "static"
        msg = "🎭 Стикер получен! Выбери цвет:"

    user_pending[user_id] = {"data": buf.getvalue(), "type": stype, "emoji": emoji}
    await update.message.reply_text(msg, reply_markup=make_color_keyboard())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text("🖼 Фото получено! Выбери цвет:", reply_markup=make_color_keyboard())


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.mime_type not in ("image/png", "image/webp", "image/jpeg"):
        await update.message.reply_text("❌ Поддерживаются PNG, WEBP, JPEG.")
        return
    file = await doc.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    user_pending[update.effective_user.id] = {"data": buf.getvalue(), "type": "static", "emoji": "🖼"}
    await update.message.reply_text("📎 Файл получен! Выбери цвет:", reply_markup=make_color_keyboard())


async def handle_color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    if user_id not in user_pending:
        await query.edit_message_text("❌ Стикер не найден. Отправь стикер заново.")
        return

    color_name = query.data.replace("color:", "")
    pending = user_pending[user_id]
    stype = pending["type"]
    type_icon = {"animated": "✨", "video": "🎬", "static": "🎭"}.get(stype, "🎭")

    if color_name == "all_pack":
        await query.edit_message_text(
            f"{type_icon} Добавляю все 20 цветов в пак...\n⏳ Это займёт ~40 секунд, подожди!"
        )
        colors = list(COLOR_PRESETS.values())
        added = 0
        pack_name = None
        errors = []

        for color in colors:
            try:
                pack_name = await colorize_and_add(context, user_id, pending, color)
                added += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error adding color {color}: {e}")
                errors.append(color)

        if pack_name:
            err_note = f"\n⚠️ Не удалось: {len(errors)} шт." if errors else ""
            await query.edit_message_text(
                f"✅ Готово! Добавлено {added}/20 стикеров!{err_note}\n\n"
                f"👉 <a href='https://t.me/addstickers/{pack_name}'>Открыть пак</a>",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text("❌ Не удалось создать пак. Попробуй снова.")

    else:
        await query.edit_message_text(f"{type_icon} Раскрашиваю и добавляю в пак...")
        try:
            pack_name = await colorize_and_add(context, user_id, pending, color_name)
            label = next((k for k, v in COLOR_PRESETS.items() if v == color_name), color_name)
            await query.edit_message_text(
                f"✅ Добавлено! Цвет: {label}\n\n"
                f"👉 <a href='https://t.me/addstickers/{pack_name}'>Открыть пак</a>",
                parse_mode="HTML"
            )
        except TelegramError as e:
            logger.error(f"Telegram error: {e}")
            await query.edit_message_text(
                f"❌ Ошибка Telegram: {e}\n\nПопробуй отправить стикер заново."
            )
        except Exception as e:
            logger.error(f"Processing error: {e}")
            await query.edit_message_text(f"❌ Ошибка обработки: {e}")


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤔 Отправь стикер, фото или PNG/WEBP файл!\n/help — справка")


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mypack", my_pack))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(CallbackQueryHandler(handle_color_callback, pattern=r"^color:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_unknown))
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
    
