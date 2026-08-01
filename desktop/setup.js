'use strict';

// First-run setup window logic. External (not inline) so it runs under a strict `script-src 'self'`
// CSP — an inline <script> was silently blocked by the app's session CSP, which is why this window
// used to render blank. Consumes the progress feed exposed by setup-preload.js (window.openNolanSetup).

(() => {
  const log = document.getElementById('log');
  const stage = document.getElementById('stage');   // big human-readable current step
  const detail = document.getElementById('detail');  // small secondary line (latest activity)
  const fill = document.getElementById('fill');
  const pctEl = document.getElementById('pct');

  function line(text, cls) {
    const d = document.createElement('div');
    if (cls) d.className = cls;
    d.textContent = text;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    // Mirror the newest technical line into the small detail slot so movement is visible even
    // when the log is scrolled or the eye is on the stage/bar.
    if (!failed) detail.textContent = text.length > 90 ? text.slice(0, 90) + '…' : text;
  }

  // Determinate progress. `display` is what the bar shows; each step frame gives the step's start
  // (pct) and where it will land (end). Between frames we creep toward `ceil` so a long silent
  // install (uv / npm ci) still reads as alive. Never move backwards, never pass ceil.
  let display = 0, ceil = 0, haveStep = false, failed = false;
  function render() {
    fill.style.width = display.toFixed(1) + '%';
    pctEl.textContent = Math.floor(display) + '%';
  }

  const api = window.openNolanSetup;
  if (api) {
    api.onProgress((l) => line(l));

    api.onStep(({ pct, end, label }) => {
      if (failed) return;
      display = Math.max(display, Math.min(100, pct || 0));
      ceil = Math.max(display, Math.min(100, end == null ? display : end));
      if (!haveStep) {
        // Leaving the indeterminate slide: snap the width with NO transition, else the 40%-wide
        // slider visibly drains backwards to the (small) real pct over the .35s transition.
        haveStep = true;
        fill.classList.remove('indet');
        fill.style.transition = 'none';
        render();
        void fill.offsetWidth; // flush so the snap isn't batched into the next transition
        fill.style.transition = '';
      }
      if (label) stage.textContent = label;
      render();
    });

    api.onDone(() => { if (!failed) stage.textContent = 'Finishing up…'; });

    api.onError((m) => {
      failed = true;
      stage.textContent = 'Setup failed';
      detail.textContent = 'See the log below.';
      pctEl.textContent = '';
      fill.classList.remove('indet');
      fill.classList.add('err');
      fill.style.width = '100%';
      line(m, 'err');
    });
  } else {
    // No bridge = the preload didn't load. Surface it instead of sitting on a mystery blank window.
    stage.textContent = 'Setup UI failed to initialize';
    detail.textContent = 'The progress bridge did not load.';
  }

  // Purely exponential creep (no linear floor): asymptotically approaches — never reaches — the
  // step's ceiling, so a multi-minute uv / npm ci keeps showing motion instead of pinning early.
  // ~0.5%/s of the remaining gap: after 60s ≈ 26% of the gap consumed, after 3min ≈ 59%.
  setInterval(() => {
    if (!haveStep || failed || ceil - display < 0.05) return;
    display += (ceil - display) * 0.001;
    render();
  }, 200);
})();
