import io
import logging
import re
from aiogram import Router, Bot, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart

from colorizer import recolor_static_webp, recolor_tgs, recolor_webm, recolor_static_png
from pack_manager import add_sticker_to_pack

logger = logging.getLogger(__name__)
router = Router()


class RecolorStates(StatesGroup):
    waiting_for_hex = State()


COLORS = {
    "🔴 Красный": "FF0000",
    "🔵 Синий": "0055FF",
    "🟢 Зелёный": "00CC44",
    "🟡 Жёлтый": "FFD700",
    "⚫ Чёрный": "111111",
    "⚪ Белый": "FFFFFF",
    "🟠 Оранжевый": "FF6600",
    "🟣 Фиолетовый": "8800FF",
    "🩵 Голубой": "00CCFF",
}

# Store pending sticker data in FSM
class PendingSticker:
    def __init__(self, file_id, file_unique_id, sticker_type, emoji):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.sticker_type = sticker_type  # "static" | "animated" | "video"
        self.emoji = emoji


def build_color_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, (label, hex_val) in enumerate(COLORS.items()):
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"color:{hex_val}"
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        text="🎨 Свой HEX цвет",
        callback_data="color:custom"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "<b>🎨 Бот для перекраски стикеров и эмодзи</b>\n\n"
        "Просто отправь мне <b>стикер</b> или <b>премиум эмодзи</b> — "
        "я покрашу его в любой цвет и добавлю в твой персональный пак! 🚀\n\n"
        "Поддерживаются: статичные, обычные и анимированные стикеры/эмодзи.",
        parse_mode="HTML"
    )


@router.message(F.sticker)
async def handle_sticker(message: Message, state: FSMContext):
    sticker = message.sticker
    # Determine type
    if sticker.is_animated:
        stype = "animated"
    elif sticker.is_video:
        stype = "video"
    else:
        stype = "static"

    emoji = sticker.emoji or "🎨"

    await state.update_data(
        file_id=sticker.file_id,
        file_unique_id=sticker.file_unique_id,
        sticker_type=stype,
        emoji=emoji,
        is_custom_emoji=False,
    )

    type_label = {"static": "Статичный", "animated": "Анимированный", "video": "Видео"}[stype]

    await message.answer(
        f"<b>Стикер получен!</b> ({type_label})\n\n"
        f"Выбери цвет для покраски:",
        reply_markup=build_color_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_for_hex(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != RecolorStates.waiting_for_hex:
        return

    hex_input = message.text.strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", hex_input):
        await message.answer(
            "❌ Неверный формат HEX. Пример: <code>FF5500</code> или <code>#FF5500</code>",
            parse_mode="HTML"
        )
        return

    await state.update_data(chosen_hex=hex_input)
    data = await state.get_data()
    await state.set_state(None)
    await process_recolor(message, message.bot, data, hex_input)


@router.callback_query(F.data.startswith("color:"))
async def handle_color_choice(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]

    if value == "custom":
        await state.set_state(RecolorStates.waiting_for_hex)
        await callback.message.edit_text(
            "✏️ Введи HEX цвет (например: <code>FF5500</code> или <code>#1A2B3C</code>):",
            parse_mode="HTML"
        )
        await callback.answer()
        return

    hex_color = value
    data = await state.get_data()
    await state.set_state(None)

    await callback.message.edit_text("⏳ Перекрашиваю...")
    await callback.answer()

    await process_recolor(callback.message, callback.bot, data, hex_color)


async def process_recolor(message: Message, bot: Bot, data: dict, hex_color: str):
    file_id = data.get("file_id")
    sticker_type = data.get("sticker_type", "static")
    emoji = data.get("emoji", "🎨")

    if not file_id:
        await message.answer("❌ Стикер не найден. Отправь стикер снова.")
        return

    try:
        await message.answer("🎨 Скачиваю и перекрашиваю...")

        # Download sticker
        file = await bot.get_file(file_id)
        file_bytes_io = io.BytesIO()
        await bot.download_file(file.file_path, destination=file_bytes_io)
        file_bytes = file_bytes_io.getvalue()

        # Recolor based on type
        if sticker_type == "animated":
            recolored = recolor_tgs(file_bytes, hex_color)
        elif sticker_type == "video":
            recolored = recolor_webm(file_bytes, hex_color)
        else:
            # Try WebP first, fallback to PNG
            try:
                recolored = recolor_static_webp(file_bytes, hex_color)
            except Exception:
                recolored = recolor_static_png(file_bytes, hex_color)

        await message.answer("📦 Добавляю в пак...")

        # Add to pack
        pack_name = await add_sticker_to_pack(
            bot=bot,
            user=message.chat,
            sticker_file=recolored,
            sticker_format=sticker_type,
            emoji_list=[emoji],
        )

        pack_url = f"https://t.me/addemoji/{pack_name}"

        await message.answer(
            f"✅ <b>Готово!</b> Стикер покрашен в <code>#{hex_color}</code> и добавлен в твой пак!\n\n"
            f"👉 <a href='{pack_url}'>Открыть пак эмодзи</a>\n\n"
            f"<i>Отправь ещё стикер для покраски 🎨</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception(f"Error during recolor: {e}")
        await message.answer(
            f"❌ <b>Ошибка при обработке:</b> <code>{str(e)[:200]}</code>\n\n"
            "Попробуй ещё раз или отправь другой стикер.",
            parse_mode="HTML"
  )
  
