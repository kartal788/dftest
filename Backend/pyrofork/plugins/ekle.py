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

    deleted_ids = []

    # --- MOVIE Koleksiyonunu Güncelle ---
    async for movie in movie_col.find({}):
        updated = False
        for telegram_item in movie.get("telegram", []):
            if "id" in telegram_item:
                deleted_ids.append(telegram_item["id"])
                telegram_item.pop("id")
                telegram_item["link"] = link
                updated = True
        if updated:
            await movie_col.update_one({"_id": movie["_id"]}, {"$set": movie})

    # --- TV Koleksiyonunu Güncelle ---
    async for tv_show in series_col.find({}):
        updated = False
        for season in tv_show.get("seasons", []):
            for episode in season.get("episodes", []):
                for telegram_item in episode.get("telegram", []):
                    if "id" in telegram_item:
                        deleted_ids.append(telegram_item["id"])
                        telegram_item.pop("id")
                        telegram_item["link"] = link
                        updated = True
        if updated:
            await series_col.update_one({"_id": tv_show["_id"]}, {"$set": tv_show})

    # --- Silinen ID’leri logla ---
    if deleted_ids:
        with open("deleted_ids.txt", "a", encoding="utf-8") as f:
            for _id in deleted_ids:
                f.write(_id + "\n")

    await message.reply_text(f"✅ Link eklendi. Toplam {len(deleted_ids)} id silindi ve kaydedildi.")
