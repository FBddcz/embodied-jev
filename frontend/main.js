import { RobotScene } from "./robot-scene.js";
import { motorChannelsHtml } from "./motor-channels.js";
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
  incremental: "逐步 XYZ 决策",
  hierarchical: "分层 XYZ 决策",
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
const cameraSelections = {
  none: [],
  external: ["external"],
  wrist: ["wrist"],
  both: ["external", "wrist"],
};
function enabledCameras(snapshot) {
  if (snapshot.camera_views?.length) return snapshot.camera_views;
  if (["vision", "rgbd"].includes(snapshot.observation_mode))
    return snapshot.perception?.camera_views?.length
      ? snapshot.perception.camera_views
      : cameraSelections.both;
  return [];
}
function cameraSelection(views) {
  return views.length === 2 ? "both" : views[0] || "none";
}
function cameraNames(views) {
  return views.map((view) => (view === "wrist" ? "腕部" : "外部")).join("与");
}
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
  <section class="observation-setting"><label class="field-label" for="control-mode">动作决策方式</label><div class="select-wrap"><select id="control-mode" aria-describedby="control-help"><option value="skills">预设技能选择</option><option value="incremental">逐步 XYZ · 闭环规划</option><option value="hierarchical">分层 XYZ · 子目标规划</option></select>${icon("chevron-down")}</div><p id="control-help">选择预设技能，技能内部轨迹由程序执行。</p></section>
  <section class="observation-setting"><label class="field-label" for="observation-mode">观测来源</label><div class="select-wrap"><select id="observation-mode" aria-describedby="observation-help"><option value="privileged">仿真真值 · 默认</option><option value="rgbd">RGB-D 视觉 · 实验</option><option value="vision">直接图像 · 多模态模型</option></select>${icon("chevron-down")}</div><p id="observation-help">直接读取仿真中的物体位置。</p></section>
  <section class="observation-setting"><label class="field-label" for="camera-mode">启用相机</label><div class="select-wrap"><select id="camera-mode" aria-describedby="camera-help"><option value="none">无相机</option><option value="external">仅外部相机</option><option value="wrist">仅腕部相机</option><option value="both">双相机</option></select>${icon("chevron-down")}</div><p id="camera-help">无相机 · 模型使用仿真真值，非视觉输入。</p></section>
  <div class="divider"></div>
  <section class="input-section" id="input-section"><div class="section-topline"><h2>输入状态</h2><span class="eyebrow">m</span></div><p class="input-context" id="input-context">实时观测</p><table class="input-table"><thead><tr><th>位置</th><th>X</th><th>Y</th><th>Z</th></tr></thead><tbody id="input-positions"></tbody></table><div class="input-contacts" id="input-contacts">等待观测</div></section>
  <details class="advanced-settings" id="advanced-settings"><summary>执行设置 <span>预演 / 速度 / 预算</span></summary><section>
   <div class="settings-row"><label for="seed">随机种子</label><input class="number-input" id="seed" type="number" min="0" max="99999" value="0"></div>
   <div class="settings-row"><label for="budget">动作预算</label><input class="number-input" id="budget" type="number" min="1" max="200" value="30"></div>
   <div class="settings-row"><span>动作预演</span><label class="switch"><input id="preview" type="checkbox" checked aria-label="动作预演"><span></span></label></div>
   <div class="settings-row"><label for="threshold">决策门槛</label><span class="range-label" id="threshold-value">0.55</span></div><input class="range" id="threshold" type="range" min="0" max="1" step="0.05" value="0.55"><div class="range-ticks"><span>0.00</span><span>1.00</span></div>
   <div class="settings-row"><label for="speed">执行速度</label><span class="range-label" id="speed-value">1.5×</span></div><input class="range" id="speed" type="range" min="0.5" max="4" step="0.5" value="1.5"><div class="range-ticks"><span>0.5×</span><span>4×</span></div>
   <div class="evaluation-settings"><label class="field-label" for="intervention-kind">外部评测扰动</label><div class="select-wrap"><select id="intervention-kind" aria-describedby="intervention-help"><option value="none">不施加扰动</option><option value="object_shift">移动方块</option><option value="target_shift">移动目标</option></select>${icon("chevron-down")}</div><p id="intervention-help">在指定动作后注入外部位移，用于观察后续调整。它不是模型动作；设置在重置或开始实验时应用。</p><div id="intervention-fields" hidden><div class="settings-row"><label for="intervention-cycle">第几步后</label><input class="number-input" id="intervention-cycle" type="number" min="1" max="199" step="1" value="5" required></div><div class="settings-row"><label for="intervention-x">X 位移 / m</label><input class="number-input" id="intervention-x" type="number" min="-0.06" max="0.06" step="0.01" value="0.04" required></div><div class="settings-row"><label for="intervention-y">Y 位移 / m</label><input class="number-input" id="intervention-y" type="number" min="-0.06" max="0.06" step="0.01" value="0" required></div></div><p id="intervention-status" role="status" hidden></p><label class="evaluation-checkbox"><input id="shuffle-candidates" type="checkbox">打乱候选顺序</label><p>逐步 XYZ 模式按种子重排动作菜单，用于检查选择是否依赖排列位置。</p></div>
  </section></details><div class="sidebar-bottom"><span>FRANKA PANDA</span><span>7 自由度 · 双指夹爪</span></div>
 </aside>
 <main class="workspace">
  <div class="scene-toolbar"><nav class="tabs" aria-label="实验视图"><button class="tab active" data-tab="scene">${icon("box")} 场景</button><button class="tab" data-tab="vision">${icon("scan-line")} 视觉</button><button class="tab" data-tab="data">${icon("braces")} 观测</button><button class="tab decision-tab" id="decision-open">${icon("git-branch")} 决策</button></nav><div class="scene-tools"><button class="icon-button" id="camera-top" title="俯视" aria-label="俯视">${icon("scan")}</button><button class="icon-button" id="camera-home" title="复位视角" aria-label="复位视角">${icon("focus")}</button></div></div>
  <div class="viewport" id="viewport"><div class="viewport-label"><h1>Franka Panda</h1><p>MANIPULATION / <span id="scene-task">TRANSFER</span></p></div><div class="scene-status" id="scene-status"><span class="dot"></span><span id="status-text">待命</span></div><div class="scene-axis"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z</span><span>WORLD / m</span></div><span class="scene-bottom-right" id="scene-time">t = 0.00 s</span><div class="success-stamp" id="success-stamp">${icon("circle-check")}物体稳定 · 夹爪已撤离</div><div class="loading" id="loading">加载机器人场景…</div><pre class="raw-state" id="raw-state"></pre></div>
  <div class="telemetry"><div class="metric"><div class="metric-label">末端 X</div><div class="metric-value"><span id="tcp-x">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Y</div><div class="metric-value"><span id="tcp-y">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Z</div><div class="metric-value"><span id="tcp-z">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">物体抬升</div><div class="metric-value"><span id="lift">0</span><small>mm</small></div></div></div>
  <div class="timeline"><button class="icon-button" id="replay-play" aria-label="播放轨迹" title="播放轨迹">${icon("play")}</button><div class="timeline-track"><div class="timeline-caption"><span id="timeline-label">EPISODE TIMELINE</span><span id="frame-label">0000 / 0000</span></div><input id="timeline" type="range" min="0" max="0" value="0" aria-label="轨迹时间轴"></div><button class="live-link" id="live">LIVE</button></div>
  <div class="controls"><button class="primary" id="run">${icon("play")}<span id="run-label">运行实验</span></button><button class="icon-button" id="step" title="单步执行" aria-label="单步执行">${icon("step-forward")}</button><button class="icon-button stop" id="stop" title="停止实验" aria-label="停止实验">${icon("square")}</button><button class="icon-button" id="reset" title="重置实验" aria-label="重置实验">${icon("rotate-ccw")}</button><span class="run-budget" id="run-budget">00 / 30 ACTIONS</span></div>
 </main>
 <aside class="inspector" id="inspector" aria-label="决策与执行记录">
  <section class="inspector-section decision-section" id="decision-section" tabindex="-1"><div class="section-topline"><h2 id="decision-heading">当前决策</h2><span class="eyebrow" id="stage">READY</span></div><div class="decision-context"><span id="decision-context" role="status">实时 · 等待开始</span><button type="button" class="text-button" id="decision-live" hidden>返回实时</button></div><div class="decision-title">${icon("git-branch")}<span id="decision-title">等待开始</span></div><div class="decision-meta"><span id="decision-provider">RULE BASELINE</span><span id="latency">— ms</span></div><div id="intent-panel" hidden><div class="decision-meta"><span>01 · 操作阶段</span><span id="intent-latency"></span></div><div class="probabilities" id="intent-probabilities"></div><div class="decision-meta"><span>02 · 执行动作</span></div></div><div class="probabilities" id="probabilities"><div class="empty">尚无候选动作</div></div><p class="decision-note" id="decision-note"></p><div class="history-observations" id="history-observations" hidden><details><summary>执行前 · 结构化观测</summary><pre id="history-before"></pre></details><details><summary>执行后 · 结构化观测</summary><pre id="history-after"></pre></details><details><summary>候选与模型返回</summary><pre id="history-payload"></pre></details><details id="history-inputs-detail" hidden><summary>本步模型输入</summary><pre id="history-inputs"></pre></details></div></section>
  <section class="inspector-section"><div class="section-topline"><h2 id="feedback-heading">物理反馈</h2><span class="eyebrow">FEEDBACK</span></div><div class="sensors"><span class="name">夹爪状态</span><span class="sensor-value" id="gripper">OPEN</span><span class="name">双侧接触</span><div class="contacts"><span class="contact" id="contact-l">L</span><span class="contact" id="contact-r">R</span></div><span class="name">目标支撑接触</span><span class="sensor-value" id="support">NO</span><span class="name">稳定时长</span><span class="sensor-value" id="stable">0.00 s</span><span class="name">动作预演</span><span class="sensor-value" id="preview-state">ON</span></div></section>
  <div class="event-heading"><div class="section-topline"><h2>执行记录</h2><span class="eyebrow" id="event-count">0 步</span></div></div><ol class="events" id="events"><li class="empty">暂无执行记录</li></ol><details class="runtime-log" id="runtime-log"><summary>运行日志 <span id="log-count">0 条</span></summary><ol id="log-entries"></ol><p>仅展示最近 12 条，完整日志可随实验导出。</p></details><div class="inspector-footer"><span id="model-calls">调用 0 次</span><span id="tokens">输入 0 tokens</span></div>
 </aside></div><footer class="bottom-bar"><div class="bottom-left"><span id="connection">连接中</span><span>物理仿真 500 Hz</span><span id="observation-source">仿真真值 · 几何与接触</span></div><span class="bottom-right" id="episode-id">实验 / —</span></footer>
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
const visionPanel = document.createElement("section");
visionPanel.id = "vision-panel";
visionPanel.className = "vision-panel";
visionPanel.hidden = true;
visionPanel.setAttribute("aria-label", "相机视觉观测");
visionPanel.innerHTML = `
  <div class="vision-heading"><div><span class="eyebrow" id="vision-source-heading">PERCEPTION / RGB-D</span><h2>相机最近观测</h2></div><div class="vision-switch" aria-label="相机通道"><button type="button" data-vision-channel="rgb" aria-pressed="true" disabled>RGB</button><button type="button" data-vision-channel="depth" aria-pressed="false" disabled>深度</button></div></div>
  <div class="vision-view-row"><div class="vision-switch" aria-label="相机视角"><button type="button" data-vision-view="external" aria-pressed="true" disabled>外部相机</button><button type="button" data-vision-view="wrist" aria-pressed="false" disabled>腕部相机</button></div><span id="vision-view-help">外部相机 · 固定机位</span></div>
  <p class="vision-explanation">颜色检测已知物体，结合深度估计位置；夹爪与接触来自传感器。动作预演仍使用仿真安全筛选。</p>
  <div class="vision-image-wrap"><div id="vision-image-container"></div><p id="vision-empty" role="status">选择「RGB-D 视觉」并重置实验后，显示实际相机画面。</p><span id="vision-image-label" hidden>最近感知帧</span></div>
  <div class="vision-meta" id="vision-meta" hidden><div><span>观测来源</span><strong id="vision-source-label">RGB-D · 颜色检测</strong></div><div><span>感知耗时</span><strong id="vision-latency">—</strong></div><div><span>采集时刻</span><strong id="vision-time">—</strong></div><div><span id="vision-visibility-label">可见物体</span><strong id="vision-visibility">—</strong></div></div>
  <p class="vision-status" id="vision-status" role="status"></p><div class="vision-download-row"><button type="button" class="text-button" id="vision-retry" hidden>重试读取</button><button type="button" class="text-button" id="vision-export" disabled>下载观测帧</button><span>全部 RGB 视角 + SHA-256 清单 · ZIP</span></div><p class="vision-note" id="vision-note">这里显示最近一次感知画面；场景页展示当前仿真。此模式支持已知颜色物体，尚不具备通用视觉识别能力。</p>`;
$("#viewport").append(visionPanel);
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
const planningOutput = document.createElement("div");
planningOutput.id = "planning-output";
planningOutput.className = "planning-output";
planningOutput.hidden = true;
planningOutput.innerHTML =
  '<p class="planning-caption">本步模型说明 · 用于检查决策依据</p><dl><dt>行动意图</dt><dd id="planning-intent"></dd><dt>视觉依据</dt><dd id="planning-evidence"></dd><dt>选中动作</dt><dd id="planning-action"></dd><dt>输入图像</dt><dd id="planning-image"></dd></dl>';
$("#probabilities").before(planningOutput);
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
$(".viewport-label p").firstChild.textContent = "仿真画面 / ";
let visionChannel = "rgb",
  visionView = "external",
  cameraExportPending = false,
  visionRequestKey = "",
  visionRequest = 0,
  visionMetadata = null,
  visionImageKey = "";

function clearVisionImage(message) {
  visionImageKey = "";
  $("#vision-image-container").replaceChildren();
  $("#vision-image-label").hidden = true;
  $("#vision-empty").hidden = false;
  $("#vision-empty").textContent = message;
}

function renderVisionImage() {
  if (!visionMetadata?.capture_id || !state?.id) return;
  const key = `${state.id}:${visionMetadata.capture_id}:${visionView}:${visionChannel}`;
  if (key === visionImageKey) return;
  clearVisionImage("读取已采集画面…");
  visionImageKey = key;
  const image = new Image();
  image.id = "vision-image";
  image.alt =
    `${visionView === "wrist" ? "腕部" : "外部"} · ` +
    (visionChannel === "rgb"
      ? "MuJoCo 相机实际 RGB 画面"
      : "MuJoCo 相机实际深度画面");
  image.hidden = true;
  image.onload = () => {
    if (visionImageKey !== key) return;
    image.hidden = false;
    $("#vision-empty").hidden = true;
    $("#vision-image-label").hidden = false;
    $("#vision-image-label").textContent =
      `${visionChannel === "rgb" ? "RGB" : "深度"} · 最近感知帧${visionView === "wrist" ? " · 腕部相机" : ""}`;
  };
  image.onerror = () => {
    if (visionImageKey !== key) return;
    $("#vision-empty").textContent = "这张感知帧已更新或读取失败，请重试。";
    $("#vision-retry").hidden = false;
  };
  const query = new URLSearchParams({
    episode_id: state.id,
    capture_id: visionMetadata.capture_id,
    view: visionView,
  });
  image.src = `/api/perception/${visionChannel}.png?${query}`;
  $("#vision-image-container").replaceChildren(image);
}

function renderVisionMetadata(metadata) {
  const direct = state.observation_mode === "vision";
  const previewOnly = state.observation_mode === "privileged";
  $("#vision-meta").hidden = false;
  for (const button of document.querySelectorAll("[data-vision-channel]"))
    button.disabled = direct && button.dataset.visionChannel === "depth";
  const views = metadata.camera_views || enabledCameras(state);
  if (!views.includes(visionView)) visionView = views[0] || "external";
  for (const button of document.querySelectorAll("[data-vision-view]")) {
    button.hidden = !views.includes(button.dataset.visionView);
    button.disabled = !views.includes(button.dataset.visionView);
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.visionView === visionView),
    );
  }
  $("#vision-view-help").textContent =
    visionView === "wrist" ? "腕部相机 · 随机械臂移动" : "外部相机 · 固定机位";
  if (direct && visionChannel !== "rgb") {
    visionChannel = "rgb";
    for (const button of document.querySelectorAll("[data-vision-channel]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.visionChannel === "rgb"),
      );
  }
  $("#vision-source-label").textContent = direct
    ? "RGB 图像 → 多模态模型"
    : previewOnly
      ? "RGB 相机预览 · 不发送模型"
      : "RGB-D · 颜色检测";
  $("#vision-visibility-label").textContent = direct
    ? "空间关系"
    : previewOnly
      ? "用途"
      : "可见物体";
  $("#vision-latency").textContent = Number.isFinite(metadata.latency_ms)
    ? `${metadata.latency_ms.toFixed(1)} ms`
    : "—";
  $("#vision-time").textContent = Number.isFinite(metadata.sim_time)
    ? `t = ${metadata.sim_time.toFixed(2)} s`
    : "—";
  $("#vision-time").title = metadata.captured_at || "";
  const objects = Array.isArray(metadata.objects)
    ? metadata.objects
    : Object.entries(metadata.objects || {}).map(([id, value]) => ({
        id,
        ...value,
      }));
  const visible = objects.filter((object) => object.visible).length;
  $("#vision-visibility").textContent = direct
    ? "由图像判断"
    : previewOnly
      ? "仅预览"
      : objects.length
        ? `${visible} / ${objects.length}`
        : "—";
  const missing = objects
    .filter((object) => !object.visible)
    .map((object) => object.label || object.id);
  $("#vision-status").textContent =
    (previewOnly
      ? "相机仅供查看；模型使用仿真真值，非视觉输入。"
      : metadata.message) ||
    (direct
      ? "模型直接接收 RGB 图像；不提供物体或目标坐标。"
      : missing.length
        ? `遮挡或未检测到：${missing.join("、")}`
        : objects.length
          ? "当前已知物体可见"
          : "等待检测结果");
  $("#vision-status").classList.toggle(
    "has-alert",
    missing.length > 0 ||
      ["partial", "unavailable", "error"].includes(metadata.status),
  );
  renderVisionImage();
}

function renderVision(s) {
  const rgbd = s.observation_mode === "rgbd";
  const direct = s.observation_mode === "vision";
  const cameras = enabledCameras(s);
  const camera = cameras.length > 0;
  const cameraLabel = cameraNames(cameras);
  $("#vision-export").disabled =
    cameraExportPending || !s.id || !s.perception?.capture_id;
  $("#camera-help").textContent = !camera
    ? "无相机 · 模型使用仿真真值，非视觉输入。"
    : direct
      ? `模型接收${cameraLabel}相机图像。`
      : rgbd
        ? `${cameraLabel}相机用于 RGB-D 位置估计，模型接收检测坐标。`
        : `${cameraLabel}相机仅供查看；模型使用仿真真值，非视觉输入。`;
  for (const button of document.querySelectorAll("[data-vision-view]"))
    button.hidden = !cameras.includes(button.dataset.visionView);
  $("#control-help").textContent =
    s.control_mode === "hierarchical"
      ? `${s.provider === "baseline" ? "规则对照" : "模型"}每步先选子目标，再选 XYZ 方向和夹爪；程序计算几何与短步幅度。`
      : s.control_mode === "incremental"
      ? `${s.provider === "baseline" ? "规则基线" : "模型"}每步选择 XYZ 位移与夹爪动作，执行后重新观测。是否能规划须由实验检验。`
      : "选择预设技能，技能内部轨迹由程序执行。";
  $("#observation-help").textContent = direct
    ? `发送${cameraLabel} RGB 图像及机器人自身状态，不提供物体/目标坐标。需要逐步 XYZ 和支持图像的 Chat / Claude 模型。`
    : rgbd
      ? "发送颜色检测与深度估计的物体坐标；模型不直接接收图像。"
      : "模型读取仿真中的物体位置，不接收图像（非视觉输入）。";
  $("#observation-source").textContent = direct
    ? `模型输入 · ${cameraLabel} RGB + 自身状态`
    : rgbd
      ? "RGB-D 感知 + 接触传感器"
      : "非视觉输入 · 仿真真值与接触";
  if (visionPanel.hidden || comparisonVisible || extensionsVisible) return;
  $("#vision-source-heading").textContent = direct
    ? "MODEL INPUT / RGB"
    : rgbd
      ? "PERCEPTION / RGB-D"
      : "CAMERA PREVIEW";
  $(".vision-explanation").textContent = direct
    ? `${cameraLabel} RGB 图像直接送入多模态模型。模型结合末端位置、夹爪与接触反馈判断空间关系，再选择下一步动作；不提供物体和目标坐标。`
    : rgbd
      ? "颜色检测已知物体，结合深度估计位置；模型接收检测坐标，夹爪与接触来自传感器。动作预演仍使用仿真安全筛选。"
      : camera
        ? "相机仅供查看仿真画面；本轮模型读取仿真真值，不发送图像。"
        : "当前未启用相机；本轮模型读取仿真真值，属于非视觉实验。";
  $("#vision-note").textContent =
    `${replayMode ? "正在回放轨迹；这里仍是最近一次感知画面。" : "相机在决策与动作边界采集，等待模型返回时画面暂停，并非实时视频。"}${rgbd ? "此模式支持已知颜色物体，尚不具备通用视觉识别能力。" : `${cameras.includes("external") ? "外部相机机位固定。" : ""}${cameras.includes("wrist") ? "腕部相机随机械臂移动。" : ""}${direct ? "每步图像与简短视觉依据可随实验导出。" : "图像仅用于查看，不发送给模型。"}`}`;
  const capture = s.perception?.capture_id;
  if (!camera || !capture) {
    visionRequest++;
    visionRequestKey = "";
    visionMetadata = null;
    for (const button of document.querySelectorAll("[data-vision-channel]"))
      button.disabled = true;
    for (const button of document.querySelectorAll("[data-vision-view]"))
      button.disabled = true;
    $("#vision-meta").hidden = true;
    $("#vision-status").textContent = camera
      ? s.perception?.message || "等待感知采集"
      : "";
    $("#vision-retry").hidden = true;
    clearVisionImage(
      camera
        ? "尚无相机画面。感知就绪后将在这里显示。"
        : "选择「RGB-D 视觉」或「直接图像」会启用相机；也可在「启用相机」中单独开启预览。",
    );
    return;
  }
  const key = `${s.id}:${capture}`;
  if (key === visionRequestKey) return;
  visionRequestKey = key;
  visionMetadata = null;
  $("#vision-meta").hidden = true;
  $("#vision-retry").hidden = true;
  $("#vision-status").textContent = "";
  clearVisionImage("读取已采集画面…");
  const request = ++visionRequest;
  const query = new URLSearchParams({ episode_id: s.id, capture_id: capture });
  api(`/api/perception?${query}`)
    .then((metadata) => {
      if (request !== visionRequest || state?.id !== s.id) return;
      visionMetadata = metadata;
      renderVisionMetadata(metadata);
    })
    .catch((error) => {
      if (request !== visionRequest) return;
      clearVisionImage("相机画面暂不可用");
      $("#vision-status").textContent = error.message;
      $("#vision-retry").hidden = false;
    });
}

for (const button of document.querySelectorAll("[data-vision-channel]"))
  button.onclick = () => {
    visionChannel = button.dataset.visionChannel;
    for (const other of document.querySelectorAll("[data-vision-channel]"))
      other.setAttribute("aria-pressed", String(other === button));
    renderVisionImage();
  };
for (const button of document.querySelectorAll("[data-vision-view]"))
  button.onclick = () => {
    visionView = button.dataset.visionView;
    if (visionMetadata) renderVisionMetadata(visionMetadata);
  };
$("#vision-retry").onclick = () => {
  visionRequestKey = "";
  if (state) renderVision(state);
};
$("#vision-export").onclick = async () => {
  if (cameraExportPending || !state?.id || !state.perception?.capture_id)
    return;
  const episodeId = state.id;
  cameraExportPending = true;
  $("#vision-export").disabled = true;
  $("#vision-export").textContent = "读取观测帧…";
  try {
    const response = await fetch(
      `/api/export/cameras.zip?${new URLSearchParams({ episode_id: episodeId })}`,
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        typeof error.detail === "string"
          ? error.detail
          : "观测帧下载失败，请重试。",
      );
    }
    const file = await response.blob();
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = `camera-observations-${episodeId}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    toast(error.message);
  } finally {
    cameraExportPending = false;
    $("#vision-export").textContent = "下载观测帧";
    $("#vision-export").disabled = !state?.id || !state.perception?.capture_id;
  }
};
let lastFrame;
function renderFrame(frame) {
  if (!frame) return;
  sceneView.render(frame);
  lastFrame = frame;
  const o = frame.observation || {};
  ["x", "y", "z"].forEach(
    (k, i) =>
      ($(`#tcp-${k}`).textContent = Number.isFinite(o.tcp?.[i])
        ? o.tcp[i].toFixed(3)
        : "—"),
  );
  $("#lift").textContent = Number.isFinite(o.max_lift_m)
    ? (o.max_lift_m * 1000).toFixed(0)
    : "—";
  $("#gripper").textContent = o.gripper === "closed" ? "CLOSED" : "OPEN";
  $("#contact-l").classList.toggle(
    "on",
    o.finger_contacts?.includes("left") || false,
  );
  $("#contact-r").classList.toggle(
    "on",
    o.finger_contacts?.includes("right") || false,
  );
  $("#support").textContent =
    o.support_contact === undefined ? "—" : o.support_contact ? "YES" : "NO";
  $("#support").classList.toggle("on", o.support_contact);
  $("#stable").textContent = Number.isFinite(o.stable_seconds)
    ? o.stable_seconds.toFixed(2) + " s"
    : "—";
  $("#scene-time").textContent = Number.isFinite(o.sim_seconds)
    ? "t = " + o.sim_seconds.toFixed(2) + " s"
    : "—";
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
    decision_inputs: s.last_decision_inputs,
  };
}

function selectedActionSummary(action, before) {
  if (!action) return "等待选择";
  const delta =
    action.delta_xyz ||
    (action.target?.length === 3 && before?.tcp?.length === 3
      ? action.target.map((value, index) => value - before.tcp[index])
      : null);
  const motion = delta?.every(Number.isFinite)
    ? `ΔXYZ (${delta.map((value) => `${value >= 0 ? "+" : ""}${value.toFixed(3)}`).join(", ")}) m`
    : "ΔXYZ 未记录";
  return `${motion} · 夹爪${{ open: "张开", close: "闭合", closed: "闭合" }[action.gripper] || "保持"}`;
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
  const hierarchical = s.control_mode === "hierarchical";
  if (hierarchical && intentPanel.parentElement !== $("#decision-section"))
    planningOutput.before(intentPanel);
  else if (!hierarchical && intentPanel.parentElement !== $("#input-section"))
    $("#input-section").append(intentPanel);
  const incremental =
    hierarchical || shown.phase === "incremental" || s.control_mode === "incremental";
  const direct = s.observation_mode === "vision";
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
      : direct
        ? `模型输入 · ${cameraNames(enabledCameras(s))} RGB + 自身状态`
        : s.observation_mode === "rgbd"
          ? "最近 RGB-D 位置估计 · 接触传感器"
          : "仿真真值 · 位置与接触";
  $("#input-positions").innerHTML = [
    ["末端", "tcp"],
    ["方块", "object"],
    ["目标", "destination"],
  ]
    .map(
      ([label, key]) =>
        `<tr><th>${label}</th>${direct && key !== "tcp" ? '<td colspan="3" class="image-position">由图像判断</td>' : [0, 1, 2].map((index) => `<td>${Number.isFinite(observation?.[key]?.[index]) ? observation[key][index].toFixed(3) : "—"}</td>`).join("")}</tr>`,
    )
    .join("");
  $("#input-contacts").textContent = observation
    ? `夹爪${observation.gripper === "closed" ? "闭合" : "张开"} · ${observation.held ? "双侧抓持" : observation.finger_contacts?.length ? "单侧接触" : "未接触物体"}`
    : "等待观测";
  $("#intent-heading").textContent =
    (hierarchical ? "当前子目标" : "阶段选择") +
    (history ? " · 历史" : previous ? " · 上一条" : "");
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
  $("#planning-output").hidden = !incremental;
  $("#planning-output .planning-caption").textContent = hierarchical
    ? "本步子目标与执行指令"
    : "本步模型说明 · 用于检查决策依据";
  $("#planning-intent").textContent =
    (hierarchical && intent ? phaseNames[intent.choice] || intent.choice : decision?.intent) ||
    (decision ? "模型未提供行动意图" : "等待模型决策");
  $("#planning-evidence").textContent =
    decision?.visual_evidence ||
    (direct ? "模型尚未提供视觉依据" : "本步输入为结构化观测");
  const chosenAction =
    candidates.find((candidate) => candidate.id === decision?.choice) ||
    shown.action;
  $("#planning-action").textContent = selectedActionSummary(
    chosenAction,
    history?.before || shown.decision_inputs?.action?.state?.observation,
  );
  const imageHash = decision?.image_sha256;
  $("#planning-image").textContent = imageHash
    ? `SHA-256 ${typeof imageHash === "string" ? imageHash : JSON.stringify(imageHash)}`
    : direct
      ? "等待本步图像记录"
      : "未发送图像";
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
          ? hierarchical
            ? "每个通道单独选择；四组概率不合成为整步概率，也不代表任务成功率。"
            : incremental
            ? `${s.provider === "baseline" ? "规则基线" : "模型"}逐步选择位移和夹爪动作；物理成功不等于已验证规划能力。`
            : "候选由任务控制器生成；概率不代表任务成功率。"
          : "运行实验后查看动作选择。";
  if (
    decision?.probability_warning === "choice_below_reported_max" ||
    intent?.probability_warning === "choice_below_reported_max"
  ) {
    $("#decision-note").textContent =
      "API 选择与公布概率排序不一致；本步保留 API 返回的选择与原始概率。 " +
      $("#decision-note").textContent;
  }
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
  $("#intent-panel").hidden = (incremental && !hierarchical) || !intent;
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
  $("#probabilities").innerHTML = hierarchical
    ? motorChannelsHtml(decision, escape)
    : candidates.length
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
    "#model-connect,#provider,#control-mode,#observation-mode,#camera-mode,.task-option,#seed,#budget,#preview,#threshold,#speed,#intervention-kind,#shuffle-candidates",
  ))
    el.disabled = locked;
  const interventionEnabled = $("#intervention-kind").value !== "none";
  $("#intervention-fields").hidden = !interventionEnabled;
  for (const input of $("#intervention-fields").querySelectorAll("input"))
    input.disabled = locked || !interventionEnabled;
  $("#threshold").disabled =
    locked || ["baseline", "chat", "claude"].includes(state.provider);
}

let logSignature = "";
function renderLogs(s) {
  const events = [...(s.events || [])];
  for (const intervention of s.interventions || []) {
    if (
      !events.some(
        (event) =>
          event.event === "external_intervention" &&
          event.cycle === intervention.after_cycle,
      )
    )
      events.push({
        event: "external_intervention",
        level: "warning",
        time: intervention.sim_time,
        cycle: intervention.after_cycle,
        message: `外部评测扰动：${intervention.kind === "object_shift" ? "移动方块" : "移动目标"}，不是模型动作。`,
      });
  }
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
    $("#observation-mode").value = s.observation_mode || "privileged";
    $("#control-mode").value = s.control_mode || "skills";
    $("#camera-mode").value = cameraSelection(enabledCameras(s));
    $("#intervention-kind").value = s.intervention?.kind || "none";
    $("#intervention-cycle").value = s.intervention?.after_cycle ?? 5;
    $("#intervention-x").value = s.intervention?.delta_xy?.[0] ?? 0.04;
    $("#intervention-y").value = s.intervention?.delta_xy?.[1] ?? 0;
    $("#shuffle-candidates").checked = s.shuffle_candidates === true;
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
  renderVision(s);
  renderLogs(s);
  $("#intervention-status").hidden = !s.interventions?.length;
  $("#intervention-status").textContent = s.interventions?.length
    ? `已注入 ${s.interventions.length} 次外部评测扰动；详见运行日志。`
    : "";
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
  const inputTokens = s.input_tokens == null
    ? `未知（已知 ${s.token_usage?.input?.known_tokens?.toLocaleString() ?? 0}，${s.token_usage?.input?.known_calls ?? 0}/${s.model_calls} 次）`
    : s.input_tokens.toLocaleString();
  $("#tokens").textContent = "输入 " + inputTokens + " tokens";
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

function configuredIntervention() {
  const kind = $("#intervention-kind").value;
  if (kind === "none") return null;
  const after_cycle = Number($("#intervention-cycle").value);
  const delta_xy = [$("#intervention-x"), $("#intervention-y")].map((input) =>
    input.value.trim() === "" ? NaN : Number(input.value),
  );
  if (!Number.isInteger(after_cycle) || after_cycle < 1 || after_cycle > 199)
    throw new Error("外部评测扰动时刻必须为第 1–199 步后。");
  if (
    delta_xy.some(
      (value) => !Number.isFinite(value) || Math.abs(value) > 0.06,
    ) ||
    delta_xy.every((value) => value === 0)
  )
    throw new Error(
      "外部评测扰动的 X / Y 位移必须在 ±0.06 米内，且不能同时为零。",
    );
  return { kind, after_cycle, delta_xy };
}
function setup() {
  return {
    expected_episode_id: state?.id,
    task: currentTask,
    scene_config: configuredScene,
    user_context: configuredContext,
    observation_mode: $("#observation-mode").value,
    control_mode: $("#control-mode").value,
    camera_views: [...cameraSelections[$("#camera-mode").value]],
    intervention: configuredIntervention(),
    shuffle_candidates: $("#shuffle-candidates").checked,
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
    if ($("#observation-mode").value === "vision") {
      if ($("#control-mode").value !== "incremental")
        throw new Error(
          "直接图像需要「逐步 XYZ」动作决策。请先切换动作决策方式。",
        );
      if (
        !["chat", "claude"].includes(
          selectionConfig($("#provider").value, profileValues).provider,
        )
      )
        throw new Error(
          "直接图像需要支持图像的 Chat 或 Claude 模型。请先配置并选择模型连接。",
        );
    }
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
    visionPanel.hidden = el.dataset.tab !== "vision";
    $("#viewport").classList.toggle("vision-active", !visionPanel.hidden);
    $(".scene-tools").hidden = el.dataset.tab !== "scene";
    if (state) renderVision(state);
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
$("#provider").onchange = async () => {
  $("#provider-note").textContent =
    $("#provider").value === "baseline"
      ? "离线 · 确定性策略"
      : $("#provider").value === "jev"
        ? "远程 · TypeSafe API"
        : "本地 · 候选概率";
  if (!(await reset()))
    $("#provider").value = state?.profile_id
      ? "profile:" + state.profile_id
      : state?.provider || "baseline";
};
$("#control-mode").onchange = async () => {
  const oldBudget = $("#budget").value;
  if ($("#control-mode").value === "hierarchical" && Number(oldBudget) < 160)
    $("#budget").value = 160;
  else if ($("#control-mode").value === "incremental" && Number(oldBudget) === 30)
    $("#budget").value = 100;
  if (!(await reset())) {
    $("#control-mode").value = state?.control_mode || "skills";
    $("#budget").value = oldBudget;
  }
};
$("#observation-mode").onchange = async () => {
  if (
    ["vision", "rgbd"].includes($("#observation-mode").value) &&
    $("#camera-mode").value === "none"
  )
    $("#camera-mode").value = "both";
  if (!(await reset())) {
    $("#observation-mode").value = state?.observation_mode || "privileged";
    $("#camera-mode").value = cameraSelection(enabledCameras(state));
  }
};
$("#camera-mode").onchange = async () => {
  const noCamera = $("#camera-mode").value === "none";
  if (noCamera) $("#observation-mode").value = "privileged";
  if (!(await reset())) {
    $("#camera-mode").value = cameraSelection(enabledCameras(state));
    $("#observation-mode").value = state.observation_mode || "privileged";
  } else if (noCamera) toast("已关闭相机，模型使用仿真真值（非视觉输入）。");
};
$("#intervention-kind").onchange = updateControlAvailability;
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
  renderVision(state);
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
