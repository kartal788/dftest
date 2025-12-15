# tek.py
# -------------------------------------------------
# SINGLE FILE: /ekle + /sil + metadata builder (FIXED)
# -------------------------------------------------

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

# ---------------- ENV ----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb")]
if not db_urls:
    raise RuntimeError("MongoDB URL bulunamadı")

MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
if not TMDB_API:
    raise RuntimeError("TMDB_API tanımlı değil")

tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

# ---------------- DB ----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(8)
awaiting_confirmation = {}

# ---------------- HELPERS ----------------
def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except:
        return None

def pixeldrain_to_api(url):
    m = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url

async def head(url, key):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True) as r:
                return r.headers.get(key)
    except:
        return None

async def filename_from_url(url):
    cd = await head(url, "Content-Disposition")
    if cd:
        m = re.search(r'filename="(.+?)"', cd)
        if m:
            return m.group(1)
    return url.split("/")[-1]

async def filesize(url):
    size = await head(url, "Content-Length")
    if not size:
        return "UNKNOWN"
    size = int(size)
    for u in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {u}"
        size /= 1024

def build_base(meta, details):
    return {
        "tmdb_id": meta.id,
        "imdb_id": getattr(meta, "imdb_id", ""),
        "title": getattr(meta, "title", getattr(meta, "name", "")),
        "description": getattr(meta, "overview", ""),
        "rating": getattr(meta, "vote_average", 0),
        "genres": [g.name for g in getattr(details, "genres", [])],
        "release_year": year_from(
            getattr(meta, "release_date", getattr(meta, "first_air_date", None))
        ),
        "poster": f"https://image.tmdb.org/t/p/w500{getattr(meta,'poster_path','')}",
        "backdrop": f"https://image.tmdb.org/t/p/w780{getattr(meta,'backdrop_path','')}",
        "logo": "",
        "updated_on": str(datetime.utcnow()),
        "db_index": 1,
    }

# ---------------- /EKLE ----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(_, message: Message):
    raw_args = message.command[1:]
    if not raw_args:
        return await message.reply_text("/ekle link [link2 ...]")

    urls = [pixeldrain_to_api(x) for x in raw_args if x.startswith("http")]
    added = []

    for url in urls:
        filename = await filename_from_url(url)

        try:
            parsed = PTN.parse(filename)
        except:
            continue

        title = parsed.get("title")
        if not title:
            continue

        season = parsed.get("season")
        episode = parsed.get("episode")
        year = parsed.get("year")
        quality = parsed.get("resolution") or "UNKNOWN"
        size = await filesize(url)

        async with API_SEMAPHORE:
            if season and episode:
                results = await tmdb.search().tv(query=title)
                media_type = "tv"
                col = series_col
            else:
                results = await tmdb.search().movies(query=title)
                media_type = "movie"
                col = movie_col

        if not results:
            continue

        meta = results[0]
        details = await (
            tmdb.tv(meta.id).details()
            if media_type == "tv"
            else tmdb.movie(meta.id).details()
        )

        base = build_base(meta, details)

        # ---------------- MOVIE ----------------
        if media_type == "movie":
            doc = await col.find_one({"tmdb_id": meta.id})
            file_entry = {
                "quality": quality,
                "id": url,
                "name": filename,
                "size": size,
            }

            if not doc:
                base.update({
                    "media_type": "movie",
                    "runtime": f"{getattr(details,'runtime','')} min",
                    "telegram": [file_entry],
                })
                await col.insert_one(base)
            else:
                if not any(x["name"] == filename for x in doc.get("telegram", [])):
                    doc["telegram"].append(file_entry)
                doc["updated_on"] = str(datetime.utcnow())
                await col.update_one({"_id": doc["_id"]}, {"$set": doc})

        # ---------------- TV ----------------
        else:
            doc = await col.find_one({"tmdb_id": meta.id})

            if not doc:
                base.update({
                    "media_type": "tv",
                    "runtime": f"{details.episode_run_time[0] if details.episode_run_time else ''} min",
                    "seasons": [{
                        "season_number": season,
                        "episodes": [{
                            "episode_number": episode,
                            "title": filename,
                            "overview": base["description"],
                            "released": None,
                            "telegram": [{
                                "quality": quality,
                                "id": url,
                                "name": filename,
                                "size": size,
                            }],
                        }],
                    }],
                })
                await col.insert_one(base)
            else:
                s = next((x for x in doc["seasons"] if x["season_number"] == season), None)
                if not s:
                    s = {"season_number": season, "episodes": []}
                    doc["seasons"].append(s)

                e = next((x for x in s["episodes"] if x["episode_number"] == episode), None)
                if not e:
                    e = {
                        "episode_number": episode,
                        "title": filename,
                        "telegram": [],
                    }
                    s["episodes"].append(e)

                if not any(x["name"] == filename for x in e["telegram"]):
                    e["telegram"].append({
                        "quality": quality,
                        "id": url,
                        "name": filename,
                        "size": size,
                    })

                doc["updated_on"] = str(datetime.utcnow())
                await col.update_one({"_id": doc["_id"]}, {"$set": doc})

        added.append(title)

    await message.reply_text(
        "✅ Eklendi:\n" + "\n".join(set(added)) if added else "⚠️ Eklenemedi"
    )

# ---------------- /SIL ----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(_, message: Message):
    uid = message.from_user.id
    awaiting_confirmation[uid] = datetime.utcnow()
    await message.reply_text("⚠️ TÜM VERİLER SİLİNECEK! Evet / Hayır")

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(_, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("✅ Tüm veriler silindi")
    else:
        await message.reply_text("❌ İptal edildi")
