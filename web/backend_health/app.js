(() => {
  "use strict";

  const HEALTH_ENDPOINT = "/api/models/health";
  const REFRESH_INTERVAL_MS = 30_000;
  const STATUS_LABELS = {
    healthy: "Ready",
    degraded: "Needs attention",
    offline: "Offline",
    idle: "Idle",
    misconfigured: "Misconfigured",
    checking: "Checking",
    stale: "Not checked",
  };

  let requestGeneration = 0;
  let refreshTimer = null;
  let elements = null;

  function findBackend() {
    const selected = document.querySelector(".backend-option.selected[data-backend]");
    if (selected) {
      return selected.dataset.backend || "";
    }
    const checked = document.querySelector("[name='backend']:checked");
    return checked ? checked.value : "";
  }

  function valueOf(selector) {
    const element = document.querySelector(selector);
    return element && "value" in element ? String(element.value || "") : "";
  }

  function currentPayload() {
    return {
      backend: findBackend(),
      base_url: valueOf("#baseUrlInput"),
      api_key: valueOf("#apiKeyInput"),
      model: valueOf("#modelInput"),
    };
  }

  function formatLatency(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "—";
    }
    if (number < 10) {
      return `${number.toFixed(1)} ms`;
    }
    return `${Math.round(number)} ms`;
  }

  function formatCheckedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "—";
    }
    return date.toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function modelReadiness(result) {
    if (result.model_loaded === true) {
      return "Loaded and ready";
    }
    if (result.model_available === true && result.model_loaded === false) {
      return "Available, not loaded";
    }
    if (result.model_available === true) {
      return "Listed by server";
    }
    if (result.model_available === false) {
      return "Configured model not found";
    }
    return result.model ? "Not verified" : "No model selected";
  }

  function processReadiness(result) {
    if (!result.managed) {
      return "External backend";
    }
    if (result.process_running) {
      return result.process_pid
        ? `Running · PID ${result.process_pid}`
        : "Running";
    }
    if (result.status === "idle") {
      return "Not started · lazy launch";
    }
    return "Not running";
  }

  function runtimeSummary(result) {
    if (!result.managed || !result.details) {
      return "";
    }
    const details = result.details;
    const parts = [];
    if (details.context_size !== undefined) {
      parts.push(`context ${details.context_size}`);
    }
    if (details.gpu_layers !== undefined) {
      parts.push(`GPU layers ${details.gpu_layers}`);
    }
    if (details.threads !== undefined) {
      parts.push(`threads ${details.threads}`);
    }
    if (details.parallel_slots !== undefined) {
      parts.push(`slots ${details.parallel_slots}`);
    }
    return parts.join(" · ");
  }

  function setStatus(status, message) {
    const normalized = STATUS_LABELS[status] ? status : "offline";
    elements.card.dataset.status = normalized;
    elements.badge.textContent = STATUS_LABELS[normalized];
    elements.message.textContent = message || "No health result is available.";
  }

  function markStale(message = "Settings changed. Check again to verify them.") {
    requestGeneration += 1;
    setStatus("stale", message);
    elements.button.disabled = false;
    elements.button.textContent = "Check now";
  }

  function render(result) {
    setStatus(result.status, result.message);
    elements.backend.textContent = result.display_name || result.backend || "—";
    elements.latency.textContent = formatLatency(result.latency_ms);
    elements.endpoint.textContent = result.endpoint || result.base_url || "—";
    elements.endpoint.title = result.endpoint || result.base_url || "";
    elements.model.textContent = modelReadiness(result);
    elements.process.textContent = processReadiness(result);
    elements.checked.textContent = formatCheckedAt(result.checked_at);

    const runtime = runtimeSummary(result);
    elements.runtime.hidden = !runtime;
    elements.runtime.textContent = runtime;

    const logFile = result.details && result.details.log_file;
    elements.log.hidden = !logFile;
    elements.log.textContent = logFile ? `Log: ${logFile}` : "";
    elements.log.title = logFile || "";
  }

  async function checkHealth() {
    const payload = currentPayload();
    if (!payload.backend) {
      markStale("Choose a backend before running a health check.");
      return;
    }

    const generation = ++requestGeneration;
    setStatus("checking", "Checking server and model readiness…");
    elements.button.disabled = true;
    elements.button.textContent = "Checking…";

    try {
      const response = await fetch(HEALTH_ENDPOINT, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof body.detail === "string"
          ? body.detail
          : `Health check failed with HTTP ${response.status}.`;
        throw new Error(detail);
      }
      if (generation !== requestGeneration) {
        return;
      }
      render(body);
    } catch (error) {
      if (generation !== requestGeneration) {
        return;
      }
      setStatus(
        "offline",
        error instanceof Error ? error.message : "Health check failed.",
      );
      elements.backend.textContent = payload.backend;
      elements.latency.textContent = "—";
      elements.checked.textContent = formatCheckedAt(new Date().toISOString());
    } finally {
      if (generation === requestGeneration) {
        elements.button.disabled = false;
        elements.button.textContent = "Check now";
      }
    }
  }

  function scheduleHealth(delay = 150) {
    window.setTimeout(() => {
      if (document.visibilityState === "visible") {
        checkHealth();
      }
    }, delay);
  }

  function createPanel() {
    const main = document.querySelector("main.models-layout") || document.querySelector("main");
    const contentGrid = document.querySelector(".content-grid");
    if (!main || document.querySelector("#backendHealthCard")) {
      return false;
    }

    const card = document.createElement("section");
    card.id = "backendHealthCard";
    card.className = "backend-health-card";
    card.dataset.status = "stale";
    card.innerHTML = `
      <div class="backend-health-heading">
        <div>
          <p class="backend-health-eyebrow">Live readiness</p>
          <h2>Backend health</h2>
          <p id="backendHealthMessage" class="backend-health-message">
            Checking the selected backend…
          </p>
        </div>
        <div class="backend-health-actions">
          <span id="backendHealthBadge" class="backend-health-badge">Not checked</span>
          <button id="backendHealthCheck" type="button" class="secondary-button">
            Check now
          </button>
        </div>
      </div>
      <div class="backend-health-grid" aria-live="polite">
        <div class="backend-health-metric">
          <span>Backend</span>
          <strong id="backendHealthBackend">—</strong>
        </div>
        <div class="backend-health-metric">
          <span>Response time</span>
          <strong id="backendHealthLatency">—</strong>
        </div>
        <div class="backend-health-metric">
          <span>Model readiness</span>
          <strong id="backendHealthModel">—</strong>
        </div>
        <div class="backend-health-metric">
          <span>Managed process</span>
          <strong id="backendHealthProcess">—</strong>
        </div>
        <div class="backend-health-metric backend-health-endpoint">
          <span>Checked endpoint</span>
          <strong id="backendHealthEndpoint">—</strong>
        </div>
        <div class="backend-health-metric">
          <span>Last checked</span>
          <strong id="backendHealthChecked">—</strong>
        </div>
      </div>
      <p id="backendHealthRuntime" class="backend-health-detail" hidden></p>
      <p id="backendHealthLog" class="backend-health-detail backend-health-log" hidden></p>
    `;

    if (contentGrid && contentGrid.parentElement === main) {
      main.insertBefore(card, contentGrid);
    } else {
      main.appendChild(card);
    }

    elements = {
      card,
      badge: card.querySelector("#backendHealthBadge"),
      message: card.querySelector("#backendHealthMessage"),
      button: card.querySelector("#backendHealthCheck"),
      backend: card.querySelector("#backendHealthBackend"),
      latency: card.querySelector("#backendHealthLatency"),
      model: card.querySelector("#backendHealthModel"),
      process: card.querySelector("#backendHealthProcess"),
      endpoint: card.querySelector("#backendHealthEndpoint"),
      checked: card.querySelector("#backendHealthChecked"),
      runtime: card.querySelector("#backendHealthRuntime"),
      log: card.querySelector("#backendHealthLog"),
    };
    return true;
  }

  function bindEvents() {
    elements.button.addEventListener("click", checkHealth);

    document.addEventListener(
      "click",
      (event) => {
        const target = event.target instanceof Element ? event.target : null;
        const option = target?.closest(".backend-option[data-backend]");
        if (!option) {
          return;
        }
        markStale("Backend changed. Verifying its saved connection…");
        scheduleHealth(200);
      },
      true,
    );

    ["#baseUrlInput", "#apiKeyInput", "#modelInput"].forEach((selector) => {
      const input = document.querySelector(selector);
      if (!input) {
        return;
      }
      input.addEventListener("input", () => markStale());
      input.addEventListener("change", () => markStale());
    });

    const testButton = document.querySelector("#testButton");
    if (testButton) {
      testButton.addEventListener("click", () => {
        markStale("Waiting for the connection test to finish…");
        scheduleHealth(1_000);
        scheduleHealth(2_500);
      });
    }

    const saveButton = document.querySelector("#saveButton");
    if (saveButton) {
      saveButton.addEventListener("click", () => scheduleHealth(600));
    }

    window.addEventListener("akira:model-config-changed", () => {
      markStale("Model configuration changed. Verifying the active backend…");
      scheduleHealth(250);
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        scheduleHealth(100);
      }
    });

    refreshTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        checkHealth();
      }
    }, REFRESH_INTERVAL_MS);
    window.addEventListener("beforeunload", () => {
      if (refreshTimer !== null) {
        window.clearInterval(refreshTimer);
      }
    });
  }

  function initialize() {
    if (!createPanel()) {
      return;
    }
    bindEvents();
    scheduleHealth(500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
