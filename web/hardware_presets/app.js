(() => {
  "use strict";

  const API_URL = "/api/models/hardware-presets";
  const APPLY_URL = "/api/models/hardware-presets/apply";
  const RESET_URL = "/api/models/hardware-presets/reset";
  const MODEL_CONFIG_URL = "/api/models/config";
  const MODEL_SELECT_URL = "/api/models/select";
  const GIB = 1024 ** 3;
  const MIB = 1024 ** 2;
  const layout = document.querySelector(".models-layout");
  if (!layout || layout.querySelector(".hardware-presets-card")) return;

  const card = document.createElement("section");
  card.className = "hardware-presets-card";
  card.innerHTML = `
    <div class="hardware-presets-heading">
      <div>
        <p class="eyebrow">Managed llama.cpp</p>
        <h3>Hardware presets</h3>
        <p class="hardware-presets-copy">
          Four editable starting points for low-end, 8 GB, 12 GB, and 16+ GB
          systems. These flags control Project Akira's managed llama.cpp server;
          LM Studio manages its own context, GPU offload, and CPU threads.
        </p>
      </div>
      <button class="button secondary hardware-refresh" type="button">Refresh</button>
    </div>
    <div class="hardware-status" role="status" aria-live="polite">Detecting hardware…</div>
    <div class="hardware-summary" hidden></div>
    <div class="hardware-preset-grid" hidden></div>
  `;

  const hero = layout.querySelector(".hero-card");
  if (hero?.nextSibling) layout.insertBefore(card, hero.nextSibling);
  else layout.appendChild(card);

  const refreshButton = card.querySelector(".hardware-refresh");
  const statusBox = card.querySelector(".hardware-status");
  const summary = card.querySelector(".hardware-summary");
  const presetGrid = card.querySelector(".hardware-preset-grid");
  let busy = false;
  let latestData = null;
  let backendBusy = false;
  let managedBackendSelected = false;
  let managedSelectionDirty = false;
  let latestModelConfig = null;
  let pendingBackend = null;
  let enforcingBackendUi = false;

  const backendOptions = document.querySelector(".backend-options");
  const baseUrlInput = document.getElementById("baseUrlInput");
  const apiKeyInput = document.getElementById("apiKeyInput");
  const reasoningSelect = document.getElementById("reasoningSelect");
  const reasoningHint = document.getElementById("reasoningHint");
  const baseUrlHint = document.getElementById("baseUrlHint");
  const contextField = document.getElementById("contextField");
  const testButton = document.getElementById("testButton");
  const modelSaveButton = document.getElementById("saveButton");
  const modelSaveState = document.getElementById("saveState");
  const activeModelLabel = document.getElementById("activeModelLabel");
  const lmStudioActions = document.getElementById("lmStudioActions");

  const REASONING_LABELS = {
    off: "Off — fastest conversation",
    low: "Low",
    medium: "Medium",
    high: "High",
    on: "On",
    auto: "Auto / compatibility mode",
  };

  function backendDisplayName(value) {
    if (value === "llama_cpp") return "Managed llama.cpp";
    if (value === "lm_studio") return "LM Studio";
    if (value === "openai_compatible") return "OpenAI-compatible server";
    return String(value || "Unknown");
  }

  function ensureManagedBackendOption() {
    if (!backendOptions) return null;
    let option = backendOptions.querySelector('[data-backend="llama_cpp"]');
    if (!option) {
      option = document.createElement("button");
      option.className = "backend-option managed-llama-option";
      option.type = "button";
      option.dataset.backend = "llama_cpp";
      option.setAttribute("role", "radio");
      option.setAttribute("aria-checked", "false");
      option.innerHTML = `
        <span class="backend-icon managed">GG</span>
        <span><strong>Managed llama.cpp</strong><small>Downloaded GGUF model with Project Akira-managed runtime settings.</small></span>
      `;
      backendOptions.appendChild(option);
    }
    backendOptions.classList.add("has-managed-llama");
    return option;
  }

  const managedBackendOption = ensureManagedBackendOption();

  function selectedBackend() {
    return pendingBackend || latestModelConfig?.backend || "lm_studio";
  }

  function rememberedUrl(backend) {
    const configured = latestModelConfig?.backend_urls?.[backend];
    if (configured) return configured;
    if (backend === "lm_studio") return "http://localhost:1234/v1";
    if (backend === "openai_compatible") return "http://localhost:11434/v1";
    return latestModelConfig?.base_url || "http://127.0.0.1:8080/v1";
  }

  function setElementHidden(element, hidden) {
    if (element && element.hidden !== hidden) element.hidden = hidden;
  }

  function setElementDisabled(element, disabled) {
    if (element && element.disabled !== disabled) element.disabled = disabled;
  }

  function replaceReasoningOptions(values, preferred) {
    if (!reasoningSelect) return;
    const currentValues = [...reasoningSelect.options].map((option) => option.value);
    const sameValues = currentValues.length === values.length
      && currentValues.every((value, index) => value === values[index]);
    if (!sameValues) {
      reasoningSelect.replaceChildren();
      for (const value of values) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = REASONING_LABELS[value] || value;
        reasoningSelect.appendChild(option);
      }
    }
    reasoningSelect.value = values.includes(preferred) ? preferred : values[0];
  }

  function setManagedConnectionFields(managed) {
    for (const control of [baseUrlInput, apiKeyInput]) {
      const field = control?.closest(".field");
      setElementHidden(field, managed);
    }
    const reasoningField = reasoningSelect?.closest(".field");
    setElementHidden(reasoningField, false);
    setElementHidden(testButton, managed);
  }

  function configureBackendUi(backend, { restoreUrl = false } = {}) {
    if (enforcingBackendUi) return;
    enforcingBackendUi = true;
    try {
      const managed = backend === "llama_cpp";
      const lmStudio = backend === "lm_studio";
      document.documentElement.dataset.modelBackend = backend;
      setManagedConnectionFields(managed);

      if (restoreUrl && !managed && baseUrlInput) {
        const nextUrl = rememberedUrl(backend);
        if (baseUrlInput.value !== nextUrl) {
          baseUrlInput.value = nextUrl;
          baseUrlInput.dispatchEvent(new Event("input", { bubbles: true }));
          baseUrlInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }

      if (managed) {
        const configured = ["off", "on"].includes(reasoningSelect?.value)
          ? reasoningSelect.value
          : ["off", "on"].includes(latestModelConfig?.reasoning_mode)
            ? latestModelConfig.reasoning_mode
            : "off";
        replaceReasoningOptions(["off", "on"], configured);
        setElementDisabled(reasoningSelect, false);
        if (reasoningHint) {
          reasoningHint.textContent = "Managed llama.cpp passes this as --reasoning. Off is the default.";
        }
        if (baseUrlHint) {
          baseUrlHint.textContent = "The managed server URL is generated from its host and port settings.";
        }
      } else if (lmStudio) {
        const existingValues = reasoningSelect
          ? [...reasoningSelect.options].map((option) => option.value)
          : [];
        if (existingValues.length <= 2 || existingValues.every((value) => ["off", "on", "auto"].includes(value))) {
          const preferred = ["off", "low", "medium", "high", "on", "auto"].includes(reasoningSelect?.value)
            ? reasoningSelect.value
            : ["off", "low", "medium", "high", "on", "auto"].includes(latestModelConfig?.reasoning_mode)
              ? latestModelConfig.reasoning_mode
              : "off";
          replaceReasoningOptions(["off", "low", "medium", "high", "on", "auto"], preferred);
        }
        setElementDisabled(reasoningSelect, false);
        if (reasoningHint) reasoningHint.textContent = "LM Studio native chat enforces this setting.";
        if (baseUrlHint) baseUrlHint.textContent = "LM Studio normally uses http://localhost:1234/v1.";
      } else {
        replaceReasoningOptions(["auto"], "auto");
        setElementDisabled(reasoningSelect, true);
        if (reasoningHint) {
          reasoningHint.textContent = "Generic compatibility mode does not enforce Project Akira reasoning settings.";
        }
        if (baseUrlHint) baseUrlHint.textContent = "Use the server's OpenAI-compatible /v1 base URL.";
      }

      setElementHidden(contextField, !lmStudio);
      // lmStudioActions is observed below. Avoid writing the same reflected
      // boolean attribute repeatedly or the observer can schedule itself forever.
      setElementHidden(lmStudioActions, !lmStudio);
    } finally {
      enforcingBackendUi = false;
    }
  }

  function renderBackendSelection(config, { backendOverride = null, dirty = false } = {}) {
    if (!backendOptions || !managedBackendOption) return;
    const backend = backendOverride || config?.backend || "lm_studio";
    const managed = backend === "llama_cpp";
    managedBackendSelected = managed;
    managedSelectionDirty = managed && dirty;
    for (const option of backendOptions.querySelectorAll(".backend-option")) {
      const selected = option.dataset.backend === backend;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-checked", String(selected));
    }
    configureBackendUi(backend, { restoreUrl: Boolean(backendOverride) });
    if (modelSaveButton && dirty) modelSaveButton.disabled = false;
    if (modelSaveState && dirty) {
      modelSaveState.textContent = "Unsaved";
      modelSaveState.classList.add("dirty");
      modelSaveState.classList.remove("saved");
    }
  }

  function scheduleBackendUiEnforcement() {
    for (const delay of [0, 50, 250, 700]) {
      window.setTimeout(() => configureBackendUi(selectedBackend()), delay);
    }
  }

  async function refreshBackendSelection() {
    try {
      const response = await fetch(MODEL_CONFIG_URL, { cache: "no-store" });
      const config = await readJson(response);
      latestModelConfig = config;
      if (pendingBackend && config.backend === pendingBackend) pendingBackend = null;
      renderBackendSelection(config, {
        backendOverride: pendingBackend,
        dirty: Boolean(pendingBackend),
      });
      if (config.backend === "llama_cpp" && activeModelLabel) {
        activeModelLabel.textContent = config.model || "Managed llama.cpp";
      }
      scheduleBackendUiEnforcement();
    } catch (_error) {
      // The base Models page will display connection errors. Keep presets usable.
    }
  }

  async function saveManagedBackendSelection() {
    if (backendBusy) return;
    backendBusy = true;
    if (modelSaveButton) modelSaveButton.disabled = true;
    if (modelSaveState) modelSaveState.textContent = "Saving";
    try {
      const configResponse = await fetch(MODEL_CONFIG_URL, { cache: "no-store" });
      const config = await readJson(configResponse);
      const reasoningMode = ["off", "on"].includes(reasoningSelect?.value)
        ? reasoningSelect.value
        : "off";
      const response = await fetch(MODEL_SELECT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backend: "llama_cpp",
          base_url: config.backend_urls?.llama_cpp || config.base_url || "http://127.0.0.1:8080/v1",
          api_key: "",
          model: config.model || "managed-llama.cpp",
          reasoning_mode: reasoningMode,
        }),
      });
      const result = await readJson(response);
      pendingBackend = null;
      latestModelConfig = { ...config, ...result, backend_urls: config.backend_urls };
      renderBackendSelection(latestModelConfig);
      if (activeModelLabel) activeModelLabel.textContent = result.model;
      if (modelSaveState) {
        modelSaveState.textContent = "Saved";
        modelSaveState.classList.remove("dirty");
        modelSaveState.classList.add("saved");
      }
      setStatus(
        `Managed llama.cpp selected with ${result.model}. Hardware presets now control the active backend.`,
        "success",
      );
      window.dispatchEvent(new CustomEvent("akira:model-config-changed", { detail: result }));
      await loadPresets({ quiet: true, preserveStatus: true });
      // Reinitialize the base Models controller with llama.cpp as its backend so
      // its catalog state and details panel no longer refer to LM Studio.
      window.setTimeout(() => window.location.reload(), 350);
    } catch (error) {
      setStatus(error.message || "Managed llama.cpp could not be selected.", "error");
      if (modelSaveButton) modelSaveButton.disabled = false;
      if (modelSaveState) modelSaveState.textContent = "Unsaved";
    } finally {
      backendBusy = false;
    }
  }

  backendOptions?.addEventListener("click", (event) => {
    const option = event.target.closest(".backend-option");
    if (!option) return;
    const backend = option.dataset.backend;
    pendingBackend = backend;
    if (backend === "llama_cpp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      renderBackendSelection(latestModelConfig || {}, {
        backendOverride: "llama_cpp",
        dirty: true,
      });
      setStatus(
        "Managed llama.cpp selected. Choose Off or On for reasoning, then save the backend.",
      );
      scheduleBackendUiEnforcement();
      return;
    }

    managedBackendSelected = false;
    managedSelectionDirty = false;
    // Let the base Models controller update its private backend state first,
    // then restore this backend's remembered URL and correct field states.
    window.setTimeout(() => {
      renderBackendSelection(latestModelConfig || {}, {
        backendOverride: backend,
        dirty: true,
      });
      scheduleBackendUiEnforcement();
    }, 0);
  }, true);

  modelSaveButton?.addEventListener("click", (event) => {
    if (!managedBackendSelected || !managedSelectionDirty) {
      // LM Studio and generic selections are saved by the base Models page.
      // Re-read the shared setting after that request completes so presets and
      // Settings remain in lockstep without a manual page refresh.
      for (const delay of [200, 600, 1200]) {
        window.setTimeout(() => {
          void refreshBackendSelection();
          void loadPresets({ quiet: true });
        }, delay);
      }
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    void saveManagedBackendSelection();
  }, true);

  testButton?.addEventListener("click", () => {
    for (const delay of [100, 400, 1000]) {
      window.setTimeout(() => {
        void refreshBackendSelection();
        configureBackendUi(selectedBackend());
      }, delay);
    }
  });

  if (reasoningSelect || lmStudioActions) {
    const observer = new MutationObserver(() => {
      window.queueMicrotask(() => configureBackendUi(selectedBackend()));
    });
    if (reasoningSelect) {
      observer.observe(reasoningSelect, {
        attributes: true,
        attributeFilter: ["disabled"],
        childList: true,
      });
    }
    if (lmStudioActions) {
      observer.observe(lmStudioActions, {
        attributes: true,
        attributeFilter: ["hidden"],
      });
    }
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "Unknown";
    if (bytes === 0) return "0 B";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let size = bytes;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    const digits = index >= 3 ? 1 : 0;
    return `${size.toFixed(digits)} ${units[index]}`;
  }

  async function readJson(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status})`);
    }
    return data;
  }

  function setStatus(message, kind = "") {
    statusBox.textContent = message;
    statusBox.className = `hardware-status ${kind}`.trim();
    statusBox.hidden = !message;
  }

  function addFact(parent, label, value) {
    const item = document.createElement("div");
    item.className = "hardware-fact";
    const caption = document.createElement("span");
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    item.append(caption, strong);
    parent.appendChild(item);
  }

  function renderSummary(data) {
    summary.replaceChildren();

    const facts = document.createElement("div");
    facts.className = "hardware-facts";
    addFact(facts, "Active backend", backendDisplayName(data.active_backend));
    addFact(facts, "Logical CPU threads", String(data.profile.logical_cpu_count));
    addFact(facts, "System memory", formatBytes(data.profile.total_memory_bytes));
    addFact(facts, "Detected GPUs", String(data.profile.gpus.length));
    addFact(facts, "Total VRAM", formatBytes(data.profile.total_vram_bytes));
    addFact(
      facts,
      "Selected GGUF",
      data.model_size_bytes ? formatBytes(data.model_size_bytes) : "None selected",
    );
    summary.appendChild(facts);

    if (data.profile.gpus.length) {
      const gpuList = document.createElement("div");
      gpuList.className = "hardware-gpu-list";
      data.profile.gpus.forEach((gpu, index) => {
        const row = document.createElement("div");
        const name = document.createElement("span");
        name.textContent = `GPU ${index + 1}: ${gpu.name}`;
        const memory = document.createElement("strong");
        memory.textContent = formatBytes(gpu.memory_bytes);
        row.append(name, memory);
        gpuList.appendChild(row);
      });
      summary.appendChild(gpuList);
    }

    const details = [];
    if (data.model_path) details.push(`Model: ${data.model_path}`);
    if (data.model_layer_count) details.push(`GGUF layers: ${data.model_layer_count}`);
    for (const note of data.profile.notes || []) details.push(note);
    if (details.length) {
      const noteList = document.createElement("ul");
      noteList.className = "hardware-notes";
      for (const text of details) {
        const item = document.createElement("li");
        item.textContent = text;
        noteList.appendChild(item);
      }
      summary.appendChild(noteList);
    }

    summary.hidden = false;
  }

  function badge(text, kind) {
    const element = document.createElement("span");
    element.className = `hardware-badge ${kind}`;
    element.textContent = text;
    return element;
  }

  function normalizeLayers(value) {
    const text = String(value ?? "").trim().toLowerCase();
    if (text === "auto" || text === "all") return text;
    if (!/^\d+$/.test(text)) throw new Error("GPU layers must be auto, all, or a number.");
    return String(Number(text));
  }

  function estimateMemory(data, contextSize, gpuLayers) {
    const modelBytes = Number(data.model_size_bytes);
    if (!Number.isFinite(modelBytes) || modelBytes <= 0) {
      return { vram: null, ram: null };
    }
    const normalized = normalizeLayers(gpuLayers);
    let fraction = 1;
    if (normalized !== "auto" && normalized !== "all") {
      const layers = Number(normalized);
      const totalLayers = Number(data.model_layer_count) || 80;
      fraction = Math.min(1, Math.max(0, layers / totalLayers));
    }
    let kvBytes;
    const perToken = Number(data.kv_bytes_per_token);
    if (Number.isFinite(perToken) && perToken > 0) {
      kvBytes = perToken * contextSize;
    } else {
      kvBytes = Math.max(256 * MIB, modelBytes * 0.25 * contextSize / 8192);
    }
    const vram = modelBytes * fraction + kvBytes * fraction + (fraction > 0 ? 512 * MIB : 0);
    const ram = modelBytes * (1 - fraction) + kvBytes * (1 - fraction) + GIB;
    return { vram, ram };
  }

  function createField(label, value, options = {}) {
    const wrapper = document.createElement("label");
    wrapper.className = "hardware-preset-field";
    const caption = document.createElement("span");
    caption.textContent = label;
    const input = document.createElement("input");
    input.value = String(value);
    input.type = options.type || "text";
    input.className = options.className || "";
    if (options.min !== undefined) input.min = String(options.min);
    if (options.max !== undefined) input.max = String(options.max);
    if (options.step !== undefined) input.step = String(options.step);
    input.autocomplete = "off";
    wrapper.append(caption, input);
    return { wrapper, input };
  }

  function readCardValues(item) {
    const contextSize = Number(item.querySelector(".preset-context").value);
    const gpuLayers = normalizeLayers(item.querySelector(".preset-layers").value);
    const threads = Number(item.querySelector(".preset-threads").value);
    if (!Number.isInteger(contextSize) || contextSize < 256 || contextSize > 2000000) {
      throw new Error("Context must be an integer from 256 to 2,000,000.");
    }
    if (!Number.isInteger(threads) || threads === 0 || threads < -1 || threads > 1024) {
      throw new Error("Threads must be -1 or an integer from 1 to 1,024.");
    }
    return { context_size: contextSize, gpu_layers: gpuLayers, threads };
  }

  function renderPresets(data) {
    presetGrid.replaceChildren();
    const managedActive = data.active_backend === "llama_cpp";

    for (const preset of data.presets) {
      const item = document.createElement("article");
      item.className = "hardware-preset";
      item.dataset.presetId = preset.id;
      if (preset.recommended) item.classList.add("recommended");
      if (preset.current) item.classList.add("current");
      if (preset.default) item.classList.add("default");

      const heading = document.createElement("div");
      heading.className = "hardware-preset-title";
      const title = document.createElement("h4");
      title.textContent = preset.name;
      const badges = document.createElement("div");
      badges.className = "hardware-badges";
      if (preset.recommended) badges.appendChild(badge("Recommended", "recommended"));
      if (preset.default) badges.appendChild(badge("Default", "default"));
      if (preset.current) badges.appendChild(badge("Applied", "current"));
      if (preset.customized) badges.appendChild(badge("Edited", "customized"));
      heading.append(title, badges);

      const description = document.createElement("p");
      description.className = "hardware-preset-description";
      description.textContent = preset.description;

      const target = document.createElement("p");
      target.className = "hardware-preset-target";
      target.textContent = `Target tier: ${formatBytes(preset.target_vram_bytes)} VRAM`;

      const fields = document.createElement("div");
      fields.className = "hardware-preset-fields";
      const context = createField("Context", preset.context_size, {
        type: "number", min: 256, max: 2000000, step: 256, className: "preset-context",
      });
      const layers = createField("GPU layers", preset.gpu_layers, {
        className: "preset-layers",
      });
      const threads = createField("CPU threads", preset.threads, {
        type: "number", min: -1, max: 1024, step: 1, className: "preset-threads",
      });
      fields.append(context.wrapper, layers.wrapper, threads.wrapper);

      const estimates = document.createElement("div");
      estimates.className = "hardware-memory-estimates";
      const vramEstimate = document.createElement("div");
      const ramEstimate = document.createElement("div");
      estimates.append(vramEstimate, ramEstimate);

      function updateEstimates() {
        try {
          const values = readCardValues(item);
          const estimate = estimateMemory(data, values.context_size, values.gpu_layers);
          vramEstimate.innerHTML = `<span>Expected VRAM</span><strong>${
            estimate.vram === null ? "Select a model" : formatBytes(estimate.vram)
          }</strong>`;
          ramEstimate.innerHTML = `<span>Expected RAM</span><strong>${
            estimate.ram === null ? "Select a model" : formatBytes(estimate.ram)
          }</strong>`;
          item.classList.remove("invalid");
        } catch (_error) {
          vramEstimate.innerHTML = "<span>Expected VRAM</span><strong>Invalid values</strong>";
          ramEstimate.innerHTML = "<span>Expected RAM</span><strong>Invalid values</strong>";
          item.classList.add("invalid");
        }
      }
      for (const input of fields.querySelectorAll("input")) {
        input.addEventListener("input", updateEstimates);
      }

      item.append(heading, description, target, fields, estimates);

      if (preset.warnings?.length) {
        const warnings = document.createElement("ul");
        warnings.className = "hardware-warnings";
        for (const warning of preset.warnings) {
          const row = document.createElement("li");
          row.textContent = warning;
          warnings.appendChild(row);
        }
        item.appendChild(warnings);
      }

      const actions = document.createElement("div");
      actions.className = "hardware-preset-actions";
      const applyButton = document.createElement("button");
      applyButton.className = "button secondary";
      applyButton.type = "button";
      applyButton.textContent = managedActive ? "Apply" : "Requires llama.cpp";
      applyButton.disabled = !managedActive;
      applyButton.title = managedActive
        ? "Apply these values to Project Akira's managed llama.cpp backend."
        : "LM Studio controls its own runtime settings. Switch to Managed llama.cpp to apply this preset.";
      applyButton.addEventListener("click", () => applyPreset(item, false));

      const defaultButton = document.createElement("button");
      defaultButton.className = "button primary";
      defaultButton.type = "button";
      defaultButton.textContent = preset.default ? "Save default" : "Set as default";
      defaultButton.disabled = false;
      defaultButton.title = managedActive
        ? "Apply these values and make this the default preset."
        : "Save these values for the next time Managed llama.cpp is selected.";
      defaultButton.addEventListener("click", () => applyPreset(item, true));

      const resetButton = document.createElement("button");
      resetButton.className = "hardware-reset-button";
      resetButton.type = "button";
      resetButton.textContent = "Restore built-in";
      resetButton.disabled = !preset.customized;
      resetButton.addEventListener("click", () => resetPreset(preset.id));
      actions.append(applyButton, defaultButton, resetButton);
      item.appendChild(actions);
      presetGrid.appendChild(item);
      updateEstimates();
    }

    presetGrid.hidden = false;
  }

  async function loadPresets({ quiet = false, preserveStatus = false } = {}) {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    if (!quiet) setStatus("Detecting hardware…");
    try {
      const response = await fetch(API_URL, { cache: "no-store" });
      const data = await readJson(response);
      latestData = data;
      renderSummary(data);
      renderPresets(data);
      if (!preserveStatus) {
        if (data.active_backend === "llama_cpp") {
          setStatus("");
        } else {
          setStatus(
            `Hardware presets are saved for Managed llama.cpp only. ${backendDisplayName(data.active_backend)} manages its own context, GPU offload, and threads.`,
          );
        }
      }
    } catch (error) {
      setStatus(error.message || "Hardware detection failed.", "error");
    } finally {
      busy = false;
      refreshButton.disabled = false;
    }
  }

  async function applyPreset(item, setDefault) {
    if (busy) return;
    let values;
    try {
      values = readCardValues(item);
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }
    busy = true;
    refreshButton.disabled = true;
    for (const button of presetGrid.querySelectorAll("button")) button.disabled = true;
    const managedActive = latestData?.active_backend === "llama_cpp";
    setStatus(
      setDefault && !managedActive
        ? "Saving preset for the next Managed llama.cpp session…"
        : setDefault
          ? "Saving default hardware preset…"
          : "Applying hardware preset…",
    );
    try {
      const response = await fetch(APPLY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset_id: item.dataset.presetId,
          ...values,
          set_default: setDefault,
        }),
      });
      const result = await readJson(response);
      const defaultText = result.saved_as_default ? " and saved as default" : "";
      const flags = [
        `--ctx-size ${result.configured_context_size}`,
        `--n-gpu-layers ${result.configured_gpu_layers}`,
        `--threads ${result.configured_threads}`,
        "--parallel 1",
      ].join(" · ");
      let runtimeText = "";
      let statusKind = "success";
      if (result.runtime_state === "inactive_backend") {
        runtimeText = ` Current backend is ${backendDisplayName(result.active_backend)}, so its running model was not changed. These flags are saved for Managed llama.cpp.`;
      } else if (result.runtime_state === "restarted") {
        const pidText = result.runtime_pid ? ` (PID ${result.runtime_pid})` : "";
        runtimeText = ` Running llama-server restarted now${pidText}. Active: `
          + `context ${result.active_context_size}, GPU layers ${result.active_gpu_layers}, `
          + `threads ${result.active_threads}.`;
      } else if (result.runtime_state === "pending") {
        runtimeText = " No managed llama-server was running, so these values will be used on its next launch.";
      } else if (result.runtime_state === "restart_failed") {
        runtimeText = ` Settings were saved, but llama-server could not restart: ${result.restart_error || "unknown error"}`;
        statusKind = "error";
      } else {
        runtimeText = " Those values were already configured.";
      }
      setStatus(
        `${result.preset.name} ${result.applied_to_active_backend ? "applied" : "saved"}${defaultText}. Configured llama.cpp launch flags: ${flags}.${runtimeText}`,
        statusKind,
      );
      busy = false;
      await loadPresets({ quiet: true, preserveStatus: true });
    } catch (error) {
      setStatus(error.message || "The preset could not be applied.", "error");
    } finally {
      busy = false;
      refreshButton.disabled = false;
    }
  }

  async function resetPreset(presetId) {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    for (const button of presetGrid.querySelectorAll("button")) button.disabled = true;
    setStatus("Restoring built-in preset values…");
    try {
      const response = await fetch(RESET_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset_id: presetId }),
      });
      const data = await readJson(response);
      latestData = data;
      renderSummary(data);
      renderPresets(data);
      setStatus("Built-in preset values restored.", "success");
    } catch (error) {
      setStatus(error.message || "The preset could not be restored.", "error");
    } finally {
      busy = false;
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", () => loadPresets());
  window.addEventListener("akira:model-config-changed", (event) => {
    const changedBackend = event.detail?.backend;
    if (changedBackend === "llama_cpp" && latestModelConfig?.backend !== "llama_cpp") {
      // Download selection changes the backend outside the base Models
      // controller. Reload so both controllers initialize from one source.
      window.setTimeout(() => window.location.reload(), 250);
      return;
    }
    void refreshBackendSelection();
    void loadPresets({ quiet: true });
  });
  void refreshBackendSelection();
  window.setTimeout(() => void refreshBackendSelection(), 250);
  void loadPresets();
})();
