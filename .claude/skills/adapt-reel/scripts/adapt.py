#!/usr/bin/env python3
"""
Adapt an Instagram reel to the user's niche (tech lifestyle humor, relatable):
index it via the local inst-ai-bot API, then run an adaptation prompt against
the artifact. Prints the remix plan to stdout.

Usage: adapt.py <instagram-reel-url> [--server http://localhost:8000]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_SERVER = "http://localhost:8000"
RUN_LABEL = "adapt"
RUN_MODEL = "google/gemini-2.5-pro"


ADAPT_PROMPT_TEMPLATE = """You are a short-form video strategist helping a creator adapt other people's reels into \
their own niche.

THE CREATOR'S NICHE
Tech lifestyle humor, relatable. Audience: developers, founders, AI/SaaS people, tech-curious.
Tone: self-aware, lightly self-deprecating, observational. Not cringe-tutorial, not motivational.
Recurring premises that already land for them: vibe coding, AI agents doing your job, founder grind \
vs lifestyle (vacation vs work), devtool absurdities, "me explaining X to my non-technical Y", \
on-call / PR-review chaos, Claude/Cursor/Copilot behaving like a coworker.

THE SOURCE REEL (what the creator is borrowing from)
Caption: {caption}
Hashtags: {hashtags}
Uploader: {uploader}

TOP COMMENTS (audience signal — what people actually responded to)
{comments_block}

TRANSCRIPT
{transcript}

Now watch the video itself, then deliver the adaptation plan in these sections:

1. **Why this works** — In 2-3 sentences, name the underlying mechanic that makes this reel hit. \
Is it a setup/payoff joke? A visual contrast (A vs B)? A POV roleplay? A bait-and-switch? A trend audio? \
A relatable observation? Be precise — name the *format*, not the topic.

2. **The transferable structure** — Strip out the source topic and write the format as a reusable template. \
Use placeholders. Example: "Hook: [unexpected visual]. Cut to: [contradicting context]. Text overlay: \
'[setup] vs [reveal]'. Audio: [emotional beat from trending sound]." Make it copyable.

3. **Three tech-niche remixes** — Three concrete adaptations into the creator's niche. For each: \
one-line premise + the specific tech-world detail that sells the joke. Don't be generic ("about coding") \
— pick a specific situation (a Cursor autocomplete fail, a standup ritual, a deployed-on-Friday moment). \
Rank them: best first.

4. **Shoot-ready script for #1** — A shot-by-shot plan for the strongest remix. Include: \
hook frame (0-1s), on-screen text per beat, dialogue or VO lines, B-roll inserts, the punchline beat, \
total length target. Tight enough that the creator could film it today.

Be direct. No platitudes. If the source format won't transfer cleanly to the niche, say so in section 1 \
and propose the closest workable variant instead. Aim for tight, not exhaustive."""


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


def adapt(url: str, server: str) -> None:
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

    prompt = ADAPT_PROMPT_TEMPLATE.format(
        caption=post_meta["caption"],
        hashtags=post_meta["hashtags"],
        uploader=post_meta["uploader"],
        comments_block=_format_comments(post_meta["comments"]),
        transcript=transcript_text,
    )

    print(f"→ Running adapt prompt ({RUN_MODEL}) ...", file=sys.stderr)
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
    p = argparse.ArgumentParser(description="Adapt an Instagram reel into the user's niche.")
    p.add_argument("url", help="Instagram reel URL")
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"inst-ai-bot server URL (default: {DEFAULT_SERVER})")
    args = p.parse_args()
    adapt(args.url, args.server)


if __name__ == "__main__":
    main()
