import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
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
};
const stageNames = {
  ready: "READY",
  deciding: "DECIDING",
  previewing: "SIMULATING",
  executing: "EXECUTING",
  observing: "OBSERVING",
  verified: "VERIFIED",
};
let config,
  state,
  currentTask = "transfer",
  activeId = null,
  replayMode = false,
  replayTimer = null,
  replayIndex = 0,
  historySignature = "",
  candidateSignature = "",
  resetting = false;
$("#app").innerHTML = `
<div class="app-shell">
 <header class="header">
  <div class="brand"><div class="brand-mark">${icon("scan-line")}</div><div><strong>行知</strong><span>EmbodiedJev</span></div></div>
  <div class="header-divider"></div><div class="header-context">具身决策实验室</div>
  <div class="header-right"><span class="engine-label"><span class="dot"></span>MUJOCO / PANDA</span><span class="version">v0.1</span><button class="icon-button mobile-settings" id="settings-open" title="实验参数" aria-label="实验参数">${icon("sliders-horizontal")}</button><button class="icon-button" id="export" title="导出实验记录" aria-label="导出实验记录">${icon("download")}</button></div>
 </header>
 <div class="body-grid">
 <div class="scrim" id="scrim"></div>
 <aside class="sidebar" id="sidebar">
  <section><div class="section-topline"><h2>实验任务</h2><span class="eyebrow">01 / SETUP</span><button class="icon-button close-settings" id="settings-close" aria-label="关闭参数">${icon("x")}</button></div>
   <div class="task-options"><button class="task-option active" data-task="transfer">${icon("move-up-right")}<span>搬运入盘</span><span class="task-number">01</span></button><button class="task-option" data-task="stack">${icon("layers-2")}<span>方块堆叠</span><span class="task-number">02</span></button><button class="task-option" data-task="barrier">${icon("route")}<span>越障搬运</span><span class="task-number">03</span></button></div>
   <p class="task-goal" id="task-goal"></p></section>
  <div class="divider"></div>
  <section><div class="section-topline"><h2>决策模型</h2><button class="icon-button connection-button" id="model-connect" title="模型连接" aria-label="模型连接">${icon("plug-zap")}</button></div><div class="select-wrap"><select id="provider" aria-label="决策模型"></select>${icon("chevron-down")}</div><div class="provider-status"><span class="dot"></span><span id="provider-note">离线 · 确定性策略</span></div></section>
  <div class="divider"></div>
  <section><div class="section-topline"><h2>执行参数</h2><span class="eyebrow">CONTROL</span></div>
   <div class="settings-row"><label for="seed">随机种子</label><input class="number-input" id="seed" type="number" min="0" max="99999" value="0"></div>
   <div class="settings-row"><label for="budget">动作预算</label><input class="number-input" id="budget" type="number" min="1" max="100" value="30"></div>
   <div class="settings-row"><span>动作预演</span><label class="switch"><input id="preview" type="checkbox" checked aria-label="动作预演"><span></span></label></div>
   <div class="settings-row"><label for="threshold">决策门槛</label><span class="range-label" id="threshold-value">0.55</span></div><input class="range" id="threshold" type="range" min="0" max="1" step="0.05" value="0.55"><div class="range-ticks"><span>0.00</span><span>1.00</span></div>
   <div class="settings-row"><label for="speed">执行速度</label><span class="range-label" id="speed-value">1.5×</span></div><input class="range" id="speed" type="range" min="0.5" max="4" step="0.5" value="1.5"><div class="range-ticks"><span>0.5×</span><span>4×</span></div>
  </section><div class="sidebar-bottom"><span>FRANKA PANDA</span><span>7 DOF + GRIPPER</span></div>
 </aside>
 <main class="workspace">
  <div class="scene-toolbar"><nav class="tabs"><button class="tab active" data-tab="scene">${icon("box")} 场景</button><button class="tab" data-tab="data">${icon("braces")} 观测</button></nav><div class="scene-tools"><button class="icon-button" id="camera-top" title="俯视" aria-label="俯视">${icon("scan")}</button><button class="icon-button" id="camera-home" title="复位视角" aria-label="复位视角">${icon("focus")}</button></div></div>
  <div class="viewport" id="viewport"><div class="viewport-label"><h1>Franka Panda</h1><p>MANIPULATION / <span id="scene-task">TRANSFER</span></p></div><div class="scene-status" id="scene-status"><span class="dot"></span><span id="status-text">待命</span></div><div class="scene-axis"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z</span><span>WORLD / m</span></div><span class="scene-bottom-right" id="scene-time">t = 0.00 s</span><div class="success-stamp" id="success-stamp">${icon("circle-check")}物体稳定 · 夹爪已撤离</div><div class="loading" id="loading">加载机器人场景…</div><pre class="raw-state" id="raw-state"></pre></div>
  <div class="telemetry"><div class="metric"><div class="metric-label">末端 X</div><div class="metric-value"><span id="tcp-x">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Y</div><div class="metric-value"><span id="tcp-y">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">末端 Z</div><div class="metric-value"><span id="tcp-z">—</span><small>m</small></div></div><div class="metric"><div class="metric-label">物体抬升</div><div class="metric-value"><span id="lift">0</span><small>mm</small></div></div></div>
  <div class="timeline"><button class="icon-button" id="replay-play" aria-label="播放轨迹" title="播放轨迹">${icon("play")}</button><div class="timeline-track"><div class="timeline-caption"><span id="timeline-label">EPISODE TIMELINE</span><span id="frame-label">0000 / 0000</span></div><input id="timeline" type="range" min="0" max="0" value="0" aria-label="轨迹时间轴"></div><button class="live-link" id="live">LIVE</button></div>
  <div class="controls"><button class="primary" id="run">${icon("play")}<span id="run-label">运行实验</span></button><button class="icon-button" id="step" title="单步执行" aria-label="单步执行">${icon("step-forward")}</button><button class="icon-button stop" id="stop" title="停止实验" aria-label="停止实验">${icon("square")}</button><button class="icon-button" id="reset" title="重置实验" aria-label="重置实验">${icon("rotate-ccw")}</button><span class="run-budget" id="run-budget">00 / 30 ACTIONS</span></div>
 </main>
 <aside class="inspector">
  <section class="inspector-section"><div class="section-topline"><h2>当前决策</h2><span class="eyebrow" id="stage">READY</span></div><div class="decision-title">${icon("git-branch")}<span id="decision-title">等待开始</span></div><div class="decision-meta"><span id="decision-provider">RULE BASELINE</span><span id="latency">— ms</span></div><div id="intent-panel" hidden><div class="decision-meta"><span>01 · 操作阶段</span><span id="intent-latency"></span></div><div class="probabilities" id="intent-probabilities"></div><div class="decision-meta"><span>02 · 执行动作</span></div></div><div class="probabilities" id="probabilities"><div class="empty">尚无候选动作</div></div></section>
  <section class="inspector-section"><div class="section-topline"><h2>物理反馈</h2><span class="eyebrow">FEEDBACK</span></div><div class="sensors"><span class="name">夹爪状态</span><span class="sensor-value" id="gripper">OPEN</span><span class="name">双侧接触</span><div class="contacts"><span class="contact" id="contact-l">L</span><span class="contact" id="contact-r">R</span></div><span class="name">目标支撑接触</span><span class="sensor-value" id="support">NO</span><span class="name">稳定时长</span><span class="sensor-value" id="stable">0.00 s</span><span class="name">动作预演</span><span class="sensor-value" id="preview-state">ON</span></div></section>
  <div class="event-heading"><div class="section-topline"><h2>执行记录</h2><span class="eyebrow" id="event-count">0 EVENTS</span></div></div><ol class="events" id="events"><li class="empty">暂无执行记录</li></ol><div class="inspector-footer"><span id="model-calls">0 MODEL CALLS</span><span id="tokens">0 TOKENS</span></div>
 </aside></div><footer class="bottom-bar"><div class="bottom-left"><span id="connection">CONNECTING</span><span>PHYSICS 500 Hz</span><span>GEOMETRY + CONTACTS</span></div><span class="bottom-right" id="episode-id">EPISODE / —</span></footer>
</div><div class="toast" id="toast" role="status"></div>
<dialog id="connection-dialog" class="connection-dialog" aria-labelledby="connection-title">
 <form id="connection-form">
  <div class="dialog-heading"><div><span class="eyebrow">MODEL CONNECTION</span><h2 id="connection-title">模型连接</h2></div><button type="button" class="icon-button" id="connection-close" aria-label="关闭模型连接">${icon("x")}</button></div>
  <label class="field-label" for="api-provider">接口类型</label><select id="api-provider"><option value="chat">OpenAI 兼容 API</option><option value="claude">Claude 原生 API</option><option value="jev">TypeSafe Jev</option><option value="local">Jev / 结构化决策 API</option></select>
  <label class="field-label" for="api-url">Base URL / 接口地址</label><input id="api-url" type="url" required placeholder="https://your-provider.example/v1" autocomplete="off">
  <label class="field-label" for="api-model">模型 ID</label><input id="api-model" required placeholder="平台提供的模型名称" autocomplete="off">
  <label class="field-label" for="api-key">API Key <span id="key-state">未配置</span></label><input id="api-key" type="password" placeholder="API Key" autocomplete="off" spellcheck="false">
  <label class="json-mode" id="json-mode-row"><input id="api-json" type="checkbox" checked>JSON 模式</label>
  <p class="connection-retention">密钥仅保存在本次服务进程中，重启后失效。</p>
  <div id="connection-result" class="connection-result" role="status"></div>
  <div class="dialog-actions"><button type="button" class="secondary" id="connection-test">${icon("plug-zap")}测试调用</button><button type="submit" class="primary" id="connection-save">保存连接</button></div>
 </form>
</dialog>`;
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
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
const scene = new THREE.Scene();
scene.background = new THREE.Color("#eef2f1");
const camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
camera.up.set(0, 0, 1);
const renderer = new THREE.WebGLRenderer({
  antialias: true,
  preserveDrawingBuffer: true,
});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
$("#viewport").prepend(renderer.domElement);
renderer.domElement.setAttribute("aria-label", "MuJoCo 机械臂三维场景");
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(0.33, 0, 0.25);
controls.minDistance = 0.65;
controls.maxDistance = 3.2;
controls.maxPolarAngle = Math.PI * 0.49;
function cameraHome() {
  camera.position.set(1.4, -1.65, 1.27);
  controls.target.set(0.32, 0, 0.24);
  controls.update();
}
cameraHome();
const hemisphere = new THREE.HemisphereLight(0xffffff, 0xa6b9ad, 1.3);
hemisphere.position.set(0, 0, 3);
scene.add(hemisphere);
const light = new THREE.DirectionalLight(0xffffff, 2.2);
light.position.set(-0.8, -1.1, 2.5);
light.castShadow = true;
light.shadow.mapSize.set(1024, 1024);
light.shadow.camera.left = -1.2;
light.shadow.camera.right = 1.2;
light.shadow.camera.top = 1.2;
light.shadow.camera.bottom = -1.2;
light.shadow.normalBias = 0.001;
light.shadow.bias = -0.0001;
scene.add(light);
const fill = new THREE.DirectionalLight(0xdce9ff, 0.6);
fill.position.set(1, 1, 1.4);
scene.add(fill);
const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(200, 200),
  new THREE.MeshStandardMaterial({ color: 0xeef2f1, roughness: 0.93 }),
);
floor.position.z = -0.075;
floor.receiveShadow = true;
scene.add(floor);
const robotGroup = new THREE.Group();
scene.add(robotGroup);
const geomObjects = new Map();
let lastFrame;
function renderFrame(frame) {
  if (!frame) return;
  if (
    lastFrame?.time === frame.time &&
    JSON.stringify(lastFrame.qpos) === JSON.stringify(frame.qpos)
  )
    return;
  lastFrame = frame;
  for (const [id, mesh] of geomObjects) {
    const p = frame.positions[id],
      r = frame.rotations[id];
    if (!p || !r) continue;
    mesh.position.set(...p);
    const matrix = new THREE.Matrix4().set(
      r[0],
      r[1],
      r[2],
      0,
      r[3],
      r[4],
      r[5],
      0,
      r[6],
      r[7],
      r[8],
      0,
      0,
      0,
      0,
      1,
    );
    mesh.quaternion.setFromRotationMatrix(matrix);
  }
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
  requestRender();
}
async function loadScene() {
  $("#loading").classList.remove("hidden");
  const data = await api("/api/scene");
  for (const child of [...robotGroup.children]) {
    child.geometry.dispose();
    child.material.dispose();
    robotGroup.remove(child);
  }
  geomObjects.clear();
  lastFrame = null;
  for (const g of data.geometries) {
    let geometry;
    if (g.type === 7) {
      const m = data.meshes[g.mesh];
      geometry = new THREE.BufferGeometry();
      geometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(m.vertices.flat(), 3),
      );
      geometry.setIndex(m.faces.flat());
      geometry.computeVertexNormals();
    } else if (g.type === 6) {
      geometry = new THREE.BoxGeometry(
        g.size[0] * 2,
        g.size[1] * 2,
        g.size[2] * 2,
      );
    } else if (g.type === 2) {
      geometry = new THREE.SphereGeometry(g.size[0], 24, 16);
    } else if (g.type === 5) {
      geometry = new THREE.CylinderGeometry(
        g.size[0],
        g.size[0],
        g.size[1] * 2,
        32,
      );
      geometry.rotateX(Math.PI / 2);
    } else {
      continue;
    }
    const [r, b, c, a] = g.color;
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(r, b, c).convertSRGBToLinear(),
      roughness: g.name === "cube_geom" ? 0.35 : 0.56,
      metalness: 0.08,
      transparent: a < 1,
      opacity: a,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    robotGroup.add(mesh);
    geomObjects.set(g.id, mesh);
  }
  $("#scene-task").textContent = data.task.toUpperCase();
  $("#loading").classList.add("hidden");
  requestRender();
}
const observer = new ResizeObserver(() => {
  const el = $("#viewport");
  renderer.setSize(el.clientWidth, el.clientHeight, false);
  camera.aspect = el.clientWidth / el.clientHeight;
  camera.updateProjectionMatrix();
  requestRender();
});
observer.observe($("#viewport"));
let renderRequested = false;
function requestRender() {
  if (renderRequested) return;
  renderRequested = true;
  requestAnimationFrame(() => {
    renderRequested = false;
    controls.update();
    renderer.render(scene, camera);
  });
}
controls.addEventListener("change", requestRender);
renderer.domElement.addEventListener("webglcontextlost", () =>
  toast("三维渲染上下文已丢失，请刷新页面"),
);
requestRender();

function renderState(s) {
  state = s;
  if (activeId !== s.id) {
    activeId = s.id;
    historySignature = "";
    candidateSignature = "";
    replayMode = false;
    replayRequest++;
    clearInterval(replayTimer);
    replayTimer = null;
    currentTask = s.task;
    for (const button of document.querySelectorAll("[data-task]")) {
      button.classList.toggle("active", button.dataset.task === s.task);
    }
    $("#task-goal").textContent = config.tasks[s.task].goal;
    $("#provider").value = s.provider;
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
    ["error", "exhausted", "uncertain"].includes(s.status),
  );
  $("#success-stamp").classList.toggle(
    "visible",
    s.status === "completed" && !replayMode,
  );
  $("#stage").textContent = stageNames[s.stage] || s.stage;
  $("#decision-title").textContent = s.phase ? phaseNames[s.phase] : "等待开始";
  if (s.provider === "minicpm" && s.model_runtime) {
    const runtime = s.model_runtime;
    const device = (runtime.device || "AUTO").toUpperCase();
    $("#provider-note").textContent = {
      not_loaded: "本地 · 首次决策加载权重",
      loading: `正在加载权重 · ${device}`,
      ready: `本地就绪 · ${device} · 候选概率`,
      error: `加载失败 · ${runtime.error || "请检查服务日志"}`,
    }[runtime.status];
    if (runtime.status === "loading") {
      $("#decision-title").textContent = "正在加载 MiniCPM5-2B";
      $("#stage").textContent = "LOADING";
    }
  }
  $("#decision-provider").textContent =
    s.provider === "baseline"
      ? "RULE BASELINE"
      : ["chat", "claude"].includes(s.provider)
        ? s.provider === "claude"
          ? "CLAUDE / TOOL"
          : "CHAT / JSON"
        : s.provider.toUpperCase();
  $("#latency").textContent = s.last_decision?.model_call
    ? s.last_decision.latency_ms.toFixed(0) + " ms"
    : "— ms";
  $("#run-budget").textContent =
    String(s.cycles).padStart(2, "0") + " / " + s.max_cycles + " ACTIONS";
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
  $("#run").disabled = ["completed", "stopped", "error", "exhausted"].includes(
    s.status,
  );
  $("#step").disabled = [
    "running",
    "completed",
    "stopped",
    "error",
    "exhausted",
  ].includes(s.status);
  $("#stop").disabled = ["idle", "completed", "stopped"].includes(s.status);
  $("#preview-state").textContent = s.preview ? "ON" : "OFF";
  $("#model-calls").textContent = s.model_calls + " MODEL CALLS";
  $("#tokens").textContent = s.input_tokens.toLocaleString() + " TOKENS";
  $("#timeline").max = Math.max(0, s.frame_count - 1);
  if (!replayMode) {
    $("#timeline").value = s.frame_count - 1;
    $("#frame-label").textContent =
      String(s.frame_count).padStart(4, "0") +
      " / " +
      String(s.frame_count).padStart(4, "0");
  }
  $("#replay-play").disabled = s.frame_count < 2 || s.status === "running";
  const signature = JSON.stringify([
    s.candidates,
    s.last_decision,
    s.last_intent,
  ]);
  if (signature !== candidateSignature) {
    candidateSignature = signature;
    const intent = s.last_intent;
    $("#intent-panel").hidden = !intent;
    $("#intent-latency").textContent = intent?.model_call
      ? intent.latency_ms.toFixed(0) + " ms"
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
    const p = s.last_decision?.probabilities || {};
    $("#probabilities").innerHTML = s.candidates.length
      ? s.candidates
          .map((c) => {
            const selected = s.last_decision?.choice === c.id;
            const label = !c.admitted
              ? "已拦截"
              : p[c.id] !== undefined
                ? (p[c.id] * 100).toFixed(1) + "%"
                : selected
                  ? "已选择"
                  : "待选择";
            const width =
              p[c.id] !== undefined ? p[c.id] * 100 : selected ? 100 : 0;
            return `<div class="prob-row ${selected ? "selected" : ""} ${!c.admitted ? "rejected" : ""}" title="${escape(c.rejection || "")}"><div class="prob-top"><span>${escape(c.label)}</span><span>${label}</span></div><div class="bar"><div class="bar-fill" style="width:${width}%"></div></div></div>`;
          })
          .join("")
      : '<div class="empty">尚无候选动作</div>';
  }
  const hsig = s.id + "-" + s.history.length;
  if (hsig !== historySignature) {
    historySignature = hsig;
    $("#event-count").textContent = s.history.length + " EVENTS";
    $("#events").innerHTML = s.history.length
      ? s.history
          .map(
            (h) =>
              `<li class="event"><div class="event-line"><span>${escape(h.label)}</span><small>${h.after.sim_seconds.toFixed(1)} s</small></div><div class="event-detail">${String(h.cycle).padStart(2, "0")} · ${h.after.held ? "BILATERAL CONTACT" : h.after.support_contact ? "TARGET CONTACT" : "POSE UPDATED"}${h.rejected_count ? " · " + h.rejected_count + " REJECTED" : ""}</div></li>`,
          )
          .join("")
      : '<li class="empty">暂无执行记录</li>';
    $("#events").scrollTop = $("#events").scrollHeight;
  }
  const locked = s.status === "running" || s.status === "paused";
  $("#model-connect").disabled = locked;
  $("#provider").disabled = locked;
  for (const el of document.querySelectorAll(
    ".task-option,#seed,#budget,#preview,#threshold,#speed",
  ))
    el.disabled = locked;
  $("#threshold").disabled =
    locked || ["baseline", "chat", "claude"].includes(s.provider);
  $("#threshold-value").textContent = ["baseline", "chat", "claude"].includes(
    s.provider,
  )
    ? "N/A"
    : Number($("#threshold").value).toFixed(2);
  $("#threshold").title = ["chat", "claude"].includes(s.provider)
    ? "生成式接口不提供原生候选概率"
    : "";
  if (s.provider === "chat")
    $("#provider-note").textContent = "API · 结构化选择";
  if (s.provider === "claude")
    $("#provider-note").textContent = "Claude API · 工具选择";
  if (s.provider === "local")
    $("#provider-note").textContent = "API · 候选概率";
  if (s.message && s.message !== renderState.lastMessage) {
    toast(s.message);
    renderState.lastMessage = s.message;
  }
  $("#connection").textContent = "● CONNECTED";
}

function setup() {
  return {
    task: currentTask,
    seed: Number($("#seed").value),
    provider: $("#provider").value,
    preview: $("#preview").checked,
    threshold: Number($("#threshold").value),
    max_cycles: Number($("#budget").value),
    speed: Number($("#speed").value),
  };
}
async function reset() {
  if (resetting) return false;
  resetting = true;
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
  }
}
async function control(action) {
  try {
    replayRequest++;
    replayMode = false;
    clearInterval(replayTimer);
    replayTimer = null;
    $("#timeline-label").textContent = "EPISODE TIMELINE";
    if (action === "start" && state.status === "idle") {
      if (!(await reset())) return;
    }
    renderState(
      await api("/api/control/" + action, {
        threshold: Number($("#threshold").value),
      }),
    );
  } catch (e) {
    toast(e.message);
  }
}
$("#run").onclick = () =>
  control(state.status === "running" ? "pause" : "start");
$("#step").onclick = async () => {
  if (state.status === "idle" && !(await reset())) return;
  await control("step");
};
$("#stop").onclick = () => control("stop");
$("#reset").onclick = reset;
$("#export").onclick = () => {
  const a = document.createElement("a");
  a.href = "/api/export";
  a.download = "episode.json";
  a.click();
};
$("#camera-home").onclick = cameraHome;
$("#camera-top").onclick = () => {
  camera.position.set(0.42, -0.001, 1.85);
  controls.target.set(0.4, 0, 0.05);
  controls.update();
};
for (const button of document.querySelectorAll("[data-task]"))
  button.onclick = async () => {
    currentTask = button.dataset.task;
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
  config = await api("/api/config");
  $("#provider").innerHTML = config.providers
    .map(
      (p) =>
        `<option value="${p.id}" ${p.ready ? "" : "disabled"}>${escape(p.name)}${p.ready ? "" : " · 未配置"}</option>`,
    )
    .join("");
}
let connectionValues = {};
function fillConnection() {
  const provider = $("#api-provider").value;
  const saved = connectionValues[provider] || {};
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
  $("#connection-result").textContent = "";
}
$("#model-connect").onclick = async () => {
  try {
    connectionValues = await api("/api/connections");
    $("#api-provider").value = ["jev", "local", "chat", "claude"].includes(
      state.provider,
    )
      ? state.provider
      : "chat";
    fillConnection();
    $("#connection-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
};
$("#api-provider").onchange = fillConnection;
$("#connection-close").onclick = () => $("#connection-dialog").close();
$("#connection-dialog").onclose = () => {
  $("#api-key").value = "";
};
async function saveConnection(testCall = false) {
  if (!$("#connection-form").reportValidity()) return;
  const provider = $("#api-provider").value;
  $("#connection-save").disabled = $("#connection-test").disabled = true;
  $("#connection-result").textContent = testCall
    ? "正在测试调用…"
    : "正在保存…";
  try {
    await api("/api/connections", {
      provider,
      url: $("#api-url").value.trim(),
      model: $("#api-model").value.trim(),
      api_key: $("#api-key").value,
      json_mode: $("#api-json").checked,
    });
    $("#api-key").value = "";
    connectionValues = await api("/api/connections");
    $("#key-state").textContent = connectionValues[provider].key_configured
      ? "已配置"
      : "未配置";
    await refreshProviders();
    $("#provider").value = provider;
    await reset();
    if (testCall) {
      const result = await api(`/api/connections/${provider}/test`, {});
      $("#connection-result").textContent =
        `调用通过 · ${result.model} · ${result.latency_ms} ms`;
    } else {
      $("#connection-dialog").close();
      toast("模型连接已保存");
    }
  } catch (error) {
    $("#connection-result").textContent = error.message;
  } finally {
    $("#connection-save").disabled = $("#connection-test").disabled = false;
  }
}
$("#connection-form").onsubmit = (event) => {
  event.preventDefault();
  saveConnection();
};
$("#connection-test").onclick = () => saveConnection(true);

async function boot() {
  try {
    await refreshProviders();
    $("#task-goal").textContent = config.tasks.transfer.goal;
    await loadScene();
    renderState(await api("/api/state"));
    const poll = async () => {
      try {
        if (!resetting) {
          const next = await api("/api/state");
          if (!resetting) {
            if (activeId !== next.id) await loadScene();
            renderState(next);
          }
        }
      } catch (e) {
        $("#connection").textContent = "DISCONNECTED";
      }
      setTimeout(poll, 120);
    };
    poll();
  } catch (e) {
    $("#loading").textContent = "场景加载失败";
    toast(e.message);
  }
}
boot();
