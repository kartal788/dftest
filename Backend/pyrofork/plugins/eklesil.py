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
    from datetime import datetime

    def progress_bar(done, total, length=20):
        filled = int(length * done / total)
        return "█" * filled + "░" * (length - filled)

    def progress_text(done, total, ok, fail):
        return (
            f"📥 Dosyalar ekleniyor...\n\n"
            f"`[{progress_bar(done, total)}]`\n"
            f"🔄 İşlenen: {done}/{total}\n"
            f"✅ Başarılı: {len(ok)}\n"
            f"❌ Başarısız: {len(fail)}"
        )

    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle link [link2 ...]")

    urls = [pixeldrain_to_api(x) for x in args if x.startswith("http")]
    total = len(urls)

    success = []
    failed = []

    progress_msg = await message.reply_text("📥 Başlatılıyor...")
    last_edit = datetime.utcnow()

    for i, raw in enumerate(urls, start=1):
        filename = raw
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
                    media_type = "tv"
                    col = series_col
                else:
                    results = await tmdb.search().movies(query=title, year=year)
                    media_type = "movie"
                    col = movie_col

            if not results:
                raise Exception("TMDB bulunamadı")

            meta = results[0]
            details = await (
                tmdb.tv(meta.id).details()
                if media_type == "tv"
                else tmdb.movie(meta.id).details()
            )

            # -------- MOVIE --------
            if media_type == "movie":
                doc = await col.find_one({"tmdb_id": meta.id})

                if not doc:
                    doc = build_media_record(meta, details, filename, raw, quality, "movie")
                    doc["telegram"][0]["size"] = size
                    await col.insert_one(doc)
                else:
                    tg = doc.get("telegram", [])
                    f = next((x for x in tg if x["name"] == filename), None)
                    if f:
                        f.update({"quality": quality, "id": raw, "size": size})
                    else:
                        tg.append({
                            "quality": quality,
                            "id": raw,
                            "name": filename,
                            "size": size
                        })
                    doc["updated_on"] = str(datetime.utcnow())
                    await col.replace_one({"_id": doc["_id"]}, doc)

            # -------- TV --------
            else:
                doc = await col.find_one({"tmdb_id": meta.id})

                if not doc:
                    doc = build_media_record(
                        meta, details, filename, raw, quality, "tv", season, episode
                    )
                    doc["seasons"][0]["episodes"][0]["telegram"][0]["size"] = size
                    await col.insert_one(doc)
                else:
                    s = next((x for x in doc["seasons"] if x["season_number"] == season), None)
                    if not s:
                        s = {"season_number": season, "episodes": []}
                        doc["seasons"].append(s)

                    e = next((x for x in s["episodes"] if x["episode_number"] == episode), None)
                    if not e:
                        e = {"episode_number": episode, "title": filename, "telegram": []}
                        s["episodes"].append(e)

                    t = next((x for x in e["telegram"] if x["name"] == filename), None)
                    if t:
                        t.update({"quality": quality, "id": raw, "size": size})
                    else:
                        e["telegram"].append({
                            "quality": quality,
                            "id": raw,
                            "name": filename,
                            "size": size
                        })

                    doc["updated_on"] = str(datetime.utcnow())
                    await col.replace_one({"_id": doc["_id"]}, doc)

            success.append(filename)

        except Exception:
            failed.append(filename)

        # ⏱ 15 saniyede bir edit
        if (datetime.utcnow() - last_edit).seconds >= 15 or i == total:
            await progress_msg.edit_text(
                progress_text(i, total, success, failed)
            )
            last_edit = datetime.utcnow()

    # -------- SONUÇ --------
    summary = (
        f"📊 **İşlem Tamamlandı**\n\n"
        f"🔢 Toplam: {total}\n"
        f"✅ Başarılı: {len(success)}\n"
        f"❌ Başarısız: {len(failed)}"
    )

    detail = (
        "✅ Başarılı Dosyalar:\n" + "\n".join(success) +
        "\n\n❌ Başarısız Dosyalar:\n" + "\n".join(failed)
    )

    if total > 10:
        path = "/tmp/ekle_sonuc.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(summary + "\n\n" + detail)

        await progress_msg.edit_text(summary)
        await message.reply_document(path)
        os.remove(path)
    else:
        await progress_msg.edit_text(summary + "\n\n" + detail)

# ----------------- /SİL -----------------
awaiting_confirmation = {}

@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    awaiting_confirmation[uid] = True
    await message.reply_text(
        "⚠️ **TÜM VERİLER SİLİNECEK!**\n\n"
        "Onay için **Evet**, iptal için **Hayır** yaz.\n\n"
        "📊 Silinecek veriler:\n"
        f"🎬 Filmler: `{await movie_col.count_documents({})}`\n"
        f"📺 Diziler: `{await series_col.count_documents({})}`"
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
        movie_count = await movie_col.count_documents({})
        series_count = await series_col.count_documents({})

        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            "✅ **Silme İşlemi Tamamlandı**\n\n"
            f"🎬 Silinen Filmler: `{movie_count}`\n"
            f"📺 Silinen Diziler: `{series_count}`\n"
            f"🗑 Toplam: `{movie_count + series_count}`"
        )
    else:
        await message.reply_text("❌ İşlem iptal edildi.")
