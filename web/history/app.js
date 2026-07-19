(() => {
  "use strict";

  const elements = {
    connectionBadge: document.getElementById("connectionBadge"),
    connectionText: document.getElementById("connectionText"),
    newChatButton: document.getElementById("newChatButton"),
    searchInput: document.getElementById("searchInput"),
    clearSearchButton: document.getElementById("clearSearchButton"),
    listStatus: document.getElementById("listStatus"),
    conversationList: document.getElementById("conversationList"),
    emptyState: document.getElementById("emptyState"),
    conversationView: document.getElementById("conversationView"),
    conversationTitle: document.getElementById("conversationTitle"),
    conversationDetails: document.getElementById("conversationDetails"),
    activeBadge: document.getElementById("activeBadge"),
    transcript: document.getElementById("transcript"),
    continueButton: document.getElementById("continueButton"),
    renameButton: document.getElementById("renameButton"),
    deleteButton: document.getElementById("deleteButton"),
    errorBanner: document.getElementById("errorBanner"),
    errorText: document.getElementById("errorText"),
    dismissErrorButton: document.getElementById("dismissErrorButton"),
    conversationCardTemplate: document.getElementById("conversationCardTemplate"),
    turnTemplate: document.getElementById("turnTemplate"),
    renameDialog: document.getElementById("renameDialog"),
    renameForm: document.getElementById("renameForm"),
    renameInput: document.getElementById("renameInput"),
    confirmRenameButton: document.getElementById("confirmRenameButton"),
    deleteDialog: document.getElementById("deleteDialog"),
    deleteForm: document.getElementById("deleteForm"),
    confirmDeleteButton: document.getElementById("confirmDeleteButton"),
  };

  const state = {
    conversations: [],
    selectedId: null,
    selectedSummary: null,
    activeId: null,
    query: "",
    loadSequence: 0,
    searchTimer: null,
    websocket: null,
    reconnectTimer: null,
    reconnectDelay: 1000,
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
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

  function setConnection(online, label) {
    elements.connectionBadge.classList.toggle("online", online);
    elements.connectionBadge.classList.toggle("offline", !online);
    elements.connectionText.textContent = label;
  }

  function parseDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function relativeTime(value) {
    const date = parseDate(value);
    if (!date) return "";
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    const ranges = [[31536000, "year"], [2592000, "month"], [604800, "week"], [86400, "day"], [3600, "hour"], [60, "minute"]];
    for (const [size, unit] of ranges) {
      if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
    }
    return "just now";
  }

  function fullDate(value) {
    const date = parseDate(value);
    if (!date) return "Unknown date";
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function renderConversationList() {
    elements.conversationList.replaceChildren();
    const count = state.conversations.length;
    elements.listStatus.textContent = state.query
      ? `${count} matching conversation${count === 1 ? "" : "s"}`
      : `${count} saved conversation${count === 1 ? "" : "s"}`;

    for (const summary of state.conversations) {
      const fragment = elements.conversationCardTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".conversation-card");
      card.dataset.conversationId = String(summary.id);
      card.classList.toggle("selected", summary.id === state.selectedId);
      card.setAttribute("aria-selected", String(summary.id === state.selectedId));
      fragment.querySelector(".card-title").textContent = summary.title;
      const time = fragment.querySelector(".card-time");
      time.textContent = relativeTime(summary.updated_at);
      time.dateTime = summary.updated_at;
      fragment.querySelector(".card-preview").textContent = summary.last_message || "No messages yet";
      fragment.querySelector(".card-meta").textContent = `${summary.turn_count} turn${summary.turn_count === 1 ? "" : "s"}${summary.id === state.activeId ? " · Active" : ""}`;
      card.addEventListener("click", () => selectConversation(summary.id));
      elements.conversationList.appendChild(fragment);
    }

    if (count === 0) {
      const empty = document.createElement("div");
      empty.className = "list-status";
      empty.textContent = state.query ? "No conversations matched your search." : "No saved conversations yet.";
      elements.conversationList.appendChild(empty);
    }
  }

  function renderTranscript(turns) {
    elements.transcript.replaceChildren();
    if (!turns.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML = "<h2>No messages yet</h2><p>This conversation was created but has no completed turns.</p>";
      elements.transcript.appendChild(empty);
      return;
    }

    for (const turn of turns) {
      const fragment = elements.turnTemplate.content.cloneNode(true);
      fragment.querySelector(".user-bubble").textContent = turn.user_text;
      fragment.querySelector(".assistant-bubble").textContent = turn.assistant_text;
      fragment.querySelector(".message-source").textContent = turn.source === "voice" ? "Voice" : "Text";
      fragment.querySelector(".spoken-state").textContent = turn.spoken ? "Spoken" : "Text only";
      const time = fragment.querySelector("time");
      time.textContent = fullDate(turn.created_at);
      time.dateTime = turn.created_at;
      elements.transcript.appendChild(fragment);
    }
    elements.transcript.scrollTop = 0;
  }

  function showConversation(summary, turns) {
    state.selectedSummary = summary;
    elements.emptyState.hidden = true;
    elements.conversationView.hidden = false;
    elements.conversationTitle.textContent = summary.title;
    elements.conversationDetails.textContent = `${summary.turn_count} turn${summary.turn_count === 1 ? "" : "s"} · Updated ${fullDate(summary.updated_at)}`;
    elements.activeBadge.hidden = summary.id !== state.activeId;
    renderTranscript(turns);
    renderConversationList();
  }

  function clearSelection() {
    state.selectedId = null;
    state.selectedSummary = null;
    elements.conversationView.hidden = true;
    elements.emptyState.hidden = false;
    elements.transcript.replaceChildren();
    renderConversationList();
  }

  async function selectConversation(id) {
    const selected = Number(id);
    if (!Number.isInteger(selected)) return;
    state.selectedId = selected;
    renderConversationList();
    const sequence = ++state.loadSequence;
    clearError();

    try {
      const [summary, turns] = await Promise.all([
        api(`/api/conversations/${selected}/summary`),
        api(`/api/conversations/${selected}`),
      ]);
      if (sequence !== state.loadSequence) return;
      showConversation(summary, turns);
    } catch (error) {
      if (sequence !== state.loadSequence) return;
      showError(error.message);
      clearSelection();
    }
  }

  async function loadConversations({ preserveSelection = true } = {}) {
    const params = new URLSearchParams({ limit: "200" });
    if (state.query) params.set("query", state.query);
    try {
      const conversations = await api(`/api/conversations?${params}`);
      state.conversations = Array.isArray(conversations) ? conversations : [];
      renderConversationList();

      const stillExists = state.conversations.some((item) => item.id === state.selectedId);
      if (preserveSelection && stillExists) return;
      if (state.conversations.length) await selectConversation(state.conversations[0].id);
      else clearSelection();
    } catch (error) {
      showError(error.message);
      elements.listStatus.textContent = "Unable to load history.";
    }
  }

  async function loadStatus() {
    try {
      const status = await api("/api/status");
      state.activeId = status.conversation_id;
      renderConversationList();
      if (state.selectedSummary) elements.activeBadge.hidden = state.selectedSummary.id !== state.activeId;
    } catch (error) {
      showError(`Unable to read current status: ${error.message}`);
    }
  }

  async function startNewChat() {
    elements.newChatButton.disabled = true;
    try {
      await api("/api/conversations", { method: "POST", body: JSON.stringify({ title: null }) });
      window.location.assign("/");
    } catch (error) {
      showError(error.message);
      elements.newChatButton.disabled = false;
    }
  }

  async function continueSelected() {
    if (!state.selectedSummary) return;

    clearError();
    elements.continueButton.disabled = true;

    try {
      await api(
        `/api/conversations/${state.selectedSummary.id}/activate`,
        { method: "POST" },
      );
      window.location.assign("/");
    } catch (error) {
      showError(error.message);
      elements.continueButton.disabled = false;
    }
  }

  async function renameSelected(event) {
    event.preventDefault();
    if (!state.selectedSummary) return;
    const title = elements.renameInput.value.trim();
    if (!title) return;
    elements.confirmRenameButton.disabled = true;
    try {
      const updated = await api(`/api/conversations/${state.selectedSummary.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      state.selectedSummary = updated;
      const index = state.conversations.findIndex((item) => item.id === updated.id);
      if (index >= 0) state.conversations[index] = updated;
      elements.conversationTitle.textContent = updated.title;
      renderConversationList();
      elements.renameDialog.close();
    } catch (error) {
      showError(error.message);
    } finally {
      elements.confirmRenameButton.disabled = false;
    }
  }

  async function deleteSelected(event) {
    event.preventDefault();
    if (!state.selectedSummary) return;
    const deletedId = state.selectedSummary.id;
    elements.confirmDeleteButton.disabled = true;
    try {
      await api(`/api/conversations/${deletedId}`, { method: "DELETE" });
      elements.deleteDialog.close();
      state.conversations = state.conversations.filter((item) => item.id !== deletedId);
      clearSelection();
      await loadStatus();
      await loadConversations({ preserveSelection: false });
    } catch (error) {
      showError(error.message);
    } finally {
      elements.confirmDeleteButton.disabled = false;
    }
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/events`);
    state.websocket = socket;
    setConnection(false, "Connecting");

    socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "status" })));
    socket.addEventListener("message", (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { return; }
      const data = event.data || {};
      if (event.type === "connection.ready") {
        setConnection(true, "Connected");
        state.reconnectDelay = 1000;
        state.activeId = data.status && data.status.conversation_id;
        renderConversationList();
      } else if (event.type === "status.snapshot" || event.type === "conversation.changed") {
        state.activeId = data.conversation_id;
        renderConversationList();
      } else if (["chat.completed", "history.conversation_renamed", "history.conversation_deleted"].includes(event.type)) {
        loadConversations();
      } else if (event.type === "system.shutdown") {
        setConnection(false, "Server stopped");
      }
    });
    socket.addEventListener("close", () => {
      if (state.websocket !== socket) return;
      setConnection(false, "Reconnecting");
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = setTimeout(connectWebSocket, state.reconnectDelay);
      state.reconnectDelay = Math.min(state.reconnectDelay * 1.7, 10000);
    });
    socket.addEventListener("error", () => socket.close());
  }

  elements.searchInput.addEventListener("input", () => {
    state.query = elements.searchInput.value.trim();
    elements.clearSearchButton.hidden = !state.query;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => loadConversations({ preserveSelection: false }), 220);
  });
  elements.clearSearchButton.addEventListener("click", () => {
    elements.searchInput.value = "";
    state.query = "";
    elements.clearSearchButton.hidden = true;
    loadConversations({ preserveSelection: false });
    elements.searchInput.focus();
  });
  elements.newChatButton.addEventListener("click", startNewChat);
  elements.continueButton.addEventListener("click", continueSelected);
  elements.renameButton.addEventListener("click", () => {
    if (!state.selectedSummary) return;
    elements.renameInput.value = state.selectedSummary.title;
    elements.renameDialog.showModal();
    elements.renameInput.select();
  });
  elements.deleteButton.addEventListener("click", () => state.selectedSummary && elements.deleteDialog.showModal());
  elements.renameForm.addEventListener("submit", renameSelected);
  elements.deleteForm.addEventListener("submit", deleteSelected);
  elements.dismissErrorButton.addEventListener("click", clearError);
  window.addEventListener("beforeunload", () => state.websocket && state.websocket.close());

  connectWebSocket();
  Promise.all([loadStatus(), loadConversations()]);
})();
