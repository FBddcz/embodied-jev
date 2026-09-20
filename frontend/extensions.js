import {
  selectionOptions,
  selectionConfig,
  storageLabel,
  storageDescription,
} from "./model-options.js";
import "./extensions.css";

const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const STORE = "embodied-jev-presets-v1";
const TASKS = ["transfer", "stack", "barrier"];
function finiteJSON(value) {
  return typeof value === "number"
    ? Number.isFinite(value)
    : value && typeof value === "object"
      ? Object.values(value).every(finiteJSON)
      : true;
}
function validatedObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label}必须是 JSON 对象。`);
  if (!finiteJSON(value)) throw new Error(`${label}不能包含非有限数值。`);
  if (new TextEncoder().encode(JSON.stringify(value)).length > 8192)
    throw new Error(`${label}不能超过 8 KB。`);
  return value;
}
function objectJSON(text, label) {
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label}需要有效的 JSON。`);
  }
  return validatedObject(value, label);
}
function validatePreset(value) {
  if (!value || value.format !== "embodied-jev-preset-v1")
    throw new Error("不是支持的 embodied-jev-preset-v1 预设。");
  if (
    Object.keys(value).some(
      (key) =>
        !["format", "name", "task", "scene_config", "user_context"].includes(
          key,
        ),
    )
  )
    throw new Error(
      "预设只能包含名称、任务、场景与额外要求，不能包含模型连接或密钥。",
    );
  if (
    typeof value.name !== "string" ||
    !value.name.trim() ||
    value.name.length > 80 ||
    !TASKS.includes(value.task)
  )
    throw new Error("请填写有效名称并选择基础任务模板。");
  const scene = validatedObject(value.scene_config ?? {}, "场景配置"),
    context = validatedObject(value.user_context ?? {}, "额外决策要求");
  if (
    Object.keys(scene).some(
      (key) =>
        !["name", "source_xy", "target_xy", "barrier_height"].includes(key),
    )
  )
    throw new Error(
      "场景配置含不支持的字段；仅支持 name、source_xy、target_xy、barrier_height。",
    );
  if (
    scene.name !== undefined &&
    (typeof scene.name !== "string" || scene.name.length > 80)
  )
    throw new Error("场景名称必须是 80 字以内的文字。");
  for (const key of ["source_xy", "target_xy"]) {
    const xy = scene[key];
    if (xy === undefined || (key === "source_xy" && xy === null)) continue;
    if (
      !Array.isArray(xy) ||
      xy.length !== 2 ||
      xy.some((value) => typeof value !== "number") ||
      xy[0] < 0.3 ||
      xy[0] > 0.58 ||
      xy[1] < -0.28 ||
      xy[1] > 0.28
    )
      throw new Error(
        "场景坐标需要为 [X, Y]：X 在 0.30–0.58，Y 在 -0.28–0.28 米之间。",
      );
  }
  if (
    scene.barrier_height != null &&
    (value.task !== "barrier" ||
      typeof scene.barrier_height !== "number" ||
      scene.barrier_height < 0.02 ||
      scene.barrier_height > 0.16)
  )
    throw new Error("障碍高度仅适用于越障任务，范围为 0.02–0.16 米。");
  const secret = (item) =>
    item &&
    typeof item === "object" &&
    Object.entries(item).some(
      ([key, nested]) =>
        /^(api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token)$/i.test(
          key,
        ) || secret(nested),
    );
  if (secret(scene) || secret(context))
    throw new Error("预设检测到密钥字段；请通过模型连接管理凭据。");
  return {
    format: "embodied-jev-preset-v1",
    name: value.name.trim(),
    task: value.task,
    scene_config: scene,
    user_context: context,
  };
}
function downloadJSON(name, value) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function createExtensions(
  container,
  { api, openConnection, applyPreset, applyModel },
) {
  const $ = (selector) => container.querySelector(selector);
  let profiles = [],
    config,
    probeBusy = false,
    actionBusy = false;
  let saved = [];
  try {
    saved = JSON.parse(localStorage.getItem(STORE) || "[]").map(validatePreset);
  } catch {
    saved = [];
  }
  container.innerHTML = `<div class="extension-heading"><div><p class="eyebrow">配置与实验工具</p><h1>扩展</h1><p>保存模型连接、复用场景预设，或单独测试一次决策。</p></div><label>应用目标<select id="ext-target"><option value="workbench">实验台</option><option value="comparison">模型对比</option></select></label></div>
  <details class="extension-section" id="ext-models" open><summary>模型配置 <span>为不同平台和模型起名字</span></summary><div class="extension-body"><div class="extension-actions"><button class="secondary" id="ext-new-profile">新建模型配置</button></div><div id="ext-profile-list" class="extension-profile-list"></div><p class="extension-hint" id="ext-profile-storage"></p></div></details>
  <details class="extension-section" id="ext-presets"><summary>任务与场景预设 <span>保存 / 导入 / 导出 JSON</span></summary><div class="extension-body"><div class="extension-grid"><label>已保存的预设<select id="ext-preset-select"><option value="">新预设</option></select></label><label>预设名称<input id="ext-preset-name" maxlength="80" value="我的搬运场景"></label><label>基础任务<select id="ext-task"><option value="transfer">搬运入盘</option><option value="stack">方块堆叠</option><option value="barrier">越障搬运</option></select></label><label>物体 X · m<input id="ext-source-x" type="number" min="0.30" max="0.58" step="any" placeholder="留空按种子生成"></label><label>物体 Y · m<input id="ext-source-y" type="number" min="-0.28" max="0.28" step="any" placeholder="留空按种子生成"></label><label>目标 X · m<input id="ext-target-x" type="number" min="0.30" max="0.58" step="any" value="0.43"></label><label>目标 Y · m<input id="ext-target-y" type="number" min="-0.28" max="0.28" step="any" value="0.18"></label><label>障碍高度 · m<input id="ext-barrier-height" type="number" min="0.02" max="0.16" step="any" value="0.11" disabled></label></div><label class="extension-json-label">额外决策要求 · user_context JSON<textarea id="ext-user-context" spellcheck="false" rows="5">{}</textarea></label><p class="extension-hint">JSON 中请勿填写 API Key、密码或私人凭据。额外要求独立追加，不会覆盖实测位置、接触或成功条件。规则基线不读取自然语言指令；全新任务语义与机器人类型需要通过代码扩展。场景是否可达、安全，以后端校验为准。</p><div class="extension-actions"><button class="primary" id="ext-apply-preset">应用预设</button><button class="secondary" id="ext-save-preset">保存到浏览器</button><button class="secondary" id="ext-import-preset">导入 JSON</button><button class="secondary" id="ext-export-preset">导出 JSON</button><input id="ext-import-file" type="file" accept="application/json,.json" hidden></div></div></details>
  <details class="extension-section" id="ext-probe"><summary>输入测试 <span>只选择候选，不移动机器人</span></summary><div class="extension-body"><div class="extension-grid"><label>决策模型<select id="ext-probe-provider"></select></label><label>模型 ID · 可选覆盖<input id="ext-probe-model" placeholder="继承已有配置" disabled></label></div><label class="extension-json-label">观察数据 · JSON<textarea id="ext-probe-observation" rows="6" spellcheck="false">{"gripper":"open","object_reachable":true}</textarea></label><label class="extension-json-label">决策问题<textarea id="ext-probe-question" rows="3">Choose the next safe action using the supplied observation.</textarea></label><label class="extension-json-label">候选 · key → 描述 JSON<textarea id="ext-probe-options" rows="5" spellcheck="false">{"approach":"Move above the reachable object","hold":"Keep the current pose"}</textarea></label><div class="extension-actions"><button class="primary" id="ext-probe-run">测试一次决策</button></div><p class="extension-hint">此处是独立输入实验，不执行机械臂控制。规则基线固定选择第一个候选，用于检查输入流程，不验证语义。</p><div id="ext-probe-result" class="extension-probe-result" hidden><p id="ext-probe-summary"></p><details><summary>模型返回与实际输入</summary><pre id="ext-probe-json"></pre></details></div></div></details><p id="ext-message" class="extension-message" role="status"></p>`;

  function showSaved() {
    $("#ext-preset-select").innerHTML =
      '<option value="">新预设</option>' +
      saved
        .map(
          (preset, index) =>
            `<option value="${index}">${escape(preset.name)}</option>`,
        )
        .join("");
  }
  function setPreset(preset) {
    $("#ext-preset-name").value = preset.name;
    $("#ext-task").value = preset.task;
    $("#ext-source-x").value = preset.scene_config.source_xy?.[0] ?? "";
    $("#ext-source-y").value = preset.scene_config.source_xy?.[1] ?? "";
    $("#ext-target-x").value = preset.scene_config.target_xy?.[0] ?? 0.43;
    $("#ext-target-y").value = preset.scene_config.target_xy?.[1] ?? 0.18;
    $("#ext-barrier-height").value = preset.scene_config.barrier_height ?? 0.11;
    $("#ext-barrier-height").disabled = preset.task !== "barrier";
    $("#ext-user-context").value = JSON.stringify(preset.user_context, null, 2);
  }
  function currentPreset() {
    if (
      ![
        ...container.querySelectorAll("#ext-presets input:not([type=file])"),
      ].every((input) => input.reportValidity())
    )
      throw new Error("请检查场景参数范围。");
    const x = $("#ext-source-x").value,
      y = $("#ext-source-y").value;
    if (!!x !== !!y) throw new Error("物体 X 和 Y 需要一起填写，或同时留空。");
    if (!$("#ext-target-x").value || !$("#ext-target-y").value)
      throw new Error("请填写目标 X 和 Y。");
    const scene_config = {
      name: $("#ext-preset-name").value.trim(),
      source_xy: x ? [Number(x), Number(y)] : null,
      target_xy: [
        Number($("#ext-target-x").value),
        Number($("#ext-target-y").value),
      ],
    };
    if ($("#ext-task").value === "barrier")
      scene_config.barrier_height = Number($("#ext-barrier-height").value);
    return validatePreset({
      format: "embodied-jev-preset-v1",
      name: scene_config.name,
      task: $("#ext-task").value,
      scene_config,
      user_context: objectJSON($("#ext-user-context").value, "额外决策要求"),
    });
  }
  async function checkedPreset() {
    return api("/api/presets/validate", currentPreset());
  }
  async function handle(action) {
    if (actionBusy) return;
    actionBusy = true;
    $("#ext-message").textContent = "正在处理…";
    try {
      await action();
    } catch (error) {
      $("#ext-message").textContent = error.message;
    } finally {
      actionBusy = false;
    }
  }
  $("#ext-task").onchange = () => {
    $("#ext-barrier-height").disabled = $("#ext-task").value !== "barrier";
  };
  $("#ext-preset-select").onchange = () => {
    const index = $("#ext-preset-select").value;
    if (index !== "") setPreset(saved[Number(index)]);
  };
  $("#ext-save-preset").onclick = () =>
    handle(async () => {
      const preset = await checkedPreset();
      saved = [
        ...saved.filter((item) => item.name !== preset.name),
        preset,
      ].slice(-30);
      localStorage.setItem(STORE, JSON.stringify(saved));
      showSaved();
      $("#ext-preset-select").value = saved.length - 1;
      $("#ext-message").textContent = "已保存到本浏览器，未运行推理。";
    });
  $("#ext-export-preset").onclick = () =>
    handle(async () =>
      downloadJSON("embodied-jev-preset.json", await checkedPreset()),
    );
  $("#ext-import-preset").onclick = () => $("#ext-import-file").click();
  $("#ext-import-file").onchange = () =>
    handle(async () => {
      const file = $("#ext-import-file").files[0];
      if (!file) return;
      if (file.size > 40000) throw new Error("预设文件过大。");
      const preset = await api(
        "/api/presets/validate",
        validatePreset(JSON.parse(await file.text())),
      );
      setPreset(preset);
      $("#ext-import-file").value = "";
      $("#ext-message").textContent = "已导入预设草稿；点击应用才会改变场景。";
    });
  $("#ext-apply-preset").onclick = () =>
    handle(async () => {
      await applyPreset(await checkedPreset(), $("#ext-target").value);
      $("#ext-message").textContent = "预设已应用，尚未运行推理。";
    });
  $("#ext-new-profile").onclick = () => openConnection();
  $("#ext-profile-list").onclick = (event) =>
    handle(async () => {
      const button = event.target.closest("[data-profile]");
      if (!button) return;
      if (button.dataset.action === "edit")
        await openConnection(button.dataset.profile);
      else {
        await applyModel(button.dataset.profile, $("#ext-target").value);
        $("#ext-message").textContent = "已选择模型配置，尚未调用模型。";
      }
    });
  $("#ext-probe-provider").onchange = () => {
    const selected = selectionConfig($("#ext-probe-provider").value, profiles);
    $("#ext-probe-model").disabled = ["baseline", "minicpm"].includes(
      selected.provider,
    );
    if ($("#ext-probe-model").disabled) $("#ext-probe-model").value = "";
  };
  $("#ext-probe-run").onclick = async () => {
    if (probeBusy) return;
    probeBusy = true;
    $("#ext-probe-result").hidden = true;
    $("#ext-probe-run").disabled = true;
    for (const input of container.querySelectorAll(
      "#ext-probe textarea,#ext-probe input,#ext-probe select",
    ))
      input.disabled = true;
    $("#ext-message").textContent = "正在测试一次决策，机器人不会移动…";
    try {
      const selected = selectionConfig(
        $("#ext-probe-provider").value,
        profiles,
      );
      const options = objectJSON($("#ext-probe-options").value, "候选");
      if (
        Object.keys(options).length < 2 ||
        Object.keys(options).length > 12 ||
        Object.values(options).some(
          (value) => typeof value !== "string" || !value.trim(),
        )
      )
        throw new Error("请提供 2–12 个候选，每个描述必须是非空字符串。");
      const question = $("#ext-probe-question").value.trim();
      if (!question) throw new Error("请填写决策问题。");
      const model = $("#ext-probe-model").value.trim();
      const result = await api("/api/decision/probe", {
        ...selected,
        ...(model ? { model } : {}),
        observation: objectJSON($("#ext-probe-observation").value, "观察数据"),
        question,
        options,
      });
      $("#ext-probe-result").hidden = false;
      $("#ext-probe-summary").textContent =
        `选择 ${result.decision.choice} · ${result.decision.model_call ? `${Number(result.decision.latency_ms).toFixed(0)} ms` : "无模型调用"}`;
      $("#ext-probe-json").textContent = JSON.stringify(result, null, 2);
      $("#ext-message").textContent =
        result.message || "输入测试完成，机器人未执行动作。";
    } catch (error) {
      $("#ext-message").textContent = error.message;
    } finally {
      probeBusy = false;
      $("#ext-probe-run").disabled = false;
      for (const input of container.querySelectorAll(
        "#ext-probe textarea,#ext-probe input,#ext-probe select",
      ))
        input.disabled = false;
      $("#ext-probe-provider").onchange();
    }
  };
  showSaved();
  async function refresh(target) {
    if (target) $("#ext-target").value = target;
    const previous = $("#ext-probe-provider").value;
    let storage;
    [config, { profiles, storage }] = await Promise.all([
      api("/api/config"),
      api("/api/model-profiles"),
    ]);
    $("#ext-profile-list").innerHTML =
      profiles
        .map(
          (profile) =>
            `<article><div><strong>${escape(profile.name)}</strong><p>${escape(profile.provider)} · ${escape(profile.model)} · ${profile.key_configured ? "密钥已配置" : "未配置密钥"}</p><p>${escape(storageLabel(profile.storage || storage))}</p></div><div><button class="text-button" data-profile="${escape(profile.id)}" data-action="use">使用</button><button class="text-button" data-profile="${escape(profile.id)}" data-action="edit">编辑</button></div></article>`,
        )
        .join("") ||
      '<p class="extension-hint">尚无具名模型配置，可先使用内置规则基线。</p>';
    $("#ext-profile-storage").textContent =
      `${storageDescription(storage)} 密钥不会写入浏览器预设或实验导出。`;
    $("#ext-probe-provider").innerHTML = selectionOptions(
      config,
      profiles,
      escape,
    );
    $("#ext-probe-provider").value =
      previous &&
      [...$("#ext-probe-provider").options].some(
        (option) => option.value === previous,
      )
        ? previous
        : "baseline";
    $("#ext-probe-provider").onchange();
  }
  await refresh();
  return { refresh };
}
