import os
import logging
from aiogram import Bot
from aiogram.types import InputSticker, BufferedInputFile

from database import get_user, create_user, update_user_pack

logger = logging.getLogger(__name__)

MAX_STICKERS_PER_PACK = 200
BOT_USERNAME_CACHE = {}


async def get_bot_username(bot: Bot) -> str:
    if "username" not in BOT_USERNAME_CACHE:
        me = await bot.get_me()
        BOT_USERNAME_CACHE["username"] = me.username
    return BOT_USERNAME_CACHE["username"]


def make_pack_name(user_id: int, bot_username: str, index: int) -> str:
    return f"recolor_{user_id}_{index}_by_{bot_username}"


def make_pack_title(index: int) -> str:
    return f"Recolored {index}"


async def ensure_user_pack(bot: Bot, user_id: int, username: str) -> dict:
    user_data = await get_user(user_id)
    if not user_data:
        bot_username = await get_bot_username(bot)
        pack_name = make_pack_name(user_id, bot_username, 1)
        await create_user(user_id, username, pack_name)
        user_data = await get_user(user_id)
    return user_data


async def add_sticker_to_pack(
    bot: Bot,
    user_id: int,
    username: str,
    sticker_file: bytes,
    sticker_format: str,
    emoji_list: list = None,
) -> str:
    if emoji_list is None:
        emoji_list = ["🎨"]

    user_data = await ensure_user_pack(bot, user_id, username)
    bot_username = await get_bot_username(bot)

    pack_name = user_data["pack_name"]
    pack_index = user_data["pack_index"]
    sticker_count = user_data["sticker_count"]

    if sticker_count >= MAX_STICKERS_PER_PACK:
        pack_index += 1
        pack_name = make_pack_name(user_id, bot_username, pack_index)
        sticker_count = 0
        await update_user_pack(user_id, pack_name, pack_index, sticker_count)

    if sticker_format == "animated":
        suffix = ".tgs"
    elif sticker_format == "video":
        suffix = ".webm"
    else:
        suffix = ".webp"

    # Correct way in aiogram 3: BufferedInputFile
    input_file = BufferedInputFile(sticker_file, filename=f"sticker{suffix}")

    uploaded = await bot.upload_sticker_file(
        user_id=user_id,
        sticker=input_file,
        sticker_format=sticker_format,
    )

    pack_exists = False
    try:
        await bot.get_sticker_set(pack_name)
        pack_exists = True
    except Exception:
        pack_exists = False

    input_sticker = InputSticker(
        sticker=uploaded.file_id,
        format=sticker_format,
        emoji_list=emoji_list,
    )

    if not pack_exists:
        await bot.create_new_sticker_set(
            user_id=user_id,
            name=pack_name,
            title=make_pack_title(pack_index),
            stickers=[input_sticker],
            sticker_type="custom_emoji",
        )
        sticker_count = 1
    else:
        await bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_name,
            sticker=input_sticker,
        )
        sticker_count += 1

    await update_user_pack(user_id, pack_name, pack_index, sticker_count)
    return pack_name
    
