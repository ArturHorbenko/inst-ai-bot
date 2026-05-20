---
name: adapt-reel
description: Take an Instagram reel and propose how to adapt its format/structure into the user's niche (tech lifestyle humor, relatable). Index the reel via the inst-ai-bot MCP server, then return the transferable format, 2-3 niche-specific remix concepts, and a shot-by-shot script for the strongest one. Use when the user wants to remix a reel, steal a format, get adaptation ideas, or asks "how would I make this for my niche".
---

# adapt-reel

Run a niche-adaptation pass on an Instagram reel by calling the `inst-ai-bot` MCP server. The output is a remix plan, not a critique.

## Quick start

1. Confirm the user supplied an Instagram reel URL (`instagram.com/reel/...` or `/p/...`). If not, ask for one.

2. Call MCP tool **`inst-ai-bot.index_video_from_url`** with `{"url": "<reel-url>"}`. It returns `{content_hash, transcript_text, caption, hashtags, uploader, comments, ...}`. The call is idempotent — re-running on the same URL is a cheap cache hit.

3. Format `comments` as a numbered list (see "Comment formatting" below) and `hashtags` as a comma-joined string. Build the prompt by filling the template in "Prompt template" with the returned fields.

4. Call MCP tool **`inst-ai-bot.run_prompt`** with:
   - `artifact_hash`: the `content_hash` from step 2
   - `prompt`: the filled template from step 3
   - `model`: `"google/gemini-2.5-pro"`
   - `label`: `"adapt"`

5. Show the `output` field from the response to the user as the adaptation plan. Don't paraphrase. Surface any error from the MCP tool call directly.

## Assumptions

- The `inst-ai-bot` MCP server is configured in this Claude host (see `skills/README.md` for per-host setup). If the MCP tools aren't available, tell the user — do **not** try to start a server or fall back to anything else.
- `GEMINI_API_KEY` and `GROQ_API_KEY` live in the server's `.env`. Skill-side has no secrets.

## Customizing

- **Different niche framing** (e.g. design humor, B2B SaaS, fitness): edit the "THE CREATOR'S NICHE" block in the prompt template below.
- **Different model**: pass a different `model` to `run_prompt` (e.g. another `google/...` model). Only the `google` provider is wired today.

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
You are a short-form video strategist helping a creator adapt other people's reels into their own niche.

THE CREATOR'S NICHE
Tech lifestyle humor, relatable. Audience: developers, founders, AI/SaaS people, tech-curious.
Tone: self-aware, lightly self-deprecating, observational. Not cringe-tutorial, not motivational.
Recurring premises that already land for them: vibe coding, AI agents doing your job, founder grind vs lifestyle (vacation vs work), devtool absurdities, "me explaining X to my non-technical Y", on-call / PR-review chaos, Claude/Cursor/Copilot behaving like a coworker.

THE SOURCE REEL (what the creator is borrowing from)
Caption: {caption}
Hashtags: {hashtags}
Uploader: {uploader}

TOP COMMENTS (audience signal — what people actually responded to)
{comments_block}

TRANSCRIPT
{transcript}

Now watch the video itself, then deliver the adaptation plan in these sections:

1. **Why this works** — In 2-3 sentences, name the underlying mechanic that makes this reel hit. Is it a setup/payoff joke? A visual contrast (A vs B)? A POV roleplay? A bait-and-switch? A trend audio? A relatable observation? Be precise — name the *format*, not the topic.

2. **The transferable structure** — Strip out the source topic and write the format as a reusable template. Use placeholders. Example: "Hook: [unexpected visual]. Cut to: [contradicting context]. Text overlay: '[setup] vs [reveal]'. Audio: [emotional beat from trending sound]." Make it copyable.

3. **Three tech-niche remixes** — Three concrete adaptations into the creator's niche. For each: one-line premise + the specific tech-world detail that sells the joke. Don't be generic ("about coding") — pick a specific situation (a Cursor autocomplete fail, a standup ritual, a deployed-on-Friday moment). Rank them: best first.

4. **Shoot-ready script for #1** — A shot-by-shot plan for the strongest remix. Include: hook frame (0-1s), on-screen text per beat, dialogue or VO lines, B-roll inserts, the punchline beat, total length target. Tight enough that the creator could film it today.

Be direct. No platitudes. If the source format won't transfer cleanly to the niche, say so in section 1 and propose the closest workable variant instead. Aim for tight, not exhaustive.
```

## What this skill does NOT do

- Doesn't grill or critique the source video. That's `/grill-reel`.
- Doesn't start, restart, or check any server lifecycle. The MCP connector either works or it doesn't — report cleanly and stop.
- Doesn't analyze non-Instagram URLs (TikTok, YouTube). The indexer rejects them.
- Doesn't produce more than one adaptation run per call. Want a different angle? Re-run or edit the prompt.
