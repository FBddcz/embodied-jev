import { RobotScene } from "./robot-scene.js";
import {
  selectionOptions,
  selectionConfig,
  storageLabel,
  storageDescription,
} from "./model-options.js";
import {
  createIcons,
  ScanLine,
  SlidersHorizontal,
  Download,
  X,
  MoveUpRight,
  Layers2,
  Route,
  ChevronDown,
  Box,
  Braces,
  Scan,
  Focus,
  CircleCheck,
  Play,
  Pause,
  StepForward,
  Square,
  RotateCcw,
  GitBranch,
  PlugZap,
} from "lucide";
import "./style.css";

const icon = (name) => `<i data-lucide="${name}"></i>`;
const $ = (s) => document.querySelector(s);
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const phaseNames = {
  approach: "移至物体上方",
  descend: "下降对准",
  grasp: "闭合夹爪",
  lift: "抬升物体",
  carry: "移向目标",
  lower: "降低放置",
  release: "松开夹爪",
  withdraw: "向上撤离",
  recover: "张开重试",
  finish: "完成",
};
const stateNames = {
  idle: "待命",
  running: "运行中",
  paused: "已暂停",
  uncertain: "等待人工处理",
  completed: "验证通过",
  stopped: "已停止",
  error: "执行异常",
  exhausted: "预算耗尽",
  stalled: "决策停滞",
};
const stageNames = {
  ready: "就绪",
  deciding: "决策中",
  previewing: "动作预演",
  executing: "执行中",
  observing: "读取反馈",
  verified: "已验证",
};
let config,
  profileValues = [],
  configuredScene = {},
  configuredContext = {},
  state,
  currentTask = "transfer",
  activeId = null,
  replayMode = false,
  replayTimer = null,
  replayIndex = 0,
  historySignature = "",
  candidateSignature = "",
  resetting = false,
  controlPending = false,
  connectionPending = false,
  selectedCycle = null,
  lastCompletedDecision = null;
$("#app").innerHTML = `
<div class="app-shell">
 <header class="header">
  <div class="brand"><div class="brand-mark">${icon("scan-line")}</div><div><strong>行知</strong><span>EmbodiedJev</span></div></div>
  <div class="header-divider"></div><div class="header-context">具身决策实验室</div>
  <nav class="view-switch" aria-label="工作模式"><button class="active" id="workbench-view" type="button">实验台</button><button id="comparison-open" type="button">模型对比</button><button id="extensions-open" type="button">扩展</button></nav>
  <div class="header-right"><span class="engine-label"><span class="dot"></span>MUJOCO / PANDA</span><span class="version">v0.1</span><button class="icon-button mobile-settings" id="settings-open" title="实验参数" aria-label="实验参数">${icon("sliders-horizontal")}</button><button class="icon-button" id="export" title="导出实验记录" aria-label="导出实验记录">${icon("download")}</button></div>
 </header>
 <div class="body-grid">
 <div class="scrim" id="scrim"></div>
 <aside class="sidebar" id="sidebar">
  <section><div class="section-topline"><h2>实验任务</h2><button class="icon-button close-settings" id="settings-close" aria-label="关闭参数">${icon("x")}</button></div>
   <div class="task-options"><button class="task-option active" data-task="transfer">${icon("move-up-right")}<span>搬运入盘</span><span class="task-number">01</span></button><button class="task-option" data-task="stack">${icon("layers-2")}<span>方块堆叠</span><span class="task-number">02</span></button><button class="task-option" data-task="barrier">${icon("route")}<span>越障搬运</span><span class="task-number">03</span></button></div>
   <p class="task-goal" id="task-goal"></p></section>
  <div class="divider"></div>
  <section><div class="section-topline"><h2>决策模型</h2><button class="icon-button connection-button" id="model-connect" title="模型连接" aria-label="模型连接">${icon("plug-zap")}</button></div><div class="select-wrap"><select id="provider" aria-label="决策模型"></select>${icon("chevron-down")}</div><div class="provider-status"><span class="dot"></span><span id="provider-note">离线 · 确定性策略</span></div></section>
  <div class="divider"></div>
  <section class="input-section" id="input-section"><div class="section-topline"><h2>输入状态</h2><span class="eyebrow">m</span></div><p class="input-context" id="input-context">实时观测</p><table class="input-table"><thead><tr><th>位置</th><th>X</th><th>Y</th><th>Z</th></tr></thead><tbody id="input-positions"></tbody></table><div class="input-contacts" id="input-contacts">等待观测</div></section>
  <details class="advanced-settings" id="advanced-settings"><summary>执行设置 <span>预演 / 速度 / 预算</span></summary><section>
   <div class="settings-row"><label for="seed">随机种子</label><input class="number-input" id="seed" type="number" min="0" max="99999" value="0"></div>
   <div class="settings-row"><label for="budget">动作预算</label><input class="number-input" id="budget" type="number" min="1" max="100" value="30"></div>
   <div class="settings-row"><span>动作预演</span><label class="switch"><input id="preview" type="checkbox" checked aria-label="动作预演"><span></span></label></div>
   <div class="settings-row"><label for="threshold">决策门槛</label><span class="range-label" id="threshold-value">0.55</span></div><input class="range" id="threshold" type="range" min="0" max="1" step="0.05" value="0.55"><div class="range-ticks"><span>0.00</span><span>1.00</span></div>
   <div class="settings-row"><label for="speed">执行速度</label><span class="range-label" id="speed-value">1.5×</span></div><input class="range" id="speed" type="range" min="0.5" max="4" step="0.5" value="1.5"><div class="range-ticks"><span>0.5×</span><span>4×</span></div>
  </section></details><div class="sidebar-bottom"><span>FRANKA PANDA</span><span>7 自由度 · 双指夹爪</span></div>
 </aside>
 <main class="workspace">
  <div class="scene-toolbar"><nav class="tabs"><button class="tab active" data-tab="scene">${icon("box")} 场景</button><button class="tab" data-tab="data">${icon("braces")} 观测</button><button class="tab decision-tab" id="decision-open">${icon("git-branch")} 决策</button></nav><div class="scene-tools"><button class="icon-button" id="camera-top" title="俯视" aria-label="俯视">${icon("scan")}</button><button class="icon-button" id="camera-home" title="复位视角" aria-label="复位视角">${icon("focus")}</button></div></div>
  <div class="viewport" id="viewport"><div class="viewport-label"><h1>Franka Panda</h1><p>MANIPULATION / <span id="scene-task">TRANSFER</span></p></div><div class="scene-status" id="scene-status"><span class="dot"></span><span id="status-text">待命</span></div><div class="scene-axis"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z</span><span>WORLD / m</span></div><span class="scene-bottom-right" id="scene-time">t = 0.00 s</span><div class="success-stamp" id="success-stamp">${icon("circle-check")}物体稳定 · 夹爪已撤离</div><div class="loading" id="loading">加载机器人场景…</div><pre class="raw-state" id="raw-state"></pre></div>
  <div class="telemetry"><div class="metric"><div class="metric-label">末端 X</div><div class="metric-value"><span id="tcp-x">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Y</div><div class="metric-value"><span id="tcp-y">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Z</div><div class="metric-value"><span id="tcp-z">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">物体抬升</div><div class="metric-value"><span id="lift">0</span><small>mm</small></div></div></div>
  <div class="timeline"><button class="icon-button" id="replay-play" aria-label="播放轨迹" title="播放轨迹">${icon("play")}</button><div class="timeline-track"><div class="timeline-caption"><span id="timeline-label">EPISODE TIMELINE</span><span id="frame-label">0000 / 0000</span></div><input id="timeline" type="range" min="0" max="0" value="0" aria-label="轨迹时间轴"></div><button class="live-link" id="live">LIVE</button></div>
  <div class="controls"><button class="primary" id="run">${icon("play")}<span id="run-label">运行实验</span></button><button class="icon-button" id="step" title="单步执行" aria-label="单步执行">${icon("step-forward")}</button><button class="icon-button stop" id="stop" title="停止实验" aria-label="停止实验">${icon("square")}</button><button class="icon-button" id="reset" title="重置实验" aria-label="重置实验">${icon("rotate-ccw")}</button><span class="run-budget" id="run-budget">00 / 30 ACTIONS</span></div>
 </main>
 <aside class="inspector" id="inspector" aria-label="决策与执行记录">
  <section class="inspector-section decision-section" id="decision-section" tabindex="-1"><div class="section-topline"><h2 id="decision-heading">当前决策</h2><span class="eyebrow" id="stage">READY</span></div><div class="decision-context"><span id="decision-context" role="status">实时 · 等待开始</span><button type="button" class="text-button" id="decision-live" hidden>返回实时</button></div><div class="decision-title">${icon("git-branch")}<span id="decision-title">等待开始</span></div><div class="decision-meta"><span id="decision-provider">RULE BASELINE</span><span id="latency">— ms</span></div><div id="intent-panel" hidden><div class="decision-meta"><span>01 · 操作阶段</span><span id="intent-latency"></span></div><div class="probabilities" id="intent-probabilities"></div><div class="decision-meta"><span>02 · 执行动作</span></div></div><div class="probabilities" id="probabilities"><div class="empty">尚无候选动作</div></div><p class="decision-note" id="decision-note"></p><div class="history-observations" id="history-observations" hidden><details><summary>执行前 · 结构化观测</summary><pre id="history-before"></pre></details><details><summary>执行后 · 结构化观测</summary><pre id="history-after"></pre></details><details><summary>候选与模型返回</summary><pre id="history-payload"></pre></details><details id="history-inputs-detail" hidden><summary>本步模型输入</summary><pre id="history-inputs"></pre></details></div></section>
  <section class="inspector-section"><div class="section-topline"><h2 id="feedback-heading">物理反馈</h2><span class="eyebrow">FEEDBACK</span></div><div class="sensors"><span class="name">夹爪状态</span><span class="sensor-value" id="gripper">OPEN</span><span class="name">双侧接触</span><div class="contacts"><span class="contact" id="contact-l">L</span><span class="contact" id="contact-r">R</span></div><span class="name">目标支撑接触</span><span class="sensor-value" id="support">NO</span><span class="name">稳定时长</span><span class="sensor-value" id="stable">0.00 s</span><span class="name">动作预演</span><span class="sensor-value" id="preview-state">ON</span></div></section>
  <div class="event-heading"><div class="section-topline"><h2>执行记录</h2><span class="eyebrow" id="event-count">0 步</span></div></div><ol class="events" id="events"><li class="empty">暂无执行记录</li></ol><details class="runtime-log" id="runtime-log"><summary>运行日志 <span id="log-count">0 条</span></summary><ol id="log-entries"></ol><p>仅展示最近 12 条，完整日志可随实验导出。</p></details><div class="inspector-footer"><span id="model-calls">调用 0 次</span><span id="tokens">输入 0 tokens</span></div>
 </aside></div><footer class="bottom-bar"><div class="bottom-left"><span id="connection">连接中</span><span>物理仿真 500 Hz</span><span>几何与接触状态</span></div><span class="bottom-right" id="episode-id">实验 / —</span></footer>
</div><div class="toast" id="toast" role="status"></div>
<dialog id="connection-dialog" class="connection-dialog" aria-labelledby="connection-title">
 <form id="connection-form">
  <div class="dialog-heading"><div><span class="eyebrow">MODEL CONNECTION</span><h2 id="connection-title">模型连接</h2></div><button type="button" class="icon-button" id="connection-close" aria-label="关闭模型连接">${icon("x")}</button></div>
  <label class="field-label" for="api-provider">接口类型</label><select id="api-provider"><option value="chat">OpenAI 兼容 API</option><option value="claude">Claude 原生 API</option><option value="jev">TypeSafe Jev</option><option value="local">Jev / 结构化决策 API</option></select>
  <label class="field-label" for="profile-name">配置名称 <span>保存具名配置时填写</span></label><input id="profile-name" maxlength="80" placeholder="例如：OpenAI · GPT6" autocomplete="off">
  <label class="field-label" for="api-url">Base URL / 接口地址</label><input id="api-url" type="url" required placeholder="https://your-provider.example/v1" autocomplete="off">
  <label class="field-label" for="api-model">模型 ID</label><input id="api-model" required placeholder="平台提供的模型名称" autocomplete="off">
  <label class="field-label" for="api-key">API Key <span id="key-state">未配置</span></label><input id="api-key" type="password" placeholder="API Key" autocomplete="off" spellcheck="false">
  <label class="json-mode" id="json-mode-row"><input id="api-json" type="checkbox" checked>JSON 模式</label>
  <p class="connection-retention" id="provider-help"></p>
  <button type="button" class="text-button api-preset" id="api-official-preset">填入 OpenAI 官方示例</button>
  <p class="connection-retention" id="typesafe-links" hidden><a href="https://console.typesafe.ai" target="_blank" rel="noopener noreferrer">管理 TypeSafe Key ↗</a> · <a href="https://typesafe.ai" target="_blank" rel="noopener noreferrer">申请访问 ↗</a> · <a href="https://docs.typesafe.ai/api" target="_blank" rel="noopener noreferrer">接口说明 ↗</a></p>
  <p class="connection-retention" id="connection-storage">正在读取本机存储状态…</p>
  <div id="connection-verification" class="connection-verification"><span class="dot"></span><span id="verification-label">尚未验证</span></div>
  <div id="connection-result" class="connection-result" role="status"></div>
  <p class="connection-retention" id="profile-help">另存为具名配置时请重新填写 Key；不会复制默认连接的密钥。</p><div class="dialog-actions"><button type="button" class="secondary" id="connection-profile">另存为模型配置</button><button type="button" class="secondary" id="connection-test">${icon("plug-zap")}测试调用</button><button type="submit" class="primary" id="connection-save">保存连接</button></div>
 </form>
</dialog>`;
const comparisonContainer = document.createElement("main");
comparisonContainer.id = "comparison-view";
comparisonContainer.hidden = true;
$(".bottom-bar").before(comparisonContainer);
const extensionsContainer = document.createElement("main");
extensionsContainer.id = "extensions-view";
extensionsContainer.hidden = true;
$(".bottom-bar").before(extensionsContainer);
let comparisonView,
  comparisonVisible = false,
  openingComparison = false,
  extensionsView,
  extensionsVisible = false,
  comparisonLoading,
  extensionsLoading;
function moduleFailure(container) {
  container.innerHTML =
    '<div class="view-load-state" role="status"><h1>页面已更新或模块加载失败</h1><p>刷新后可重新加载界面。未保存的配置需要重新填写，当前仿真实验不会因刷新而重置。</p><button class="secondary module-reload" type="button">刷新页面重试</button></div>';
  container.querySelector(".module-reload").onclick = () =>
    window.location.reload();
}
async function ensureComparison() {
  if (comparisonView) return comparisonView;
  if (!comparisonLoading) {
    comparisonContainer.innerHTML =
      '<div class="view-load-state" role="status">正在加载模型对比…</div>';
    comparisonLoading = import("./comparison.js")
      .then(({ createComparison }) =>
        createComparison(comparisonContainer, { api, toast }),
      )
      .then((view) => (comparisonView = view))
      .catch(() => {
        comparisonLoading = null;
        moduleFailure(comparisonContainer);
        throw new Error("模型对比加载失败，可点击刷新页面重试。");
      });
  }
  return comparisonLoading;
}
async function ensureExtensions() {
  if (extensionsView) return extensionsView;
  if (!extensionsLoading) {
    extensionsContainer.innerHTML =
      '<div class="view-load-state" role="status">正在加载扩展…</div>';
    extensionsLoading = import("./extensions.js")
      .then(({ createExtensions }) =>
        createExtensions(extensionsContainer, {
          api,
          openConnection: (profileId) => openConnection(profileId, true),
          applyPreset: applyExtensionPreset,
          applyModel: applyExtensionModel,
        }),
      )
      .then((view) => (extensionsView = view))
      .catch(() => {
        extensionsLoading = null;
        moduleFailure(extensionsContainer);
        throw new Error("扩展页面加载失败，可点击刷新页面重试。");
      });
  }
  return extensionsLoading;
}
async function switchView(compare) {
  if (compare && openingComparison) return;
  comparisonVisible = compare;
  extensionsVisible = false;
  extensionsContainer.hidden = true;
  $("#extensions-open").classList.remove("active");
  $(".body-grid").hidden = compare;
  $(".bottom-bar").hidden = compare;
  comparisonContainer.hidden = !compare;
  $(".app-shell").classList.toggle("comparing", compare);
  $(".app-shell").classList.remove("extending");
  $("#workbench-view").classList.toggle("active", !compare);
  $("#comparison-open").classList.toggle("active", compare);
  if (compare && !comparisonView) {
    openingComparison = true;
    try {
      await ensureComparison();
    } catch (error) {
      toast(error.message);
    } finally {
      openingComparison = false;
    }
  }
  comparisonView?.setActive(comparisonVisible);
  if (!compare && state) {
    renderState(state);
    sceneView.requestRender();
  }
}
$("#workbench-view").onclick = () => switchView(false);
$("#comparison-open").onclick = () => switchView(true);
$("#extensions-open").onclick = async () => {
  const target = comparisonVisible ? "comparison" : "workbench";
  comparisonVisible = false;
  extensionsVisible = true;
  comparisonView?.setActive(false);
  $(".body-grid").hidden =
    $(".bottom-bar").hidden =
    comparisonContainer.hidden =
      true;
  extensionsContainer.hidden = false;
  $(".app-shell").classList.remove("comparing");
  $(".app-shell").classList.add("extending");
  $("#workbench-view").classList.remove("active");
  $("#comparison-open").classList.remove("active");
  $("#extensions-open").classList.add("active");
  try {
    await ensureExtensions();
    await extensionsView.refresh(target);
  } catch (error) {
    toast(error.message);
  }
};
async function applyExtensionPreset(preset, target) {
  if (target === "comparison")
    return (await ensureComparison()).applyPreset(preset);
  if (
    ["running", "paused"].includes(state.status) ||
    resetting ||
    controlPending
  )
    throw new Error("请先停止当前实验，再应用预设。");
  const previous = {
    task: currentTask,
    scene: configuredScene,
    context: configuredContext,
  };
  currentTask = preset.task;
  configuredScene = preset.scene_config;
  configuredContext = preset.user_context;
  if (!(await reset())) {
    currentTask = previous.task;
    configuredScene = previous.scene;
    configuredContext = previous.context;
    throw new Error("预设未应用，已保留原实验。请检查场景校验提示。");
  }
}
async function applyExtensionModel(profileId, target) {
  if (target === "comparison")
    return (await ensureComparison()).applyModel(profileId);
  if (
    ["running", "paused"].includes(state.status) ||
    resetting ||
    controlPending
  )
    throw new Error("请先停止当前实验，再切换模型配置。");
  await refreshProviders();
  $("#provider").value = "profile:" + profileId;
  if (!(await reset())) throw new Error("模型配置未应用，请检查服务提示。");
}
const intentPanel = $("#intent-panel");
intentPanel.lastElementChild.remove();
intentPanel.firstElementChild.firstElementChild.id = "intent-heading";
$("#input-section").append(intentPanel);
const liveInputs = document.createElement("details");
liveInputs.id = "live-inputs-detail";
liveInputs.className = "live-inputs";
liveInputs.hidden = true;
liveInputs.innerHTML =
  '<summary>本轮模型输入</summary><pre id="live-inputs"></pre>';
$("#history-observations").before(liveInputs);
const historyList = document.createElement("details");
historyList.className = "history-list";
historyList.id = "history-list";
historyList.innerHTML =
  '<summary>执行记录 <span id="history-count"></span></summary>';
$(".event-heading").before(historyList);
historyList.append($(".event-heading"), $("#events"));
const feedbackDetails = document.createElement("details");
feedbackDetails.className = "feedback-details";
feedbackDetails.innerHTML = "<summary>物理反馈</summary>";
const feedbackSection = $("#feedback-heading").closest("section");
feedbackSection.before(feedbackDetails);
feedbackDetails.append(feedbackSection);
const narrowLayout = window.matchMedia("(max-width: 820px)");
function placeInputPanel() {
  if (narrowLayout.matches) $("#inspector").prepend($("#input-section"));
  else $("#sidebar").insertBefore($("#input-section"), $("#advanced-settings"));
}
narrowLayout.addEventListener("change", placeInputPanel);
placeInputPanel();
const icons = {
  ScanLine,
  SlidersHorizontal,
  Download,
  X,
  MoveUpRight,
  Layers2,
  Route,
  ChevronDown,
  Box,
  Braces,
  Scan,
  Focus,
  CircleCheck,
  Play,
  Pause,
  StepForward,
  Square,
  RotateCcw,
  GitBranch,
  PlugZap,
};
createIcons({ icons });

let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("visible"), 6000);
}
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json();
    throw new Error(
      typeof error.detail === "string" ? error.detail : "参数或服务异常",
    );
  }
  return response.json();
}
const sceneView = new RobotScene($("#viewport"), { onError: toast });
let lastFrame;
function renderFrame(frame) {
  if (!frame) return;
  sceneView.render(frame);
  lastFrame = frame;
  const o = frame.observation;
  ["x", "y", "z"].forEach(
    (k, i) => ($(`#tcp-${k}`).textContent = o.tcp[i].toFixed(3)),
  );
  $("#lift").textContent = (o.max_lift_m * 1000).toFixed(0);
  $("#gripper").textContent = o.gripper === "closed" ? "CLOSED" : "OPEN";
  $("#contact-l").classList.toggle("on", o.finger_contacts.includes("left"));
  $("#contact-r").classList.toggle("on", o.finger_contacts.includes("right"));
  $("#support").textContent = o.support_contact ? "YES" : "NO";
  $("#support").classList.toggle("on", o.support_contact);
  $("#stable").textContent = o.stable_seconds.toFixed(2) + " s";
  $("#scene-time").textContent = "t = " + o.sim_seconds.toFixed(2) + " s";
  $("#raw-state").textContent = JSON.stringify(o, null, 2);
}
async function loadScene() {
  $("#loading").classList.remove("hidden");
  const data = await api("/api/scene");
  sceneView.load(data);
  lastFrame = null;
  $("#scene-task").textContent = data.task.toUpperCase();
  $("#loading").classList.add("hidden");
}

function decisionSnapshot(s) {
  return {
    cycle: s.cycles,
    phase: s.phase,
    intent: s.last_intent,
    decision: s.last_decision,
    candidates: s.candidates || [],
  };
}

function renderDecision(s) {
  const history = s.history.find((h) => h.cycle === selectedCycle);
  if (selectedCycle !== null && !history) selectedCycle = null;
  if (s.last_decision && s.candidates?.length) {
    lastCompletedDecision = decisionSnapshot(s);
  } else if (!lastCompletedDecision && s.history.length) {
    lastCompletedDecision = s.history.at(-1);
  }
  const previous = !s.last_decision && !!lastCompletedDecision;
  const shown =
    history || (previous ? lastCompletedDecision : decisionSnapshot(s));
  const intent = shown.intent;
  const decision = shown.decision;
  const candidates = shown.candidates || (shown.action ? [shown.action] : []);
  const loading =
    s.provider === "minicpm" && s.model_runtime?.status === "loading";
  const pending =
    s.status === "running" && ["deciding", "previewing"].includes(s.stage);
  const observation =
    history?.before ||
    (replayMode ? lastFrame?.observation : s.frame?.observation);
  $("#input-context").textContent = history
    ? `历史第 ${history.cycle} 步 · 执行前观测`
    : replayMode
      ? "回放观测"
      : "实时观测 · 位置与接触";
  $("#input-positions").innerHTML = [
    ["末端", "tcp"],
    ["方块", "object"],
    ["目标", "destination"],
  ]
    .map(
      ([label, key]) =>
        `<tr><th>${label}</th>${[0, 1, 2].map((index) => `<td>${Number.isFinite(observation?.[key]?.[index]) ? observation[key][index].toFixed(3) : "—"}</td>`).join("")}</tr>`,
    )
    .join("");
  $("#input-contacts").textContent = observation
    ? `夹爪${observation.gripper === "closed" ? "闭合" : "张开"} · ${observation.held ? "双侧抓持" : observation.finger_contacts?.length ? "单侧接触" : "未接触物体"}`
    : "等待观测";
  $("#intent-heading").textContent = history
    ? "阶段选择 · 历史"
    : previous
      ? "阶段选择 · 上一条"
      : "阶段选择";
  $("#decision-heading").textContent = history
    ? `历史输出 · 第 ${history.cycle} 步`
    : "动作输出";
  $("#decision-live").hidden = !history;
  $("#decision-section").classList.toggle("viewing-history", !!history);
  $("#stage").textContent = history
    ? "历史"
    : loading
      ? "加载中"
      : stageNames[s.stage] || s.stage;
  $("#decision-context").textContent = history
    ? `历史记录 · ${history.after.sim_seconds.toFixed(2)} s`
    : previous
      ? `上一条决策 · ${pending ? "正在计算新决策" : stateNames[s.status] || s.status}`
      : loading
        ? "正在加载 MiniCPM5-2B 权重"
        : pending
          ? "实时 · 正在计算新决策"
          : `实时 · ${stateNames[s.status] || s.status}`;
  $("#decision-title").textContent =
    candidates.find((candidate) => candidate.id === decision?.choice)?.label ||
    (loading ? "首次加载模型" : "等待开始");
  $("#decision-provider").textContent =
    decision?.model ||
    intent?.model ||
    {
      baseline: "规则基线",
      chat: "CHAT / JSON",
      claude: "CLAUDE / TOOL",
    }[s.provider] ||
    s.provider.toUpperCase();
  $("#latency").textContent = decision?.model_call
    ? Number(decision.latency_ms).toFixed(0) + " ms"
    : decision
      ? "无模型调用"
      : "— ms";
  $("#feedback-heading").textContent = replayMode
    ? "回放物理反馈"
    : "实时物理反馈";
  const failure = ["error", "uncertain", "exhausted", "stalled"].includes(
    s.status,
  );
  $("#decision-note").classList.toggle("failure", !history && failure);
  $("#decision-note").textContent = history
    ? `正在查看该步记录，三维场景保持${replayMode ? "轨迹回放" : "实时显示"}。${history.candidates ? "" : "旧记录未保存完整候选，仅显示已选动作。"}`
    : failure && s.message
      ? s.message
      : previous
        ? "保留上一次完整选择，新结果返回后自动更新。"
        : decision
          ? "候选由任务控制器生成；概率不代表任务成功率。"
          : "运行实验后查看动作选择。";
  $("#history-observations").hidden = !history;
  $("#live-inputs-detail").hidden =
    !!history ||
    !(s.last_decision_inputs?.phase || s.last_decision_inputs?.action);
  $("#live-inputs").textContent = s.last_decision_inputs
    ? JSON.stringify(s.last_decision_inputs, null, 2)
    : "";
  $("#history-inputs-detail").hidden = !history?.decision_inputs;
  const signature = JSON.stringify([
    selectedCycle,
    candidates,
    decision,
    intent,
    history?.decision_inputs,
  ]);
  if (signature === candidateSignature) return;
  candidateSignature = signature;
  $("#intent-panel").hidden = !intent;
  $("#intent-latency").textContent = intent?.model_call
    ? Number(intent.latency_ms).toFixed(0) + " ms"
    : intent?.reason === "only_eligible_action"
      ? "单一可行阶段 · 无模型调用"
      : "规则选择";
  $("#intent-probabilities").innerHTML = intent
    ? (Object.entries(intent.probabilities || {}).length
        ? Object.entries(intent.probabilities)
        : [[intent.choice, null]]
      )
        .map(
          ([choice, probability]) =>
            `<div class="prob-row ${choice === intent.choice ? "selected" : ""}"><div class="prob-top"><span>${escape(phaseNames[choice] || choice)}</span><span>${probability === null ? "已选择" : (probability * 100).toFixed(1) + "%"}</span></div>${probability === null ? "" : `<div class="bar"><div class="bar-fill" style="width:${probability * 100}%"></div></div>`}</div>`,
        )
        .join("")
    : "";
  const probabilities = decision?.probabilities || {};
  $("#probabilities").innerHTML = candidates.length
    ? candidates
        .map((candidate) => {
          const selected = decision?.choice === candidate.id;
          const rejected = candidate.admitted === false;
          const probability = probabilities[candidate.id];
          const label = rejected
            ? "已拦截"
            : probability !== undefined
              ? (probability * 100).toFixed(1) + "%"
              : selected
                ? "已选择"
                : decision
                  ? "未选择"
                  : "等待决策";
          const width =
            probability !== undefined ? probability * 100 : selected ? 100 : 0;
          return `<div class="prob-row ${selected ? "selected" : ""} ${rejected ? "rejected" : ""}" title="${escape(candidate.rejection || "")}"><div class="prob-top"><span>${escape(candidate.label || candidate.id)}</span><span>${label}</span></div><div class="bar"><div class="bar-fill" style="width:${width}%"></div></div></div>`;
        })
        .join("")
    : `<div class="empty">${pending ? "等待模型返回候选选择…" : "尚无候选动作"}</div>`;
  if (history) {
    $("#history-before").textContent = JSON.stringify(history.before, null, 2);
    $("#history-after").textContent = JSON.stringify(history.after, null, 2);
    $("#history-payload").textContent = JSON.stringify(
      {
        cycle: history.cycle,
        phase: history.phase,
        intent,
        decision,
        candidates,
      },
      null,
      2,
    );
    $("#history-inputs").textContent = history.decision_inputs
      ? JSON.stringify(history.decision_inputs, null, 2)
      : "";
  }
}

function updateControlAvailability() {
  if (!state) return;
  const pending = controlPending || resetting;
  $("#run").disabled =
    pending ||
    ["completed", "stopped", "error", "exhausted", "stalled"].includes(
      state.status,
    );
  $("#step").disabled =
    pending ||
    [
      "running",
      "completed",
      "stopped",
      "error",
      "exhausted",
      "stalled",
    ].includes(state.status);
  $("#stop").disabled =
    pending || ["idle", "completed", "stopped"].includes(state.status);
  $("#reset").disabled = pending;
  $(".controls").setAttribute("aria-busy", String(pending));
  const locked = pending || ["running", "paused"].includes(state.status);
  for (const el of document.querySelectorAll(
    "#model-connect,#provider,.task-option,#seed,#budget,#preview,#threshold,#speed",
  ))
    el.disabled = locked;
  $("#threshold").disabled =
    locked || ["baseline", "chat", "claude"].includes(state.provider);
}

let logSignature = "";
function renderLogs(s) {
  const events = s.events || [];
  const signature = JSON.stringify(events.slice(-12));
  if (signature === logSignature) return;
  logSignature = signature;
  const errors = events.filter((event) =>
    ["error", "warning"].includes(event.level),
  ).length;
  $("#log-count").textContent =
    `${events.length} 条${errors ? ` · ${errors} 条提醒` : ""}`;
  $("#runtime-log").classList.toggle("has-alert", errors > 0);
  $("#log-entries").innerHTML =
    events
      .slice(-12)
      .reverse()
      .map((event) => {
        const level = ["error", "warning"].includes(event.level)
          ? event.level
          : "info";
        const time =
          typeof event.time === "number"
            ? `${event.time.toFixed(1)} s`
            : String(event.time || "")
                .replace(/^.*T/, "")
                .slice(0, 8);
        return `<li class="log-item ${level}"><span>${escape(time)} · 第 ${escape(event.cycle ?? 0)} 步</span><p>${escape(event.message || event.event || "")}</p></li>`;
      })
      .join("") || '<li class="empty">暂无运行日志</li>';
}

function renderState(s) {
  state = s;
  if (activeId !== s.id) {
    activeId = s.id;
    historySignature = "";
    candidateSignature = "";
    selectedCycle = null;
    lastCompletedDecision = null;
    replayMode = false;
    replayRequest++;
    clearInterval(replayTimer);
    replayTimer = null;
    currentTask = s.task;
    configuredScene = s.scene_config || {};
    configuredContext = s.user_context || {};
    for (const button of document.querySelectorAll("[data-task]")) {
      button.classList.toggle("active", button.dataset.task === s.task);
    }
    $("#task-goal").textContent = config.tasks[s.task].goal;
    $("#provider").value = s.profile_id
      ? "profile:" + s.profile_id
      : s.provider;
    $("#seed").value = s.seed;
    $("#budget").value = s.max_cycles;
    $("#preview").checked = s.preview;
    $("#threshold").value = s.threshold;
    $("#threshold-value").textContent = s.threshold.toFixed(2);
    $("#speed").value = s.speed;
    $("#speed-value").textContent = s.speed.toFixed(1) + "×";
    $("#timeline-label").textContent = "EPISODE TIMELINE";
    $("#provider-note").textContent =
      s.provider === "baseline"
        ? "离线 · 确定性策略"
        : s.provider === "jev"
          ? "远程 · TypeSafe API"
          : "本地 · 候选概率";
    $("#episode-id").textContent = "EPISODE / " + s.id.toUpperCase();
  }
  if (!replayMode) renderFrame(s.frame);
  $("#status-text").textContent = replayMode
    ? "轨迹回放"
    : stateNames[s.status];
  $("#scene-status").classList.toggle(
    "error",
    ["error", "exhausted", "uncertain", "stalled"].includes(s.status),
  );
  $("#success-stamp").classList.toggle(
    "visible",
    s.status === "completed" && !replayMode,
  );
  if (s.provider === "minicpm" && s.model_runtime) {
    const runtime = s.model_runtime;
    const device = (runtime.device || "AUTO").toUpperCase();
    $("#provider-note").textContent = {
      not_loaded: "本地 · 首次决策加载权重",
      loading: `正在加载权重 · ${device}`,
      ready: `本地就绪 · ${device} · 候选概率`,
      error: `加载失败 · ${runtime.error || "请检查服务日志"}`,
    }[runtime.status];
  }
  renderDecision(s);
  renderLogs(s);
  $("#run-budget").textContent =
    String(s.cycles).padStart(2, "0") + " / " + s.max_cycles + " 步";
  $("#run-label").textContent =
    s.status === "running"
      ? "暂停实验"
      : ["paused", "uncertain"].includes(s.status)
        ? "继续实验"
        : "运行实验";
  const runIcon = s.status === "running" ? "pause" : "play";
  if ($("#run").dataset.icon !== runIcon) {
    $("#run").dataset.icon = runIcon;
    $("#run svg").outerHTML = icon(runIcon);
    createIcons({ icons });
  }
  updateControlAvailability();
  $("#preview-state").textContent = s.preview ? "ON" : "OFF";
  $("#model-calls").textContent = "调用 " + s.model_calls + " 次";
  $("#tokens").textContent =
    "输入 " + s.input_tokens.toLocaleString() + " tokens";
  $("#timeline").max = Math.max(0, s.frame_count - 1);
  if (!replayMode) {
    $("#timeline").value = s.frame_count - 1;
    $("#frame-label").textContent =
      String(s.frame_count).padStart(4, "0") +
      " / " +
      String(s.frame_count).padStart(4, "0");
  }
  $("#replay-play").disabled = s.frame_count < 2 || s.status === "running";
  const hsig = s.id + "-" + s.history.length + "-" + selectedCycle;
  if (hsig !== historySignature) {
    historySignature = hsig;
    $("#event-count").textContent = s.history.length + " 步";
    $("#history-count").textContent = s.history.length + " 步";
    $("#events").innerHTML = s.history.length
      ? s.history
          .map(
            (h) =>
              `<li class="event ${h.cycle === selectedCycle ? "active" : ""}"><button type="button" class="event-button" data-cycle="${h.cycle}" aria-pressed="${h.cycle === selectedCycle}" aria-label="查看第 ${h.cycle} 步决策：${escape(h.label)}"><span class="event-line"><span>${escape(h.label)}</span><small>${h.after.sim_seconds.toFixed(1)} s</small></span><span class="event-detail">第 ${h.cycle} 步 · ${h.after.held ? "双侧接触" : h.after.support_contact ? "目标支撑" : "位置已更新"}${h.rejected_count ? " · 拦截 " + h.rejected_count + " 个候选" : ""}</span></button></li>`,
          )
          .join("")
      : '<li class="empty">暂无执行记录</li>';
    if (selectedCycle === null)
      $("#events").scrollTop = $("#events").scrollHeight;
  }
  $("#threshold-value").textContent = ["baseline", "chat", "claude"].includes(
    s.provider,
  )
    ? "N/A"
    : Number($("#threshold").value).toFixed(2);
  $("#threshold").title = ["chat", "claude"].includes(s.provider)
    ? "生成式接口不提供原生候选概率"
    : "";
  renderProviderStatus();
  if (s.message && s.message !== renderState.lastMessage) {
    toast(s.message);
    renderState.lastMessage = s.message;
  }
  $("#connection").textContent = "● 已连接";
}

function setup() {
  return {
    expected_episode_id: state?.id,
    task: currentTask,
    scene_config: configuredScene,
    user_context: configuredContext,
    seed: Number($("#seed").value),
    ...selectionConfig($("#provider").value, profileValues),
    preview: $("#preview").checked,
    threshold: Number($("#threshold").value),
    max_cycles: Number($("#budget").value),
    speed: Number($("#speed").value),
  };
}
async function reset(fromControl = false) {
  if (resetting || (controlPending && !fromControl)) return false;
  resetting = true;
  updateControlAvailability();
  clearInterval(replayTimer);
  replayTimer = null;
  replayRequest++;
  replayMode = false;
  try {
    const s = await api("/api/reset", setup());
    await loadScene();
    renderState(s);
    return true;
  } catch (e) {
    toast(e.message);
    return false;
  } finally {
    resetting = false;
    updateControlAvailability();
  }
}
async function control(action) {
  if (controlPending || resetting) return;
  controlPending = true;
  updateControlAvailability();
  try {
    replayRequest++;
    replayMode = false;
    clearInterval(replayTimer);
    replayTimer = null;
    $("#timeline-label").textContent = "EPISODE TIMELINE";
    if (["start", "step"].includes(action) && state.status === "idle") {
      if (!(await reset(true))) return;
    }
    renderState(
      await api("/api/control/" + action, {
        episode_id: state.id,
        threshold: Number($("#threshold").value),
      }),
    );
  } catch (e) {
    toast(e.message);
  } finally {
    controlPending = false;
    updateControlAvailability();
  }
}
$("#run").onclick = () =>
  control(state.status === "running" ? "pause" : "start");
$("#step").onclick = () => control("step");
$("#stop").onclick = () => control("stop");
$("#reset").onclick = () => reset();
$("#export").onclick = () => {
  const a = document.createElement("a");
  a.href = "/api/export";
  a.download = "episode.json";
  a.click();
};
$("#camera-home").onclick = () => sceneView.cameraHome();
$("#camera-top").onclick = () => sceneView.cameraTop();
for (const button of document.querySelectorAll("[data-task]"))
  button.onclick = async () => {
    if (controlPending || resetting) return;
    currentTask = button.dataset.task;
    configuredScene = {};
    configuredContext = {};
    for (const b of document.querySelectorAll("[data-task]"))
      b.classList.toggle("active", b === button);
    $("#task-goal").textContent = config.tasks[currentTask].goal;
    await reset();
  };
for (const el of document.querySelectorAll("[data-tab]"))
  el.onclick = () => {
    document
      .querySelectorAll("[data-tab]")
      .forEach((b) => b.classList.toggle("active", b === el));
    $("#raw-state").classList.toggle("visible", el.dataset.tab === "data");
  };
function focusDecision() {
  (narrowLayout.matches
    ? $("#input-section")
    : $("#decision-section")
  ).scrollIntoView({ block: "start", behavior: "auto" });
  $("#decision-section").focus({ preventScroll: true });
}
$("#decision-open").onclick = focusDecision;
$("#events").onclick = (event) => {
  const button = event.target.closest("[data-cycle]");
  if (!button) return;
  selectedCycle = Number(button.dataset.cycle);
  renderState(state);
  focusDecision();
};
$("#decision-live").onclick = () => {
  selectedCycle = null;
  renderState(state);
};
$("#threshold").oninput = () =>
  ($("#threshold-value").textContent = Number($("#threshold").value).toFixed(
    2,
  ));
$("#speed").oninput = () =>
  ($("#speed-value").textContent = Number($("#speed").value).toFixed(1) + "×");
$("#provider").onchange = () => {
  $("#provider-note").textContent =
    $("#provider").value === "baseline"
      ? "离线 · 确定性策略"
      : $("#provider").value === "jev"
        ? "远程 · TypeSafe API"
        : "本地 · 候选概率";
  reset();
};
function drawer(open) {
  $("#sidebar").classList.toggle("open", open);
  $("#scrim").classList.toggle("visible", open);
}
$("#settings-open").onclick = () => drawer(true);
$("#settings-close").onclick = () => drawer(false);
$("#scrim").onclick = () => drawer(false);
let replayRequest = 0;
async function showReplay(index) {
  const request = ++replayRequest;
  replayMode = true;
  replayIndex = index;
  const frame = await api("/api/replay/" + index);
  if (request !== replayRequest) return;
  renderFrame(frame);
  $("#timeline").value = index;
  $("#frame-label").textContent =
    String(index + 1).padStart(4, "0") +
    " / " +
    String(state.frame_count).padStart(4, "0");
  $("#timeline-label").textContent = "RECORDED TRAJECTORY";
  $("#status-text").textContent = "轨迹回放";
  $("#success-stamp").classList.remove("visible");
}
$("#timeline").oninput = async () => {
  if (state.status === "running") await control("pause");
  try {
    await showReplay(Number($("#timeline").value));
  } catch (e) {
    toast(e.message);
  }
};
$("#live").onclick = () => {
  clearInterval(replayTimer);
  replayTimer = null;
  replayRequest++;
  replayMode = false;
  $("#timeline-label").textContent = "EPISODE TIMELINE";
  renderState(state);
};
$("#replay-play").onclick = () => {
  if (replayTimer) {
    clearInterval(replayTimer);
    replayTimer = null;
    return;
  }
  replayIndex =
    replayMode && replayIndex < state.frame_count - 1 ? replayIndex : 0;
  let busy = false;
  replayTimer = setInterval(async () => {
    if (busy) return;
    if (replayIndex >= state.frame_count - 1) {
      clearInterval(replayTimer);
      replayTimer = null;
      return;
    }
    busy = true;
    try {
      await showReplay(replayIndex + 1);
    } catch (e) {
      clearInterval(replayTimer);
      replayTimer = null;
      toast(e.message);
    } finally {
      busy = false;
    }
  }, 80);
};
async function refreshProviders() {
  const previous = $("#provider").value;
  [config, { profiles: profileValues }] = await Promise.all([
    api("/api/config"),
    api("/api/model-profiles"),
  ]);
  $("#provider").innerHTML = selectionOptions(config, profileValues, escape);
  if ([...$("#provider").options].some((option) => option.value === previous))
    $("#provider").value = previous;
}
let connectionValues = {};
let connectionDraftChanged = false;
let editingProfileId = null;
let profileEditor = false;
function currentConnection() {
  if (editingProfileId)
    return (
      profileValues.find((profile) => profile.id === editingProfileId) || {}
    );
  const saved = connectionValues[$("#api-provider").value] || {};
  return profileEditor
    ? { ...saved, key_configured: false, verification: null }
    : saved;
}
function verificationLabel(saved, provider = $("#api-provider").value) {
  const ready =
    saved?.url &&
    saved?.model &&
    (!["jev", "claude"].includes(provider) || saved.key_configured);
  if (!ready) return "未配置";
  const verification = saved.verification;
  if (verification?.status === "passed") return "已验证";
  if (verification?.status === "failed") return "验证失败";
  return "已配置 · 未验证";
}
function renderProviderStatus() {
  if (!["chat", "claude", "jev", "local"].includes(state?.provider)) {
    delete $(".provider-status").dataset.verification;
    return;
  }
  const prefix = {
    chat: "兼容 API",
    claude: "Claude API",
    jev: "TypeSafe API",
    local: "结构化 API",
  }[state.provider];
  const saved = state.profile_id
    ? profileValues.find((profile) => profile.id === state.profile_id)
    : connectionValues[state.provider];
  $("#provider-note").textContent =
    `${saved?.name || prefix} · ${verificationLabel(saved, state.provider)}`;
  $(".provider-status").dataset.verification =
    saved?.verification?.status || "untested";
}
function renderVerification() {
  const saved = currentConnection();
  $("#connection-storage").textContent = storageDescription(saved.storage);
  const verification = saved.verification;
  if (profileEditor && !editingProfileId) {
    $("#connection-verification").dataset.status = "untested";
    $("#verification-label").textContent = "新配置草稿 · 尚未保存或验证";
    return;
  }
  if (connectionDraftChanged) {
    $("#connection-verification").dataset.status = "untested";
    $("#verification-label").textContent = "草稿已修改 · 尚未保存或验证";
    return;
  }
  $("#connection-verification").dataset.status =
    verification?.status || "untested";
  $("#verification-label").textContent =
    verificationLabel(saved) +
    (verification?.status === "passed"
      ? ` · ${verification.model || saved.model}${Number.isFinite(verification.latency_ms) ? ` · ${verification.latency_ms} ms` : ""}`
      : verification?.status === "failed" && verification.message
        ? ` · ${verification.message}`
        : "");
}
function fillConnection() {
  connectionDraftChanged = false;
  const provider = $("#api-provider").value;
  const saved = currentConnection();
  $("#profile-name").value = saved.name || "";
  $("#connection-title").textContent = editingProfileId
    ? "编辑模型配置"
    : profileEditor
      ? "新建模型配置"
      : "模型连接";
  $("#connection-save").hidden = $("#connection-test").hidden = profileEditor;
  $("#connection-profile").textContent = editingProfileId
    ? "更新模型配置"
    : profileEditor
      ? "保存模型配置"
      : "另存为模型配置";
  $("#profile-help").textContent = editingProfileId
    ? "留空 Key 仅在接口地址保持相同时保留原密钥。更新配置不会自动开始实验。"
    : "另存为具名配置时请重新填写 Key；不会复制默认连接的密钥。";
  $("#api-provider").disabled = !!editingProfileId;
  $("#api-url").value =
    provider === "chat"
      ? (saved.url || "").replace(/\/chat\/completions$/, "")
      : provider === "claude"
        ? (saved.url || "https://api.anthropic.com/v1").replace(
            /\/messages$/,
            "",
          )
        : saved.url || "";
  $("#api-url").readOnly = provider === "jev";
  $("#api-model").value = saved.model || "";
  $("#api-key").value = "";
  $("#api-key").placeholder = saved.key_configured
    ? "留空保留已配置密钥"
    : "API Key";
  $("#key-state").textContent = saved.key_configured ? "已配置" : "未配置";
  $("#api-json").checked = saved.json_mode !== false;
  $("#json-mode-row").hidden = provider !== "chat";
  $("#api-official-preset").hidden = provider !== "chat";
  $("#typesafe-links").hidden = provider !== "jev";
  $("#provider-help").textContent = {
    jev: "官方 Jev 当前采用邀请制：先申请访问，获批后创建 TypeSafe Key。地址已锁定；jev-latest 跟随官方更新，对比时可固定版本，如 jev-1.13.0。",
    claude:
      "Claude 原生 Messages API，使用 Anthropic Key。测试时会显示服务返回的模型名称。",
    chat: "填写平台提供的 Base URL 和模型 ID。普通聊天兼容接口不能替代 Jev 专用决策接口。",
    local: "填写完整的结构化决策接口地址；服务需要返回候选动作及其概率。",
  }[provider];
  $("#connection-result").textContent = "";
  renderVerification();
}
async function openConnection(profileId = null, asProfile = false) {
  try {
    [connectionValues, { profiles: profileValues }] = await Promise.all([
      api("/api/connections"),
      api("/api/model-profiles"),
    ]);
    const profile = profileId
      ? profileValues.find((item) => item.id === profileId)
      : null;
    if (profileId && !profile) throw new Error("模型配置已失效，请重新选择。");
    editingProfileId = profileId;
    profileEditor = asProfile || !!profileId;
    $("#api-provider").value =
      profile?.provider ||
      (["jev", "local", "chat", "claude"].includes(state.provider)
        ? state.provider
        : "chat");
    fillConnection();
    $("#connection-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}
$("#model-connect").onclick = () => openConnection(state?.profile_id || null);
$("#api-provider").onchange = fillConnection;
for (const input of document.querySelectorAll(
  "#api-url,#api-model,#api-key,#api-json,#profile-name",
)) {
  input.addEventListener("input", () => {
    connectionDraftChanged = true;
    $("#connection-result").textContent = "";
    renderVerification();
  });
}
$("#api-official-preset").onclick = () => {
  if (connectionPending || $("#api-provider").value !== "chat") return;
  $("#api-url").value = "https://api.openai.com/v1";
  $("#api-model").value = "gpt-6-astra";
  $("#api-key").value = "";
  $("#api-json").checked = true;
  connectionDraftChanged = true;
  renderVerification();
  $("#connection-result").textContent =
    "已填入官方示例，尚未调用。填写自己的 Key 后可测试；模型权限以账号为准。";
  $("#api-key").focus();
};
$("#connection-close").onclick = () => $("#connection-dialog").close();
$("#connection-dialog").onclose = () => {
  $("#api-key").value = "";
};
async function saveConnection(testCall = false) {
  if (connectionPending) return;
  if (!$("#connection-form").reportValidity()) return;
  connectionPending = true;
  const provider = $("#api-provider").value;
  setConnectionBusy(true);
  $("#connection-result").textContent = testCall
    ? "正在测试调用…"
    : "正在保存…";
  try {
    const saved = await api("/api/connections", {
      provider,
      url: $("#api-url").value.trim(),
      model: $("#api-model").value.trim(),
      api_key: $("#api-key").value,
      json_mode: $("#api-json").checked,
    });
    $("#api-key").value = "";
    connectionValues = await api("/api/connections");
    connectionDraftChanged = false;
    $("#key-state").textContent = connectionValues[provider].key_configured
      ? "已配置"
      : "未配置";
    renderVerification();
    await refreshProviders();
    $("#provider").value = provider;
    await reset();
    if (testCall) {
      const result = await api(`/api/connections/${provider}/test`, {});
      $("#connection-result").textContent =
        result.ok === false
          ? `验证失败 · ${result.detail || "请检查接口配置"}`
          : `调用通过 · ${result.model} · ${result.latency_ms} ms${result.detail ? ` · ${result.detail}` : ""}`;
      connectionValues = await api("/api/connections");
      renderVerification();
      renderProviderStatus();
    } else {
      $("#connection-dialog").close();
      toast(
        `${storageLabel(saved.storage)} · ${verificationLabel(connectionValues[provider], provider)}`,
      );
    }
  } catch (error) {
    $("#connection-result").textContent = error.message;
    try {
      connectionValues = await api("/api/connections");
      renderVerification();
      renderProviderStatus();
    } catch {
      /* Preserve the original connection error. */
    }
  } finally {
    connectionPending = false;
    setConnectionBusy(false);
  }
}
function setConnectionBusy(busy) {
  for (const input of document.querySelectorAll(
    "#api-provider,#api-url,#api-model,#api-key,#api-json,#api-official-preset,#profile-name,#connection-save,#connection-test,#connection-profile",
  ))
    input.disabled = busy;
  if (editingProfileId) $("#api-provider").disabled = true;
}
async function saveProfile() {
  if (connectionPending || !$("#connection-form").reportValidity()) return;
  const name = $("#profile-name").value.trim();
  if (!name) {
    $("#connection-result").textContent = "请为模型配置起一个名字。";
    $("#profile-name").focus();
    return;
  }
  connectionPending = true;
  setConnectionBusy(true);
  $("#connection-result").textContent = "正在保存模型配置…";
  try {
    const saved = await api("/api/model-profiles", {
      ...(editingProfileId ? { id: editingProfileId } : {}),
      name,
      provider: $("#api-provider").value,
      url: $("#api-url").value.trim(),
      model: $("#api-model").value.trim(),
      api_key: $("#api-key").value,
      json_mode: $("#api-json").checked,
    });
    $("#api-key").value = "";
    await refreshProviders();
    await extensionsView?.refresh();
    $("#connection-dialog").close();
    toast(`${storageLabel(saved.storage)} · 未调用模型。`);
  } catch (error) {
    $("#connection-result").textContent = error.message;
  } finally {
    connectionPending = false;
    setConnectionBusy(false);
  }
}
$("#connection-form").onsubmit = (event) => {
  event.preventDefault();
  if (profileEditor) saveProfile();
  else saveConnection();
};
$("#connection-test").onclick = () => saveConnection(true);
$("#connection-profile").onclick = saveProfile;

async function boot() {
  try {
    await refreshProviders();
    connectionValues = await api("/api/connections");
    $("#task-goal").textContent = config.tasks.transfer.goal;
    await loadScene();
    renderState(await api("/api/state"));
    const poll = async () => {
      try {
        if (!resetting && !controlPending) {
          const next = await api("/api/state");
          if (!resetting && !controlPending) {
            if (activeId !== next.id) await loadScene();
            renderState(next);
          }
        }
      } catch (e) {
        $("#connection").textContent = "连接已断开";
      }
      setTimeout(
        poll,
        document.hidden
          ? 3000
          : comparisonVisible || extensionsVisible
            ? 1500
            : state?.status === "running"
              ? 180
              : 900,
      );
    };
    poll();
  } catch (e) {
    $("#loading").textContent = "场景加载失败";
    toast(e.message);
  }
}
boot();
