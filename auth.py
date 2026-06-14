import os
from typing import Optional

from fastapi import Header, HTTPException
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Service-role client — bypasses RLS, used for admin/scheduled jobs
admin_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_user_client(token: str) -> Client:
    """Client whose requests are scoped to the given user's JWT (RLS applies)."""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client.postgrest.auth(token)
    return client


def get_user_email(user_id: str) -> Optional[str]:
    """Look up a user's email via the admin API. Returns None if not found."""
    try:
        res = admin_client.auth.admin.get_user_by_id(user_id)
        return res.user.email if res and res.user else None
    except Exception:
        return None


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency: validates the bearer token and returns the Supabase user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        res = admin_client.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not res or not res.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = res.user
    admin_client.table("profiles").upsert({
        "user_id": user.id,
        "display_name": (user.user_metadata or {}).get("full_name"),
    }, on_conflict="user_id", ignore_duplicates=True).execute()

    return {"user": user, "token": token}
