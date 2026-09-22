import { test, expect } from "@playwright/test";

test("published videos, result assets and category navigation work", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#run=metaworld-hierarchy");
  await expect(page.getByRole("heading", { name: "六局任务，同屏对照。" })).toBeVisible();
  await expect(page.locator("#metrics")).toContainText("$0.0183");
  const catalog = await (await request.get("/catalog.json")).json();
  for (const item of catalog.experiments) {
    for (const field of ["video", "poster", "chart", "download"]) {
      if (item[field]) expect((await request.head("/" + item[field])).status()).toBe(200);
    }
  }
  await page.getByRole("button", { name: "Panda · 机制演示", exact: true }).click();
  await expect(page.locator("#cards button")).toHaveCount(3);
  await page.getByRole("button", { name: /看图，抓取，再放下/ }).click();
  await expect(page.locator("#probability-note")).toHaveText("此接口未提供概率");
  await page.getByRole("button", { name: "跳到决策 5", exact: true }).click();
  await expect(page.locator("#decision-index")).toHaveText("05 / 32");
  await page.getByLabel("播放速度", { exact: true }).selectOption("4");
  await expect.poll(() => page.locator("video").evaluate((v) => v.playbackRate)).toBe(4);
  await page.getByRole("button", { name: /Jev：一步一步搬运/ }).click();
  await expect(page.locator("#probability-note")).toHaveText("接口返回概率 · 非成功率");
  await expect(page.locator(".probability-row")).toHaveCount(4);
  await page.getByRole("button", { name: "Meta-World · 标准任务", exact: true }).click();
  await expect(page.locator("#chart-section")).toBeVisible();
  expect(errors).toEqual([]);
});

for (const width of [390, 1280]) {
  test(`gallery fits ${width}px and exposes real decisions`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/#run=jev-hierarchical");
    await expect(page.locator("#decision-index")).toHaveText("01 / 88");
    await expect(page.getByRole("button", { name: "下一个决策" })).toBeEnabled();
    await page.getByRole("button", { name: "下一个决策" }).click();
    await expect(page.locator("#decision-index")).toHaveText("02 / 88");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `playwright-results/site/gallery-${width}.png`, fullPage: true });
  });
}

test("LIBERO replays expose both real decision tracks on the shared clock", async ({ page, request }) => {
  const catalog = await (await request.get("/catalog.json")).json();
  const experiment = catalog.experiments.find((item) => item.id === "libero-drawer");
  await page.goto("/#run=libero-drawer");
  await expect(page.locator("#experiment-title")).toHaveText("关上顶层抽屉。");
  await expect(page.locator("#stage")).toHaveText("等待首个决策");
  await page.getByRole("button", { name: "跳到决策 1", exact: true }).click();
  await expect(page.locator("#decision-index")).toHaveText("01 / " + experiment.decision_tracks[0].decisions.length);
  await expect(page.locator("#probability-note")).toHaveText("此接口未提供概率");
  await page.getByLabel("查看哪组决策").selectOption("1");
  await page.getByRole("button", { name: "跳到决策 1", exact: true }).click();
  await expect(page.locator("#probability-note")).toHaveText("接口返回概率 · 非成功率");
  await expect(page.locator(".probability-row")).toHaveCount(7);
  await page.getByRole("button", { name: "LIBERO · 视觉协作", exact: true }).click();
  await expect(page.locator("#cards button")).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "playwright-results/site/libero-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: "playwright-results/site/libero-desktop.png", fullPage: true });
});
