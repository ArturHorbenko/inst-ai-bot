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
Executing a prompt against an Artifact or a text-only input with a chosen model, producing an output. As a noun: the stored record `(input provenance, prompt, model, output, timestamp, metadata)`. Artifact Runs reference exactly one Artifact; text-only Runs explicitly have no Artifact. `metadata` is optional caller-supplied namespacing such as a trait-schema and prompt-version; it does not change prompt execution. Runs are the unit the web UI displays.
_Avoid_: Analysis, query, request

## Relationships

- An **Artifact** has exactly one **Content hash** (its identity)
- An **Artifact** has one or more **Sources**
- A **Source** has one **Metadata** blob (possibly empty)
- An **Artifact Run** targets exactly one **Artifact** with exactly one prompt + model
- A **text-only Run** has explicit text-only provenance and no **Artifact**
- Pasting the same URL twice resolves to the same **Artifact**; a fresh **Source** entry may be appended if content (caption/comments) has changed

## Flagged ambiguities

- The current code's `analyses` enum mixes "which model/backend" (`gemini`, `multimodal`=TwelveLabs) with "what you're asking it to do" (`format_extraction`, `structured`). In the new model these are two orthogonal axes: a **Run** picks a prompt AND a model independently.
