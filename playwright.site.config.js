import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests-site",
  outputDir: "./playwright-results/site",
  timeout: 30000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8133", browserName: "chromium",
    channel: process.env.EMBODIED_TEST_CHANNEL, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: { command: "python3 scripts/serve_site.py --port 8133",
    url: "http://127.0.0.1:8133", reuseExistingServer: false },
});
