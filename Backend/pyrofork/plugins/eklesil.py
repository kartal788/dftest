import os
import re
import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from motor.motor_asyncio import AsyncIOMotorClient
from themoviedb import aioTMDb
import PTN
import aiohttp

from Backend.helper.custom_filter import CustomFilters

# ---------------- ENV ----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb+srv")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

# ---------------- DB ----------------
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(10)
awaiting_confirmation = {}

# ---------------- HELPERS ----------------
def pixeldrain_api(url: str):
    m = re.search(r"/u/([a-zA-Z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url

async def pixeldrain_filename(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                data = await r.json()
                return data.get("name")
    except:
        return None

async def filesize(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url) as r:
                size = int(r.headers.get("Content-Length", 0))
    except:
        return "UNKNOWN"

    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except:
        return None

# ---------------- /EKLE ----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text(
            "❌ Kullanım:\n"
            "`/ekle <pixeldrain_link> [dosya adı]`"
        )

    raw_link = args[0]
    api_link = pixeldrain_api(raw_link)

    # 👉 Dosya adı önceliği
    if len(args) > 1:
        filename = " ".join(args[1:])
    else:
        filename = await pixeldrain_filename(api_link)

    if not filename:
        return await message.reply_text("❌ Dosya adı alınamadı")

    parsed = PTN.parse(filename)

    title = parsed.get("title")
    year = parsed.get("year")
    season = parsed.get("season")
    episode = parsed.get("episode")
    quality = parsed.get("resolution", "UNKNOWN")

    size = await filesize(api_link)

    async with API_SEMAPHORE:
        if season and episode:
            results = await tmdb.search().tv(query=title)
            media_type = "tv"
            col = series_col
        else:
            results = await tmdb.search().movies(query=title, year=year)
            media_type = "movie"
            col = movie_col

    if not results:
        return await message.reply_text("❌ TMDB bulunamadı")

    meta = results[0]
    details = await (
        tmdb.tv(meta.id).details()
        if media_type == "tv"
        else tmdb.movie(meta.id).details()
    )

    base = {
        "tmdb_id": meta.id,
        "title": meta.title if media_type == "movie" else meta.name,
        "rating": meta.vote_average,
        "release_year": year_from(
            meta.release_date if media_type == "movie" else meta.first_air_date
        ),
        "updated_on": str(datetime.utcnow()),
    }

    # -------- MOVIE --------
    if media_type == "movie":
        doc = await col.find_one({"tmdb_id": meta.id})

        if not doc:
            doc = {
                **base,
                "media_type": "movie",
                "telegram": [{
                    "quality": quality,
                    "id": api_link,
                    "name": filename,
                    "size": size
                }]
            }
            await col.insert_one(doc)
        else:
            doc["telegram"].append({
                "quality": quality,
                "id": api_link,
                "name": filename,
                "size": size
            })
            doc["updated_on"] = str(datetime.utcnow())
            await col.replace_one({"_id": doc["_id"]}, doc)

    # -------- TV --------
    else:
        doc = await col.find_one({"tmdb_id": meta.id})

        if not doc:
            doc = {
                **base,
                "media_type": "tv",
                "seasons": [{
                    "season_number": season,
                    "episodes": [{
                        "episode_number": episode,
                        "telegram": [{
                            "quality": quality,
                            "id": api_link,
                            "name": filename,
                            "size": size
                        }]
                    }]
                }]
            }
            await col.insert_one(doc)
        else:
            s = next((x for x in doc["seasons"] if x["season_number"] == season), None)
            if not s:
                s = {"season_number": season, "episodes": []}
                doc["seasons"].append(s)

            e = next((x for x in s["episodes"] if x["episode_number"] == episode), None)
            if not e:
                e = {"episode_number": episode, "telegram": []}
                s["episodes"].append(e)

            e["telegram"].append({
                "quality": quality,
                "id": api_link,
                "name": filename,
                "size": size
            })

            doc["updated_on"] = str(datetime.utcnow())
            await col.replace_one({"_id": doc["_id"]}, doc)

    await message.reply_text(f"✅ Eklendi:\n`{filename}`")

# ---------------- /SİL ----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    awaiting_confirmation[uid] = True

    await message.reply_text(
        "⚠️ **TÜM VERİLER SİLİNECEK!**\n\n"
        "Onay için **Evet**, iptal için **Hayır** yaz."
    )

@Client.on_message(filters.regex("(?i)^(evet|hayır)$") & filters.private & CustomFilters.owner)
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        m = await movie_col.count_documents({})
        t = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(f"🗑 Silindi\n🎬 Film: {m}\n📺 Dizi: {t}")
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
