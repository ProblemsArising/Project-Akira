(() => {
  "use strict";

  const elements = {
    connectionBadge: document.getElementById("connectionBadge"),
    connectionText: document.getElementById("connectionText"),
    stateLabel: document.getElementById("stateLabel"),
    stateDetail: document.getElementById("stateDetail"),
    modeText: document.getElementById("modeText"),
  };

  const state = {
    socket: null,
    reconnectTimer: null,
    reconnectDelay: 1000,
    listening: false,
  };

  const COPY = {
    idle: ["Ready", "Waiting for your next message."],
    listening: ["Listening", "Talk normally. Akira is waiting for your voice."],
    transcribing: ["Transcribing", "Turning the recorded audio into text."],
    thinking: ["Thinking", "Akira is preparing a reply."],
    speaking: ["Speaking", "Akira is answering now."],
    error: ["Something went wrong", "Check the main window for details."],
    offline: ["Offline", "Waiting for the Project Akira backend."],
  };

  function setConnection(online, label) {
    elements.connectionBadge.classList.toggle("online", online);
    elements.connectionBadge.classList.toggle("offline", !online);
    elements.connectionText.textContent = label;
  }

  function setStage(nextState, detail = null) {
    const normalized = Object.prototype.hasOwnProperty.call(COPY, nextState)
      ? nextState
      : "idle";
    const [label, defaultDetail] = COPY[normalized];
    document.body.dataset.state = normalized;
    elements.stateLabel.textContent = label;
    elements.stateDetail.textContent = detail || defaultDetail;
  }

  function restoreIdle() {
    setStage(state.listening ? "listening" : "idle");
  }

  function handleEvent(event) {
    const data = event.data || {};

    switch (event.type) {
      case "connection.ready":
        setConnection(true, "Connected");
        state.reconnectDelay = 1000;
        state.listening = Boolean(data.status && data.status.is_listening);
        restoreIdle();
        break;
      case "connection.pong":
        break;
      case "status.snapshot":
        state.listening = Boolean(data.is_listening);
        restoreIdle();
        break;
      case "listening.changed":
        state.listening = Boolean(data.is_listening);
        restoreIdle();
        break;
      case "voice.recording.started":
        setStage("listening");
        break;
      case "voice.recording.completed":
      case "voice.transcription.started":
        setStage("transcribing");
        break;
      case "voice.transcription.completed":
      case "chat.started":
        setStage("thinking");
        break;
      case "chat.reply_ready":
        setStage(data.will_speak ? "speaking" : "idle");
        break;
      case "chat.completed":
      case "voice.recording.cancelled":
      case "voice.transcription.empty":
        restoreIdle();
        break;
      case "chat.failed":
        setStage("error", data.error || "Akira could not complete that reply.");
        break;
      case "personality.changed":
        if (data.name) {
          elements.modeText.textContent = data.name;
        }
        break;
      case "system.shutdown":
        setConnection(false, "Server stopped");
        setStage("offline");
        break;
      default:
        break;
    }
  }

  function connect() {
    if (
      state.socket &&
      (state.socket.readyState === WebSocket.CONNECTING ||
        state.socket.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/events`);
    state.socket = socket;
    setConnection(false, "Connecting");

    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ type: "status" }));
    });

    socket.addEventListener("message", (message) => {
      try {
        handleEvent(JSON.parse(message.data));
      } catch (error) {
        console.error("Invalid Project Akira avatar event", error);
      }
    });

    socket.addEventListener("close", () => {
      if (state.socket !== socket) return;
      setConnection(false, "Reconnecting");
      setStage("offline");
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = window.setTimeout(connect, state.reconnectDelay);
      state.reconnectDelay = Math.min(state.reconnectDelay * 1.7, 10000);
    });

    socket.addEventListener("error", () => socket.close());
  }

  window.addEventListener("beforeunload", () => {
    if (state.socket) state.socket.close();
  });

  connect();
})();
