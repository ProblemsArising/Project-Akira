(() => {
  "use strict";

  const elements = {
    connectionBadge: document.getElementById("connectionBadge"),
    connectionText: document.getElementById("connectionText"),
    calibrationStatus: document.getElementById("calibrationStatus"),
    phaseBadge: document.getElementById("phaseBadge"),
    meterFill: document.getElementById("meterFill"),
    startMarker: document.getElementById("startMarker"),
    stopMarker: document.getElementById("stopMarker"),
    levelValue: document.getElementById("levelValue"),
    dbValue: document.getElementById("dbValue"),
    progressValue: document.getElementById("progressValue"),
    inputDevice: document.getElementById("inputDevice"),
    durationSeconds: document.getElementById("durationSeconds"),
    startButton: document.getElementById("startButton"),
    startMultiplier: document.getElementById("startMultiplier"),
    endMultiplier: document.getElementById("endMultiplier"),
    minStartThreshold: document.getElementById("minStartThreshold"),
    minEndThreshold: document.getElementById("minEndThreshold"),
    endSilence: document.getElementById("endSilence"),
    calibrationSeconds: document.getElementById("calibrationSeconds"),
    recommendedButton: document.getElementById("recommendedButton"),
    saveButton: document.getElementById("saveButton"),
    saveState: document.getElementById("saveState"),
    emptyResults: document.getElementById("emptyResults"),
    results: document.getElementById("results"),
    noiseFloor: document.getElementById("noiseFloor"),
    speechPeak: document.getElementById("speechPeak"),
    suggestedStart: document.getElementById("suggestedStart"),
    suggestedEnd: document.getElementById("suggestedEnd"),
    samplePlayer: document.getElementById("samplePlayer"),
    stepQuiet: document.getElementById("stepQuiet"),
    stepSpeak: document.getElementById("stepSpeak"),
    stepReview: document.getElementById("stepReview"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("noticeTitle"),
    noticeText: document.getElementById("noticeText"),
    dismissNotice: document.getElementById("dismissNotice"),
  };

  const state = {
    socket: null,
    settings: null,
    result: null,
    running: false,
    dirty: false,
    meterMaximum: 0.12,
  };

  function setConnection(online, text) {
    elements.connectionBadge.classList.toggle("online", online);
    elements.connectionBadge.classList.toggle("offline", !online);
    elements.connectionText.textContent = text;
  }

  function showNotice(title, text) {
    elements.noticeTitle.textContent = title;
    elements.noticeText.textContent = text;
    elements.notice.hidden = false;
  }

  function clearNotice() {
    elements.notice.hidden = true;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`);
    return payload;
  }

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function setSteps(active) {
    for (const [name, node] of Object.entries({ quiet: elements.stepQuiet, speak: elements.stepSpeak, review: elements.stepReview })) {
      node.classList.toggle("active", name === active);
    }
  }

  function setPhase(phase) {
    elements.phaseBadge.className = `phase-badge ${phase || ""}`;
    if (phase === "noise") {
      elements.phaseBadge.textContent = "Room noise";
      elements.calibrationStatus.textContent = "Stay quiet for a moment";
      setSteps("quiet");
    } else if (phase === "speech") {
      elements.phaseBadge.textContent = "Speech sample";
      elements.calibrationStatus.textContent = "Speak normally until the test ends";
      setSteps("speak");
    } else {
      elements.phaseBadge.textContent = "Idle";
    }
  }

  function updateMarkers(startValue, stopValue) {
    const max = Math.max(state.meterMaximum, startValue * 1.35, 0.04);
    elements.startMarker.style.left = `${Math.min(100, (startValue / max) * 100)}%`;
    elements.stopMarker.style.left = `${Math.min(100, (stopValue / max) * 100)}%`;
  }

  function updateMeter(data) {
    const rms = number(data.rms);
    const peak = number(data.peak);
    state.meterMaximum = Math.max(0.04, state.meterMaximum * 0.995, peak * 1.15);
    elements.meterFill.style.width = `${Math.min(100, (rms / state.meterMaximum) * 100)}%`;
    elements.levelValue.textContent = rms.toFixed(4);
    elements.dbValue.textContent = `${number(data.dbfs, -160).toFixed(1)} dBFS`;
    elements.progressValue.textContent = `${Math.round(number(data.progress) * 100)}%`;
    setPhase(data.phase);
  }

  function currentThresholds() {
    const noise = state.result?.noise_floor || 0;
    return {
      start: Math.max(noise * number(elements.startMultiplier.value, 6), number(elements.minStartThreshold.value, .02)),
      end: Math.max(noise * number(elements.endMultiplier.value, 1.8), number(elements.minEndThreshold.value, .006)),
    };
  }

  function refreshMarkers() {
    const thresholds = currentThresholds();
    updateMarkers(thresholds.start, thresholds.end);
  }

  function markDirty() {
    state.dirty = true;
    elements.saveState.textContent = "Unsaved changes";
    refreshMarkers();
  }

  async function loadPage() {
    try {
      const [settingsPayload, devicePayload] = await Promise.all([
        api("/api/settings"),
        api("/api/audio/devices"),
      ]);
      state.settings = settingsPayload.settings;
      const audio = state.settings.audio;
      elements.startMultiplier.value = audio.start_threshold_multiplier;
      elements.endMultiplier.value = audio.end_threshold_multiplier;
      elements.minStartThreshold.value = audio.min_start_threshold;
      elements.minEndThreshold.value = audio.min_end_threshold;
      elements.endSilence.value = audio.end_silence_seconds;
      elements.calibrationSeconds.value = Math.max(.6, audio.calibration_seconds);

      for (const device of devicePayload.devices) {
        const option = document.createElement("option");
        option.value = device.selection_key;
        option.textContent = `${device.name} · ${device.host_api}`;
        elements.inputDevice.appendChild(option);
      }
      elements.inputDevice.value = devicePayload.configured_input || "";
      refreshMarkers();
      elements.saveState.textContent = "No unsaved changes";
    } catch (error) {
      showNotice("Unable to load audio settings", error.message);
    }
  }

  function connect() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/api/audio/calibration`);
    state.socket = socket;
    setConnection(false, "Connecting");

    socket.addEventListener("open", () => setConnection(true, "Connected"));
    socket.addEventListener("close", () => {
      setConnection(false, "Disconnected");
      state.running = false;
      elements.startButton.disabled = false;
      setTimeout(connect, 1500);
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const data = message.data || {};
      if (message.type === "calibration.ready") {
        setConnection(true, "Connected");
      } else if (message.type === "calibration.started") {
        state.running = true;
        elements.startButton.disabled = true;
        elements.startButton.textContent = "Testing…";
        elements.results.hidden = true;
        elements.emptyResults.hidden = false;
        clearNotice();
        setPhase("noise");
      } else if (message.type === "calibration.level") {
        updateMeter(data);
      } else if (message.type === "calibration.completed") {
        finishCalibration(data);
      } else if (message.type === "calibration.error") {
        state.running = false;
        elements.startButton.disabled = false;
        elements.startButton.textContent = "Start calibration";
        elements.calibrationStatus.textContent = "Calibration failed";
        showNotice("Audio calibration failed", data.error || "Unknown error");
      }
    });
  }

  function finishCalibration(data) {
    state.running = false;
    state.result = data;
    elements.startButton.disabled = false;
    elements.startButton.textContent = "Run again";
    elements.calibrationStatus.textContent = "Calibration complete";
    elements.phaseBadge.className = "phase-badge speech";
    elements.phaseBadge.textContent = "Complete";
    setSteps("review");

    elements.emptyResults.hidden = true;
    elements.results.hidden = false;
    elements.noiseFloor.textContent = number(data.noise_floor).toFixed(4);
    elements.speechPeak.textContent = number(data.peak_level).toFixed(4);
    elements.suggestedStart.textContent = number(data.suggested_min_start_threshold).toFixed(4);
    elements.suggestedEnd.textContent = number(data.suggested_min_end_threshold).toFixed(4);
    elements.samplePlayer.src = data.sample_url;
    elements.samplePlayer.load();
    elements.recommendedButton.disabled = false;
    updateMarkers(number(data.current_start_threshold), number(data.current_end_threshold));
  }

  function startCalibration() {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      showNotice("Not connected", "Wait for the calibration service to reconnect.");
      return;
    }
    state.socket.send(JSON.stringify({
      type: "start",
      input_device: elements.inputDevice.value || null,
      duration_seconds: number(elements.durationSeconds.value, 7),
      calibration_seconds: number(elements.calibrationSeconds.value, 1.5),
    }));
  }

  function useRecommended() {
    if (!state.result) return;
    elements.minStartThreshold.value = number(state.result.suggested_min_start_threshold).toFixed(4);
    elements.minEndThreshold.value = number(state.result.suggested_min_end_threshold).toFixed(4);
    markDirty();
  }

  async function saveSettings() {
    clearNotice();
    elements.saveButton.disabled = true;
    try {
      const payload = await api("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({ changes: { audio: {
          input_device: elements.inputDevice.value || null,
          start_threshold_multiplier: number(elements.startMultiplier.value),
          end_threshold_multiplier: number(elements.endMultiplier.value),
          min_start_threshold: number(elements.minStartThreshold.value),
          min_end_threshold: number(elements.minEndThreshold.value),
          end_silence_seconds: number(elements.endSilence.value),
          calibration_seconds: number(elements.calibrationSeconds.value),
        } } }),
      });
      state.settings = payload.settings;
      state.dirty = false;
      elements.saveState.textContent = payload.restart_required ? "Saved · restart Akira to apply" : "Saved";
      if (payload.restart_required) showNotice("Restart recommended", "Akira is already loaded. Restart the server before testing the new runtime values.");
    } catch (error) {
      showNotice("Could not save audio settings", error.message);
      elements.saveState.textContent = "Save failed";
    } finally {
      elements.saveButton.disabled = false;
    }
  }

  for (const input of [elements.inputDevice, elements.startMultiplier, elements.endMultiplier, elements.minStartThreshold, elements.minEndThreshold, elements.endSilence, elements.calibrationSeconds]) {
    input.addEventListener("input", markDirty);
    input.addEventListener("change", markDirty);
  }
  elements.startButton.addEventListener("click", startCalibration);
  elements.recommendedButton.addEventListener("click", useRecommended);
  elements.saveButton.addEventListener("click", saveSettings);
  elements.dismissNotice.addEventListener("click", clearNotice);
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) { event.preventDefault(); event.returnValue = ""; }
    if (state.socket) state.socket.close();
  });

  loadPage();
  connect();
})();
