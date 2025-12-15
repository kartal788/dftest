from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import json
import asyncio

# ------------ ENV'DEN DATABASE AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]

if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

MONGO_URL = db_urls[1]
client_db = AsyncIOMotorClient(MONGO_URL)

db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    db_names = await client_db.list_database_names()
    if not db_names:
        raise Exception("Hiç DB bulunamadı!")
    db = client_db[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private)
async def add_json_to_db(client, message):
    await init_db()

    try:
        data = None

        # 1️⃣ Dosya gönderilmişse
        if message.document:
            file_path = await message.download()
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

        # 2️⃣ Inline JSON gönderilmişse
        elif len(message.text.split()) > 1:
            json_text = message.text.split(None, 1)[1]
            data = json.loads(json_text)

        else:
            await message.reply_text("⚠️ JSON dosyası veya JSON string gönderin.")
            return

        # 3️⃣ Verileri DB'ye ekle
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

        await message.reply_text(
            f"✅ Veritabanına kaydedildi:\nFilmler: {movies_added}\nDiziler: {tv_added}"
        )

    except json.JSONDecodeError:
        await message.reply_text("❌ Geçersiz JSON formatı!")
    except Exception as e:
        await message.reply_text(f"❌ Hata: {e}")
