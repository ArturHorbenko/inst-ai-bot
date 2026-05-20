# Instagram insights are mutable, account-scoped snapshots

Reel performance data (views, reach, engagement, watch time) is pulled from the
Instagram Graph API and stored as append-only snapshots in a separate `insights`
collection — one document per fetch, keyed by `media_id` + `fetched_at`. Insights are
deliberately **not** placed on the Artifact and **not** content-addressed. The
`get_reel_insights` primitive takes only a reel URL; the reel does not have to be
indexed, and snapshots carry no `content_hash` link.

This is a recognised departure from the rest of the system. An Artifact is immutable,
identified by the SHA-256 of its bytes, and idempotent — re-indexing the same input
returns the same Artifact. Insights are the opposite: the numbers change every hour,
they belong to a *post* (a `media_id`) rather than to *bytes*, and they are scoped to
the operator's own Professional account — there is no insights data for arbitrary
public reels. Folding mutable, time-varying, identity-scoped data into the
content-addressed Artifact would break its core invariant.

The alternative was to fetch live and not persist — always return the current numbers,
no new collection. We chose snapshots because the motivating use case is "why did this
reel underperform", and decay/growth over time is itself a signal: a snapshot history
lets a caller compare a reel against its past self. The cost is one extra collection
and a token-refresh path; the Graph token is long-lived (~60 days, refreshable) and the
server refreshes it in place in a `meta_credentials` document so `.env` is never
rewritten.
