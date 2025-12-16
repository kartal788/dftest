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

# ===================== CONFIG =====================
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb")]
if not db_urls:
    raise RuntimeError("MongoDB URL bulunamadı")

MONGO_URL = db_urls[0]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API", "")
tmdb = aioTMDb(key=TMDB_API, language="en-US", region="US")

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(12)
awaiting_confirmation = {}

# ===================== HELPERS =====================
def pixeldrain_to_api(url: str) -> str:
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
        return "YOK"

    size = int(size)
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {u}"
        size /= 1024


def parse_links_and_names(text: str):
    items = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("/ekle"):
            line = line.replace("/ekle", "", 1).strip()

        if line.startswith("http"):
            parts = line.split(maxsplit=1)
            url = parts[0]
            name = parts[1] if len(parts) > 1 else None
            items.append((pixeldrain_to_api(url), name))

    return items


def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except:
        return None


def build_movie(meta, filename, url, quality, size):
    return {
        "tmdb_id": meta.id,
        "title": meta.title,
        "description": meta.overview or "",
        "rating": meta.vote_average or 0,
        "release_year": year_from(meta.release_date),
        "media_type": "movie",
        "updated_on": str(datetime.utcnow()),
        "telegram": [{
            "quality": quality,
            "id": url,
            "name": filename,
            "size": size
        }]
    }


def build_tv(meta, filename, url, quality, size, season, episode):
    return {
        "tmdb_id": meta.id,
        "title": meta.name,
        "description": meta.overview or "",
        "rating": meta.vote_average or 0,
        "release_year": year_from(meta.first_air_date),
        "media_type": "tv",
        "updated_on": str(datetime.utcnow()),
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode,
                "title": filename,
                "telegram": [{
                    "quality": quality,
                    "id": url,
                    "name": filename,
                    "size": size
                }]
            }]
        }]
    }

# ===================== /EKLE =====================
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    items = parse_links_and_names(message.text)
    if not items:
        return await message.reply_text("Kullanım:\n/ekle link DOSYA_ADI.mkv")

    success, failed = [], []

    for raw_url, custom_name in items:
        try:
            filename = custom_name or await filename_from_url(raw_url)
            parsed = PTN.parse(filename)

            title = parsed.get("title")
            year = parsed.get("year")
            season = parsed.get("season")
            episode = parsed.get("episode")
            quality = parsed.get("resolution") or "UNKNOWN"
            size = await filesize(raw_url)

            async with API_SEMAPHORE:
                if season and episode:
                    results = await tmdb.search().tv(query=title)
                    meta = results[0]
                    col = series_col
                    doc = await col.find_one({"tmdb_id": meta.id})

                    if not doc:
                        doc = build_tv(meta, filename, raw_url, quality, size, season, episode)
                        await col.insert_one(doc)
                    else:
                        doc["updated_on"] = str(datetime.utcnow())
                        await col.replace_one({"_id": doc["_id"]}, doc)

                else:
                    results = await tmdb.search().movies(query=title, year=year)
                    meta = results[0]
                    col = movie_col
                    doc = await col.find_one({"tmdb_id": meta.id})

                    if not doc:
                        doc = build_movie(meta, filename, raw_url, quality, size)
                        await col.insert_one(doc)
                    else:
                        doc["updated_on"] = str(datetime.utcnow())
                        await col.replace_one({"_id": doc["_id"]}, doc)

            success.append(filename)

        except Exception as e:
            print("HATA:", e)
            failed.append(custom_name or raw_url)

    await message.reply_text(
        f"✅ Başarılı: {len(success)}\n❌ Başarısız: {len(failed)}"
    )

# ===================== /SİL =====================
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    awaiting_confirmation[message.from_user.id] = True
    await message.reply_text(
        "⚠️ TÜM VERİLER SİLİNECEK!\n\n"
        "Onay için **Evet**, iptal için **Hayır** yaz."
    )


@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("🗑 Tüm veriler silindi.")
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
