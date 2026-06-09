# Visual Hierarchy Edit Fix

Use this creative skill when a short-form video needs to **teach by repairing a weak edit**: show the flawed composition, diagnose the design principle it violates, then rebuild it beat-by-beat into a stronger layout. Inspired by Aevy Video School Reel `DZHvGrfSQ4t` (“This is how we will fix this edit.”).

## Format promise

The viewer should feel: “I can see exactly why the original edit looked amateur, and I can steal the fix for my own videos.”

This is not a generic design lecture. It is a visible before/after autopsy.

## Reference breakdown

- **Hook:** starts with a creator talking-head plus a visible comment bubble asking, “How exactly would you fix this?” The comment acts as the viewer’s question and legitimizes the tutorial.
- **Problem artifact:** shows the bad edit as a simplified silhouette/layout: a huge yellow subject on dark background with weak/unclear text placement. The creator appears as a small circular PIP to keep authority while the edit is on screen.
- **Principle cards:** briefly flashes design/typography references such as “Anatomy Of Typeface” and visual-design principles. These act as proof that the critique is rule-based, not taste-based.
- **Timeline proof:** shows the editing timeline/audio waveform with a yellow circle highlight to connect the lesson to actual editing work.
- **Redesign arc:** reconstructs the text “i’ve spent 5 years interacting” into a better hierarchy, repeatedly emphasizing that one dominant focal element must win.
- **Final transformation:** shifts from white/black rough layout to bright green/blue poster-like composition with subject cutouts, a large curved shape, strong scale contrast, and text like “one habit that changes.”

## Reusable scene system

1. **Comment-to-challenge hook**
   - Talking head fills the frame, warm creator lighting.
   - Overlay a real/comment-style bubble in lower third.
   - Spoken move: “Someone asked how I’d fix this edit. Here’s the exact redesign.”

2. **Bad edit isolate**
   - Cut to the flawed frame with nonessential details stripped away.
   - Use a high-contrast silhouette or wireframe version so the problem is obvious.
   - Add creator PIP in a corner for continuity.

3. **Principle receipt flash**
   - Show 1–2 quick reference cards: typography anatomy, visual principles, timeline or layer stack.
   - Highlight one term at a time: `scale`, `contrast`, `alignment`, `proximity`, `hierarchy`.
   - Keep receipts short; the payoff is the visible fix, not a slideshow.

4. **Hierarchy rebuild**
   - Rebuild the frame in layers: dominant word/number first, supporting words second, subject/asset third, background shape last.
   - Use circles/arrows/scribbles to point at the active fix.
   - If a text phrase has a number, make the number the hero unless another noun carries more emotional weight.

5. **Color/posterization pass**
   - Move from monochrome diagnosis to saturated final design.
   - Add one loud background color, one supporting shape, one grayscale or cutout subject cluster, and one dominant text group.
   - Preserve phone-safe readability; do not let the “after” become cluttered.

6. **Rule takeaway / CTA**
   - End with a reusable rule, e.g. “Before you animate, decide what wins the frame.”
   - CTA can ask for the next teardown: “Drop an edit and I’ll fix the hierarchy.”

## Visual language

- Canvas: vertical 9:16.
- Talking head: medium/close-up, purple-blue creator lighting, frequent punch-ins.
- Diagnostic canvases: white or charcoal backgrounds, bold black/yellow/blue annotations.
- Final designs: saturated green background, deep blue curved blob, grayscale cutout people, royal-blue text.
- PIP: circular creator avatar in top-right with white rim; use while screen/design examples are full-frame.
- Markups: yellow hand-drawn circles, blue oval/circle highlights, arrow/scribble overlays.

## Typography rules

- Use bold grotesk/sans for rebuilt poster text.
- Use extreme scale contrast: one word/number should be 2–5× larger than support text.
- Keep text chunks short: 1–4 words per object.
- Do not animate a phrase before hierarchy is solved. The reference’s key lesson is that motion cannot rescue unclear priority.

## Motion grammar

- Talking-head cuts: fast punch-ins and expression changes every 2–4 seconds.
- Diagnostic frames: snap cuts, quick zoom-to-detail, freeze-and-markup.
- Markups: draw-on circles/ovals over 8–14 frames, preferably synced to spoken emphasis.
- Rebuild: layer-pop or slide-in one element at a time; hold each state just long enough for comprehension.
- Receipts: 0.5–1.2 second flashes with a circled term or timeline section.
- Final after-state: scale/push-in 3–6%, subtle parallax on subject cutouts, CTA hold.

## Sound design

- Use short pops/clicks when layers appear.
- Use whoosh or snap on before/after transitions.
- Use a light riser into the final redesign reveal.
- Keep background music low; the tutorial depends on clear VO and visible design decisions.

## OpenNolan / HyperFrames implementation notes

- Best runtime: HyperFrames for GSAP-style text/shape rebuilds and hand-drawn SVG markups; Remotion also works if using existing text/card primitives.
- Useful components: `comment_bubble`, `creator_pip`, `bad_edit_wireframe`, `principle_receipt_card`, `handdrawn_circle`, `hierarchy_rebuild_stack`, `poster_after_state`.
- Represent each design fix as data: `{element, before, after, principle, timing}` so the same template can teardown any edit.
- QA requirement: make a contact sheet that includes bad state, mid-rebuild, and final state. Verify the dominant element is obvious in each frame at phone size.

## Common mistakes

- Explaining design principles without a visible before/after.
- Adding more motion before solving scale/contrast/alignment.
- Showing too many principles at once; pick one active principle per beat.
- Letting creator PIP cover the thing being critiqued.
- Overcrowding the final design with decorative cutouts.
