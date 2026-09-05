# Audit Instagram content performance

Produce an evidence-backed audit for the one creator configured by the server,
then recommend three prioritized content experiments. Run the process
automatically using stored data. An audit does not require indexing videos,
refreshing Meta data, or starting paid video-model Runs.

## Establish scope and coverage

1. Use the user's requested period, defaulting to 30 days. `content_audit` accepts
   a rolling 1–365 day window, not arbitrary historical date ranges. Do not silently
   substitute a rolling window for an unsupported range; explain the limitation
   and ask for a supported period when necessary.
2. Call `get_current_creator_profile(days=60)` for niche, voice, audience, and
   profile coverage, then `content_audit(days=<audit period>)` for the audit data.
   These are distinct windows; a 60-day profile is context, not the audit sample.
3. Inspect returned `window`, `coverage`, content counts, publication dates,
   metric availability, and trait coverage before interpreting results. Summary
   rows in `content` are compact: their identifier is `mediaId`. Observation
   dates and detailed metadata are in `leaders` and the detail calls below;
   do not assume sample-wide freshness from a single leader. The audit concerns
   posts published in the window and their stored
   performance; it is not automatically account activity earned within that window.
   Trial Reels are excluded. Archived content may be included: use its stored
   observations, noting stale coverage when material. Use `content` as the sample;
   do not double-count the compatibility `reels` field.
   If the sample is empty, report it and the next useful data-collection step.
   Do not fabricate findings or three evidence-backed experiments from no evidence.

## Compare and investigate

4. Use the returned personal `medians`, `leaders`, and `byFormat` comparisons.
   Relate views, share rate, and save rate to the user's goal when supplied;
   otherwise explain reach and audience response separately. `shareRate` is
   shares/reach and `saveRate` is saved/reach; both are ratios, so multiply by 100
   only when formatting percentages. Missing or unavailable values are not zero.
   Overall medians mix Reels and Feed posts; label that baseline and use available
   `byFormat` groups for format-specific comparisons. If a comparable subgroup
   baseline is unavailable, say so rather than describing an overall median as one.
   Compare like formats and similar post ages where supported. If matched-age
   observations are unavailable, label lifetime comparisons as age-confounded;
   do not claim normalization. Avoid industry benchmarks, causal claims, and
   statistical confidence unsupported by the sample.
5. Select a bounded, representative set of strong and weak performers (normally
   2–3 of each, fewer for small samples), using valid comparable metrics. Deduplicate
   posts that lead multiple metrics and keep immature posts out of failure claims.
   Call `get_content_analytics(media_id, days)` for their metrics, traits, and
   available comment evidence. This detail tool supports 1–90 days: cap its window
   at 90 for longer audits and disclose the shorter history where relevant.
6. Inspect returned taxonomy, content traits, retrieval traits, and comment
   response to connect performance to actual content. Detail results identify
   the post as `media.id`; they do not supply an Artifact hash. When more video
   evidence is needed, use `search_videos` with a distinctive caption or topic,
   verify that a result's `media_id` matches the selected post, then call
   `get_video_context` with that result's `content_hash` and `media_id`. Never
   substitute a media ID for an Artifact hash. If no matching stored result
   exists, use the available traits/caption and state the limitation.
   Search similarity scores are not performance metrics. Examples outside the
   audit sample may be context but must not be counted in its findings.
   Use stored transcript and timestamped descriptions with their provenance;
   do not claim to have watched the video. For Feed posts, use the available
   post traits and captions rather than assuming a video exists.

## Deliver

7. Present:
   - The period, sample size, freshness, and material coverage limitations.
   - The strongest supported findings, each with concrete posts, metrics and
     comparable baselines. Link returned permalinks or identify media IDs when
     links are absent. Separate observed patterns from hypotheses about causes.
   - Three prioritized experiments when the evidence supports them. For each,
     explain the hypothesis, specific creative change, metric to watch, and a
     comparison plan using similar formats and observation ages. With sparse
     evidence, label exploratory ideas explicitly and recommend fewer if warranted.
   Keep the recommendations specific to this creator's evidence and constraints.
   Avoid unsupported retention/drop-off claims from views or transcripts alone.
   Do not infer an improvement or decline against a prior period unless comparable
   prior-period data is actually available.

If the creator profile is unavailable but analytics work, complete a performance
audit with limited personalization. If detailed evidence is unavailable, report
the quantitative findings and identify the unanswered content questions. If the
audit data itself is unavailable, report the blocker; do not invent performance.
