import { EmbeddedVRMRenderer } from "/static/avatar/renderer.js";
import { TextExpressionPlayer } from "/static/avatar/expressions.js";
import { TextVisemePlayer } from "/static/avatar/visemes.js";

window.__akiraAvatarAppStarted = true;

const MAX_MODEL_BYTES = 100 * 1024 * 1024;

const elements = {
  connectionBadge: document.getElementById("connectionBadge"),
  connectionText: document.getElementById("connectionText"),
  stateLabel: document.getElementById("stateLabel"),
  stateDetail: document.getElementById("stateDetail"),
  modeText: document.getElementById("modeText"),
  rendererText: document.getElementById("rendererText"),
  modelText: document.getElementById("modelText"),
  modelInput: document.getElementById("modelInput"),
  removeModelButton: document.getElementById("removeModelButton"),
  modelNotice: document.getElementById("modelNotice"),
  modelNoticeText: document.getElementById("modelNoticeText"),
  rendererHost: document.getElementById("rendererHost"),
};

const state = {
  socket: null,
  reconnectTimer: null,
  reconnectDelay: 1000,
  listening: false,
  model: null,
  modelLoading: false,
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

let embeddedRenderer = null;
let expressionPlayer = null;
let visemePlayer = null;
try {
  embeddedRenderer = new EmbeddedVRMRenderer(elements.rendererHost);
  expressionPlayer = new TextExpressionPlayer(embeddedRenderer);
  visemePlayer = new TextVisemePlayer(embeddedRenderer);
} catch (error) {
  console.error("Embedded VRM renderer could not start", error);
  showModelNotice("WebGL is unavailable. The VMC backend can still be used.", true);
}

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

function showModelNotice(message, error = false) {
  elements.modelNotice.hidden = false;
  elements.modelNotice.classList.toggle("error", error);
  elements.modelNoticeText.textContent = message;
}

function hideModelNotice() {
  elements.modelNotice.hidden = true;
  elements.modelNotice.classList.remove("error");
  elements.modelNoticeText.textContent = "";
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 1) return "";
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

async function readJson(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || "Avatar request failed.");
  }
  return payload;
}

async function syncAvatarSettings() {
  if (!visemePlayer && !expressionPlayer && !embeddedRenderer) return;
  try {
    const payload = await readJson(await fetch("/api/settings", { cache: "no-store" }));
    const rootSettings = payload.settings || {};
    const settings = {
      ...(rootSettings.avatar || {}),
      tts_rate: rootSettings.tts && rootSettings.tts.rate,
    };
    if (embeddedRenderer) embeddedRenderer.configureIdle(settings);
    if (expressionPlayer) expressionPlayer.configure(settings);
    if (visemePlayer) visemePlayer.configure(settings);
  } catch (error) {
    console.warn("Avatar animation settings could not be loaded", error);
  }
}

async function loadConfiguredModel(model) {
  state.model = model;
  state.modelLoading = true;
  elements.removeModelButton.hidden = false;
  elements.rendererText.textContent = "Loading embedded VRM";
  elements.modelText.textContent = model.filename || "Saved avatar";
  showModelNotice("Loading avatar…");

  if (!embeddedRenderer) {
    state.modelLoading = false;
    elements.rendererText.textContent = "VMC only — WebGL unavailable";
    return;
  }

  try {
    const version = model.sha256 ? `?v=${encodeURIComponent(model.sha256)}` : "";
    const vrm = await embeddedRenderer.load(
      `${model.model_url}${version}`,
      (progress) => {
        showModelNotice(`Loading avatar… ${Math.round(progress * 100)}%`);
      },
    );
    if (!vrm) return;
    document.body.classList.add("model-loaded");
    document.body.classList.remove("model-error");
    const versionText = model.vrm_version ? `VRM ${model.vrm_version}` : "VRM";
    const sizeText = formatBytes(model.size_bytes);
    const capabilities = embeddedRenderer.getExpressionCapabilities();
    const hasFaceExpressions = capabilities.face.length > 0;
    const idleCapabilities = embeddedRenderer.getIdleCapabilities();
    const idleLabel = idleCapabilities.enabled ? " · idle" : "";
    elements.rendererText.textContent = hasFaceExpressions
      ? `Embedded VRM · natural visemes + expressions${idleLabel}`
      : `Embedded VRM · natural visemes${idleLabel} · no face presets`;
    elements.modelText.textContent = [model.filename, versionText, sizeText]
      .filter(Boolean)
      .join(" · ");
    console.info("Embedded VRM expression capabilities", capabilities);
    if (hasFaceExpressions) {
      hideModelNotice();
    } else {
      showModelNotice(
        "This VRM does not expose standard facial-expression presets. Mouth animation will still work.",
        true,
      );
    }
  } catch (error) {
    console.error("VRM model could not be rendered", error);
    document.body.classList.remove("model-loaded");
    document.body.classList.add("model-error");
    elements.rendererText.textContent = "Placeholder / optional VMC";
    showModelNotice(error.message || "The avatar could not be rendered.", true);
  } finally {
    state.modelLoading = false;
  }
}

function clearConfiguredModel() {
  if (expressionPlayer) expressionPlayer.cancel(true);
  if (visemePlayer) visemePlayer.stop();
  if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
  state.model = null;
  state.modelLoading = false;
  if (embeddedRenderer) embeddedRenderer.clear();
  document.body.classList.remove("model-loaded", "model-error");
  elements.removeModelButton.hidden = true;
  elements.rendererText.textContent = "Placeholder / optional VMC";
  elements.modelText.textContent = "No VRM selected";
  hideModelNotice();
}

async function syncModel() {
  try {
    const model = await readJson(await fetch("/api/avatar/model", { cache: "no-store" }));
    if (!model.configured) {
      clearConfiguredModel();
      return;
    }
    if (
      state.model &&
      state.model.sha256 === model.sha256 &&
      document.body.classList.contains("model-loaded")
    ) {
      return;
    }
    await loadConfiguredModel(model);
  } catch (error) {
    console.error("Avatar model status could not be loaded", error);
    showModelNotice(error.message || "Avatar model status is unavailable.", true);
  }
}

async function uploadModel(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".vrm")) {
    showModelNotice("Choose a .vrm avatar file.", true);
    return;
  }
  if (file.size > MAX_MODEL_BYTES) {
    showModelNotice("VRM files must be 100 MiB or smaller.", true);
    return;
  }

  elements.modelInput.disabled = true;
  showModelNotice("Saving avatar…");
  try {
    const response = await fetch("/api/avatar/model", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Akira-Filename": encodeURIComponent(file.name),
      },
      body: file,
    });
    const model = await readJson(response);
    await loadConfiguredModel(model);
  } catch (error) {
    showModelNotice(error.message || "The VRM avatar could not be saved.", true);
  } finally {
    elements.modelInput.disabled = false;
    elements.modelInput.value = "";
  }
}

async function removeModel() {
  elements.removeModelButton.disabled = true;
  try {
    await readJson(await fetch("/api/avatar/model", { method: "DELETE" }));
    clearConfiguredModel();
  } catch (error) {
    showModelNotice(error.message || "The VRM avatar could not be removed.", true);
  } finally {
    elements.removeModelButton.disabled = false;
  }
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
      if (embeddedRenderer) embeddedRenderer.setSpeaking(Boolean(data.will_speak));
      if (expressionPlayer) {
        expressionPlayer.start(
          data.expression || { preset: "soft", score: 0 },
          data.reply || "",
          Boolean(data.will_speak),
        );
      }
      if (visemePlayer) {
        if (data.will_speak) visemePlayer.start(data.reply || "");
        else visemePlayer.stop();
      }
      break;
    case "chat.completed":
      if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
      if (expressionPlayer) expressionPlayer.complete();
      if (visemePlayer) visemePlayer.stop();
      restoreIdle();
      break;
    case "voice.recording.cancelled":
    case "voice.transcription.empty":
      restoreIdle();
      break;
    case "chat.failed":
      if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
      if (expressionPlayer) expressionPlayer.cancel();
      if (visemePlayer) visemePlayer.stop();
      setStage("error", data.error || "Akira could not complete that reply.");
      break;
    case "personality.changed":
      if (data.name) elements.modeText.textContent = data.name;
      break;
    case "settings.updated":
      syncAvatarSettings();
      break;
    case "avatar.model.changed":
      syncModel();
      break;
    case "system.shutdown":
      if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
      if (expressionPlayer) expressionPlayer.cancel();
      if (visemePlayer) visemePlayer.stop();
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
    if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
    if (expressionPlayer) expressionPlayer.cancel();
    if (visemePlayer) visemePlayer.stop();
    if (state.socket !== socket) return;
    setConnection(false, "Reconnecting");
    setStage("offline");
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = window.setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.7, 10000);
  });

  socket.addEventListener("error", () => socket.close());
}

elements.modelInput.addEventListener("change", () => {
  uploadModel(elements.modelInput.files && elements.modelInput.files[0]);
});
elements.removeModelButton.addEventListener("click", removeModel);

window.addEventListener("beforeunload", () => {
  if (state.socket) state.socket.close();
  if (embeddedRenderer) embeddedRenderer.setSpeaking(false);
  if (expressionPlayer) expressionPlayer.cancel(true);
  if (visemePlayer) visemePlayer.stop();
  if (embeddedRenderer) embeddedRenderer.dispose();
});

syncAvatarSettings();
syncModel();
connect();
