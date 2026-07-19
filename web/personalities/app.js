(() => {
  "use strict";

  const elements = {
    list: document.getElementById("presetList"),
    listStatus: document.getElementById("listStatus"),
    search: document.getElementById("searchInput"),
    newButton: document.getElementById("newButton"),
    emptyState: document.getElementById("emptyState"),
    form: document.getElementById("editorForm"),
    editorTitle: document.getElementById("editorTitle"),
    editorSubtitle: document.getElementById("editorSubtitle"),
    activeBadge: document.getElementById("activeBadge"),
    builtInBadge: document.getElementById("builtInBadge"),
    draftBadge: document.getElementById("draftBadge"),
    nameInput: document.getElementById("nameInput"),
    descriptionInput: document.getElementById("descriptionInput"),
    promptInput: document.getElementById("promptInput"),
    nameError: document.getElementById("nameError"),
    promptError: document.getElementById("promptError"),
    descriptionCount: document.getElementById("descriptionCount"),
    promptCount: document.getElementById("promptCount"),
    saveButton: document.getElementById("saveButton"),
    discardButton: document.getElementById("discardButton"),
    activateButton: document.getElementById("activateButton"),
    duplicateButton: document.getElementById("duplicateButton"),
    deleteButton: document.getElementById("deleteButton"),
    updatedText: document.getElementById("updatedText"),
    saveState: document.getElementById("saveState"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("noticeTitle"),
    noticeText: document.getElementById("noticeText"),
    dismissNotice: document.getElementById("dismissNotice"),
    errorBanner: document.getElementById("errorBanner"),
    errorText: document.getElementById("errorText"),
    dismissErrorButton: document.getElementById("dismissErrorButton"),
    deleteDialog: document.getElementById("deleteDialog"),
    deleteForm: document.getElementById("deleteForm"),
    closeDeleteButton: document.getElementById("closeDeleteButton"),
    cancelDeleteButton: document.getElementById("cancelDeleteButton"),
    presetTemplate: document.getElementById("presetTemplate"),
  };

  const state = {
    presets: [],
    activeId: "gamer",
    selectedId: null,
    draft: false,
    original: null,
    dirty: false,
    saving: false,
    legacyOverride: false,
    websocket: null,
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(
        Array.isArray(detail)
          ? detail.map((item) => item.msg || String(item)).join(", ")
          : detail || `Request failed (${response.status})`,
      );
    }
    return payload;
  }

  function showError(message) {
    elements.errorText.textContent = String(message || "Something went wrong.");
    elements.errorBanner.hidden = false;
  }

  function clearError() {
    elements.errorBanner.hidden = true;
    elements.errorText.textContent = "";
  }

  function showNotice(title, text) {
    elements.noticeTitle.textContent = title;
    elements.noticeText.textContent = text;
    elements.notice.hidden = false;
  }

  function clearNotice() {
    elements.notice.hidden = true;
    elements.noticeTitle.textContent = "";
    elements.noticeText.textContent = "";
  }

  function setSaveState(label, type = "") {
    elements.saveState.textContent = label;
    elements.saveState.className = `save-state ${type}`.trim();
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function selectedPreset() {
    return state.presets.find((item) => item.id === state.selectedId) || null;
  }

  function currentValues() {
    return {
      name: elements.nameInput.value.trim(),
      description: elements.descriptionInput.value.trim(),
      prompt: elements.promptInput.value.trim(),
    };
  }

  function updateCounts() {
    elements.descriptionCount.textContent = `${elements.descriptionInput.value.length} / 300`;
    elements.promptCount.textContent = `${elements.promptInput.value.length} / 20000`;
  }

  function validate() {
    const values = currentValues();
    elements.nameError.textContent = values.name ? "" : "Name is required.";
    elements.promptError.textContent =
      values.prompt.length >= 20 ? "" : "System prompt must contain at least 20 characters.";
    return Boolean(values.name && values.prompt.length >= 20);
  }

  function refreshDirtyState() {
    if (!state.original) {
      state.dirty = false;
    } else {
      const values = currentValues();
      state.dirty =
        values.name !== state.original.name ||
        values.description !== state.original.description ||
        values.prompt !== state.original.prompt;
    }

    const preset = selectedPreset();
    const editable = state.draft || Boolean(preset && !preset.built_in);
    elements.saveButton.disabled = !editable || !state.dirty || state.saving;
    elements.discardButton.disabled = !state.dirty || state.saving;
    setSaveState(state.dirty ? "Unsaved" : "Saved", state.dirty ? "dirty" : "saved");
    updateCounts();
    validate();
  }

  function confirmAbandonChanges() {
    return !state.dirty || window.confirm("Discard your unsaved personality changes?");
  }

  function renderList() {
    const query = elements.search.value.trim().toLowerCase();
    const visible = state.presets.filter((preset) =>
      !query || `${preset.name} ${preset.description}`.toLowerCase().includes(query),
    );

    elements.list.replaceChildren();
    elements.listStatus.textContent = visible.length
      ? `${visible.length} ${visible.length === 1 ? "personality" : "personalities"}`
      : "No personalities found.";

    for (const preset of visible) {
      const fragment = elements.presetTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".preset-card");
      const badges = fragment.querySelector(".preset-badges");
      card.dataset.presetId = preset.id;
      card.classList.toggle("selected", preset.id === state.selectedId);
      card.setAttribute("aria-selected", String(preset.id === state.selectedId));
      fragment.querySelector(".preset-name").textContent = preset.name;
      fragment.querySelector(".preset-description").textContent =
        preset.description || "No description";
      fragment.querySelector(".preset-meta").textContent = preset.built_in
        ? "Protected built-in preset"
        : `Updated ${formatDate(preset.updated_at)}`;

      if (preset.id === state.activeId) {
        const active = document.createElement("span");
        active.className = "mini-badge active";
        active.textContent = "Active";
        badges.appendChild(active);
      }
      if (preset.built_in) {
        const builtIn = document.createElement("span");
        builtIn.className = "mini-badge";
        builtIn.textContent = "Built-in";
        badges.appendChild(builtIn);
      }

      card.addEventListener("click", () => selectPreset(preset.id));
      elements.list.appendChild(fragment);
    }
  }

  function showEditor() {
    elements.emptyState.hidden = true;
    elements.form.hidden = false;
  }

  function selectPreset(presetId, { force = false } = {}) {
    if (!force && !confirmAbandonChanges()) return;
    const preset = state.presets.find((item) => item.id === presetId);
    if (!preset) return;

    state.selectedId = preset.id;
    state.draft = false;
    state.original = {
      name: preset.name,
      description: preset.description,
      prompt: preset.prompt,
    };
    state.dirty = false;

    showEditor();
    elements.nameInput.value = preset.name;
    elements.descriptionInput.value = preset.description;
    elements.promptInput.value = preset.prompt;
    elements.editorTitle.textContent = preset.name;
    elements.editorSubtitle.textContent = preset.built_in
      ? "Built-in presets are protected. Duplicate this one to customize it."
      : "Changes are stored locally in data/personalities.json.";
    elements.activeBadge.hidden = preset.id !== state.activeId;
    elements.builtInBadge.hidden = !preset.built_in;
    elements.draftBadge.hidden = true;

    elements.nameInput.disabled = preset.built_in;
    elements.descriptionInput.disabled = preset.built_in;
    elements.promptInput.disabled = preset.built_in;
    elements.activateButton.disabled = preset.id === state.activeId && !state.legacyOverride;
    elements.activateButton.textContent = preset.id === state.activeId ? "Currently active" : "Use personality";
    elements.duplicateButton.disabled = false;
    elements.deleteButton.disabled = preset.built_in || preset.id === state.activeId;
    elements.deleteButton.title = preset.built_in
      ? "Built-in personalities cannot be deleted"
      : preset.id === state.activeId
        ? "Activate another personality before deleting this one"
        : "Delete personality";
    elements.updatedText.textContent = preset.built_in
      ? "Built-in preset"
      : `Last updated ${formatDate(preset.updated_at)}`;

    if (state.legacyOverride) {
      showNotice(
        "Legacy prompt override is active",
        "The custom prompt in Settings currently overrides every preset. Activating a personality here clears that override.",
      );
    } else {
      clearNotice();
    }
    clearError();
    renderList();
    refreshDirtyState();
  }

  function startDraft() {
    if (!confirmAbandonChanges()) return;
    state.selectedId = null;
    state.draft = true;
    state.original = { name: "", description: "", prompt: "" };
    showEditor();
    elements.nameInput.disabled = false;
    elements.descriptionInput.disabled = false;
    elements.promptInput.disabled = false;
    elements.nameInput.value = "";
    elements.descriptionInput.value = "";
    elements.promptInput.value = "";
    elements.editorTitle.textContent = "New personality";
    elements.editorSubtitle.textContent = "Create a reusable personality preset for Akira.";
    elements.activeBadge.hidden = true;
    elements.builtInBadge.hidden = true;
    elements.draftBadge.hidden = false;
    elements.activateButton.disabled = true;
    elements.activateButton.textContent = "Save before activating";
    elements.duplicateButton.disabled = true;
    elements.deleteButton.disabled = true;
    elements.updatedText.textContent = "Not saved yet";
    clearNotice();
    clearError();
    renderList();
    elements.nameInput.focus();
    refreshDirtyState();
  }

  async function saveCurrent(event) {
    event.preventDefault();
    if (!validate() || state.saving) return;

    state.saving = true;
    refreshDirtyState();
    setSaveState("Saving");
    clearError();
    const values = currentValues();

    try {
      let response;
      const wasDraft = state.draft;
      if (wasDraft) {
        response = await api("/api/personalities", {
          method: "POST",
          body: JSON.stringify({ ...values, activate: false }),
        });
        state.presets.push(response.preset);
        state.selectedId = response.preset.id;
        state.draft = false;
      } else {
        response = await api(`/api/personalities/${encodeURIComponent(state.selectedId)}`, {
          method: "PATCH",
          body: JSON.stringify(values),
        });
        const index = state.presets.findIndex((item) => item.id === response.preset.id);
        if (index >= 0) state.presets[index] = response.preset;
      }
      state.activeId = response.active_id;
      selectPreset(response.preset.id, { force: true });
      showNotice(
        wasDraft ? "Personality created" : "Changes saved",
        wasDraft
          ? "The new preset is ready. Click Use personality to activate it."
          : response.applied_live
            ? "The active personality was updated immediately without clearing this chat."
            : "The personality preset was saved locally.",
      );
      setSaveState("Saved", "saved");
    } catch (error) {
      showError(error.message);
      setSaveState("Error");
    } finally {
      state.saving = false;
      refreshDirtyState();
    }
  }

  async function duplicateCurrent() {
    const preset = selectedPreset();
    if (!preset || !confirmAbandonChanges()) return;
    clearError();
    elements.duplicateButton.disabled = true;
    try {
      const response = await api(`/api/personalities/${encodeURIComponent(preset.id)}/duplicate`, {
        method: "POST",
        body: JSON.stringify({ name: `${preset.name} Copy`, activate: false }),
      });
      state.presets.push(response.preset);
      selectPreset(response.preset.id, { force: true });
      showNotice("Personality duplicated", "Edit the copy, then activate it when you are ready.");
    } catch (error) {
      showError(error.message);
      elements.duplicateButton.disabled = false;
    }
  }

  async function activateCurrent() {
    const preset = selectedPreset();
    if (!preset || state.dirty) {
      if (state.dirty) showError("Save or discard your changes before activating this personality.");
      return;
    }
    clearError();
    elements.activateButton.disabled = true;
    try {
      const response = await api(`/api/personalities/${encodeURIComponent(preset.id)}/activate`, {
        method: "POST",
      });
      state.activeId = response.active_id;
      state.legacyOverride = false;
      selectPreset(preset.id, { force: true });
      showNotice(
        "Personality activated",
        response.applied_live
          ? `${preset.name} is active now. The current chat context was preserved.`
          : `${preset.name} will be used when Akira's LLM is first loaded.`,
      );
    } catch (error) {
      showError(error.message);
      elements.activateButton.disabled = false;
    }
  }

  function closeDeleteDialog() {
    if (elements.deleteDialog.open) {
      elements.deleteDialog.close("cancel");
    }
  }

  async function deleteCurrent(event) {
    event.preventDefault();

    // Defensive guard: only the explicit destructive submit button may delete.
    if (event.submitter && event.submitter.value === "cancel") {
      closeDeleteDialog();
      return;
    }

    const preset = selectedPreset();
    if (!preset) return;
    try {
      await api(`/api/personalities/${encodeURIComponent(preset.id)}`, { method: "DELETE" });
      state.presets = state.presets.filter((item) => item.id !== preset.id);
      elements.deleteDialog.close();
      const next = state.presets.find((item) => item.id === state.activeId) || state.presets[0];
      if (next) selectPreset(next.id, { force: true });
      showNotice("Personality deleted", `${preset.name} was removed from this computer.`);
    } catch (error) {
      elements.deleteDialog.close();
      showError(error.message);
    }
  }

  function discardChanges() {
    if (state.draft) {
      const active = state.presets.find((item) => item.id === state.activeId) || state.presets[0];
      if (active) selectPreset(active.id, { force: true });
      return;
    }
    if (state.selectedId) selectPreset(state.selectedId, { force: true });
  }

  async function loadPersonalities({ keepSelection = false } = {}) {
    setSaveState("Loading");
    try {
      const response = await api("/api/personalities");
      state.presets = response.presets;
      state.activeId = response.active_id;
      state.legacyOverride = response.legacy_prompt_override;
      const preferred = keepSelection && state.selectedId
        ? state.presets.find((item) => item.id === state.selectedId)
        : state.presets.find((item) => item.id === state.activeId);
      const selected = preferred || state.presets[0];
      renderList();
      if (selected) selectPreset(selected.id, { force: true });
      setSaveState("Saved", "saved");
    } catch (error) {
      elements.listStatus.textContent = "Unable to load personalities.";
      showError(error.message);
      setSaveState("Offline");
    }
  }

  function connectEvents() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/events`);
    state.websocket = socket;
    socket.addEventListener("message", (message) => {
      try {
        const event = JSON.parse(message.data);
        if (["personality.created", "personality.updated", "personality.deleted"].includes(event.type)) {
          loadPersonalities({ keepSelection: true });
        }
        if (event.type === "personality.changed") {
          state.activeId = event.data.preset_id;
          state.legacyOverride = false;
          renderList();
          if (state.selectedId) selectPreset(state.selectedId, { force: true });
        }
      } catch (error) {
        console.error("Invalid Project Akira event", error);
      }
    });
    socket.addEventListener("close", () => {
      window.setTimeout(connectEvents, 1800);
    });
  }

  elements.form.addEventListener("submit", saveCurrent);
  elements.newButton.addEventListener("click", startDraft);
  elements.search.addEventListener("input", renderList);
  elements.nameInput.addEventListener("input", refreshDirtyState);
  elements.descriptionInput.addEventListener("input", refreshDirtyState);
  elements.promptInput.addEventListener("input", refreshDirtyState);
  elements.discardButton.addEventListener("click", discardChanges);
  elements.duplicateButton.addEventListener("click", duplicateCurrent);
  elements.activateButton.addEventListener("click", activateCurrent);
  elements.deleteButton.addEventListener("click", () => elements.deleteDialog.showModal());
  elements.closeDeleteButton.addEventListener("click", closeDeleteDialog);
  elements.cancelDeleteButton.addEventListener("click", closeDeleteDialog);
  elements.deleteForm.addEventListener("submit", deleteCurrent);
  elements.dismissNotice.addEventListener("click", clearNotice);
  elements.dismissErrorButton.addEventListener("click", clearError);
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
    if (state.websocket) state.websocket.close();
  });

  loadPersonalities();
  connectEvents();
})();
