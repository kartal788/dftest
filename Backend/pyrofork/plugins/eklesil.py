from pyrogram import Client, filters
from pyrogram.types import Message
import PTN
from datetime import datetime

from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import fetch_movie_metadata, fetch_tv_metadata
from Backend.db import movie_col, series_col
from Backend.logger import LOGGER


@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def ekle(_, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text("Kullanım: /ekle <link> [link2 ...]")

    added = []

    for raw in args:
        try:
            filename = raw.split("/")[-1]
            parsed = PTN.parse(filename)
        except Exception:
            continue

        title   = parsed.get("title")
        year    = parsed.get("year")
        season  = parsed.get("season")
        episode = parsed.get("episode")
        quality = parsed.get("resolution") or "UNKNOWN"

        # ---------------- MOVIE ----------------
        if not season and not episode:
            try:
                meta = await fetch_movie_metadata(
                    title=title,
                    year=year,
                    encoded_string=filename,
                    quality=quality
                )
            except Exception as e:
                LOGGER.exception(f"Movie metadata error: {e}")
                continue

            if not meta:
                continue

            doc = await movie_col.find_one({"tmdb_id": meta["tmdb_id"]})
            if not doc:
                doc = {
                    "tmdb_id": meta["tmdb_id"],
                    "imdb_id": meta["imdb_id"],
                    "title": meta["title"],
                    "description": meta["description"],
                    "genres": meta["genres"],
                    "cast": meta["cast"],
                    "rating": meta["rate"],
                    "runtime": meta["runtime"],
                    "poster": meta["poster"],
                    "backdrop": meta["backdrop"],
                    "logo": meta["logo"],
                    "media_type": "movie",
                    "updated_on": str(datetime.utcnow()),
                    "telegram": []
                }

            file_entry = next(
                (x for x in doc["telegram"] if x["name"] == filename), None
            )

            if file_entry:
                file_entry.update({"quality": quality, "id": raw})
            else:
                doc["telegram"].append({
                    "quality": quality,
                    "id": raw,
                    "name": filename
                })

            await movie_col.replace_one(
                {"tmdb_id": meta["tmdb_id"]},
                doc,
                upsert=True
            )
            added.append(meta["title"])

        # ---------------- TV ----------------
        else:
            try:
                meta = await fetch_tv_metadata(
                    title=title,
                    season=season,
                    episode=episode,
                    year=year,
                    encoded_string=filename,
                    quality=quality
                )
            except Exception as e:
                LOGGER.exception(f"TV metadata error: {e}")
                continue

            if not meta:
                continue

            doc = await series_col.find_one({"tmdb_id": meta["tmdb_id"]})
            if not doc:
                doc = {
                    "tmdb_id": meta["tmdb_id"],
                    "imdb_id": meta["imdb_id"],
                    "title": meta["title"],
                    "description": meta["description"],
                    "genres": meta["genres"],
                    "cast": meta["cast"],
                    "rating": meta["rate"],
                    "runtime": meta["runtime"],
                    "poster": meta["poster"],
                    "backdrop": meta["backdrop"],
                    "logo": meta["logo"],
                    "media_type": "tv",
                    "updated_on": str(datetime.utcnow()),
                    "seasons": []
                }

            season_doc = next(
                (s for s in doc["seasons"] if s["season_number"] == season), None
            )
            if not season_doc:
                season_doc = {"season_number": season, "episodes": []}
                doc["seasons"].append(season_doc)

            ep_doc = next(
                (e for e in season_doc["episodes"] if e["episode_number"] == episode), None
            )
            if not ep_doc:
                ep_doc = {
                    "episode_number": episode,
                    "overview": meta.get("episode_overview"),
                    "released": meta.get("episode_released"),
                    "episode_backdrop": meta.get("episode_backdrop"),
                    "telegram": []
                }
                season_doc["episodes"].append(ep_doc)

            file_entry = next(
                (x for x in ep_doc["telegram"] if x["name"] == filename), None
            )
            if file_entry:
                file_entry.update({"quality": quality, "id": raw})
            else:
                ep_doc["telegram"].append({
                    "quality": quality,
                    "id": raw,
                    "name": filename
                })

            await series_col.replace_one(
                {"tmdb_id": meta["tmdb_id"]},
                doc,
                upsert=True
            )
            added.append(f"{meta['title']} S{season}E{episode}")

    await message.reply_text(
        "✅ Eklendi:\n" + "\n".join(set(added))
        if added else "⚠️ Hiçbir içerik eklenemedi."
    )
# -----------  sil -------
awaiting_delete_confirm = {}


@Client.on_message(filters.command("sil") & filters.private & CustomFilters.owner)
async def sil(_, message: Message):
    uid = message.from_user.id
    awaiting_delete_confirm[uid] = True

    await message.reply_text(
        "⚠️ **TÜM VERİLER SİLİNECEK!**\n\n"
        "Onay için **EVET**\n"
        "İptal için **HAYIR** yaz."
    )


@Client.on_message(
    filters.private &
    CustomFilters.owner &
    filters.regex("(?i)^(evet|hayır)$")
)
async def sil_onay(_, message: Message):
    uid = message.from_user.id

    if uid not in awaiting_delete_confirm:
        return

    awaiting_delete_confirm.pop(uid)

    if message.text.lower() == "evet":
        await movie_col.delete_many({})
        await series_col.delete_many({})
        await message.reply_text("✅ Tüm veriler başarıyla silindi.")
    else:
        await message.reply_text("❌ Silme işlemi iptal edildi.")
