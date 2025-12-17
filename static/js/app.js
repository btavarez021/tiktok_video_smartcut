// ================================
// Variables
// ================================
let previewAudio = null;
let previewPlaying = false;

// 🔵 Active session (hotel / batch)
let ACTIVE_SESSION = "default";

let ACTIVE_EXPORT_TASK = null;


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

// ================================
// Utility helpers
// ================================

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
        if (!el) return;
        el.textContent = log.join("\n");
        el.scrollTop = el.scrollHeight;
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

        renderUploadList("rawUploads", data.raw, "raw");
        renderUploadList("processedUploads", data.processed, "processed");
    } catch (e) {
        console.error("UploadManager error:", e);
    }
}

function renderUploadList(elementId, items, kind) {
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

            return `
        <div class="upload-item">
            <div class="file-info">
                <strong>${session}/${file}</strong>
            </div>

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

    // 🚫 Do not score if captions are not visible or empty
    if (!captionsEl || !captionsEl.value.trim()) {
        return;
    }

    const scoreEl = document.getElementById("hookScoreValue");
    const reasonsEl = document.getElementById("hookScoreReasons");
    const hookEl = document.getElementById("hookScoreHook");
    const statusEl = document.getElementById("hookScoreStatus");

    if (!scoreEl || !reasonsEl || !hookEl) return;

    try {
        if (statusEl) statusEl.textContent = "Checking hook…";

        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/hook_score?session=${session}`);

        scoreEl.textContent = `${data.score ?? 0}/100`;
        hookEl.textContent = data.hook || "(no first caption yet)";

        const reasons = data.reasons || [];
        reasonsEl.innerHTML = reasons.length
            ? reasons.map(r => `<li>${r}</li>`).join("")
            : `<li>Looks solid ✅</li>`;

        if (statusEl) statusEl.textContent = "";
    } catch (err) {
        if (statusEl) statusEl.textContent = "Hook score unavailable.";
        console.error("Hook score error:", err);
    }
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

        // Reload captions UI and YAML preview so user sees change
        await loadCaptionsFromYaml();
        await loadConfigAndYaml();
        await refreshHookScore();

        if (statusEl) statusEl.textContent = "Hook improved ✅";
        setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 1500);
    } catch (err) {
        console.error(err);
        if (statusEl) statusEl.textContent = "Failed to improve hook.";
    } finally {
        btn.disabled = false;
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

async function loadCaptionsFromYaml() {
    const statusEl = document.getElementById("captionsStatus");
    const captionsEl = document.getElementById("captionsText");
    if (!statusEl || !captionsEl) return;

    setStatus("captionsStatus", "Loading captions…", "info");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};
        captionsEl.value = buildCaptionsFromConfig(cfg);
        await refreshHookScore();
        setStatus("captionsStatus", "Captions loaded.", "success");
    } catch (err) {
        console.error(err);
        setStatus(
            "captionsStatus",
            `Error loading captions: ${err.message}`,
            "error"
        );
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

// ================================
// Step 4: Overlay, timings, TTS, CTA, fg scale, music
// ================================
async function applyOverlay() {
    const styleSel = document.getElementById("overlayStyle");
    const statusEl = document.getElementById("overlayStatus");
    if (!styleSel || !statusEl) return;

    const style = styleSel.value || "travel_blog";

    console.log("[OVERLAY] Applying:", style, "Session:", getActiveSession());

    setStatus("overlayStatus", `Applying overlay style “${style}”…`, "info");

    try {
        const result = await jsonFetch("/api/overlay", {
            method: "POST",
            body: JSON.stringify({
                style,
                session: getActiveSession(),
            }),
        });

        console.log("[OVERLAY RESULT]", result);

        setStatus("overlayStatus", "Overlay applied.", "success", true);

        // ALWAYS force-refresh config
        await loadConfigAndYaml();

    } catch (err) {
        console.error(err);
        setStatus(
            "overlayStatus",
            `Error applying overlay: ${err.message}`,
            "error"
        );
    }
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
document.addEventListener("DOMContentLoaded", () => {
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
        await fetch(`/api/session/${safe}`, { method: "POST" });

        setActiveSession(safe);
        sidebarLoadSessions();
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

    document.getElementById("refreshSessionsBtn")?.addEventListener("click", loadSessions);

    // Stepper & logs
    initStepper();
    startStatusLogPolling();

    // Sliders / fg-scale UI
    initFgScaleSlider();
    initFgScaleUI();
    initMusicVolumeSlider();

    // Music preview
    initMusicPreview();

    // Upload UI & manager
    initUploadUI();
    loadUploadManager();

    // Session lists
    loadSessions();
    loadSessionDropdown();
    sidebarLoadSessions();

    // Music list + settings
    loadMusicTracks();
    loadMusicSettingsFromYaml();

    // YAML + analyses
    refreshAnalyses();
    loadConfigAndYaml();
    loadLayoutFromYaml();

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

    document.getElementById("saveTtsBtn")?.addEventListener("click", saveTtsSettings);
    document.getElementById("saveCtaBtn")?.addEventListener("click", saveCtaSettings);
    document.getElementById("saveFgScaleBtn")?.addEventListener("click", saveFgScale);
    document.getElementById("saveLayoutBtn")?.addEventListener("click", saveLayoutMode);
    document.getElementById("saveMusicBtn")?.addEventListener("click", saveMusicSettings);

    document.getElementById("exportBtn")?.addEventListener("click", exportVideo);
    document.getElementById("chatSendBtn")?.addEventListener("click", sendChat);
    document.getElementById("improveHookBtn")?.addEventListener("click", improveHook);


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
