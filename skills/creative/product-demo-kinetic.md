# Product Demo Kinetic — Creative Skill

## When to Use
A software product demo where the only source material is **static UI screenshots**, and
the video has to feel like a live session. Launch videos, feature reveals, "here it is
working" walkthroughs.

Not this skill if you have a real screen recording — use `screen-recording` +
`screen-demo`, which polishes real captured motion. Not this skill if the subject is an
idea rather than a product surface — use `animated-explainer`.

## Core Principle
**The camera is the demo.** A static screenshot with a highlight ring drawn on it is an
explainer about a product. A camera pushing into that same screenshot while a cursor
travels to a button, clicks, and the UI swaps on that frame — that is a demo *of* the
product. Same asset. Entirely different video.

If you catch yourself drawing a ring around a region to direct attention, stop: move the
camera there instead. The ring is what you reach for when the camera is not doing its job.

## The numbers are the direction
Explainer playbooks specify "subtle 2-4% camera push-in" with holds up to 6s. Applied to
product screenshots that reads as static and slow. These numbers are not decoration:

| | explainer grammar | this skill |
|---|---|---|
| camera scale travel per move | 2-4% | **15-40%** |
| max hold on one framing | 6 s | **2.0 s** |
| attention device | ring / arrow overlay | **the camera arriving** |
| state change | new slide | **on the click frame** |
| cursor | none | **always, and it drives** |

## Required Inputs
| Input | Required? | Notes |
|---|---:|---|
| Product screenshots | Yes | One per UI state on the demo path. Native resolution or higher — a push-in magnifies, so a source at 1x goes soft on screen. |
| The demo path | Yes | An ordered sequence of UI states, i.e. what a user actually does. Not a feature list. |
| Narration script | Yes | Short declarative clauses, one claim per beat, each with on-screen proof. |
| Style playbook | Yes | `styles/product-demo-kinetic.yaml`. |
| Pipeline | Yes | `pipeline_defs/product-demo.yaml`. |

## Build order

### 1. Write the path, not the feature list
"Type an idea → the agent plans it → the timeline fills → you export" is a path. "Prompt
box, pipeline panel, editor, export" is a feature list, and it always renders as a
slideshow because there is no causality between the items to cut on.

### 2. Give every beat a camera move
Each beat states: where the camera starts, where it ends, how long it takes.

```
  beat 3  "and it fills the timeline"
    from  scale 1.0  centered on the prompt bar
    to    scale 1.6  centered on the first timeline clip
    move  whip-pan 0.22s, motion blur
    lands on the word "timeline"
```

A beat with no camera move AND no UI state change is a slideshow frame. Cut it or merge
it into its neighbour.

### 3. Choreograph the cursor
The cursor is the actor. It travels to the control (0.25-0.4s, ease-out), presses, the
ripple fires, and the UI state swaps **on that exact frame**. Typing is typed character by
character with key SFX — never revealed as a finished string. Nothing in the UI changes
without the cursor or a keystroke causing it, because that is the difference between a
product responding and a picture being replaced.

### 4. Cut on the action
The cut lands on the click, not a beat after it. A crossfade between two UI states reads
as "two pictures"; a hard cut reads as "it responded". Waiting states — spinners,
uploads, renders — get speed-ramped through in 0.2-0.4s. Never hold one at 1x; that is
the single fastest way to make software look slow.

### 5. Crop the frame honestly
Out of every source: window chrome, tabs, the OS menu bar, and any developer or agent
chat panel. Crop at the asset stage — a tight camera move will find anything you left in
and hoped it would miss.

### 6. QA on frames, not on the plan
Pull a frame from **every** scene and look at it. Check: is the UI text still legible at
this magnification; does anything collide with the caption band; do two consecutive
frames share a framing (if so, the camera stopped working). Write those frames to the
project's `.scratch/` directory — never `/tmp`.

## Sound
Key clicks for typing, a soft press for every cursor click, a whoosh on whip-pans, a low
confirm tone when a result lands. The SFX are load-bearing: they are what convince the ear
that the click caused the change. Music is a steady pulse under the cut rhythm at low
level; narration always wins.

## Checklist before compose
- [ ] Every beat has a camera move with a stated start, end and duration
- [ ] No framing is held longer than 2.0s
- [ ] Every interaction beat has a visible cursor that travels, presses and ripples
- [ ] Every UI state change lands on its click or keystroke frame
- [ ] Every reveal lands ON the narrated word, never before it
- [ ] No ring/arrow overlay is carrying a beat the camera should carry
- [ ] No spinner or loading state is held at 1x
- [ ] No window chrome, menu bar, or agent chat panel is visible in any frame
- [ ] Consecutive beats differ in framing, not only in content
- [ ] A sampled frame from every scene was inspected at final render scale
