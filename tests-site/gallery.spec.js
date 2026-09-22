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
  await expect(page.locator("#cards button")).toHaveCount(catalog.experiments.filter((item) => item.category === "libero").length);
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "playwright-results/site/libero-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: "playwright-results/site/libero-desktop.png", fullPage: true });
});

async function plateCatalog(page, request) {
  const catalog = await (await request.get("/catalog.json")).json();
  const drawer = catalog.experiments.find((item) => item.id === "libero-drawer");
  const microwave = catalog.experiments.find((item) => item.id === "libero-microwave");
  const decision = (time, index, probabilities = {}) => ({
    time, index, label: "候选选择：c1_cautious", stage: "c1_cautious",
    intent: "重新接近可见盘沿 · 速度档：谨慎（40% 幅度）",
    evidence: "盘子的位置未推进；轴方向由共享代码伺服计算。",
    latency_ms: 1200, tcp: [.1 * index, .2, .3], channels: {}, probabilities,
    selection: "c1_cautious", selection_profile: "cautious",
    servo_choices: { x: "positive", y: "hold", z: "negative" },
    control_source: "deterministic numeric servo",
  });
  const plate = { ...drawer, id: "libero-supervisor-plate-push-plate", title: "v2 · 将盘子推到炉前。",
    kicker: "LIBERO / CANDIDATE SUPERVISOR V2", badge: "v2 fixture · 仅验证页面",
    decision_tracks: [
      { id: "gpt6", label: "纯 GPT-6", decisions: [decision(1, 1), decision(3, 2)] },
      { id: "gpt6-jev", label: "GPT-6 + Jev", decisions: [
        decision(1.2, 1, { c0_normal: .2, c1_cautious: .7, reobserve: .1 }),
        decision(4, 2, { c0_normal: .1, c1_cautious: .8, reobserve: .1 }),
      ] },
    ],
  };
  catalog.experiments = [plate, ...catalog.experiments.filter((item) =>
    !["libero-v2-microwave", plate.id].includes(item.id))];
  await page.route("**/catalog.json", (route) => route.fulfill({ json: catalog }));
  return { drawer, microwave, plate };
}

test("plate candidates never expose future choices or servo probabilities", async ({ page, request }) => {
  await plateCatalog(page, request);
  await page.goto("/#run=libero-supervisor-plate-push-plate");
  await expect(page).toHaveURL(/#run=libero-supervisor-plate-push-plate$/);
  await expect(page.locator("#experiment-title")).toHaveText("v2 · 将盘子推到炉前。");
  await page.getByLabel("查看哪组决策").selectOption("1");
  await expect(page.locator("#stage")).toHaveText("等待首个决策");
  await expect(page.locator("#decision-index")).toHaveText("00 / 2");
  await expect(page.locator(".probability-row")).toHaveCount(0);
  await expect(page.locator("#timeline button")).toHaveCount(2);
  await page.getByRole("button", { name: "跳到决策 1", exact: true }).click();
  await expect(page.locator("#stage")).toHaveText("c1_cautious");
  await expect(page.locator("#intent")).toContainText("谨慎");
  await expect(page.locator("#evidence")).toContainText("代码伺服");
  await expect(page.locator(".probability-row")).toHaveCount(3);
  await expect(page.locator(".probability-row.selected")).toContainText("c1_cautious");
  await expect(page.locator(".probability-row.selected")).toContainText("70%");
  await expect(page.locator("#probabilities")).not.toContainText("positive");
  await expect(page.locator("#probability-note")).toHaveText("接口返回概率 · 非成功率");
  await page.getByLabel("查看哪组决策").selectOption("0");
  await page.getByRole("button", { name: "跳到决策 1", exact: true }).click();
  await expect(page.locator(".probability-row")).toHaveCount(0);
  await expect(page.locator("#probability-note")).toHaveText("此接口未提供概率");
});

test("plate leads while the successful GPT microwave replay and drawer retain their own URLs", async ({ page, request }) => {
  const { drawer, microwave, plate } = await plateCatalog(page, request);
  await page.goto("/");
  await expect(page).toHaveURL(/#run=libero-supervisor-plate-push-plate$/);
  await expect(page.locator("#experiment-title")).toHaveText(plate.title);
  await page.goto("/#run=libero-drawer");
  await expect(page).toHaveURL(/#run=libero-drawer$/);
  await expect(page.locator("#experiment-title")).toHaveText(drawer.title);
  await expect(page.locator("#video")).toHaveAttribute("src", drawer.video);
  await page.getByRole("button", { name: "LIBERO · 视觉协作", exact: true }).click();
  await expect(page.locator("#cards button")).toHaveCount(3);
  await page.goto("/#run=libero-microwave");
  await expect(page).toHaveURL(/#run=libero-microwave$/);
  await expect(page.locator("#experiment-title")).toHaveText(microwave.title);
  await expect(page.locator("#video")).toHaveAttribute("src", microwave.video);
  await expect(page.locator("#decision-track option")).toHaveCount(1);
  await expect(page.locator("#decision-track option")).toHaveText("纯 GPT-6");
  await page.goto("/#run=libero-v2-microwave");
  await expect(page).toHaveURL(/#run=libero-microwave$/);
  await expect(page.locator("#video")).toHaveAttribute("src", microwave.video);
  await page.locator("#cards button").filter({ hasText: plate.title }).click();
  await expect(page).toHaveURL(/#run=libero-supervisor-plate-push-plate$/);
  await expect(page.locator("#experiment-title")).toHaveText(plate.title);
  await page.locator("#cards button").filter({ hasText: drawer.title }).click();
  await expect(page).toHaveURL(/#run=libero-drawer$/);
  await expect(page.locator("#experiment-title")).toHaveText(drawer.title);
});

test("community shortcut preserves its anchor and shows an honest empty state", async ({ page }) => {
  await page.goto("/#community");
  await expect(page).toHaveURL(/#community$/);
  await expect(page.locator("#community-title")).toBeInViewport();
  await expect(page.locator("#community-count")).toHaveText("0 CONTRIBUTIONS");
  await expect(page.locator("#community-empty")).toBeVisible();
  await expect(page.getByRole("link", { name: /投稿 \/ 上传录像/ })).toHaveAttribute("href", /issues\/new\?template=community-reproduction.yml$/);
});

test("community replay shows attribution, task and unknown costs without affecting project results", async ({ page, request }) => {
  const catalog = await (await request.get("/catalog.json")).json();
  const projectCount = catalog.experiments.length;
  const item = { ...catalog.experiments[0], id: "community-test-record", origin: "community",
    title: "社区测试作品", author: { name: "测试作者 <b>literal</b>", github: "example-author", url: "https://github.com/example-author" },
    date: "2026-09-22", task: "推软块 · seed 2", method: "规则选择器", result: "partial",
    badge: "社区复现 · 测试作者 · 2026-09-22", poster: "", chart: "", decisions: [], decision_tracks: [],
    metrics: [{ label: "费用估算", value: "未提供" }],
    note: "社区作者自行报告，尚未经项目独立复核；不计入项目官方实验统计。",
  };
  catalog.experiments.push(item);
  await page.route("**/catalog.json", (route) => route.fulfill({ json: catalog }));
  await page.goto("/#community");
  await expect(page.locator("#cards button")).toHaveCount(projectCount);
  await expect(page.locator("#community-cards button")).toHaveCount(1);
  await expect(page.locator("#community-empty")).toBeHidden();
  await expect(page.locator("#community-cards .card-placeholder")).toBeVisible();
  await expect(page.locator("#community-cards")).toContainText("@example-author");
  await expect(page.locator("#community-cards")).toContainText("部分完成");
  await page.locator("#community-cards button").click();
  await expect(page).toHaveURL(/#run=community-test-record$/);
  await expect(page.locator("#contributor-details")).toContainText("测试作者 <b>literal</b>");
  await expect(page.locator("#contributor-details b")).toHaveCount(0);
  await expect(page.locator("#contributor-details")).toContainText("2026-09-22");
  await expect(page.locator("#contributor-details")).toContainText("推软块 · seed 2");
  await expect(page.locator("#contributor-details")).toContainText("规则选择器");
  await expect(page.locator("#contributor-details a")).toHaveAttribute("href", item.author.url);
  await expect(page.locator("#metrics")).toContainText("未提供");
  await expect(page.locator("#decision-panel")).toBeHidden();
  await expect(page.locator(".timeline-wrap")).toBeHidden();
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: /看图，抓取，再放下/ }).click();
  await expect(page.locator("#contributor-details")).toBeHidden();
  await expect(page.locator("#decision-panel")).toBeVisible();
});
