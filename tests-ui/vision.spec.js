import { test, expect } from "@playwright/test";

const pixel = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5N8AAAAASUVORK5CYII=",
  "base64",
);

async function visionFixture(page) {
  const initial = await (await page.request.get("/api/state")).json();
  const snapshot = {
    ...initial,
    status: "idle",
    provider: "baseline",
    observation_mode: "privileged",
    perception: null,
  };
  const requests = { metadata: [], images: [], resets: [] };
  const metadata = {
    capture_id: "camera-1",
    source: "rgbd",
    status: "ready",
    captured_at: "2026-09-20T12:00:00Z",
    sim_time: 0.12,
    latency_ms: 18.4,
    objects: [
      { id: "object", label: "方块", visible: true },
      { id: "destination", label: "目标", visible: true },
    ],
  };
  await page.route("**/api/state", (route) =>
    route.fulfill({ json: snapshot }),
  );
  await page.route("**/api/reset", (route) => {
    const body = route.request().postDataJSON();
    requests.resets.push(body);
    snapshot.id = `${initial.id}-vision-${requests.resets.length}`;
    snapshot.observation_mode = body.observation_mode;
    snapshot.perception =
      body.observation_mode === "rgbd" ? { ...metadata } : null;
    return route.fulfill({ json: snapshot });
  });
  await page.route("**/api/perception?*", (route) => {
    requests.metadata.push(new URL(route.request().url()));
    return route.fulfill({ json: { ...metadata } });
  });
  await page.route("**/api/perception/*.png?*", (route) => {
    requests.images.push(new URL(route.request().url()));
    return route.fulfill({ contentType: "image/png", body: pixel });
  });
  return { snapshot, metadata, requests };
}

async function nextPoll(page) {
  await page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/state",
  );
}

test("mocked camera contract: capture caching, channel switching and responsive layout", async ({
  page,
}, testInfo) => {
  const { snapshot, metadata, requests } = await visionFixture(page);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-empty")).toContainText(
    "选择「RGB-D 视觉」",
  );
  await expect(page.locator('[data-vision-channel="depth"]')).toBeDisabled();
  expect(requests.metadata).toHaveLength(0);
  expect(requests.images).toHaveLength(0);

  await page.locator("#observation-mode").selectOption("rgbd");
  await expect(page.locator("#vision-image")).toBeVisible();
  expect(requests.resets.at(-1).observation_mode).toBe("rgbd");
  await expect(page.locator("#vision-latency")).toHaveText("18.4 ms");
  await expect(page.locator("#vision-time")).toHaveText("t = 0.12 s");
  await expect(page.locator("#vision-visibility")).toHaveText("2 / 2");
  await expect(page.locator("#input-context")).toContainText("RGB-D");
  await expect(page.locator("#vision-note")).toContainText(
    "尚不具备通用视觉识别能力",
  );
  expect(requests.metadata).toHaveLength(1);
  expect(requests.images).toHaveLength(1);
  expect(requests.images[0].searchParams.get("episode_id")).toBe(snapshot.id);
  expect(requests.images[0].searchParams.get("capture_id")).toBe("camera-1");

  await nextPoll(page);
  await nextPoll(page);
  expect(requests.metadata).toHaveLength(1);
  expect(requests.images).toHaveLength(1);
  await page.locator('[data-vision-channel="depth"]').click();
  await expect(page.locator("#vision-image-label")).toHaveText(
    "深度 · 最近感知帧",
  );
  expect(requests.images.at(-1).pathname).toBe("/api/perception/depth.png");
  expect(requests.metadata).toHaveLength(1);

  metadata.capture_id = "camera-2";
  metadata.status = "partial";
  metadata.objects[0].visible = false;
  snapshot.perception = { ...metadata };
  await expect(page.locator("#vision-visibility")).toHaveText("1 / 2");
  await expect(page.locator("#vision-status")).toContainText(
    "遮挡或未检测到：方块",
  );
  await expect(page.locator("#vision-status")).toHaveClass(/has-alert/);
  expect(requests.metadata).toHaveLength(2);

  snapshot.status = "running";
  await expect(page.locator("#observation-mode")).toBeDisabled();
  snapshot.status = "idle";
  await expect(page.locator("#observation-mode")).toBeEnabled();

  for (const width of [1440, 1024, 768, 390, 330]) {
    await page.setViewportSize({ width, height: 960 });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    expect(
      await page
        .locator("#vision-panel")
        .evaluate((panel) => panel.scrollWidth <= panel.clientWidth),
    ).toBe(true);
    await expect(page.locator('[data-vision-channel="depth"]')).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`vision-${width}.png`),
      fullPage: true,
    });
    await page.locator('[data-tab="scene"]').click();
    const tabBounds = await page.locator(".tabs").boundingBox();
    const toolsBounds = await page.locator(".scene-tools").boundingBox();
    expect(tabBounds.x + tabBounds.width).toBeLessThanOrEqual(toolsBounds.x);
    await page.locator('[data-tab="vision"]').click();
  }
  expect(errors).toEqual([]);
});

test("mocked camera contract: an old response cannot overwrite the latest capture", async ({
  page,
}) => {
  const { snapshot, metadata } = await visionFixture(page);
  snapshot.observation_mode = "rgbd";
  snapshot.perception = { ...metadata };
  let releaseOld;
  const oldResponse = new Promise((resolve) => {
    releaseOld = resolve;
  });
  await page.route("**/api/perception?*", async (route) => {
    const capture = new URL(route.request().url()).searchParams.get(
      "capture_id",
    );
    if (capture === "camera-1") {
      await oldResponse;
      return route.fulfill({
        json: { ...metadata, capture_id: "camera-1", latency_ms: 999 },
      });
    }
    return route.fulfill({ json: { ...metadata } });
  });
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  const started = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/perception",
  );
  await page.locator('[data-tab="vision"]').click();
  await started;
  metadata.capture_id = "camera-2";
  snapshot.perception = { ...metadata };
  await expect(page.locator("#vision-image")).toBeVisible();
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /capture_id=camera-2/,
  );
  releaseOld();
  await nextPoll(page);
  await expect(page.locator("#vision-latency")).toHaveText("18.4 ms");
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /capture_id=camera-2/,
  );
});

test("mocked camera contract: failed reads stay explicit and retry only on request", async ({
  page,
}) => {
  const { snapshot, metadata } = await visionFixture(page);
  snapshot.observation_mode = "rgbd";
  snapshot.perception = { ...metadata };
  let attempts = 0;
  await page.route("**/api/perception?*", (route) => {
    attempts++;
    return attempts === 1
      ? route.fulfill({ status: 409, json: { detail: "感知帧已更新" } })
      : route.fulfill({ json: metadata });
  });
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-status")).toHaveText("感知帧已更新");
  await expect(page.locator("#vision-image")).toHaveCount(0);
  await nextPoll(page);
  expect(attempts).toBe(1);
  await page.locator("#vision-retry").click();
  await expect(page.locator("#vision-image")).toBeVisible();
  await expect(page.locator("#vision-retry")).toBeHidden();
  expect(attempts).toBe(2);
});
