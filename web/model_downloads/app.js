(() => {
  "use strict";

  const layout = document.querySelector(".models-layout");
  if (!layout) return;

  const runtimeCard = document.createElement("section");
  runtimeCard.className = "download-manager-card runtime-manager-card";
  runtimeCard.innerHTML = `
    <div class="download-heading runtime-heading">
      <div>
        <p class="eyebrow">Managed llama.cpp</p>
        <h3>Runtime installer</h3>
        <p>Install a pinned official llama.cpp build and its required acceleration DLLs. Project Akira verifies every archive, tests <code>llama-server.exe --list-devices</code>, and selects the working executable automatically.</p>
      </div>
      <span id="runtimeStatusBadge" class="runtime-status-badge">Checking…</span>
    </div>

    <div class="runtime-controls">
      <label class="download-field runtime-variant-field">
        <span>Runtime variant</span>
        <select id="runtimeVariantSelect" aria-label="llama.cpp runtime variant"></select>
      </label>
      <div class="runtime-actions">
        <button id="runtimeInstallButton" class="button primary" type="button">Install recommended runtime</button>
        <button id="runtimeCancelButton" class="button secondary" type="button" hidden>Cancel</button>
        <button id="runtimeRemoveButton" class="button secondary danger" type="button" hidden>Remove managed runtime</button>
      </div>
    </div>

    <div id="runtimeProgress" class="runtime-progress" hidden>
      <div class="download-item-head">
        <div>
          <strong id="runtimeProgressTitle">Preparing runtime…</strong>
          <small id="runtimeProgressAsset"></small>
        </div>
        <span id="runtimeProgressStatus" class="download-status"></span>
      </div>
      <div class="download-progress" aria-label="Runtime download progress"><span></span></div>
      <span id="runtimeProgressBytes" class="download-size"></span>
    </div>

    <div class="runtime-details">
      <div><span>Tested release</span><strong id="runtimeVersion">—</strong></div>
      <div><span>Installed variant</span><strong id="runtimeInstalledVariant">Not installed</strong></div>
      <div class="runtime-path-row"><span>Executable</span><code id="runtimeExecutable">Not configured</code></div>
      <div class="runtime-path-row"><span>Detected devices</span><code id="runtimeDevices">—</code></div>
    </div>
    <p class="runtime-help">A manually selected executable still takes priority. You can keep using a custom build through Settings.</p>
    <div id="runtimeNotice" class="download-notice" hidden></div>
  `;

  const card = document.createElement("section");
  card.className = "download-manager-card";
  card.innerHTML = `
    <div class="download-heading">
      <div>
        <p class="eyebrow">Managed llama.cpp</p>
        <h3>Download GGUF models</h3>
        <p>Paste a direct HTTP or HTTPS link. Downloads resume from a kept <code>.part</code> file when the server supports byte ranges.</p>
      </div>
      <span id="downloadDirectory" class="download-directory"></span>
    </div>

    <form id="modelDownloadForm" class="download-form">
      <label class="download-field download-url-field">
        <span>Direct GGUF URL</span>
        <input id="modelDownloadUrl" type="url" required spellcheck="false" autocomplete="off" placeholder="https://example.com/model.Q4_K_M.gguf">
      </label>
      <label class="download-field">
        <span>Filename <em>optional</em></span>
        <input id="modelDownloadFilename" type="text" spellcheck="false" autocomplete="off" placeholder="Taken from URL">
      </label>
      <label class="download-field download-hash-field">
        <span>SHA-256 <em>optional</em></span>
        <input id="modelDownloadHash" type="text" spellcheck="false" autocomplete="off" maxlength="64" placeholder="64 hexadecimal characters">
      </label>
      <button id="modelDownloadButton" class="button primary" type="submit">Download model</button>
    </form>

    <div id="downloadNotice" class="download-notice" hidden></div>

    <div class="download-columns">
      <div>
        <div class="download-subheading">
          <h4>Downloads</h4>
          <span id="downloadJobCount">0</span>
        </div>
        <div id="downloadJobs" class="download-list">
          <p class="download-empty">No downloads started.</p>
        </div>
      </div>
      <div>
        <div class="download-subheading">
          <h4>Downloaded locally</h4>
          <span id="downloadModelCount">0</span>
        </div>
        <div id="localModels" class="download-list">
          <p class="download-empty">No managed GGUF models found.</p>
        </div>
      </div>
    </div>
  `;

  const hero = layout.querySelector(".hero-card");
  if (hero?.nextSibling) layout.insertBefore(runtimeCard, hero.nextSibling);
  else layout.appendChild(runtimeCard);
  if (runtimeCard.nextSibling) layout.insertBefore(card, runtimeCard.nextSibling);
  else layout.appendChild(card);

  const runtimeElements = {
    status: runtimeCard.querySelector("#runtimeStatusBadge"),
    variant: runtimeCard.querySelector("#runtimeVariantSelect"),
    install: runtimeCard.querySelector("#runtimeInstallButton"),
    cancel: runtimeCard.querySelector("#runtimeCancelButton"),
    remove: runtimeCard.querySelector("#runtimeRemoveButton"),
    progress: runtimeCard.querySelector("#runtimeProgress"),
    progressTitle: runtimeCard.querySelector("#runtimeProgressTitle"),
    progressAsset: runtimeCard.querySelector("#runtimeProgressAsset"),
    progressStatus: runtimeCard.querySelector("#runtimeProgressStatus"),
    progressBar: runtimeCard.querySelector("#runtimeProgress .download-progress"),
    progressFill: runtimeCard.querySelector("#runtimeProgress .download-progress span"),
    progressBytes: runtimeCard.querySelector("#runtimeProgressBytes"),
    version: runtimeCard.querySelector("#runtimeVersion"),
    installedVariant: runtimeCard.querySelector("#runtimeInstalledVariant"),
    executable: runtimeCard.querySelector("#runtimeExecutable"),
    devices: runtimeCard.querySelector("#runtimeDevices"),
    notice: runtimeCard.querySelector("#runtimeNotice"),
  };

  const elements = {
    form: card.querySelector("#modelDownloadForm"),
    url: card.querySelector("#modelDownloadUrl"),
    filename: card.querySelector("#modelDownloadFilename"),
    sha256: card.querySelector("#modelDownloadHash"),
    button: card.querySelector("#modelDownloadButton"),
    notice: card.querySelector("#downloadNotice"),
    directory: card.querySelector("#downloadDirectory"),
    jobs: card.querySelector("#downloadJobs"),
    models: card.querySelector("#localModels"),
    jobCount: card.querySelector("#downloadJobCount"),
    modelCount: card.querySelector("#downloadModelCount"),
  };

  let pollTimer = null;
  let runtimePollTimer = null;
  let busy = false;
  let runtimeBusy = false;
  let latestRuntimeSnapshot = null;
  let lastRuntimeJobState = null;

  function formatBytes(value) {
    if (!Number.isFinite(value) || value < 0) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = value;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
  }

  function showNotice(message, kind = "info") {
    elements.notice.textContent = message;
    elements.notice.className = `download-notice ${kind}`;
    elements.notice.hidden = false;
  }

  function hideNotice() {
    elements.notice.hidden = true;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof payload?.detail === "string"
        ? payload.detail
        : `Request failed (${response.status})`;
      throw new Error(detail);
    }
    return payload;
  }

  function runtimeOperationActive(job) {
    return Boolean(job && [
      "queued",
      "downloading",
      "verifying",
      "extracting",
      "validating",
      "activating",
    ].includes(job.status));
  }

  function runtimeVariantLabel(snapshot, variantId) {
    return snapshot.variants?.find((item) => item.id === variantId)?.name || variantId || "—";
  }

  function showRuntimeNotice(message, kind = "info") {
    runtimeElements.notice.textContent = message;
    runtimeElements.notice.className = `download-notice ${kind}`;
    runtimeElements.notice.hidden = false;
  }

  function renderRuntime(snapshot) {
    latestRuntimeSnapshot = snapshot;
    const previous = runtimeElements.variant.value;
    runtimeElements.variant.replaceChildren();
    for (const variant of snapshot.variants || []) {
      const option = document.createElement("option");
      option.value = variant.id;
      option.textContent = `${variant.name}${variant.recommended ? " — recommended" : ""}`;
      option.title = variant.description;
      runtimeElements.variant.appendChild(option);
    }
    const preferred = previous && [...runtimeElements.variant.options].some((item) => item.value === previous)
      ? previous
      : snapshot.installed_variant || snapshot.recommended_variant;
    runtimeElements.variant.value = preferred;

    const job = snapshot.job;
    const active = runtimeOperationActive(job);
    const installed = Boolean(snapshot.installed);
    runtimeElements.version.textContent = snapshot.version || "—";
    runtimeElements.installedVariant.textContent = installed
      ? runtimeVariantLabel(snapshot, snapshot.installed_variant)
      : "Not installed";
    runtimeElements.executable.textContent = snapshot.executable || "Not configured";
    runtimeElements.executable.title = snapshot.executable || "";
    runtimeElements.devices.textContent = snapshot.devices?.length
      ? snapshot.devices.join(" · ")
      : installed ? "No device output reported" : "—";
    runtimeElements.devices.title = snapshot.devices?.join("\n") || "";

    runtimeElements.status.textContent = !snapshot.supported
      ? "Unsupported platform"
      : active
        ? job.status
        : installed
          ? "Installed"
          : "Not installed";
    runtimeElements.status.className = `runtime-status-badge${installed && !active ? " installed" : ""}${job?.status === "failed" ? " failed" : ""}`;

    runtimeElements.install.disabled = runtimeBusy || active || !snapshot.supported;
    runtimeElements.variant.disabled = runtimeBusy || active || !snapshot.supported;
    runtimeElements.cancel.hidden = !active;
    runtimeElements.cancel.disabled = runtimeBusy;
    runtimeElements.remove.hidden = !installed || active;
    runtimeElements.remove.disabled = runtimeBusy;
    runtimeElements.install.textContent = installed
      ? "Repair or replace runtime"
      : "Install selected runtime";

    runtimeElements.progress.hidden = !job;
    if (job) {
      const percent = Number.isFinite(job.progress) ? Math.round(job.progress * 100) : null;
      runtimeElements.progressTitle.textContent = job.status === "installed"
        ? "Runtime installed and selected"
        : job.status === "failed"
          ? "Runtime installation failed"
          : `Installing ${runtimeVariantLabel(snapshot, job.variant)}`;
      runtimeElements.progressAsset.textContent = job.current_asset || "Preparing files";
      runtimeElements.progressStatus.textContent = job.status;
      runtimeElements.progressFill.style.width = `${percent ?? 0}%`;
      runtimeElements.progressBar.classList.toggle("indeterminate", percent === null && active);
      runtimeElements.progressBytes.textContent = progressText(job);
      if (job.error) showRuntimeNotice(job.error, "error");
      const nextState = `${job.id}:${job.status}`;
      if (lastRuntimeJobState !== nextState) {
        if (job.status === "installed") {
          showRuntimeNotice("llama.cpp was validated and selected as the managed executable.", "success");
          window.dispatchEvent(new CustomEvent("akira:model-config-changed", { detail: { runtime: snapshot } }));
        } else if (job.status === "cancelled") {
          showRuntimeNotice("Runtime installation cancelled. Valid partial archives were kept for resume.");
        }
        lastRuntimeJobState = nextState;
      }
    }
  }

  function scheduleRuntimeRefresh(snapshot) {
    if (runtimePollTimer) window.clearTimeout(runtimePollTimer);
    runtimePollTimer = window.setTimeout(refreshRuntime, runtimeOperationActive(snapshot.job) ? 750 : 5000);
  }

  async function refreshRuntime() {
    try {
      const snapshot = await request("/api/llama-cpp/runtime");
      renderRuntime(snapshot);
      scheduleRuntimeRefresh(snapshot);
    } catch (error) {
      showRuntimeNotice(`Could not load llama.cpp runtime status: ${error.message}`, "error");
      runtimePollTimer = window.setTimeout(refreshRuntime, 5000);
    }
  }

  function progressText(job) {
    const downloaded = formatBytes(job.downloaded_bytes);
    const total = job.total_bytes ? formatBytes(job.total_bytes) : null;
    return total ? `${downloaded} / ${total}` : downloaded;
  }

  function renderJobs(jobs) {
    elements.jobCount.textContent = String(jobs.length);
    elements.jobs.innerHTML = "";
    if (!jobs.length) {
      elements.jobs.innerHTML = '<p class="download-empty">No downloads started.</p>';
      return;
    }

    for (const job of [...jobs].reverse()) {
      const item = document.createElement("article");
      item.className = `download-item job-${job.status}`;
      const percent = Number.isFinite(job.progress) ? Math.round(job.progress * 100) : null;
      item.innerHTML = `
        <div class="download-item-head">
          <div>
            <strong></strong>
            <small></small>
          </div>
          <span class="download-status"></span>
        </div>
        <div class="download-progress" aria-label="Download progress"><span></span></div>
        <div class="download-item-foot">
          <span class="download-size"></span>
          <button class="button secondary download-cancel" type="button">Cancel</button>
        </div>
        <p class="download-error" hidden></p>
      `;
      item.querySelector("strong").textContent = job.filename;
      item.querySelector("small").textContent = job.url;
      item.querySelector(".download-status").textContent = job.status;
      item.querySelector(".download-size").textContent = progressText(job);
      item.querySelector(".download-progress span").style.width = `${percent ?? 0}%`;
      item.querySelector(".download-progress").classList.toggle("indeterminate", percent === null && job.status === "downloading");

      const cancel = item.querySelector(".download-cancel");
      const cancellable = job.status === "queued" || job.status === "downloading";
      cancel.hidden = !cancellable;
      cancel.addEventListener("click", async () => {
        cancel.disabled = true;
        try {
          await request(`/api/models/downloads/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
          await refresh();
        } catch (error) {
          showNotice(error.message, "error");
          cancel.disabled = false;
        }
      });

      if (job.error) {
        const error = item.querySelector(".download-error");
        error.textContent = job.error;
        error.hidden = false;
      }
      elements.jobs.appendChild(item);
    }
  }

  function renderModels(models) {
    elements.modelCount.textContent = String(models.length);
    elements.models.innerHTML = "";
    if (!models.length) {
      elements.models.innerHTML = '<p class="download-empty">No managed GGUF models found.</p>';
      return;
    }

    for (const model of models) {
      const item = document.createElement("article");
      item.className = `download-item local-model${model.active ? " active" : ""}`;
      item.innerHTML = `
        <div class="download-item-head">
          <div>
            <strong></strong>
            <small></small>
          </div>
          <span class="download-status"></span>
        </div>
        <div class="download-item-foot">
          <span class="download-size"></span>
          <div class="download-actions">
            <button class="button primary use-model" type="button">Use model</button>
            <button class="button secondary delete-model" type="button">Delete</button>
          </div>
        </div>
      `;
      item.querySelector("strong").textContent = model.filename;
      item.querySelector("small").textContent = model.path;
      item.querySelector(".download-size").textContent = formatBytes(model.size_bytes);
      const status = item.querySelector(".download-status");
      status.textContent = model.active ? "Active" : "Ready";

      const useButton = item.querySelector(".use-model");
      useButton.disabled = model.active;
      useButton.textContent = model.active ? "In use" : "Use model";
      useButton.addEventListener("click", async () => {
        useButton.disabled = true;
        try {
          const result = await request("/api/models/downloads/select", {
            method: "POST",
            body: JSON.stringify({ filename: model.filename }),
          });
          const activeLabel = document.getElementById("activeModelLabel");
          if (activeLabel) activeLabel.textContent = result.model_alias;
          window.dispatchEvent(new CustomEvent("akira:model-config-changed", {
            detail: {
              backend: result.backend,
              model: result.model_alias,
              model_path: result.model_path,
            },
          }));
          showNotice(`${model.filename} will start with managed llama.cpp on the next message.`, "success");
          await refresh();
        } catch (error) {
          showNotice(error.message, "error");
          useButton.disabled = false;
        }
      });

      const deleteButton = item.querySelector(".delete-model");
      deleteButton.disabled = model.active;
      deleteButton.addEventListener("click", async () => {
        if (!window.confirm(`Delete ${model.filename}?`)) return;
        deleteButton.disabled = true;
        try {
          await request(`/api/models/downloads/local/${encodeURIComponent(model.filename)}`, { method: "DELETE" });
          showNotice(`${model.filename} was deleted.`, "success");
          await refresh();
        } catch (error) {
          showNotice(error.message, "error");
          deleteButton.disabled = false;
        }
      });

      elements.models.appendChild(item);
    }
  }

  function scheduleRefresh(jobs) {
    if (pollTimer) window.clearTimeout(pollTimer);
    const active = jobs.some((job) => job.status === "queued" || job.status === "downloading");
    pollTimer = window.setTimeout(refresh, active ? 750 : 5000);
  }

  async function refresh() {
    try {
      const snapshot = await request("/api/models/downloads");
      elements.directory.textContent = snapshot.directory;
      elements.directory.title = snapshot.directory;
      renderJobs(snapshot.jobs || []);
      renderModels(snapshot.models || []);
      scheduleRefresh(snapshot.jobs || []);
    } catch (error) {
      showNotice(`Could not load managed downloads: ${error.message}`, "error");
      pollTimer = window.setTimeout(refresh, 5000);
    }
  }

  runtimeElements.install.addEventListener("click", async () => {
    if (runtimeBusy) return;
    runtimeBusy = true;
    runtimeElements.install.disabled = true;
    runtimeElements.notice.hidden = true;
    try {
      await request("/api/llama-cpp/runtime/install", {
        method: "POST",
        body: JSON.stringify({ variant: runtimeElements.variant.value || "recommended" }),
      });
      showRuntimeNotice("Runtime download started. Project Akira will verify and test it before activation.", "success");
      await refreshRuntime();
    } catch (error) {
      showRuntimeNotice(error.message, "error");
    } finally {
      runtimeBusy = false;
      if (latestRuntimeSnapshot) renderRuntime(latestRuntimeSnapshot);
    }
  });

  runtimeElements.cancel.addEventListener("click", async () => {
    if (runtimeBusy) return;
    runtimeBusy = true;
    runtimeElements.cancel.disabled = true;
    try {
      const snapshot = await request("/api/llama-cpp/runtime/cancel", { method: "POST" });
      renderRuntime(snapshot);
    } catch (error) {
      showRuntimeNotice(error.message, "error");
    } finally {
      runtimeBusy = false;
      if (latestRuntimeSnapshot) renderRuntime(latestRuntimeSnapshot);
    }
  });

  runtimeElements.remove.addEventListener("click", async () => {
    if (runtimeBusy || !window.confirm("Remove the Project Akira-managed llama.cpp runtime? Manually selected runtimes are not affected.")) return;
    runtimeBusy = true;
    runtimeElements.remove.disabled = true;
    try {
      const snapshot = await request("/api/llama-cpp/runtime", { method: "DELETE" });
      renderRuntime(snapshot);
      showRuntimeNotice("Managed llama.cpp runtime removed. Your GGUF models were kept.", "success");
      window.dispatchEvent(new CustomEvent("akira:model-config-changed", { detail: { runtime: snapshot } }));
    } catch (error) {
      showRuntimeNotice(error.message, "error");
    } finally {
      runtimeBusy = false;
      if (latestRuntimeSnapshot) renderRuntime(latestRuntimeSnapshot);
    }
  });

  elements.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    hideNotice();
    busy = true;
    elements.button.disabled = true;
    elements.button.textContent = "Starting…";
    try {
      await request("/api/models/downloads", {
        method: "POST",
        body: JSON.stringify({
          url: elements.url.value.trim(),
          filename: elements.filename.value.trim(),
          sha256: elements.sha256.value.trim(),
        }),
      });
      showNotice("Download started. You can leave this page while Project Akira remains open.", "success");
      elements.filename.value = "";
      elements.sha256.value = "";
      await refresh();
    } catch (error) {
      showNotice(error.message, "error");
    } finally {
      busy = false;
      elements.button.disabled = false;
      elements.button.textContent = "Download model";
    }
  });

  window.addEventListener("beforeunload", () => {
    if (pollTimer) window.clearTimeout(pollTimer);
    if (runtimePollTimer) window.clearTimeout(runtimePollTimer);
  });

  refreshRuntime();
  refresh();
})();
