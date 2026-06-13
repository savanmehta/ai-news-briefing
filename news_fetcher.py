import re
import feedparser
import httpx
import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

# ── RSS / Atom feeds ──────────────────────────────────────────────────────────
# "limit" caps entries taken per feed (default 12 if omitted).
RSS_FEEDS = [
    # ArXiv — research papers
    {"url": "http://arxiv.org/rss/cs.AI",  "source": "ArXiv AI",      "category": "Research"},
    {"url": "http://arxiv.org/rss/cs.LG",  "source": "ArXiv ML",      "category": "Research"},
    {"url": "http://arxiv.org/rss/cs.CL",  "source": "ArXiv NLP",     "category": "Research"},
    {"url": "http://arxiv.org/rss/cs.CV",  "source": "ArXiv CV",      "category": "Research"},
    {"url": "http://arxiv.org/rss/stat.ML","source": "ArXiv Stat.ML", "category": "Research"},
    # Industry blogs
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "source": "TechCrunch AI",  "category": "Industry"},
    {"url": "https://venturebeat.com/category/ai/feed/",                     "source": "VentureBeat AI", "category": "Industry"},
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "source": "The Verge AI", "category": "Industry"},
    {"url": "https://simonwillison.net/atom/everything/",                    "source": "Simon Willison", "category": "Industry"},
    # Company blogs
    {"url": "https://openai.com/blog/rss.xml",       "source": "OpenAI Blog",     "category": "Company"},
    {"url": "https://deepmind.google/blog/rss.xml",  "source": "Google DeepMind", "category": "Company"},
    # Newsletters / Substacks (6 confirmed working feeds)
    {"url": "https://aishwaryasrinivasan.substack.com/feed",  "source": "AI with Aish",       "category": "Newsletter"},
    {"url": "https://importai.substack.com/feed",             "source": "Import AI",          "category": "Newsletter"},
    {"url": "https://tldr.tech/rss/ai",                       "source": "TLDR AI",            "category": "Newsletter"},
    {"url": "https://magazine.sebastianraschka.com/feed",     "source": "Ahead of AI",        "category": "Newsletter"},
    {"url": "https://thealgorithmicbridge.substack.com/feed", "source": "Algorithmic Bridge", "category": "Newsletter"},
    {"url": "https://bensbites.com/feed",                     "source": "Ben's Bites",        "category": "Newsletter"},
    # The Batch (deeplearning.ai) and The Rundown AI have no public RSS feed.
    # YouTube AI channels — channel_id format still works but IDs must be verified
    # These are commented out until channel IDs are confirmed current (all 4 returned 404
    # in June 2026, likely due to channel migrations; re-add with fresh IDs from the
    # channel's /about page source: look for "channelId" in the page HTML).
    # {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCH-lyty0WB9v-Bs7Wq-xb4g",
    #  "source": "Karpathy (YT)",       "category": "Education", "limit": 6},
    # {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",
    #  "source": "Two Minute Papers",   "category": "Education", "limit": 6},
    # {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCNJ1Ymd5yFuUPtn21xtRbbw",
    #  "source": "AI Explained (YT)",   "category": "Education", "limit": 6},
    # {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCZHmQk67mSJgfCCTn7xBfew",
    #  "source": "Yannic Kilcher (YT)", "category": "Education", "limit": 6},
]

# ── Nitter instances (Twitter/X via RSS) ──────────────────────────────────────
# Tried in order; first that returns valid RSS wins. Nitter instances come and go
# as Twitter/X blocks them — failures are handled silently.
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.unixfox.eu",
    "https://nitter.1d4.us",
]
NITTER_ACCOUNTS = [
    {"username": "OpenAI",        "source": "X: @OpenAI"},
    {"username": "AnthropicAI",   "source": "X: @AnthropicAI"},
    {"username": "GoogleDeepMind","source": "X: @GoogleDeepMind"},
    {"username": "karpathy",      "source": "X: @karpathy"},
    {"username": "sama",          "source": "X: @sama"},
    {"username": "ylecun",        "source": "X: @ylecun"},
    {"username": "emollick",      "source": "X: @emollick"},
]

# ── GitHub repos to track releases for ───────────────────────────────────────
GITHUB_RELEASE_REPOS = [
    "huggingface/transformers",
    "openai/openai-python",
    "anthropics/anthropic-sdk-python",
    "langchain-ai/langchain",
    "ollama/ollama",
    "microsoft/autogen",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()[:12]


def clean_html(raw: str, max_len: int = 500) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(separator=" ").strip()
    text = " ".join(text.split())
    # Strip ArXiv boilerplate
    for prefix in ("Announce Type: new Abstract:", "Announce Type: new\nAbstract:"):
        if prefix in text:
            text = text[text.index(prefix) + len(prefix):].strip()
    text = re.sub(r'^arXiv:\S+\s*', '', text).strip()
    return text[:max_len]


def _get_published(entry) -> str:
    if hasattr(entry, "published"):
        return entry.published
    if hasattr(entry, "updated"):
        return entry.updated
    return ""


# ── RSS fetch (shared for all RSS/Atom feeds) ─────────────────────────────────

async def fetch_rss_feed(client: httpx.AsyncClient, feed_info: dict) -> List[Dict]:
    limit = feed_info.get("limit", 12)
    try:
        response = await client.get(feed_info["url"], timeout=15.0)
        response.raise_for_status()
        parsed = feedparser.parse(response.text)

        stories = []
        for entry in parsed.entries[:limit]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            summary = ""
            if hasattr(entry, "summary"):
                summary = clean_html(entry.summary, 500)
            elif hasattr(entry, "description"):
                summary = clean_html(entry.description, 500)

            image_url: Optional[str] = None
            if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get("url")
            elif hasattr(entry, "media_content") and entry.media_content:
                image_url = entry.media_content[0].get("url")

            stories.append({
                "id": make_id(title, link),
                "title": title,
                "summary": summary,
                "url": link,
                "source": feed_info["source"],
                "category": feed_info["category"],
                "published": _get_published(entry),
                "topics": [],
                "image_url": image_url,
            })

        return stories
    except Exception as e:
        print(f"Feed error [{feed_info['source']}]: {type(e).__name__}: {e}")
        return []


# ── Nitter / Twitter RSS ──────────────────────────────────────────────────────

async def _find_nitter_base() -> Optional[str]:
    """Return the first Nitter instance that serves a valid RSS feed, or None."""
    async def _probe(base: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(
                timeout=8.0, follow_redirects=True,
                headers={"User-Agent": "AI-News-Briefing/1.0"}
            ) as client:
                r = await client.get(f"{base}/karpathy/rss")
                if r.status_code == 200 and "<rss" in r.text[:500]:
                    return base
        except Exception:
            pass
        return None

    results = await asyncio.gather(*[_probe(b) for b in NITTER_INSTANCES])
    return next((r for r in results if r), None)


async def fetch_nitter_feeds() -> List[Dict]:
    """Fetch recent tweets from key AI accounts via Nitter RSS.
    Tries multiple instances; returns [] gracefully if all are down."""
    base = await _find_nitter_base()
    if not base:
        print("Nitter: all instances down or blocking — skipping Twitter/X feeds")
        return []

    async def _fetch_account(username: str, display: str) -> List[Dict]:
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
                headers={"User-Agent": "AI-News-Briefing/1.0"}
            ) as client:
                r = await client.get(f"{base}/{username}/rss")
                r.raise_for_status()
            parsed = feedparser.parse(r.text)
            results = []
            for entry in parsed.entries[:5]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "").strip()
                if not title or not link:
                    continue
                canonical = re.sub(r'https?://[^/]+', 'https://x.com', link)
                summary   = clean_html(entry.get("summary", ""), 400) or title
                results.append({
                    "id": make_id(title, link),
                    "title": f"@{username}: {title[:120]}",
                    "summary": summary,
                    "url": canonical,
                    "source": display,
                    "category": "Social",
                    "published": _get_published(entry),
                    "topics": [],
                    "image_url": None,
                })
            return results
        except Exception as e:
            print(f"Nitter [{username}] skipped: {type(e).__name__}")
            return []

    results = await asyncio.gather(
        *[_fetch_account(a["username"], a["source"]) for a in NITTER_ACCOUNTS],
        return_exceptions=True,
    )
    stories: List[Dict] = []
    for r in results:
        if isinstance(r, list):
            stories.extend(r)
    print(f"Nitter [{base}]: {len(stories)} tweets fetched")
    return stories


# ── GitHub Releases ───────────────────────────────────────────────────────────

async def fetch_github_releases() -> List[Dict]:
    """Fetch the latest release from each tracked AI repo."""
    import os
    gh_headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "AI-News-Briefing/1.0"}
    if os.environ.get("GITHUB_TOKEN"):
        gh_headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    async def _fetch_release(repo: str) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{repo}/releases/latest",
                    headers=gh_headers,
                )
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                data = r.json()

            tag = data.get("tag_name", "")
            body = clean_html(data.get("body", ""), 400)
            url = data.get("html_url", "")
            return {
                "id": make_id(f"{repo}@{tag}", url),
                "title": f"🚀 {repo} released {tag}",
                "summary": body or f"New release {tag} of {repo}.",
                "url": url,
                "source": "GitHub Releases",
                "category": "Open Source",
                "published": data.get("published_at", ""),
                "topics": ["Open Source"],
                "image_url": None,
            }
        except Exception as e:
            print(f"GitHub release [{repo}] skipped: {type(e).__name__}: {e}")
            return None

    results = await asyncio.gather(
        *[_fetch_release(r) for r in GITHUB_RELEASE_REPOS],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]


# ── HuggingFace Daily Papers (JSON API — no RSS available) ───────────────────

async def fetch_huggingface_papers() -> List[Dict]:
    """Fetch today's trending papers from huggingface.co/papers via JSON API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://huggingface.co/api/daily_papers",
                params={"limit": 20},
            )
            r.raise_for_status()
            papers = r.json()

        stories: List[Dict] = []
        for p in papers:
            paper_id = p.get("paper", {}).get("id", "")
            title    = p.get("title", "").strip()
            summary  = (p.get("summary", "") or "").strip()[:500]
            url      = f"https://huggingface.co/papers/{paper_id}" if paper_id else ""
            if not title or not url:
                continue
            stories.append({
                "id": make_id(title, url),
                "title": title,
                "summary": summary,
                "url": url,
                "source": "HF Papers",
                "category": "Research",
                "published": p.get("publishedAt", ""),
                "topics": ["Research"],
                "image_url": p.get("thumbnail"),
            })
        return stories
    except Exception as e:
        print(f"HF Papers error: {type(e).__name__}: {e}")
        return []


# ── Hacker News ───────────────────────────────────────────────────────────────

async def fetch_hackernews() -> List[Dict]:
    """Two separate Algolia queries (Algolia HN doesn't support boolean OR chains)."""
    seen: set = set()
    stories: List[Dict] = []

    async def _query(q: str) -> list:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={"tags": "story", "query": q,
                            "numericFilters": "points>20", "hitsPerPage": 10},
                )
                return r.json().get("hits", [])
        except Exception as e:
            print(f"HN query '{q}' error: {e}")
            return []

    all_hits: list = []
    for hits in await asyncio.gather(
        _query("artificial intelligence"), _query("large language model")
    ):
        all_hits.extend(hits)

    for hit in all_hits:
        title = hit.get("title", "").strip()
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
        if not title or title in seen:
            continue
        seen.add(title)
        points   = hit.get("points", 0)
        comments = hit.get("num_comments", 0)
        stories.append({
            "id": make_id(title, url),
            "title": title,
            "summary": f"Trending on Hacker News — {points} points · {comments} comments",
            "url": url,
            "source": "Hacker News",
            "category": "Community",
            "published": hit.get("created_at", ""),
            "topics": [],
            "image_url": None,
        })
    return stories


# ── GitHub Trending repos ─────────────────────────────────────────────────────

async def fetch_github_trending() -> List[Dict]:
    """Three separate topic searches (OR between topic: qualifiers is invalid in GH API)."""
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    import os
    gh_headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "AI-News-Briefing/1.0"}
    if os.environ.get("GITHUB_TOKEN"):
        gh_headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    async def _search(topic: str) -> list:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": f"topic:{topic} pushed:>{week_ago}",
                            "sort": "stars", "order": "desc", "per_page": 6},
                    headers=gh_headers,
                )
                return r.json().get("items", [])
        except Exception as e:
            print(f"GitHub topic:{topic} error: {e}")
            return []

    all_items: list = []
    for batch in await asyncio.gather(
        _search("machine-learning"), _search("llm"), _search("ai")
    ):
        all_items.extend(batch)

    seen: set = set()
    stories: List[Dict] = []
    for repo in all_items:
        url = repo.get("html_url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        name        = repo.get("full_name", "")
        description = repo.get("description") or "No description provided"
        stars       = repo.get("stargazers_count", 0)
        language    = repo.get("language") or ""
        lang_str    = f" · {language}" if language else ""
        stories.append({
            "id": make_id(name, url),
            "title": f"⭐ {name}",
            "summary": f"{description} — {stars:,} stars{lang_str}",
            "url": url,
            "source": "GitHub Trending",
            "category": "Open Source",
            "published": repo.get("pushed_at", ""),
            "topics": ["Open Source"],
            "image_url": None,
        })

    def _stars(s: Dict) -> int:
        try:
            return int(s["summary"].split("—")[1].strip().split(" ")[0].replace(",", ""))
        except Exception:
            return 0

    stories.sort(key=_stars, reverse=True)
    return stories[:12]


# ── Main aggregator ───────────────────────────────────────────────────────────

async def fetch_all_news() -> List[Dict]:
    rss_client_headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; AI-News-Briefing/1.0; "
            "+https://github.com/ainews)"
        )
    }

    # RSS/Atom feeds share one connection pool
    async with httpx.AsyncClient(
        headers=rss_client_headers, follow_redirects=True, timeout=15.0
    ) as client:
        rss_tasks   = [fetch_rss_feed(client, feed) for feed in RSS_FEEDS]
        rss_results = await asyncio.gather(*rss_tasks, return_exceptions=True)

    # Independent fetchers run concurrently
    hn, gh_trending, gh_releases, nitter, hf_papers = await asyncio.gather(
        fetch_hackernews(),
        fetch_github_trending(),
        fetch_github_releases(),
        fetch_nitter_feeds(),
        fetch_huggingface_papers(),
    )

    all_stories: List[Dict] = []
    for result in rss_results:
        if isinstance(result, list):
            all_stories.extend(result)
    all_stories.extend(hn)
    all_stories.extend(gh_trending)
    all_stories.extend(gh_releases)
    all_stories.extend(nitter)
    all_stories.extend(hf_papers)

    # Deduplicate by ID
    seen: set = set()
    unique: List[Dict] = []
    for story in all_stories:
        if story["id"] not in seen:
            seen.add(story["id"])
            unique.append(story)

    total_sources = len(RSS_FEEDS) + 5  # +HN, GH Trending, GH Releases, Nitter, HF Papers
    print(f"Fetched {len(unique)} unique stories from {total_sources} source groups")
    return unique
