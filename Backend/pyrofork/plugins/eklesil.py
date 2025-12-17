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
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

# ----------------- MongoDB -----------------
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

# ----------------- Helpers -----------------
def pixeldrain_to_api(url: str) -> str:
    m = re.match(r"https?://pixeldrain\.com/u/([A-Za-z0-9]+)", url)
    return f"https://pixeldrain.com/api/file/{m.group(1)}" if m else url

async def head(url, key):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=20) as r:
                return r.headers.get(key)
    except:
        return None

async def filename_from_url(url):
    try:
        cd = await head(url, "Content-Disposition")
        if cd:
            m = re.search(r'filename="(.+?)"', cd)
            if m: return m.group(1)
        return url.split("/")[-1]
    except:
        return url.split("/")[-1]

async def filesize(url):
    try:
        size = await head(url, "Content-Length")
        if not size: return "YOK"
        size = int(size)
        for u in ["B","KB","MB","GB"]:
            if size < 1024: return f"{size:.2f}{u}"
            size /= 1024
        return "YOK"
    except:
        return "YOK"

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    text = message.text.strip()
    if "\n" in text:
        lines = text.split("\n")[1:]
    else:
        parts = text.split(maxsplit=1)
        lines = [parts[1]] if len(parts) > 1 else []

    if not lines:
        return await message.reply_text(
            "Kullanım:\n/ekle link [imdb|tmdb|filename]\nveya\n/ekle\\nlink"
        )

    status = await message.reply_text("📥 Metadata alınıyor...")
    movie_count = series_count = 0
    failed = []
    added_movies = []
    added_series = []

    for line in lines:
        line = line.strip()
        if not line: continue

        parts = line.split(maxsplit=1)
        link = parts[0]
        extra_info = parts[1] if len(parts) > 1 else None

        try:
            api_link = pixeldrain_to_api(link) if "pixeldrain.com" in link else link

            try:
                size = await filesize(api_link)
                cd_filename = await filename_from_url(api_link)
                meta_filename = extra_info or cd_filename or link.split("/")[-1]
            except:
                size = "YOK"
                meta_filename = extra_info or link.split("/")[-1]

            meta = await metadata(
                filename=meta_filename,
                channel=message.chat.id,
                msg_id=message.id
            )

            if not meta:
                meta = {
                    "media_type": "movie",
                    "tmdb_id": None,
                    "imdb_id": None,
                    "title": meta_filename,
                    "genres": [],
                    "description": "",
                    "rate": 0,
                    "year": None,
                    "poster": "",
                    "backdrop": "",
                    "logo": "",
                    "cast": [],
                    "runtime": 0,
                    "season_number": 1,
                    "episode_number": 1,
                    "episode_title": meta_filename,
                    "episode_backdrop": "",
                    "episode_overview": "",
                    "episode_released": None,
                    "quality": "Unknown"
                }

            telegram_obj = {
                "quality": meta.get("quality", "Unknown"),
                "id": api_link,
                "name": meta_filename,
                "size": size
            }

            # ----------------- MOVIE -----------------
            if meta["media_type"] == "movie":
                doc = await movie_col.find_one({"tmdb_id": meta["tmdb_id"]})
                if not doc:
                    doc = {**meta, "db_index":1, "media_type":"movie", "updated_on":str(datetime.utcnow()), "telegram":[telegram_obj]}
                    await movie_col.insert_one(doc)
                else:
                    if telegram_obj not in doc.get("telegram", []):
                        doc["telegram"].append(telegram_obj)
                    doc["updated_on"] = str(datetime.utcnow())
                    await movie_col.replace_one({"_id": doc["_id"]}, doc)
                movie_count += 1
                added_movies.append(meta["title"])

            # ----------------- TV -----------------
            else:
                doc = await series_col.find_one({"tmdb_id": meta["tmdb_id"]})
                episode_obj = {
                    "episode_number": meta["episode_number"],
                    "title": meta["episode_title"],
                    "episode_backdrop": meta["episode_backdrop"],
                    "overview": meta["episode_overview"],
                    "released": meta["episode_released"],
                    "telegram": [telegram_obj]
                }

                if not doc:
                    doc = {**meta, "db_index":1, "media_type":"tv", "updated_on":str(datetime.utcnow()),
                           "seasons":[{"season_number":meta["season_number"],"episodes":[episode_obj]}]}
                    await series_col.insert_one(doc)
                else:
                    season = next((s for s in doc["seasons"] if s["season_number"]==meta["season_number"]), None)
                    if not season:
                        season = {"season_number":meta["season_number"],"episodes":[]}
                        doc["seasons"].append(season)
                    episode = next((e for e in season["episodes"] if e["episode_number"]==meta["episode_number"]), None)
                    if not episode:
                        season["episodes"].append(episode_obj)
                    else:
                        # Aynı bölüm varsa telegram listesine ekle, tekrarları önle
                        for t in telegram_obj["telegram"]:
                            if t not in episode["telegram"]:
                                episode["telegram"].append(t)
                    doc["updated_on"] = str(datetime.utcnow())
                    await series_col.replace_one({"_id": doc["_id"]}, doc)
                series_count += 1
                added_series.append(meta["title"])

        except Exception as e:
            LOGGER.exception(e)
            failed.append(line)

    # ----------------- Mesaj formatı -----------------
    if len(added_movies)+len(added_series) > 15:
        result_text = f"✅ İşlem tamamlandı\n\n🎬 Film: {movie_count}\n📺 Dizi: {series_count}\n❌ Hatalı: {len(failed)}"
    else:
        movies_text = "\n".join(f"🎬 {name}" for name in added_movies)
        series_text = "\n".join(f"📺 {name}" for name in added_series)
        result_text = f"✅ İşlem tamamlandı\n\n{movies_text}\n{series_text}\n❌ Hatalı: {len(failed)}"

    await status.edit_text(result_text)

# ----------------- /SİL -----------------
awaiting_confirmation = {}

@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id
    movie_count = await movie_col.count_documents({})
    tv_count = await series_col.count_documents({})
    if movie_count==0 and tv_count==0:
        return await message.reply_text("ℹ️ Veritabanı zaten boş.")
    awaiting_confirmation[uid] = True
    await message.reply_text(
        f"⚠️ TÜM VERİLER SİLİNECEK ⚠️\n\n🎬 Filmler: {movie_count}\n📺 Diziler: {tv_count}\n\nOnaylamak için **Evet**, iptal için **Hayır**"
    )

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in awaiting_confirmation: return
    awaiting_confirmation.pop(uid)
    if message.text.lower()=="evet":
        m = await movie_col.count_documents({})
        t = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(f"✅ Silme tamamlandı\n🎬 {m} film\n📺 {t} dizi")
    else:
        await message.reply_text("❌ Silme iptal edildi.")
