(() => {
  "use strict";

  const layout = document.querySelector(".models-layout");
  if (!layout) return;

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
  if (hero?.nextSibling) layout.insertBefore(card, hero.nextSibling);
  else layout.appendChild(card);

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
  let busy = false;

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
  });

  refresh();
})();
