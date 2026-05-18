# Prompts are opaque to the server

`POST /runs` takes a prompt as a plain string. The server has no prompt registry, no named prompts, no templating engine. Prompt management — storage, versioning, variable interpolation — is entirely the caller's responsibility (skill files, agent working directory, git).

The alternative was a server-side prompt registry with named prompts and server-side templating. We declined because: (1) prompts belong with the skills that use them, not with the HTTP server — same lifecycle, same repo, same iteration loop; (2) the agent's native file tools (Read/Edit) are a better prompt editor than any RPC we'd build; (3) server-side templating is a feature to build, maintain, and migrate, while caller-side string assembly is free. A registry can be added as a separate service later without touching the server contract.
