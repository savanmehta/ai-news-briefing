import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from cache import NewsCache
from news_fetcher import fetch_all_news
from claude_service import tag_stories, rewrite_story, generate_content_angles
from email_digest import send_digest

load_dotenv()

cache = NewsCache()
scheduler = AsyncIOScheduler()


async def refresh_news():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching news...")
    try:
        stories = await fetch_all_news()
        stories = await tag_stories(stories)
        cache.update(stories)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cache updated: {len(stories)} stories")
    except Exception as e:
        print(f"Refresh error: {e}")


async def run_send_digest():
    """Async wrapper so APScheduler can call the blocking SMTP function."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: send_digest(cache.get_all()))
    if result["ok"]:
        print(f"[digest] Sent {result['stories_sent']} stories → {result['to']}")
    else:
        print(f"[digest] Failed: {result['error']}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await refresh_news()
    scheduler.add_job(
        refresh_news,
        IntervalTrigger(hours=2),
        id="news_refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        run_send_digest,
        CronTrigger(hour=4, minute=30),   # 10:00 AM IST (server runs in UTC)
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="AI News Briefing", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return Path("static/index.html").read_text()


@app.get("/api/news")
async def get_news(topics: str = ""):
    stories = cache.get_all()
    if topics:
        topic_list = [t.strip() for t in topics.split(",") if t.strip()]
        stories = [
            s for s in stories
            if not s.get("topics") or any(t in s.get("topics", []) for t in topic_list)
        ]
    return {
        "stories": stories,
        "last_updated": cache.last_updated,
        "count": len(stories),
    }


@app.post("/api/refresh")
async def trigger_refresh():
    await refresh_news()
    return {
        "stories": cache.get_all(),
        "last_updated": cache.last_updated,
        "count": cache.count(),
    }


@app.post("/api/rewrite")
async def rewrite(request: dict):
    story_id = request.get("story_id", "")
    role = request.get("role", "Developer")
    detail_level = request.get("detail_level", "short")

    if not os.environ.get("HF_TOKEN"):
        raise HTTPException(status_code=503, detail="HF_TOKEN not configured — add a free HuggingFace token to .env")

    story = cache.get_by_id(story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    rewritten = await rewrite_story(story, role, detail_level)
    return {"rewritten_summary": rewritten}


@app.post("/api/content-angles")
async def content_angles(request: dict):
    story_id = request.get("story_id", "")

    if not os.environ.get("HF_TOKEN"):
        raise HTTPException(status_code=503, detail="HF_TOKEN not configured — add a free HuggingFace token to .env")

    story = cache.get_by_id(story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    angles = await generate_content_angles(story)
    return angles


@app.post("/api/send-digest")
async def send_digest_endpoint():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: send_digest(cache.get_all()))
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.get("/api/status")
async def status():
    refresh_job = scheduler.get_job("news_refresh")
    digest_job  = scheduler.get_job("daily_digest")

    return {
        "stories_count":      cache.count(),
        "last_updated":       cache.last_updated,
        "next_refresh":       refresh_job.next_run_time.isoformat() if refresh_job and refresh_job.next_run_time else None,
        "has_api_key":        bool(os.environ.get("HF_TOKEN")),
        "digest_configured":  bool(os.environ.get("DIGEST_EMAIL_TO") and os.environ.get("GMAIL_APP_PASSWORD")),
        "next_digest":        digest_job.next_run_time.isoformat() if digest_job and digest_job.next_run_time else None,
    }
