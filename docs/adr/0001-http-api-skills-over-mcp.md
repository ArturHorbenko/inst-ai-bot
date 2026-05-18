# HTTP API + skills as primary surface; MCP deferred

The agent-facing surface is a plain HTTP API. Workflow logic ("grill this video", "extract viral format") lives in skill files that call the API — separate from this repo. MCP is not implemented.

The main alternative was going MCP-first, which would have given us auto-discovery and typed tool schemas. We chose against it because: (1) the iteration loop we care about — agent reads a prompt file, edits it, re-runs — is file editing with native agent tools, not an RPC pattern; (2) multi-step workflows carry too much instructional content to fit in MCP tool descriptions; (3) HTTP is universal and MCP can always be added as a thin wrapper later without changing the API contract.
