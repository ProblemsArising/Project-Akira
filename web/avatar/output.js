const VALID_BACKENDS = new Set(["embedded", "vmc", "both", "disabled"]);

export function normalizeAvatarBackend(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return VALID_BACKENDS.has(normalized) ? normalized : "embedded";
}

export function resolveAvatarOutputs(settings = {}) {
  const enabled = settings.enabled !== false;
  const backend = normalizeAvatarBackend(settings.backend);
  return {
    backend,
    enabled,
    embedded: enabled && (backend === "embedded" || backend === "both"),
    vmc: enabled && (backend === "vmc" || backend === "both"),
  };
}

export function avatarOutputLabel(selection) {
  if (!selection.enabled || selection.backend === "disabled") return "Output disabled";
  if (selection.backend === "both") return "Embedded + VMC";
  if (selection.backend === "vmc") return "VMC output · embedded preview";
  return "Embedded VRM";
}
