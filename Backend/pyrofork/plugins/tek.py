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

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb+srv")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

# ----------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

async def init_db():
    global movie_col, series_col
    movie_col = db["movie"]
    series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(12)
awaiting_confirmation = {}

# ----------------- Helpers -----------------
def safe(obj, attr, default=None):
    return getattr(obj, attr, default) or default

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

def build_media_record(meta, details, filename, url, quality, media_type, season=None, episode=None):
    genres = [g.name for g in safe(details, "genres", [])]
    cast = [c.name for c in safe(details, "cast", [])[:5]]
    poster = safe(meta, "poster_path", "")
    backdrop = safe(meta, "backdrop_path", "")
    logo = safe(meta, "logo", "")

    base = {
        "tmdb_id": meta.id,
        "imdb_id": safe(meta, "imdb_id", ""),
        "db_index": 1,
        "title": safe(meta, "title", safe(meta, "name")),
        "genres": genres,
        "description": safe(meta, "overview", ""),
        "rating": safe(meta, "vote_average", 0),
        "release_year": year_from(safe(meta, "release_date", safe(meta, "first_air_date"))),
        "poster": f"https://image.tmdb.org/t/p/w500{poster}",
        "backdrop": f"https://image.tmdb.org/t/p/w780{backdrop}",
        "logo": f"https://image.tmdb.org/t/p/w300{logo}",
        "cast": cast,
        "updated_on": str(datetime.utcnow()),
    }

    if media_type == "movie":
        return {
            **base,
            "runtime": f"{safe(details,'runtime','UNKNOWN')} min",
            "media_type": "movie",
            "telegram": [{
                "quality": quality,
                "id": url,
                "name": filename,
                "size": "UNKNOWN"
            }]
        }

    return {
        **base,
        "runtime": f"{safe(details,'episode_run_time',[None])[0]} min",
        "media_type": "tv",
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode,
                "title": filename,
                "overview": safe(meta, "overview", ""),
                "released": None,
                "telegram": [{
                    "quality": quality,
                    "id": url,
                    "name": filename,
                    "size": "UNKNOWN"
                }]
            }]
        }]
    }

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    await init_db()
    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle link1 [link2 link3 ...]")

    urls, override_name = [], None
    for i, a in enumerate(args):
        if a.startswith("http"):
            urls.append(a)
        else:
            override_name = " ".join(args[i:])
            break

    added = []

    for i, raw in enumerate(urls):
        url = pixeldrain_to_api(raw)
        filename = override_name if (i == len(urls)-1 and override_name) else await filename_from_url(url)

        try:
            parsed = PTN.parse(filename)
        except:
            continue

        title = parsed.get("title")
        season = parsed.get("season")
        episode = parsed.get("episode")
        year = parsed.get("year")
        quality = parsed.get("resolution") or "UNKNOWN"

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
            continue

        meta = results[0]
        details = await (tmdb.tv(meta.id).details() if media_type == "tv" else tmdb.movie(meta.id).details())
        size = await filesize(url)

        if media_type == "movie":
            rec = build_media_record(meta, details, filename, url, quality, "movie")
            rec["telegram"][0]["size"] = size
            await col.insert_one(rec)
        else:
            doc = await col.find_one({"tmdb_id": meta.id})
            if not doc:
                doc = build_media_record(meta, details, filename, url, quality, "tv", season, episode)
                doc["seasons"][0]["episodes"][0]["telegram"][0]["size"] = size
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
                    "id": url,
                    "name": filename,
                    "size": size
                })
                await col.replace_one({"tmdb_id": meta.id}, doc)

        added.append(title)

    await message.reply_text("✅ Eklendi:\n" + "\n".join(set(added)) if added else "⚠️ Hiçbir içerik eklenemedi.")

# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    await message.reply_text("⚠️ TÜM VERİLER SİLİNECEK!\nOnay için **Evet**, iptal için **Hayır** yaz.")
    awaiting_confirmation[uid] = True

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return
    awaiting_confirmation.pop(uid)
    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("✅ Tüm veriler silindi.")
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
