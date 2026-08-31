---
name: grill-reel
description: Critique an Instagram Reel for the one creator configured by the inst-ai-bot MCP server. Use for direct hook, pacing, and audience feedback.
---

# Grill a Reel

Use the `inst-ai-bot` MCP tools to critique a Reel. It operates on the one creator configured by the server.

## Workflow

1. Require an Instagram Reel URL. If none was supplied, ask for it.
2. Call `inst-ai-bot.get_current_creator_profile` with `{"days": 60}`. Keep the
   complete result as the evidence-based creator context.
3. Call `inst-ai-bot.index_video_from_url` with the supplied URL. Keep its
   `content_hash`, transcript, caption, hashtags, uploader, and comments.
4. Format comments as a numbered list with author, like count, and text.
   Serialize the creator profile as readable JSON.
5. Call `inst-ai-bot.run_prompt` with the indexed `content_hash` as
   `artifact_hash`, `google/gemini-2.5-pro` as `model`, `grill` as `label`, and
   the prompt below after replacing every placeholder.
6. Return the tool's `output` without paraphrasing. Surface tool errors clearly.

If any MCP tool is unavailable, tell the user that the `inst-ai-bot` MCP
connection must be configured. Do not try to start or repair the server. Never
ask for or expose connector credentials.

## Prompt

```text
You are reviewing an Instagram Reel as a senior short-form video creator. Tell this creator directly and specifically what to improve next time.

CURRENT EVIDENCE-BASED CREATOR PROFILE
{creator_profile}

POST CONTEXT
Caption: {caption}
Hashtags: {hashtags}
Uploader: {uploader}

TOP COMMENTS
{comments_block}

TRANSCRIPT
{transcript}

Watch the video itself, then provide:

1. Hook (0–3s) — whether the opener earns the watch and the exact frame, line, or cut to change.
2. Pacing and retention — likely drop-off points, dragging beats, and beats that land.
3. Audience signal — what comments reveal about comprehension and response.
4. Improve list — 3–5 concrete edits for the next Reel.

Be direct and concise. Acknowledge genuine strengths briefly, then move on. Avoid generic advice.
```

Use `(none)` for missing comments or hashtags, `(no caption)` for a missing
caption, and `(unknown)` for a missing uploader. This skill supports Instagram
URLs only and performs one critique run per request.
