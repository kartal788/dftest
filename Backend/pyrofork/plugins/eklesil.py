import os
import re
import asyncio
import aiohttp
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from motor.motor_asyncio import AsyncIOMotorClient
import PTN

from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import metadata   # 🔴 KRİTİK
from Backend.logger import LOGGER

# ----------------- ENV -----------------
DATABASE_RAW = os.getenv("DATABASE", "")
db_urls = [u.strip() for u in DATABASE_RAW.split(",") if u.strip().startswith("mongodb")]
MONGO_URL = db_urls[1]
DB_NAME = "dbFyvio"

# ----------------- MongoDB -----------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
movie_col = db["movie"]
series_col = db["tv"]

API_SEMAPHORE = asyncio.Semaphore(10)

# ----------------- Helpers -----------------
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
            return f"{size:.2f}{u}"
        size /= 1024
    return "YOK"

# ----------------- /EKLE -----------------
@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle pixeldrain_link1 pixeldrain_link2 ...")

    status = await message.reply_text("📥 Metadata alınıyor...")

    added_files = []  # List of successfully added files
    failed_files = []  # List of failed files
    movie_count = 0  # Number of movies added
    series_count = 0  # Number of series added

    for raw_link in args:
        try:
            api_link = pixeldrain_to_api(raw_link)
            filename = await filename_from_url(api_link)
            size = await filesize(api_link)

            # 🔴 METADATA.PY ÇAĞRISI
            meta = await metadata(
                filename=filename,
                channel=message.chat.id,
                msg_id=message.id
            )

            if not meta:
                raise ValueError("metadata.py veri döndürmedi (parse / eşleşme hatası)")

            # ----------------- MOVIE -----------------
            if meta["media_type"] == "movie":
                col = movie_col
                doc = await col.find_one({"tmdb_id": meta["tmdb_id"]})

                telegram_obj = {
                    "quality": meta["quality"],
                    "id": api_link,
                    "name": filename,
                    "size": size
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
                        "media_type": "movie",
                        "updated_on": str(datetime.utcnow()),
                        "telegram": [telegram_obj]
                    }
                    await col.insert_one(doc)
                else:
                    doc["telegram"].append(telegram_obj)
                    doc["updated_on"] = str(datetime.utcnow())
                    await col.replace_one({"_id": doc["_id"]}, doc)

                added_files.append(filename)
                movie_count += 1

            # ----------------- TV -----------------
            else:
                col = series_col
                doc = await col.find_one({"tmdb_id": meta["tmdb_id"]})

                telegram_obj = {
                    "quality": meta["quality"],
                    "id": api_link,
                    "name": filename,
                    "size": size
                }

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
                    await col.insert_one(doc)

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

                    season["episodes"].append(episode_obj)
                    doc["updated_on"] = str(datetime.utcnow())
                    await col.replace_one({"_id": doc["_id"]}, doc)

                added_files.append(filename)
                series_count += 1

        except Exception as e:
            LOGGER.exception(e)
            failed_files.append(filename)
            continue  # Skip to the next file on failure

    # Wait for at least 15 seconds before sending the final message
    await asyncio.sleep(15)

    # Preparing the final message
    if len(added_files) <= 10:
        # If there are 10 or fewer files, list them
        added_message = "\n".join([f"{i+1}) {file}" for i, file in enumerate(added_files)])
        failed_message = "\n".join([f"{i+1}) {file}" for i, file in enumerate(failed_files)])
        message_text = f"Eklenenler:\n{added_message}\n\nBaşarısız:\n{failed_message}"
    else:
        # If there are more than 10 files, summarize by category
        message_text = f"Eklenenler:\nFilm: {movie_count}\nDizi: {series_count}"

    # Sending the final message with success/failure results
    await status.edit_text(message_text)

        except Exception as e:
            LOGGER.exception(e)
            await status.edit_text(
                "❌ **EKLEME BAŞARISIZ**\n\n"
                f"📛 Hata: `{type(e).__name__}`\n"
                f"📄 Açıklama: `{str(e)}`\n\n"
                "🔎 Olası nedenler:\n"
                "- Dosya adı parse edilemedi\n"
                "- IMDb / TMDB eşleşmesi bulunamadı\n"
                "- metadata.py None döndürdü\n"
                "- Pixeldrain erişim sorunu"
            )
            break  # Eğer bir dosyada hata olursa, döngü durdurulabilir

    await status.edit_text("✅ **Tüm dosyalar başarıyla işlendi**")

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
        "⚠️ **TÜM VERİLER SİLİNECEK** ⚠️\n\n"
        "Bu işlem geri alınamaz.\n\n"
        f"🎬 Filmler: `{movie_count}`\n"
        f"📺 Diziler: `{tv_count}`\n\n"
        "Onaylamak için **Evet** yaz.\n"
        "İptal etmek için **Hayır** yaz."
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
        movie_deleted = await movie_col.count_documents({})
        tv_deleted = await series_col.count_documents({})

        await movie_col.delete_many({})
        await series_col.delete_many({})

        await message.reply_text(
            "✅ **Silme işlemi tamamlandı**\n\n"
            f"🎬 Silinen filmler: `{movie_deleted}`\n"
            f"📺 Silinen diziler: `{tv_deleted}`"
        )
    else:
        await message.reply_text("❌ Silme işlemi iptal edildi.")

