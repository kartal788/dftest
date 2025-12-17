from asyncio import create_task
from bson import ObjectId
import motor.motor_asyncio
from datetime import datetime
from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING
from typing import Dict, List, Optional, Tuple, Any
import re

from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.helper.encrypt import decode_string
from Backend.helper.modal import (
    Episode,
    MovieSchema,
    QualityDetail,
    Season,
    TVShowSchema
)
from Backend.helper.task_manager import delete_message


def convert_objectid_to_str(document: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in document.items():
        if isinstance(value, ObjectId):
            document[key] = str(value)
        elif isinstance(value, list):
            document[key] = [
                convert_objectid_to_str(item) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, dict):
            document[key] = convert_objectid_to_str(value)
    return document


class Database:
    def __init__(self, db_name: str = "dbFyvio"):
        self.db_uris = Telegram.DATABASE
        self.db_name = db_name

        if len(self.db_uris) < 2:
            raise ValueError("At least 2 database URIs are required.")

        self.clients = {}
        self.dbs = {}
        self.current_db_index = 1

    async def connect(self):
        for index, uri in enumerate(self.db_uris):
            client = motor.motor_asyncio.AsyncIOMotorClient(uri)
            db_key = "tracking" if index == 0 else f"storage_{index}"
            self.clients[db_key] = client
            self.dbs[db_key] = client[self.db_name]

            masked_uri = re.sub(r"://(.*?):.*?@", r"://\1:*****@", uri).split("?")[0]
            LOGGER.info(f"{db_key} connected: {masked_uri}")

        state = await self.dbs["tracking"]["state"].find_one({"_id": "db_index"})
        if not state:
            await self.dbs["tracking"]["state"].insert_one(
                {"_id": "db_index", "current_index": 1}
            )
            self.current_db_index = 1
        else:
            self.current_db_index = state["current_index"]

    async def update_current_db_index(self):
        await self.dbs["tracking"]["state"].update_one(
            {"_id": "db_index"},
            {"$set": {"current_index": self.current_db_index}},
            upsert=True
        )

    # --------------------------------------------------
    # INSERT MEDIA
    # --------------------------------------------------

    async def insert_media(
        self,
        metadata_info: dict,
        channel: int,
        msg_id: int,
        size: str,
        name: str
    ) -> Optional[ObjectId]:

        if metadata_info["media_type"] == "movie":
            movie = MovieSchema(
                tmdb_id=metadata_info["tmdb_id"],
                imdb_id=metadata_info["imdb_id"],
                db_index=self.current_db_index,
                title=metadata_info["title"],
                genres=metadata_info["genres"],
                description=metadata_info["description"],
                rating=metadata_info["rate"],
                release_year=metadata_info["year"],
                poster=metadata_info["poster"],
                backdrop=metadata_info["backdrop"],
                logo=metadata_info["logo"],
                cast=metadata_info["cast"],
                runtime=metadata_info["runtime"],
                media_type="movie",

                # 🔥 PLATFORM EKLENDİ
                platform=metadata_info.get("platform", ""),

                telegram=[
                    QualityDetail(
                        quality=metadata_info["quality"],
                        id=metadata_info["encoded_string"],
                        name=name,
                        size=size
                    )
                ]
            )
            return await self.update_movie(movie)

        # ---------------- TV ----------------

        tv = TVShowSchema(
            tmdb_id=metadata_info["tmdb_id"],
            imdb_id=metadata_info["imdb_id"],
            db_index=self.current_db_index,
            title=metadata_info["title"],
            genres=metadata_info["genres"],
            description=metadata_info["description"],
            rating=metadata_info["rate"],
            release_year=metadata_info["year"],
            poster=metadata_info["poster"],
            backdrop=metadata_info["backdrop"],
            logo=metadata_info["logo"],
            cast=metadata_info["cast"],
            runtime=metadata_info["runtime"],
            media_type="tv",

            # 🔥 PLATFORM EKLENDİ
            platform=metadata_info.get("platform", ""),

            seasons=[
                Season(
                    season_number=metadata_info["season_number"],
                    episodes=[
                        Episode(
                            episode_number=metadata_info["episode_number"],
                            title=metadata_info["episode_title"],
                            episode_backdrop=metadata_info["episode_backdrop"],
                            overview=metadata_info["episode_overview"],
                            released=metadata_info["episode_released"],
                            telegram=[
                                QualityDetail(
                                    quality=metadata_info["quality"],
                                    id=metadata_info["encoded_string"],
                                    name=name,
                                    size=size
                                )
                            ]
                        )
                    ]
                )
            ]
        )

        return await self.update_tv_show(tv)

    # --------------------------------------------------
    # MOVIE UPDATE
    # --------------------------------------------------

    async def update_movie(self, movie_data: MovieSchema) -> Optional[ObjectId]:
        movie_dict = movie_data.dict()
        movie_dict["updated_on"] = datetime.utcnow()

        for i in range(1, len(self.dbs)):
            db = self.dbs[f"storage_{i}"]
            existing = await db["movie"].find_one({"tmdb_id": movie_dict["tmdb_id"]})
            if existing:
                existing["telegram"].extend(movie_dict["telegram"])
                existing["updated_on"] = datetime.utcnow()
                await db["movie"].replace_one({"_id": existing["_id"]}, existing)
                return existing["_id"]

        db = self.dbs[f"storage_{self.current_db_index}"]
        result = await db["movie"].insert_one(movie_dict)
        return result.inserted_id

    # --------------------------------------------------
    # TV UPDATE
    # --------------------------------------------------

    async def update_tv_show(self, tv_data: TVShowSchema) -> Optional[ObjectId]:
        tv_dict = tv_data.dict()
        tv_dict["updated_on"] = datetime.utcnow()

        for i in range(1, len(self.dbs)):
            db = self.dbs[f"storage_{i}"]
            existing = await db["tv"].find_one({"tmdb_id": tv_dict["tmdb_id"]})
            if existing:
                existing["seasons"].extend(tv_dict["seasons"])
                existing["updated_on"] = datetime.utcnow()
                await db["tv"].replace_one({"_id": existing["_id"]}, existing)
                return existing["_id"]

        db = self.dbs[f"storage_{self.current_db_index}"]
        result = await db["tv"].insert_one(tv_dict)
        return result.inserted_id

    # --------------------------------------------------
    # DELETE DOCUMENT
    # --------------------------------------------------

    async def delete_document(self, media_type: str, tmdb_id: int, db_index: int) -> bool:
        db = self.dbs[f"storage_{db_index}"]

        collection = "movie" if media_type.lower() == "movie" else "tv"
        doc = await db[collection].find_one({"tmdb_id": tmdb_id})

        if not doc:
            return False

        if collection == "movie":
            for q in doc.get("telegram", []):
                await self._delete_telegram(q)
        else:
            for s in doc.get("seasons", []):
                for e in s.get("episodes", []):
                    for q in e.get("telegram", []):
                        await self._delete_telegram(q)

        await db[collection].delete_one({"tmdb_id": tmdb_id})
        return True

    async def _delete_telegram(self, quality):
        try:
            decoded = await decode_string(quality["id"])
            chat_id = int(f"-100{decoded['chat_id']}")
            msg_id = int(decoded["msg_id"])
            create_task(delete_message(chat_id, msg_id))
        except Exception as e:
            LOGGER.error(f"Telegram delete failed: {e}")
