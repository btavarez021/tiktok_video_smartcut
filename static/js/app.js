// ================================
// Variables
// ================================
let previewAudio = null;
let previewPlaying = false;

// 🔵 Active session (hotel / batch)
let ACTIVE_SESSION = "default";

let ACTIVE_EXPORT_TASK = null;

let suppressNextPreview = false;

let lastSavedCaptionsText = "";


function setVariantsStatus(message, state = "loading") {
  const el = document.getElementById("variantsInlineStatus");
  if (!el) return;

  el.textContent = message;
  el.className = `inline-status ${state}`;
  el.classList.remove("hidden");
}

function countBlocks(text) {
  if (!text) return 0;
  return text.split(/\n\s*\n/).filter(Boolean).length;
}

function toggleClipSummaries() {
    const body = document.getElementById("clipSummariesBody");
    const chevron = document.getElementById("clipSummaryChevron");

    const isHidden = body.classList.contains("hidden");

    body.classList.toggle("hidden");
    chevron.textContent = isHidden ? "▾" : "▸";

    if (isHidden) {
        loadClipSummaries();
    }
}

async function loadClipSummaries() {
    const list = document.getElementById("clipSummariesList");
    if (!list) return;

    list.innerHTML = "<li>Loading summaries…</li>";

    try {
        const session = getActiveSession();
        const res = await jsonFetch(`/api/clip_summaries?session=${encodeURIComponent(session)}`);

        if (!res.clips || res.clips.length === 0) {
            list.innerHTML = "<li>No analysis found yet. Run Analyze Clips first.</li>";
            return;
        }

        list.innerHTML = "";
        res.clips.forEach(c => {
            const li = document.createElement("li");
            li.innerHTML = `
                <strong>${c.file}</strong>
                <div class="hint-text">${c.summary}</div>
            `;
            list.appendChild(li);
        });

    } catch (err) {
        console.error(err);
        list.innerHTML = "<li>Failed to load clip summaries.</li>";
    }
}


function flashElement(el) {
  if (!el) return;
  el.classList.remove("flash");
  void el.offsetWidth; // force reflow
  el.classList.add("flash");
}

function setCaptionSource(type, text, noChange = false) {
  const el = document.getElementById("captionStatus");
  if (!el) return;

  el.textContent = text;

  el.className = "caption-source"; // reset

  if (type === "yaml") el.classList.add("source-yaml");
  if (type === "filenames") el.classList.add("source-filenames");

  if (noChange) {
    el.classList.add("no-change");
    el.textContent += " · No changes";
  }
}

function toggleCaptionCompare(forceOpen = false) {
    const body = document.getElementById("captionCompareBody");
    const chevron = document.getElementById("compareChevron");

    if (!body) return;

    const isHidden = body.classList.contains("hidden");

    if (forceOpen || isHidden) {
        body.classList.remove("hidden");
        chevron.textContent = "▾";
    } else {
        body.classList.add("hidden");
        chevron.textContent = "▸";
    }
}


function setCaptionInlineStatus(text, type = "info") {
  const el = document.getElementById("captionInlineStatus");
  if (!el) return;

  el.textContent = text;
  el.className = `caption-inline-status ${type}`;
  el.classList.remove("hidden");

    // Auto-hide after short delay
  setTimeout(() => {
    el.classList.add("hidden");
  }, 2200);

}


// -------------------------
// Session helpers
// -------------------------
function updateSessionLabels() {
    const labels = document.querySelectorAll(".sessionLabel");
    labels.forEach((l) => (l.textContent = getActiveSession()));
}

function sessionQS() {
    return "?session=" + encodeURIComponent(getActiveSession());
}

function updateSessionTags() {
    document.querySelectorAll("#currentSessionTag").forEach((el) => {
        el.textContent = getActiveSession();
    });
}

function sanitizeSessionName(raw) {
    let s = (raw || "").toLowerCase().trim();

    try {
        s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
    } catch {
        // ignore
    }

    s = s.replace(/[^a-z0-9]+/g, "_");
    s = s.replace(/^_+|_+$/g, "");

    if (!s) s = "default";
    return s;
}

function getActiveSession() {
    return ACTIVE_SESSION || "default";
}

function setActiveSession(name) {
    const safe = sanitizeSessionName(name);
    ACTIVE_SESSION = safe;

    // Update label chips
    updateSessionLabels();

    // persist
    try {
        localStorage.setItem("activeSession", ACTIVE_SESSION);
    } catch {}

    // OLD header label (if present)
    const label = document.getElementById("activeSessionLabel");
    if (label) {
        label.textContent = ACTIVE_SESSION;
        label.classList.remove("session-active-flash");
        void label.offsetWidth;
        label.classList.add("session-active-flash");
    }

    // OLD dropdown (if present)
    const ddl = document.getElementById("sessionDropdown");
    if (ddl) {
        ddl.value = ACTIVE_SESSION;
        ddl.classList.remove("session-pulse");
        void ddl.offsetWidth;
        ddl.classList.add("session-pulse");
    }

    // OLD toast area (if present)
    const toastArea = document.getElementById("sessionToastArea");
    if (toastArea) {
        toastArea.innerHTML = `
            <div class="session-toast">
                ✓ Active session changed to <strong>${ACTIVE_SESSION}</strong>
            </div>
        `;
        setTimeout(() => (toastArea.innerHTML = ""), 2600);
    }

    console.log("[SESSION] Active:", ACTIVE_SESSION);

    // Sidebar label
    sidebarSyncActiveLabel();

    // UI refresh actions
    loadUploadManager();
    clearAnalysisUI();
    refreshAnalyses();
    loadConfigAndYaml();
    refreshHookScore();
    loadSessionDropdown();
    loadSessions();
    sidebarLoadSessions();

    document.querySelectorAll('.session-tag').forEach(tag => {
    tag.classList.remove('pulse-once');
    void tag.offsetWidth;
    tag.classList.add('pulse-once');
    });

}


// =========================================
// SIDEBAR SESSION MANAGER v2
// =========================================
function sidebarToast(msg) {
    const area = document.getElementById("sidebarSessionToastArea");
    if (!area) return;

    const div = document.createElement("div");
    div.className = "sidebar-toast";
    div.textContent = msg;

    area.appendChild(div);
    setTimeout(() => div.classList.add("fade-out"), 1300);
    setTimeout(() => div.remove(), 1600);
}

async function sidebarLoadSessions() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();

        const ddl = document.getElementById("sidebarSessionDropdown");
        if (!ddl) return;

        ddl.innerHTML = "";

        (data.sessions || []).forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s;
            opt.textContent = s;
            ddl.appendChild(opt);
        });

        ddl.value = getActiveSession();
    } catch (err) {
        console.error("Failed loading sessions:", err);
    }
}

function sidebarSyncActiveLabel() {
    const el = document.getElementById("sidebarActiveSession");
    if (!el) return;
    el.textContent = getActiveSession();
}


function getOverlayStyle() {
    return (document.getElementById("overlayStyle")?.value || "ai_recommended").toLowerCase();
}

// ===============================
// 🔥 Overlay Preview System
// ===============================
async function previewOverlay(mode = "fast") {

    if (!document.getElementById("overlayPreviewBox")) return;

    const session = getActiveSession();
    const box = document.getElementById("overlayPreviewBox");
    if (!box) return;

    box.innerHTML = "⏳ generating preview…";

    try {
        let res;

        if (mode === "fast") {
            res = await jsonFetch("/api/overlay_preview", {
                method: "POST",
                body: JSON.stringify({
                    session,
                    style: getOverlayStyle()
                }),
            });
        } else {
    // Full preview is STILL READ-ONLY
    // It just asks for a higher-quality preview image

    res = await jsonFetch("/api/overlay_preview", {
        method: "POST",
        body: JSON.stringify({
            session,
            style: getOverlayStyle(),
            quality: "full"   // optional hint to backend
        }),
    });
}

        if (res?.image) {
            box.innerHTML = "";
            const img = document.createElement("img");
            img.src = res.image;
            img.style.width = "100%";
            img.style.height = "100%";
            img.style.objectFit = "cover";
            img.style.position = "absolute";
            img.style.zIndex = "3";

            box.appendChild(img);
        } else {
            box.innerHTML = "⚠ No preview returned.";
        }
    } catch (e) {
        console.error(e);
        box.innerHTML = "❌ Preview failed — check logs.";
    }
}


// ================================
// Utility helpers
// ================================

// ================================
// Emoji-safe overlay text helper
// ================================
function stripEmojis(text) {
  if (!text) return text;
  return text.replace(/[\p{Extended_Pictographic}]/gu, "").trim();
}


function disableDownloadButton() {
    const btn = document.getElementById("downloadLink");
    if (!btn) return;

    btn.classList.add("disabled");
    btn.textContent = "Exporting…";
    btn.removeAttribute("href");   // remove old link
}


function showSessionToast(msg) {
    const area = document.getElementById("sessionToastArea");
    if (!area) return;

    const el = document.createElement("div");
    el.className = "session-toast";
    el.textContent = msg;

    area.appendChild(el);

    setTimeout(() => {
        el.classList.add("fade-out");
        setTimeout(() => el.remove(), 500);
    }, 1300);
}

// EXPORT URL helper – checks if S3 link is live
async function probeUrl(url) {
    try {
        const res = await fetch(url, { method: "HEAD" });
        return res.ok;
    } catch {
        return false;
    }
}


function toggleUploadManager() {
    const content = document.getElementById("uploadManagerContent");
    const icon = document.getElementById("uploadManagerToggle");
    if (!content || !icon) return;

    content.classList.toggle("collapsed");

    if (content.classList.contains("collapsed")) {
        icon.textContent = "▲";
    } else {
        icon.textContent = "▼";
    }
}

// Auto-fading status helper
let _statusTimers = {};

function setStatus(id, msg, type = "info", autoHide = true) {
    const el = document.getElementById(id);
    if (!el) return;

    el.className = "status-text status-" + type;
    el.textContent = msg;

    if (_statusTimers[id]) {
        clearTimeout(_statusTimers[id]);
        delete _statusTimers[id];
    }

    if (!autoHide) return;

    _statusTimers[id] = setTimeout(() => {
        el.textContent = "";
        el.className = "status-text status-info";
        delete _statusTimers[id];
    }, 5000);
}

// JSON fetch helper with sane defaults
async function jsonFetch(url, options = {}) {
    const resp = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });

    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `Request failed: ${resp.status}`);
    }

    try {
        return await resp.json();
    } catch {
        return {};
    }
}

// Status hint helper (bottom style line)
function showStatus(msg, type = "info") {
    const el = document.getElementById("styleStatus");
    if (!el) return;
    el.textContent = msg;
    el.className = "hint-text " + type;
}

// Simple download helper
function safeDownload(url, filename = "export.mp4") {
    const a = document.createElement("a");
    a.href = url;
    a.style.display = "none";
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

// ================================
// Stepper behavior
// ================================
function initStepper() {
    const stepButtons = document.querySelectorAll(".stepper .step");

    stepButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            const targetSel = btn.dataset.target;
            const targetEl = document.querySelector(targetSel);
            if (targetEl) {
                targetEl.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                });
            }
            stepButtons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
        });
    });

    const steps = Array.from(document.querySelectorAll(".step-card"));
    if (!steps.length) return;

    const observer = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    const id = "#" + entry.target.id;
                    stepButtons.forEach((btn) => {
                        if (btn.dataset.target === id) {
                            stepButtons.forEach((b) => b.classList.remove("active"));
                            btn.classList.add("active");
                        }
                    });
                }
            });
        },
        { threshold: 0.4 }
    );

    steps.forEach((s) => observer.observe(s));
}

// ================================
// Status log polling
// ================================
let statusLogTimer = null;

async function refreshStatusLog() {
  try {
    const data = await jsonFetch("/api/status");
    const log = data.status_log || [];
    const el = document.getElementById("statusLog");
    const autoScroll = document.getElementById("autoScrollLogs")?.checked;

    if (!el) return;

    const wasAtBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 40;

    el.textContent = log.join("\n");

    // ✅ Only scroll if:
    // - Auto-scroll is ON
    // - User was already near the bottom
    if (autoScroll && wasAtBottom) {
      el.scrollTop = el.scrollHeight;
    }
  } catch {
    // silent
  }
}


function startStatusLogPolling() {
    if (statusLogTimer) clearInterval(statusLogTimer);
    refreshStatusLog();
    statusLogTimer = setInterval(refreshStatusLog, 2000);
}

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

    setStatus("uploadStatus", "⬆ Uploading…", "info");

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

function initUploadUI() {
    const dropZone = document.getElementById("dropZone");
    const fileInput = document.getElementById("uploadFiles");
    const preview = document.getElementById("uploadPreview");
    const uploadBtn = document.getElementById("uploadBtn");
    const progressWrapper = document.getElementById("uploadProgressWrapper");
    const progressBar = document.getElementById("uploadProgress");
    const statusEl = document.getElementById("uploadStatus");

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
                updatePreview();
            };

            wrapper.appendChild(name);
            wrapper.appendChild(removeBtn);
            preview.appendChild(wrapper);
        });

        uploadBtn.disabled = selectedFiles.length === 0;
    }

    dropZone.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", (e) => {
        selectedFiles = Array.from(e.target.files);
        updatePreview();
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
        selectedFiles = Array.from(e.dataTransfer.files);
        updatePreview();
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

        const formData = new FormData();
        selectedFiles.forEach((f) => formData.append("files", f));

        const xhr = new XMLHttpRequest();
        const session = encodeURIComponent(getActiveSession());
        xhr.open("POST", `/api/upload?session=${session}`);

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const pct = (e.loaded / e.total) * 100;
                progressBar.style.width = pct.toFixed(1) + "%";
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                const resp = JSON.parse(xhr.responseText);
                statusEl.textContent = `✅ Uploaded ${resp.uploaded?.length || 0} file(s).`;
                progressBar.style.width = "100%";
                loadUploadManager();
            } else {
                statusEl.textContent = `❌ Upload failed: ${xhr.statusText}`;
            }
        };

        xhr.onerror = () => {
            statusEl.textContent = "❌ Upload error.";
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
    } catch (e) {
        console.error("UploadManager error:", e);
    }
}


function renderUploadList(elementId, items, kind, labels={}) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (!items || items.length === 0) {
        el.innerHTML = `<div class="empty">No videos</div>`;
        return;
    }

    const session = getActiveSession();
    const rawPrefix = `raw_uploads/${session}/`;
    const processedPrefix = `processed/${session}/`;

    el.innerHTML = items
        .map((file) => {
            const isRaw = kind === "raw";
            const srcKey = isRaw ? rawPrefix + file : processedPrefix + file;
            const destKey = isRaw ? processedPrefix + file : rawPrefix + file;
            const savedLabel = labels[file] || "";
            return `
                
                <div class="upload-item">
                    <div class="file-info">
                        <strong>${session}/${file}</strong>
                    </div>

                    ${
                        isRaw
                            ? `
                            <div class="clip-label-row">
                                <button
                                    class="btn ghost small suggest-label-btn"
                                    data-file="${file}">
                                    🧠 Suggest label
                                </button>

                                 <span class="tooltip">ⓘ
                                    <span class="tooltiptext">
                                        Labels auto-save when you click away or press Enter.<br>
                                        Used to guide AI captions — not shown in the video.
                                    </span>
                                </span>

                                <input
                                    class="input clip-label-input"
                                    value="${savedLabel}"
                                    placeholder="Optional label (auto-saves)"
                                    title="Labels auto-save when you click away or press Enter"
                                    data-file="${file}"
                                    />

                                <p class="hint-text small">
                                    Used to guide captions and filename-based generation.
                                    Not shown in the video.
                                </p>


                            </div>
                            `
                            : ""
                    }

                    <div class="buttons">
                        ${
                            isRaw
                                ? `<button class="btn-move" onclick="moveUpload('${srcKey}', '${destKey}')">Move →</button>`
                                : `<button class="btn-move" onclick="moveUpload('${srcKey}', '${destKey}')">← Move</button>`
                        }
                        <button class="btn-delete" onclick="deleteUpload('${srcKey}')">Delete</button>
                    </div>
                </div>
            `;

        })
        .join("");

        // ================================
        // Wire clip label inputs
        // ================================
        el.querySelectorAll(".clip-label-input").forEach(input => {
            input.addEventListener("blur", async () => {
                const file = input.dataset.file;
                const label = input.value.trim();
                await saveClipLabel(file, label);
            });

            input.addEventListener("keydown", async (e) => {
                if (e.key === "Enter") {
                    e.preventDefault();
                    input.blur(); // triggers save
                }
            });
        });

        // ================================
        // Suggest label button
        // ================================
        el.querySelectorAll(".suggest-label-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const file = btn.dataset.file;

                btn.disabled = true;
                btn.textContent = "Thinking…";

                try {
                    const res = await jsonFetch("/api/chat", {
                        method: "POST",
                        body: JSON.stringify({
                            session: getActiveSession(),
                            message: `Suggest a short descriptive label for this hotel/travel clip: ${file}`
                        })
                    });

                    const suggestion = (res.reply || "").split("\n")[0].trim();

                    const input = btn
                        .closest(".clip-label-row")
                        ?.querySelector(".clip-label-input");

                    if (input && suggestion) {
                        input.value = suggestion;
                        await saveClipLabel(file, suggestion);
                    }
                } catch (err) {
                    console.error("Suggest label failed:", err);
                } finally {
                    btn.disabled = false;
                    btn.textContent = "🧠 Suggest label";
                }
            });
        });

}

// ================================
// Clip label persistence
// ================================
async function saveClipLabel(file, label) {
  if (!file) return;

  try {
    await jsonFetch("/api/labels", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        file,
        label
      })
    });

    // ✅ UX feedback
    const input = document.querySelector(`.clip-label-input[data-file="${file}"]`);
    if (input) {
      input.classList.add("saved-flash");
      setTimeout(() => input.classList.remove("saved-flash"), 600);
    }

  } catch (err) {
    console.error("Failed to save label:", err);
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

// Clear old analysis results whenever switching sessions
function clearAnalysisUI() {
    const list = document.getElementById("analysesList");
    if (list) list.innerHTML = "";

    const status = document.getElementById("analyzeStatus");
    if (status) {
        status.textContent = "Session changed — analyze to see results.";
        status.className = "hint-text";
    }
}

// ================================
// Step 1: Analysis
// ================================
async function analyzeClips() {
    clearAnalysisUI();
    const analyzeBtn = document.getElementById("analyzeBtn");
    const statusEl = document.getElementById("analyzeStatus");
    if (!analyzeBtn || !statusEl) return;

    analyzeBtn.disabled = true;
    setStatus(
        "analyzeStatus",
        "Analyzing clips from S3… this can take a bit…",
        "info",
        false
    );

    try {
        const data = await jsonFetch("/api/analyze", {
            method: "POST",
            body: JSON.stringify({ session: getActiveSession() }),
        });
        const count = data.count ?? Object.keys(data || {}).length;
        setStatus(
            "analyzeStatus",
            `Analysis complete. ${count} video(s).`,
            "success"
        );
        await refreshAnalyses();
    } catch (err) {
        console.error(err);
        setStatus(
            "analyzeStatus",
            `Error during analysis: ${err.message}`,
            "error"
        );
    } finally {
        analyzeBtn.disabled = false;
    }
}

async function refreshAnalyses() {
    const listEl = document.getElementById("analysesList");
    if (!listEl) return;

    listEl.innerHTML = "";
    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/analyses_cache?session=${session}`);
        const entries = Object.entries(data || {});
        if (!entries.length) {
            listEl.innerHTML =
                '<li><span class="analysis-desc">No analyses found yet. Run "Analyze clips" first.</span></li>';
            return;
        }
        entries.forEach(([file, desc]) => {
            const li = document.createElement("li");
            const f = document.createElement("div");
            f.className = "analysis-file";
            f.textContent = file;
            const d = document.createElement("div");
            d.className = "analysis-desc";
            d.textContent = desc || "(no description)";
            li.appendChild(f);
            li.appendChild(d);
            listEl.appendChild(li);
        });
    } catch (err) {
        listEl.innerHTML = `<li><span class="analysis-desc">Error loading analyses: ${err.message}</span></li>`;
    }
}

// ================================
// Step 2: YAML generation & config
// ================================
async function generateYaml() {
    const statusEl = document.getElementById("yamlStatus");
    if (!statusEl) return;
    setStatus(
        "yamlStatus",
        "Calling LLM to build config.yml storyboard…",
        "info"
    );
    try {
        await jsonFetch("/api/generate_yaml", {
            method: "POST",
            body: JSON.stringify({ session: getActiveSession() }),
        });
        setStatus("yamlStatus", "YAML generated!", "success");
        await loadConfigAndYaml();
        await refreshHookScore();
        await refreshStoryFlowScore();
    } catch (err) {
        console.error(err);
        setStatus(
            "yamlStatus",
            `Error generating YAML: ${err.message}`,
            "error"
        );
    }
}

async function loadConfigAndYaml() {
    const yamlTextEl = document.getElementById("yamlText");
    const yamlPreviewEl = document.getElementById("yamlPreview");
    if (!yamlTextEl || !yamlPreviewEl) return;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        yamlTextEl.value = data.yaml || "# No config.yml yet.";
        yamlPreviewEl.textContent = JSON.stringify(data.config || {}, null, 2);
    } catch (err) {
        yamlTextEl.value = "";
        yamlPreviewEl.textContent = `Error loading config: ${err.message}`;
    }
}

async function saveYaml() {
    const yamlTextEl = document.getElementById("yamlText");
    const statusEl = document.getElementById("yamlStatus");
    if (!yamlTextEl || !statusEl) return;

    const raw = yamlTextEl.value || "";
    setStatus("yamlStatus", "Saving YAML…", "info");

    try {
        await jsonFetch("/api/save_yaml", {
            method: "POST",
            body: JSON.stringify({
                yaml: raw,
                session: getActiveSession(),
            }),
        });
        setStatus("yamlStatus", "YAML saved to config.yml.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus(
            "yamlStatus",
            `Error saving YAML: ${err.message}`,
            "error"
        );
    }
}

async function refreshHookScore() {
  const captionsEl = document.getElementById("captionsText");
  const card = document.querySelector(".hook-score-card");
  const scoreEl = document.getElementById("hookScoreValue");
  const reasonsEl = document.getElementById("hookScoreReasons");
  const hookEl = document.getElementById("hookScoreHook");
  const statusEl = document.getElementById("hookScoreStatus");
  const improveBtn = document.getElementById("improveHookBtn");

  // 🚫 No captions → hide hook score entirely
  if (!captionsEl || !captionsEl.value.trim()) {
    card?.classList.add("hidden");
    return;
  }

  if (!card || !scoreEl || !reasonsEl || !hookEl) return;

  card.classList.remove("hidden");

  try {
    if (statusEl) statusEl.textContent = "Checking hook…";

    const session = encodeURIComponent(getActiveSession());
    const data = await jsonFetch(`/api/hook_score?session=${session}`);

    const score = Number(data.score ?? 0);

    // -----------------------------
    // 🔒 Story flow lock (NOW safe)
    // -----------------------------
    const flowCard = document.querySelector(".story-flow-card");
    const lockedHint = document.getElementById("storyFlowLockedHint");

    if (flowCard && lockedHint) {
      if (score < 60) {
        flowCard.classList.add("hidden");
        lockedHint.classList.remove("hidden");
      } else {
        lockedHint.classList.add("hidden");
        flowCard.classList.remove("hidden");
      }
    }

    updateImproveButtons(score, null);

    scoreEl.textContent = `${score}/100`;
    hookEl.textContent = data.hook || "(no opening caption yet)";

    // Reset classes
    card.classList.remove("good", "ok", "bad");
    scoreEl.classList.remove("good", "ok", "bad");

    if (score >= 85) {
      card.classList.add("good");
      scoreEl.classList.add("good");
    } else if (score >= 70) {
      card.classList.add("ok");
      scoreEl.classList.add("ok");
    } else {
      card.classList.add("bad");
      scoreEl.classList.add("bad");
    }

    const reasons = data.reasons || [];
    reasonsEl.innerHTML = reasons.length
      ? reasons.map(r => `<li>${r}</li>`).join("")
      : `<li>Looks solid ✅</li>`;

    if (statusEl) statusEl.textContent = "";
    // ================================
    // ⚠ Soft Warning: Low Hook + Rewrite Mode Active
    // ================================
    const rewriteRadio = document.querySelector(
        'input[name="captionRewriteMode"][value="rewrite"]'
        );

        const rewriteSelected = rewriteRadio?.checked;
        const rewriteEnabled = rewriteRadio && !rewriteRadio.disabled;

        if (score < 60 && rewriteEnabled) {
            setStatus(
                "overlayStatus",
                rewriteSelected
                    ? "⚠ Hook is weak — rewrite may hurt clarity. Improve Hook first."
                    : "⚠ Hook is weak. Fix it before using Rewrite for best results.",
                "warning",
                false
            );
        }


    if (score >= 60) {
        clearOverlayWarning();
        }


  } catch (err) {
    console.error("Hook score error:", err);
    if (statusEl) statusEl.textContent = "Hook score unavailable.";
  }
}

function clearOverlayWarning() {
  const el = document.getElementById("overlayStatus");
  if (!el) return;
  el.textContent = "";
  el.className = "status-text";
}



async function improveHook() {
  const btn = document.getElementById("improveHookBtn");
  const statusEl = document.getElementById("hookScoreStatus");
  if (!btn) return;

  btn.disabled = true;
  if (statusEl) statusEl.textContent = "Improving hook…";

  try {
    const data = await jsonFetch("/api/hook_improve", {
      method: "POST",
      body: JSON.stringify({ session: getActiveSession() }),
    });

    if (data.status !== "ok") throw new Error(data.error || "failed");

    // Reload captions + YAML
    await loadCaptionsFromYaml();
    await loadConfigAndYaml();

    // Re-score BOTH
    await refreshHookScore();
    await refreshStoryFlowScore();

    if (statusEl) statusEl.textContent = "Hook improved ✅";
    setTimeout(() => {
      if (statusEl) statusEl.textContent = "";
    }, 1500);
  } catch (err) {
    console.error(err);
    if (statusEl) statusEl.textContent = "Failed to improve hook.";
  } finally {
    btn.disabled = false;
  }
}

async function generateCaptionVariants() {
    const btn = document.getElementById("generateVariantsBtn");

    const modes = {
        rewrite: document.getElementById("mode_rewrite").checked,
        hook: document.getElementById("mode_hook").checked,
        punchy: document.getElementById("mode_punchy").checked,
        story: document.getElementById("mode_story").checked,
        influencer: document.getElementById("mode_influencer").checked,
        minimal: document.getElementById("mode_minimal").checked,
    };

    const session = getActiveSession();

    // 🔔 Immediate feedback
    setVariantsStatus("Generating caption variants…", "loading");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Generating…";
    }

    try {
        const res = await fetch("/api/variants", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session, modes })
        });

        if (!res.ok) {
            throw new Error("Variant generation failed");
        }

        const data = await res.json();

        const box = document.getElementById("variantsOutput");
        box.innerHTML = "";

        data.variants.forEach((variant, i) => {
            const text = variant.text || "";
            const tone = variant.tone || "";

            box.innerHTML += `
                <div class="variantCard">
                    <h4>Version ${i + 1}</h4>
                    ${tone ? `<div class="variantTone">${tone}</div>` : ""}
                    <pre style="white-space:pre-wrap">${text}</pre>
                    <button onclick="applyCaptionVariant(\`${text.replace(/`/g, "\\`")}\`)">
                        Use This
                    </button>
                </div>`;
        });

        // ✅ Success feedback AFTER render
        setVariantsStatus("Variants generated ✓", "success");

        // Optional auto-hide
        setTimeout(() => {
            document
                .getElementById("variantsInlineStatus")
                ?.classList.add("hidden");
        }, 2000);

    } catch (err) {
        console.error(err);
        setVariantsStatus("Failed to generate variants", "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "⚡ Generate Caption Variants";
        }
    }
}

// Alias used by caption system
async function refreshOverlayPreview() {
  return previewOverlay("fast");
}

// =============================================
// Apply selected generated caption variant
// =============================================
async function applyCaptionVariant(text) {
  const session = getActiveSession();
  const el = document.getElementById("captionsText");
  if (!el) return;

  const originalText = el.value;
  const originalCount = countBlocks(originalText);
  const newCount = countBlocks(text);

  // Populate comparison
  document.getElementById("compareOld").textContent = originalText;
  document.getElementById("compareNew").textContent = text;
  toggleCaptionCompare(true);

  // Apply
  el.value = text;

  if (originalCount !== newCount) {
    setStatus(
      "captionsStatus",
      `⚠ Caption count mismatch (${originalCount} → ${newCount}). Review before saving.`,
      "warning"
    );
    return;
  }

  try {
    const res = await fetch("/api/save_captions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session, text })
    });

    if (!res.ok) throw new Error("Failed to save captions");

    lastSavedCaptionsText = text; // 🔑 CRITICAL FIX

    await loadCaptionsFromYaml();
    await loadConfigAndYaml();
    await refreshOverlayPreview();

    refreshHookScore();
    refreshStoryFlowScore();

    setStatus("captionsStatus", "Caption applied ✓", "success");

  } catch (err) {
    console.error(err);
    setStatus("captionsStatus", "Failed to apply caption", "error");
  }
}


// ================================
// Step Enter Handler (Fix Missing Function)
// ================================
function addStepEnterHandler(stepNumber, callback) {
    const stepCard = document.querySelector(`.step-card:nth-of-type(${stepNumber})`);
    if (!stepCard) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) callback();
        });
    }, { threshold: 0.4 });

    observer.observe(stepCard);
}


// Story Flow Score
// Evaluates ONLY middle captions (excludes hook + CTA)
// Read-only score to assess pacing & narrative progression

async function refreshStoryFlowScore() {
    const captionsEl = document.getElementById("captionsText");
    const card = document.querySelector(".story-flow-card");
    const scoreEl = document.getElementById("storyFlowScoreValue");
    const reasonsEl = document.getElementById("storyFlowReasons");
    const improveBtn = document.getElementById("improveStoryFlowBtn");

    if (!captionsEl || !card || !scoreEl || !reasonsEl) return;

    if (!captionsEl.value.trim()) {
        card.classList.add("hidden");
        return;
    }

    const blocks = captionsEl.value
        .split(/\n\s*\n/)
        .map(b => b.trim())
        .filter(Boolean);

    // Need at least: hook + 2 middle captions
    if (blocks.length < 3) {
        card.classList.add("hidden");
        if (improveBtn) improveBtn.disabled = true;
        return;
    }

    card.classList.remove("hidden");
    if (improveBtn) improveBtn.disabled = false;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/story_flow_score?session=${session}`);

        const score = Number(data.score ?? 0);
        updateImproveButtons(null, score);
        scoreEl.textContent = `${score}/100`;

        card.classList.remove("good", "ok", "bad");
        scoreEl.classList.remove("good", "ok", "bad");

        if (score >= 85) {
            card.classList.add("good");
            scoreEl.classList.add("good");
        } else if (score >= 70) {
            card.classList.add("ok");
            scoreEl.classList.add("ok");
        } else {
            card.classList.add("bad");
            scoreEl.classList.add("bad");
        }

        const reasons = data.reasons || [];
        reasonsEl.innerHTML = reasons.length
            ? reasons.map(r => `<li>${r}</li>`).join("")
            : `<li>Flow looks solid ✅</li>`;

    } catch (err) {
        console.error("Story flow score error:", err);
        card.classList.add("hidden");
    }
}

function updateImproveButtons(hookScore, storyScore) {
  const hookBtn = document.getElementById("improveHookBtn");
  const flowBtn = document.getElementById("improveStoryFlowBtn");

  if (hookBtn && typeof hookScore === "number") {
    hookBtn.classList.toggle("hidden", hookScore >= 85);
  }

  if (flowBtn && typeof storyScore === "number") {
    flowBtn.classList.toggle("hidden", storyScore >= 80);
  }
}

    // ================================
    // Disable Rewrite Mode if no captions exist
    // ================================
    function updateRewriteModeAvailability() {
    const text = document.getElementById("captionsText")?.value.trim();
    const rewriteRadio = document.querySelector('input[name="captionRewriteMode"][value="rewrite"]');
    const captionBox = document.querySelector(".caption-mode");

    if (!rewriteRadio) return;

    const hasText = text && text.length > 3;

    // enable/disable rewrite mode + fade
    rewriteRadio.disabled = !hasText;
    rewriteRadio.parentElement.style.opacity = hasText ? "1" : "0.4";

    if (!hasText) {
        clearOverlayWarning();
        }


    // 🔥 Highlight box when rewrite ON + captions exist
    if (hasText && rewriteRadio.checked) {
        captionBox?.classList.add("rewrite-hot");
    } else {
        captionBox?.classList.remove("rewrite-hot");
    }
}


// ================================
// Step 3: Captions
// ================================
function buildCaptionsFromConfig(cfg) {
    if (!cfg || typeof cfg !== "object") return "";
    const parts = [];

    if (cfg.first_clip && cfg.first_clip.text) parts.push(cfg.first_clip.text);

    if (Array.isArray(cfg.middle_clips)) {
        cfg.middle_clips.forEach((clip) => {
            if (clip && clip.text) parts.push(clip.text);
        });
    }

    if (cfg.last_clip && cfg.last_clip.text) parts.push(cfg.last_clip.text);

    return parts.join("\n\n");
}

async function loadCaptionsFromYaml({ preserveSource = false } = {}) {
    const captionsEl = document.getElementById("captionsText");
    if (!captionsEl) return;

    setCaptionInlineStatus("Loading captions from YAML…", "info");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        const before = captionsEl.value.trim();
        const next = buildCaptionsFromConfig(cfg).trim();

        captionsEl.value = next;

        // ✅ REGISTER BASELINE (THIS FIXES THE BUG)
        lastSavedCaptionsText = next;

        updateRewriteModeAvailability();
        await refreshHookScore();
        await refreshStoryFlowScore();

        if (before === next) {
            setCaptionSource("yaml", "🔵 SOURCE: YAML", true);
            setCaptionInlineStatus("Captions already up to date", "info");
        } else {
            setCaptionSource("yaml", "🔵 SOURCE: YAML");
            setCaptionInlineStatus("Captions loaded from YAML", "success");
            flashElement(captionsEl);
        }

    } catch (err) {
        console.error(err);
        setCaptionInlineStatus("Failed to load captions", "error");
        setCaptionSource("yaml", "⚠ SOURCE: YAML (failed)");
    }
}


// OLD session list (if legacy card exists)
async function loadSessions() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();

        const list = document.getElementById("sessionList");
        if (!list) return;

        list.innerHTML = "";

        (data.sessions || []).forEach((session) => {
            const li = document.createElement("li");
            li.className = "analysis-item";
            li.innerHTML = `
                <span class="analysis-file">${session}</span>
                <button class="btn btn-delete deleteSessionBtn" data-session="${session}">
                    🗑 Delete
                </button>
            `;
            list.appendChild(li);
        });
    } catch (err) {
        console.error("[SESSION] loadSessions failed:", err);
    }
}

// Populate quick-switch session dropdown (legacy)
async function loadSessionDropdown() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();

        const ddl = document.getElementById("sessionDropdown");
        if (!ddl) return;

        ddl.innerHTML = "";

        const sessions = data.sessions || [];

        if (sessions.length === 0) {
            ddl.innerHTML = `<option value="">(no sessions found)</option>`;
            return;
        }

        sessions.forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s;
            opt.textContent = s;
            ddl.appendChild(opt);
        });

        ddl.value = getActiveSession();

        ddl.classList.add("force-restyle");
        setTimeout(() => ddl.classList.remove("force-restyle"), 0);
    } catch (err) {
        console.error("[SESSION] dropdown load failed:", err);
    }
}

async function deleteSession(session) {
    if (!confirm(`Delete session '${session}' including all its videos?`)) return;

    try {
        await fetch(`/api/session/${encodeURIComponent(session)}`, {
            method: "DELETE",
        });

        if (getActiveSession() === session) {
            setActiveSession("default");
        }

        loadSessions();
        loadSessionDropdown();
        sidebarLoadSessions();
        sidebarSyncActiveLabel();
    } catch (err) {
        console.error("[SESSION] deleteSession failed:", err);
    }
}

async function saveCaptions() {
    const captionsEl = document.getElementById("captionsText");
    if (!captionsEl) return;

    const text = captionsEl.value || "";

    setStatus(
        "captionsStatus",
        "Saving captions into config.yml…",
        "working",
        false
    );

    try {
        const result = await jsonFetch("/api/save_captions", {
            method: "POST",
            body: JSON.stringify({
                text,
                session: getActiveSession(),
            }),
        });

        setStatus(
            "captionsStatus",
            `Saved ${result.count || 0} caption block(s).`,
            "success",
            true
        );

        await loadConfigAndYaml();
        await refreshHookScore();
        await refreshStoryFlowScore();
    } catch (err) {
        console.error(err);
        setStatus(
            "captionsStatus",
            `Error saving captions: ${err.message}`,
            "error",
            false
        );
    }
}

async function regenerateCaptionsFromClips() {
    // 🔒 Force-save all visible labels first
    document.querySelectorAll(".clip-label-input").forEach(i => i.blur());

    const captionsEl = document.getElementById("captionsText");
    if (!captionsEl) return;

    if (captionsEl.value.trim()) {
        const ok = confirm(
            "This will overwrite your current captions using labels first, then filenames.\n\nContinue?"
        );
        if (!ok) return;
    }

    // 🔔 Immediate intent
    setCaptionSource("filenames", "🟣 SOURCE: Filenames / Labels");
    setCaptionInlineStatus("Generating captions from filenames…", "info");

    try {
        // Backend updates config.yml
        await jsonFetch("/api/captions/from_filenames", {
            method: "POST",
            body: JSON.stringify({ session: getActiveSession() }),
        });

        // ✅ NOW load from YAML (this sets baseline)
        await loadCaptionsFromYaml({ preserveSource: true });

        flashElement(captionsEl);
        setStatus(
        "captionsStatus",
        "Captions regenerated from clips — previous captions replaced",
        "info"
        );


        setCaptionSource("filenames", "🟣 SOURCE: Filenames / Labels");
        setCaptionInlineStatus("Captions generated from filenames", "success");

    } catch (err) {
        console.error(err);
        setCaptionInlineStatus("Failed to generate captions", "error");
        setCaptionSource("filenames", "⚠ SOURCE: Filenames (failed)");
    }
}

// =============================================
// Clear caption comparison preview (Step 3)
// =============================================
function clearCaptionComparison() {
    const wrapper = document.getElementById("captionCompareWrapper");
    const body = document.getElementById("captionCompareBody");
    const chevron = document.getElementById("compareChevron");

    const oldEl = document.getElementById("compareOld");
    const newEl = document.getElementById("compareNew");

    if (oldEl) oldEl.textContent = "";
    if (newEl) newEl.textContent = "";

    if (body) body.classList.add("hidden");
    if (wrapper) wrapper.classList.add("collapsed");

    if (chevron) chevron.textContent = "▸";
}


function updateRewriteWarning() {
    const mode = document.querySelector('input[name="captionRewriteMode"]:checked')?.value;
    const warning = document.getElementById("rewriteWarning");
    if (!warning) return console.warn("rewriteWarning element missing");

    warning.classList.toggle("hidden", mode !== "rewrite");
}




// ================================
// Step 4: Overlay, timings, TTS, CTA, fg scale, music
// ================================
async function applyOverlay() {
    console.log("APPLY OVERLAY CLICKED")
  const styleSel = document.getElementById("overlayStyle");
  const statusEl = document.getElementById("overlayStatus");
  if (!styleSel || !statusEl) return;

  const style = styleSel.value || "travel_blog";

  // 👈 THIS decides if LLM rewrites or not
  const rewriteMode = document.querySelector('input[name="captionRewriteMode"]:checked')?.value || "visual";

  setStatus(
      "overlayStatus",
      rewriteMode === "rewrite"
          ? "Applying overlay + rewriting captions…"
          : "Applying visual overlay only…",
      "info"
  );

  try {
      await jsonFetch("/api/overlay", {
          method: "POST",
          body: JSON.stringify({
              style,
              session: getActiveSession(),
              rewrite: rewriteMode === "rewrite",   // ✔ correct boolean
          })
      });

    await loadConfigAndYaml();
    await loadCaptionsFromYaml();

    suppressNextPreview = true;
    await previewOverlay("fast");


    setStatus(
    "overlayStatus",
    rewriteMode === "rewrite"
        ? "Overlay applied + captions rewritten ✓"
        : "Overlay applied without rewriting ✓",
    "success"
);


  } catch (err) {
      console.error(err);
      setStatus("overlayStatus", "Failed to apply overlay.", "error");
  }
}

// confirm
document.getElementById("confirmRewriteBtn")?.addEventListener("click", async ()=>{
    document.getElementById("rewritePreviewModal").classList.add("hidden");
    applyOverlay(true); // calls overlay rewrite for real
});

// cancel
document.getElementById("cancelRewriteBtn")?.addEventListener("click", ()=>{
    document.getElementById("rewritePreviewModal").classList.add("hidden");
});

document
  .querySelector('input[name="captionRewriteMode"][value="visual"]')
  ?.addEventListener("change", clearOverlayWarning);

  document
  .querySelector('input[name="captionRewriteMode"][value="rewrite"]')
  ?.addEventListener("change", refreshHookScore);



async function previewRewrite() {
    const session = getActiveSession();
    const rewriteActive = document.querySelector('input[name="captionRewriteMode"][value="rewrite"]')?.checked;

    if (!rewriteActive) {
        alert("Enable Rewrite Mode first to preview changes.");
        return;
    }

    const res = await jsonFetch("/api/variants", {
        method: "POST",
        body: JSON.stringify({
            session,
            modes: { rewrite: true }     // preview uses rewrite only
        }),
    });

    const variants = res.variants || [];

    // UI panel or modal popup preview
    showRewritePreview(
    variants[0]?.text || "",
    variants[1]?.text || ""
    );
    }

function showRewritePreview(original, rewritten) {
    const msg = `Original:\n\n${original}\n\n---\n\nRewrite Preview:\n\n${rewritten}`;
    alert(msg);     // basic now — later we replace with nice UI popup
}

// Timings
async function applyTiming(smart) {
    const statusEl = document.getElementById("timingStatus");
    if (!statusEl) return;

    setStatus(
        "timingStatus",
        smart
            ? "Applying cinematic smart timings…"
            : "Applying standard timing…",
        "info"
    );

    try {
        await jsonFetch("/api/timings", {
            method: "POST",
            body: JSON.stringify({
                smart,
                session: getActiveSession(),
            }),
        });
        setStatus("timingStatus", "Timings updated.", "success", true);
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus(
            "timingStatus",
            `Error adjusting timings: ${err.message}`,
            "error"
        );
    }
}

function getRewriteMode(){
  return document.querySelector('input[name="captionRewriteMode"]:checked')?.value || "visual";
}


function getCaptionMode(){
  return document.getElementById("captionModeSelect")?.value || "all";
}

async function loadCaptionMode() {
    const session = getActiveSession();
    const resp = await fetch(`/api/config?session=${session}`);
    const data = await resp.json();

    const mode = data.config?.render?.captions_mode || "all";

    document.getElementById("captionModeSelect").value = mode;
}


async function saveCaptionMode() {
    const mode = getCaptionMode();
    const session = getActiveSession();

    setStatus("captionModeStatus", "Saving...", "info");

    try {
        const resp = await fetch("/api/captions_mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session, mode })
        });
        const data = await resp.json();

        if (data.status === "ok") {
            // 🔥 Confirm visually
            setStatus("captionModeStatus", `Saved → ${mode}`, "success");

            // 🔄 Update live state instantly — no manual refresh required anymore
            await loadConfigAndYaml();
            await loadCaptionMode();
            refreshAnalyses?.();   // optional if your UI uses it
        } else {
            setStatus("captionModeStatus", data.error || "Error saving", "error");
        }

    } catch (err) {
        console.error(err);
        setStatus("captionModeStatus", "Save failed", "error");
    }
}

// Layout Mode (TikTok / Classic)
async function loadLayoutFromYaml() {
    const sel = document.getElementById("layoutMode");
    if (!sel) return;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};
        const render = cfg.render || {};

        const mode = render.layout_mode || "tiktok";
        sel.value = mode;
    } catch (err) {
        console.error("Failed loading layout mode", err);
    }
}

async function saveLayoutMode() {
    const sel = document.getElementById("layoutMode");
    const status = document.getElementById("layoutStatus");
    if (!sel || !status) return;

    const mode = sel.value || "tiktok";
    setStatus("layoutStatus", "Saving layout mode…", "info");

    try {
        await jsonFetch("/api/layout", {
            method: "POST",
            body: JSON.stringify({
                mode,
                session: getActiveSession(),
            }),
        });

        setStatus("layoutStatus", "Layout saved!", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("layoutStatus", "Error saving layout: " + err.message, "error");
    }
}


// TTS
async function saveTtsSettings() {
    const enabledEl = document.getElementById("ttsEnabled");
    const voiceEl = document.getElementById("ttsVoice");
    const statusEl = document.getElementById("ttsStatus");
    if (!enabledEl || !voiceEl || !statusEl) return;

    setStatus("ttsStatus", "Saving TTS settings…", "info");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.tts = {
            enabled: enabledEl.checked,
            voice: voiceEl.value || "alloy",
        };

        if (cfg.render) {
            delete cfg.render.tts_enabled;
            delete cfg.render.tts_voice;
        }

        const yamlText = jsyaml.dump(cfg);

        await jsonFetch("/api/save_yaml", {
            method: "POST",
            body: JSON.stringify({
                yaml: yamlText,
                session: getActiveSession(),
            }),
        });

        setStatus("ttsStatus", "TTS settings saved.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("ttsStatus", `Error saving TTS: ${err.message}`, "error");
    }
}

// CTA
async function saveCtaSettings() {
    const enabledEl = document.getElementById("ctaEnabled");
    const textEl = document.getElementById("ctaText");
    const voiceoverEl = document.getElementById("ctaVoiceover");
    const statusEl = document.getElementById("ctaStatus");
    if (!enabledEl || !textEl || !voiceoverEl || !statusEl) return;

    setStatus("ctaStatus", "Saving CTA settings…", "info");

    try {
        await jsonFetch("/api/cta", {
            method: "POST",
            body: JSON.stringify({
                enabled: enabledEl.checked,
                text: textEl.value || "",
                voiceover: voiceoverEl.checked,
                session: getActiveSession(),
            }),
        });

        setStatus("ctaStatus", "CTA settings saved.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("ctaStatus", `Error saving CTA: ${err.message}`, "error");
    }
}

// Music: load available tracks (global)
async function loadMusicTracks() {
    const sel = document.getElementById("musicFile");
    if (!sel) return;

    sel.innerHTML = `<option value="">– No music –</option>`;

    try {
        const data = await jsonFetch("/api/music_list");
        const files = data.files || [];
        files.forEach((f) => {
            const opt = document.createElement("option");
            opt.value = f;
            opt.textContent = f;
            sel.appendChild(opt);
        });
    } catch (err) {
        console.error("Music list load failed", err);
    }
}

// Music: read settings from YAML
async function loadMusicSettingsFromYaml() {
    const enabledEl = document.getElementById("musicEnabled");
    const fileEl = document.getElementById("musicFile");
    const volEl = document.getElementById("musicVolume");
    const volLbl = document.getElementById("musicVolumeLabel");
    if (!enabledEl || !fileEl || !volEl || !volLbl) return;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        const music = cfg.music || {};
        const render = cfg.render || {};

        const enabled =
            music.enabled ??
            render.music_enabled ??
            false;

        const file =
            music.file ??
            render.music_file ??
            "";

        const volume =
            music.volume ??
            render.music_volume ??
            0.25;

        enabledEl.checked = !!enabled;
        fileEl.value = file;
        volEl.value = volume;
        volLbl.textContent = Number(volume).toFixed(2);
    } catch (err) {
        console.error("Music settings load failed", err);
    }
}

// Music: save settings into YAML
async function saveMusicSettings() {
    const enabledEl = document.getElementById("musicEnabled");
    const fileEl = document.getElementById("musicFile");
    const volEl = document.getElementById("musicVolume");
    const statusEl = document.getElementById("musicStatus");

    if (!enabledEl || !fileEl || !volEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const file = fileEl.value || "";
    const volume = parseFloat(volEl.value || "0.25");

    setStatus("musicStatus", "Saving music settings…", "info");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.music = { enabled, file, volume };

        if (cfg.render) {
            delete cfg.render.music_enabled;
            delete cfg.render.music_file;
            delete cfg.render.music_volume;
        }

        const yamlText = jsyaml.dump(cfg);

        await jsonFetch("/api/save_yaml", {
            method: "POST",
            body: JSON.stringify({
                yaml: yamlText,
                session: getActiveSession(),
            }),
        });

        setStatus("musicStatus", "Music settings saved.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("musicStatus", "Error saving music: " + err.message, "error");
    }
}

// Music volume label live update
function initMusicVolumeSlider() {
    const slider = document.getElementById("musicVolume");
    const lbl = document.getElementById("musicVolumeLabel");
    if (!slider || !lbl) return;

    slider.addEventListener("input", () => {
        lbl.textContent = Number(slider.value).toFixed(2);
    });
}

// Auto Caption Style Selector
function autoSelectCaptionStyle(selectedMode) {
    const layoutSelect = document.getElementById("layoutMode");
    if (!layoutSelect) return;

    const isTikTok = selectedMode === "standard";

    layoutSelect.value = isTikTok ? "tiktok" : "classic";

    setStatus(
        "captionStyleStatus",
        isTikTok
            ? "🟣 Auto-set caption layout to TikTok Style"
            : "🔵 Auto-set caption layout to Classic Style",
        "info"
    );
}

// Preview music
function initMusicPreview() {
    const btn = document.getElementById("musicPreviewBtn");
    const select = document.getElementById("musicFile");
    const status = document.getElementById("musicStatus");

    if (!btn || !select || !status) return;

    select.addEventListener("change", () => {
        if (previewAudio) {
            previewAudio.pause();
            previewAudio.currentTime = 0;
        }
        previewAudio = null;
        previewPlaying = false;
        btn.textContent = "▶ Preview";
        status.textContent = "";
    });

    btn.addEventListener("click", () => {
        const file = select.value;

        if (!file) {
            alert("Select a music track first.");
            return;
        }

        if (!previewAudio) {
            previewAudio = new Audio(`/api/music_file/${file}`);
            previewAudio.volume = 0.8;

            previewAudio.onplay = () => {
                previewPlaying = true;
                btn.textContent = "⏸ Pause";
                status.textContent = `🎵 Now Playing: ${file}`;
            };

            previewAudio.onpause = () => {
                previewPlaying = false;
                btn.textContent = "▶ Preview";
                status.textContent = `⏸ Paused: ${file}`;
            };

            previewAudio.onended = () => {
                previewPlaying = false;
                btn.textContent = "▶ Preview";
                status.textContent = "";
            };
        }

        if (previewAudio.paused) {
            previewAudio.play();
        } else {
            previewAudio.pause();
        }
    });
}

async function saveYamlToServer() {
    const text = document.getElementById("yamlText").value;
    return jsonFetch("/api/save_yaml", {
        method: "POST",
        body: JSON.stringify({
            session: getActiveSession(),
            yaml: text,
        }),
    });
}

// Foreground scale
async function saveFgScale() {
    const auto = document.getElementById("autoFgScale").checked;
    const fg = parseFloat(document.getElementById("fgScale").value || "1.0");

    setStatus("fgStatus", "Saving foreground scale…", "info");

    try {
        let yamlObj = jsyaml.load(document.getElementById("yamlText").value) || {};
        yamlObj.render = yamlObj.render || {};

        yamlObj.render.fgscale_mode = auto ? "auto" : "manual";
        yamlObj.render.fgscale = auto ? null : fg;

        document.getElementById("yamlText").value = jsyaml.dump(yamlObj);

        await saveYamlToServer();

        await jsonFetch("/api/fgscale", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                fgscale_mode: auto ? "auto" : "manual",
                fgscale: auto ? null : fg,
            }),
        });



        setStatus("fgStatus", "Foreground scale saved.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("fgStatus", "Error saving scale: " + err.message, "error");
    }
}

function initFgScaleSlider() {
    const range = document.getElementById("fgScale");
    const label = document.getElementById("fgScaleValue");
    if (!range || !label) return;

    label.textContent = range.value;
    range.addEventListener("input", () => {
        label.textContent = range.value;
    });
}

function initFgScaleUI() {
    const autoFgScaleEl = document.getElementById("autoFgScale");
    const manualFgContainer = document.getElementById("manualFgScaleContainer");
    const fgScaleEl = document.getElementById("fgScale");
    const fgScaleValue = document.getElementById("fgScaleValue");

    if (!autoFgScaleEl || !manualFgContainer || !fgScaleEl || !fgScaleValue) {
        return;
    }

    function updateFgScaleUI() {
        if (autoFgScaleEl.checked) {
            manualFgContainer.classList.add("hidden");
        } else {
            manualFgContainer.classList.remove("hidden");
        }
    }

    autoFgScaleEl.addEventListener("change", updateFgScaleUI);

    fgScaleEl.addEventListener("input", () => {
        fgScaleValue.textContent = fgScaleEl.value;
    });

    updateFgScaleUI();
}

async function pollExportStatus(taskId) {
    return new Promise((resolve, reject) => {
        const interval = setInterval(async () => {

            const res = await fetch(`/api/export/status?task_id=${taskId}`);
            const data = await res.json();

            const statusEl = document.getElementById("exportStatus");
            const cancelBtn = document.getElementById("cancelExportBtn");
            const exportBtn = document.getElementById("exportBtn");

            // ------------------------------
            // SUCCESS
            // ------------------------------
            if (data.status === "done") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) {
                  box.innerHTML = `
                      <a id="downloadLink"
                        href="${data.download_url}"
                        class="btn-download"
                        target="_blank"
                        download>
                          ⬇️ Download Export
                      </a>
                  `;
              }

              resolve(data.download_url);
              return;
          }


            // ------------------------------
            // CANCELLED
            // ------------------------------
            if (data.status === "cancelled") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) box.innerHTML = "";

              statusEl.textContent = "";
              statusEl.className = "status-text";

              reject("cancelled");
              return;
          }


            // ------------------------------
            // ERROR
            // ------------------------------
            if (data.status === "error") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) box.innerHTML = "";

              statusEl.textContent = "Export failed.";
              statusEl.className = "status-text error";

              reject(data.error || "error");
              return;
          }



        }, 1500); // slightly faster polling = snappier UI
    });
}

function showDownloadButton(url) {
    const area = document.getElementById("downloadArea");
    area.innerHTML = `
        <button class="btn-download" onclick="window.open('${url}', '_blank')">
            ⬇️ Download Video
        </button>
    `;
}


// ================================
// Step 5: EXPORT (Async)
// ================================
async function exportVideo() {
    const btn = document.getElementById("exportBtn");
    const cancelBtn = document.getElementById("cancelExportBtn");
    const statusEl = document.getElementById("exportStatus");

    // 🧼 Clear old download button immediately
    const box = document.getElementById("downloadArea");
    if (box) box.innerHTML = "";

    btn.disabled = true;
    cancelBtn.classList.remove("hidden");
    statusEl.textContent = "⏳ Rendering… you can leave this page.";

    try {
    const startResp = await jsonFetch("/api/export/start", {
        method: "POST",
        body: JSON.stringify({ session: getActiveSession() })
    });

    const taskId = startResp.task_id;
    ACTIVE_EXPORT_TASK = taskId;

    const downloadUrl = await pollExportStatus(taskId);

    cancelBtn.classList.add("hidden");

    // ❗ Remove the Download Video <a> link
    // OLD:
    // statusEl.innerHTML = `
    //    ✅ Export complete<br>
    //    <a href="${downloadUrl}" target="_blank">Download Video</a>
    // `;

    // NEW:
    statusEl.textContent = "✅ Export complete";

    // 🔥 Show your nice styled button
    showDownloadButton(downloadUrl);

} catch (err) {
    statusEl.textContent = "❌ " + err;
} finally {
    btn.disabled = false;
    ACTIVE_EXPORT_TASK = null;
    cancelBtn.classList.add("hidden");
}
}


async function loadRewriteMode() {
    const data = await jsonFetch(`/api/config?session=${getActiveSession()}`);
    const mode = data.config?.render?.rewrite_mode || "visual";

    const radio = document.querySelector(`input[name="captionRewriteMode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    updateRewriteWarning();  // Reflect state visually
}



// ================================
// Chat
// ================================
async function sendChat() {
    const input = document.getElementById("chatInput");
    const output = document.getElementById("chatOutput");
    const btn = document.getElementById("chatSendBtn");
    if (!input || !output || !btn) return;

    const msg = (input.value || "").trim();
    if (!msg) return;

    btn.disabled = true;
    output.textContent = "Thinking…";

    try {
        const data = await jsonFetch("/api/chat", {
            method: "POST",
            body: JSON.stringify({ message: msg }),
        });
        output.textContent = data.reply || "(no reply)";
    } catch (err) {
        console.error(err);
        output.textContent = `Error: ${err.message}`;
    } finally {
        btn.disabled = false;
    }
}

// ================================
// Init wiring
// ================================
document.addEventListener("DOMContentLoaded", async () => {
    // Load stored session
    try {
        const stored = localStorage.getItem("activeSession");
        ACTIVE_SESSION = sanitizeSessionName(stored || "default");
    } catch {
        ACTIVE_SESSION = "default";
    }

    // Sync labels
    updateSessionLabels();
    sidebarSyncActiveLabel();

    // YAML preview toggle
    const toggleBtn = document.getElementById("toggleYamlPreviewBtn");
    const previewBox = document.getElementById("yamlPreviewContainer");
    if (toggleBtn && previewBox) {
        toggleBtn.addEventListener("click", () => {
            const isOpen = previewBox.classList.toggle("open");
            toggleBtn.textContent = isOpen
                ? "▲ Hide Parsed Preview"
                : "▼ Show Parsed Preview";
        });
    }


    document.addEventListener("keydown", (e) => {
        if (
            e.key === "Enter" &&
            e.target.classList.contains("clip-label-input")
        ) {
            e.preventDefault();
            e.target.blur(); // triggers save
        }
    });



  document.getElementById("improveStoryFlowBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("improveStoryFlowBtn");
    const status = document.getElementById("storyFlowStatus");

    if (btn) btn.disabled = true;
    if (status) status.textContent = "Improving story flow…";

    try {
      const res = await jsonFetch("/api/story_flow_improve", {
        method: "POST",
        body: JSON.stringify({ session: getActiveSession() }),
      });

      if (res.updated) {
        if (status) status.textContent = "Story flow improved ✓";

        // 🔥 UX IMPROVEMENT: auto-sync everything
        await loadCaptionsFromYaml();
        await loadConfigAndYaml();
        await refreshHookScore();
        await refreshStoryFlowScore();
      } else {
        if (status) status.textContent = res.reason || "No changes made.";
      }
    } catch (err) {
      console.error(err);
      if (status) status.textContent = "Failed to improve story flow.";
    } finally {
      if (btn) btn.disabled = false;
    }
  });


    // MOBILE SESSION PANEL
    const mobileSessionBtn = document.getElementById("mobileSessionBtn");
    const sidebarPanel = document.getElementById("sidebarSessionCard");
    const mobileCloseBtn = document.getElementById("mobileCloseSessionBtn");

    if (mobileSessionBtn && sidebarPanel) {
        mobileSessionBtn.addEventListener("click", () => {
            sidebarPanel.classList.add("open");
        });

        if (mobileCloseBtn) {
            mobileCloseBtn.addEventListener("click", () => {
                sidebarPanel.classList.remove("open");
            });
        }

        // Single outside-click handler
        document.addEventListener("click", (e) => {
            if (!sidebarPanel.classList.contains("open")) return;

            const clickedInside =
                sidebarPanel.contains(e.target) ||
                e.target === mobileSessionBtn;
            if (!clickedInside) {
                sidebarPanel.classList.remove("open");
            }
        });
    }

    // SIDEBAR SESSION BUTTONS
    document.getElementById("sidebarCreateBtn")?.addEventListener("click", async () => {
        const input = document.getElementById("sidebarNewSessionInput");
        if (!input) return;

        const raw = input.value.trim();
        if (!raw) {
            input.classList.add("shake");
            setTimeout(() => input.classList.remove("shake"), 300);
            return;
        }

        const safe = sanitizeSessionName(raw);

        // 👇 Create session on backend
        await fetch(`/api/session/${safe}`, { method: "POST" });

        // 👇 Sync UI immediately
        setActiveSession(safe);
        await sidebarLoadSessions();
        await loadSessionDropdown();
        sidebarSyncActiveLabel();

        sidebarToast(`Created & switched to “${safe}”`);
        input.value = "";
    });


    document.getElementById("sidebarSwitchBtn")?.addEventListener("click", () => {
        const ddl = document.getElementById("sidebarSessionDropdown");
        if (!ddl || !ddl.value) return;

        setActiveSession(ddl.value);
        sidebarSyncActiveLabel();
        sidebarToast(`Switched to “${ddl.value}”`);
    });

    document.getElementById("cancelExportBtn")?.addEventListener("click", async () => {
    if (!ACTIVE_EXPORT_TASK) return;

    const statusEl = document.getElementById("exportStatus");
    const cancelBtn = document.getElementById("cancelExportBtn");

    statusEl.textContent = "⛔ Canceling export…";

    await jsonFetch("/api/export/cancel", {
        method: "POST",
        body: JSON.stringify({ task_id: ACTIVE_EXPORT_TASK })
    });

    // Do NOT hide the button yet — wait for poller to confirm
    // Only clear your local task ID
    // UI update happens inside pollExportStatus()
    ACTIVE_EXPORT_TASK = null;
});



    document.getElementById("sidebarDeleteBtn")?.addEventListener("click", async () => {
        const session = getActiveSession();

        if (session === "default") {
            sidebarToast("Cannot delete default");
            return;
        }

        const ok = confirm(
            `Delete session "${session}"?\n\nThis deletes ALL videos, config.yml, and analysis for that session.`
        );
        if (!ok) {
            sidebarToast("Deletion cancelled");
            return;
        }

        await fetch(`/api/session/${session}`, { method: "DELETE" });

        setActiveSession("default");
        sidebarLoadSessions();
        sidebarSyncActiveLabel();

        sidebarToast(`Deleted session “${session}”`);
    });

    // Legacy delete buttons in other card (if present)
    document.addEventListener("click", (e) => {
        const btn = e.target.closest(".deleteSessionBtn");
        if (!btn) return;
        const session = btn.dataset.session;
        if (session) deleteSession(session);
    });

    // Legacy top session bar hooks (safe no-op if missing)
    const headerLabel = document.getElementById("activeSessionLabel");
    const headerInput = document.getElementById("sessionInput");
    if (headerLabel) headerLabel.textContent = getActiveSession();
    if (headerInput) headerInput.value = getActiveSession();

    document.getElementById("setSessionBtn")?.addEventListener("click", () => {
        const val = document.getElementById("sessionInput")?.value || "";
        setActiveSession(val);
        loadSessionDropdown();
    });

    // Attach button handler
    document.getElementById("saveCaptionModeBtn")
        .addEventListener("click", saveCaptionMode);

    document.getElementById("refreshSessionsBtn")?.addEventListener("click", loadSessions);

    // Stepper & logs
    initStepper();
    startStatusLogPolling();

    // ================================
    // Live Log: disable auto-scroll on user interaction
    // ================================
    const logEl = document.getElementById("statusLog");
    const autoScrollToggle = document.getElementById("autoScrollLogs");

    if (logEl && autoScrollToggle) {
    const disableAutoScroll = () => {
        autoScrollToggle.checked = false;
    };

    logEl.addEventListener("wheel", disableAutoScroll);
    logEl.addEventListener("mousedown", disableAutoScroll);
    logEl.addEventListener("touchstart", disableAutoScroll); // mobile-safe
    }



    // Sliders / fg-scale UI
    initFgScaleSlider();
    initFgScaleUI();
    initMusicVolumeSlider();

    // Music preview
    initMusicPreview();

    // Upload UI & manager
    initUploadUI();
    loadUploadManager();

    setCaptionInlineStatus(
        "Labels loaded. Regenerate captions to apply them.",
        "info"
        );


    // Session lists
    loadSessions();
    loadSessionDropdown();
    sidebarLoadSessions();

    // Music list + settings
    loadMusicTracks();
    loadMusicSettingsFromYaml();

    // YAML + analyses
    refreshAnalyses();
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();   // 🔥 ADD THIS
    loadLayoutFromYaml();


    // Load caption + rewrite mode from YAML/session
    loadCaptionMode();

    // ================================
    // Load Caption + Rewrite Mode From YAML
    // ================================
    await loadRewriteMode();     // selects radio based on config.yml
    updateRewriteWarning();      // show/hide banner accordingly

    // Activate radio toggles
    document.querySelectorAll('input[name="captionRewriteMode"]').forEach(el => {
        el.addEventListener("change", updateRewriteWarning);
    });


    // Accordion toggles
    document.querySelectorAll(".acc-header").forEach((btn) => {
        btn.addEventListener("click", () => {
            const sec = btn.parentElement;
            sec.classList.toggle("open");
        });
    });

    // Export-mode → auto caption layout
    document.querySelectorAll('input[name="exportMode"]').forEach((radio) => {
        radio.addEventListener("change", (e) => {
            autoSelectCaptionStyle(e.target.value);
        });
    });

    // Buttons / actions
    document.getElementById("analyzeBtn")?.addEventListener("click", analyzeClips);
    document
        .getElementById("refreshAnalysesBtn")
        ?.addEventListener("click", refreshAnalyses);

    document
        .getElementById("generateYamlBtn")
        ?.addEventListener("click", generateYaml);
    document
        .getElementById("refreshYamlBtn")
        ?.addEventListener("click", loadConfigAndYaml);
    document.getElementById("saveYamlBtn")?.addEventListener("click", saveYaml);

    document
        .getElementById("loadCaptionsFromYamlBtn")
        ?.addEventListener("click", loadCaptionsFromYaml);
    document.getElementById("saveCaptionsBtn")?.addEventListener("click", saveCaptions);

    document.getElementById("applyOverlayBtn")?.addEventListener("click", applyOverlay);
    document
        .getElementById("applyStandardTimingBtn")
        ?.addEventListener("click", () => applyTiming(false));
    document
        .getElementById("applyCinematicTimingBtn")
        ?.addEventListener("click", () => applyTiming(true));

    document.getElementById("saveOverlayStyle")?.addEventListener("click", async () => {
    const style = document.getElementById("overlayStyle")?.value || "default";

    setStatus(
        "overlayStyleStatus",
        `✔ Overlay style selected (${style})`,
        "success"
    );

    // 🔥 Preview immediately
    await previewOverlay("fast");
    });

    document.getElementById("captionsText")?.addEventListener("input", () => {
    document.getElementById("compareOld").textContent = lastSavedCaptionsText || "";
    document.getElementById("compareNew").textContent =
        document.getElementById("captionsText").value;

    toggleCaptionCompare(true);
});




    document.getElementById("saveTtsBtn")?.addEventListener("click", saveTtsSettings);
    document.getElementById("saveCtaBtn")?.addEventListener("click", saveCtaSettings);
    document.getElementById("saveFgScaleBtn")?.addEventListener("click", saveFgScale);
    document.getElementById("saveLayoutBtn")?.addEventListener("click", saveLayoutMode);
    document.getElementById("saveMusicBtn")?.addEventListener("click", saveMusicSettings);

    document.getElementById("exportBtn")?.addEventListener("click", exportVideo);
    document.getElementById("chatSendBtn")?.addEventListener("click", sendChat);
    document.getElementById("improveHookBtn")?.addEventListener("click", improveHook);
    // PREVIEW REWRITE — must be inside DOMContentLoaded so button exists
    document.getElementById("previewRewriteBtn")?.addEventListener("click", () => {
        console.log("Preview Rewrite CLICKED"); // Debug check
        previewRewrite();
    });


    // BUTTON EVENTS
    document.getElementById("previewFast")?.addEventListener("click", () => previewOverlay("fast"));
    document.getElementById("previewFull")?.addEventListener("click", () => previewOverlay("full"));
    document.getElementById("previewStyleBtn")?.addEventListener("click", () => previewOverlay("fast")); // button you already have


    // Auto-refresh preview when style changes
    const overlayStyleSelect = document.getElementById("overlayStyle");
    if (overlayStyleSelect) {
        overlayStyleSelect.addEventListener("change", () => {
            if (suppressNextPreview) {
                suppressNextPreview = false;
                return;
            }
            previewOverlay("fast");
        });
    }

// Watch live typing unlock rewrite mode
document.getElementById("captionsText")?.addEventListener("input", updateRewriteModeAvailability);


document.getElementById("captionsText")?.addEventListener("input", () => {
  const el = document.getElementById("captionInlineStatus");
  if (el) el.classList.add("hidden");
});


// Generate variants (unchanged)
document.getElementById("generateVariantsBtn")?.addEventListener("click", generateCaptionVariants);

// Run once after load
updateRewriteModeAvailability();



    // ================================
    // Step 4 Rewrite Mode Init
    // ================================
    addStepEnterHandler(4, async () => {
        console.log("STEP 4 OPEN → initializing rewrite controls");

        await loadCaptionMode();   // reload caption mode from YAML
        await loadRewriteMode();   // reload rewrite mode from YAML
        updateRewriteWarning();

        document.querySelectorAll('input[name="captionRewriteMode"]').forEach(el => {
            el.removeEventListener("change", updateRewriteWarning);
            el.addEventListener("change", updateRewriteWarning);
        });
    });


    // Legacy quick-switch for sessions (top bar)
    document.getElementById("switchSessionBtn")?.addEventListener("click", () => {
        const ddl = document.getElementById("sessionDropdown");
        if (!ddl) return;

        const selected = ddl.value || "default";

        setActiveSession(selected);
        loadSessionDropdown();

        const label = document.getElementById("activeSessionLabel");
        if (label) {
            label.classList.add("session-pulse");
            setTimeout(() => label.classList.remove("session-pulse"), 800);
        }

        const ddlWrapper = ddl.closest(".select-wrapper");
        if (ddlWrapper) {
            ddlWrapper.classList.add("session-ddl-pulse");
            setTimeout(
                () => ddlWrapper.classList.remove("session-ddl-pulse"),
                600
            );
        }

        showSessionToast(`Switched to “${selected}”`);
    });
});
