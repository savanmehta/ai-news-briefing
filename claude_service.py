"""
AI service using the HuggingFace Inference API (free tier).
Primary model: mistralai/Mistral-7B-Instruct-v0.3
HF_TOKEN is optional — keyword fallback tagging always works without it.
With a free HF account token the API is more reliable and unlocks personalization.
"""
import httpx
import json
import asyncio
import os
import re
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
HF_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"

TOPICS = ["Models", "Agents", "Infrastructure", "Research", "Policy", "Open Source"]
BATCH_SIZE = 20

ROLE_DESCRIPTIONS = {
    "Developer": (
        "a software developer/engineer who cares about APIs, SDKs, technical implementation, "
        "and how to build or integrate this technology into real projects"
    ),
    "Founder": (
        "a startup founder or executive who needs business implications, market opportunities, "
        "competitive threats, and strategic decisions — skip the technical jargon"
    ),
    "Content Creator": (
        "a content creator, influencer, or marketer who wants hot takes, viral angles, "
        "and audience-friendly explanations for LinkedIn or YouTube"
    ),
    "Non-Technical": (
        "someone without a technical background who wants plain English explanations, "
        "real-world impact, and why this matters for everyday life"
    ),
}

# ── Keyword fallback tagger (always works, no API needed) ─────────────────────
_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Models": [
        "model", "gpt", "llm", "llms", "claude", "gemini", "mistral", "llama",
        "benchmark", "training", "fine-tun", "neural", "transformer", "weights",
        "parameter", "token", "multimodal", "vision", "language model",
    ],
    "Agents": [
        "agent", "agentic", "autonomous", "tool use", "multi-agent", "mcp",
        "copilot", "workflow", "orchestrat", "rag", "retrieval", "function call",
    ],
    "Infrastructure": [
        "gpu", "cloud", "compute", "mlops", "deploy", "kubernetes", "tpu",
        "cuda", "hardware", "cluster", "inference", "serving", "latency",
        "throughput", "nvidia", "amd", "chip", "accelerat",
    ],
    "Research": [
        "paper", "arxiv", "study", "research", "university", "novel", "propose",
        "dataset", "evaluation", "experiment", "findings", "scaling law",
    ],
    "Policy": [
        "regulation", "policy", "government", "law", "copyright", "safety",
        "ethics", "governance", "ban", "legal", "compliance", "watermark",
        "election", "deepfake", "misinformation", "risk",
    ],
    "Open Source": [
        "open source", "open-source", "github", "hugging face", "huggingface",
        "library", "framework", "repo", "release", "mit license", "apache",
    ],
}


def keyword_tag(story: Dict) -> List[str]:
    text = (story.get("title", "") + " " + story.get("summary", "")).lower()
    tags = [
        topic for topic, kws in _TOPIC_KEYWORDS.items()
        if any(kw in text for kw in kws)
    ]
    return tags[:3]


# ── HuggingFace helpers ───────────────────────────────────────────────────────

def _hf_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _hf_chat(messages: list, max_tokens: int = 500) -> str:
    payload = {
        "model": HF_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(HF_CHAT_URL, headers=_hf_headers(), json=payload)

        # 503 = model still loading; wait and retry once
        if resp.status_code == 503:
            await asyncio.sleep(20)
            resp = await client.post(HF_CHAT_URL, headers=_hf_headers(), json=payload)

        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _extract_json(text: str):
    """Try to extract valid JSON from potentially noisy LLM output."""
    text = _strip_fences(text)
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find a JSON array or object in the text
    for pattern in (r'\[.*\]', r'\{.*\}'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

async def tag_stories(stories: List[Dict]) -> List[Dict]:
    """Tag stories with topics. Uses HF if token present, keyword fallback always runs."""
    token = os.environ.get("HF_TOKEN", "").strip()

    # Always apply keyword tags as the baseline
    for story in stories:
        if not story.get("topics"):
            story["topics"] = keyword_tag(story)

    if not token:
        return stories  # keyword tags are good enough without a token

    # Refine with HF in batches
    async def refine_batch(batch: List[Dict]) -> Dict[str, List[str]]:
        lines = "\n".join(
            f'{i+1}. [ID:{s["id"]}] {s["title"]}: {s["summary"][:180]}'
            for i, s in enumerate(batch)
        )
        prompt = (
            f"Classify these AI news stories. Available topics: {', '.join(TOPICS)}\n\n"
            "Rules:\n"
            "- Models: AI model releases, training, benchmarks, capabilities\n"
            "- Agents: AI agents, agentic AI, autonomous systems, multi-agent\n"
            "- Infrastructure: cloud, compute, GPUs, MLOps, deployment\n"
            "- Research: academic papers, scientific findings\n"
            "- Policy: AI regulation, ethics, safety, governance\n"
            "- Open Source: open source tools, libraries, repos\n\n"
            "Assign 1-3 topics per story. Return ONLY a JSON array, no explanation:\n"
            '[{"id":"abc123","topics":["Models"]}, ...]\n\n'
            f"Stories:\n{lines}"
        )
        try:
            raw = await _hf_chat([{"role": "user", "content": prompt}], max_tokens=800)
            parsed = _extract_json(raw)
            if isinstance(parsed, list):
                return {item["id"]: item["topics"] for item in parsed if "id" in item}
        except Exception as e:
            print(f"HF tagging batch error: {e}")
        return {}

    tasks = [
        refine_batch(stories[i: i + BATCH_SIZE])
        for i in range(0, len(stories), BATCH_SIZE)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if not isinstance(result, dict):
            continue
        for story in stories:
            if story["id"] in result:
                hf_topics = result[story["id"]]
                existing = story.get("topics") or []
                story["topics"] = list(dict.fromkeys(existing + hf_topics))[:3]

    return stories


async def rewrite_story(story: Dict, role: str, detail_level: str = "short") -> str:
    role_desc = ROLE_DESCRIPTIONS.get(role, ROLE_DESCRIPTIONS["Non-Technical"])

    if detail_level == "short":
        length = "Write exactly 2-3 sentences. Be punchy and direct."
        max_tokens = 200
    else:
        length = (
            "Write 3-4 paragraphs covering: what happened, why it matters, "
            "key implications, and what to watch next."
        )
        max_tokens = 600

    prompt = (
        f"Rewrite this AI news summary for {role_desc}.\n\n"
        f"Title: {story['title']}\n"
        f"Source: {story['source']}\n"
        f"Summary: {story['summary'][:600]}\n\n"
        f"{length}\n"
        "Do NOT start with 'For [role]...' — write the summary directly."
    )
    try:
        return await _hf_chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    except Exception as e:
        print(f"HF rewrite error: {e}")
        return story["summary"]


async def generate_content_angles(story: Dict) -> Dict:
    prompt = (
        "Generate content angles for this AI news story.\n\n"
        f"Title: {story['title']}\n"
        f"Source: {story['source']}\n"
        f"Summary: {story['summary'][:600]}\n\n"
        "Return ONLY valid JSON (no markdown):\n"
        '{\n'
        '  "linkedin_hook": "1-2 sentence hook that stops the scroll — bold insight or question, not \'Exciting news\'",\n'
        '  "newsletter_angle": "2-3 sentence newsletter opener with an opinionated take",\n'
        '  "talking_points": ["Point 1", "Point 2", "Point 3", "Point 4", "Point 5"]\n'
        '}'
    )
    try:
        raw = await _hf_chat([{"role": "user", "content": prompt}], max_tokens=500)
        parsed = _extract_json(raw)
        if isinstance(parsed, dict) and "linkedin_hook" in parsed:
            return parsed
    except Exception as e:
        print(f"HF content angles error: {e}")

    return {
        "linkedin_hook": "This AI development is worth your attention.",
        "newsletter_angle": "Here's what you need to know about this story.",
        "talking_points": [
            "See the original article for full details.",
        ],
    }
