import io
import logging
import re
from aiogram import Router, Bot, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart

from colorizer import recolor_static_webp, recolor_tgs, recolor_webm
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
        "Scrolling plates - генератор номерных знаков\n\n"
        "• Получай крутые ежедневные награды в течение недели\n"
        "• Крути н/з своей страны со всеми регионами\n"
        "• Доступны страны: Россия, Украина, Беларусь, Казахстан\n"
        "• Украшай номерные знаки разными модификаторами и рамками\n"
        "• Создавай комнату и играй с друзьями в разные режимы\n"
        "• Меняй настройки игры под себя, выбери свою удобную тему\n"
        "• Продавай свои номера игрокам на маркетплейсе\n\n"
        "Присоединяйся, вводи свой регион и крути номера👇",
        parse_mode="HTML"
    )


@router.message(F.sticker)
async def handle_sticker(message: Message, state: FSMContext):
    sticker = message.sticker

    if sticker.is_animated:
        stype = "animated"
    elif sticker.is_video:
        stype = "video"
    else:
        stype = "static"

    emoji = sticker.emoji or "🎨"

    await state.update_data(
        file_id=sticker.file_id,
        sticker_type=stype,
        emoji=emoji,
    )

    type_label = {"static": "Статичный", "animated": "Анимированный", "video": "Видео"}[stype]

    await message.answer(
        f"<b>Стикер получен!</b> ({type_label})\n\nВыбери цвет:",
        reply_markup=build_color_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.entities)
async def handle_message_with_entities(message: Message, state: FSMContext):
    """Handle messages containing premium custom emoji."""
    if not message.entities:
        return

    custom_emoji_entities = [
        e for e in message.entities if e.type == "custom_emoji"
    ]

    if not custom_emoji_entities:
        return

    # Take first custom emoji
    entity = custom_emoji_entities[0]
    custom_emoji_id = entity.custom_emoji_id

    # Get emoji sticker info
    try:
        stickers = await message.bot.get_custom_emoji_stickers([custom_emoji_id])
        if not stickers:
            await message.answer("❌ Не удалось получить данные эмодзи.")
            return

        sticker = stickers[0]
        if sticker.is_animated:
            stype = "animated"
        elif sticker.is_video:
            stype = "video"
        else:
            stype = "static"

        emoji_char = sticker.emoji or "🎨"

        await state.update_data(
            file_id=sticker.file_id,
            sticker_type=stype,
            emoji=emoji_char,
        )

        type_label = {"static": "Статичный", "animated": "Анимированный", "video": "Видео"}[stype]

        await message.answer(
            f"<b>Премиум эмодзи получено!</b> ({type_label})\n\nВыбери цвет:",
            reply_markup=build_color_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Error handling custom emoji: {e}")
        await message.answer(f"❌ Ошибка: <code>{str(e)[:200]}</code>", parse_mode="HTML")


@router.message(RecolorStates.waiting_for_hex)
async def handle_hex_input(message: Message, state: FSMContext):
    hex_input = message.text.strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", hex_input):
        await message.answer(
            "❌ Неверный формат. Пример: <code>FF5500</code>",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    await state.clear()
    await process_recolor(message, message.bot, data, hex_input)


@router.callback_query(F.data.startswith("color:"))
async def handle_color_choice(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]

    if value == "custom":
        await state.set_state(RecolorStates.waiting_for_hex)
        await callback.message.edit_text(
            "✏️ Введи HEX цвет (например: <code>FF5500</code>):",
            parse_mode="HTML"
        )
        await callback.answer()
        return

    data = await state.get_data()
    await state.clear()
    await callback.message.edit_text("⏳ Перекрашиваю...")
    await callback.answer()
    await process_recolor(callback.message, callback.bot, data, value)


async def process_recolor(message: Message, bot: Bot, data: dict, hex_color: str):
    file_id = data.get("file_id")
    sticker_type = data.get("sticker_type", "static")
    emoji = data.get("emoji", "🎨")
    user = message.chat

    if not file_id:
        await message.answer("❌ Стикер не найден. Отправь снова.")
        return

    try:
        # Download
        file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        file_bytes = buf.getvalue()

        # Recolor
        if sticker_type == "animated":
            recolored = recolor_tgs(file_bytes, hex_color)
        elif sticker_type == "video":
            recolored = recolor_webm(file_bytes, hex_color)
        else:
            recolored = recolor_static_webp(file_bytes, hex_color)

        # Add to pack
        pack_name = await add_sticker_to_pack(
            bot=bot,
            user_id=user.id,
            username=getattr(user, "username", None) or str(user.id),
            sticker_file=recolored,
            sticker_format=sticker_type,
            emoji_list=[emoji],
        )

        pack_url = f"https://t.me/addemoji/{pack_name}"

        await message.answer(
            f"✅ <b>Готово!</b> Покрашено в <code>#{hex_color}</code>\n\n"
            f"👉 <a href='{pack_url}'>Открыть пак эмодзи</a>\n\n"
            f"<i>Отправь ещё стикер 🎨</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception(f"Recolor error: {e}")
        await message.answer(
            f"❌ <b>Ошибка:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML"
    )
