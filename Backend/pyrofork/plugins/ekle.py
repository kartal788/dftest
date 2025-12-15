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

# ------------ Onay Bekleyen Kullanıcıları Sakla ------------
awaiting_confirmation = {}  # user_id -> asyncio.Task

# ------------ Yardımcı Fonksiyon: Pixeldrain Dosya Adı ------------
async def get_pixeldrain_file_name(link):
    """
    Pixeldrain /u/<id> linkini /api/file/<id> formatına çevirip dosya adını çek.
    """
    if "pixeldrain.com/u/" in link:
        link = link.replace("/u/", "/api/file/")
    file_name = "unknown_file"
    if "/api/file/" in link:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        file_name = data.get("name", "unknown_file")
        except Exception:
            pass
    return link, file_name

# ------------ /ekle Komutu ------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_file_link(client, message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Lütfen bir link girin. Örnek: /ekle <link>")

    raw_link = message.command[1]
    link, file_name = await get_pixeldrain_file_name(raw_link)

    await init_db()
    await message.reply_text(f"⏳ `{file_name}` için metadata çekiliyor...", quote=True)

    try:
        meta = await metadata(file_name, channel=message.chat.id, msg_id=message.message_id)
        if not meta:
            return await message.reply_text(f"❌ Metadata alınamadı: `{file_name}`")

        tg_item = {
            "quality": meta.get("quality") or "Unknown",
            "id": link,
            "name": file_name,
            "size": "Unknown"
        }

        # Film ekleme/güncelleme
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

        # Dizi ekleme/güncelleme
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
        try:
            awaiting_confirmation[user_id].cancel()
        except Exception:
            pass

    async def timeout():
        try:
            await asyncio.sleep(60)
            if user_id in awaiting_confirmation:
                awaiting_confirmation.pop(user_id, None)
                await message.reply_text("⏰ Zaman doldu, silme işlemi iptal edildi.")
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(timeout())
    awaiting_confirmation[user_id] = task

# ------------ Onay Mesajı: Evet / Hayır ------------
@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def handle_confirmation(client, message):
    user_id = message.from_user.id
    if user_id not in awaiting_confirmation:
        return

    text = message.text.strip().lower()
    awaiting_confirmation[user_id].cancel()
    awaiting_confirmation.pop(user_id, None)

    if text == "evet":
        await message.reply_text("🗑️ Silme işlemi başlatılıyor...")
        await init_db()

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
