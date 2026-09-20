import { test, expect } from "@playwright/test";

async function canvasStats(page) {
  return page.locator("canvas").evaluate((canvas) => {
    const copy = document.createElement("canvas");
    copy.width = 160;
    copy.height = 100;
    const ctx = copy.getContext("2d");
    ctx.drawImage(canvas, 0, 0, 160, 100);
    const data = ctx.getImageData(0, 0, 160, 100).data;
    let dark = 0,
      red = 0,
      blue = 0,
      checksum = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] < 150 && data[i + 1] < 150 && data[i + 2] < 150) dark++;
      if (data[i] > data[i + 1] * 1.2 && data[i] > data[i + 2] * 1.2) red++;
      if (data[i + 2] > data[i] * 1.15) blue++;
      checksum = (checksum + data[i] * (i + 1) + data[i + 1]) % 1000000007;
    }
    return { dark, red, blue, checksum };
  });
}

async function openScene(page) {
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/, {
    timeout: 30000,
  });
  await expect(page.locator("#connection")).toContainText("CONNECTED");
  await expect
    .poll(async () => (await canvasStats(page)).dark, { timeout: 15000 })
    .toBeGreaterThan(20);
}

test("desktop physical run, controls, replay and export", async ({
  page,
}, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 960 });
  await openScene(page);
  const initial = await canvasStats(page);
  expect(initial.red).toBeGreaterThan(1);
  expect(initial.blue).toBeGreaterThan(10);
  await page.screenshot({ path: "docs/workbench-desktop.png" });
  await page.locator("#camera-top").click();
  await expect
    .poll(async () => (await canvasStats(page)).checksum, { timeout: 15000 })
    .not.toBe(initial.checksum);
  await page.locator("#camera-home").click();
  const canvas = await page.locator("canvas").boundingBox();
  await page.mouse.move(
    canvas.x + canvas.width / 2,
    canvas.y + canvas.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    canvas.x + canvas.width / 2 + 60,
    canvas.y + canvas.height / 2,
    { steps: 8 },
  );
  await page.mouse.up();
  await expect
    .poll(async () => (await canvasStats(page)).checksum, { timeout: 15000 })
    .not.toBe(initial.checksum);
  await page.locator("#camera-home").click();
  await page.locator("#step").click();
  await expect(page.locator("#status-text")).toHaveText("已暂停", {
    timeout: 30000,
  });
  await expect(page.locator("#run-budget")).toContainText("01 /");
  const stepped = await canvasStats(page);
  expect(stepped.checksum).not.toBe(initial.checksum);
  await page.locator("#run").click();
  await expect(page.locator("#status-text")).toHaveText("运行中");
  await page.locator("#run").click();
  await expect(page.locator("#status-text")).toHaveText("已暂停");
  await page.locator("#run").click();
  await expect(page.locator("#status-text")).toHaveText("验证通过", {
    timeout: 60000,
  });
  await expect(page.locator("#support")).toHaveText("YES");
  await expect(page.locator("#model-calls")).toHaveText("0 MODEL CALLS");
  await page.screenshot({ path: testInfo.outputPath("completed.png") });
  await page.locator("#replay-play").click();
  await expect(page.locator("#status-text")).toHaveText("轨迹回放");
  const first = await page.locator("#timeline").inputValue();
  await expect
    .poll(() => page.locator("#timeline").inputValue())
    .not.toBe(first);
  await page.locator("#live").click();
  await expect(page.locator("#status-text")).toHaveText("验证通过");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#export").click(),
  ]);
  expect(download.suggestedFilename()).toContain(".json");
  await page.locator('[data-task="barrier"]').click();
  await expect(page.locator("#scene-task")).toHaveText("BARRIER");
  await expect(page.locator("#status-text")).toHaveText("待命");
  await page.locator("#run").click();
  await expect(page.locator("#status-text")).toHaveText("运行中");
  await page.locator("#stop").click();
  await expect(page.locator("#status-text")).toHaveText("已停止");
  expect(errors).toEqual([]);
});

test("mobile layout, scene and settings drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openScene(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const stats = await canvasStats(page);
  expect(stats.red).toBeGreaterThan(1);
  expect(stats.blue).toBeGreaterThan(10);
  await page.screenshot({ path: "docs/workbench-mobile.png", fullPage: true });
  await page.locator("#settings-open").click();
  await expect(page.locator("#sidebar")).toBeVisible();
  await page.locator('[data-task="stack"]').click();
  await page.locator("#settings-close").click();
  await expect(page.locator("#scene-task")).toHaveText("STACK");
  await page.locator('[data-tab="data"]').click();
  await expect(page.locator("#raw-state")).toBeVisible();
  await page.locator('[data-tab="scene"]').click();
  await expect(page.locator("canvas")).toBeVisible();
});

test("model connection form works on desktop and mobile without exposing keys", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await openScene(page);
  await page.locator("#model-connect").click();
  await expect(page.locator("#connection-dialog")).toBeVisible();
  await page.locator("#api-url").fill("https://example.invalid/v1");
  await page.locator("#api-model").fill("example/model");
  await page.locator("#api-key").fill("test-ui-secret");
  await page.screenshot({ path: "docs/model-connection.png" });
  await page.locator("#connection-save").click();
  await expect(page.locator("#connection-dialog")).not.toBeVisible();
  await expect(page.locator("#provider")).toHaveValue("chat");
  await expect(page.locator("#threshold")).toBeDisabled();
  await expect(page.locator("#api-key")).toHaveValue("");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#settings-open").click();
  await page.locator("#model-connect").click();
  await expect(page.locator("#connection-dialog")).toBeVisible();
  await expect(page.locator("#key-state")).toHaveText("已配置");
  await expect(page.locator("#api-key")).toHaveValue("");
  const box = await page.locator("#connection-dialog").boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "docs/model-connection-mobile.png" });
  await page.locator("#connection-close").click();
});

test("official Jev preset links to key signup and preserves the resolved model", async ({
  page,
}) => {
  await openScene(page);
  await page.locator("#model-connect").click();
  await page.locator("#api-provider").selectOption("jev");
  await expect(page.locator("#api-url")).toHaveValue(
    "https://api.typesafe.ai/v1/systemone",
  );
  await expect(page.locator("#api-url")).toHaveAttribute("readonly", "");
  await expect(page.locator("#api-model")).toHaveValue("jev-latest");
  await expect(page.locator("#typesafe-links")).toBeVisible();
  await expect(page.locator("#typesafe-links a").first()).toHaveAttribute(
    "href",
    "https://console.typesafe.ai",
  );
  await expect(page.locator("#json-mode-row")).toBeHidden();
  // Stub only this test call; saving still exercises the local backend.
  await page.route("**/api/connections/jev/test", (route) =>
    route.fulfill({
      json: { ok: true, model: "jev-1.13.0", latency_ms: 123 },
    }),
  );
  await page.locator("#api-key").fill("test-typesafe-secret");
  await page.locator("#connection-test").click();
  await expect(page.locator("#connection-result")).toContainText("jev-1.13.0");
  await expect(page.locator("#api-key")).toHaveValue("");
  await expect(page.locator("#provider")).toHaveValue("jev");
  await expect(page.locator("#threshold")).toBeEnabled();
  const state = await page.request.get("/api/state");
  expect((await state.json()).cycles).toBe(0);
  await page.locator("#api-provider").selectOption("chat");
  await expect(page.locator("#typesafe-links")).toBeHidden();
  await page.locator("#connection-close").click();
});

test("Claude native configuration is separate and does not expose credentials", async ({
  page,
}) => {
  await openScene(page);
  await page.locator("#model-connect").click();
  await page.locator("#api-provider").selectOption("claude");
  await expect(page.locator("#api-url")).toHaveValue(
    "https://api.anthropic.com/v1",
  );
  await expect(page.locator("#json-mode-row")).toBeHidden();
  await page.locator("#api-model").fill("claude-test");
  await page.locator("#api-key").fill("test-claude-secret");
  await page.locator("#connection-save").click();
  await expect(page.locator("#provider")).toHaveValue("claude");
  await expect(page.locator("#threshold")).toBeDisabled();
  await expect(page.locator("#provider-note")).toContainText("Claude API");
  await page.locator("#model-connect").click();
  await expect(page.locator("#api-provider")).toHaveValue("claude");
  await expect(page.locator("#api-key")).toHaveValue("");
  await expect(page.locator("#key-state")).toHaveText("已配置");
  await page.locator("#connection-close").click();
});
