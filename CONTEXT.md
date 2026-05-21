# inst-ai-bot

An HTTP service that turns videos into queryable Artifacts and runs prompts against them. The repo is one HTTP server; agent-facing workflows (skills, MCP wrappers) live elsewhere and call this server.

## Language

**Artifact**:
The indexed representation of a single video — the unit of input to every prompt run. Identified by content hash, contains transcript + sources + a reference to the video bytes.
_Avoid_: Job, record, document

**Content hash**:
The SHA-256 of the canonical video bytes. The primary identity for an Artifact. Same bytes from any input path produce the same hash and resolve to the same Artifact.
_Avoid_: Video ID, fingerprint

**Source**:
One place the video bytes were obtained from — a URL fetch (Instagram, TikTok, YouTube, …) or a direct upload. An Artifact has one or more Sources; new ones are appended over time, never overwritten. Each Source carries provenance (`type`, `url`, `fetched_at`, `fetcher`) and Metadata.
_Avoid_: Origin, reference

**Metadata**:
Freeform JSON attached to a Source — caption, comments, author, hashtags, user notes. Shape is platform-dependent and not schema-enforced; prompts opt in to what they want by reading `sources[i].metadata.<field>`.
_Avoid_: Context (overloaded with prompt-context), tags

**Index** (verb):
The act of turning a video input into an Artifact: download (if URL) → content-hash → transcribe → write Artifact. Index is fast and deterministic; per-prompt visual analysis happens later at run time, not here.

**Run** (verb / noun):
Executing a prompt against an Artifact with a chosen model, producing an output. As a noun: the stored record `(artifact, prompt, model, output, timestamp)`. Runs are the unit the web UI displays.
_Avoid_: Analysis, query, request

**Insights snapshot**:
A point-in-time capture of one reel's Instagram performance — views, reach, likes, comments, saved, shares, total interactions, watch time — pulled from the Instagram Graph API. Keyed by Instagram `media_id` + `fetched_at`; append-only, never overwritten. Mutable and account-scoped — the deliberate counterpoint to the immutable, content-addressed Artifact (see `docs/adr/0004`). Only available for reels on the operator's own Professional account.
_Avoid_: Analytics, Stats. (Use "metrics" only for the numbers inside a snapshot.)

## Relationships

- An **Artifact** has exactly one **Content hash** (its identity)
- An **Artifact** has one or more **Sources**
- A **Source** has one **Metadata** blob (possibly empty)
- A **Run** targets exactly one **Artifact** with exactly one prompt + model
- Pasting the same URL twice resolves to the same **Artifact**; a fresh **Source** entry may be appended if content (caption/comments) has changed
- An **Insights snapshot** belongs to an Instagram `media_id`, not to an **Artifact** — the two are correlated by reel URL, never linked by a stored reference

## Flagged ambiguities

- The current code's `analyses` enum mixes "which model/backend" (`gemini`, `multimodal`=TwelveLabs) with "what you're asking it to do" (`format_extraction`, `structured`). In the new model these are two orthogonal axes: a **Run** picks a prompt AND a model independently.
