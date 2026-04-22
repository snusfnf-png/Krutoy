import os
import logging
from aiogram import Bot
from aiogram.types import User, InputSticker
import io

from database import get_user, create_user, update_user_pack, increment_sticker_count

logger = logging.getLogger(__name__)

MAX_STICKERS_PER_PACK = 200
BOT_USERNAME_CACHE = {}


async def get_bot_username(bot: Bot) -> str:
    if "username" not in BOT_USERNAME_CACHE:
        me = await bot.get_me()
        BOT_USERNAME_CACHE["username"] = me.username
    return BOT_USERNAME_CACHE["username"]


def make_pack_name(user_id: int, bot_username: str, index: int) -> str:
    """Generate pack short name."""
    return f"recolor_{user_id}_{index}_by_{bot_username}"


def make_pack_title(index: int) -> str:
    return f"🎨 Recolored #{index}"


async def ensure_user_pack(bot: Bot, user: User) -> dict:
    """Get or create user's current pack. Returns user DB row."""
    user_data = await get_user(user.id)

    if not user_data:
        bot_username = await get_bot_username(bot)
        pack_name = make_pack_name(user.id, bot_username, 1)
        await create_user(user.id, user.username or str(user.id), pack_name)
        user_data = await get_user(user.id)

    return user_data


async def add_sticker_to_pack(
    bot: Bot,
    user: User,
    sticker_file: bytes,
    sticker_format: str,  # "static", "animated", "video"
    emoji_list: list[str] = None,
) -> str:
    """
    Add a recolored sticker to user's emoji pack.
    Returns the pack name (link).
    sticker_format: "static" | "animated" | "video"
    """
    if emoji_list is None:
        emoji_list = ["🎨"]

    user_data = await ensure_user_pack(bot, user)
    bot_username = await get_bot_username(bot)

    pack_name = user_data["pack_name"]
    pack_index = user_data["pack_index"]
    sticker_count = user_data["sticker_count"]

    # Check if we need a new pack
    if sticker_count >= MAX_STICKERS_PER_PACK:
        pack_index += 1
        pack_name = make_pack_name(user.id, bot_username, pack_index)
        sticker_count = 0
        await update_user_pack(user.id, pack_name, pack_index, sticker_count)

    # Determine sticker type suffix
    if sticker_format == "animated":
        file_suffix = ".tgs"
        tg_format = "animated"
    elif sticker_format == "video":
        file_suffix = ".webm"
        tg_format = "video"
    else:
        file_suffix = ".webp"
        tg_format = "static"

    # Upload file
    sticker_file_io = io.BytesIO(sticker_file)
    sticker_file_io.name = f"sticker{file_suffix}"

    # Try to add to existing pack first
    pack_exists = False
    try:
        pack_info = await bot.get_sticker_set(pack_name)
        pack_exists = True
    except Exception:
        pack_exists = False

    if not pack_exists:
        # Create new pack as emoji pack
        try:
            sticker_file_io.seek(0)
            uploaded = await bot.upload_sticker_file(
                user_id=user.id,
                sticker=sticker_file_io,
                sticker_format=tg_format,
            )
            await bot.create_new_sticker_set(
                user_id=user.id,
                name=pack_name,
                title=make_pack_title(pack_index),
                stickers=[
                    InputSticker(
                        sticker=uploaded.file_id,
                        format=tg_format,
                        emoji_list=emoji_list,
                    )
                ],
                sticker_type="custom_emoji",
            )
            sticker_count = 1
        except Exception as e:
            logger.error(f"Failed to create pack: {e}")
            raise
    else:
        # Add to existing pack
        try:
            sticker_file_io.seek(0)
            uploaded = await bot.upload_sticker_file(
                user_id=user.id,
                sticker=sticker_file_io,
                sticker_format=tg_format,
            )
            await bot.add_sticker_to_set(
                user_id=user.id,
                name=pack_name,
                sticker=InputSticker(
                    sticker=uploaded.file_id,
                    format=tg_format,
                    emoji_list=emoji_list,
                ),
            )
            sticker_count += 1
        except Exception as e:
            logger.error(f"Failed to add sticker to pack: {e}")
            raise

    await update_user_pack(user.id, pack_name, pack_index, sticker_count)
    return pack_name
      
