'use strict';

const { defineConfig } = require('@playwright/test');
const path = require('node:path');
const worktreeConfig = require('./worktree-config');

const frontendPort = worktreeConfig.frontendPort();
const resultRoot = process.env.OPENNOLAN_TEST_RESULTS || path.join(__dirname, '..', '.local', 'test-results', 'smoke');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.join(resultRoot, 'artifacts'),
  reporter: [['list'], ['json', { outputFile: path.join(resultRoot, 'playwright.json') }]],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: '../run-dev',
    url: `http://127.0.0.1:${frontendPort}`,
    cwd: __dirname,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
