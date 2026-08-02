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
