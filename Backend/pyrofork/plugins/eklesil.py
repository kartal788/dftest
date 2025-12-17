import os
import re
import aiohttp
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from motor.motor_asyncio import AsyncIOMotorClient
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import metadata
from Backend.logger import LOGGER

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.startswith("mongodb")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

# ----------------- Mongo -----------------
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

# ----------------- Helpers -----------------
def pixeldrain_api(url: str) -> str:
    m = re.match(r"https?://pixeldrain\.com/u/([A-Za-z0-9]+)", url)
    if not m:
        return url
    return f"https://pixeldrain.com/api/file/{m.group(1)}"

async def head(url, key):
    async with aiohttp.ClientSession() as s:
        async with s.head(url, allow_redirects=True, timeout=15) as r:
            return r.headers.get(key)

async def get_filename(url):
    cd = await head(url, "Content-Disposition")
    if cd:
        m = re.search(r'filename="(.+?)"', cd)
        if m:
            return m.group(1)
    return url.split("/")[-1]

async def get_size(url):
    size = await head(url, "Content-Length")
    if not size:
        return "YOK"
    size = int(size)
    for u in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f}{u}"
        size /= 1024
    return "YOK"

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    lines = message.text.split("\n")[1:]
    if not lines:
        return await message.reply_text(
            "Kullanım:\n"
            "/ekle\n"
            "link [imdb|tmdb|filename]"
        )

    status = await message.reply_text("📥 Dosyalar ekleniyor...")
    movie_count = 0
    series_count = 0
    failed = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        link = parts[0]
        extra = parts[1] if len(parts) > 1 else None

        try:
            api_link = pixeldrain_api(link)
            filename = await get_filename(api_link)
            size = await get_size(api_link)

            meta = None

            if extra and re.match(r"^tt\d+$", extra):
                meta = await metadata(imdb_id=extra, channel=message.chat.id, msg_id=message.id)
            elif extra and extra.isdigit():
                meta = await metadata(tmdb_id=extra, channel=message.chat.id, msg_id=message.id)
            elif extra:
                meta = await metadata(filename=extra, channel=message.chat.id, msg_id=message.id)

            if not meta:
                meta = await metadata(filename=filename, channel=message.chat.id, msg_id=message.id)

            if not meta:
                raise Exception("Metadata bulunamadı")

            tg_obj = {
                "quality": meta["quality"],
                "id": api_link,
                "name": filename,
                "size": size
            }

            # -------- MOVIE --------
            if meta["media_type"] == "movie":
                doc = await movie_col.find_one({"tmdb_id": meta["tmdb_id"]})
                if not doc:
                    doc = {
                        "tmdb_id": meta["tmdb_id"],
                        "imdb_id": meta["imdb_id"],
                        "title": meta["title"],
                        "genres": meta["genres"],
                        "description": meta["description"],
                        "rating": meta["rate"],
                        "release_year": meta["year"],
                        "poster": meta["poster"],
                        "backdrop": meta["backdrop"],
                        "logo": meta["logo"],
                        "cast": meta["cast"],
                        "runtime": meta["runtime"],
                        "media_type": "movie",
                        "updated_on": str(datetime.utcnow()),
                        "telegram": [tg_obj]
                    }
                    await movie_col.insert_one(doc)
                else:
                    doc["telegram"].append(tg_obj)
                    doc["updated_on"] = str(datetime.utcnow())
                    await movie_col.replace_one({"_id": doc["_id"]}, doc)

                movie_count += 1

            # -------- TV --------
            else:
                doc = await series_col.find_one({"tmdb_id": meta["tmdb_id"]})

                ep = {
                    "episode_number": meta["episode_number"],
                    "title": meta["episode_title"],
                    "overview": meta["episode_overview"],
                    "released": meta["episode_released"],
                    "telegram": [tg_obj]
                }

                if not doc:
                    doc = {
                        "tmdb_id": meta["tmdb_id"],
                        "imdb_id": meta["imdb_id"],
                        "title": meta["title"],
                        "genres": meta["genres"],
                        "description": meta["description"],
                        "rating": meta["rate"],
                        "release_year": meta["year"],
                        "poster": meta["poster"],
                        "backdrop": meta["backdrop"],
                        "logo": meta["logo"],
                        "cast": meta["cast"],
                        "runtime": meta["runtime"],
                        "media_type": "tv",
                        "updated_on": str(datetime.utcnow()),
                        "seasons": [{
                            "season_number": meta["season_number"],
                            "episodes": [ep]
                        }]
                    }
                    await series_col.insert_one(doc)
                else:
                    season = next(
                        (s for s in doc["seasons"] if s["season_number"] == meta["season_number"]),
                        None
                    )
                    if not season:
                        season = {
                            "season_number": meta["season_number"],
                            "episodes": []
                        }
                        doc["seasons"].append(season)

                    season["episodes"].append(ep)
                    doc["updated_on"] = str(datetime.utcnow())
                    await series_col.replace_one({"_id": doc["_id"]}, doc)

                series_count += 1

        except Exception as e:
            LOGGER.exception(e)
            failed.append(line)

    await status.edit_text(
        "✅ İşlem tamamlandı\n\n"
        f"🎬 Film: {movie_count}\n"
        f"📺 Dizi: {series_count}\n"
        f"❌ Hatalı: {len(failed)}"
    )

# ----------------- /SİL -----------------
confirm = {}

@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    confirm[uid] = True
    await message.reply_text("⚠️ Tüm veriler silinecek. Onay için **Evet** yaz.")

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in confirm:
        return

    confirm.pop(uid)

    if message.text.lower() == "evet":
        m = await movie_col.count_documents({})
        t = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(f"✅ Silindi\n🎬 {m} film\n📺 {t} dizi")
    else:
        await message.reply_text("❌ İptal edildi")
