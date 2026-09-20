import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function openComparison(page) {
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator("#comparison-open").click();
  await expect(page.locator("#cmp-start")).toBeVisible();
  await expect(page.locator("#cmp-start")).toBeEnabled();
}

async function canvasChecksum(canvas) {
  return canvas.evaluate((element) => {
    const copy = document.createElement("canvas");
    copy.width = 120;
    copy.height = 80;
    const context = copy.getContext("2d");
    context.drawImage(element, 0, 0, 120, 80);
    const pixels = context.getImageData(0, 0, 120, 80).data;
    let dark = 0,
      checksum = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (
        pixels[index] < 150 &&
        pixels[index + 1] < 150 &&
        pixels[index + 2] < 150
      )
        dark++;
      checksum =
        (checksum + pixels[index] * (index + 1) + pixels[index + 1]) %
        1000000007;
    }
    return { dark, checksum };
  });
}

test("two real baselines run sequentially, pause, replay by simulation time and export", async ({
  page,
}, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const mainBefore = await (await page.request.get("/api/state")).json();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await openComparison(page);
  await expect(page.locator("#cmp-mode")).toHaveValue("sequential");
  await expect(page.locator("#cmp-provider-0")).toHaveValue("baseline");
  await expect(page.locator("#cmp-model-0")).toBeDisabled();
  await expect(
    page.locator('#cmp-provider-0 option[value="jev"]'),
  ).toHaveJSProperty("disabled", true);
  await page.locator("#cmp-start").click();
  await expect(page.locator("#cmp-status")).toHaveText("运行中");
  await expect(page.locator(".comparison-card canvas")).toHaveCount(2);
  await expect
    .poll(
      async () =>
        (await canvasChecksum(page.locator(".comparison-card canvas").first()))
          .dark,
    )
    .toBeGreaterThan(10);
  const firstState = await (await page.request.get("/api/comparison")).json();
  expect(firstState.mode).toBe("sequential");
  expect(firstState.lanes[1].status).toBe("queued");
  await page.locator("#cmp-pause").click();
  await expect(page.locator("#cmp-status")).toHaveText("已暂停");
  const pausedState = await (await page.request.get("/api/comparison")).json();
  expect(
    pausedState.lanes.filter((lane) => lane.status === "running"),
  ).toHaveLength(0);
  const checksum = (
    await canvasChecksum(page.locator(".comparison-card canvas").first())
  ).checksum;
  await page.locator('.comparison-card [data-camera="top"]').first().click();
  await expect
    .poll(
      async () =>
        (await canvasChecksum(page.locator(".comparison-card canvas").first()))
          .checksum,
    )
    .not.toBe(checksum);
  await page.locator('.comparison-card [data-camera="home"]').first().click();
  await page.locator("#cmp-pause").click();
  await expect(page.locator("#cmp-status")).toHaveText("全部结束", {
    timeout: 90000,
  });
  const finished = await (await page.request.get("/api/comparison")).json();
  await expect(page.locator("#cmp-time")).toHaveText(
    `${finished.replay.max_time.toFixed(2)} s`,
  );
  await expect
    .poll(async () => Number(await page.locator("#cmp-timeline").inputValue()))
    .toBeCloseTo(finished.replay.max_time, 8);
  expect(finished.lanes.map((lane) => lane.session.status)).toEqual([
    "completed",
    "completed",
  ]);
  expect(
    finished.lanes.every(
      (lane) => lane.session.cycles === 8 && lane.session.model_calls === 0,
    ),
  ).toBe(true);
  await expect(page.locator(".lane-source").first()).toHaveText(
    "规则选择 · 无模型概率",
  );
  await page.screenshot({
    path: testInfo.outputPath("comparison-desktop.png"),
    fullPage: true,
  });
  await page.locator("#cmp-timeline").evaluate((input) => {
    input.value = "0";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#cmp-time-mode")).toContainText("录制回放");
  await expect(page.locator(".lane-phase").first()).toContainText("等待决策");
  await page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/api/comparison",
  );
  await expect(page.locator("#cmp-timeline")).toHaveValue("0");
  await expect(page.locator("#cmp-time")).toHaveText("0.00 s");
  const replay = await (
    await page.request.get(
      `/api/comparison/replay?time=0&comparison_id=${finished.id}`,
    )
  ).json();
  for (const lane of replay.lanes)
    await expect(
      page.locator(`[data-lane="${lane.id}"] .lane-pose`),
    ).toHaveText(
      lane.frame.observation.tcp.map((value) => value.toFixed(3)).join(" / "),
    );
  await page.locator("#cmp-replay-play").click();
  await expect
    .poll(() => page.locator("#cmp-timeline").inputValue())
    .not.toBe("0");
  await page.locator("#cmp-replay-play").click();
  await page.locator("#cmp-live").click();
  await expect(page.locator("#cmp-time-mode")).toHaveText(
    "实时 · 各路独立推进",
  );
  await expect(page.locator("#cmp-time")).toHaveText(
    `${finished.replay.max_time.toFixed(2)} s`,
  );
  await expect
    .poll(async () => Number(await page.locator("#cmp-timeline").inputValue()))
    .toBeCloseTo(finished.replay.max_time, 8);
  for (const [width, height] of [
    [1024, 1000],
    [768, 1024],
    [390, 844],
    [330, 812],
    [1440, 1100],
  ]) {
    await page.setViewportSize({ width, height });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    for (const canvas of await page.locator(".comparison-card canvas").all()) {
      await expect
        .poll(() =>
          canvas.evaluate(
            (element) =>
              element.clientWidth === element.parentElement.clientWidth &&
              element.clientHeight === element.parentElement.clientHeight,
          ),
        )
        .toBe(true);
    }
    for (const card of await page.locator(".comparison-card").all())
      expect(
        await card.evaluate(
          (element) => element.scrollWidth <= element.clientWidth,
        ),
      ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`comparison-${width}.png`),
      fullPage: true,
    });
  }
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#cmp-export").click(),
  ]);
  const exported = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(exported.format).toBe("embodied-jev-comparison-v1");
  expect(exported.lanes).toHaveLength(2);
  expect(exported.time_basis).toBe("elapsed_simulation_seconds");
  await page.locator("#workbench-view").click();
  await expect(page.locator("#viewport")).toBeVisible();
  const mainAfter = await (await page.request.get("/api/state")).json();
  expect(mainAfter.id).toBe(mainBefore.id);
  expect(mainAfter.cycles).toBe(mainBefore.cycles);
  expect(errors).toEqual([]);
});

test("three real lanes cap parallel work at two, reject duplicate start and stop cleanly", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  let previous = await (await page.request.get("/api/comparison")).json();
  if (!previous.id)
    previous = await (
      await page.request.post("/api/comparison", {
        data: {
          expected_comparison_id: null,
          lanes: [{ provider: "baseline" }, { provider: "baseline" }],
        },
      })
    ).json();
  let releaseScenes;
  const sceneGate = new Promise((resolve) => {
    releaseScenes = resolve;
  });
  let waitingScenes = 0,
    creates = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/comparison"
    )
      creates++;
  });
  await page.route("**/api/comparison/scene/**", async (route) => {
    if (
      new URL(route.request().url()).searchParams.get("comparison_id") ===
      previous.id
    ) {
      waitingScenes++;
      await sceneGate;
    }
    await route.continue();
  });
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator("#comparison-open").click();
  try {
    await expect.poll(() => waitingScenes).toBe(previous.lanes.length);
    await expect(page.locator("#cmp-status")).toHaveText("加载场景…");
    await expect(page.locator("#cmp-start")).toBeDisabled();
    await expect(page.locator("#cmp-count")).toBeDisabled();
    await expect(page.locator("#cmp-mode")).toBeDisabled();
    await expect(page.locator("#cmp-provider-0")).toBeDisabled();
    await page
      .locator("#cmp-start")
      .evaluate((button) =>
        button.dispatchEvent(new MouseEvent("click", { bubbles: true })),
      );
    expect(creates).toBe(0);
  } finally {
    releaseScenes();
  }
  await expect(page.locator("#cmp-start")).toBeEnabled();
  await page.unroute("**/api/comparison/scene/**");
  if ((await page.locator("#cmp-setup").getAttribute("open")) === null)
    await page.locator("#cmp-setup > summary").click();
  await page.locator("#cmp-count").selectOption("3");
  await page.locator("#cmp-mode").selectOption("parallel");
  await page.locator("#cmp-start").click();
  await page
    .locator("#cmp-start")
    .evaluate((button) =>
      button.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
  await expect(page.locator("#cmp-status")).toHaveText("运行中");
  await expect(page.locator(".comparison-card")).toHaveCount(3);
  expect(creates).toBe(1);
  const running = await (await page.request.get("/api/comparison")).json();
  expect(running.max_parallel).toBe(2);
  expect(
    running.lanes.filter((lane) => lane.status === "running").length,
  ).toBeLessThanOrEqual(2);
  expect(running.lanes[2].status).toBe("queued");
  await page.locator("#cmp-stop").click();
  await expect(page.locator("#cmp-status")).toHaveText("已停止");
  await expect(page.locator("#cmp-start")).toBeEnabled();
  await expect(page.locator("#cmp-stop")).toBeDisabled();
});
