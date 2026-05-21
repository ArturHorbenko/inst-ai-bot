from datetime import datetime, timezone
from typing import Optional
import logging
from pymongo import MongoClient, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

logger = logging.getLogger(__name__)


def _clean(doc: dict) -> dict:
    """Remove MongoDB _id before returning to callers."""
    if doc and "_id" in doc:
        doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


class DatabaseConnection:
    def __init__(self, config):
        self._uri = config.MONGODB_URI
        self._db_name = config.MONGODB_DB
        self._client: Optional[MongoClient] = None
        self._db = None

    def connect(self) -> bool:
        try:
            self._client = MongoClient(
                self._uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=5000,
            )
            self._client.admin.command("ping")
            self._db = self._client[self._db_name]
            logger.info("MongoDB connected")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"MongoDB connection failed: {e}")
            return False

    def close(self):
        if self._client:
            self._client.close()

    @property
    def db(self):
        return self._db


class ArtifactStore:
    def __init__(self, db):
        self._col = db["artifacts"]
        self._col.create_index("content_hash", unique=True)

    def upsert(self, artifact: dict) -> dict:
        """Insert artifact if content_hash is new; return stored artifact either way."""
        self._col.update_one(
            {"content_hash": artifact["content_hash"]},
            {"$setOnInsert": artifact},
            upsert=True,
        )
        return _clean(self._col.find_one({"content_hash": artifact["content_hash"]}))

    def append_source(self, content_hash: str, source: dict):
        """Add a source entry to an existing artifact."""
        self._col.update_one(
            {"content_hash": content_hash},
            {"$push": {"sources": source}},
        )

    def update_gemini_ref(self, content_hash: str, gemini_file_ref: str):
        self._col.update_one(
            {"content_hash": content_hash},
            {"$set": {"gemini_file_ref": gemini_file_ref}},
        )

    def get_by_hash(self, content_hash: str) -> Optional[dict]:
        doc = self._col.find_one({"content_hash": content_hash})
        return _clean(doc) if doc else None

    def list(self) -> list[dict]:
        docs = self._col.find(
            {},
            {"content_hash": 1, "indexed_at": 1, "duration_sec": 1, "sources": 1},
        ).sort("indexed_at", DESCENDING)
        return [_clean(d) for d in docs]


class RunsStore:
    def __init__(self, db):
        self._col = db["runs"]
        self._col.create_index("run_id", unique=True)
        self._col.create_index([("artifact_hash", 1), ("created_at", DESCENDING)])

    def insert(self, run: dict) -> dict:
        self._col.insert_one({**run})
        return _clean(self._col.find_one({"run_id": run["run_id"]}))

    def get(self, run_id: str) -> Optional[dict]:
        doc = self._col.find_one({"run_id": run_id})
        return _clean(doc) if doc else None

    def list(self, artifact_hash: Optional[str] = None) -> list[dict]:
        query = {"artifact_hash": artifact_hash} if artifact_hash else {}
        docs = self._col.find(query).sort("created_at", DESCENDING)
        return [_clean(d) for d in docs]


class UrlCacheStore:
    def __init__(self, db):
        self._col = db["url_cache"]
        self._col.create_index("url", unique=True)

    def get(self, url: str) -> Optional[str]:
        doc = self._col.find_one({"url": url})
        return doc["content_hash"] if doc else None

    def put(self, url: str, content_hash: str):
        self._col.update_one(
            {"url": url},
            {"$set": {"url": url, "content_hash": content_hash, "cached_at": datetime.now(timezone.utc)}},
            upsert=True,
        )


class InsightsStore:
    """Append-only time-series snapshots of reel performance. Unlike artifacts,
    snapshots are mutable, account-scoped data — see docs/adr/0004."""

    def __init__(self, db):
        self._col = db["insights"]
        self._col.create_index([("media_id", 1), ("fetched_at", DESCENDING)])

    def insert(self, snapshot: dict) -> dict:
        self._col.insert_one({**snapshot})
        return _clean(snapshot)

    def list(self, media_id: Optional[str] = None) -> list[dict]:
        query = {"media_id": media_id} if media_id else {}
        docs = self._col.find(query).sort("fetched_at", DESCENDING)
        return [_clean(d) for d in docs]

    def latest(self, media_id: str) -> Optional[dict]:
        doc = self._col.find({"media_id": media_id}).sort("fetched_at", DESCENDING).limit(1)
        docs = list(doc)
        return _clean(docs[0]) if docs else None

    def find_media_id(self, shortcode: str) -> Optional[str]:
        """Resolve shortcode -> media_id from any prior snapshot. This is the
        resolution cache: only the first fetch per reel pages the media list."""
        doc = self._col.find_one({"shortcode": shortcode})
        return doc["media_id"] if doc else None


class MetaCredentialsStore:
    """Single-document store for the live Instagram Graph API token. The server
    refreshes the token in place here so .env never has to be rewritten."""

    _DOC_ID = "graph_token"

    def __init__(self, db):
        self._col = db["meta_credentials"]

    def get(self) -> Optional[dict]:
        doc = self._col.find_one({"_id": self._DOC_ID})
        return _clean(doc) if doc else None

    def put(self, access_token: str, expires_at: datetime, bootstrap_fingerprint: str):
        self._col.update_one(
            {"_id": self._DOC_ID},
            {"$set": {
                "access_token": access_token,
                "expires_at": expires_at,
                "bootstrap_fingerprint": bootstrap_fingerprint,
                "refreshed_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
