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
def pixeldrain_to_api(url):
    m = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url

async def head_value(url, key):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True) as r:
                return r.headers.get(key)
    except:
        return None

async def get_filename(url):
    cd = await head_value(url, "Content-Disposition")
    if cd:
        m = re.search(r'filename="(.+?)"', cd)
        if m:
            return m.group(1)
    return url.split("/")[-1]

async def get_size(url):
    size = await head_value(url, "Content-Length")
    if not size:
        return "UNKNOWN"
    size = int(size)
    for unit in ["B","KB","MB","GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

def get_year(d):
    try:
        return int(str(d).split("-")[0])
    except:
        return None

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    await init_db()
    if len(message.command) < 2:
        return await message.reply_text("Kullanım: /ekle link1 [link2 link3 ...]")

    args = message.command[1:]
    urls, filename_override = [], None

    for i, arg in enumerate(args):
        if arg.startswith("http"):
            urls.append(arg)
        else:
            filename_override = " ".join(args[i:])
            break

    added = []

    for idx, raw_url in enumerate(urls):
        url = pixeldrain_to_api(raw_url)
        filename = filename_override if (idx == len(urls)-1 and filename_override) else await get_filename(url)

        try:
            parsed = PTN.parse(filename)
        except:
            continue

        title = parsed.get("title")
        season = parsed.get("season")
        episode = parsed.get("episode")
        year = parsed.get("year")
        quality = parsed.get("resolution") or "UNKNOWN"

        if not title:
            continue

        async with API_SEMAPHORE:
            if season and episode:
                results = await tmdb.search().tv(query=title)
                media_type = "tv"
                collection = series_col
            else:
                results = await tmdb.search().movies(query=title, year=year)
                media_type = "movie"
                collection = movie_col

        if not results:
            continue

        meta = results[0]
        details = await (tmdb.tv(meta.id).details() if media_type == "tv" else tmdb.movie(meta.id).details())
        size = await get_size(url)

        if media_type == "movie":
            await collection.insert_one({
                "tmdb_id": meta.id,
                "title": meta.title,
                "release_year": get_year(meta.release_date),
                "media_type": "movie",
                "telegram": [{
                    "quality": quality,
                    "id": url,
                    "name": filename,
                    "size": size
                }],
                "updated_on": str(datetime.utcnow())
            })
        else:
            doc = await collection.find_one({"tmdb_id": meta.id})
            if not doc:
                doc = {
                    "tmdb_id": meta.id,
                    "title": meta.name,
                    "media_type": "tv",
                    "seasons": []
                }

            season_doc = next((s for s in doc["seasons"] if s["season_number"] == season), None)
            if not season_doc:
                season_doc = {"season_number": season, "episodes": []}
                doc["seasons"].append(season_doc)

            ep_doc = next((e for e in season_doc["episodes"] if e["episode_number"] == episode), None)
            if not ep_doc:
                ep_doc = {"episode_number": episode, "telegram": []}
                season_doc["episodes"].append(ep_doc)

            ep_doc["telegram"].append({
                "quality": quality,
                "id": url,
                "name": filename,
                "size": size
            })

            await collection.replace_one({"tmdb_id": meta.id}, doc, upsert=True)

        added.append(title)

    await message.reply_text(
        "✅ Eklendi:\n" + "\n".join(set(added)) if added else "⚠️ Hiçbir içerik eklenemedi."
    )

# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    await message.reply_text("⚠️ TÜM VERİLER SİLİNECEK!\nOnay için **Evet**, iptal için **Hayır** yaz.")

    async def timeout():
        await asyncio.sleep(60)
        awaiting_confirmation.pop(uid, None)

    awaiting_confirmation[uid] = asyncio.create_task(timeout())

@Client.on_message(filters.private & CustomFilters.owner & filters.text)
async def sil_kesin(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid).cancel()
    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("✅ Tüm veriler silindi.")
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
