You have access to Swiggy Builders Club docs - the authoritative source
for Swiggy MCP (Food, Instamart, Dineout). Always consult these before
writing Swiggy code:
 
- Index:      https://mcp.swiggy.com/builders/llms.txt
- Full text:  https://mcp.swiggy.com/builders/llms-full.txt
- Per-page:   append `.md` to any https://mcp.swiggy.com/builders/docs/... URL
 
Tool schemas live under `/docs/reference/{food,instamart,dineout}`.
Error codes live at `/docs/reference/errors`. Auth flow is at
`/docs/start/authenticate`.
 
Rules:
1. Before recommending a tool name, parameter, error code, rate limit,
   or auth flow, fetch the relevant doc and verify.
2. Never invent tool names or parameters. If the docs don't cover it,
   say so and ask.
3. Prefer `.md` page fetches over `llms-full.txt` when you know the exact area - it's cheaper on context.
4. Write all code in Python. This is a Python-only project - do not
   introduce JavaScript, TypeScript, or any other language, and do not
   suggest tooling that requires them.

Local verified reality: `INSTAMART_NOTES.md` records what the Instamart MCP
actually did against a live account on 2026-08-24 - including the places the
docs are wrong, the assumptions that broke, and which fields can be trusted.
Read it alongside the docs before writing Instamart code. Where it contradicts
the docs on a specific (tool count, parameter names, payment ids, fee
behaviour), it was observed and the docs were not - but re-verify anything
load-bearing, since it is a snapshot of one account on one day.
