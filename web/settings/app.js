(() => {
  "use strict";

  const SECTION_META = {
    general: { label: "General", description: "Desktop behavior and window preferences." },
    llm: { label: "AI model", description: "Connection and generation behavior for the language model." },
    personality: { label: "Personality", description: "Legacy overrides. Use the Personality editor for presets." },
    stt: { label: "Speech recognition", description: "Whisper model, device, language, and decoding options." },
    audio: { label: "Microphone", description: "Recording, VAD sensitivity, and speech timing." },
    tts: { label: "Voice", description: "System TTS voice, speed, and volume." },
    avatar: { label: "Avatar", description: "VMC connection, mouth sync, expressions, and body motion." },
    memory: { label: "Memory", description: "Long-term memory size and context limits." },
  };

  const FIELD_META = {
    "general.launch_on_startup": { label: "Launch on startup", description: "Start Project Akira when Windows starts." },
    "general.open_avatar_window": { label: "Open avatar window", description: "Open the avatar automatically with the desktop app." },
    "general.avatar_always_on_top": { label: "Avatar always on top" },
    "general.remember_window_positions": { label: "Remember window positions" },

    "llm.backend": { label: "Backend", advanced: true },
    "llm.base_url": { label: "Server URL", description: "LM Studio server address. Project Akira derives the native API endpoint automatically.", fullWidth: true },
    "llm.api_key": { label: "API key", inputType: "password", advanced: true },
    "llm.model": { label: "Model name", description: "Must match the model identifier exposed by the server.", fullWidth: true },
    "llm.temperature": { label: "Temperature", description: "Higher values make replies more varied.", min: 0, max: 2, step: 0.05 },
    "llm.top_p": { label: "Top P", min: 0, max: 1, step: 0.05 },
    "llm.max_tokens": { label: "Maximum output tokens", description: "Includes hidden reasoning tokens for compatible models.", min: 1, max: 32768, step: 1 },
    "llm.max_short_term_messages": { label: "Short-term messages", min: 1, max: 500, step: 1 },
    "llm.reasoning_mode": { label: "Reasoning mode", description: "Enforced through LM Studio native chat. Auto uses the OpenAI-compatible endpoint.", options: ["off", "auto", "low", "medium", "high", "on"] },
    "llm.stop_sequences": { label: "Stop sequences", description: "One sequence per line.", fullWidth: true, advanced: true },

    "personality.preset": { label: "Active preset ID", description: "Managed from the Personality editor.", advanced: true },
    "personality.prompt": { label: "Legacy prompt override", description: "Overrides the selected preset. Clear this field to use the Personality editor.", fullWidth: true, multiline: true, advanced: true },

    "stt.model": { label: "Whisper model", description: "Larger models are more accurate but use more memory." },
    "stt.device": { label: "Device", options: ["cuda", "cpu", "auto"] },
    "stt.compute_type": { label: "Compute type", options: ["float16", "int8_float16", "int8", "float32", "auto", "default"] },
    "stt.language": { label: "Language", description: "Example: en. Leave empty for automatic detection.", nullable: true },
    "stt.beam_size": { label: "Beam size", min: 1, max: 50, step: 1, advanced: true },

    "audio.input_device": { label: "Input device", description: "Leave empty for the Windows default. Friendly selectors arrive later.", nullable: true, numericNullable: true, advanced: true },
    "audio.output_device": { label: "Output device", description: "Leave empty for the Windows default.", nullable: true, numericNullable: true, advanced: true },
    "audio.end_silence_seconds": { label: "Stop after silence", description: "Seconds of silence before a sentence is submitted.", min: 0.05, max: 30, step: 0.05 },
    "audio.pre_roll_seconds": { label: "Speech pre-roll", description: "Keeps audio immediately before speech detection.", min: 0, max: 10, step: 0.05 },
    "audio.start_threshold_multiplier": { label: "Start sensitivity", description: "Lower starts recording more easily.", min: 0.1, max: 100, step: 0.1 },
    "audio.end_threshold_multiplier": { label: "End sensitivity", description: "Lower considers quiet audio to be silence sooner.", min: 0.1, max: 100, step: 0.1 },
    "audio.calibration_seconds": { label: "Noise calibration time", min: 0, max: 30, step: 0.1 },
    "audio.max_record_seconds": { label: "Maximum recording length", min: 1, max: 600, step: 1 },

    "tts.voice_index": { label: "TTS voice", description: "On many Windows systems 0 is male and 1 is female.", options: [{ value: 0, label: "Voice 0 (usually male)" }, { value: 1, label: "Voice 1 (usually female)" }] },
    "tts.rate": { label: "Speech speed", min: 50, max: 500, step: 5 },
    "tts.volume": { label: "Volume", min: 0, max: 1, step: 0.05 },

    "avatar.enabled": { label: "Enable avatar output" },
    "avatar.backend": { label: "Avatar backend", options: ["vmc", "disabled"] },
    "avatar.vmc_ip": { label: "VMC address" },
    "avatar.vmc_port": { label: "VMC port", min: 1, max: 65535, step: 1 },
    "avatar.mouth_start_delay_seconds": { label: "Mouth start delay", min: 0, max: 10, step: 0.01 },
    "avatar.mouth_end_delay_seconds": { label: "Mouth end delay", min: 0, max: 10, step: 0.01 },
    "avatar.mouth_scale": { label: "Mouth movement", min: 0, max: 3, step: 0.05 },
    "avatar.body_idle_strength": { label: "Idle movement", min: 0, max: 3, step: 0.05 },
    "avatar.speaking_expression_strength": { label: "Speaking expression strength", min: 0, max: 2, step: 0.05 },
    "avatar.body_pose_strength": { label: "Body pose strength", min: 0, max: 3, step: 0.05 },

    "memory.max_turns": { label: "Stored turns", min: 1, max: 100000, step: 1 },
    "memory.max_facts": { label: "Stored facts", min: 1, max: 100000, step: 1 },
    "memory.max_context_chars": { label: "Memory context characters", min: 100, max: 1000000, step: 100 },
    "memory.recent_turns": { label: "Recent turns in context", min: 0, max: 1000, step: 1 },
    "memory.relevant_limit": { label: "Relevant memory limit", min: 0, max: 1000, step: 1 },
    "memory.file": { label: "Memory file", advanced: true, fullWidth: true },
  };

  const elements = {
    form: document.getElementById("settingsForm"),
    sections: document.getElementById("settingsSections"),
    navigation: document.getElementById("sectionNavigation"),
    search: document.getElementById("settingsSearch"),
    saveButton: document.getElementById("saveButton"),
    resetButton: document.getElementById("resetButton"),
    saveState: document.getElementById("saveState"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("noticeTitle"),
    noticeText: document.getElementById("noticeText"),
    dismissNotice: document.getElementById("dismissNotice"),
    sectionTemplate: document.getElementById("sectionTemplate"),
    fieldTemplate: document.getElementById("fieldTemplate"),
  };

  let originalSettings = null;
  const controls = new Map();

  function humanize(value) {
    return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function showNotice(title, text, type = "success") {
    elements.noticeTitle.textContent = title;
    elements.noticeText.textContent = text;
    elements.notice.className = `notice ${type}`;
    elements.notice.hidden = false;
  }

  function hideNotice() {
    elements.notice.hidden = true;
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
    }
    return payload;
  }

  function metadataFor(section, field, value) {
    const path = `${section}.${field}`;
    const configured = FIELD_META[path] || {};
    return {
      label: configured.label || humanize(field),
      description: configured.description || "",
      advanced: configured.advanced ?? !FIELD_META[path],
      fullWidth: configured.fullWidth || Array.isArray(value) || configured.multiline,
      ...configured,
    };
  }

  function optionEntries(options) {
    return options.map((option) => typeof option === "object" ? option : { value: option, label: humanize(option) });
  }

  function createControl(section, field, value, meta) {
    let control;
    if (typeof value === "boolean") {
      control = document.createElement("input");
      control.type = "checkbox";
      control.checked = value;
    } else if (meta.options) {
      control = document.createElement("select");
      for (const option of optionEntries(meta.options)) {
        const node = document.createElement("option");
        node.value = String(option.value);
        node.textContent = option.label;
        if (String(value) === String(option.value)) node.selected = true;
        control.appendChild(node);
      }
    } else if (Array.isArray(value) || meta.multiline) {
      control = document.createElement("textarea");
      control.value = Array.isArray(value) ? value.join("\n") : String(value ?? "");
    } else {
      control = document.createElement("input");
      control.type = meta.inputType || (typeof value === "number" ? "number" : "text");
      control.value = value ?? "";
      if (meta.min !== undefined) control.min = meta.min;
      if (meta.max !== undefined) control.max = meta.max;
      if (meta.step !== undefined) control.step = meta.step;
    }

    control.dataset.section = section;
    control.dataset.field = field;
    control.dataset.originalType = Array.isArray(value) ? "list" : value === null ? "null" : typeof value;
    control.dataset.originalValue = JSON.stringify(value);
    control.addEventListener("input", updateDirtyState);
    control.addEventListener("change", updateDirtyState);
    return control;
  }

  function renderField(section, field, value, target) {
    const meta = metadataFor(section, field, value);
    const fragment = elements.fieldTemplate.content.cloneNode(true);
    const wrapper = fragment.querySelector(".setting-field");
    const label = fragment.querySelector(".field-label");
    const description = fragment.querySelector(".field-description");
    const hint = fragment.querySelector(".field-value-hint");
    const controlHost = fragment.querySelector(".field-control");

    wrapper.dataset.search = `${meta.label} ${meta.description} ${section} ${field}`.toLowerCase();
    if (meta.fullWidth) wrapper.classList.add("full-width");
    if (typeof value === "boolean") wrapper.classList.add("is-toggle");
    label.textContent = meta.label;
    description.textContent = meta.description;
    hint.textContent = meta.advanced ? "Advanced" : "";

    const control = createControl(section, field, value, meta);
    controlHost.appendChild(control);
    controls.set(`${section}.${field}`, { control, wrapper, meta, original: value });
    target.appendChild(fragment);
  }

  function renderSettings(settings) {
    controls.clear();
    elements.sections.replaceChildren();
    elements.navigation.replaceChildren();

    for (const [section, values] of Object.entries(settings)) {
      if (section === "schema_version" || !values || typeof values !== "object" || Array.isArray(values)) continue;
      const meta = SECTION_META[section] || { label: humanize(section), description: "" };
      const fragment = elements.sectionTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".settings-card");
      const common = fragment.querySelector(".common-fields");
      const advanced = fragment.querySelector(".advanced-fields");
      const advancedDetails = fragment.querySelector(".advanced-settings");
      const advancedCount = fragment.querySelector(".advanced-count");
      card.id = `settings-${section}`;
      card.dataset.section = section;
      card.querySelector("h3").textContent = meta.label;
      card.querySelector(".section-heading p").textContent = meta.description;

      let advancedTotal = 0;
      for (const [field, value] of Object.entries(values)) {
        const fieldMeta = metadataFor(section, field, value);
        renderField(section, field, value, fieldMeta.advanced ? advanced : common);
        if (fieldMeta.advanced) advancedTotal += 1;
      }
      if (!advancedTotal) advancedDetails.hidden = true;
      advancedCount.textContent = advancedTotal ? `(${advancedTotal})` : "";
      elements.sections.appendChild(fragment);

      const link = document.createElement("a");
      link.href = `#settings-${section}`;
      link.textContent = meta.label;
      elements.navigation.appendChild(link);
    }
    updateDirtyState();
  }

  function parseControl(entry) {
    const { control, original, meta } = entry;
    if (typeof original === "boolean") return control.checked;
    if (Array.isArray(original)) return control.value.split("\n").map((item) => item.trim()).filter(Boolean);
    if (original === null) {
      const text = control.value.trim();
      if (!text) return null;
      if (/^-?\d+$/.test(text) && meta.numericNullable) return Number.parseInt(text, 10);
      return text;
    }
    if (typeof original === "number") {
      const number = Number(control.value);
      if (!Number.isFinite(number)) throw new Error(`${meta.label} must be a number.`);
      return Number.isInteger(original) ? Math.trunc(number) : number;
    }
    if (meta.options && typeof original === "number") return Number(control.value);
    return control.value;
  }

  function valuesEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function collectChanges() {
    const changes = {};
    for (const [path, entry] of controls.entries()) {
      const [section, field] = path.split(".");
      const current = parseControl(entry);
      if (!valuesEqual(current, entry.original)) {
        (changes[section] ||= {})[field] = current;
      }
    }
    return changes;
  }

  function updateDirtyState() {
    let dirty = false;
    try { dirty = Object.keys(collectChanges()).length > 0; } catch { dirty = true; }
    elements.saveButton.disabled = !dirty;
    elements.saveState.textContent = dirty ? "Unsaved" : "Saved";
    elements.saveState.className = `save-state ${dirty ? "dirty" : "saved"}`;
  }

  async function loadSettings() {
    try {
      const payload = await apiRequest("/api/settings");
      originalSettings = payload.settings;
      renderSettings(originalSettings);
    } catch (error) {
      showNotice("Could not load settings", error.message, "error");
      elements.saveState.textContent = "Error";
    }
  }

  async function saveSettings() {
    hideNotice();
    let changes;
    try { changes = collectChanges(); } catch (error) { showNotice("Check your values", error.message, "error"); return; }
    if (!Object.keys(changes).length) return;

    elements.saveButton.disabled = true;
    elements.saveState.textContent = "Saving";
    try {
      const result = await apiRequest("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({ changes }),
      });
      originalSettings = result.settings;
      renderSettings(originalSettings);
      const sections = result.changed_sections.map((section) => SECTION_META[section]?.label || humanize(section)).join(", ");
      showNotice(
        "Settings saved",
        result.restart_required
          ? `${sections} updated. Restart Project Akira before testing these changes.`
          : `${sections} updated. They will be used when Akira starts.`,
        result.restart_required ? "" : "success",
      );
    } catch (error) {
      showNotice("Could not save settings", error.message, "error");
      updateDirtyState();
    }
  }

  async function resetSettings() {
    if (!window.confirm("Reset every Project Akira setting to its default value?")) return;
    hideNotice();
    elements.resetButton.disabled = true;
    try {
      const result = await apiRequest("/api/settings/reset", { method: "POST" });
      originalSettings = result.settings;
      renderSettings(originalSettings);
      showNotice("Defaults restored", result.restart_required ? "Restart Project Akira to apply the restored defaults." : "Defaults will be used when Akira starts.", "success");
    } catch (error) {
      showNotice("Could not reset settings", error.message, "error");
    } finally {
      elements.resetButton.disabled = false;
    }
  }

  function filterSettings() {
    const query = elements.search.value.trim().toLowerCase();
    for (const card of elements.sections.querySelectorAll(".settings-card")) {
      let visible = 0;
      for (const field of card.querySelectorAll(".setting-field")) {
        const match = !query || field.dataset.search.includes(query);
        field.hidden = !match;
        if (match) visible += 1;
      }
      card.hidden = visible === 0;
      if (query && visible) card.querySelector(".advanced-settings").open = true;
    }
  }

  elements.saveButton.addEventListener("click", saveSettings);
  elements.resetButton.addEventListener("click", resetSettings);
  elements.dismissNotice.addEventListener("click", hideNotice);
  elements.search.addEventListener("input", filterSettings);
  window.addEventListener("beforeunload", (event) => {
    if (!elements.saveButton.disabled) { event.preventDefault(); event.returnValue = ""; }
  });

  loadSettings();
})();
