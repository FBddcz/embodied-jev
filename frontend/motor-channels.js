const channelNames = { x: "X 方向", y: "Y 方向", z: "Z 方向", gripper: "夹爪" };
const optionNames = { negative: "负方向", hold: "保持", positive: "正方向", open: "张开", close: "闭合" };

export function motorChannelsHtml(decision, escape) {
  const channels = decision?.channel_decisions;
  if (!channels) return '<div class="empty">等待四个动作通道的决策…</div>';
  return Object.entries(channelNames).map(([name, label]) => {
    const answer = channels[name];
    const options = name === "gripper" ? ["open", "hold", "close"] : ["negative", "hold", "positive"];
    return `<section class="motor-channel" data-channel="${name}"><div class="decision-meta"><span>${label}</span></div>${options.map(option => {
      const selected = answer?.choice === option;
      const probability = answer?.probabilities?.[option];
      const hasProbability = typeof probability === "number" && Number.isFinite(probability);
      const value = hasProbability ? `${(probability * 100).toFixed(1)}%` : selected ? "已选择" : "未提供概率";
      return `<div class="prob-row ${selected ? "selected" : ""}"><div class="prob-top"><span>${escape(optionNames[option])}</span><span>${value}</span></div>${hasProbability ? `<div class="bar"><div class="bar-fill" style="width:${probability * 100}%"></div></div>` : ""}</div>`;
    }).join("")}</section>`;
  }).join("");
}
