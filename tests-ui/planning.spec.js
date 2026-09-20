import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const pixel = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5N8AAAAASUVORK5CYII=",
  "base64",
);
const emptyZip = Buffer.from(
  "504b0506000000000000000000000000000000000000",
  "hex",
);

async function planningFixture(page) {
  // All service responses are fixtures: these tests verify the UI contract,
  // not model quality or the physical planner's success rate.
  const config = {
    tasks: Object.fromEntries(
      ["transfer", "stack", "barrier"].map((task) => [
        task,
        { goal: "将方块放入目标区域" },
      ]),
    ),
    providers: [
      { id: "baseline", name: "规则基线", ready: true },
      { id: "jev", name: "TypeSafe Jev", ready: true },
      { id: "chat", name: "OpenAI 兼容 API", ready: true },
      { id: "claude", name: "Claude 原生 API", ready: true },
    ],
  };
  const snapshot = {
    id: "mock-planning",
    task: "transfer",
    provider: "baseline",
    status: "idle",
    stage: "ready",
    phase: null,
    control_mode: "skills",
    observation_mode: "privileged",
    camera_views: [],
    intervention: null,
    interventions: [],
    shuffle_candidates: false,
    seed: 0,
    max_cycles: 30,
    threshold: 0.55,
    speed: 1.5,
    preview: true,
    scene_config: {},
    user_context: {},
    perception: null,
    cycles: 0,
    frame_count: 1,
    model_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    wall_seconds: 0,
    history: [],
    events: [],
    candidates: [],
    last_decision: null,
    frame: {
      time: 0,
      qpos: [],
      positions: {},
      rotations: {},
      observation: {
        tcp: [0.3, 0, 0.2],
        gripper: "open",
        finger_contacts: [],
        sim_seconds: 0,
        success: false,
      },
    },
  };
  const metadata = {
    capture_id: "capture-1",
    source: "vision",
    status: "ready",
    captured_at: "2026-09-20T12:00:00Z",
    sim_time: 0.12,
    latency_ms: 18.4,
    objects: [],
    camera_views: ["external", "wrist"],
  };
  const requests = { resets: [], images: [], comparisons: [], exports: [] };
  const archive = { status: 200 };
  let comparison = { id: null, status: "empty", lanes: [] };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const body =
      route.request().method() === "POST"
        ? route.request().postDataJSON()
        : null;
    let json;
    if (path === "/api/config") json = config;
    else if (path === "/api/model-profiles") json = { profiles: [] };
    else if (path === "/api/connections") json = {};
    else if (path === "/api/state") json = snapshot;
    else if (path === "/api/scene" || path.startsWith("/api/comparison/scene/"))
      json = { task: snapshot.task, geometries: [], meshes: {} };
    else if (path === "/api/reset") {
      requests.resets.push(body);
      Object.assign(snapshot, body, {
        id: `mock-planning-${requests.resets.length}`,
      });
      metadata.source = body.observation_mode;
      metadata.camera_views = [...body.camera_views];
      snapshot.perception = body.camera_views.length ? { ...metadata } : null;
      json = snapshot;
    } else if (path === "/api/perception") json = metadata;
    else if (path === "/api/export/cameras.zip") {
      requests.exports.push(url);
      if (archive.status !== 200)
        return route.fulfill({
          status: archive.status,
          json: { detail: "实验已更新，请重试导出。" },
        });
      return route.fulfill({
        contentType: "application/zip",
        headers: {
          "Content-Disposition": 'attachment; filename="observations.zip"',
        },
        body: emptyZip,
      });
    } else if (/^\/api\/perception\/.+\.png$/.test(path)) {
      requests.images.push(url);
      return route.fulfill({ contentType: "image/png", body: pixel });
    } else if (path === "/api/comparison" && body) {
      requests.comparisons.push(body);
      comparison = {
        id: `mock-comparison-${requests.comparisons.length}`,
        status: "idle",
        mode: body.mode,
        config: body,
        lanes: body.lanes.map((lane, index) => ({
          id: `lane-${index}`,
          ...lane,
          status: "idle",
          model: "Fixture multimodal model",
          session: { ...snapshot, ...body, ...lane },
        })),
        replay: { max_time: 0, common_time: 0 },
      };
      json = comparison;
    } else if (path === "/api/comparison") json = comparison;
    else if (path === "/api/comparison/control/start") json = comparison;
    else throw new Error(`Unmocked API request: ${path}`);
    await route.fulfill({ json });
  });
  return { snapshot, metadata, requests, archive };
}

async function boot(page) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.locator("#connection")).toContainText("已连接");
}

test("hierarchical setup, separate probabilities and historical decisions", async ({ page }, testInfo) => {
  const { snapshot, requests } = await planningFixture(page);
  await boot(page);
  await page.locator("#control-mode").selectOption("hierarchical");
  await expect.poll(() => requests.resets.at(-1)?.control_mode).toBe("hierarchical");
  expect(requests.resets.at(-1).max_cycles).toBe(160);
  await expect(page.locator("#control-help")).toContainText("子目标");
  const channel = (choice, alternatives) => ({ choice, probabilities: Object.fromEntries(
    alternatives.map(option => [option, option === choice ? .9 : .05])), selected_probability: .9 });
  const decision = { choice: "channels", probabilities: {}, selected_probability: null,
    model: "fixture-jev", model_call: true, latency_ms: 330,
    channel_decisions: { x: channel("hold", ["negative", "hold", "positive"]),
      y: channel("positive", ["negative", "hold", "positive"]),
      z: channel("hold", ["negative", "hold", "positive"]),
      gripper: channel("close", ["open", "hold", "close"]) } };
  const intent = { choice: "carry", probabilities: { carry: .8, lift: .2 }, model_call: true, latency_ms: 310 };
  const action = { id: "channels", label: "X 0 / Y +12 mm", delta_xyz: [0, .012, 0], channels: { y: "positive" }, admitted: true };
  Object.assign(snapshot, { provider: "jev", phase: "hierarchical", cycles: 2,
    last_decision: decision, last_intent: intent, candidates: [action], history: [{
      cycle: 1, phase: "hierarchical", label: "历史上升", intent: { ...intent, choice: "lift" },
      decision: { ...decision, channel_decisions: { ...decision.channel_decisions,
        z: channel("positive", ["negative", "hold", "positive"]) } }, action,
      candidates: [action], executed: true, before: { tcp: [.3, 0, .2] },
      after: { tcp: [.3, 0, .21], sim_seconds: .35, held: true }, decision_inputs: { phase: {}, action: {} },
    }] });
  await expect(page.locator("#intent-panel")).toBeVisible();
  await expect(page.locator("#intent-panel")).toContainText("当前子目标");
  await expect(page.locator("#intent-probabilities .selected")).toContainText("移向目标");
  await expect(page.locator("#probabilities .motor-channel")).toHaveCount(4);
  await expect(page.locator('#probabilities [data-channel="y"] .selected')).toContainText("90.0%");
  await expect(page.locator('#probabilities [data-channel="gripper"] .selected')).toContainText("闭合");
  await expect(page.locator("#decision-note")).toContainText("不合成为整步概率");
  await page.locator("#history-list > summary").click();
  await page.locator('[data-cycle="1"]').click();
  await expect(page.locator("#intent-probabilities .selected")).toContainText("抬升物体");
  await expect(page.locator('#probabilities [data-channel="z"] .selected')).toContainText("正方向");
  await page.locator("#decision-live").click();
  await expect(page.locator('#probabilities [data-channel="z"] .selected')).toContainText("保持");
  for (const channel of Object.values(decision.channel_decisions)) channel.probabilities = {};
  snapshot.last_decision = { ...decision };
  await expect(page.locator('#probabilities [data-channel="y"] .selected')).toContainText("已选择");
  await expect(page.locator("#probabilities .bar")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("hierarchical-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("comparison forwards hierarchical mode and displays all channels", async ({ page }) => {
  const { snapshot, requests } = await planningFixture(page);
  snapshot.last_intent = { choice: "lift", probabilities: { lift: .9, grasp: .1 }, model_call: true };
  snapshot.last_decision = { choice: "combined", model_call: true, probabilities: {},
    channel_decisions: Object.fromEntries(["x", "y", "z", "gripper"].map(name => [name,
      { choice: "hold", probabilities: { hold: .8 }, selected_probability: .8 }])) };
  await boot(page);
  await page.locator("#comparison-open").click();
  await page.locator(".comparison-advanced > summary").click();
  await page.locator("#cmp-control-mode").selectOption("hierarchical");
  await expect(page.locator("#cmp-budget")).toHaveValue("160");
  await page.locator("#cmp-start").click();
  await expect(page.locator(".comparison-card")).toHaveCount(2);
  expect(requests.comparisons[0].control_mode).toBe("hierarchical");
  await expect(page.locator(".lane-action .motor-channel")).toHaveCount(8);
  await expect(page.locator(".lane-phase").first()).toContainText("抬升");
});

test("mocked Jev preserves its reported choice and discloses probability mismatch", async ({ page }) => {
  const { snapshot } = await planningFixture(page);
  Object.assign(snapshot, {
    provider: "jev",
    control_mode: "incremental",
    cycles: 1,
    candidates: [
      { id: "y_neg_40", label: "Y -0.040 m", admitted: true, delta_xyz: [0, -0.04, 0] },
      { id: "z_neg_40", label: "Z -0.040 m", admitted: true, delta_xyz: [0, 0, -0.04] },
    ],
    last_decision: {
      choice: "y_neg_40", model: "jev-1.13.0", model_call: true, latency_ms: 310,
      probabilities: { y_neg_40: 0.18, z_neg_40: 0.19 }, selected_probability: 0.18,
      probability_warning: "choice_below_reported_max",
    },
  });
  await boot(page);
  await expect(page.locator("#probabilities .selected")).toContainText("Y -0.040 m");
  await expect(page.locator("#probabilities .selected")).toContainText("18.0%");
  await expect(page.locator("#probabilities")).toContainText("19.0%");
  await expect(page.locator("#decision-note")).toContainText("API 选择与公布概率排序不一致");
});

test("mocked planning settings validate vision and preserve modes across reset and task changes", async ({
  page,
}) => {
  const { requests } = await planningFixture(page);
  await boot(page);
  await page.locator("#observation-mode").selectOption("vision");
  await expect(page.locator("#toast")).toContainText("需要「逐步 XYZ」");
  await expect(page.locator("#observation-mode")).toHaveValue("privileged");
  expect(requests.resets).toHaveLength(0);
  await page.locator("#control-mode").selectOption("incremental");
  await expect(page.locator("#control-mode")).toHaveValue("incremental");
  await expect
    .poll(() => requests.resets.at(-1)?.control_mode)
    .toBe("incremental");
  expect(requests.resets.at(-1).max_cycles).toBe(100);
  await page.locator("#observation-mode").selectOption("vision");
  await expect(page.locator("#toast")).toContainText("Chat 或 Claude");
  await expect(page.locator("#observation-mode")).toHaveValue("privileged");
  await page.locator("#provider").selectOption("chat");
  await expect(page.locator("#observation-mode")).toBeEnabled();
  await page.locator("#observation-mode").selectOption("vision");
  await expect
    .poll(() => requests.resets.at(-1)?.observation_mode)
    .toBe("vision");
  expect(requests.resets.at(-1).control_mode).toBe("incremental");
  await page.locator('[data-task="barrier"]').click();
  await expect.poll(() => requests.resets.at(-1)?.task).toBe("barrier");
  await page.locator("#reset").click();
  await expect(page.locator("#reset")).toBeEnabled();
  expect(requests.resets.at(-1)).toMatchObject({
    control_mode: "incremental",
    observation_mode: "vision",
    camera_views: ["external", "wrist"],
    provider: "chat",
    max_cycles: 100,
  });
  await page.locator("#provider").selectOption("baseline");
  await expect(page.locator("#toast")).toContainText("Chat 或 Claude");
  await expect(page.locator("#provider")).toHaveValue("chat");
  await expect(page.locator("#budget")).toHaveAttribute("max", "200");
});

test("mocked direct-image decisions show evidence and exact delta with independent camera views", async ({
  page,
}, testInfo) => {
  const { snapshot, metadata, requests } = await planningFixture(page);
  Object.assign(snapshot, {
    provider: "chat",
    control_mode: "incremental",
    observation_mode: "vision",
    camera_views: ["external", "wrist"],
    phase: "incremental",
    perception: { ...metadata },
    cycles: 1,
    candidates: [
      {
        id: "move_x",
        label: "沿 X 方向移动",
        phase: "incremental",
        target: [0.33, 0, 0.2],
        delta_xyz: [0.03, 0, 0],
        gripper: "open",
        admitted: true,
      },
    ],
    last_decision: {
      choice: "move_x",
      intent: "靠近画面中的红色方块",
      visual_evidence: "方块位于夹爪右侧，目标盘在更远处。",
      image_sha256: "a".repeat(64),
      model: "Fixture multimodal model",
      model_call: true,
      latency_ms: 120,
      probabilities: {},
    },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await boot(page);
  await expect(page.locator("#input-positions")).toContainText("由图像判断");
  await expect(page.locator("#planning-intent")).toHaveText(
    "靠近画面中的红色方块",
  );
  await expect(page.locator("#planning-evidence")).toContainText(
    "方块位于夹爪右侧",
  );
  await expect(page.locator("#planning-action")).toHaveText(
    "ΔXYZ (+0.030, +0.000, +0.000) m · 夹爪张开",
  );
  await expect(page.locator("#planning-image")).toContainText("a".repeat(64));
  await expect(page.locator("#decision-note")).toContainText(
    "物理成功不等于已验证规划能力",
  );
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-image")).toBeVisible();
  await expect(page.locator("#vision-visibility")).toHaveText("由图像判断");
  await expect(page.locator("#vision-status")).not.toHaveClass(/has-alert/);
  await expect(page.locator("#vision-source-label")).toContainText(
    "多模态模型",
  );
  await expect(page.locator("#vision-note")).toContainText("并非实时视频");
  await expect(page.locator('[data-vision-channel="depth"]')).toBeDisabled();
  await page.locator('[data-vision-view="wrist"]').click();
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /view=wrist/,
  );
  await expect(page.locator("#vision-view-help")).toContainText("随机械臂移动");
  expect(requests.resets).toHaveLength(0);
  expect(requests.images.at(-1).pathname).toBe("/api/perception/rgb.png");
  // RGB-D permits depth; view and channel remain independent.
  snapshot.observation_mode = "rgbd";
  metadata.source = "rgbd";
  metadata.capture_id = "capture-2";
  snapshot.perception = { ...metadata };
  await expect(page.locator('[data-vision-channel="depth"]')).toBeEnabled();
  await page.locator('[data-vision-channel="depth"]').click();
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /depth\.png.*view=wrist/,
  );
  await page.locator('[data-vision-view="external"]').click();
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /depth\.png.*view=external/,
  );
  await expect(page.locator("#vision-time")).toHaveText("t = 0.12 s");
  for (const width of [1440, 768, 390, 330]) {
    await page.setViewportSize({ width, height: 960 });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    expect(
      await page
        .locator("#planning-output")
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true);
    await expect(page.locator('[data-vision-view="wrist"]')).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`planning-${width}.png`),
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
});

test("mocked comparison requires visual providers and forwards shared incremental settings", async ({
  page,
}) => {
  const { snapshot, requests } = await planningFixture(page);
  snapshot.phase = "incremental";
  snapshot.candidates = [
    {
      id: "up",
      label: "向上移动",
      phase: "incremental",
      delta_xyz: [0, 0, 0.02],
      gripper: null,
    },
  ];
  snapshot.last_decision = {
    choice: "up",
    intent: "提高夹爪以越过障碍",
    visual_evidence: "障碍物位于夹爪前方。",
    probabilities: {},
    model_call: true,
    latency_ms: 120,
  };
  await boot(page);
  await page.locator("#comparison-open").click();
  await expect(page.locator("#cmp-start")).toBeEnabled();
  await page.locator(".comparison-advanced > summary").click();
  await page.locator("#cmp-observation-mode").selectOption("vision");
  await page.locator("#cmp-start").click();
  await expect(page.locator("#cmp-message")).toContainText("需要「逐步 XYZ」");
  await page.locator("#cmp-control-mode").selectOption("incremental");
  await expect(page.locator("#cmp-budget")).toHaveValue("100");
  await page.locator("#cmp-start").click();
  await expect(page.locator("#cmp-message")).toContainText("每一路");
  expect(requests.comparisons).toHaveLength(0);
  await page.locator("#cmp-provider-0").selectOption("chat");
  await page.locator("#cmp-provider-1").selectOption("claude");
  await page.locator("#cmp-start").click();
  await expect(page.locator(".comparison-card")).toHaveCount(2);
  expect(requests.comparisons[0]).toMatchObject({
    control_mode: "incremental",
    observation_mode: "vision",
    max_cycles: 100,
  });
  await expect(page.locator(".lane-phase").first()).toHaveText(
    "提高夹爪以越过障碍",
  );
  await expect(page.locator(".lane-evidence").first()).toHaveText(
    "障碍物位于夹爪前方。",
  );
  await expect(page.locator(".lane-action-detail").first()).toHaveText(
    "ΔXYZ (+0.000, +0.000, +0.020) m · 夹爪保持",
  );
  await expect(page.locator(".lane-input-source").first()).toContainText(
    "物体/目标由图像判断",
  );
});

test("mocked evaluation settings persist, validate displacements and lock while running", async ({
  page,
}) => {
  const { snapshot, requests } = await planningFixture(page);
  snapshot.intervention = {
    kind: "target_shift",
    after_cycle: 7,
    delta_xy: [-0.02, 0.01],
  };
  snapshot.shuffle_candidates = true;
  await boot(page);
  await page.locator("#advanced-settings > summary").click();
  await expect(page.locator("#intervention-kind")).toHaveValue("target_shift");
  await expect(page.locator("#intervention-cycle")).toHaveValue("7");
  await expect(page.locator("#intervention-x")).toHaveValue("-0.02");
  await expect(page.locator("#intervention-y")).toHaveValue("0.01");
  await expect(page.locator("#shuffle-candidates")).toBeChecked();
  await expect(page.locator("#intervention-help")).toContainText(
    "不是模型动作",
  );
  await page.locator("#intervention-kind").selectOption("object_shift");
  await page.locator("#intervention-cycle").fill("5");
  await page.locator("#intervention-x").fill("0");
  await page.locator("#intervention-y").fill("0");
  await page.locator("#reset").click();
  await expect(page.locator("#toast")).toContainText("不能同时为零");
  expect(requests.resets).toHaveLength(0);
  await page.locator("#intervention-x").fill("0.07");
  await page.locator("#reset").click();
  await expect(page.locator("#toast")).toContainText("±0.06 米");
  expect(requests.resets).toHaveLength(0);
  await page.locator("#intervention-x").fill("0.04");
  await page.locator("#reset").click();
  await expect.poll(() => requests.resets.length).toBe(1);
  await expect(page.locator("#reset")).toBeEnabled();
  expect(requests.resets[0]).toMatchObject({
    intervention: { kind: "object_shift", after_cycle: 5, delta_xy: [0.04, 0] },
    shuffle_candidates: true,
  });
  await expect(page.locator("#intervention-x")).toHaveValue("0.04");
  snapshot.status = "running";
  for (const selector of [
    "#intervention-kind",
    "#intervention-cycle",
    "#intervention-x",
    "#intervention-y",
    "#shuffle-candidates",
  ])
    await expect(page.locator(selector)).toBeDisabled();
  snapshot.interventions = [
    {
      kind: "object_shift",
      after_cycle: 5,
      sim_time: 1.2,
      delta_xy: [0.04, 0],
    },
  ];
  await expect(page.locator("#intervention-status")).toContainText(
    "已注入 1 次外部评测扰动",
  );
  await expect(page.locator("#log-entries")).toContainText(
    "外部评测扰动：移动方块",
  );
  await expect(page.locator("#event-count")).toHaveText("0 步");
  snapshot.status = "idle";
  await expect(page.locator("#intervention-kind")).toBeEnabled();
  await page.locator("#intervention-kind").selectOption("none");
  await expect(page.locator("#intervention-fields")).toBeHidden();
  await page.locator("#shuffle-candidates").uncheck();
  await page.locator("#reset").click();
  await expect.poll(() => requests.resets.length).toBe(2);
  expect(requests.resets[1]).toMatchObject({
    intervention: null,
    shuffle_candidates: false,
  });
});

test("mocked camera archive download follows the current episode and requires a capture", async ({
  page,
}) => {
  const { snapshot, metadata, requests } = await planningFixture(page);
  await boot(page);
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-export")).toBeDisabled();
  Object.assign(snapshot, {
    id: "new-camera-episode",
    provider: "chat",
    control_mode: "incremental",
    observation_mode: "vision",
    camera_views: ["external", "wrist"],
    perception: { ...metadata },
  });
  await expect(page.locator("#vision-export")).toBeEnabled();
  await expect(page.locator(".vision-download-row")).toContainText(
    "全部 RGB 视角 + SHA-256 清单",
  );
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#vision-export").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  expect(await download.failure()).toBeNull();
  expect(await readFile(await download.path())).toEqual(emptyZip);
  expect(requests.exports).toHaveLength(1);
  expect(requests.exports[0].searchParams.get("episode_id")).toBe(
    "new-camera-episode",
  );
  expect(requests.resets).toHaveLength(0);
  snapshot.perception = null;
  await expect(page.locator("#vision-export")).toBeDisabled();
});

test("mocked camera configuration supports all four modes and keeps preview separate from model vision", async ({
  page,
}) => {
  const { snapshot, requests } = await planningFixture(page);
  await boot(page);
  await expect(page.locator("#camera-mode")).toHaveValue("none");
  await expect(page.locator("#camera-help")).toContainText("非视觉输入");
  await page.locator("#camera-mode").selectOption("external");
  await expect
    .poll(() => requests.resets.at(-1)?.camera_views)
    .toEqual(["external"]);
  expect(requests.resets.at(-1).observation_mode).toBe("privileged");
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-image")).toBeVisible();
  await expect(page.locator("#vision-source-label")).toContainText(
    "不发送模型",
  );
  await expect(page.locator("#vision-visibility")).toHaveText("仅预览");
  await expect(page.locator('[data-vision-view="external"]')).toBeVisible();
  await expect(page.locator('[data-vision-view="wrist"]')).toBeHidden();
  await page.locator("#observation-mode").selectOption("rgbd");
  await expect
    .poll(() => requests.resets.at(-1)?.observation_mode)
    .toBe("rgbd");
  expect(requests.resets.at(-1).camera_views).toEqual(["external"]);
  await page.locator("#camera-mode").selectOption("wrist");
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /view=wrist/,
  );
  await expect(page.locator('[data-vision-view="external"]')).toBeHidden();
  await expect(page.locator('[data-vision-view="wrist"]')).toBeVisible();
  expect(requests.resets.at(-1).camera_views).toEqual(["wrist"]);
  await page.locator("#reset").click();
  await expect(page.locator("#reset")).toBeEnabled();
  await expect(page.locator("#camera-mode")).toHaveValue("wrist");
  expect(requests.resets.at(-1).camera_views).toEqual(["wrist"]);
  await page.locator("#camera-mode").selectOption("both");
  await expect(page.locator('[data-vision-view="external"]')).toBeVisible();
  await expect(page.locator('[data-vision-view="wrist"]')).toBeVisible();
  expect(requests.resets.at(-1).camera_views).toEqual(["external", "wrist"]);
  const resetCount = requests.resets.length;
  await page.locator('[data-vision-view="external"]').click();
  await expect(page.locator("#vision-image")).toHaveAttribute(
    "src",
    /view=external/,
  );
  expect(requests.resets).toHaveLength(resetCount);
  snapshot.status = "running";
  await expect(page.locator("#camera-mode")).toBeDisabled();
  snapshot.status = "idle";
  await expect(page.locator("#camera-mode")).toBeEnabled();
  await page.locator("#camera-mode").selectOption("none");
  await expect(page.locator("#observation-mode")).toHaveValue("privileged");
  await expect.poll(() => requests.resets.at(-1)?.camera_views).toEqual([]);
  await expect(page.locator("#camera-help")).toContainText("无相机");
  await expect(page.locator("#observation-source")).toContainText("非视觉输入");
  await expect(page.locator("#vision-image")).toHaveCount(0);
  await expect(page.locator('[data-vision-view="external"]')).toBeHidden();
  await expect(page.locator('[data-vision-view="wrist"]')).toBeHidden();
  await page.locator("#control-mode").selectOption("incremental");
  await page.locator("#provider").selectOption("chat");
  await page.locator("#observation-mode").selectOption("vision");
  await expect(page.locator("#camera-mode")).toHaveValue("both");
  await expect
    .poll(() => requests.resets.at(-1)?.observation_mode)
    .toBe("vision");
  expect(requests.resets.at(-1).camera_views).toEqual(["external", "wrist"]);
});

test("mocked comparison forwards the camera subset and marks no-camera runs as nonvisual", async ({
  page,
}) => {
  const { requests } = await planningFixture(page);
  await boot(page);
  await page.locator("#comparison-open").click();
  await expect(page.locator("#cmp-start")).toBeEnabled();
  await page.locator(".comparison-advanced > summary").click();
  await expect(page.locator("#cmp-camera-mode")).toHaveValue("none");
  await page.locator("#cmp-observation-mode").selectOption("rgbd");
  await expect(page.locator("#cmp-camera-mode")).toHaveValue("both");
  await page.locator("#cmp-camera-mode").selectOption("wrist");
  await page.locator("#cmp-start").click();
  await expect(page.locator(".comparison-card")).toHaveCount(2);
  expect(requests.comparisons[0]).toMatchObject({
    camera_views: ["wrist"],
    observation_mode: "rgbd",
  });
  await page.locator("#cmp-setup > summary").click();
  await expect(page.locator("#cmp-camera-mode")).toHaveValue("wrist");
  await page.locator("#cmp-camera-mode").selectOption("none");
  await expect(page.locator("#cmp-observation-mode")).toHaveValue("privileged");
  await expect(page.locator("#cmp-camera-help")).toContainText("非视觉输入");
  await page.locator("#cmp-start").click();
  await expect.poll(() => requests.comparisons.length).toBe(2);
  expect(requests.comparisons[1]).toMatchObject({
    camera_views: [],
    observation_mode: "privileged",
  });
  await expect(page.locator(".lane-input-source").first()).toContainText(
    "非视觉输入",
  );
});

test("mocked archive server errors remain visible and cannot masquerade as successful downloads", async ({
  page,
}) => {
  const { snapshot, metadata, archive, requests } = await planningFixture(page);
  Object.assign(snapshot, {
    provider: "chat",
    control_mode: "incremental",
    observation_mode: "vision",
    camera_views: ["external"],
    perception: { ...metadata, camera_views: ["external"] },
  });
  archive.status = 409;
  const downloads = [];
  page.on("download", (download) => downloads.push(download));
  await boot(page);
  await page.locator('[data-tab="vision"]').click();
  await expect(page.locator("#vision-export")).toBeEnabled();
  await page.locator("#vision-export").click();
  await expect(page.locator("#toast")).toHaveText("实验已更新，请重试导出。");
  expect(requests.exports).toHaveLength(1);
  expect(downloads).toHaveLength(0);
  await expect(page.locator("#vision-export")).toBeEnabled();
});
