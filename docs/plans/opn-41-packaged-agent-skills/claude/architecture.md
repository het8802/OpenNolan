# OPN-41 — Packaged app agent loads zero SDK skills

**Status: PLAN**

Linear: [OPN-41](https://linear.app/hettikawala/issue/OPN-41/packaged-app-agent-loads-zero-sdk-skills-claudeskills-not-shipped)

---

## 1. What is actually broken

The packaged Mac app's agent has **zero** skills reachable through the `Skill`
tool. In dev it has 56. Two independent causes:

**Cause A — the SDK only looks in one place, and we don't ship it.**
`build_agent_options` passes `setting_sources=["project"]`
(`server/agent_runner.py:655`) with `cwd=repo_root`
(`server/agent_runner.py:650`). The `project` setting source makes the CLI
discover skills at `<cwd>/.claude/skills/*/SKILL.md`.

- Dev: `cwd` = repo root, which has `.claude/skills/` → 56 skills.
- Packaged: `cwd` = `Resources/backend`, and `.claude/` is **not** in
  `extraResources` (`desktop/package.json:20-70`) → 0 skills.

`.agents/skills/` *is* shipped (`desktop/package.json:47`) — but the SDK never
looks there, so those 78 skills are only reachable if the agent hand-reads a
path it was told about in prose.

**Cause B — the two skill folders are the same pile, copied.**

```
.claude/skills/    56 dirs   tracked (450 files)   NOT shipped   SDK discovery
.agents/skills/    78 dirs   tracked (526 files)  shipped      prose pointers
```

`.claude/skills/` is a strict, **byte-identical** subset of `.agents/skills/`
— 55 of its 56 dirs exist verbatim in `.agents/skills/` (verified with `cmp`);
the single exception is `explain-with-html`. `.claude/` contains *nothing else*
— no `settings.json`, no agents, no commands. It exists solely as a copy made
so the SDK would see the video skills.

So the "dev skills vs app skills" framing needs correcting: **neither folder is
a dev-skill home today.** Both are the same mixed pile, and `karpathy-guidelines`
(a dev skill) is currently served to the video agent from both.

---

## 2. Your two SDK questions, answered against the installed SDK

Verified against `claude-agent-sdk` **0.2.128**
(`.venv/lib/python3.12/site-packages/claude_agent_sdk/`) and confirmed by
running the CLI, not from memory.

### Q1: Is there a parameter for initializing skills? Paths, or a MANIFEST file?

Yes — `ClaudeAgentOptions.skills` exists (`types.py:1999`). But it is
**names-only, and it is a filter, not a loader**:

```python
skills: list[str] | Literal["all"] | None = None
```

- `"all"` → enable every **discovered** skill.
- `list[str]` → names matching the SKILL.md `name` / directory name, or
  `plugin:skill` for plugin-qualified ones.

> "This is a **context filter**, not a sandbox: unlisted skills are hidden from
> the model's listing and rejected by the Skill tool, but their files remain on
> disk." — `types.py:2013`

So: **no paths, and no MANIFEST.md.** You cannot point `skills=` at a directory
or a description file. It only narrows a set that was already discovered
somewhere else. Setting it also auto-wires the `Skill` tool and defaults
`setting_sources` to `["user", "project"]`
(`_internal/transport/subprocess_cli.py:421-457`) — you don't add `"Skill"` to
`allowed_tools` yourself.

**What actually lands in the agent's context** (measured, CLI 2.1.220 — see
§2a): the `name` + `description` frontmatter of each discovered skill, about
**13 tokens per skill**. The SKILL.md **body is not loaded** until the model
invokes the skill. So "78 skills available" costs ~1k tokens, not 78 files.

**Discovery** — the part that actually loads files — has exactly two levers:

| Lever | Mechanism | Reaches |
|---|---|---|
| `setting_sources=["project"]` | CLI scans `<cwd>/.claude/skills/` | repo root only |
| `plugins=[{"type":"local","path":P}]` | CLI flag `--plugin-dir P` | **any directory** |

`plugins` is the one that takes a path (`subprocess_cli.py:602-608`).

### Q2: You want real skill architecture, not a hard-coded prose pointer.

Agreed, and it's available. The **local plugin** is the native mechanism — the
same one every installed plugin here uses (`ponytail`, `understand-anything`,
`andrej-karpathy-skills`). A plugin root is just:

```
<plugin-root>/
├── .claude-plugin/plugin.json     {"name": ..., "description": ...}
└── skills/<skill-name>/SKILL.md
```

`.agents/` already has `skills/` and nothing else — **it is one `plugin.json`
away from being a valid plugin root.**

**Empirically verified** (canary plugin, `--setting-sources ""`, cwd with no
project settings):

```
$ claude -p "list every skill available via the Skill tool" \
    --setting-sources "" --plugin-dir <canary> --allowed-tools Skill
opn-canary:zzz-canary-skill      <-- plugin skill found with settings OFF
dataviz, update-config, run, init, review, security-review, ...
```

Two results that shape the design:

1. Plugin skills load **with filesystem settings fully disabled**. That is what
   makes clean separation possible: the app agent can have its own skills and
   still be blind to the repo's `.claude/skills/` and dev `CLAUDE.md`.
2. ~12 **CLI-bundled** skills (`run`, `init`, `review`, `security-review`, …)
   appear regardless. See §6.

---

## 2a. What `skills=` does mechanically — measured

Three questions, three experiments. All run against CLI **2.1.220** with
`--setting-sources ""` from an empty cwd, so nothing else could contribute.

**(1) Is it a path? — No.** `skills=` never touches the filesystem. Paths come
only from `setting_sources` (scans `<cwd>/.claude/skills/`) and `plugins`
(`--plugin-dir`). If a skill was not discovered by one of those two, naming it
in `skills=` does nothing at all.

**(2) Is the metadata preloaded, or is it just a pointer? — Metadata is
preloaded; bodies are not.** Canary skill with a distinctive description and a
unique token `MAGICWORD-7731` buried in the SKILL.md body:

```
prompt: "Do NOT use any tool. LINE1: description of skill canary-alpha.
         LINE2: does MAGICWORD-7731 appear in your context? YES/NO"

LINE1: Reports the orbital alignment of the moons of Jupiter for a given date.
LINE2: NO
```

It knew the **description** without invoking anything → metadata is in the
system prompt. It could not see the **body** → the file is read only when the
model calls `Skill(name)`. This is progressive disclosure: the agent gets a
menu, not the meals.

Cost of the menu, measured on the real 78 app skills (symlinked as a plugin):

```
baseline, no plugin :  24,446 tokens
+ 78 app skills     :  25,460 tokens
                       ---------------
                       +1,014  ==  ~13 tokens/skill
```

**(3) Does the list form prune context? — No. It only gates invocation.**
The docstring claims unlisted skills are "hidden from the model's listing"
(`types.py:2013`). The installed CLI does not do that:

```
78 skills, no filter          : 25,460 tokens
78 skills, filter = 1 name    : 25,460 tokens   <-- identical
  skills still listed by model: 76
```

`skills=[...]` compiles to `--allowed-tools "Skill(name)"`
(`subprocess_cli.py:441-455`), which is a **permission** pattern. Unlisted
skills stay in the listing and stay invocable — they just route through
`can_use_tool` (`agent_runner.py:656`) instead of being auto-allowed.

**Consequence for this plan:** `skills="all"` and `skills=[75 names]` produce
an identical context. The list form buys nothing here, so use `"all"` and skip
the name-generation code entirely. **The separation comes from
`setting_sources=[]` + the plugin path — not from `skills=`.** `skills=` is
only there to switch the `Skill` tool on.

---

## 3. Design

One source of truth per audience, each discovered natively.

```
BEFORE                              AFTER

repo/                               repo/
├── .claude/skills/  56  ─┐         ├── .agents/
│     (copy, not shipped)  │ same   │   ├── skills/          ~7  DEV only
├── .agents/skills/  78  ─┘ files   │   │     ^ Codex native (see §3b)
│     (shipped, prose-only)         │   └── app/             PLUGIN ROOT
└── skills/         Layer 2        │       ├── .claude-plugin/plugin.json NEW
      (unchanged)                   │       └── skills/     ~71  APP only
                                    │             ^ app agent via --plugin-dir
                                    ├── .claude/skills/
                                    │     symlinks -> ../../.agents/skills/*
                                    │     ^ Claude Code native (see §3b)
                                    └── skills/             Layer 2, unchanged
```

Agent wiring — **identical in dev and packaged**, which is the point:

```python
# server/agent_runner.py:649
return ClaudeAgentOptions(
    cwd=str(repo_root),
    setting_sources=[],                                   # was ["project"]
    plugins=[{"type": "local", "path": str(repo_root / ".agents" / "app")}],
    skills="all",
    ...
)
```

Why `setting_sources=[]` rather than keeping `["project"]`:

- It is what stops the leak. With `["project"]`, the dev-mode app agent (cwd =
  repo root) picks up the repo's `.claude/skills/` **and** the dev `CLAUDE.md`
  — the exact cross-contamination this issue is about.
- Packaged already loads no `CLAUDE.md` (it isn't in `extraResources`; only
  `AGENT_GUIDE.md` and `PROJECT_CONTEXT.md` are), so the comment on
  `agent_runner.py:655` is already stale in production. Dropping the source
  makes dev match packaged instead of the reverse.
- The app contract reaches the agent through `AGENT_SYSTEM_PROMPT`
  (`server/agent_runner.py:443`), which already says "Read AGENT_GUIDE.md
  before acting" — not through `CLAUDE.md`.

**Checked — nothing else rides on that setting source.** `setting_sources=[]`
would also drop a project `.mcp.json` and `.claude/settings.json` permission
rules. Neither exists in this repo (verified: no `.mcp.json` at root, no
`.claude/settings*.json`; `.claude/` holds only `skills`). MCP servers are
passed explicitly via `mcp_servers=` (`agent_runner.py:658`).

---

## 3b. Dev skills reach BOTH agents natively — measured folder grid

> **Correction.** An earlier draft of this section claimed Codex cannot
> discover skills inside a repo, and recommended keeping dev skills as a prose
> pointer in `CLAUDE.md`/`AGENTS.md`. **That was wrong.** It came from reading
> strings in the Codex binary, all of which mention `$CODEX_HOME/skills`.
> Running `codex debug prompt-input` — which renders the real model-visible
> prompt with no model call — shows Codex already loading this repo's skills.
> The prose-pointer option is dead; native discovery works in both agents.

Evidence, from `codex debug prompt-input` run at the repo root:

```
### Skill roots
  r6 = <this repo>/.agents/skills          <-- repo-relative, no config entry
### Available skills
  - acestep: AI music generation with ACE-Step 1.5 - background ...
  - ai-video-gen: Generate AI videos from text prompts using mult...
  ... 76 of this repo's skills, name + description, already loaded
```

Canary folders in a scratch directory, one per candidate location:

```
folder in the repo          Claude Code   Codex
--------------------------------------------------
.claude/skills/                 YES        no
.agents/skills/                 no         YES
.codex/skills/                  no         YES
.agents/dev-skills/             no         no     <-- name must be "skills"
```

Two conclusions:

1. **The duplication was not sloppiness.** `.agents/skills/` is the Codex
   convention; `.claude/skills/` is the Claude Code convention. One folder per
   agent, same content. Deleting `.claude/skills/` outright — as §2 originally
   implied — would cut Claude Code's native discovery.
2. **No single folder is read by both.** But Claude Code **does follow
   symlinks** for project skills (verified: canary at
   `.claude/skills/sym-canary` → elsewhere → listed). So one real directory
   plus symlinks gives native discovery in both, with no copies and no prose.

### The design

```
.agents/skills/<name>/SKILL.md         real files    -> Codex, native
.claude/skills/<name>  --symlink-->    ../../.agents/skills/<name>
                                                     -> Claude Code, native
```

Both agents get `name` + `description` in context and choose for themselves —
the actual skill mechanism, which is what a `CLAUDE.md` pointer could never be
(a pointer forces the agent to open the file just to learn what it is for).

**The consequence that drives Phase 1:** if `.agents/skills/` becomes the dev
home, the ~71 app skills must **move out of it**, or Codex keeps seeing all of
them — the exact leak this issue is about. They move to `.agents/app/skills/`,
which is invisible to Codex (proven by the `.agents/dev-skills` canary: Codex
matches the folder name `skills` exactly and does not glob `.agents/*/skills`)
and invisible to Claude Code. The app agent reaches them only through
`--plugin-dir`.

Net effect — every audience gets exactly its own set, natively, stored once:

```
                      .agents/
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
     skills/  ~7 dev              app/skills/  ~71 app
          │                             │
    ┌─────┴─────┐                       ▼
    ▼           ▼                 ┌─ app agent ─────────┐
 Codex      Claude Code           │ plugin root only    │
 native     via .claude/skills    │ setting_sources=[]  │
            symlinks              │ so dev is unreachable│
                                  └─────────────────────┘
```

`setting_sources=[]` is what keeps the app agent out of `.claude/skills/` — so
even on a dev machine, where its cwd *is* the repo root, it cannot see dev
skills.

---

## 4. Implementation

### Phase 1 — App skills become a plugin (fixes the zero-skills bug)

Phase 1 lands the **final** layout so nothing gets wired twice.

1. `git mv` the ~71 app skills from `.agents/skills/` to `.agents/app/skills/`.
   Mechanical, no file edits. This is also what stops Codex from seeing them.
2. Add `.agents/app/.claude-plugin/plugin.json`:
   ```json
   {"name": "opennolan", "description": "OpenNolan video production skills"}
   ```
3. `desktop/package.json:47` — change `../.agents/skills` →
   `../.agents/app` so the marker ships alongside the skills.
4. `server/agent_runner.py:649` — add `plugins=` (pointing at
   `.agents/app`), add `skills="all"`, change `setting_sources` to `[]`.
5. `AGENT_GUIDE.md` — the `.agents/skills/` paths at `:59`, `:711`, `:731`
   now point at a folder that holds dev skills. Repoint or, better, convert to
   "invoke the skill" per Phase 3 step 8.

**Success condition:** in a packaged build, the agent's skill listing is
non-empty and contains `opennolan:ffmpeg`. Test both legs:

```
dev:      count of opennolan:* == count of .agents/app/skills/*/SKILL.md
packaged:  same count (currently 0)
```

Phase 1 alone closes the issue. Phases 2–3 are the "one source of truth" half.

### Phase 2 — Kill the duplicate

6. `git rm -r` the 55 duplicate dirs in `.claude/skills/` — byte-identical
   copies of skills now living in `.agents/app/skills/`, and unreachable by the
   app agent by design. Keep `explain-with-html` (unique) as a dev skill.

**Success condition:** no skill's files exist in two places.

### Phase 3 — Dev skills into place

7. Dev set — **descriptions read from each `SKILL.md`, not guessed from folder
   names** (that error is recorded below):

   | Skill | Why dev |
   |---|---|
   | `karpathy-guidelines` | coding discipline; CLAUDE.md + AGENTS.md mandate it |
   | `ponytail` | simplest-solution discipline; added by Het as a dev skill |
   | `emil-design-eng` | UI polish / component design; added by Het as dev |
   | `explain-with-html` | authoring docs for humans, not video |
   | `vercel-composition-patterns` | *"refactoring components with boolean prop proliferation"* |
   | `vercel-react-best-practices` | *"React and Next.js performance optimization"* |
   | `web-design-guidelines` | *"Review UI code … 'review my UI'"* |

   `ponytail` and `emil-design-eng` exist today as untracked folders in
   `.claude/skills/` and must be **committed** into `.agents/skills/`.

   **Corrected:** an earlier draft listed `agents` as dev, reading the folder
   name as "agent configuration". Its actual description is *"Build voice AI
   agents with ElevenLabs"* — an app skill. It stays with the app set.

   **Open coin-flip:** `tailwind-design-system` — Tailwind is used both in the
   editor UI (`web/src`) and in HyperFrames compositions. Currently on the app
   side. If both need it, symlink rather than copy.

8. Home them: real files in `.agents/skills/` (Codex native), plus a checked-in
   symlink per skill at `.claude/skills/<name>` → `../../.agents/skills/<name>`
   (Claude Code native). No prose pointer, no copies.

9. `CLAUDE.md:17` **and** `AGENTS.md:17` both point at
   `.agents/skills/karpathy-guidelines/SKILL.md`. The path still resolves after
   Phase 3, so no edit is strictly required — but the "read this file"
   framing should become "use the skill", since both agents now discover it.
   Update **both** files; touching only `CLAUDE.md` is how Codex silently drifts.
   (`CODEX.md` delegates to `AGENT_GUIDE.md` and needs no change.)

10. `AGENT_GUIDE.md` — Layer 3 is now discovered, not read by path. The
    `.agents/skills/` pointers at `:59`, `:711`, `:731` should say "invoke the
    skill". Keep the category table; it is useful routing and stops being a
    *substitute* for discovery once discovery works.

**Success conditions** — all three checkable with no model call except the last:

```
codex debug prompt-input | grep 'r_ = .*/.agents/skills'
    -> lists the ~7 dev skills, zero app skills
claude -p 'list your skills' at repo root
    -> lists the ~7 dev skills (via symlinks), zero opennolan:*
app agent in the packaged build
    -> ~71 opennolan:* skills, zero dev skills
```

---

## 5. Deliberately NOT doing

- **Not touching `skills/`** (Layer 2 pipeline/director skills). Different
  shape — stage directors read by explicit path at a known point in the
  pipeline, not autonomously selected. Converting them to SDK skills is a
  separate question.
- **Not writing a MANIFEST.md.** The SDK cannot consume one (§2), and the
  skill's own frontmatter `description` already *is* the manifest — that is the
  string the agent selects on (§2a).
- **Not passing an explicit `skills=[...]` list.** Measured identical to
  `"all"` in both context and token cost (§2a-3), so the list is pure
  maintenance burden for zero effect.
- **Not symlinking the *app* skills into `.claude/skills`.** The dev-skill
  symlinks (§3b) are fine — they are repo-local and never packaged. App skills
  are different: they ship inside the `.app`, and electron-builder + code
  signing + notarization treat symlinks inconsistently. App skills stay real
  files reached by `--plugin-dir`.
- **Not deduping against the globally-installed `andrej-karpathy-skills`
  plugin**, which already provides `karpathy-guidelines` on this machine. That
  is the user's env, not the repo's.
- **Not repointing `CODEX_HOME` per-repo.** Unnecessary now that Codex reads
  `<cwd>/.agents/skills` (§3b), and it also holds `auth.json`, `config.toml`,
  and session history — moving it per worktree would break auth for nothing.
- **Not using `[[skills.config]]` in Codex config.** It takes `enabled` plus
  either a `path` or `name` selector, but it configures skills that were already
  discovered; it is not a discovery root, and it lives in global config rather
  than the repo.

---

## 6. Known residual

The ~12 CLI-bundled skills (`run`, `init`, `review`, `security-review`,
`update-config`, `dataviz`, …) remain in the app agent's listing even with
`setting_sources=[]`. Per §2a(3) the name filter cannot remove them — it only
gates invocation, so the model sees them but calling one routes through
`can_use_tool` (`server/agent_runner.py:656`). Cost is ~150 tokens.

These ship with Claude Code itself, are present in every SDK session today, and
are not repo dev skills — so this issue does not regress because of them. If
their presence in the video agent's context is judged harmful, the lever is
`disallowed_tools` (already plumbed at `agent_runner.py:659`), as a follow-up.

---

## 7. What would change my mind

- **A `.mcp.json` or `.claude/settings.json` gets added before this lands**
  (neither exists today) → keep `setting_sources=["project"]` and instead move
  dev skills out of `.claude/skills/` entirely, accepting that the app agent
  sees an empty project-skills dir in dev.
- **`.claude/skills/` keeps collecting stray installs.** `ponytail` and
  `emil-design-eng` appeared there untracked during planning — an install drops
  a real folder next to our symlinks. If that recurs after Phase 3, add a
  `.gitignore` rule admitting only the tracked symlinks, so the duplicate pile
  cannot silently rebuild.
- **A future Codex version adds a repo-relative plugin root.** Codex already has
  `.codex-plugin/plugin.json` and `codex plugin marketplace add <local path>`;
  if that becomes usable per-repo, the `.claude/skills` symlinks could be
  replaced by one manifest serving both agents.
- **Plugin-qualified names (`opennolan:ffmpeg`) break a tool's `agent_skills`
  pointer** → the registry's `agent_skills` fields would need the prefix, or
  Phase 1 ships the skills at `<cwd>/.claude/skills/` in the packaged bundle
  instead and accepts that dev and packaged discover differently.
