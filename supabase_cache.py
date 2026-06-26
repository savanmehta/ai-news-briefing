import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

RETENTION_DAYS = 90

_COLUMNS = ["id", "title", "url", "source", "author", "category", "summary", "published", "topics", "image_url"]


class SupabaseCache:
    """In-memory read cache backed by Supabase. Reads (get_all/get_by_id/count)
    are served from memory; only `update()` (manual/scheduled refresh) hits the DB."""

    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.client: Client = create_client(url, key)
        self.last_updated: Optional[str] = None
        self._cache: List[Dict] = []
        self._refresh_last_updated()
        self._load_from_db()

    def _refresh_last_updated(self):
        res = (
            self.client.table("articles")
            .select("fetched_at")
            .order("fetched_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            self.last_updated = res.data[0]["fetched_at"]

    def _load_from_db(self):
        res = (
            self.client.table("articles")
            .select("*")
            .order("published", desc=True)
            .limit(1000)
            .execute()
        )
        self._cache = res.data or []

    def update(self, stories: List[Dict]):
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for s in stories:
            row = {col: s.get(col) for col in _COLUMNS}
            row["fetched_at"] = now
            rows.append(row)

        if rows:
            self.client.table("articles").upsert(rows, on_conflict="id").execute()

        self.last_updated = now
        self._cleanup_old()
        self._load_from_db()

    def _cleanup_old(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        try:
            self.client.table("articles").delete().lt("fetched_at", cutoff).execute()
        except Exception as e:
            print(f"Cache cleanup error: {e}")

    def get_all(self) -> List[Dict]:
        return self._cache

    def get_by_id(self, story_id: str) -> Optional[Dict]:
        for story in self._cache:
            if story["id"] == story_id:
                return story
        return None

    def count(self) -> int:
        return len(self._cache)
