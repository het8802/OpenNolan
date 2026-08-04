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
  await page.locator('.editor-open-btn').click();
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
