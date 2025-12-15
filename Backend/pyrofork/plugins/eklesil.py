import os
import re
import asyncio
import time
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

API_SEMAPHORE = asyncio.Semaphore(12)
awaiting_confirmation = {}

# ----------------- HELPERS -----------------
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

def progress_bar(cur, total, length=10):
    filled = int(length * cur / total)
    return f"[{'█'*filled}{'░'*(length-filled)}] {cur}/{total}"

def eta(start, done, total):
    if done == 0:
        return "Hesaplanıyor..."
    elapsed = time.time() - start
    remain = (elapsed / done) * (total - done)
    m, s = divmod(int(remain), 60)
    return f"{m}dk {s}sn"

def build_media_record(meta, details, filename, url, quality, media_type, season=None, episode=None):
    base = {
        "tmdb_id": meta.id,
        "title": safe(meta, "title", safe(meta, "name")),
        "description": safe(meta, "overview", ""),
        "rating": safe(meta, "vote_average", 0),
        "release_year": year_from(safe(meta, "release_date", safe(meta, "first_air_date"))),
        "updated_on": str(datetime.utcnow()),
    }

    if media_type == "movie":
        return {
            **base,
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
        "media_type": "tv",
        "seasons": [{
            "season_number": season,
            "episodes": [{
                "episode_number": episode,
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
    urls = [pixeldrain_to_api(x) for x in message.command[1:] if x.startswith("http")]
    if not urls:
        return await message.reply_text("Kullanım: /ekle link [link2...]")

    total = len(urls)
    processed = 0
    start = time.time()
    last_edit = 0

    success, failed = [], []

    status = await message.reply_text("⏳ Başlatılıyor...")

    for raw in urls:
        processed += 1
        try:
            filename = await filename_from_url(raw)
            parsed = PTN.parse(filename)

            title = parsed.get("title")
            season = parsed.get("season")
            episode = parsed.get("episode")
            year = parsed.get("year")
            quality = parsed.get("resolution") or "UNKNOWN"
            size = await filesize(raw)

            async with API_SEMAPHORE:
                if season and episode:
                    results = await tmdb.search().tv(query=title)
                    col = series_col
                    media_type = "tv"
                else:
                    results = await tmdb.search().movies(query=title, year=year)
                    col = movie_col
                    media_type = "movie"

            if not results:
                failed.append(f"{filename} | TMDB bulunamadı")
                continue

            meta = results[0]
            details = await (tmdb.tv(meta.id).details() if media_type == "tv" else tmdb.movie(meta.id).details())

            doc = await col.find_one({"tmdb_id": meta.id})
            if not doc:
                doc = build_media_record(meta, details, filename, raw, quality, media_type, season, episode)
                doc["telegram"][0]["size"] = size
                await col.insert_one(doc)
            else:
                doc.setdefault("telegram", []).append({
                    "quality": quality,
                    "id": raw,
                    "name": filename,
                    "size": size
                })
                await col.replace_one({"_id": doc["_id"]}, doc)

            success.append(filename)

        except Exception as e:
            failed.append(f"{raw} | {e}")

        if time.time() - last_edit >= 15:
            await status.edit_text(
                f"📥 Ekleniyor...\n"
                f"{progress_bar(processed, total)}\n"
                f"⏱️ ETA: {eta(start, processed, total)}"
            )
            last_edit = time.time()

    # ----------------- SONUÇ (EDIT + TXT ŞARTLI) -----------------
    result_text = (
        f"✅ İşlem tamamlandı\n"
        f"{progress_bar(processed, total)}\n\n"
    )

    if success:
        if len(success) <= 10:
            result_text += "✅ Başarılı:\n" + "\n".join(f"• {x}" for x in success) + "\n\n"
        else:
            path = "/tmp/basarili.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(success))
            await client.send_document(message.chat.id, path, caption=f"✅ Başarılı: {len(success)}")
            result_text += f"✅ Başarılı: {len(success)} (TXT)\n\n"
    else:
        result_text += "✅ Başarılı: Yok\n\n"

    if failed:
        if len(failed) <= 10:
            result_text += "❌ Başarısız:\n" + "\n".join(f"• {x}" for x in failed)
        else:
            path = "/tmp/hatali.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(failed))
            await client.send_document(message.chat.id, path, caption=f"❌ Başarısız: {len(failed)}")
            result_text += f"❌ Başarısız: {len(failed)} (TXT)"

    await status.edit_text(result_text)

# ----------------- /SİL -----------------
@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    awaiting_confirmation[message.from_user.id] = True
    await message.reply_text("⚠️ TÜM VERİLER SİLİNECEK!\nEvet / Hayır")

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
        await message.reply_text(f"🗑️ Silindi\n🎬 Filmler: {m}\n📺 Diziler: {s}")
    else:
        await message.reply_text("❌ İptal edildi")
