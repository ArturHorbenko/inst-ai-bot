---
name: adapt-reel
description: Take an Instagram reel and propose how to adapt its format/structure into the user's niche (tech lifestyle humor, relatable). Index the reel via the local inst-ai-bot API, then return the transferable format, 2-3 niche-specific remix concepts, and a shot-by-shot script for the strongest one. Use when the user wants to remix a reel, steal a format, get adaptation ideas, or asks "how would I make this for my niche".
---

# adapt-reel

Run a niche-adaptation pass on an Instagram reel using the local `inst-ai-bot` HTTP API. The output is a remix plan, not a critique.

## Quick start

1. Confirm the user supplied an Instagram reel URL (`instagram.com/reel/...` or `/p/...`). If not, ask for one.
2. Run the bundled script:

   ```bash
   python3 .claude/skills/adapt-reel/scripts/adapt.py "<reel-url>"
   ```

3. Show the script's stdout to the user as the adaptation output. Surface any `ERROR:` line directly — don't retry blindly.

## What the script does

- Hits `GET /health` to verify the server is up. Fails with a clear instruction if not.
- `POST /artifacts {url}` — indexes the reel (idempotent; fast on a re-run).
- Extracts caption, hashtags, uploader, and top scraped comments from the artifact.
- `POST /runs` with a hard-coded adaptation prompt that embeds the post metadata + transcript and instructs Gemini to: (1) extract the transferable structure, (2) propose 2-3 tech-lifestyle-humor remixes, (3) write a shot-by-shot script for the best one.
- Prints the run output to stdout. Logs progress to stderr.

## The niche (baked into the prompt)

Tech lifestyle humor, relatable. Audience: developers, founders, AI/SaaS people. Tone: self-aware, lightly self-deprecating, observational. Recurring premises that work: vibe coding, AI agents doing your job, founder grind vs lifestyle, devtool absurdities, "me explaining X to my non-technical Y", on-call/PR-review chaos.

## Assumptions

- The `inst-ai-bot` server is running at `http://localhost:8000`. If not, the script fails with a hint to run `npm run dev:backend`. Do **not** attempt to start the server yourself.
- `GEMINI_API_KEY` and `GROQ_API_KEY` are configured in the repo's `.env`.

## Customizing

- Different server URL: pass `--server http://host:port`.
- Different niche framing (e.g. design humor, B2B SaaS, fitness): edit `ADAPT_PROMPT_TEMPLATE` in `scripts/adapt.py`. The script is the source of truth for the prompt — don't paraphrase it elsewhere.

## What this skill does NOT do

- Doesn't grill or critique the source video. That's `/grill-reel`.
- Doesn't start, restart, or check the dev server lifecycle. Assume running.
- Doesn't analyze non-Instagram URLs (TikTok, YouTube). The downloader rejects them.
- Doesn't produce more than one adaptation run per call. Want a different angle? Re-run or edit the prompt.
