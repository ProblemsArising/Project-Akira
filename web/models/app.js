(() => {
  "use strict";

  const elements = {
    connectionBadge: document.getElementById("connectionBadge"),
    connectionText: document.getElementById("connectionText"),
    activeModelLabel: document.getElementById("activeModelLabel"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("noticeTitle"),
    noticeText: document.getElementById("noticeText"),
    dismissNotice: document.getElementById("dismissNotice"),
    saveState: document.getElementById("saveState"),
    backendOptions: [...document.querySelectorAll(".backend-option")],
    baseUrlInput: document.getElementById("baseUrlInput"),
    apiKeyInput: document.getElementById("apiKeyInput"),
    reasoningSelect: document.getElementById("reasoningSelect"),
    reasoningHint: document.getElementById("reasoningHint"),
    baseUrlHint: document.getElementById("baseUrlHint"),
    testButton: document.getElementById("testButton"),
    saveButton: document.getElementById("saveButton"),
    searchInput: document.getElementById("searchInput"),
    catalogSummary: document.getElementById("catalogSummary"),
    catalogStatus: document.getElementById("catalogStatus"),
    modelList: document.getElementById("modelList"),
    modelTemplate: document.getElementById("modelTemplate"),
    modelInput: document.getElementById("modelInput"),
    contextInput: document.getElementById("contextInput"),
    contextField: document.getElementById("contextField"),
    modelDetails: document.getElementById("modelDetails"),
    detailsName: document.getElementById("detailsName"),
    detailsId: document.getElementById("detailsId"),
    detailsDescription: document.getElementById("detailsDescription"),
    detailsFacts: document.getElementById("detailsFacts"),
    loadedBadge: document.getElementById("loadedBadge"),
    activeBadge: document.getElementById("activeBadge"),
    lmStudioActions: document.getElementById("lmStudioActions"),
    loadButton: document.getElementById("loadButton"),
    unloadButton: document.getElementById("unloadButton"),
  };

  const state = {
    backend: "lm_studio",
    models: [],
    selectedModel: null,
    original: null,
    connected: false,
    busy: false,
  };

  function setConnection(mode, label) {
    elements.connectionBadge.classList.toggle("online", mode === "online");
    elements.connectionBadge.classList.toggle("testing", mode === "testing");
    elements.connectionBadge.classList.toggle("offline", mode === "offline");
    elements.connectionText.textContent = label;
  }

  function showNotice(title, text, kind = "info") {
    elements.noticeTitle.textContent = title;
    elements.noticeText.textContent = text;
    elements.notice.classList.toggle("error", kind === "error");
    elements.notice.classList.toggle("success", kind === "success");
    elements.notice.hidden = false;
  }

  function hideNotice() {
    elements.notice.hidden = true;
  }

  function normalizeError(payload, fallback) {
    if (payload && typeof payload.detail === "string") return payload.detail;
    if (payload && Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg || String(item)).join(", ");
    }
    return fallback;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(normalizeError(payload, `Request failed (${response.status})`));
    }
    return payload;
  }

  function currentForm() {
    return {
      backend: state.backend,
      base_url: elements.baseUrlInput.value.trim(),
      api_key: elements.apiKeyInput.value,
      model: elements.modelInput.value.trim(),
      reasoning_mode: elements.reasoningSelect.value,
    };
  }

  function isDirty() {
    if (!state.original) return false;
    const current = currentForm();
    return ["backend", "base_url", "api_key", "model", "reasoning_mode"]
      .some((key) => String(current[key] ?? "") !== String(state.original[key] ?? ""));
  }

  function updateDirtyState() {
    const dirty = isDirty();
    elements.saveButton.disabled = state.busy || !dirty || !elements.modelInput.value.trim();
    elements.saveState.textContent = dirty ? "Unsaved" : "Saved";
    elements.saveState.classList.toggle("dirty", dirty);
    elements.saveState.classList.toggle("saved", !dirty);
    renderModelList();
    renderDetails();
  }

  function setBusy(busy, label = null) {
    state.busy = busy;
    elements.testButton.disabled = busy;
    elements.loadButton.disabled = busy;
    elements.unloadButton.disabled = busy;
    if (label) elements.testButton.textContent = label;
    else elements.testButton.textContent = "Test & refresh";
    updateDirtyState();
  }

  function setBackend(backend, { preserveUrl = false } = {}) {
    state.backend = backend;
    for (const option of elements.backendOptions) {
      const selected = option.dataset.backend === backend;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-checked", String(selected));
    }

    const lmStudio = backend === "lm_studio";
    elements.reasoningSelect.disabled = !lmStudio;
    elements.contextField.hidden = !lmStudio;
    elements.lmStudioActions.hidden = !lmStudio;
    elements.reasoningHint.textContent = lmStudio
      ? "LM Studio native chat enforces this setting."
      : "Generic compatibility mode does not enforce Project Akira reasoning settings.";
    elements.baseUrlHint.textContent = lmStudio
      ? "LM Studio normally uses http://localhost:1234/v1."
      : "Use the server's OpenAI-compatible /v1 base URL.";

    if (!lmStudio) {
      elements.reasoningSelect.value = "auto";
    } else if (elements.reasoningSelect.value === "auto" && state.original?.backend !== "openai_compatible") {
      elements.reasoningSelect.value = state.original?.reasoning_mode || "off";
    }

    if (!preserveUrl) {
      const current = elements.baseUrlInput.value.trim();
      if (!current || current === "http://localhost:1234/v1") {
        elements.baseUrlInput.value = lmStudio
          ? "http://localhost:1234/v1"
          : "http://localhost:11434/v1";
      }
    }

    state.models = [];
    state.selectedModel = null;
    state.connected = false;
    setConnection("offline", "Not tested");
    renderCatalog();
    updateDirtyState();
  }

  function formatBytes(value) {
    if (!Number.isFinite(value) || value <= 0) return null;
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = value;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
  }

  function formatContext(value) {
    if (!Number.isFinite(value) || value <= 0) return null;
    return value >= 1000 ? `${Math.round(value / 1000)}K context` : `${value} context`;
  }

  function modelMeta(model) {
    return [model.params, model.quantization, formatBytes(model.size_bytes), formatContext(model.max_context_length)]
      .filter(Boolean)
      .join(" · ") || "Model details unavailable";
  }

  function filteredModels() {
    const query = elements.searchInput.value.trim().toLowerCase();
    if (!query) return state.models;
    return state.models.filter((model) => [model.display_name, model.id, model.publisher, model.architecture]
      .some((value) => String(value || "").toLowerCase().includes(query)));
  }

  function renderCatalog() {
    elements.modelList.innerHTML = "";
    const models = filteredModels();
    elements.catalogStatus.hidden = state.models.length > 0;

    if (state.models.length === 0) {
      elements.catalogSummary.textContent = state.connected
        ? "The server connected but returned no chat models."
        : "Test the connection to list models.";
      elements.modelDetails.hidden = true;
      return;
    }

    elements.catalogSummary.textContent = `${state.models.length} chat model${state.models.length === 1 ? "" : "s"} found · ${state.models.filter((model) => model.loaded).length} loaded`;

    if (models.length === 0) {
      elements.catalogStatus.hidden = false;
      elements.catalogStatus.querySelector("strong").textContent = "No matching models";
      elements.catalogStatus.querySelector("p").textContent = "Try a different search term.";
      return;
    }

    for (const model of models) {
      const fragment = elements.modelTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".model-card");
      card.dataset.modelId = model.id;
      card.classList.toggle("selected", elements.modelInput.value.trim() === model.id);
      card.setAttribute("aria-selected", String(elements.modelInput.value.trim() === model.id));
      fragment.querySelector(".model-name").textContent = model.display_name;
      fragment.querySelector(".model-id").textContent = model.id;
      fragment.querySelector(".model-meta").textContent = modelMeta(model);
      const badges = fragment.querySelector(".model-badges");
      if (model.loaded) {
        const badge = document.createElement("span");
        badge.className = "mini-badge loaded";
        badge.textContent = "Loaded";
        badges.appendChild(badge);
      }
      if (state.original?.model === model.id) {
        const badge = document.createElement("span");
        badge.className = "mini-badge selected";
        badge.textContent = "Active";
        badges.appendChild(badge);
      }
      card.addEventListener("click", () => selectModel(model));
      elements.modelList.appendChild(fragment);
    }
  }

  function renderModelList() {
    if (state.models.length) renderCatalog();
  }

  function reasoningOptionsFor(model) {
    if (state.backend !== "lm_studio") return ["auto"];
    return model?.reasoning_options?.length
      ? model.reasoning_options
      : ["off", "low", "medium", "high", "on", "auto"];
  }

  function refreshReasoningOptions(model) {
    const previous = elements.reasoningSelect.value;
    const labels = {
      off: "Off — fastest conversation",
      low: "Low",
      medium: "Medium",
      high: "High",
      on: "On",
      auto: "Auto / compatibility mode",
    };
    const options = reasoningOptionsFor(model);
    elements.reasoningSelect.innerHTML = "";
    for (const value of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labels[value] || value;
      elements.reasoningSelect.appendChild(option);
    }
    const preferred = options.includes(previous)
      ? previous
      : options.includes(state.original?.reasoning_mode)
        ? state.original.reasoning_mode
        : model?.default_reasoning && options.includes(model.default_reasoning)
          ? model.default_reasoning
          : options[0];
    elements.reasoningSelect.value = preferred;
    elements.reasoningSelect.disabled = state.backend !== "lm_studio";
  }

  function selectModel(model) {
    state.selectedModel = model;
    elements.modelInput.value = model.id;
    refreshReasoningOptions(model);
    renderModelList();
    renderDetails();
    updateDirtyState();
  }

  function renderDetails() {
    const id = elements.modelInput.value.trim();
    const model = state.models.find((item) => item.id === id) || state.selectedModel;
    if (!model || model.id !== id) {
      elements.modelDetails.hidden = true;
      return;
    }

    state.selectedModel = model;
    elements.modelDetails.hidden = false;
    elements.detailsName.textContent = model.display_name;
    elements.detailsId.textContent = model.id;
    elements.detailsDescription.textContent = model.description || "No model description provided by the server.";
    elements.loadedBadge.hidden = !model.loaded;
    const isActive = state.original?.model === model.id;
    elements.activeBadge.hidden = !isActive;
    elements.activeBadge.classList.toggle("visible", isActive);
    elements.detailsFacts.innerHTML = "";

    const facts = [
      model.publisher,
      model.architecture,
      model.params,
      model.quantization,
      formatBytes(model.size_bytes),
      formatContext(model.max_context_length),
      model.vision ? "Vision" : null,
      model.tool_use ? "Tool use" : null,
      model.reasoning_options?.length ? `Reasoning: ${model.reasoning_options.join(", ")}` : null,
    ].filter(Boolean);
    for (const text of facts) {
      const fact = document.createElement("span");
      fact.className = "fact";
      fact.textContent = text;
      elements.detailsFacts.appendChild(fact);
    }

    elements.lmStudioActions.hidden = state.backend !== "lm_studio";
    elements.loadButton.hidden = model.loaded;
    elements.unloadButton.hidden = !model.loaded;
  }

  async function discover({ quiet = false } = {}) {
    hideNotice();
    setBusy(true, "Connecting…");
    setConnection("testing", "Testing");
    try {
      const form = currentForm();
      const result = await api("/api/models/discover", {
        method: "POST",
        body: JSON.stringify({
          backend: form.backend,
          base_url: form.base_url,
          api_key: form.api_key,
        }),
      });
      state.models = result.models || [];
      state.connected = true;
      setConnection("online", "Connected");

      const configured = state.models.find((model) => model.id === elements.modelInput.value.trim());
      if (configured) {
        state.selectedModel = configured;
        refreshReasoningOptions(configured);
      } else if (!elements.modelInput.value.trim() && state.models.length) {
        selectModel(state.models[0]);
      }
      renderCatalog();
      renderDetails();
      if (!quiet) showNotice("Connection successful", `Found ${state.models.length} available chat model${state.models.length === 1 ? "" : "s"}.`, "success");
    } catch (error) {
      state.models = [];
      state.selectedModel = null;
      state.connected = false;
      setConnection("offline", "Offline");
      renderCatalog();
      showNotice("Could not reach the model server", error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveSelection() {
    const form = currentForm();
    if (!form.model) {
      showNotice("Choose a model", "Select a model from the catalog or enter its exact model ID.", "error");
      return;
    }
    hideNotice();
    setBusy(true);
    try {
      const result = await api("/api/models/select", {
        method: "POST",
        body: JSON.stringify(form),
      });
      state.original = {
        backend: result.backend,
        base_url: result.base_url,
        api_key: form.api_key,
        model: result.model,
        reasoning_mode: result.reasoning_mode,
      };
      elements.activeModelLabel.textContent = result.model;
      showNotice(
        "Model selection saved",
        result.context_reset
          ? "The new backend will be used on the next message. Recent short-term model context was reset; saved history and long-term memory remain."
          : "The new backend will be used on the next message.",
        "success",
      );
    } catch (error) {
      showNotice("Could not save model selection", error.message, "error");
    } finally {
      setBusy(false);
      updateDirtyState();
    }
  }

  async function loadSelected() {
    const model = state.selectedModel;
    if (!model) return;
    setBusy(true);
    showNotice("Loading model", "LM Studio may take several seconds while it allocates VRAM and RAM.");
    try {
      const context = elements.contextInput.value.trim();
      await api("/api/models/load", {
        method: "POST",
        body: JSON.stringify({
          backend: state.backend,
          base_url: elements.baseUrlInput.value.trim(),
          api_key: elements.apiKeyInput.value,
          model: model.id,
          context_length: context ? Number(context) : null,
        }),
      });
      showNotice("Model loaded", `${model.display_name} is now loaded in LM Studio.`, "success");
      await discover({ quiet: true });
    } catch (error) {
      showNotice("Could not load model", error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function unloadSelected() {
    const model = state.selectedModel;
    const instanceId = model?.instance_ids?.[0];
    if (!model || !instanceId) return;
    setBusy(true);
    try {
      await api("/api/models/unload", {
        method: "POST",
        body: JSON.stringify({
          backend: state.backend,
          base_url: elements.baseUrlInput.value.trim(),
          api_key: elements.apiKeyInput.value,
          instance_id: instanceId,
        }),
      });
      showNotice("Model unloaded", `${model.display_name} was removed from memory.`, "success");
      await discover({ quiet: true });
    } catch (error) {
      showNotice("Could not unload model", error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function loadConfiguration() {
    setBusy(true);
    try {
      const config = await api("/api/models/config");
      state.original = {
        backend: config.backend,
        base_url: config.base_url,
        api_key: config.api_key || "",
        model: config.model,
        reasoning_mode: config.reasoning_mode,
      };
      state.backend = config.backend;
      elements.baseUrlInput.value = config.base_url;
      elements.apiKeyInput.value = config.api_key || "";
      elements.modelInput.value = config.model;
      elements.reasoningSelect.value = config.reasoning_mode;
      elements.activeModelLabel.textContent = config.model;
      setBackend(config.backend, { preserveUrl: true });
      elements.reasoningSelect.value = config.backend === "openai_compatible" ? "auto" : config.reasoning_mode;
      state.original.reasoning_mode = elements.reasoningSelect.value;
      updateDirtyState();
      await discover({ quiet: true });
    } catch (error) {
      showNotice("Could not load model settings", error.message, "error");
      setConnection("offline", "Unavailable");
    } finally {
      setBusy(false);
      updateDirtyState();
    }
  }

  for (const option of elements.backendOptions) {
    option.addEventListener("click", () => setBackend(option.dataset.backend));
  }
  for (const input of [elements.baseUrlInput, elements.apiKeyInput, elements.modelInput, elements.reasoningSelect]) {
    input.addEventListener("input", () => {
      if (input === elements.modelInput) {
        state.selectedModel = state.models.find((model) => model.id === input.value.trim()) || null;
        if (state.selectedModel) refreshReasoningOptions(state.selectedModel);
      }
      updateDirtyState();
    });
    input.addEventListener("change", updateDirtyState);
  }
  elements.searchInput.addEventListener("input", renderCatalog);
  elements.testButton.addEventListener("click", () => discover());
  elements.saveButton.addEventListener("click", saveSelection);
  elements.loadButton.addEventListener("click", loadSelected);
  elements.unloadButton.addEventListener("click", unloadSelected);
  elements.dismissNotice.addEventListener("click", hideNotice);

  window.addEventListener("beforeunload", (event) => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  });

  loadConfiguration();
})();
