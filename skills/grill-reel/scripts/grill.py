#!/usr/bin/env python3
"""
Grill an Instagram reel: index it via the local inst-ai-bot API, then run an
opinionated creator-feedback prompt against the artifact. Prints the grill
output to stdout.

Usage: grill.py <instagram-reel-url> [--server URL]

Server URL precedence: --server flag > $INST_AI_BOT_URL > http://localhost:8000.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_SERVER = os.environ.get("INST_AI_BOT_URL", "http://localhost:8000")
RUN_LABEL = "grill"
RUN_MODEL = "google/gemini-2.5-pro"


GRILL_PROMPT_TEMPLATE = """You are reviewing an Instagram reel as a senior short-form video creator. \
Your job is to tell this creator, directly and specifically, what they could do better next time.

CONTEXT FROM THE POST
Caption: {caption}
Hashtags: {hashtags}
Uploader: {uploader}

TOP COMMENTS (what the audience actually said — read them honestly)
{comments_block}

TRANSCRIPT (what was said in the video)
{transcript}

Now watch the video itself, then deliver feedback in these sections:

1. **Hook (0–3s)** — Does the opener earn the watch? What would make it stronger? Be specific about the frame, line, or cut you would change.
2. **Pacing & retention** — Where will viewers drop off? Which beats drag? Which land?
3. **Audience signal** — What do the comments reveal about how this landed (hooked, confused, off-topic, hostile, missing the point)? Quote a comment if it sharpens the point.
4. **The improve list** — 3 to 5 concrete edits the creator should try next time. No platitudes like "post more consistently". Specific changes: "cut the intro line, open on the X frame", "add a B-roll insert at ~0:08", "the CTA at the end is too soft — try Y instead".

Be direct. Don't pad. If something is genuinely good, say so in one line and move on. Total length: aim for tight, not exhaustive."""


def _post(url: str, payload: dict, timeout: int = 600) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _check_health(server: str) -> None:
    try:
        _get(f"{server}/health", timeout=5)
    except (urllib.error.URLError, ConnectionError) as e:
        sys.exit(
            f"ERROR: inst-ai-bot server is not reachable at {server} ({e}).\n"
            f"Start it with: npm run dev:backend (or python -m fastapi run server.py)"
        )


def _format_comments(comments: list) -> str:
    if not comments:
        return "(none scraped — either disabled, or the post has no comments)"
    lines = []
    for i, c in enumerate(comments, 1):
        author = c.get("author") or "anon"
        text = (c.get("text") or "").strip().replace("\n", " ")
        likes = c.get("like_count")
        likes_suffix = f" [{likes} likes]" if likes else ""
        lines.append(f"{i}. @{author}{likes_suffix}: {text}")
    return "\n".join(lines)


def _extract_post_metadata(artifact: dict) -> dict:
    """Pull caption / hashtags / comments from the first Instagram source on the artifact."""
    for src in artifact.get("sources", []):
        if src.get("type") == "instagram_reel":
            md = src.get("metadata", {}) or {}
            return {
                "caption": md.get("caption") or "(no caption)",
                "hashtags": ", ".join(md.get("hashtags") or []) or "(none)",
                "uploader": md.get("uploader") or "(unknown)",
                "comments": md.get("comments") or [],
            }
    return {"caption": "(no caption)", "hashtags": "(none)", "uploader": "(unknown)", "comments": []}


def grill(url: str, server: str) -> None:
    _check_health(server)

    print(f"→ Indexing {url} ...", file=sys.stderr)
    try:
        artifact = _post(f"{server}/artifacts", {"url": url})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.exit(f"ERROR: /artifacts returned {e.code}: {body}")

    content_hash = artifact["content_hash"]
    print(f"→ Artifact: {content_hash}", file=sys.stderr)

    post_meta = _extract_post_metadata(artifact)
    transcript_text = (artifact.get("transcript") or {}).get("text") or "(no transcript)"

    prompt = GRILL_PROMPT_TEMPLATE.format(
        caption=post_meta["caption"],
        hashtags=post_meta["hashtags"],
        uploader=post_meta["uploader"],
        comments_block=_format_comments(post_meta["comments"]),
        transcript=transcript_text,
    )

    print(f"→ Running grill prompt ({RUN_MODEL}) ...", file=sys.stderr)
    try:
        run = _post(
            f"{server}/runs",
            {
                "artifact": content_hash,
                "prompt": prompt,
                "model": RUN_MODEL,
                "label": RUN_LABEL,
            },
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.exit(f"ERROR: /runs returned {e.code}: {body}")

    print(run["output"])
    print(f"\n---\nrun_id: {run['run_id']}  artifact: {content_hash}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="Grill an Instagram reel for creator feedback.")
    p.add_argument("url", help="Instagram reel URL")
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"inst-ai-bot server URL (default: {DEFAULT_SERVER})")
    args = p.parse_args()
    grill(args.url, args.server)


if __name__ == "__main__":
    main()
