# Current Creator Profile MCP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Expose an evidence-first creator profile for the connected Instagram account and require MCP clients to read it before creator-specific work.

**Architecture:** The Instagram analytics dashboard remains the authoritative first-party data source. It derives a bounded 30–60 day profile from stored own-content media, analytics observations, validated taxonomy/content traits, and bounded official comment analysis. `inst-ai-bot` only proxies that read-only dashboard endpoint through `get_current_creator_profile(days=60)`.

**Tech Stack:** Next.js route + TypeScript/Vitest/Mongo read repository; Python FastMCP + `httpx`/pytest.

**Privacy and evidence rules:** Only stored first-party dashboard records are used. Trial Reels remain excluded. Sponsorship is reported only where existing taxonomy has explicit `sponsored_partner` evidence; post mentions must not be called sponsorships. Voice/audience results must expose coverage and evidence rather than infer demographics or partnerships.

---

### Task 1: Derive an evidence-first profile in the dashboard

**Objective:** Convert the latest 30–60 days of stored own content into a stable, bounded profile payload.

**Files:**
- Create: `src/lib/meta/creator-profile-model.ts`
- Modify: `src/lib/meta/read-repository.ts`
- Test: `tests/creator-profile-model.test.ts`

**Step 1: Write failing tests**

Test a profile built from fixture media/observations/taxonomy/content traits/comment analysis. Assert:
- window and coverage are returned;
- recurring topics become ranked `pillars`;
- `contentTypes` reports archetype, format, and delivery distributions;
- `voice` reports only observed hook/delivery/CTA patterns;
- `audience` reports bounded stored comment-theme/reaction aggregates and coverage;
- `brands` reports sponsorship evidence separately and does not infer it from an arbitrary caption mention;
- trial Reels are excluded.

**Step 2: Verify RED**

Run: `pnpm vitest run tests/creator-profile-model.test.ts`

Expected: FAIL because the model module/export does not exist.

**Step 3: Implement minimal model**

Implement pure aggregation with deterministic ranking (count descending, label ascending on ties). Use only `trait_extractions` records already validated by the dashboard. Include `sampleSize` and `coverage` for every derived dimension. Return empty arrays/explicit unavailable coverage when evidence is absent.

**Step 4: Add repository method**

Add `creatorProfile(days, now?)` to `createAnalyticsReadRepository`. Query own REELS/FEED posted in the bounded date window, exclude `isTrialReel`, load observations, taxonomy/content traits, and available official comment analyses; pass the records to the pure model.

**Step 5: Verify GREEN**

Run: `pnpm vitest run tests/creator-profile-model.test.ts tests/read-repository.test.ts`

Expected: PASS.

### Task 2: Publish the profile as a protected dashboard MCP-read endpoint

**Objective:** Add `GET /api/internal/mcp/profile?days=30..60` under the existing MCP read-secret authentication model.

**Files:**
- Create: `src/app/api/internal/mcp/profile/route.ts`
- Test: `tests/mcp-profile-route.test.ts`

**Step 1: Write failing route tests**

Assert successful default behavior calls `repository.creatorProfile(60)`, valid `days=30` works, `29`, `61`, decimals, and non-numeric values return 400, and the existing secret checks remain unchanged.

**Step 2: Verify RED**

Run: `pnpm vitest run tests/mcp-profile-route.test.ts`

Expected: FAIL because the route does not exist.

**Step 3: Implement minimal route**

Follow the existing audit/reels endpoint authentication pattern. Default to 60 days; accept only integer days from 30 through 60; return `{ ok: true, profile }`.

**Step 4: Verify GREEN**

Run: `pnpm vitest run tests/mcp-profile-route.test.ts`

Expected: PASS.

### Task 3: Expose the dashboard profile through inst-ai-bot MCP

**Objective:** Make `get_current_creator_profile` the first tool an MCP client uses for creator-specific workflows.

**Files:**
- Modify: `video_processor/dashboard_analytics.py`
- Modify: `video_processor/mcp_server.py`
- Modify: `tests/test_dashboard_analytics.py`
- Create: `tests/test_mcp_creator_profile.py`

**Step 1: Write failing tests**

Assert `DashboardAnalyticsClient.get_current_creator_profile(60)` requests `/api/internal/mcp/profile` with the separate dashboard read secret and `{days: 60}`. Assert the MCP server instructions tell clients to call the profile tool first and the tool delegates `days` to the dashboard client.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_dashboard_analytics.py tests/test_mcp_creator_profile.py -q`

Expected: FAIL because the client/tool do not exist.

**Step 3: Implement minimal proxy/tool**

Add the client method and a read-only `@mcp.tool()` named `get_current_creator_profile(days: int = 60)`. Keep the 30–60 boundary enforced authoritatively by the dashboard route. Update `FastMCP.instructions` to require a call to this tool at the beginning of every creator-specific workflow before subsequent analysis/retrieval/prompt runs.

**Step 4: Verify GREEN**

Run: `python -m pytest tests/test_dashboard_analytics.py tests/test_mcp_creator_profile.py tests/test_mcp_server_auth.py -q`

Expected: PASS.

### Task 4: Integration verification and deployment

**Objective:** Prove both services expose the new contract without exposing secrets.

**Files:**
- Modify only if needed: `skills/README.md` or MCP docstrings.

**Steps:**
1. Run dashboard verification: `pnpm test && pnpm lint && pnpm typecheck && pnpm build`.
2. Run inst-ai-bot tests: `source venv/bin/activate && python -m pytest tests -q`.
3. Build/restart `instagram-analytics-dashboard.service`, verify `GET /api/internal/mcp/profile?days=60` with the configured read secret returns a bounded profile (do not print secret).
4. Restart `inst-ai-bot-mcp.service`, verify its health and make one bounded authenticated MCP tool-list/tool-call check without logging bearer material.
5. Commit each repository separately with focused commit messages.
