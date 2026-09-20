import { RobotScene } from "./robot-scene.js";
import {
  selectionOptions,
  selectionConfig,
  profileReady,
} from "./model-options.js";
import "./comparison.css";

const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const phases = {
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
const statuses = {
  empty: "尚未开始",
  idle: "已就绪",
  queued: "等待运行",
  running: "运行中",
  paused: "已暂停",
  done: "全部结束",
  stopped: "已停止",
  completed: "任务成功",
  uncertain: "低于决策门槛",
  exhausted: "预算耗尽",
  error: "执行异常",
  stalled: "决策停滞",
};
const numeric = (value, digits = 1) =>
  Number.isFinite(value) ? value.toFixed(digits) : "—";

export async function createComparison(container, { api, toast }) {
  const $ = (selector) => container.querySelector(selector);
  let [config, { profiles }] = await Promise.all([
    api("/api/config"),
    api("/api/model-profiles"),
  ]);
  let sceneConfig = {},
    userContext = {},
    snapshot = { id: null, status: "empty", lanes: [] };
  let active = false,
    busy = false,
    refreshing = false,
    timer,
    pendingRefresh = Promise.resolve();
  let replayMode = false,
    replayRequest = 0,
    replayTimer,
    replayPlaying = false,
    replayTime = 0;
  let desiredReplayTime = null,
    replayLoading = false,
    replayQueue = Promise.resolve();
  const scenes = new Map(),
    completed = new Map();
  container.innerHTML = `
    <div class="comparison-heading"><div><p class="eyebrow">同一任务 · 独立决策</p><h1>模型对比</h1><p>相同起点，观察不同模型如何选择和执行。实时画面各自推进，回放按仿真时间对齐。</p></div><span class="comparison-status" id="cmp-status">尚未开始</span></div>
    <details class="comparison-setup" id="cmp-setup" open><summary>对比设置 <span id="cmp-setup-summary">选择 2–3 个模型</span></summary><div class="comparison-settings">
      <label>共同任务<select id="cmp-task"><option value="transfer">搬运入盘</option><option value="stack">方块堆叠</option><option value="barrier">越障搬运</option></select></label>
      <label>运行方式<select id="cmp-mode"><option value="sequential">依次运行 · 更省资源</option><option value="parallel">并行运行 · 最多 2 路</option></select></label>
      <label>比较数量<select id="cmp-count"><option value="2">2 个模型</option><option value="3">3 个模型</option></select></label>
    </div><div class="comparison-lane-setup" id="cmp-lane-setup"></div>
    <details class="comparison-advanced"><summary>共同执行参数</summary><div class="comparison-settings">
      <label>随机种子<input id="cmp-seed" type="number" min="0" max="99999" value="0"></label>
      <label>动作预算<input id="cmp-budget" type="number" min="1" max="100" value="30"></label>
      <label>概率门槛<input id="cmp-threshold" type="number" min="0" max="1" step="0.05" value="0"></label>
      <label>执行速度<input id="cmp-speed" type="number" min="0.5" max="4" step="0.5" value="1.5"></label>
      <label class="comparison-checkbox"><input id="cmp-preview" type="checkbox" checked> 动作预演</label>
    </div><p>门槛仅用于返回原生候选概率的接口。任务、起点、预算一致，模型仍可能走出不同轨迹。</p></details>
    <p id="cmp-preset-label" class="comparison-hint" hidden></p><p class="comparison-hint">API 地址和 Key 继承默认连接或「扩展 → 模型配置」。这里可覆盖模型 ID；填写名称不代表账号已获使用权限。未配置 API 时可先用两个规则基线体验。</p></details>
    <div class="comparison-toolbar"><button class="primary" id="cmp-start">开始对比</button><button class="secondary" id="cmp-pause" disabled>暂停</button><button class="secondary" id="cmp-stop" disabled>停止</button><button class="secondary" id="cmp-export" disabled>导出记录</button></div>
    <p id="cmp-message" class="comparison-message" role="status"></p>
    <div class="comparison-replay" id="cmp-replay" hidden><div><strong id="cmp-time-mode">实时 · 各路独立推进</strong><span id="cmp-time-range"></span></div><div class="comparison-replay-controls"><button class="secondary" id="cmp-replay-play">播放回放</button><input id="cmp-timeline" type="range" min="0" max="0" step="any" value="0" aria-label="对比统一仿真时间轴"><output id="cmp-time">0.00 s</output><button class="text-button" id="cmp-live">返回实时</button></div></div>
    <div class="comparison-cards" id="cmp-cards"><div class="comparison-empty">选择模型后开始对比，真实场景和决策会显示在这里。</div></div>
    <p class="comparison-notes" id="cmp-notes"></p>`;

  function setupRows() {
    const previous = [...container.querySelectorAll(".lane-provider")].map(
      (select, index) => ({
        provider: select.value,
        model: container.querySelector(`#cmp-model-${index}`).value,
      }),
    );
    $("#cmp-lane-setup").innerHTML = Array.from(
      { length: Number($("#cmp-count").value) },
      (_, index) =>
        `<fieldset><legend>模型 ${index + 1}</legend><label>决策接口<select class="lane-provider" id="cmp-provider-${index}" aria-label="模型 ${index + 1} 决策接口">${selectionOptions(config, profiles, escape)}</select></label><label>模型 ID <span>可选覆盖</span><input id="cmp-model-${index}" class="lane-model" aria-label="模型 ${index + 1} 模型 ID" placeholder="继承已有配置" autocomplete="off"></label></fieldset>`,
    ).join("");
    container.querySelectorAll(".lane-provider").forEach((select, index) => {
      const previousValue = previous[index]?.provider;
      select.value =
        previousValue &&
        [...select.options].some((option) => option.value === previousValue)
          ? previousValue
          : "baseline";
      $(`#cmp-model-${index}`).value = previous[index]?.model || "";
      const update = () => {
        const fixed = ["baseline", "minicpm"].includes(
          selectionConfig(select.value, profiles).provider,
        );
        $(`#cmp-model-${index}`).disabled = fixed;
        if (fixed) $(`#cmp-model-${index}`).value = "";
      };
      select.onchange = update;
      update();
    });
  }
  setupRows();
  $("#cmp-count").onchange = setupRows;
  $("#cmp-task").onchange = () => {
    sceneConfig = {};
    userContext = {};
    renderPresetLabel();
  };
  function renderPresetLabel() {
    $("#cmp-preset-label").hidden =
      !Object.keys(sceneConfig).length && !Object.keys(userContext).length;
    $("#cmp-preset-label").textContent =
      `共同场景：${sceneConfig.name || "自定义预设"} · 待开始时应用于所有模型。`;
  }
  async function refreshModels() {
    [config, { profiles }] = await Promise.all([
      api("/api/config"),
      api("/api/model-profiles"),
    ]);
  }

  function updateControls() {
    const running = snapshot.status === "running",
      paused = snapshot.status === "paused";
    $("#cmp-start").disabled = busy || running || paused;
    $("#cmp-start").textContent = snapshot.id ? "重新运行" : "开始对比";
    $("#cmp-pause").disabled = busy || !(running || paused);
    $("#cmp-pause").textContent = paused ? "继续" : "暂停";
    $("#cmp-stop").disabled = busy || !(running || paused);
    $("#cmp-export").disabled = !snapshot.id || busy;
    $("#cmp-replay-play").disabled = !snapshot.replay?.max_time || busy;
    $("#cmp-timeline").disabled = !snapshot.replay?.max_time || busy;
    for (const input of container.querySelectorAll(
      "#cmp-setup input,#cmp-setup select",
    ))
      input.disabled = busy || running || paused;
    container.querySelectorAll(".lane-provider").forEach((select, index) => {
      if (
        ["baseline", "minicpm"].includes(
          selectionConfig(select.value, profiles).provider,
        )
      )
        $(`#cmp-model-${index}`).disabled = true;
    });
    $(".comparison-toolbar").setAttribute("aria-busy", String(busy));
  }

  function stopReplay() {
    replayPlaying = false;
    clearTimeout(replayTimer);
    $("#cmp-replay-play").textContent = "播放回放";
  }
  function source(provider) {
    return (
      {
        baseline: "规则选择 · 无模型概率",
        minicpm: "候选 token 分数归一化",
        jev: "官方 API 返回候选概率",
        local: "结构化 API 返回候选概率",
        chat: "结构化选择 · 不提供候选概率",
        claude: "工具选择 · 不提供候选概率",
      }[provider] || provider
    );
  }
  function options(decision, labels) {
    if (!decision) return '<span class="comparison-muted">等待决策</span>';
    const entries = Object.entries(decision.probabilities || {});
    if (!entries.length)
      return `<span class="comparison-choice">${escape(labels[decision.choice] || decision.choice)} · 已选择</span>`;
    return entries
      .map(
        ([choice, probability]) =>
          `<div class="comparison-probability ${choice === decision.choice ? "selected" : ""}"><span>${escape(labels[choice] || choice)}</span><b>${numeric(probability * 100)}%</b><i style="--probability:${Math.min(100, Math.max(0, probability * 100))}%"></i></div>`,
      )
      .join("");
  }

  function renderLane(lane, recorded) {
    const card = container.querySelector(`[data-lane="${lane.id}"]`);
    if (!card) return;
    const session = lane.session;
    const frame = replayMode ? recorded?.frame : session.frame;
    scenes.get(lane.id)?.render(frame);
    const observation = frame?.observation || {};
    if (session.last_decision && session.candidates?.length)
      completed.set(lane.id, {
        intent: session.last_intent,
        decision: session.last_decision,
        candidates: session.candidates,
        phase: session.phase,
      });
    const previous = !session.last_decision && completed.has(lane.id);
    const choice = replayMode
      ? recorded?.decision
      : previous
        ? completed.get(lane.id)
        : {
            intent: session.last_intent,
            decision: session.last_decision,
            candidates: session.candidates,
            phase: session.phase,
          };
    const labels = Object.fromEntries(
      (choice?.candidates || []).map((candidate) => [
        candidate.id,
        candidate.label,
      ]),
    );
    if (choice?.action) labels[choice.action.id] = choice.action.label;
    const status = lane.status === "queued" ? "queued" : session.status;
    card.querySelector(".lane-status").textContent = replayMode
      ? `回放 ${numeric(recorded?.frame_time, 2)} s${recorded?.clamped ? " · 末帧" : ""}`
      : statuses[status] || status;
    card
      .querySelector(".lane-status")
      .classList.toggle(
        "failed",
        ["error", "exhausted", "uncertain", "stalled"].includes(status),
      );
    card.querySelector(".lane-model-name").textContent =
      lane.model ||
      lane.requested_model ||
      config.providers.find((provider) => provider.id === lane.provider)
        ?.name ||
      lane.provider;
    card.querySelector(".lane-source").textContent = source(lane.provider);
    card.querySelector(".lane-decision-mode").textContent = replayMode
      ? "该仿真时刻的决策"
      : previous
        ? "上一条选择 · 新决策计算中"
        : "当前决策";
    card.querySelector(".lane-phase").innerHTML = options(
      choice?.intent,
      phases,
    );
    card.querySelector(".lane-action").innerHTML = options(choice?.decision, {
      direct: "正常执行",
      gentle: "减速执行",
      hold: "保持不动",
      ...labels,
    });
    card.querySelector(".lane-pose").textContent =
      (observation.tcp || [])
        .map((position) => numeric(position, 3))
        .join(" / ") || "—";
    card.querySelector(".lane-fingers").textContent =
      `${observation.gripper === "closed" ? "闭合" : "张开"} · ${observation.held ? "双侧抓持" : observation.finger_contacts?.length ? "单侧接触" : "无物体接触"}`;
    card.querySelector(".lane-sim").textContent =
      `${numeric(observation.sim_seconds, 2)} s`;
    const latency =
      (choice?.intent?.model_call ? choice.intent.latency_ms : 0) +
      (choice?.decision?.model_call ? choice.decision.latency_ms : 0);
    card.querySelector(".lane-latency").textContent = latency
      ? `${numeric(latency, 0)} ms`
      : "无模型调用";
    card.querySelector(".lane-stat-title").textContent = replayMode
      ? "整轮累计统计（非该帧）"
      : "本路累计统计";
    card.querySelector(".lane-statistics").textContent =
      `调用 ${session.model_calls} 次 · 输入 ${session.input_tokens} / 输出 ${session.output_tokens || 0} tokens · 时长 ${numeric(session.wall_seconds, 2)} s`;
    card.querySelector(".lane-message").textContent = replayMode
      ? recorded?.clamped
        ? "本路已到达当前录制末帧。"
        : ""
      : session.message || "";
  }

  async function render(next) {
    const changed = next.id !== snapshot.id;
    snapshot = next;
    if (changed) {
      stopReplay();
      replayMode = false;
      replayRequest++;
      completed.clear();
      for (const scene of scenes.values()) scene.dispose();
      scenes.clear();
      if (next.id && !busy) {
        sceneConfig = next.config?.scene_config || {};
        userContext = next.config?.user_context || {};
        renderPresetLabel();
        for (const [field, key] of [
          ["task", "task"],
          ["seed", "seed"],
          ["budget", "max_cycles"],
          ["threshold", "threshold"],
          ["speed", "speed"],
        ])
          if (next.config?.[key] !== undefined)
            $(`#cmp-${field}`).value = next.config[key];
        $("#cmp-preview").checked = next.config?.preview !== false;
        $("#cmp-mode").value = next.mode;
        $("#cmp-count").value = next.lanes.length;
        setupRows();
        next.lanes.forEach((lane, index) => {
          const profileId = lane.profile_id || lane.session.profile_id;
          $(`#cmp-provider-${index}`).value = profileId
            ? "profile:" + profileId
            : lane.provider;
          $(`#cmp-model-${index}`).value = ["baseline", "minicpm"].includes(
            lane.provider,
          )
            ? ""
            : lane.requested_model || "";
        });
        $("#cmp-setup").open = false;
      }
      $("#cmp-cards").innerHTML =
        next.lanes
          .map(
            (lane, index) =>
              `<article class="comparison-card" data-lane="${escape(lane.id)}"><header><div><span class="comparison-lane-label">模型 ${index + 1} · ${escape(config.providers.find((provider) => provider.id === lane.provider)?.name || lane.provider)}</span><h2 class="lane-model-name"></h2></div><span class="lane-status"></span></header><div class="comparison-scene"><div class="comparison-camera"><button type="button" data-camera="home">复位视角</button><button type="button" data-camera="top">俯视</button></div></div><div class="comparison-card-body"><p class="lane-source"></p><p class="lane-decision-mode"></p><div class="comparison-decision-grid"><section><h3>阶段选择</h3><div class="lane-phase"></div></section><section><h3>动作输出</h3><div class="lane-action"></div></section></div><dl><dt>末端 X / Y / Z · m</dt><dd class="lane-pose"></dd><dt>夹爪 / 接触</dt><dd class="lane-fingers"></dd><dt>仿真时间</dt><dd class="lane-sim"></dd><dt>本次推理耗时</dt><dd class="lane-latency"></dd></dl><p class="lane-message"></p><details><summary class="lane-stat-title">本路累计统计</summary><p class="lane-statistics"></p></details></div></article>`,
          )
          .join("") ||
        '<div class="comparison-empty">选择模型后开始对比，真实场景和决策会显示在这里。</div>';
      $("#cmp-cards").style.setProperty("--lane-count", next.lanes.length || 2);
      if (next.id)
        await Promise.all(
          next.lanes.map(async (lane) => {
            const element = container.querySelector(
              `[data-lane="${lane.id}"] .comparison-scene`,
            );
            const scene = new RobotScene(element, {
              label: `${lane.id} 机械臂三维场景`,
              pixelRatio: 1.25,
              onError: toast,
            });
            scenes.set(lane.id, scene);
            scene.load(
              await api(
                `/api/comparison/scene/${lane.id}?comparison_id=${encodeURIComponent(next.id)}`,
              ),
            );
            element.querySelector('[data-camera="home"]').onclick = () =>
              scene.cameraHome();
            element.querySelector('[data-camera="top"]').onclick = () =>
              scene.cameraTop();
          }),
        );
    }
    $("#cmp-status").textContent = statuses[next.status] || next.status;
    $("#cmp-replay").hidden = !next.id;
    $("#cmp-time-range").textContent =
      `共同录制 ${numeric(next.replay?.common_time, 2)} s · 最长 ${numeric(next.replay?.max_time, 2)} s`;
    $("#cmp-timeline").max = next.replay?.max_time || 0;
    $("#cmp-notes").textContent = (next.notes || []).join(" ");
    if (!replayMode) {
      syncLiveTime();
      next.lanes.forEach((lane) => renderLane(lane));
    }
    updateControls();
  }

  function syncLiveTime() {
    const time = snapshot.replay?.max_time || 0;
    $("#cmp-time-mode").textContent = "实时 · 各路独立推进";
    $("#cmp-time").textContent = `${numeric(time, 2)} s`;
    $("#cmp-timeline").value = time;
  }

  function refresh() {
    if (refreshing || busy) {
      schedule();
      return;
    }
    refreshing = true;
    pendingRefresh = (async () => {
      try {
        await render(await api("/api/comparison"));
      } catch (error) {
        $("#cmp-message").textContent = error.message;
      } finally {
        refreshing = false;
        schedule();
      }
    })();
    return pendingRefresh;
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(
      refresh,
      document.hidden || !active
        ? 3000
        : snapshot.status === "running"
          ? 180
          : 900,
    );
  }
  async function control(action) {
    if (busy || !snapshot.id) return;
    const comparisonId = snapshot.id;
    busy = true;
    updateControls();
    try {
      await pendingRefresh;
      if (snapshot.id !== comparisonId)
        throw new Error("比较已更新，请检查当前状态后再操作。");
      await render(
        await api(`/api/comparison/control/${action}`, {
          comparison_id: comparisonId,
        }),
      );
    } catch (error) {
      $("#cmp-message").textContent = error.message;
    } finally {
      busy = false;
      updateControls();
    }
  }
  $("#cmp-start").onclick = async () => {
    if (busy) return;
    const expectedId = snapshot.id;
    if (
      ![...container.querySelectorAll("#cmp-setup input")].every((input) =>
        input.reportValidity(),
      )
    )
      return;
    busy = true;
    updateControls();
    $("#cmp-message").textContent = "";
    try {
      await pendingRefresh;
      if (snapshot.id !== expectedId)
        throw new Error("比较已更新，请检查当前状态后再创建。");
      await refreshModels();
      const lanes = [...container.querySelectorAll(".lane-provider")].map(
        (select, index) => {
          const selected = selectionConfig(select.value, profiles);
          const ready = selected.profile_id
            ? profileReady(
                profiles.find((profile) => profile.id === selected.profile_id),
              )
            : config.providers.find(
                (provider) => provider.id === selected.provider,
              )?.ready;
          if (!ready)
            throw new Error("请先在实验台配置该模型连接，再开始对比。");
          const model = $(`#cmp-model-${index}`).value.trim();
          return { ...selected, ...(model ? { model } : {}) };
        },
      );
      const next = await api("/api/comparison", {
        task: $("#cmp-task").value,
        scene_config: sceneConfig,
        user_context: userContext,
        seed: Number($("#cmp-seed").value),
        preview: $("#cmp-preview").checked,
        threshold: Number($("#cmp-threshold").value),
        max_cycles: Number($("#cmp-budget").value),
        speed: Number($("#cmp-speed").value),
        mode: $("#cmp-mode").value,
        lanes,
        expected_comparison_id: expectedId,
      });
      await render(next);
      $("#cmp-setup").open = false;
      $("#cmp-setup-summary").textContent =
        `${lanes.length} 路 · ${$("#cmp-task").selectedOptions[0].textContent} · ${$("#cmp-mode").value === "parallel" ? "最多两路并行" : "依次运行"}`;
      await render(
        await api("/api/comparison/control/start", {
          comparison_id: snapshot.id,
        }),
      );
    } catch (error) {
      $("#cmp-message").textContent = error.message;
    } finally {
      busy = false;
      updateControls();
    }
  };
  $("#cmp-pause").onclick = () => {
    stopReplay();
    desiredReplayTime = null;
    replayRequest++;
    replayMode = false;
    syncLiveTime();
    control(snapshot.status === "paused" ? "start" : "pause");
  };
  $("#cmp-stop").onclick = () => control("stop");
  $("#cmp-export").onclick = () => {
    const link = document.createElement("a");
    link.href = `/api/comparison/export?comparison_id=${encodeURIComponent(snapshot.id)}`;
    link.download = `comparison-${snapshot.id}.json`;
    link.click();
  };
  async function replayAt(time) {
    const request = ++replayRequest;
    const comparisonId = snapshot.id;
    if (snapshot.status === "running") await control("pause");
    if (request !== replayRequest || busy || snapshot.id !== comparisonId)
      return;
    if (snapshot.status === "running")
      throw new Error("暂停未完成，请稍后再回放。");
    replayMode = true;
    const result = await api(
      `/api/comparison/replay?time=${time}&comparison_id=${encodeURIComponent(comparisonId)}`,
    );
    if (request !== replayRequest || result.id !== snapshot.id) return;
    replayTime = result.time;
    $("#cmp-time-mode").textContent = "录制回放 · 按统一仿真时间对齐";
    $("#cmp-time").textContent = `${numeric(result.time, 2)} s`;
    $("#cmp-timeline").value = result.time;
    snapshot.lanes.forEach((lane) =>
      renderLane(
        lane,
        result.lanes.find((item) => item.id === lane.id),
      ),
    );
  }
  function queueReplay(time) {
    desiredReplayTime = time;
    if (replayLoading) return replayQueue;
    replayLoading = true;
    replayQueue = (async () => {
      while (desiredReplayTime !== null) {
        const next = desiredReplayTime;
        desiredReplayTime = null;
        await replayAt(next);
      }
    })().finally(() => {
      replayLoading = false;
    });
    return replayQueue;
  }
  $("#cmp-timeline").oninput = () => {
    stopReplay();
    queueReplay(Number($("#cmp-timeline").value)).catch((error) => {
      $("#cmp-message").textContent = error.message;
    });
  };
  $("#cmp-live").onclick = () => {
    stopReplay();
    desiredReplayTime = null;
    replayRequest++;
    replayMode = false;
    syncLiveTime();
    snapshot.lanes.forEach((lane) => renderLane(lane));
  };
  $("#cmp-replay-play").onclick = async () => {
    if (replayPlaying) {
      stopReplay();
      return;
    }
    replayPlaying = true;
    $("#cmp-replay-play").textContent = "暂停回放";
    if (!replayMode || replayTime >= snapshot.replay.max_time) replayTime = 0;
    const tick = async () => {
      if (!replayPlaying || !active) return;
      try {
        await queueReplay(Math.min(snapshot.replay.max_time, replayTime + 0.1));
      } catch (error) {
        $("#cmp-message").textContent = error.message;
        stopReplay();
        return;
      }
      if (replayTime >= snapshot.replay.max_time) {
        stopReplay();
        return;
      }
      if (replayPlaying) replayTimer = setTimeout(tick, 120);
    };
    await tick();
  };
  await refresh();
  async function editableDraft() {
    await pendingRefresh;
    if (busy || ["running", "paused"].includes(snapshot.status))
      throw new Error("请先停止模型对比，再修改预设或模型。");
  }
  return {
    async applyPreset(preset) {
      await editableDraft();
      $("#cmp-task").value = preset.task;
      sceneConfig = structuredClone(preset.scene_config);
      userContext = structuredClone(preset.user_context);
      renderPresetLabel();
      $("#cmp-setup").open = true;
      $("#cmp-message").textContent =
        "预设已填入共同设置，点击开始后才会创建场景和调用模型。";
    },
    async applyModel(profileId) {
      await editableDraft();
      await refreshModels();
      setupRows();
      if (!profiles.some((profile) => profile.id === profileId))
        throw new Error("模型配置已失效，请重新选择。");
      $("#cmp-provider-0").value = "profile:" + profileId;
      $("#cmp-provider-0").onchange();
      $("#cmp-setup").open = true;
      $("#cmp-message").textContent =
        "已填入模型 1；其余模型可在对比设置中选择。尚未调用模型。";
    },
    async setActive(value) {
      active = value;
      if (!value) {
        stopReplay();
        schedule();
        return;
      }
      clearTimeout(timer);
      try {
        await refreshModels();
        setupRows();
        updateControls();
        await refresh();
      } catch (error) {
        $("#cmp-message").textContent = error.message;
      }
    },
  };
}
