import os
import re
import aiohttp
from datetime import timezone, datetime
from dateutil.parser import parse as parse_date

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
    if not m:
        return url
    return f"https://pixeldrain.com/api/file/{m.group(1)}"

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
            if m:
                return m.group(1)
        return url.split("/")[-1]
    except:
        return url.split("/")[-1]

async def filesize(url):
    try:
        size = await head(url, "Content-Length")
        if not size:
            return "YOK"
        size = int(size)
        for u in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.2f}{u}"
            size /= 1024
        return "YOK"
    except:
        return "YOK"

# ----------ekle ----------
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

    movie_count = 0
    series_count = 0
    failed = []
    added_movies = []
    added_series = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

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
                    doc = {
                        "tmdb_id": meta["tmdb_id"],
                        "imdb_id": meta["imdb_id"],
                        "db_index": 1,
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
                        "telegram": [telegram_obj]
                    }
                    await movie_col.insert_one(doc)
                else:
                    # Aynı quality veya id farketmeksizin her zaman ekle
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
                    doc = {
                        "tmdb_id": meta["tmdb_id"],
                        "imdb_id": meta["imdb_id"],
                        "db_index": 1,
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
                            "episodes": [episode_obj]
                        }]
                    }
                    await series_col.insert_one(doc)
                else:
                    season = next((s for s in doc["seasons"] if s["season_number"] == meta["season_number"]), None)
                    if not season:
                        season = {"season_number": meta["season_number"], "episodes": []}
                        doc["seasons"].append(season)

                    ep = next((e for e in season["episodes"] if e["episode_number"] == meta["episode_number"]), None)
                    if not ep:
                        season["episodes"].append(episode_obj)
                    else:
                        # Aynı bölüm için her zaman yeni telegram objesi ekle
                        ep["telegram"].append(telegram_obj)

                    doc["updated_on"] = str(datetime.utcnow())
                    await series_col.replace_one({"_id": doc["_id"]}, doc)
                series_count += 1
                added_series.append(meta["title"])

        except Exception as e:
            LOGGER.exception(e)
            failed.append(line)

    # ----------------- Mesaj formatı -----------------
    if len(added_movies) + len(added_series) > 15:
        result_text = f"✅ İşlem tamamlandı\n\n🎬 Film: {movie_count}\n📺 Dizi: {series_count}\n❌ Hatalı: {len(failed)}"
    else:
        movies_text = "\n".join(f"🎬 {name}" for name in added_movies)
        series_text = "\n".join(f"📺 {name}" for name in added_series)
        result_text = f"✅ İşlem tamamlandı\n\n{movies_text}\n{series_text}\n❌ Hatalı: {len(failed)}"

    await status.edit_text(result_text)
    
# ----------------- /yenibolum -----------------
@Client.on_message(filters.command("yenibolum") & filters.private & CustomFilters.owner)
async def yenibolum(client: Client, message: Message):
    status = await message.reply_text("🔄 Yeni bölüm tarihleri güncelleniyor...")

    updated = 0
    skipped = 0

    cursor = series_col.find({})

    async for tv in cursor:
        latest = None

        for season in tv.get("seasons", []):
            for ep in season.get("episodes", []):
                released = ep.get("released")
                if not released:
                    continue
                try:
                    from dateutil.parser import parse as parse_date
                    dt = parse_date(released)
                    if not dt.tzinfo:
                        from datetime import timezone
                        dt = dt.replace(tzinfo=timezone.utc)
                    if not latest or dt > latest:
                        latest = dt
                except Exception:
                    continue

        if not latest:
            skipped += 1
            continue

        latest_iso = latest.isoformat().replace("+00:00", "Z")

        await series_col.update_one(
            {"_id": tv["_id"]},
            {"$set": {"latest_episode_released": latest_iso, "updated_on": str(datetime.utcnow())}}
        )

        updated += 1

    await status.edit_text(
        f"✅ Güncelleme tamamlandı\n\n📺 Güncellenen dizi: {updated}\n⏭ Atlanan (tarihsiz): {skipped}"
    )

# ----------------- /SİL -----------------
awaiting_confirmation = {}

@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(client: Client, message: Message):
    uid = message.from_user.id

    movie_count = await movie_col.count_documents({})
    tv_count = await series_col.count_documents({})

    if movie_count == 0 and tv_count == 0:
        return await message.reply_text("ℹ️ Veritabanı zaten boş.")

    awaiting_confirmation[uid] = True

    await message.reply_text(
        "⚠️ TÜM VERİLER SİLİNECEK ⚠️\n\n"
        f"🎬 Filmler: {movie_count}\n"
        f"📺 Diziler: {tv_count}\n\n"
        "Onaylamak için **Evet** yaz.\n"
        "İptal için **Hayır** yaz."
    )

@Client.on_message(filters.private & CustomFilters.owner & filters.regex("(?i)^(evet|hayır)$"))
async def sil_onay(client: Client, message: Message):
    uid = message.from_user.id

    if uid not in awaiting_confirmation:
        return

    awaiting_confirmation.pop(uid)

    if message.text.lower() == "evet":
        m = await movie_col.count_documents({})
        t = await series_col.count_documents({})
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text(
            f"✅ Silme tamamlandı\n🎬 {m} film\n📺 {t} dizi"
        )
    else:
        await message.reply_text("❌ Silme iptal edildi.")
