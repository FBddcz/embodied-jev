export function profileReady(profile) {
  return !!(
    profile?.url &&
    profile.model &&
    (!["jev", "claude"].includes(profile.provider) || profile.key_configured)
  );
}

export function storageLabel(storage) {
  return storage?.persistent ? "已安全保存在本机" : "仅保存在本次服务进程";
}

export function storageDescription(storage) {
  if (storage?.persistent)
    return "配置保存在本机，Key 由系统钥匙串保护。刷新页面或重启服务后可以继续使用。";
  const message =
    storage?.message || "当前仅保存在服务进程中，重启服务后需要重新配置。";
  return message.includes("刷新页面")
    ? message
    : `${message} 刷新页面不会丢失配置。`;
}

export function selectionConfig(value, profiles) {
  if (!value.startsWith("profile:")) return { provider: value };
  const profile = profiles.find((item) => item.id === value.slice(8));
  if (!profile) throw new Error("模型配置已失效，请重新选择。");
  return { provider: profile.provider, profile_id: profile.id };
}

export function selectionOptions(config, profiles, escape) {
  const defaults = config.providers
    .map(
      (provider) =>
        `<option value="${escape(provider.id)}" ${provider.ready ? "" : "disabled"}>${escape(provider.name)}${provider.ready ? "" : " · 未配置"}</option>`,
    )
    .join("");
  const saved = profiles
    .map(
      (profile) =>
        `<option value="profile:${escape(profile.id)}" ${profileReady(profile) ? "" : "disabled"}>${escape(profile.name)} · ${escape(profile.model)}${profileReady(profile) ? "" : " · 未配置"}</option>`,
    )
    .join("");
  return (
    defaults +
    (saved ? `<optgroup label="已保存的模型配置">${saved}</optgroup>` : "")
  );
}
