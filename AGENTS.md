# OpenNolan

**MANDATORY: Read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) before responding to ANY user message (if the user has asked to create a video).**
**MANDATORY: Read [`RULES.md`](RULES.md) before responding to ANY user message (if you are claude code coding agent).**

Do not act on the user's request until you have read AGENT_GUIDE.md.
It contains routing rules that determine your first action based on what the user asked.
Skipping it WILL cause you to take the wrong action.

All other OpenNolan production instructions are in AGENT_GUIDE.md & RULES.md.

## GBrain development memory

GBrain is the shared development memory for this repository. When its MCP server
is available, use it for durable product and engineering context:

1. Before answering questions or proposing changes about prior product decisions,
   architecture, tradeoffs, or project history, search GBrain first.
2. Do not ask the user to repeat information that may already be in GBrain.
3. After a meaningful decision, write a concise GBrain page with the decision,
   rationale, alternatives considered, affected areas, and unresolved follow-ups.
4. When using prior context, name the GBrain page that supports it. Treat retrieved
   context as evidence, not as permission to make an unapproved change.

GBrain is local to the developer machine. Its MCP configuration contains no
credentials and is safe to keep in this public repository; the actual memory data
stays outside this repository in the local GBrain store.


<!-- gbrain:retrieval-reflex:resolver-rows -->
- retrieval-reflex | a named person/company/project/place becomes the subject; a brain-page pointer appears in context; "who is", "what do we know about", "tell me about"; about to assert a non-trivial detail about a named entity
<!-- /gbrain:retrieval-reflex:resolver-rows -->
