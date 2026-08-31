---
name: grill-reel
description: Grill an Instagram reel — index it via the inst-ai-bot MCP server (caption, transcript, top comments), then deliver direct creator-focused feedback on hook, pacing, audience reaction, and what to change next time. Use when the user wants to grill a reel, review their short-form video, get creator feedback on an Instagram URL, or asks "what would make this reel better".
---

# grill-reel

Run an opinionated creator-feedback pass on an Instagram reel by calling the `inst-ai-bot` MCP server. The server exposes the one creator configured by the server.

## Quick start

1. Confirm the user supplied an Instagram reel URL (`instagram.com/reel/...` or `/p/...`). If not, ask for one.

2. Call MCP tool **`inst-ai-bot.get_current_creator_profile`** with `{"days": 60}`. Keep the returned profile as the current evidence-based creator context.

3. Call MCP tool **`inst-ai-bot.index_video_from_url`** with `{"url": "<reel-url>"}`. It returns `{content_hash, transcript_text, caption, hashtags, uploader, comments, ...}`. Idempotent — fast on a re-grill.

4. Format `comments` as a numbered list (see "Comment formatting" below) and `hashtags` as a comma-joined string. Serialize the creator profile as readable JSON. Fill the template in "Prompt template" with the profile and indexed Reel fields.

5. Call MCP tool **`inst-ai-bot.run_prompt`** with:
   - `artifact_hash`: the `content_hash` from step 3
   - `prompt`: the filled template from step 4
   - `model`: `"google/gemini-2.5-pro"`
   - `label`: `"grill"`

6. Show the `output` field from the response to the user as the grill output. Don't paraphrase. Surface any error from the MCP tool call directly.

## Assumptions

- The `inst-ai-bot` MCP server is configured in the current client (see `skills/README.md` for per-client setup). If the MCP tools aren't available, tell the user — do **not** try to start a server or fall back to anything else.
- `GEMINI_API_KEY` and `GROQ_API_KEY` live in the server's `.env`. Skill-side has no secrets.

## Customizing

- **Different grilling angle** (e.g. hook-only critique, B2B vs lifestyle framing): edit the "Prompt template" block below.
- **Different model**: pass a different `model` to `run_prompt`. Only the `google` provider is wired today.

## Comment formatting

Comments come back as a list of `{author, text, like_count}` dicts. Render them as:

```
1. @author [N likes]: text on one line
2. @author2: another comment
```

If `comments` is empty or missing, write `(none scraped — either disabled, or the post has no comments)`.

If `caption` is empty, use `(no caption)`. Same fallback shape for `hashtags` → `(none)` and `uploader` → `(unknown)`.

## Prompt template

Use this prompt verbatim, replacing `{placeholders}` with the values from step 2 (formatted per "Comment formatting"):

```
You are reviewing an Instagram reel as a senior short-form video creator. Your job is to tell this creator, directly and specifically, what they could do better next time.

CURRENT EVIDENCE-BASED CREATOR PROFILE
{creator_profile}

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

Be direct. Don't pad. If something is genuinely good, say so in one line and move on. Total length: aim for tight, not exhaustive.
```

## What this skill does NOT do

- Doesn't propose remixes or adaptations. That's `/adapt-reel`.
- Doesn't start, restart, or check any server lifecycle. The MCP connector either works or it doesn't — report cleanly and stop.
- Doesn't analyze non-Instagram URLs (TikTok, YouTube). The indexer rejects them.
- Doesn't follow up with additional runs. One reel → one grill output. If the user wants a second angle, run it again with a different prompt.
