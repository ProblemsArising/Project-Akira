(() => {
  "use strict";

  const elements = {
    messages: document.getElementById("messages"),
    welcomeCard: document.getElementById("welcomeCard"),
    composer: document.getElementById("composer"),
    messageInput: document.getElementById("messageInput"),
    sendButton: document.getElementById("sendButton"),
    speakToggle: document.getElementById("speakToggle"),
    listenButton: document.getElementById("listenButton"),
    listenButtonText: document.getElementById("listenButtonText"),
    newConversationButton: document.getElementById("newConversationButton"),
    connectionBadge: document.getElementById("connectionBadge"),
    connectionText: document.getElementById("connectionText"),
    activityBadge: document.getElementById("activityBadge"),
    conversationMeta: document.getElementById("conversationMeta"),
    errorBanner: document.getElementById("errorBanner"),
    errorText: document.getElementById("errorText"),
    dismissErrorButton: document.getElementById("dismissErrorButton"),
    messageTemplate: document.getElementById("messageTemplate"),
  };

  const state = {
    websocket: null,
    reconnectTimer: null,
    reconnectDelay: 1000,
    connected: false,
    busy: false,
    listening: false,
    running: false,
    conversationId: null,
    pendingTurn: null,
    eventTurn: null,
  };

  const savedSpeak = window.localStorage.getItem("akira.speakReplies");
  if (savedSpeak !== null) {
    elements.speakToggle.checked = savedSpeak === "true";
  }

  function setConnection(connected, label) {
    state.connected = connected;
    elements.connectionBadge.classList.toggle("online", connected);
    elements.connectionBadge.classList.toggle("offline", !connected);
    elements.connectionText.textContent = label;
  }

  function setActivity(label) {
    elements.activityBadge.textContent = label;
  }

  function showError(message) {
    elements.errorText.textContent = String(message || "Something went wrong.");
    elements.errorBanner.hidden = false;
  }

  function clearError() {
    elements.errorBanner.hidden = true;
    elements.errorText.textContent = "";
  }

  function normalizeError(payload, fallback) {
    if (payload && typeof payload.detail === "string") {
      return payload.detail;
    }
    if (payload && Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg || String(item)).join(", ");
    }
    return fallback;
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });

    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(normalizeError(payload, `Request failed (${response.status})`));
    }
    return payload;
  }

  function scrollToBottom() {
    window.requestAnimationFrame(() => {
      elements.messages.scrollTop = elements.messages.scrollHeight;
    });
  }

  function hideWelcome() {
    if (elements.welcomeCard) {
      elements.welcomeCard.hidden = true;
    }
  }

  function clearMessages() {
    elements.messages.querySelectorAll(".message").forEach((node) => node.remove());
    if (elements.welcomeCard) {
      elements.welcomeCard.hidden = false;
    }
  }

  function createMessage(role, text = "", source = "text", pending = false) {
    hideWelcome();

    const fragment = elements.messageTemplate.content.cloneNode(true);
    const article = fragment.querySelector(".message");
    const avatar = fragment.querySelector(".message-avatar");
    const author = fragment.querySelector(".message-author");
    const sourceLabel = fragment.querySelector(".message-source");
    const bubble = fragment.querySelector(".message-bubble");

    article.classList.add(role);
    if (pending) {
      article.classList.add("pending");
    }

    const isUser = role === "user";
    avatar.textContent = isUser ? "Y" : "A";
    author.textContent = isUser ? "You" : "Akira";
    sourceLabel.textContent = source === "voice" ? "Voice" : "";
    bubble.textContent = text;

    if (pending) {
      bubble.innerHTML = [
        '<span class="typing-dots" aria-label="Akira is thinking">',
        "<span></span><span></span><span></span>",
        "</span>",
      ].join("");
    }

    elements.messages.appendChild(fragment);
    scrollToBottom();
    return article;
  }

  function setMessageText(messageElement, text) {
    if (!messageElement) {
      return;
    }
    const bubble = messageElement.querySelector(".message-bubble");
    messageElement.classList.remove("pending");
    bubble.textContent = text;
    scrollToBottom();
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.sendButton.disabled = busy;
    elements.messageInput.disabled = busy;
    elements.newConversationButton.disabled = busy;
  }

  function autoResizeInput() {
    elements.messageInput.style.height = "auto";
    elements.messageInput.style.height =
      `${Math.min(elements.messageInput.scrollHeight, 150)}px`;
  }

  function updateListeningState(isListening, isRunning = isListening) {
    state.listening = Boolean(isListening);
    state.running = Boolean(isRunning);

    elements.listenButton.classList.toggle("active", state.listening);
    elements.listenButton.setAttribute(
      "aria-label",
      state.listening ? "Stop microphone listening" : "Start microphone listening",
    );
    elements.listenButton.title =
      state.listening ? "Stop microphone listening" : "Start microphone listening";
    elements.listenButtonText.textContent = state.listening ? "Stop" : "Listen";

    if (state.listening) {
      setActivity("Listening");
    } else if (!state.busy) {
      setActivity("Ready");
    }
  }

  function updateConversation(conversationId) {
    state.conversationId =
      conversationId === null || conversationId === undefined
        ? null
        : Number(conversationId);

    elements.conversationMeta.textContent = state.conversationId
      ? `Conversation #${state.conversationId} · saved locally`
      : "Messages stay on this computer.";
  }

  function turnMatches(turn, data) {
    return Boolean(
      turn &&
      data &&
      String(turn.userText || "").trim() === String(data.user_text || "").trim(),
    );
  }

  function beginTurn(userText, source, ownedByRequest = false) {
    const userMessage = createMessage("user", userText, source);
    const assistantMessage = createMessage("assistant", "", source, true);
    return {
      userText,
      source,
      userMessage,
      assistantMessage,
      ownedByRequest,
      completed: false,
    };
  }

  function completeTurn(turn, data) {
    if (!turn || turn.completed) {
      return;
    }
    turn.completed = true;
    setMessageText(turn.assistantMessage, data.reply || "");
    updateConversation(data.conversation_id);
    setActivity(data.spoken ? "Finished speaking" : "Ready");
  }

  async function sendMessage(text) {
    const normalized = String(text || "").trim();
    if (!normalized || state.busy) {
      return;
    }

    clearError();
    setBusy(true);
    setActivity("Thinking");

    const turn = beginTurn(normalized, "text", true);
    state.pendingTurn = turn;

    elements.messageInput.value = "";
    autoResizeInput();

    try {
      const response = await apiRequest("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          message: normalized,
          speak: elements.speakToggle.checked,
        }),
      });

      if (state.pendingTurn === turn && !turn.completed) {
        completeTurn(turn, response);
        state.pendingTurn = null;
      }
    } catch (error) {
      if (state.pendingTurn === turn) {
        setMessageText(turn.assistantMessage, `Error: ${error.message}`);
        turn.assistantMessage.classList.add("error");
        state.pendingTurn = null;
      }
      showError(error.message);
      setActivity("Error");
    } finally {
      setBusy(false);
      elements.messageInput.focus();
      if (!state.listening && elements.activityBadge.textContent !== "Error") {
        setActivity("Ready");
      }
    }
  }

  function beginExternalTurn(data) {
    if (state.eventTurn && !state.eventTurn.completed) {
      return state.eventTurn;
    }
    state.eventTurn = beginTurn(data.user_text || "", data.source || "voice");
    return state.eventTurn;
  }

  function handleEvent(event) {
    const data = event.data || {};

    switch (event.type) {
      case "connection.ready":
        setConnection(true, "Connected");
        state.reconnectDelay = 1000;
        if (data.status) {
          updateListeningState(
            data.status.is_listening,
            data.status.is_running,
          );
          updateConversation(data.status.conversation_id);
        }
        break;

      case "connection.pong":
        break;

      case "status.snapshot":
        updateListeningState(data.is_listening, data.is_running);
        updateConversation(data.conversation_id);
        break;

      case "listening.changed":
        updateListeningState(data.is_listening, data.is_running);
        break;

      case "voice.recording.started":
        setActivity("Listening");
        break;

      case "voice.recording.completed":
        setActivity("Transcribing");
        break;

      case "voice.transcription.started":
        setActivity("Transcribing");
        break;

      case "voice.transcription.completed":
        setActivity("Thinking");
        break;

      case "voice.recording.cancelled":
      case "voice.transcription.empty":
        setActivity(state.listening ? "Listening" : "Ready");
        break;

      case "chat.started":
        setActivity("Thinking");
        if (turnMatches(state.pendingTurn, data)) {
          break;
        }
        beginExternalTurn(data);
        break;

      case "chat.reply_ready": {
        setActivity(data.will_speak ? "Speaking" : "Reply ready");
        const turn = turnMatches(state.pendingTurn, data)
          ? state.pendingTurn
          : turnMatches(state.eventTurn, data)
            ? state.eventTurn
            : beginExternalTurn(data);
        setMessageText(turn.assistantMessage, data.reply || "");
        break;
      }

      case "chat.completed": {
        const turn = turnMatches(state.pendingTurn, data)
          ? state.pendingTurn
          : turnMatches(state.eventTurn, data)
            ? state.eventTurn
            : beginExternalTurn(data);

        completeTurn(turn, data);

        if (turn === state.pendingTurn) {
          state.pendingTurn = null;
        }
        if (turn === state.eventTurn) {
          state.eventTurn = null;
        }
        break;
      }

      case "chat.failed": {
        const turn = turnMatches(state.pendingTurn, data)
          ? state.pendingTurn
          : state.eventTurn;
        if (turn) {
          setMessageText(
            turn.assistantMessage,
            data.error || "Akira could not complete that reply.",
          );
        }
        showError(data.error || "Akira could not complete that reply.");
        setActivity("Error");
        break;
      }

      case "conversation.changed":
        updateConversation(data.conversation_id);
        clearMessages();
        setActivity("New conversation");
        break;

      case "history.error":
        showError(`History warning: ${data.error || "Unable to save conversation."}`);
        break;

      case "system.shutdown":
        setConnection(false, "Server stopped");
        setActivity("Offline");
        break;

      default:
        break;
    }
  }

  function connectWebSocket() {
    if (
      state.websocket &&
      (state.websocket.readyState === WebSocket.CONNECTING ||
        state.websocket.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/api/events`,
    );
    state.websocket = socket;
    setConnection(false, "Connecting");

    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ type: "status" }));
    });

    socket.addEventListener("message", (message) => {
      try {
        handleEvent(JSON.parse(message.data));
      } catch (error) {
        console.error("Invalid Project Akira event", error);
      }
    });

    socket.addEventListener("close", () => {
      if (state.websocket !== socket) {
        return;
      }
      setConnection(false, "Reconnecting");
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = window.setTimeout(
        connectWebSocket,
        state.reconnectDelay,
      );
      state.reconnectDelay = Math.min(state.reconnectDelay * 1.7, 10000);
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  async function refreshStatus() {
    try {
      const status = await apiRequest("/api/status");
      updateListeningState(status.is_listening, status.is_running);
      updateConversation(status.conversation_id);

      if (status.conversation_id) {
        await loadConversation(status.conversation_id);
      }
    } catch (error) {
      showError(`Unable to read server status: ${error.message}`);
    }
  }

  async function loadConversation(conversationId) {
    try {
      const turns = await apiRequest(
        `/api/conversations/${encodeURIComponent(conversationId)}`,
      );
      if (!Array.isArray(turns) || turns.length === 0) {
        return;
      }

      clearMessages();
      for (const turn of turns) {
        createMessage("user", turn.user_text, turn.source);
        createMessage("assistant", turn.assistant_text, turn.source);
      }
    } catch (error) {
      showError(`Unable to load current conversation: ${error.message}`);
    }
  }

  async function toggleListening() {
    clearError();
    elements.listenButton.disabled = true;

    try {
      const endpoint = state.listening
        ? "/api/listening/stop"
        : "/api/listening/start";
      const result = await apiRequest(endpoint, { method: "POST" });
      updateListeningState(result.is_listening, result.is_running);
    } catch (error) {
      showError(error.message);
    } finally {
      elements.listenButton.disabled = false;
    }
  }

  async function startNewConversation() {
    if (state.busy) {
      return;
    }

    clearError();
    elements.newConversationButton.disabled = true;

    try {
      const result = await apiRequest("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ title: null }),
      });
      state.pendingTurn = null;
      state.eventTurn = null;
      updateConversation(result.conversation_id);
      clearMessages();
      setActivity("New conversation");
      elements.messageInput.focus();
    } catch (error) {
      showError(error.message);
    } finally {
      elements.newConversationButton.disabled = false;
    }
  }

  elements.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(elements.messageInput.value);
  });

  elements.messageInput.addEventListener("input", autoResizeInput);
  elements.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });

  elements.speakToggle.addEventListener("change", () => {
    window.localStorage.setItem(
      "akira.speakReplies",
      String(elements.speakToggle.checked),
    );
  });

  elements.listenButton.addEventListener("click", toggleListening);
  elements.newConversationButton.addEventListener(
    "click",
    startNewConversation,
  );
  elements.dismissErrorButton.addEventListener("click", clearError);

  window.addEventListener("beforeunload", () => {
    if (state.websocket) {
      state.websocket.close();
    }
  });

  connectWebSocket();
  refreshStatus();
  autoResizeInput();
  elements.messageInput.focus();
})();
