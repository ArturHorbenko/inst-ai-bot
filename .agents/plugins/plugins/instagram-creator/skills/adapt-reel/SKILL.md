---
name: adapt-reel
description: Adapt an Instagram Reel's format for the one creator configured by the inst-ai-bot MCP server. Use for remix ideas and shoot-ready scripts.
---

# Adapt a Reel

Use the `inst-ai-bot` MCP tools to turn a source Instagram Reel into a plan for
the one creator configured by the server.

## Workflow

1. Require an Instagram Reel URL. If none was supplied, ask for it.
2. Call `inst-ai-bot.get_current_creator_profile` with `{"days": 60}`. Keep the
   complete result as the evidence-based creator context.
3. Call `inst-ai-bot.index_video_from_url` with the supplied URL. Keep its
   `content_hash`, transcript, caption, hashtags, uploader, and comments.
4. Format comments as a numbered list with author, like count, and text.
   Serialize the creator profile as readable JSON.
5. Call `inst-ai-bot.run_prompt` with the indexed `content_hash` as
   `artifact_hash`, `google/gemini-2.5-pro` as `model`, `adapt` as `label`, and
   the prompt below after replacing every placeholder.
6. Return the tool's `output` without paraphrasing. Surface tool errors clearly.

If any MCP tool is unavailable, tell the user that the `inst-ai-bot` MCP
connection must be configured. Do not try to start or repair the server. Never
ask for or expose connector credentials.

## Prompt

```text
You are a short-form video strategist helping a creator adapt another person's Reel into their own voice.

CURRENT EVIDENCE-BASED CREATOR PROFILE
{creator_profile}

SOURCE REEL
Caption: {caption}
Hashtags: {hashtags}
Uploader: {uploader}

TOP COMMENTS
{comments_block}

TRANSCRIPT
{transcript}

Watch the video itself, then provide:

1. Why this works — identify the precise format or mechanic in 2–3 sentences.
2. Transferable structure — rewrite it as a reusable template with placeholders.
3. Three creator-specific remixes — concrete ideas grounded in the creator profile, ranked best first.
4. Shoot-ready script for #1 — hook frame, on-screen text, dialogue or voiceover, B-roll, punchline, and target length.

Be direct and specific. If the source format does not transfer cleanly, say so and propose the closest workable variant.
```

Use `(none)` for missing comments or hashtags, `(no caption)` for a missing
caption, and `(unknown)` for a missing uploader. This skill supports Instagram
URLs only and performs one adaptation run per request.
