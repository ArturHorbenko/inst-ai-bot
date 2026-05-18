---
name: grill-reel
description: Grill an Instagram reel — index it via the local inst-ai-bot API (caption, transcript, top comments), then deliver direct creator-focused feedback on hook, pacing, audience reaction, and what to change next time. Use when the user wants to grill a reel, review their short-form video, get creator feedback on an Instagram URL, or asks "what would make this reel better".
---

# grill-reel

Run an opinionated creator-feedback pass on an Instagram reel using the local `inst-ai-bot` HTTP API.

## Quick start

1. Confirm the user supplied an Instagram reel URL (`instagram.com/reel/...` or `/p/...`). If not, ask for one.
2. Run the bundled script:

   ```bash
   python3 .claude/skills/grill-reel/scripts/grill.py "<reel-url>"
   ```

3. Show the script's stdout to the user as the grill output. Surface any `ERROR:` line directly — don't retry blindly.

## What the script does

- Hits `GET /health` to verify the server is up. Fails with a clear instruction if not.
- `POST /artifacts {url}` — indexes the reel (idempotent; fast on a re-grill).
- Extracts caption, hashtags, uploader, and the top scraped comments from the artifact.
- `POST /runs` with a hard-coded grill prompt that embeds the post metadata + transcript and instructs Gemini to watch the video and deliver feedback in four sections: Hook, Pacing & retention, Audience signal, Improve list.
- Prints the run output to stdout. Logs progress to stderr.

## Assumptions

- The `inst-ai-bot` server is running at `http://localhost:8000`. If it isn't, the script fails with a hint to run `npm run dev:backend`. Do **not** attempt to start the server yourself.
- `GEMINI_API_KEY` and `GROQ_API_KEY` are configured in the repo's `.env`.

## Customizing

- Different server URL: pass `--server http://host:port`.
- Different grilling angle (e.g. only hook critique, or B2B vs lifestyle framing): edit `GRILL_PROMPT_TEMPLATE` in `scripts/grill.py`. The script is the source of truth for the prompt — don't paraphrase it elsewhere.

## What this skill does NOT do

- Doesn't start, restart, or check the dev server lifecycle. Assume running.
- Doesn't analyze non-Instagram URLs (TikTok, YouTube). The downloader rejects them.
- Doesn't follow up with additional runs. One reel → one grill output. If the user wants a second angle, run it again with a different prompt (or extend the script).
