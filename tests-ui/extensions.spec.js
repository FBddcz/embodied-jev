import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function resetBaseline(page) {
  const state = await (await page.request.get("/api/state")).json();
  const response = await page.request.post("/api/reset", {
    data: {
      expected_episode_id: state.id,
      provider: "baseline",
      task: "transfer",
    },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function openExtensions(page) {
  await page.goto("/");
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator("#extensions-open").click();
  await expect(page.locator("#ext-new-profile")).toBeVisible();
}

test("named API profiles save without calling models or storing keys in the browser", async ({
  page,
}, testInfo) => {
  const before = await resetBaseline(page);
  await page.setViewportSize({ width: 1440, height: 960 });
  await openExtensions(page);
  const defaultConnections = await (
    await page.request.get("/api/connections")
  ).json();
  const testKey = "ui-profile-placeholder-private-key";
  const errors = [];
  const modelRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      /\/api\/(decision\/probe|connections\/.+\/test|control\/(start|step)|comparison\/control\/start)/.test(
        request.url(),
      )
    )
      modelRequests.push(request.url());
  });
  await page.locator("#ext-new-profile").click();
  await expect(page.locator("#connection-title")).toHaveText("新建模型配置");
  await expect(page.locator("#connection-save")).toBeHidden();
  await page.locator("#profile-name").fill("测试平台 A · OpenAI 兼容");
  await page.locator("#api-url").fill("https://ui-profile.invalid/v1");
  await page.locator("#api-model").fill("profile-test-model");
  await page.locator("#api-key").fill(testKey);
  await expect(page.locator("#api-key")).toHaveAttribute("type", "password");
  await page.locator("#connection-profile").click();
  await expect(page.locator("#connection-dialog")).toBeHidden();
  await expect(page.locator("#api-key")).toHaveValue("");
  await expect(page.locator("#ext-profile-list")).toContainText("测试平台 A");
  const profileResponse = await page.request.get("/api/model-profiles");
  const { profiles } = await profileResponse.json();
  const profile = profiles.find(
    (value) => value.name === "测试平台 A · OpenAI 兼容",
  );
  expect(profile.key_configured).toBe(true);
  expect(profile).not.toHaveProperty("key");
  expect(profile).not.toHaveProperty("api_key");
  expect(await profileResponse.text()).not.toContain(testKey);
  expect(await (await page.request.get("/api/connections")).json()).toEqual(
    defaultConnections,
  );
  const afterSave = await (await page.request.get("/api/state")).json();
  expect(afterSave.id).toBe(before.id);
  expect(afterSave.cycles).toBe(0);
  expect(modelRequests).toEqual([]);
  await page.reload();
  await expect(page.locator("#loading")).toHaveClass(/hidden/);
  await page.locator("#extensions-open").click();
  await expect(page.locator("#ext-profile-list")).toContainText("测试平台 A");
  await expect(page.locator("#ext-profile-storage")).toContainText(
    "刷新页面不会丢失",
  );
  expect((await (await page.request.get("/api/state")).json()).id).toBe(
    before.id,
  );

  await page
    .locator(
      `#ext-profile-list [data-profile="${profile.id}"][data-action="edit"]`,
    )
    .click();
  await expect(page.locator("#api-key")).toHaveValue("");
  await expect(page.locator("#key-state")).toHaveText("已配置");
  await expect(page.locator("#connection-storage")).toContainText(
    "刷新页面不会丢失",
  );
  await page.locator("#profile-name").fill("平台 A · 已命名");
  await page.locator("#connection-profile").click();
  await expect(page.locator("#connection-dialog")).toBeHidden();
  await page
    .locator(
      `#ext-profile-list [data-profile="${profile.id}"][data-action="use"]`,
    )
    .click();
  await expect(page.locator("#ext-message")).toContainText("尚未调用模型");
  await expect
    .poll(
      async () =>
        (await (await page.request.get("/api/state")).json()).profile_id,
    )
    .toBe(profile.id);
  await page.locator("#workbench-view").click();
  await expect(page.locator("#provider")).toHaveValue(`profile:${profile.id}`);
  await expect(page.locator("#provider-note")).toContainText("平台 A · 已命名");
  await page.locator("#extensions-open").click();
  await page.locator("#ext-target").selectOption("comparison");
  await page
    .locator(
      `#ext-profile-list [data-profile="${profile.id}"][data-action="use"]`,
    )
    .click();
  await expect(page.locator("#ext-message")).toContainText("尚未调用模型");
  await page.locator("#comparison-open").click();
  await expect(page.locator("#cmp-provider-0")).toHaveValue(
    `profile:${profile.id}`,
  );
  await expect(page.locator("#cmp-model-0")).toBeEnabled();
  await page.locator("#extensions-open").click();

  await page.locator("#ext-presets > summary").click();
  await page
    .locator("#ext-user-context")
    .fill(JSON.stringify({ note: testKey }));
  await page.locator("#ext-save-preset").click();
  await expect(page.locator("#ext-message")).toContainText(/API Key|密钥/);
  expect(
    await page.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    ),
  ).not.toContain(testKey);
  expect(await (await page.request.get("/api/export")).text()).not.toContain(
    testKey,
  );
  await page.locator("#ext-user-context").fill("{}");
  await page.locator("#ext-save-preset").click();
  await expect(page.locator("#ext-message")).toContainText("已保存到本浏览器");
  await page.locator("#ext-presets > summary").click();
  await page.screenshot({
    path: testInfo.outputPath("extensions-models-desktop.png"),
    fullPage: true,
  });
  for (const width of [390, 330]) {
    await page.setViewportSize({ width, height: 844 });
    await page
      .locator(
        `#ext-profile-list [data-profile="${profile.id}"][data-action="edit"]`,
      )
      .click();
    await expect(page.locator("#api-key")).toHaveValue("");
    expect(
      await page
        .locator("#connection-dialog")
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`named-profile-${width}.png`),
      fullPage: true,
    });
    await page.locator("#connection-close").click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  expect(errors).toEqual([]);
  expect(modelRequests).toEqual([]);
  await resetBaseline(page);
});

test("portable presets validate, round trip and change real geometry without inference", async ({
  page,
}, testInfo) => {
  await resetBaseline(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openExtensions(page);
  await page.locator("#ext-presets > summary").click();
  await page.locator("#ext-preset-name").fill("侧向搬运实验");
  await page.locator("#ext-source-x").fill("0.42");
  await page.locator("#ext-source-y").fill("-0.18");
  await page.locator("#ext-target-x").fill("0.46");
  await page.locator("#ext-target-y").fill("0.20");
  await page
    .locator("#ext-user-context")
    .fill('{"instruction":"优先避免障碍接触"}');
  await page.locator("#ext-save-preset").click();
  await expect(page.locator("#ext-message")).toContainText("已保存到本浏览器");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#ext-export-preset").click(),
  ]);
  const exported = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(exported.format).toBe("embodied-jev-preset-v1");
  expect(Object.keys(exported).sort()).toEqual([
    "format",
    "name",
    "scene_config",
    "task",
    "user_context",
  ]);
  expect(exported.scene_config.source_xy).toEqual([0.42, -0.18]);
  await page.locator("#ext-preset-name").fill("尚未保存的草稿");
  await page.locator("#ext-import-file").setInputFiles({
    name: "preset.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(exported)),
  });
  await expect(page.locator("#ext-preset-name")).toHaveValue("侧向搬运实验");
  await page.locator("#ext-apply-preset").click();
  await expect(page.locator("#ext-message")).toContainText("预设已应用");
  const applied = await (await page.request.get("/api/state")).json();
  expect(applied.scene_config.source_xy).toEqual([0.42, -0.18]);
  expect(applied.frame.observation.object[0]).toBeCloseTo(0.42, 3);
  expect(applied.frame.observation.object[1]).toBeCloseTo(-0.18, 3);
  expect(applied.frame.observation.destination.slice(0, 2)).toEqual([
    0.46, 0.2,
  ]);
  expect(applied.user_context).toEqual(exported.user_context);
  expect(applied.status).toBe("idle");
  expect(applied.cycles).toBe(0);
  expect(applied.model_calls).toBe(0);

  const comparisonBefore = await (
    await page.request.get("/api/comparison")
  ).json();
  await page.locator("#ext-target").selectOption("comparison");
  await page.locator("#ext-apply-preset").click();
  await expect(page.locator("#ext-message")).toContainText("预设已应用");
  await page.locator("#comparison-open").click();
  await expect(page.locator("#cmp-task")).toHaveValue("transfer");
  await expect(page.locator("#cmp-preset-label")).toContainText("侧向搬运实验");
  expect((await (await page.request.get("/api/comparison")).json()).id).toBe(
    comparisonBefore.id,
  );
  await page.locator("#extensions-open").click();
  for (const [name, value, message] of [
    [
      "unknown.json",
      {
        ...exported,
        scene_config: { ...exported.scene_config, goal: "wrong" },
      },
      "不支持的字段",
    ],
    [
      "secret.json",
      { ...exported, user_context: { api_key: "not-for-export" } },
      "密钥字段",
    ],
  ]) {
    await page.locator("#ext-import-file").setInputFiles({
      name,
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(value)),
    });
    await expect(page.locator("#ext-message")).toContainText(message);
  }
  await page.locator("#ext-user-context").fill('{"amount":1e999}');
  await page.locator("#ext-save-preset").click();
  await expect(page.locator("#ext-message")).toContainText("非有限数值");
  await page
    .locator("#ext-user-context")
    .fill(JSON.stringify(exported.user_context));
  await page.locator("#ext-save-preset").click();
  await expect(page.locator("#ext-message")).toContainText("已保存到本浏览器");
  await page.screenshot({
    path: testInfo.outputPath("extensions-preset-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 330, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("extensions-preset-mobile.png"),
    fullPage: true,
  });
  await resetBaseline(page);
});

test("input probe shows an actual isolated baseline choice without moving the robot", async ({
  page,
}, testInfo) => {
  const before = await resetBaseline(page);
  await page.setViewportSize({ width: 1440, height: 960 });
  await openExtensions(page);
  await page.locator("#ext-probe > summary").click();
  await page
    .locator("#ext-probe-observation")
    .fill('{"gripper":"open","tcp":[0.3,0.1,0.2],"object_reachable":true}');
  await page
    .locator("#ext-probe-options")
    .fill(
      '{"hold":"Keep the current pose","approach":"Move above the object"}',
    );
  const requestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/api/decision/probe"),
  );
  await page.locator("#ext-probe-run").click();
  const request = await requestPromise;
  expect(request.postDataJSON().provider).toBe("baseline");
  expect(request.postDataJSON().observation.tcp).toEqual([0.3, 0.1, 0.2]);
  await expect(page.locator("#ext-probe-summary")).toHaveText(
    "选择 hold · 无模型调用",
  );
  await expect(page.locator("#ext-message")).toContainText(
    "固定选择第一个候选",
  );
  const after = await (await page.request.get("/api/state")).json();
  expect(after.id).toBe(before.id);
  expect(after.cycles).toBe(0);
  expect(after.frame.qpos).toEqual(before.frame.qpos);
  await page.locator("#ext-probe-result summary").click();
  await expect(page.locator("#ext-probe-json")).toContainText(
    '"decision_input": null',
  );
  await page.screenshot({
    path: testInfo.outputPath("input-probe-desktop.png"),
    fullPage: true,
  });
  for (const width of [1024, 768, 390, 330]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

for (const [moduleName, trigger, ready] of [
  ["comparison", "#comparison-open", "#cmp-start"],
  ["extensions", "#extensions-open", "#ext-new-profile"],
]) {
  test(`${moduleName} module failure offers explicit reload and preserves unsaved password until requested`, async ({
    page,
  }) => {
    const before = await resetBaseline(page);
    await page.goto("/");
    await expect(page.locator("#loading")).toHaveClass(/hidden/);
    let releaseFailure;
    const released = new Promise((resolve) => {
      releaseFailure = resolve;
    });
    const matcher = new RegExp(`/assets/${moduleName}-[^/]+\\.js$`);
    await page.route(matcher, async (route) => {
      await released;
      await route.abort();
    });
    const requested = page.waitForRequest((request) =>
      matcher.test(request.url()),
    );
    let navigations = 0;
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) navigations++;
    });
    await page.locator(trigger).click();
    await requested;
    await page.locator("#workbench-view").click();
    await page.locator("#model-connect").click();
    await expect(page.locator("#connection-dialog")).toBeVisible();
    await page.locator("#api-key").fill("unsaved-placeholder-key");
    releaseFailure();
    await expect(
      page.locator(`#${moduleName}-view .module-reload`),
    ).toHaveCount(1);
    await expect(page.locator("#api-key")).toHaveValue(
      "unsaved-placeholder-key",
    );
    expect(navigations).toBe(0);
    await page.locator("#connection-close").click();
    await page.locator(trigger).click();
    await expect(page.locator(`#${moduleName}-view`)).toContainText(
      "页面已更新或模块加载失败",
    );
    await page.unroute(matcher);
    await page.locator(`#${moduleName}-view .module-reload`).click();
    await expect(page.locator("#loading")).toHaveClass(/hidden/);
    await page.locator(trigger).click();
    await expect(page.locator(ready)).toBeVisible();
    expect(navigations).toBe(1);
    const after = await (await page.request.get("/api/state")).json();
    expect(after.id).toBe(before.id);
    expect(after.cycles).toBe(0);
  });
}
