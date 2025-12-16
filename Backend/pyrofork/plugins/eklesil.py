import os
import re
import asyncio
from datetime import datetime

import aiohttp
import PTN
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient
from themoviedb import aioTMDb

from Backend.helper.custom_filter import CustomFilters

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.startswith("mongodb")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

# ----------------- DB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(10)
awaiting_confirmation = {}

# ----------------- HELPERS -----------------
def pixeldrain_api(url: str) -> str:
    m = re.match(r"https?://pixeldrain\.com/u/([A-Za-z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url


async def pixeldrain_head(url):
    async with aiohttp.ClientSession() as s:
        async with s.head(url, allow_redirects=True) as r:
            return r.headers


def size_format(size):
    size = int(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024


def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except:
        return None


# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "Kullanım:\n`/ekle PIXELDRAIN_LINK DOSYA_ADI`"
        )

    raw_link = message.command[1]
    filename = " ".join(message.command[2:])

    api_link = pixeldrain_api(raw_link)

    try:
        headers = await pixeldrain_head(api_link)
        size = size_format(headers.get("Content-Length", 0))
    except:
        return await message.reply_text("❌ Pixeldrain dosya bilgisi alınamadı.")

    parsed = PTN.parse(filename)

    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    year = parsed.get("year")
    quality = parsed.get("resolution", "UNKNOWN")

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
        return await message.reply_text("❌ TMDB eşleşmesi bulunamadı.")

    meta = results[0]
    details = await (
        tmdb.tv(meta.id).details()
        if media_type == "tv"
        else tmdb.movie(meta.id).details()
    )

    base = {
        "tmdb_id": meta.id,
        "title": meta.title if media_type == "movie" else meta.name,
        "description": meta.overview,
        "rating": meta.vote_average,
        "release_year": year_from(meta.release_date if media_type == "movie" else meta.first_air_date),
        "poster": f"https://image.tmdb.org/t/p/w500{meta.poster_path}",
        "backdrop": f"https://image.tmdb.org/t/p/w780{meta.backdrop_path}",
        "updated_on": str(datetime.utcnow()),
    }

    # -------- MOVIE --------
    if media_type == "movie":
        doc = await col.find_one({"tmdb_id": meta.id})

        entry = {
            "quality": quality,
            "id": api_link,
            "name": filename,
            "size": size,
        }

        if not doc:
            base["media_type"] = "movie"
            base["telegram"] = [entry]
            await col.insert_one(base)
        else:
            doc["telegram"].append(entry)
            doc["updated_on"] = str(datetime.utcnow())
            await col.replace_one({"_id": doc["_id"]}, doc)

    # -------- TV --------
    else:
        doc = await col.find_one({"tmdb_id": meta.id})

        ep_entry = {
            "quality": quality,
            "id": api_link,
            "name": filename,
            "size": size,
        }

        if not doc:
            base["media_type"] = "tv"
            base["seasons"] = [{
                "season_number": season,
                "episodes": [{
                    "episode_number": episode,
                    "telegram": [ep_entry]
                }]
            }]
            await col.insert_one(base)
        else:
            season_obj = next(
                (s for s in doc["seasons"] if s["season_number"] == season), None
            )
            if not season_obj:
                season_obj = {"season_number": season, "episodes": []}
                doc["seasons"].append(season_obj)

            ep = next(
                (e for e in season_obj["episodes"] if e["episode_number"] == episode), None
            )
            if not ep:
                ep = {"episode_number": episode, "telegram": []}
                season_obj["episodes"].append(ep)

            ep["telegram"].append(ep_entry)
            doc["updated_on"] = str(datetime.utcnow())
            await col.replace_one({"_id": doc["_id"]}, doc)

    await message.reply_text("✅ **Başarıyla eklendi**")


# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    awaiting_confirmation[uid] = True

    await message.reply_text(
        "⚠️ **TÜM VERİLER SİLİNECEK!**\n\n"
        "Onay için **Evet**, iptal için **Hayır** yaz."
    )


@Client.on_message(
    filters.private &
    CustomFilters.owner &
    filters.regex("(?i)^(evet|hayır)$")
)
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        mc = await movie_col.count_documents({})
        sc = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            f"🗑 **Silme tamamlandı**\n\n"
            f"🎬 Film: `{mc}`\n"
            f"📺 Dizi: `{sc}`"
        )
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
