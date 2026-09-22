const $ = (id) => document.getElementById(id);
const video = $("video");
let catalog, selected, category = "all", activeIndex = -1, pendingSeek = null, activeTrack = 0;
function decisionsForSelection() {
  return selected?.decision_tracks?.[activeTrack]?.decisions || selected?.decisions || [];
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function text(id, value) { $(id).textContent = value ?? ""; }
function renderCategories() {
  $("categories").replaceChildren(...catalog.categories.map((item) => {
    const button = node("button", item.label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(category === item.id));
    button.addEventListener("click", () => {
      category = item.id;
      renderCategories(); renderCards();
      const list = filtered();
      if (list.length) select(list.includes(selected) ? selected.id : list[0].id);
      $("replay").hidden = !list.length;
      if (!list.length) { video.pause(); $("chart-section").hidden = true; }
    });
    return button;
  }));
}
function filtered() {
  return catalog.experiments.filter((item) => category === "all" || item.category === category);
}
function renderCards() {
  const entries = filtered();
  $("empty-state").hidden = entries.length > 0;
  text("record-count", entries.length + " RECORDED EXPERIMENTS");
  $("cards").replaceChildren(...entries.map((entry) => {
    const button = node("button", undefined, "experiment-card");
    button.type = "button"; button.setAttribute("aria-pressed", String(selected?.id === entry.id));
    const image = node("img", undefined, "card-image");
    image.src = entry.poster; image.alt = ""; image.loading = "lazy";
    const copy = node("div", undefined, "card-copy");
    copy.append(node("small", entry.kicker), node("strong", entry.title), node("p", entry.badge));
    button.append(image, copy);
    button.addEventListener("click", () => select(entry.id));
    return button;
  }));
}
function select(id) {
  selected = catalog.experiments.find((item) => item.id === id) || catalog.experiments[0];
  if (!selected) return;
  video.pause(); video.src = selected.video; video.poster = selected.poster;
  video.playbackRate = Number($("speed").value);
  video.load(); activeIndex = -1; pendingSeek = null; activeTrack = 0;
  const tracks = selected.decision_tracks || [];
  $("track-control").hidden = !tracks.length;
  $("decision-track").replaceChildren(...tracks.map((track, i) => {
    const option = node("option", track.label); option.value = String(i); return option;
  }));
  $("replay").hidden = false;
  $("replay").classList.toggle("wide", !decisionsForSelection().length);
  $("replay").setAttribute("aria-busy", "false");
  text("experiment-kicker", selected.kicker); text("experiment-badge", selected.badge);
  text("experiment-title", selected.title); text("experiment-description", selected.description);
  text("playback-note", selected.playback); text("experiment-note", selected.note);
  $("metrics").replaceChildren(...selected.metrics.map((item) => {
    const cell = node("div", undefined, "metric");
    cell.append(node("span", item.label), node("strong", item.value)); return cell;
  }));
  const links = selected.links.map((item) => {
    const a = node("a", item.label + " ↗"); a.href = item.url;
    a.target = "_blank"; a.rel = "noreferrer"; return a;
  });
  const download = node("a", "下载 MP4 ↓");
  download.href = selected.download || selected.video; download.download = "";
  $("record-links").replaceChildren(download, ...links);
  $("chart-section").hidden = !selected.chart;
  if (selected.chart) $("chart").src = selected.chart;
  else $("chart").removeAttribute("src");
  renderTimeline();
  history.replaceState(null, "", "#run=" + encodeURIComponent(selected.id));
  renderCards(); updateDecision(true);
}
function renderTimeline() {
  const decisions = decisionsForSelection();
  $("timeline").replaceChildren(...decisions.map((item, i) => {
    const button = node("button"); button.type = "button";
    button.title = item.index + " · " + item.label;
    button.setAttribute("aria-label", "跳到决策 " + item.index);
    button.addEventListener("click", () => seek(i)); return button;
  }));
  if (!decisions.length) {
    $("timeline").append(node("span", "六局决策已叠加在录像画面内；可暂停或调整播放速度查看。", "timeline-note"));
  }
}
function seek(index) {
  const decisions = decisionsForSelection();
  if (!decisions.length) return;
  index = Math.max(0, Math.min(index, decisions.length - 1));
  const target = decisions[index].time;
  if (video.readyState > 0) video.currentTime = target;
  else pendingSeek = target;
  updateDecision(true, target);
}
function updateDecision(force = false, at = pendingSeek ?? video.currentTime) {
  const decisions = decisionsForSelection();
  if (!decisions.length) {
    text("stage", "六局并排对照"); text("intent", "观看录像内的阶段、动作和模型决策。");
    text("evidence", selected?.note); text("latency", "");
    text("probability-note", "各回合数据见原始记录"); text("decision-index", "6 PAIRS");
    $("probabilities").replaceChildren();
    $("trajectory-path").setAttribute("d", ""); $("trajectory-dot").setAttribute("visibility", "hidden");
    $("previous").disabled = $("next").disabled = true; return;
  }
  if (selected.decision_tracks && at + .03 < decisions[0].time) {
    activeIndex = -1;
    text("stage", "等待首个决策"); text("intent", "模型正在处理当前观测。");
    text("evidence", "点击决策时间轴可跳到该次请求完成的时刻。");
    text("decision-index", "00 / " + decisions.length); text("latency", "");
    text("probability-note", "尚无返回结果"); $("probabilities").replaceChildren();
    $("trajectory-path").setAttribute("d", ""); $("trajectory-dot").setAttribute("visibility", "hidden");
    [...$("timeline").children].forEach((button) => { button.classList.remove("active"); button.setAttribute("aria-current", "false"); });
    $("previous").disabled = true; $("next").disabled = false; return;
  }
  let index = 0;
  for (let i = 0; i < decisions.length; i++) if (decisions[i].time <= at + .03) index = i;
  if (!force && activeIndex === index) return;
  activeIndex = index;
  const d = decisions[index];
  text("decision-index", String(index + 1).padStart(2, "0") + " / " + decisions.length);
  text("stage", d.stage); text("intent", d.intent || d.label); text("evidence", d.evidence);
  text("latency", d.latency_ms == null ? "—" : (d.latency_ms / 1000).toFixed(2) + "s / API");
  const channels = Object.entries(d.channels || {});
  let bars = channels.flatMap(([name, item]) => item.probabilities?.[item.choice] == null ? [] :
    [{label: name + " · " + item.choice, value: item.probabilities[item.choice], selected: true}]);
  if (!bars.length) bars = Object.entries(d.probabilities || {}).map(([name, value]) => ({label: name, value, selected: name === d.stage}));
  $("probabilities").replaceChildren(...bars.map((item) => {
    const row = node("div", undefined, "probability-row" + (item.selected ? " selected" : ""));
    const track = node("div", undefined, "probability-track");
    const fill = node("div", undefined, "probability-fill");
    fill.style.width = Math.max(0, Math.min(1, item.value)) * 100 + "%"; track.append(fill);
    row.append(node("span", item.label), track, node("span", (item.value * 100).toFixed(0) + "%")); return row;
  }));
  text("probability-note", bars.length ? "接口返回概率 · 非成功率" : "此接口未提供概率");
  [...$("timeline").children].forEach((button, i) => {
    button.classList.toggle("active", i === index);
    button.setAttribute("aria-current", i === index ? "step" : "false");
  });
  $("previous").disabled = index === 0; $("next").disabled = index === decisions.length - 1;
  drawTrajectory(decisions, index);
}
function drawTrajectory(decisions, index) {
  const points = decisions.map((d) => d.tcp).filter((p) => Array.isArray(p) && p.length >= 2);
  if (points.length !== decisions.length) {
    $("trajectory-path").setAttribute("d", ""); $("trajectory-dot").setAttribute("visibility", "hidden"); return;
  }
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const minX = Math.min(...xs), minY = Math.min(...ys);
  const range = Math.max(Math.max(...xs) - minX, Math.max(...ys) - minY, .01);
  const projected = points.map((p) => [20 + (p[0] - minX) / range * 260, 115 - (p[1] - minY) / range * 100]);
  $("trajectory-path").setAttribute("d", projected.slice(0, index + 1).map((p, i) => (i ? "L" : "M") + p.join(",")).join(" "));
  $("trajectory-dot").setAttribute("cx", projected[index][0]); $("trajectory-dot").setAttribute("cy", projected[index][1]);
  $("trajectory-dot").setAttribute("visibility", "visible");
}
video.addEventListener("timeupdate", () => updateDecision());
video.addEventListener("seeked", () => updateDecision(true));
video.addEventListener("loadedmetadata", () => {
  video.playbackRate = Number($("speed").value);
  if (pendingSeek !== null) { video.currentTime = pendingSeek; pendingSeek = null; updateDecision(true); }
});
video.addEventListener("error", () => text("playback-note", "录像暂时无法加载，请使用「下载 MP4」链接。"));
$("speed").addEventListener("change", () => { video.playbackRate = Number($("speed").value); });
$("decision-track").addEventListener("change", () => {
  activeTrack = Number($("decision-track").value); activeIndex = -1;
  renderTimeline(); updateDecision(true);
});
$("previous").addEventListener("click", () => seek(activeIndex - 1));
$("next").addEventListener("click", () => seek(activeIndex + 1));
try {
  const response = await fetch("./catalog.json");
  if (!response.ok) throw new Error("catalog unavailable");
  catalog = await response.json();
  renderCategories();
  const id = new URLSearchParams(location.hash.slice(1)).get("run");
  select(id || catalog.experiments[0]?.id);
} catch {
  $("empty-state").hidden = false; text("empty-state", "实验目录暂时无法加载。请刷新页面，或前往 GitHub 查看原始视频与结果。");
  $("replay").hidden = true;
}
