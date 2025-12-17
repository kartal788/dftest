import os
import re
import asyncio
import aiohttp
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from motor.motor_asyncio import AsyncIOMotorClient
from themoviedb import aioTMDb
import PTN

from Backend.helper.custom_filter import CustomFilters

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

TMDB_API = os.getenv("TMDB_API")
tmdb = aioTMDb(key=TMDB_API, language="tr-TR", region="TR")

# ----------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(10)
awaiting_confirmation = {}

# ----------------- Helpers -----------------
def safe(obj, attr, default=None):
    return getattr(obj, attr, default) or default

def year_from(date):
    try:
        return int(str(date).split("-")[0])
    except Exception:
        return None

def pixeldrain_to_api(url: str) -> str:
    m = re.match(r"https?://pixeldrain\.com/u/([a-zA-Z0-9]+)", url)
    if not m:
        raise ValueError("Pixeldrain link formatı geçersiz")
    return f"https://pixeldrain.com/api/file/{m.group(1)}"

async def head(url, key):
    async with aiohttp.ClientSession() as s:
        async with s.head(url, allow_redirects=True, timeout=15) as r:
            return r.headers.get(key)

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
    for u in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {u}"
        size /= 1024
    return "YOK"

# ----------------- Media Builder -----------------
def build_media_record(meta, details, display_name, url, quality, media_type, season=None, episode=None, size="YOK"):
    poster = f"https://image.tmdb.org/t/p/w500{safe(meta, 'poster_path', '')}"
    backdrop = f"https://image.tmdb.org/t/p/w780{safe(meta, 'backdrop_path', '')}"

    base = {
        "tmdb_id": meta.id,
        "imdb_id": safe(details, "imdb_id", ""),
        "title": safe(meta, "title", safe(meta, "name")),
        "description": safe(meta, "overview", ""),
        "rating": safe(meta, "vote_average", 0),
        "release_year": year_from(safe(meta, "release_date", safe(meta, "first_air_date"))),
        "poster": poster,
        "backdrop": backdrop,
        "genres": [g.name for g in safe(details, "genres", [])],
        "updated_on": str(datetime.utcnow()),
    }

    if media_type == "movie":
        return {
            **base,
            "media_type": "movie",
            "runtime": f"{safe(details,'runtime','?')} dk",
            "telegram": [{
                "quality": quality,
                "id": url,
                "name": display_name,
                "size": size
            }]
        }

    return {
        **base,
        "media_type": "tv",
        "runtime": f"{safe(details,'episode_run_time',[None])[0]} dk",
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode,
                "title": display_name,
                "telegram": [{
                    "quality": quality,
                    "id": url,
                    "name": display_name,
                    "size": size
                }]
            }]
        }]
    }

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle link [özel isim]")

    msg = await message.reply_text("📥 İşlem başlatıldı...")
    success, failed = [], []

    try:
        raw = pixeldrain_to_api(args[0])
        custom_name = " ".join(args[1:]) if len(args) > 1 else None

        filename = await filename_from_url(raw)
        parsed = PTN.parse(filename)

        title = parsed.get("title")
        if not title:
            raise ValueError("Dosya isminden başlık çıkarılamadı")

        season = parsed.get("season")
        episode = parsed.get("episode")
        quality = parsed.get("resolution") or "UNKNOWN"
        size = await filesize(raw)

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
            raise LookupError("TMDB arama sonucu boş döndü")

        meta = results[0]
        details = await (tmdb.tv(meta.id).details() if media_type == "tv" else tmdb.movie(meta.id).details())

        doc = await col.find_one({"tmdb_id": meta.id})

        if not doc:
            doc = build_media_record(
                meta, details, custom_name or filename,
                raw, quality, media_type,
                season, episode, size
            )
            await col.insert_one(doc)
        else:
            # basit ekleme (çakışma yok)
            if media_type == "movie":
                doc["telegram"].append({
                    "quality": quality,
                    "id": raw,
                    "name": custom_name or filename,
                    "size": size
                })
            else:
                s = next((x for x in doc["seasons"] if x["season_number"] == season), None)
                if not s:
                    s = {"season_number": season, "episodes": []}
                    doc["seasons"].append(s)

                e = next((x for x in s["episodes"] if x["episode_number"] == episode), None)
                if not e:
                    e = {"episode_number": episode, "title": filename, "telegram": []}
                    s["episodes"].append(e)

                e["telegram"].append({
                    "quality": quality,
                    "id": raw,
                    "name": filename,
                    "size": size
                })

            doc["updated_on"] = str(datetime.utcnow())
            await col.replace_one({"_id": doc["_id"]}, doc)

        success.append(filename)

    except Exception as e:
        failed.append(str(e))
        await message.reply_text(
            "❌ **EKLEME BAŞARISIZ**\n\n"
            f"📄 Dosya: `{args[0]}`\n"
            f"🧩 Hata Tipi: `{type(e).__name__}`\n"
            f"📛 Açıklama: `{str(e)}`\n\n"
            "🔎 Olası Nedenler:\n"
            "- Pixeldrain dosyası erişilemiyor\n"
            "- Dosya adı parse edilemedi\n"
            "- TMDB eşleşmesi bulunamadı\n"
            "- TMDB API limiti / key hatası"
        )

    await msg.edit_text(
        f"📊 **Tamamlandı**\n\n"
        f"✅ Başarılı: {len(success)}\n"
        f"❌ Başarısız: {len(failed)}"
    )

# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    awaiting_confirmation[message.from_user.id] = True
    await message.reply_text(
        "⚠️ TÜM VERİLER SİLİNECEK\n\n"
        "Onay için **Evet**, iptal için **Hayır** yaz"
    )

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        m = await movie_col.count_documents({})
        s = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(f"✅ Silindi\n🎬 {m} | 📺 {s}")
    else:
        await message.reply_text("❌ İptal edildi")
