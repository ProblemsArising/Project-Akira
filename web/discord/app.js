(() => {
  "use strict";

  const elements = {
    form: document.getElementById("discordForm"),
    enabled: document.getElementById("enabledInput"),
    token: document.getElementById("tokenInput"),
    allowedUsers: document.getElementById("allowedUsersInput"),
    start: document.getElementById("startButton"),
    stop: document.getElementById("stopButton"),
    refresh: document.getElementById("refreshButton"),
    save: document.getElementById("saveButton"),
    deleteToken: document.getElementById("deleteTokenButton"),
    health: document.getElementById("healthBadge"),
    gateway: document.getElementById("gatewayStatus"),
    tokenStatus: document.getElementById("tokenStatus"),
    latency: document.getElementById("latencyStatus"),
    uptime: document.getElementById("uptimeStatus"),
    reconnects: document.getElementById("reconnectStatus"),
    disconnects: document.getElementById("disconnectStatus"),
    notice: document.getElementById("notice"),
    noticeTitle: document.getElementById("noticeTitle"),
    noticeText: document.getElementById("noticeText"),
  };

  let initialLoad = true;
  let requestActive = false;

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

  function showNotice(title, text, type = "success") {
    elements.noticeTitle.textContent = title;
    elements.noticeText.textContent = text;
    elements.notice.className = `notice ${type}`;
    elements.notice.hidden = false;
  }

  function formatDuration(value) {
    if (value === null || value === undefined) return "—";
    let seconds = Math.max(0, Math.floor(value));
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    seconds %= 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  function renderStatus(status, { updateForm = false } = {}) {
    const health = String(status.health || status.adapter_state || "stopped");
    elements.health.textContent = health.replaceAll("_", " ");
    elements.health.className = `health-badge ${health}`;
    elements.gateway.textContent = status.ready ? "Ready" : status.connected ? "Connected" : "Disconnected";
    elements.tokenStatus.textContent = status.token_configured ? "Configured" : "Not configured";
    elements.latency.textContent = status.latency_ms === null ? "—" : `${Math.round(status.latency_ms)} ms`;
    elements.uptime.textContent = formatDuration(status.uptime_seconds);
    elements.reconnects.textContent = String(status.reconnect_count || 0);
    elements.disconnects.textContent = String(status.disconnect_count || 0);
    elements.start.disabled = requestActive || status.running;
    elements.stop.disabled = requestActive || !status.running;
    elements.deleteToken.disabled = requestActive || !status.token_configured;

    if (updateForm) {
      elements.enabled.checked = Boolean(status.enabled);
      elements.allowedUsers.value = (status.allowed_user_ids || []).join("\n");
      elements.token.value = "";
    }
  }

  async function refreshStatus({ updateForm = false, quiet = false } = {}) {
    try {
      const status = await apiRequest("/api/discord/settings");
      renderStatus(status, { updateForm });
      initialLoad = false;
    } catch (error) {
      if (!quiet) showNotice("Could not load Discord settings", error.message, "error");
    }
  }

  function allowedUserIds() {
    return elements.allowedUsers.value
      .split(/[\s,]+/)
      .map((value) => value.trim())
      .filter(Boolean);
  }

  async function runAction(action, successTitle) {
    if (requestActive) return;
    requestActive = true;
    elements.start.disabled = true;
    elements.stop.disabled = true;
    try {
      const status = await action();
      renderStatus(status, { updateForm: true });
      showNotice(successTitle, "Discord remote messaging was updated.");
    } catch (error) {
      showNotice("Discord operation failed", error.message, "error");
    } finally {
      requestActive = false;
      await refreshStatus({ quiet: true });
    }
  }

  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    runAction(
      () => apiRequest("/api/discord/settings", {
        method: "PUT",
        body: JSON.stringify({
          enabled: elements.enabled.checked,
          allowed_user_ids: allowedUserIds(),
          bot_token: elements.token.value.trim() || null,
        }),
      }),
      "Discord settings saved",
    );
  });

  elements.start.addEventListener("click", () => {
    runAction(() => apiRequest("/api/discord/start", { method: "POST" }), "Discord started");
  });

  elements.stop.addEventListener("click", () => {
    runAction(() => apiRequest("/api/discord/stop", { method: "POST" }), "Discord stopped");
  });

  elements.refresh.addEventListener("click", () => refreshStatus({ quiet: false }));

  elements.deleteToken.addEventListener("click", () => {
    if (!window.confirm("Remove the saved Discord bot token and stop remote messaging?")) return;
    runAction(() => apiRequest("/api/discord/token", { method: "DELETE" }), "Discord token removed");
  });

  refreshStatus({ updateForm: true });
  window.setInterval(() => {
    if (!initialLoad && document.visibilityState === "visible" && !requestActive) {
      refreshStatus({ quiet: true });
    }
  }, 3000);
})();
