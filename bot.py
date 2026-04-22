import logging
import io
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from PIL import Image
import colorizer

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Color palette buttons
COLOR_PRESETS = {
    "🔴 Красный": "red",
    "🟠 Оранжевый": "orange",
    "🟡 Жёлтый": "yellow",
    "🟢 Зелёный": "green",
    "🔵 Синий": "blue",
    "🟣 Фиолетовый": "purple",
    "🩷 Розовый": "pink",
    "🩵 Голубой": "cyan",
    "🤎 Коричневый": "brown",
    "🖤 Чёрно-белый": "grayscale",
    "🌈 Радуга": "rainbow",
    "✨ Случайный": "random",
    "🌅 Закат": "sunset",
    "🌊 Океан": "ocean",
    "🌿 Лес": "forest",
    "🔥 Огонь": "fire",
    "❄️ Лёд": "ice",
    "🌸 Сакура": "sakura",
    "🪐 Галактика": "galaxy",
    "☀️ Золото": "gold",
}

# Store pending stickers/images
user_pending = {}


def make_color_keyboard():
    """Create inline keyboard with color options."""
    buttons = []
    items = list(COLOR_PRESETS.items())
    for i in range(0, len(items), 2):
        row = []
        row.append(InlineKeyboardButton(items[i][0], callback_data=f"color:{items[i][1]}"))
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(items[i+1][0], callback_data=f"color:{items[i+1][1]}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🎨 Все варианты сразу", callback_data="color:all")])
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-раскрасчик стикеров!\n\n"
        "📌 Как использовать:\n"
        "• Отправь мне стикер 🎭\n"
        "• Отправь эмодзи как изображение\n"
        "• Отправь любую PNG/WEBP картинку\n\n"
        "Я перекрашу его в разные цвета! 🌈\n\n"
        "Попробуй прямо сейчас — отправь стикер!",
        parse_mode="HTML"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 <b>Справка по боту</b>\n\n"
        "<b>Поддерживаемые типы:</b>\n"
        "• Стикеры (обычные и анимированные рамки)\n"
        "• Фото и изображения\n"
        "• PNG/WEBP файлы\n\n"
        "<b>Режимы раскраски:</b>\n"
        "• Одиночные цвета (красный, синий и т.д.)\n"
        "• Градиентные темы (закат, океан, огонь...)\n"
        "• Радужный режим\n"
        "• Все цвета сразу (20 вариантов в одном сообщении)\n\n"
        "<b>Команды:</b>\n"
        "/start — начало работы\n"
        "/help — эта справка",
        parse_mode="HTML"
    )


async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sticker = update.message.sticker

    # Animated stickers (TGS) not supported for pixel editing
    if sticker.is_animated:
        await update.message.reply_text(
            "⚠️ Анимированные стикеры (.tgs) не поддерживаются.\n"
            "Отправь обычный статичный стикер или изображение!"
        )
        return

    # Video stickers
    if sticker.is_video:
        await update.message.reply_text(
            "⚠️ Видео-стикеры не поддерживаются.\n"
            "Отправь статичный стикер!"
        )
        return

    # Static sticker (WEBP)
    file = await sticker.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    user_id = update.effective_user.id
    user_pending[user_id] = buf.read()

    await update.message.reply_text(
        "🎭 Стикер получен! Выбери цвет раскраски:",
        reply_markup=make_color_keyboard()
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # highest resolution
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    user_id = update.effective_user.id
    user_pending[user_id] = buf.read()

    await update.message.reply_text(
        "🖼 Изображение получено! Выбери цвет раскраски:",
        reply_markup=make_color_keyboard()
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type in ("image/png", "image/webp", "image/jpeg", "image/gif"):
        await update.message.reply_text("❌ Поддерживаются только PNG, WEBP, JPEG изображения.")
        return

    file = await doc.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    user_id = update.effective_user.id
    user_pending[user_id] = buf.read()

    await update.message.reply_text(
        "📎 Файл получен! Выбери цвет раскраски:",
        reply_markup=make_color_keyboard()
    )


async def handle_color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    if user_id not in user_pending:
        await query.edit_message_text("❌ Стикер не найден. Отправь стикер заново.")
        return

    color_name = query.data.replace("color:", "")
    image_data = user_pending[user_id]

    await query.edit_message_text("⏳ Раскрашиваю...")

    try:
        img = Image.open(io.BytesIO(image_data)).convert("RGBA")

        if color_name == "all":
            # Send all color variants
            await query.edit_message_text("🎨 Создаю все варианты цветов...")
            
            colors_to_send = [k for k in COLOR_PRESETS.values() if k != "all"]
            media_group = []
            
            for color in colors_to_send:
                colored = colorizer.apply_color(img.copy(), color)
                out = io.BytesIO()
                colored.save(out, format="PNG")
                out.seek(0)
                media_group.append(out)

            # Send in groups of 10 (Telegram limit)
            color_names_list = [k for k in COLOR_PRESETS.keys() if "Все варианты" not in k]
            
            for i in range(0, len(media_group), 10):
                chunk = media_group[i:i+10]
                names_chunk = color_names_list[i:i+len(chunk)]
                
                from telegram import InputMediaDocument
                media = [
                    InputMediaDocument(
                        media=chunk[j],
                        filename=f"sticker_{names_chunk[j]}.png"
                    )
                    for j in range(len(chunk))
                ]
                await context.bot.send_media_group(
                    chat_id=query.message.chat_id,
                    media=media
                )
                if i + 10 < len(media_group):
                    await asyncio.sleep(0.5)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ Готово! Все 20 вариантов цветов отправлены!"
            )

        else:
            colored = colorizer.apply_color(img, color_name)
            out = io.BytesIO()
            colored.save(out, format="PNG")
            out.seek(0)

            # Find label for this color
            label = next((k for k, v in COLOR_PRESETS.items() if v == color_name), color_name)

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=out,
                filename="colored_sticker.png",
                caption=f"✅ Готово! Цвет: {label}\n\nОтправь новый стикер или выбери другой цвет!"
            )
            await query.delete_message()

    except Exception as e:
        logger.error(f"Error processing image: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при обработке изображения.\n"
            f"Попробуй отправить другой стикер.\n\nДетали: {str(e)}"
        )


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤔 Отправь мне стикер, фото или PNG/WEBP файл — я его раскрашу!\n"
        "Используй /help для справки."
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(CallbackQueryHandler(handle_color_callback, pattern=r"^color:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_unknown))

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
  
