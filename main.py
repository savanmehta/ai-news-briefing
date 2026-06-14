import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from supabase_cache import SupabaseCache
from news_fetcher import fetch_all_news
from claude_service import tag_stories, rewrite_story, generate_content_angles
from email_digest import send_digest, send_digest_to
from auth import get_current_user, get_user_client, get_user_email, admin_client

load_dotenv()

cache = SupabaseCache()
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


def _send_user_digests(field: str, hours: int, subject_suffix: str = "") -> int:
    """Send a per-user digest to everyone subscribed via `field` (daily_digest/weekly_digest)."""
    stories = cache.get_all()
    profiles = (
        admin_client.table("profiles")
        .select("user_id, topics")
        .eq(field, True)
        .execute()
        .data
        or []
    )

    sent = 0
    for profile in profiles:
        email = get_user_email(profile["user_id"])
        if not email:
            continue

        result = send_digest_to(
            stories, email, topics=profile.get("topics"), hours=hours, subject_suffix=subject_suffix,
        )
        if result["ok"]:
            sent += 1
            print(f"[{field}] Sent {result['stories_sent']} stories → {email}")
        else:
            print(f"[{field}] Failed for {email}: {result['error']}")

    return sent


async def run_daily_user_digests():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _send_user_digests("daily_digest", hours=24))


async def run_weekly_recaps():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: _send_user_digests("weekly_digest", hours=24 * 7, subject_suffix="Weekly Recap")
    )


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
    scheduler.add_job(
        run_daily_user_digests,
        CronTrigger(hour=4, minute=30),   # 10:00 AM IST (server runs in UTC)
        id="daily_user_digests",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekly_recaps,
        CronTrigger(day_of_week="mon", hour=4, minute=30),   # 10:00 AM IST every Monday
        id="weekly_recaps",
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


@app.get("/api/config")
async def get_config():
    return {
        "supabase_url": os.environ["SUPABASE_URL"],
        "supabase_anon_key": os.environ["SUPABASE_ANON_KEY"],
    }


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


# ── Auth / Profile / Favorites / Read Tracking ──────────────────────────────

@app.get("/api/auth/me")
async def auth_me(ctx: dict = Depends(get_current_user)):
    user = ctx["user"]
    client = get_user_client(ctx["token"])
    profile = client.table("profiles").select("*").eq("user_id", user.id).single().execute()
    return {
        "id": user.id,
        "email": user.email,
        "profile": profile.data,
    }


@app.get("/api/preferences")
async def get_preferences(ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    res = client.table("profiles").select("*").eq("user_id", ctx["user"].id).single().execute()
    return res.data


@app.put("/api/preferences")
async def update_preferences(request: dict, ctx: dict = Depends(get_current_user)):
    allowed = {"role", "topics", "daily_digest", "weekly_digest", "display_name"}
    updates = {k: v for k, v in request.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    client = get_user_client(ctx["token"])
    res = (
        client.table("profiles")
        .update(updates)
        .eq("user_id", ctx["user"].id)
        .execute()
    )
    return {"ok": True, "profile": res.data[0] if res.data else None}


@app.get("/api/favorites")
async def get_favorites(ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    fav_res = (
        client.table("favorites")
        .select("article_id, saved_at")
        .eq("user_id", ctx["user"].id)
        .order("saved_at", desc=True)
        .execute()
    )
    favorites = fav_res.data or []
    if not favorites:
        return {"favorites": []}

    article_ids = [f["article_id"] for f in favorites]
    articles_res = client.table("articles").select("*").in_("id", article_ids).execute()
    articles_by_id = {a["id"]: a for a in (articles_res.data or [])}

    for f in favorites:
        f["article"] = articles_by_id.get(f["article_id"])

    return {"favorites": favorites}


@app.post("/api/favorites/{article_id}")
async def add_favorite(article_id: str, ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    client.table("favorites").upsert({
        "user_id": ctx["user"].id,
        "article_id": article_id,
    }).execute()
    return {"ok": True}


@app.delete("/api/favorites/{article_id}")
async def remove_favorite(article_id: str, ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    client.table("favorites").delete().eq("user_id", ctx["user"].id).eq("article_id", article_id).execute()
    return {"ok": True}


@app.post("/api/read/{article_id}")
async def mark_read(article_id: str, ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    client.table("read_articles").upsert({
        "user_id": ctx["user"].id,
        "article_id": article_id,
    }).execute()
    return {"ok": True}


@app.get("/api/read")
async def get_read(ctx: dict = Depends(get_current_user)):
    client = get_user_client(ctx["token"])
    res = client.table("read_articles").select("article_id").eq("user_id", ctx["user"].id).execute()
    return {"read_ids": [r["article_id"] for r in res.data]}
