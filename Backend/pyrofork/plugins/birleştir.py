from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import PTN
from datetime import datetime
from Backend.helper.encrypt import encode_string
from Backend.logger import LOGGER
from Backend.helper.metadata import metadata  # sizin verdiğiniz metadata fonksiyonu

# ------------ ENV'DEN AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")
MONGO_URL = db_urls[1]
TMDB_API = os.getenv("TMDB_API", "")

# ------------ MONGO BAĞLANTISI ------------
client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    db_names = await client.list_database_names()
    db = client[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ Onay Bekleyen Kullanıcıları Sakla ------------
awaiting_confirmation = {}  # user_id -> asyncio.Task

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file(client, message):
    await init_db()
    if len(message.command) < 3:
        await message.reply_text("Kullanım: /ekle <URL> <DosyaAdı>")
        return

    url = message.command[1]
    filename = " ".join(message.command[2:])

    # PTN ile ayrıştır
    try:
        parsed = PTN.parse(filename)
    except Exception as e:
        await message.reply_text(f"Dosya adı ayrıştırılamadı: {e}")
        return

    # Metadata çek
    meta = await metadata(filename, message.chat.id, message.id)
    if not meta:
        await message.reply_text("Metadata çekilemedi.")
        return

    # MongoDB kaydı hazırla
    record = {
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
        "updated_on": datetime.utcnow(),
        "telegram": [
            {
                "quality": meta.get("quality"),
                "id": url,
                "name": filename,
                "size": "bilinmiyor"  # opsiyonel: gerçek boyut eklenebilir
            }
        ]
    }

    # TV ise seasons/episodes yapısı ekle
    if meta.get("media_type") == "tv":
        record["seasons"] = [
            {
                "season_number": meta.get("season_number"),
                "episodes": [
                    {
                        "episode_number": meta.get("episode_number"),
                        "title": meta.get("episode_title"),
                        "episode_backdrop": meta.get("episode_backdrop"),
                        "overview": meta.get("episode_overview"),
                        "released": meta.get("episode_released"),
                        "telegram": record["telegram"]
                    }
                ]
            }
        ]

    # MongoDB ekleme (upsert)
    collection = series_col if meta.get("media_type") == "tv" else movie_col
    await collection.update_one(
        {"tmdb_id": record["tmdb_id"]},
        {"$setOnInsert": record, "$push": {"telegram": record["telegram"][0]}},
        upsert=True
    )

    await message.reply_text(f"✅ {meta.get('title')} başarıyla eklendi.")

# ------------ /sil Komutu ------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def request_delete(client, message):
    user_id = message.from_user.id
    await message.reply_text(
        "⚠️ Tüm veriler silinecek!\n"
        "Onaylamak için **Evet**, iptal etmek için **Hayır** yazın.\n"
        "⏱ 60 saniye içinde cevap vermezsen işlem otomatik iptal edilir."
    )

    if user_id in awaiting_confirmation:
        awaiting_confirmation[user_id].cancel()

    async def timeout():
        await asyncio.sleep(60)
        if user_id in awaiting_confirmation:
            awaiting_confirmation.pop(user_id, None)
            await message.reply_text("⏰ Zaman doldu, silme işlemi otomatik olarak iptal edildi.")

    task = asyncio.create_task(timeout())
    awaiting_confirmation[user_id] = task

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    await init_db()
    if text == "evet":
        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})

        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            f"✅ Silme işlemi tamamlandı.\n\n"
            f"📌 Filmler silindi: {movie_count}\n"
            f"📌 Diziler silindi: {series_count}"
        )
    elif text == "hayır":
        await message.reply_text("❌ Silme işlemi iptal edildi.")
