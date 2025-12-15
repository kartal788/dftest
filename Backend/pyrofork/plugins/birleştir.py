from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import PTN
from datetime import datetime
from Backend.logger import LOGGER
from Backend.helper.metadata import metadata

# ------------ ENV ------------

db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

MONGO_URL = db_urls[1]
TMDB_API = os.getenv("TMDB_API", "")

# ------------ MONGO ------------

client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    if db:
        return
    db_names = await client.list_database_names()
    db = client[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ DELETE CONFIRM ------------

awaiting_confirmation = {}

# ------------ /ekle ------------

@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client, message):
    await init_db()

    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = message.command[1]
    filename = " ".join(message.command[2:])

    try:
        PTN.parse(filename)
    except Exception as e:
        await message.reply_text(f"Dosya adı ayrıştırılamadı: {e}")
        return

    meta = await metadata(filename, message.chat.id, message.id)
    if not meta:
        await message.reply_text("Metadata alınamadı.")
        return

    telegram_entry = {
        "quality": meta.get("quality"),
        "id": url,
        "name": filename,
        "size": "bilinmiyor"
    }

    base_record = {
        "tmdb_id": meta.get("tmdb_id"),
        "imdb_id": meta.get("imdb_id"),
        "db_index": 1,
        "title": meta.get("title"),
        "genres": meta.get("genres", []),
        "description": meta.get("description"),
        "rating": meta.get("rate"),
        "release_year": meta.get("year"),
        "poster": meta.get("poster"),
        "backdrop": meta.get("backdrop"),
        "logo": meta.get("logo"),
        "cast": meta.get("cast", []),
        "runtime": meta.get("runtime"),
        "media_type": meta.get("media_type"),
        "updated_on": datetime.utcnow()
    }

    collection = series_col if meta.get("media_type") == "tv" else movie_col

    update_doc = {
        "$set": {
            "updated_on": datetime.utcnow()
        },
        "$addToSet": {
            "telegram": telegram_entry
        },
        "$setOnInsert": {
            **base_record,
            "telegram": []
        }
    }

    # TV için season / episode sadece ilk insert’te eklenir
    if meta.get("media_type") == "tv":
        update_doc["$setOnInsert"]["seasons"] = [
            {
                "season_number": meta.get("season_number"),
                "episodes": [
                    {
                        "episode_number": meta.get("episode_number"),
                        "title": meta.get("episode_title"),
                        "episode_backdrop": meta.get("episode_backdrop"),
                        "overview": meta.get("episode_overview"),
                        "released": meta.get("episode_released"),
                        "telegram": []
                    }
                ]
            }
        ]

    await collection.update_one(
        {"tmdb_id": meta.get("tmdb_id")},
        update_doc,
        upsert=True
    )

    await message.reply_text(f"✅ {meta.get('title')} başarıyla eklendi.")

# ------------ /sil ------------

@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client, message):
    user_id = message.from_user.id

    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\n"
        "Onaylamak için **Evet**, iptal için **Hayır** yazın.\n"
        "⏱ 60 saniye süreniz var."
    )

    if user_id in awaiting_confirmation:
        awaiting_confirmation[user_id].cancel()

    async def timeout():
        await asyncio.sleep(60)
        if user_id in awaiting_confirmation:
            awaiting_confirmation.pop(user_id, None)
            await message.reply_text("⏰ Süre doldu, işlem iptal edildi.")

    task = asyncio.create_task(timeout())
    awaiting_confirmation[user_id] = task

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    await init_db()

    text = message.text.strip().lower()
    if text == "evet":
        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})

        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            f"✅ Silme tamamlandı.\n\n"
            f"Filmler: {movie_count}\n"
            f"Diziler: {series_count}"
        )
    else:
        await message.reply_text("❌ Silme iptal edildi.")
