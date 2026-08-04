# OPN-41 in pictures

**Status: PLAN** — plain-language version of
[`architecture.md`](architecture.md). Same plan, no vocabulary.

A "skill" here is a folder with a `SKILL.md` file in it. The agent reads one
when it needs to know how to do something — how to cut video with ffmpeg, how
to write a caption. The app ships 78 of them.

---

## 1. The bug: the agent looks in one folder, and we never shipped it

```
┌─ on your machine ───────────┐  ┌─ in the shipped Mac app ────┐
│ agent starts in: repo root  │  │ agent starts in:            │
│                             │  │   Resources/backend         │
└──────────────┬──────────────┘  └──────────────┬──────────────┘
               └───────────────┬────────────────┘
                               ▼
        both look in <start folder>/.claude/skills/
        because of  server/agent_runner.py:655
               ┌───────────────┴────────────────┐
               ▼                                ▼
       .claude/skills/ is here          .claude/ was never
       → finds 56 skills                copied into the app
                                        → finds 0 skills
```

The painful part: **the 78 skills are already inside the app.** They ride
along at `desktop/package.json:47` as `.agents/skills`. The agent just never
looks there, because the only folder it checks is `.claude/skills`.

So it isn't a missing-files bug. It's a looking-in-the-wrong-place bug.

---

## 2. Why there are two skill folders (and why one can go)

```
.claude/skills/     56 folders    NOT shipped   the agent looks here
.agents/skills/     78 folders    shipped       nobody looks here
```

We compared them file by file. 55 of the 56 in `.claude/skills/` are
**exact copies** of folders already in `.agents/skills/`. One is unique
(`explain-with-html`). `.claude/` holds nothing else — no settings, no config.

There is a reason for the copy, and it isn't laziness: each agent looks in a
different folder name (§6). So the copy has to stay — but as a **shortcut**
rather than 450 duplicated files.

**So: one real copy of every file, plus shortcuts where another agent needs
to find it.**

---

## 3. The fix: hand the agent a folder of its own

```
MOVE THE VIDEO SKILLS DOWN ONE LEVEL
  .agents/skills/*   ──►   .agents/app/skills/*
  a rename, no file edits. Why: see §6 — it also stops Codex
  from seeing them.

ADD ONE FILE
  .agents/app/.claude-plugin/plugin.json
  { "name": "opennolan", "description": "OpenNolan video skills" }

  ^ the marker that makes .agents/app/ a bundle the agent can be
    handed whole.

CHANGE THE STARTUP SETTINGS   server/agent_runner.py:649
                       BEFORE                 AFTER
  where to look        the start folder        <repo>/.agents/app
  turn skills on       (not set)               yes, all of them
  read project files   yes                     no
                               │
                               ▼
        your machine and the shipped app now behave identically
```

Plus one line in `desktop/package.json:47` so the new folder ships.

That's it — the bug is closed at this point. Everything after this is cleanup.

---

## 4. What the agent actually holds in its head

Worth being precise, because it sounds expensive and isn't.

```
┌─ handed to the agent at startup ──────────────────────────┐
│  ffmpeg    — "trim, join, and re-encode video"            │
│  remotion  — "build a video out of React components"      │
│  music     — "generate a backing track"                   │
│  … 78 lines like this                                     │
└──────────────────────────┬────────────────────────────────┘
                           │   name + one line each.
                           │   78 skills = ~1,000 words of budget.
                           ▼
              agent decides "I need ffmpeg"
                           │
                           ▼
              ── only NOW is the full file opened ──
```

We tested this with a decoy: a skill whose description was visible but with a
made-up password buried in its body. The agent could recite the description
without being asked, and could not see the password. So it holds a **menu, not
the meals.** 78 skills is cheap.

**Correction after QA.** That decoy test used ONE skill. With all 73, the
descriptions do **not** fit — measured, the whole set costs ~1,000 tokens while
the descriptions alone would be ~5,000. So the menu the agent gets is
effectively **just the names**:

```
no skills at all                24,444 tokens
73 names, no descriptions       25,343      names cost      ~900
73 names + real descriptions    25,462      descriptions add ~119
```

The cost figure in step 4 is right, but it is the price of 73 *names*. The agent
picks mostly by name, then reads the file. This is not caused by our choice —
delivering the same 73 the other way costs the same, and it is exactly how the
app already behaved before this change. Worth knowing, not worth redesigning:
short, self-explanatory skill names matter more than long descriptions.

---

## 5. Keeping the video skills and the coding skills apart

Two different jobs are currently sharing one pile:

- **Video skills** — what the app's agent uses to make a video for a customer.
- **Coding skills** — what Claude Code and Codex use to build the app itself.

Neither should see the other's. After the split:

```
                        .agents/
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
        app/skills/                  skills/
        ~71 video                    ~7 coding
              │                         │
              ▼                   ┌─────┴─────┐
┌─ the app's agent ─────────┐     ▼           ▼
│ handed app/skills/ only   │  Codex      Claude Code
│                           │  reads      reads
│ "read project files: no"  │  this       .claude/skills/,
│ means it CANNOT reach     │  folder     which holds
│ the coding skills, even   │  directly   shortcuts to
│ on your machine           │             this folder
└───────────────────────────┘
```

The wall is the "read project files: no" setting from step 3. Not a naming
convention, not good manners — the app's agent is simply not looking.

Both coding agents get the name and one-line description of each coding skill
and decide for themselves, exactly like the video agent does with its 71. One
real copy of each file, no duplicates.

---

## 6. Each coding agent looks in its own folder

This is the part that decided the layout. Each agent has one folder name it
looks for, and the two do not agree. We tested all four:

```
folder inside the repo        Claude Code    Codex
──────────────────────────────────────────────────
.claude/skills/                  finds it    misses it
.agents/skills/                 misses it    finds it
.codex/skills/                  misses it    finds it
.agents/dev-skills/             misses it    misses it
```

That last row matters: the folder has to be named exactly `skills`. Call it
anything else and both agents walk past it.

**This is why the repo has two copies.** Not sloppiness — one folder per agent,
same contents. Deleting either one on its own quietly breaks that agent.

**The fix is a shortcut, not a copy.** Claude Code follows shortcuts, so one
real folder can serve both:

```
   .agents/skills/karpathy-guidelines/SKILL.md      the real file
              ▲                                    → Codex reads it here
              │
   .claude/skills/karpathy-guidelines ── shortcut ──┘
                                                  → Claude Code reads it
```

One file on disk. Both agents find it the normal way — name and description in
memory — and pick it when it fits. No written instructions, no second copy.

**Why the video skills have to move.** Codex reads `.agents/skills/`. If the 71
video skills stay there, Codex keeps seeing all of them, which is the mixing we
are trying to stop. So they move down one level into `.agents/app/skills/`,
where neither coding agent looks. That is the move in step 1.

> **An earlier draft of this plan said the opposite** — that Codex could not see
> anything inside the repo, so the coding skills had to be named in `CLAUDE.md`
> and `AGENTS.md` instead. That was wrong. It came from reading text inside the
> Codex program; actually asking Codex what it could see showed it had been
> reading this repo's `.agents/skills/` all along. Pushing back on it was the
> right call.

---

## 7. Which skills count as "coding"

Read off each file's own description, not guessed from the folder name:

```
karpathy-guidelines          coding discipline; both agents already use it
ponytail                      simplest-solution discipline    (you added this)
emil-design-eng               UI polish, component design     (you added this)
explain-with-html             writing docs for people
vercel-composition-patterns   "refactoring components with boolean prop..."
vercel-react-best-practices   "React and Next.js performance optimization"
web-design-guidelines         "Review UI code ... 'review my UI'"
```

`ponytail` and `emil-design-eng` are on your disk but not committed yet, so
they get committed as part of this.

**One I had wrong:** I listed `agents` as a coding skill, reading the folder
name as "agent setup". Its actual description is *"Build voice AI agents with
ElevenLabs"* — that is a video skill. It stays with the app.

**One still undecided:** `tailwind-design-system`. Tailwind is used both in the
app's own interface and inside the video compositions, so it could belong to
either. Left on the video side for now. If both need it, it gets a shortcut
rather than a copy.

**A folder that refills itself:** `ponytail` and `emil-design-eng` showed up in
`.claude/skills/` on their own — installing a skill drops a real folder there.
Once that folder is mostly shortcuts, stray installs will land beside them. If
it happens again we add a `.gitignore` rule, so the duplicate pile cannot
quietly rebuild.

---

## Order of work

```
1  move the 71 video skills to .agents/app/skills/, add plugin.json,
   ship it, change the 3 startup settings
   → BUG CLOSED. app agent has its skills; Codex stops seeing them.
2  delete the 55 duplicate copies in .claude/skills/
   → one real copy of everything.
3  put the coding skills in .agents/skills/ and add the shortcuts
   → both coding agents find them; the two jobs stop sharing.
```

Step 1 closes the ticket on its own, and uses the final folder layout so
nothing gets wired up twice. Steps 2 and 3 can land separately.

**How we'll know step 1 worked:** open the shipped app and ask the agent what
skills it has. Today: nothing. After: 71, including `opennolan:ffmpeg` — and
the same count on your machine. If the two disagree, the fix is half-applied.

**How we'll know step 3 worked:** `codex debug prompt-input` at the repo root
lists the coding skills and no video ones. It prints what the model actually
sees and costs nothing to run.
