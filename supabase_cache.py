import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

RETENTION_DAYS = 90

_COLUMNS = ["id", "title", "url", "source", "author", "category", "summary", "published", "topics"]


class SupabaseCache:
    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.client: Client = create_client(url, key)
        self.last_updated: Optional[str] = None
        self._refresh_last_updated()

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

    def _cleanup_old(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        try:
            self.client.table("articles").delete().lt("fetched_at", cutoff).execute()
        except Exception as e:
            print(f"Cache cleanup error: {e}")

    def get_all(self) -> List[Dict]:
        res = (
            self.client.table("articles")
            .select("*")
            .order("published", desc=True)
            .limit(1000)
            .execute()
        )
        return res.data or []

    def get_by_id(self, story_id: str) -> Optional[Dict]:
        res = (
            self.client.table("articles")
            .select("*")
            .eq("id", story_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def count(self) -> int:
        res = self.client.table("articles").select("id", count="exact").execute()
        return res.count or 0
