let uploadListenersInitialized = false;

// ================================
  // Upload: plain + drag & drop UI
  // ================================
  async function uploadFiles() {
      const input = document.getElementById("uploadFiles");
      const status = document.getElementById("uploadStatus");
      if (!input || !status) return;

      if (!input.files.length) {
          status.textContent = "❌ No files selected.";
          return;
      }

      const formData = new FormData();
      for (let f of input.files) {
          formData.append("files", f);
      }

      setStatus("uploadStatus", "⬆ Uploading…", "working", false);

      try {
          const session = encodeURIComponent(getActiveSession());
          const resp = await fetch(`/api/upload?session=${session}`, {
              method: "POST",
              body: formData,
          });
          const data = await resp.json();
          if (data.uploaded?.length) {
              setStatus(
                  "uploadStatus",
                  `✅ Uploaded ${data.uploaded.length} file(s).`,
                  "success"
              );
              loadUploadManager();
          } else {
              status.textContent = `⚠ No files uploaded (check logs).`;
          }
      } catch (err) {
          console.error(err);
          setStatus("uploadStatus", `❌ Upload failed: ${err.message}`, "error");
      }
  }

function updateReselectButtonVisibility(selectedFiles = []) {
  const reselectBtn = document.getElementById("reselectPendingUploadsBtn");

  if (!reselectBtn) return;

  const hasRealFiles = selectedFiles.length > 0;
  const hasGhosts = loadPendingUploadState().length > 0;

  if (hasGhosts && !hasRealFiles) {
    reselectBtn.classList.remove("hidden");
  } else {
    reselectBtn.classList.add("hidden");
  }
}


  function initUploadUI() {
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("uploadFiles");
  const preview = document.getElementById("uploadPreview");
  const uploadBtn = document.getElementById("uploadBtn");
  const progressWrapper = document.getElementById("uploadProgressWrapper");
  const progressBar = document.getElementById("uploadProgress");
  const statusEl = document.getElementById("uploadStatus");
  const cancelUploadBtn = document.getElementById("cancelUploadBtn");
  const reselectPendingUploadsBtn = document.getElementById("reselectPendingUploadsBtn");

  if (
    !dropZone ||
    !fileInput ||
    !preview ||
    !uploadBtn ||
    !progressWrapper ||
    !progressBar ||
    !statusEl
  ) {
    return;
  }

  let selectedFiles = [];

  function fileFingerprint(file) {
    return `${file.name}__${file.size}__${file.lastModified}`;
  }

  function mergeFiles(existing, incoming) {
    const map = new Map();

    existing.forEach(file => {
      map.set(fileFingerprint(file), file);
    });

    incoming.forEach(file => {
      map.set(fileFingerprint(file), file);
    });

    return Array.from(map.values());
  }

  function updatePreview() {
    preview.innerHTML = "";

    selectedFiles.forEach((file, idx) => {
      const wrapper = document.createElement("div");
      wrapper.className = "preview-item";

      const name = document.createElement("div");
      name.className = "preview-name";
      name.textContent = file.name;

      const removeBtn = document.createElement("button");
      removeBtn.className = "preview-remove";
      removeBtn.innerHTML = "✖";

      removeBtn.onclick = () => {
        selectedFiles.splice(idx, 1);
        savePendingUploadState(selectedFiles);
        updatePreview();
      };

      wrapper.appendChild(name);
      wrapper.appendChild(removeBtn);
      preview.appendChild(wrapper);
    });

    uploadBtn.disabled = selectedFiles.length === 0;

    if (!selectedFiles.length) {
      clearPendingUploadState();
    }

    updateReselectButtonVisibility(selectedFiles);
  }

  function addFiles(newFiles) {
    if (!newFiles?.length) return;

    selectedFiles = mergeFiles(selectedFiles, Array.from(newFiles));
    savePendingUploadState(selectedFiles);
    updatePreview();

    statusEl.textContent = `${selectedFiles.length} file(s) ready to upload.`;
  }

  function markPreviewUploaded() {
    preview.querySelectorAll(".preview-item").forEach((row) => {
      row.classList.add("uploaded");
      const x = row.querySelector(".preview-remove");
      if (x) {
        x.disabled = true;
        x.style.opacity = "0.4";
        x.style.cursor = "not-allowed";
      }
    });
  }

  function clearSelectedUploadsUI({ delayMs = 2200 } = {}) {
    setTimeout(() => {
      selectedFiles = [];
      preview.innerHTML = "";
      fileInput.value = "";
      uploadBtn.disabled = true;
      progressWrapper.classList.add("hidden");
      progressBar.style.width = "0%";
      clearPendingUploadState();
      updateReselectButtonVisibility(selectedFiles);
    }, delayMs);
  }

  // Restore pending UI after refresh
  const restoredPending = loadPendingUploadState(getActiveSession());

  if (restoredPending.length) {
    renderPendingUploadGhosts(restoredPending);
    setStatus(
      "uploadStatus",
      "⚠ Pending upload restored. Re-select the same file(s) and click Upload to retry.",
      "warning",
      false
    );
  } else {
    setStatus("uploadStatus", "", "info", false);
  }

  updateReselectButtonVisibility(selectedFiles);

  dropZone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", (e) => {
    addFiles(e.target.files);
    fileInput.value = "";
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    addFiles(e.dataTransfer.files);
  });

  reselectPendingUploadsBtn?.addEventListener("click", () => {
    fileInput.click();
  });

  cancelUploadBtn?.addEventListener("click", () => {
    if (CURRENT_UPLOAD_XHR) {
      CURRENT_UPLOAD_XHR.abort();
    }
  });

  uploadBtn.addEventListener("click", () => {
    if (!selectedFiles.length) {
      setStatus(
        "uploadStatus",
        "❗ Please select at least one video before uploading.",
        "error"
      );

      uploadBtn.classList.add("error-flash");
      setTimeout(() => uploadBtn.classList.remove("error-flash"), 400);
      return;
    }

    statusEl.textContent = "Uploading…";
    progressWrapper.classList.remove("hidden");
    progressBar.style.width = "0%";
    cancelUploadBtn?.classList.remove("hidden");

    const formData = new FormData();
    selectedFiles.forEach((f) => formData.append("files", f));

    const xhr = new XMLHttpRequest();
    CURRENT_UPLOAD_XHR = xhr;
    UPLOAD_IN_PROGRESS = true;

    const session = encodeURIComponent(getActiveSession());
    xhr.open("POST", `/api/upload?session=${session}`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = (e.loaded / e.total) * 100;
        progressBar.style.width = pct.toFixed(1) + "%";
      }
    };

    xhr.onload = () => {
      UPLOAD_IN_PROGRESS = false;
      CURRENT_UPLOAD_XHR = null;
      cancelUploadBtn?.classList.add("hidden");

      if (xhr.status === 200) {
        const resp = JSON.parse(xhr.responseText);
        const count = resp.uploaded?.length || 0;

        statusEl.textContent = `✅ Uploaded ${count} file(s).`;
        progressBar.style.width = "100%";

        markPreviewUploaded();
        loadUploadManager();

        clearSelectedUploadsUI({ delayMs: 2200 });
        reselectPendingUploadsBtn?.classList.add("hidden");
      } else {
        statusEl.textContent = `❌ Upload failed: ${xhr.statusText || "server error"}`;
        savePendingUploadState(selectedFiles);
      }
    };

    xhr.onerror = () => {
      UPLOAD_IN_PROGRESS = false;
      CURRENT_UPLOAD_XHR = null;
      cancelUploadBtn?.classList.add("hidden");
      statusEl.textContent = "❌ Upload error. Files are still queued for retry.";
      savePendingUploadState(selectedFiles);
    };

    xhr.onabort = () => {
      UPLOAD_IN_PROGRESS = false;
      CURRENT_UPLOAD_XHR = null;
      cancelUploadBtn?.classList.add("hidden");
      statusEl.textContent = "⚠ Upload cancelled. Files remain queued.";
      savePendingUploadState(selectedFiles);
    };

    xhr.send(formData);
  });
}

// ================================
  // Manage uploads already in S3
  // ================================
  async function loadUploadManager() {
      try {
          const session = encodeURIComponent(getActiveSession());

          const sessLabel = document.getElementById("uploadManagerSession");
          if (sessLabel) sessLabel.textContent = getActiveSession();

          const res = await fetch(`/api/uploads?session=${session}`);
          const data = await res.json();

          const labelsRes = await fetch(`/api/labels?session=${session}`);
          const labelsData = await labelsRes.json();
          const labels = labelsData.labels || {};

          renderUploadList("rawUploads", data.raw, "raw", labels);
          renderUploadList("processedUploads", data.processed, "processed", labels);
          loadAISetupSummary();
      } catch (e) {
          console.error("UploadManager error:", e);
      }
  }


  function renderUploadList(elementId, items, kind, labels = {}) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (!items || items.length === 0) {
      el.innerHTML = `<div class="empty">No videos</div>`;
      return;
    }

    const session = getActiveSession();
    const rawPrefix = `raw_uploads/${session}/`;
    const processedPrefix = `processed/${session}/`;

    // --------------------------------
    // Render HTML
    // --------------------------------
    el.innerHTML = items
      .map(file => {
        const isRaw = kind === "raw";
        const srcKey = isRaw ? rawPrefix + file : processedPrefix + file;
        const destKey = isRaw ? processedPrefix + file : rawPrefix + file;
        const savedLabel = labels[file] || "";

        return `
          <div class="upload-item">
            ${
              isRaw
                ? `
                <div class="clip-card">
                  <img class="clip-preview large" data-file="${file}" />
                  <div class="clip-filename">${file}</div>

                  <input
                    class="input clip-label-input"
                    value="${savedLabel}"
                    placeholder="e.g. Rooftop cocktails"
                    data-file="${file}"
                  />

                  <p class="hint-text small">
                    Used to guide captions and storytelling.
                  </p>

                  <div class="clip-actions">
                    <button class="btn ghost small recreate-label-btn" data-file="${file}">
                      🔁 Re-create label
                    </button>

                    <button class="btn-move" onclick="moveUpload('${srcKey}', '${destKey}')">
                      Move →
                    </button>

                    <button class="btn-delete" onclick="deleteUpload('${srcKey}')">
                      Delete
                    </button>
                  </div>
                </div>
                `
                : `
                <div class="clip-card processed">
                  <img class="clip-preview" data-file="${file}" />
                  <div class="clip-filename">${file}</div>

                  <div class="clip-actions">
                    <button class="btn-move" onclick="moveUpload('${srcKey}', '${destKey}')">
                      ← Move back
                    </button>

                    <button class="btn-delete" onclick="deleteUpload('${srcKey}')">
                      Delete
                    </button>
                  </div>
                </div>
                `
            }
          </div>
        `;
      })
      .join("");

    // --------------------------------
    // Load previews
    // --------------------------------
    el.querySelectorAll(".clip-preview").forEach(img => {
      const file = img.dataset.file;
      loadClipPreview(file, img);

      img.addEventListener("click", () => {
        img.src = "";
        loadClipPreview(file, img);
      });
    });

    // --------------------------------
    // Auto-save + AI auto-suggest (once)
    // --------------------------------
    el.querySelectorAll(".clip-label-input").forEach(input => {
      const glowSuccess = () => {
        input.classList.remove("error");
        input.classList.add("saved");
        setTimeout(() => input.classList.remove("saved"), 1200);
      };

      const glowError = () => {
        input.classList.add("error");
        setTimeout(() => input.classList.remove("error"), 1500);
      };

      const save = async () => {
        const file = input.dataset.file;
        const label = input.value.trim();

        try {
          // 🧠 AUTO-AI: only once, only if empty
          if (!label && !input.dataset.aiSuggested) {
            input.dataset.aiSuggested = "true";

            try {
              const res = await jsonFetch("/repair_label", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  session: getActiveSession(),
                  file,
                  label: ""
                })
              });

              if (res?.fixed_label) {
                input.value = res.fixed_label;
                await saveClipLabel(file, res.fixed_label);
                glowSuccess();
                return;
              }
            } catch (e) {
              console.warn("AI auto-suggest failed", e);
            }
          }

          // Normal save
          await saveClipLabel(file, label);
          glowSuccess();

        } catch (e) {
          console.error("Label save failed", e);
          glowError();
        }
      };

      input.addEventListener("blur", save);

      input.addEventListener("keydown", e => {
        if (e.key === "Enter") {
          e.preventDefault();
          input.blur();
        }
      });
    });

    // --------------------------------
    // 🔁 Re-create label (explicit AI)
    // --------------------------------
    el.querySelectorAll(".recreate-label-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const file = btn.dataset.file;
        const input = btn.closest(".clip-card")
                        ?.querySelector(".clip-label-input");
        if (!input) return;

        // Explicit action → allow AI again
        input.dataset.aiSuggested = "true";

        btn.disabled = true;
        btn.textContent = "Re-thinking…";

        try {
          const res = await jsonFetch("/repair_label", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session: getActiveSession(),
              file,
              label: input.value || ""
            })
          });

          if (res?.fixed_label) {
            input.value = res.fixed_label;
            await saveClipLabel(file, res.fixed_label);
            input.classList.add("saved");
            setTimeout(() => input.classList.remove("saved"), 1200);
          }

        } catch (e) {
          console.error("Re-create label failed", e);
          input.classList.add("error");
          setTimeout(() => input.classList.remove("error"), 1500);
          alert("Couldn’t re-create label");
        } finally {
          btn.disabled = false;
          btn.textContent = "🔁 Re-create label";
        }
      });
    });
  }
    

  async function saveClipLabel(key, label) {
    if (!key) return;

    try {
      const res = await jsonFetch("/api/labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session: getActiveSession(),
          file: key.split("/").pop(), // 🔥 FIX
          label
        })
      });

      const finalLabel = res?.label ?? "";
      const weak = !!res?.weak;

      const input = document.querySelector(
        `.clip-label-input[data-file="${key.split("/").pop()}"]`
      );

      const card = input?.closest(".clip-card");

      if (input && finalLabel !== input.value) {
        input.value = finalLabel;
      }

      if (card) {
        card.classList.toggle("label-weak", weak);
      }

      if (input) {
        input.classList.add("saved-flash");
        setTimeout(() => input.classList.remove("saved-flash"), 600);
      }

      loadAISetupSummary();

    } catch (err) {
      console.error("Failed to save label:", err);
      alert("Failed to save label");
    }
  }


  async function moveUpload(src, dest) {
      await fetch("/api/uploads/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ src, dest }),
      });

      loadUploadManager();
  }

  async function deleteUpload(key) {
      if (!confirm("Delete this file?")) return;

      await fetch("/api/uploads/delete", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key }),
      });

      loadUploadManager();
  }


  async function loadClipPreview(filename, imgEl) {
      const session = getActiveSession();

      try {
          const res = await fetch("/api/clip_preview", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session, filename })
          });

          const data = await res.json();
          if (data.image) {
              imgEl.src = data.image;
          }
      } catch (e) {
          console.warn("Preview failed for", filename);
      }
  }


    function showLabelWarning(file, badLabel, reason) {
    if (!confirm(
      `⚠️ Label is weak: ${reason}\n\nFixing labels improves captions, hooks and story flow.\n\nClick OK to auto-fix it or Cancel to edit yourself.`
    )) {
      return;
    }

    fetch("/repair_label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file,
        label: badLabel,
        session: getActiveSession()
      })
    })
    .then(r => r.json())
    .then(data => {
      if (data.fixed_label) {
        document.querySelector(`input[data-file="${file}"]`).value = data.fixed_label;
      }
    });
  }

 function initUploadListeners() {
    if (uploadListenersInitialized) return;
    uploadListenersInitialized = true;
    initUploadUI();
    loadUploadManager();

    document.addEventListener("keydown", (e) => {
      if (
        e.key === "Enter" &&
        e.target.classList.contains("clip-label-input")
      ) {
        e.preventDefault();
        e.target.blur();
      }
    });

    document.getElementById("reselectPendingUploadsBtn")
      ?.addEventListener("click", () => {
        document.getElementById("uploadFiles")?.click();
      });

    window.addEventListener("beforeunload", (e) => {
      if (!UPLOAD_IN_PROGRESS) return;
      e.preventDefault();
      e.returnValue = "";
    });
}

function getPendingUploadStorageKey() {
  return `pendingUploads:${getActiveSession()}`;
}

function savePendingUploadState(files = []) {
  try {
    const payload = files.map(f => ({
      name: f.name,
      size: f.size,
      type: f.type,
      lastModified: f.lastModified
    }));

    sessionStorage.setItem(
      getPendingUploadStorageKey(),
      JSON.stringify(payload)
    );
  } catch (err) {
    console.warn("Failed to save pending upload state", err);
  }
}

function loadPendingUploadState() {
  try {
    const raw = sessionStorage.getItem(getPendingUploadStorageKey());
    if (!raw) return [];
    return JSON.parse(raw);
  } catch (err) {
    console.warn("Failed to load pending upload state", err);
    return [];
  }
}

function clearPendingUploadState() {
  try {
    sessionStorage.removeItem(getPendingUploadStorageKey());
  } catch (err) {
    console.warn("Failed to clear pending upload state", err);
  }
}

function renderPendingUploadGhosts(filesMeta = []) {
  const preview = document.getElementById("uploadPreview");
  const uploadBtn = document.getElementById("uploadBtn");
  const statusEl = document.getElementById("uploadStatus");

  if (!preview) return;

  preview.innerHTML = "";

  if (!filesMeta.length) {
    if (uploadBtn) uploadBtn.disabled = true;
    updateReselectButtonVisibility([]);
    return;
  }

  filesMeta.forEach(file => {
    const wrapper = document.createElement("div");
    wrapper.className = "preview-item pending-ghost";

    const name = document.createElement("div");
    name.className = "preview-name";
    name.textContent = `${file.name} — pending only, not attached`;

    wrapper.appendChild(name);
    preview.appendChild(wrapper);
  });

  if (uploadBtn) uploadBtn.disabled = true;

  if (statusEl) {
    statusEl.textContent =
      "⚠ These files were uploading before refresh. Re-select them from your device to continue.";
  }

  updateReselectButtonVisibility([]);
}
