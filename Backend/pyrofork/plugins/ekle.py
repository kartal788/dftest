from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import json
from io import BytesIO

# ------------ SADECE ENV'DEN DATABASE AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]

if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

MONGO_URL = db_urls[1]

# ------------ MONGO BAĞLANTISI ------------
client_db = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    db_names = await client_db.list_database_names()
    db = client_db[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_json_to_db(client, message):
    """
    /ekle komutu JSON dosyası veya JSON string'i alır ve database'e ekler.
    """

    await init_db()  # DB başlat

    try:
        # 1️⃣ JSON dosyası varsa
        if message.document:
            file = await message.download(in_memory=True)
            data = json.load(BytesIO(file))
        
        # 2️⃣ JSON string olarak verildiyse
        elif len(message.command) > 1:
            json_text = " ".join(message.command[1:])
            data = json.loads(json_text)
        
        else:
            await message.reply_text("⚠️ Lütfen JSON dosyası veya JSON string gönderin.")
            return

        movies_added = 0
        for movie in data.get("movie", []):
            movie["db_index"] = 1
            await movie_col.insert_one(movie)
            movies_added += 1

        tv_added = 0
        for tv in data.get("tv", []):
            tv["db_index"] = 1
            await series_col.insert_one(tv)
            tv_added += 1

        await message.reply_text(f"✅ Veritabanına kaydedildi:\nFilmler: {movies_added}\nDiziler: {tv_added}")

    except json.JSONDecodeError:
        await message.reply_text("❌ Geçersiz JSON formatı!")
    except Exception as e:
        await message.reply_text(f"❌ Hata: {e}")
