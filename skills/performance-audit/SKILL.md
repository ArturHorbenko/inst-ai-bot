---
name: performance-audit
description: Audit the connected creator's Instagram performance, investigate strong and weak posts, and recommend experiments using the current server workflow.
---

# Audit performance

Use the configured `inst-ai-bot` MCP connection for the one creator configured by the server.

1. At the start of each new audit request, call `get_workflow` with `{"name":"performance-audit"}`, even if a guide was fetched earlier in the conversation.
2. Read the returned `instructions` and complete that workflow within the user's request. Follow the current guide for tool selection, evidence, and deliverables; do not use a remembered procedure.
3. If the tool or guide is unavailable, explain that the MCP connection must expose `get_workflow` and be refreshed. Do not invent a replacement guide or start services.

The server owns the detailed workflow. This skill contains no prompt template or credentials.
