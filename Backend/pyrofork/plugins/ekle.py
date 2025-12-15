import aiohttp
from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import metadata
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio

# ------------ DATABASE ------------
db_raw = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
if len(db_urls) < 2:
    raise Exception("İkinci DATABASE bulunamadı!")
MONGO_URL = db_urls[1]

client = AsyncIOMotorClient(MONGO_URL)
db = None
movie_col = None
series_col = None

async def init_db():
    global db, movie_col, series_col
    if db is not None:
        return
    db_names = await client.list_database_names()
    db = client[db_names[0]]
    movie_col = db["movie"]
    series_col = db["tv"]

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file_link(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Lütfen bir link girin. Örnek: /ekle <link>")

    link = message.command[1]

    # Pixeldrain linkini API formatına çevir
    if "pixeldrain.com/u/" in link:
        link = link.replace("/u/", "/api/file/")

    # Dosya adını almak için Pixeldrain API kullan
    file_name = "unknown_file"
    if "pixeldrain.com/api/file/" in link:
        async with aiohttp.ClientSession() as session:
            async with session.get(link) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    file_name = data.get("name", "unknown_file")

    await init_db()
    await message.reply_text(f"⏳ `{file_name}` için metadata çekiliyor...", quote=True)

    try:
        # Metadata helper ile bilgileri çek
        meta = await metadata(file_name, channel=message.chat.id, msg_id=message.message_id)
        if not meta:
            return await message.reply_text(f"❌ Metadata alınamadı: `{file_name}`")

        # Telegram bilgisi ekle
        size = "Unknown"
        tg_item = {
            "quality": meta.get("quality") or "Unknown",
            "id": link,
            "name": file_name,
            "size": size
        }

        if meta.get("media_type") == "movie":
            existing = await movie_col.find_one({"imdb_id": meta["imdb_id"]})
            if existing:
                existing_telegram = existing.get("telegram", [])
                if tg_item["id"] not in [t["id"] for t in existing_telegram]:
                    existing_telegram.append(tg_item)
                    await movie_col.update_one({"_id": existing["_id"]}, {"$set": {"telegram": existing_telegram}})
                await message.reply_text(f"✅ Film zaten var, link telegram listesine eklendi: `{file_name}`")
            else:
                meta["telegram"] = [tg_item]
                await movie_col.insert_one(meta)
                await message.reply_text(f"✅ Film başarıyla eklendi: `{file_name}`")

        elif meta.get("media_type") == "tv":
            season_number = meta.get("season_number") or 1
            episode_number = meta.get("episode_number") or 1

            existing = await series_col.find_one({"imdb_id": meta["imdb_id"]})
            if existing:
                seasons = existing.get("seasons", [])
                season_obj = next((s for s in seasons if s["season_number"] == season_number), None)
                if not season_obj:
                    season_obj = {"season_number": season_number, "episodes": []}
                    seasons.append(season_obj)

                episode_obj = next((e for e in season_obj["episodes"] if e["episode_number"] == episode_number), None)
                if not episode_obj:
                    episode_obj = {
                        "episode_number": episode_number,
                        "title": meta.get("episode_title"),
                        "telegram": [tg_item]
                    }
                    season_obj["episodes"].append(episode_obj)
                else:
                    if tg_item["id"] not in [t["id"] for t in episode_obj.get("telegram", [])]:
                        episode_obj["telegram"].append(tg_item)

                await series_col.update_one({"_id": existing["_id"]}, {"$set": {"seasons": seasons}})
                await message.reply_text(f"✅ Dizi zaten var, sezon/episode güncellendi: `{file_name}`")
            else:
                meta["seasons"] = [{
                    "season_number": season_number,
                    "episodes": [{
                        "episode_number": episode_number,
                        "title": meta.get("episode_title"),
                        "telegram": [tg_item]
                    }]
                }]
                await series_col.insert_one(meta)
                await message.reply_text(f"✅ Dizi başarıyla eklendi: `{file_name}`")
        else:
            await message.reply_text(f"❌ Desteklenmeyen medya türü: `{file_name}`")

    except Exception as e:
        await message.reply_text(f"❌ Hata oluştu: `{str(e)}`")
