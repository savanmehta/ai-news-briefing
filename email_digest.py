"""
Daily email digest — builds a clean HTML email from the last 24 hrs of cached
stories and sends it via Gmail SMTP (TLS, port 587).

Required .env keys:
  DIGEST_EMAIL_TO      recipient address
  DIGEST_EMAIL_FROM    Gmail address used as sender
  GMAIL_APP_PASSWORD   Gmail App Password (not your regular password)
                       Create one at myaccount.google.com/apppasswords after
                       enabling 2-Step Verification on the account.
"""
import html
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

# ── Category config ───────────────────────────────────────────────────────────

# Display order and per-category story caps (sum = 20)
CATEGORY_CONFIG: List[Tuple[str, int]] = [
    ("Newsletter",  5),
    ("Research",    4),
    ("Industry",    4),
    ("Company",     2),
    ("Open Source", 2),
    ("Community",   2),
    ("Social",      1),
]

CATEGORY_COLORS = {
    "Research":    ("#166534", "#dcfce7"),
    "Industry":    ("#1d4ed8", "#dbeafe"),
    "Company":     ("#6d28d9", "#ede9fe"),
    "Newsletter":  ("#065f46", "#ecfdf5"),
    "Open Source": ("#15803d", "#f0fdf4"),
    "Community":   ("#92400e", "#fef9c3"),
    "Social":      ("#9d174d", "#fdf2f8"),
    "Education":   ("#c2410c", "#fff7ed"),
}


# ── Date parsing ──────────────────────────────────────────────────────────────

def _parse_dt(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    for parser in (
        lambda s: parsedate_to_datetime(s),
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    ):
        try:
            dt = parser(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None


# ── Story selection ───────────────────────────────────────────────────────────

def filter_recent(stories: List[Dict], hours: int = 24) -> List[Dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = [s for s in stories if (dt := _parse_dt(s.get("published", ""))) and dt >= cutoff]
    # Fall back to all stories when the last-24h window is too sparse
    return recent if len(recent) >= 10 else stories


def filter_by_topics(stories: List[Dict], topics: Optional[List[str]]) -> List[Dict]:
    """Keep stories that match one of the given topics, or have no topics at all."""
    if not topics:
        return stories
    topic_set = set(topics)
    return [
        s for s in stories
        if not s.get("topics") or any(t in topic_set for t in s.get("topics", []))
    ]


def select_diverse(stories: List[Dict]) -> List[Dict]:
    """Round-robin across categories in priority order, respecting per-category caps."""
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for story in stories:
        by_cat[story.get("category", "Industry")].append(story)

    selected: List[Dict] = []
    queues = {cat: list(by_cat.get(cat, [])) for cat, _ in CATEGORY_CONFIG}

    for cat, limit in CATEGORY_CONFIG:
        q = queues[cat]
        selected.extend(q[:limit])

    return selected


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_digest_html(stories: List[Dict]) -> Tuple[str, str]:
    """Return (subject, html_body) for the digest email."""
    now = datetime.now()
    date_str = now.strftime(f"%A, %B {now.day}, %Y")
    subject  = f"🤖 AI Briefing — {now.strftime(f'%B {now.day}, %Y')}"

    # Group selected stories by category in display order
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for story in stories:
        by_cat[story.get("category", "Industry")].append(story)

    sections_html = ""
    for cat, _ in CATEGORY_CONFIG:
        group = by_cat.get(cat, [])
        if not group:
            continue
        fg, bg = CATEGORY_COLORS.get(cat, ("#374151", "#f9fafb"))

        rows = ""
        for s in group:
            title   = html.escape(s.get("title", "No title"))
            summary = html.escape((s.get("summary", "") or "")[:180])
            url     = html.escape(s.get("url", "#"))
            source  = html.escape(s.get("source", ""))

            rows += f"""
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #f3f4f6;">
                  <a href="{url}" style="font-size:14px;font-weight:600;color:#0f172a;
                     text-decoration:none;line-height:1.45;display:block;margin-bottom:5px;"
                  >{title}</a>
                  <span style="font-size:11px;font-weight:600;padding:2px 8px;
                     border-radius:20px;background:{bg};color:{fg};
                     display:inline-block;margin-bottom:5px;">{source}</span>
                  <p style="margin:3px 0 0;font-size:13px;color:#475569;line-height:1.5;">
                    {summary}
                  </p>
                </td>
              </tr>"""

        sections_html += f"""
        <tr>
          <td style="padding:22px 0 6px;">
            <p style="margin:0 0 6px;font-size:11px;font-weight:700;
               text-transform:uppercase;letter-spacing:0.9px;color:{fg};">{cat}</p>
            <div style="height:2px;background:{bg};margin-bottom:2px;border-radius:2px;"></div>
            <table width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
          </td>
        </tr>"""

    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:8000")

    body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f1f5f9;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
  style="background:#f1f5f9;padding:24px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
      style="max-width:600px;width:100%;">

      <!-- Header -->
      <tr><td style="background:#0f172a;border-radius:12px 12px 0 0;padding:26px 32px;">
        <p style="margin:0;font-size:22px;font-weight:800;color:#f1f5f9;
           letter-spacing:-0.5px;">⚡ AI Briefing</p>
        <p style="margin:5px 0 0;font-size:13px;color:#94a3b8;">
          {date_str} &nbsp;·&nbsp; {len(stories)} stories
        </p>
      </td></tr>

      <!-- Body -->
      <tr><td style="background:#ffffff;padding:4px 32px 24px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {sections_html}
        </table>
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#f8fafc;border-radius:0 0 12px 12px;
         padding:18px 32px;border-top:1px solid #e2e8f0;">
        <p style="margin:0;font-size:12px;color:#94a3b8;text-align:center;">
          Open the full dashboard →&nbsp;
          <a href="{dashboard_url}"
             style="color:#8b5cf6;text-decoration:none;">{dashboard_url}</a>
          &nbsp;·&nbsp; AI Briefing Daily Digest
        </p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""

    return subject, body


# ── SMTP sender ───────────────────────────────────────────────────────────────

def send_digest_to(
    all_stories: List[Dict],
    to_addr: str,
    topics: Optional[List[str]] = None,
    hours: int = 24,
    subject_suffix: str = "",
) -> Dict:
    """Filter (by recency + topics), select, build, and send a digest to `to_addr`."""
    from_addr = os.environ.get("DIGEST_EMAIL_FROM", "").strip()
    password  = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    missing = [k for k, v in {
        "DIGEST_EMAIL_FROM": from_addr,
        "GMAIL_APP_PASSWORD": password,
    }.items() if not v]
    if missing:
        return {"ok": False, "error": f"Missing .env keys: {', '.join(missing)}"}

    recent   = filter_recent(all_stories, hours=hours)
    relevant = filter_by_topics(recent, topics)
    selected = select_diverse(relevant)
    if not selected:
        return {"ok": False, "error": "No stories available to send"}

    subject, html_body = build_digest_html(selected)
    if subject_suffix:
        subject = f"{subject} — {subject_suffix}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"AI Briefing <{from_addr}>"
    msg["To"]      = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(from_addr, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())

        return {
            "ok": True,
            "to": to_addr,
            "subject": subject,
            "stories_sent": len(selected),
            "recent_window": len(recent),
        }
    except smtplib.SMTPAuthenticationError:
        return {
            "ok": False,
            "error": (
                "Gmail authentication failed. Make sure GMAIL_APP_PASSWORD is an "
                "App Password (not your regular password). Create one at "
                "myaccount.google.com/apppasswords after enabling 2-Step Verification."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_digest(all_stories: List[Dict]) -> Dict:
    """Send the global admin digest (DIGEST_EMAIL_TO). Returns a status dict."""
    to_addr = os.environ.get("DIGEST_EMAIL_TO", "").strip()
    if not to_addr:
        return {"ok": False, "error": "Missing .env key: DIGEST_EMAIL_TO"}
    return send_digest_to(all_stories, to_addr, hours=24)
