import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

CACHE_FILE = Path("news_cache.json")


class NewsCache:
    def __init__(self):
        self._stories: Dict[str, Dict] = {}
        self.last_updated: Optional[str] = None
        self._load_from_disk()

    def _load_from_disk(self):
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                self._stories = {s["id"]: s for s in data.get("stories", [])}
                self.last_updated = data.get("last_updated")
                print(f"Cache loaded: {len(self._stories)} stories")
            except Exception as e:
                print(f"Cache load error: {e}")

    def _save_to_disk(self):
        try:
            data = {
                "stories": list(self._stories.values()),
                "last_updated": self.last_updated,
            }
            CACHE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f"Cache save error: {e}")

    def update(self, stories: List[Dict]):
        self._stories = {s["id"]: s for s in stories}
        self.last_updated = datetime.now().isoformat()
        self._save_to_disk()

    def get_all(self) -> List[Dict]:
        return list(self._stories.values())

    def get_by_id(self, story_id: str) -> Optional[Dict]:
        return self._stories.get(story_id)

    def count(self) -> int:
        return len(self._stories)
