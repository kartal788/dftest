from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
from Backend.helper.custom_filter import CustomFilters

# ------------ SADECE ENV'DEN DATABASE AL ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]

if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")

MONGO_URL = db_urls[1]

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

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_link(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Lütfen bir link girin. Örnek: /ekle <link>")

    link = message.command[1]
    await init_db()

    updated_count = 0

    # --- MOVIE Koleksiyonunu Güncelle ---
    async for movie in movie_col.find({}):
        updated = False
        for telegram_item in movie.get("telegram", []):
            if "id" in telegram_item:
                telegram_item["id"] = link  # burada id'yi link ile değiştiriyoruz
                updated = True
        if updated:
            await movie_col.update_one({"_id": movie["_id"]}, {"$set": movie})
            updated_count += 1

    # --- TV Koleksiyonunu Güncelle ---
    async for tv_show in series_col.find({}):
        updated = False
        for season in tv_show.get("seasons", []):
            for episode in season.get("episodes", []):
                for telegram_item in episode.get("telegram", []):
                    if "id" in telegram_item:
                        telegram_item["id"] = link  # burada da id'yi link ile değiştiriyoruz
                        updated = True
        if updated:
            await series_col.update_one({"_id": tv_show["_id"]}, {"$set": tv_show})
            updated_count += 1

    await message.reply_text(f"✅ Link güncellendi. Toplam {updated_count} kayıtta id değiştirildi.")

