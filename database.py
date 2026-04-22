import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "bot.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                pack_name TEXT,
                pack_index INTEGER DEFAULT 1,
                sticker_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_user(user_id: int, username: str, pack_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, username, pack_name, pack_index, sticker_count) VALUES (?, ?, ?, 1, 0)",
            (user_id, username, pack_name)
        )
        await db.commit()


async def update_user_pack(user_id: int, pack_name: str, pack_index: int, sticker_count: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET pack_name = ?, pack_index = ?, sticker_count = ? WHERE user_id = ?",
            (pack_name, pack_index, sticker_count, user_id)
        )
        await db.commit()


async def increment_sticker_count(user_id: int) -> int:
    """Increment sticker count and return new count."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET sticker_count = sticker_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
        async with db.execute("SELECT sticker_count FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
          
