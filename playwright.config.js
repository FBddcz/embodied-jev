import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests-ui",
  outputDir: "./playwright-results",
  timeout: 120000,
  expect: { timeout: 15000 },
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8099",
    browserName: "chromium",
    channel: process.env.EMBODIED_TEST_CHANNEL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: {
      args: [
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
      ],
    },
  },
  webServer: {
    command: `${process.env.EMBODIED_TEST_PYTHON || "python"} -m embodied_jev.cli serve --port 8099`,
    env: { EMBODIED_JEV_PERSISTENCE: "memory" },
    url: "http://127.0.0.1:8099/api/config",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
