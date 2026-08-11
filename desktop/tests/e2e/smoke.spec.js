'use strict';

const { test, expect } = require('@playwright/test');

test('app starts and supports one read and one write', async ({ page }) => {
  const browserErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
  page.on('requestfailed', (request) => {
    const errorText = request.failure()?.errorText || '';
    // A deliberate navigation/reload cancels in-flight fetches. Playwright
    // reports those as ERR_ABORTED even though the browser and server are fine.
    if (errorText !== 'net::ERR_ABORTED') {
      browserErrors.push(`network: ${request.method()} ${request.url()} ${errorText}`);
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 500) browserErrors.push(`http ${response.status()}: ${response.url()}`);
  });

  await page.goto('/');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveTitle(/OpenNolan/);

  const health = await page.evaluate(async () => {
    const response = await fetch('/api/health');
    return { status: response.status, body: await response.json() };
  });
  expect(health.status).toBe(200);
  expect(health.body.status).toBe('ok');
  expect(health.body.projects_dir).toContain(process.env.OPENNOLAN_HOME);

  const projectName = `Smoke ${Date.now()}`;
  const created = await page.evaluate(async (name) => {
    const response = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    return { status: response.status, body: await response.json() };
  }, projectName);
  expect(created.status).toBe(201);
  expect(created.body.project_id).toBeTruthy();

  const projects = await page.evaluate(async () => (await fetch('/api/projects')).json());
  expect(projects.projects.some((project) => project.project_id === created.body.project_id)).toBe(true);

  await page.reload();
  await page.waitForLoadState('networkidle');
  await expect(page.getByText(projectName, { exact: true })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

// Geometry regression guard for the Studio toolbar at the app's own minimum window width
// (desktop/main.js sets minWidth: 960). This lives here, not in jsdom: the behaviour depends
// on a CSS container query and on real layout, and StudioToolbar.test.jsx can only assert that
// the markup exists. Before this test the only evidence the toolbar held at 960px was a
// one-off manual browser probe, which is not regression coverage.
test('studio toolbar holds one row at the 960px minimum width', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 600 });

  // A deliberately long name: the original defect was the project title pushing Save and the
  // terminal action off the right edge, so a short name would not exercise it.
  const projectName = `Toolbar ${Date.now()} a-very-long-project-name-that-must-not-push-actions-off-screen`;
  // Navigate first: a relative fetch needs an origin, and about:blank has none.
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const created = await page.evaluate(async (name) => {
    const response = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    return { status: response.status, body: await response.json() };
  }, projectName);
  expect(created.status).toBe(201);

  await page.reload();
  await page.waitForLoadState('networkidle');
  await page.getByText(projectName, { exact: true }).click();
  await page.locator('.pb-action', { hasText: 'Edit' }).click();
  await expect(page.locator('.st-bar')).toBeVisible();
  await expect(page.locator('.st-tools')).toBeVisible();

  // 1. No horizontal overflow anywhere in the bar — the earlier failure mode was an invisible
  //    scroller on .st-tools that hid the right-hand actions.
  const overflow = await page.evaluate(() => {
    const pick = (s) => {
      const el = document.querySelector(s);
      return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
    };
    return { bar: pick('.st-bar'), tools: pick('.st-tools') };
  });
  expect(overflow.bar.scrollWidth).toBeLessThanOrEqual(overflow.bar.clientWidth);
  expect(overflow.tools.scrollWidth).toBeLessThanOrEqual(overflow.tools.clientWidth);

  // 2. Save and Export are visible and fully inside the viewport.
  for (const name of [/^Save$/, /^Export$/]) {
    const control = page.getByRole('button', { name });
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(960);
  }

  // 3. The More overflow is showing at this width — that is the mechanism keeping the row
  //    single, so if a future breakpoint change stops it firing this fails loudly.
  await expect(page.locator('.st-more')).toBeVisible();

  // 4. One row: every tracked top-level control shares a horizontal band. If the bar wrapped,
  //    the right-hand group's centre would sit a full control-height below the left group's.
  const centres = await page.evaluate(() => {
    const sels = ['.st-bar-left', '.st-tools .st-grp', '.st-more', '.st-toggle'];
    const els = sels.flatMap((s) => [...document.querySelectorAll(s)])
      .filter((el) => el.getClientRects().length > 0);
    return els.map((el) => {
      const r = el.getBoundingClientRect();
      return r.top + r.height / 2;
    });
  });
  expect(centres.length).toBeGreaterThan(3);
  expect(Math.max(...centres) - Math.min(...centres)).toBeLessThan(8);
});

// The Calendar / Schedule / Edit entry points were restyled to REUSE existing pills rather than
// carry their own look. "Same pill" is a computed-style claim, so it can only be checked against
// real layout — jsdom has no cascade for this. Guards against a future edit quietly reintroducing
// a bespoke style, and against Schedule drifting away from Edit's shape or side.
test('calendar and project-bar entry points reuse the shared pills', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  const projectName = `Pills ${Date.now()}`;
  const created = await page.evaluate(async (name) => {
    const response = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    return { status: response.status, body: await response.json() };
  }, projectName);
  expect(created.status).toBe(201);
  await page.reload();
  await page.waitForLoadState('networkidle');

  // 1. Calendar is one of the dashboard header's pills, not its own thing: same class, same
  //    computed shape and type as BYOK, with an icon like its neighbours.
  const calendar = page.locator('.byok-btn', { hasText: 'Calendar' });
  await expect(calendar).toBeVisible();
  await expect(calendar.locator('svg')).toHaveCount(1);
  const shape = (selector) => page.evaluate((sel) => {
    const el = [...document.querySelectorAll('.byok-btn')].find((node) => node.textContent.trim() === sel);
    const s = getComputedStyle(el);
    return [s.borderRadius, s.fontSize, s.fontWeight, s.padding, s.borderWidth].join('|');
  }, selector);
  expect(await shape('Calendar')).toBe(await shape('BYOK'));

  // 2. Schedule and Edit are two of the SAME pill, side by side, Schedule on the left.
  await page.getByText(projectName, { exact: true }).click();
  const actions = page.locator('.runtimes .pb-action');
  await expect(actions).toHaveCount(2);
  await expect(actions.nth(0)).toHaveText(/Schedule/);
  await expect(actions.nth(1)).toHaveText(/Edit/);
  await expect(actions.nth(0).locator('svg')).toHaveCount(1);
  await expect(actions.nth(1).locator('svg')).toHaveCount(1);
  const boxes = await actions.evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, mid: r.top + r.height / 2, radius: getComputedStyle(el).borderRadius };
  }));
  expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].left);        // Schedule sits left of Edit
  expect(Math.abs(boxes[0].mid - boxes[1].mid)).toBeLessThan(2);    // one row
  expect(boxes[0].radius).toBe(boxes[1].radius);                    // one pill shape
  expect(parseFloat(boxes[0].radius)).toBeGreaterThan(100);         // ...and it IS a pill
});
