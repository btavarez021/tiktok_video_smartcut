// ================================
// Variables
// ================================
let previewAudio = null;
let previewPlaying = false;

let workingClipOrder = [];
let clipOrderDirty = false;


// 🔵 Active session (hotel / batch)
let ACTIVE_SESSION = "default";

let ACTIVE_EXPORT_TASK = null;

let rewriteCommitted = false;


let suppressNextPreview = false;

let lastSavedCaptionsText = "";

let workingCaptionsText = "";

let rewritePending = false;

let isInRewriteReview = false;

function debounce(fn, wait = 350) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

function syncTtsUIState() {
  const enabled = document.getElementById("ttsEnabled")?.checked;
  const voiceSelect = document.getElementById("ttsVoice");

  if (!voiceSelect) return;

  voiceSelect.disabled = !enabled;
  voiceSelect.style.opacity = enabled ? "1" : "0.5";
}

function syncFgScaleUI() {
    const autoEl = document.getElementById("autoFgScale");
    const manualContainer = document.getElementById("manualFgScaleContainer");

    if (!autoEl || !manualContainer) return;

    manualContainer.style.display = autoEl.checked ? "none" : "block";
}

function animateSessionGlow() {
  const tags = document.querySelectorAll(".active-session-tag");

  if (!tags.length) {
    console.warn("[SESSION] No active-session-tag found");
    return;
  }

  tags.forEach(tag => {
    tag.classList.remove("session-glow");
    void tag.offsetWidth; // force reflow
    tag.classList.add("session-glow");
  });
}



function syncMusicUIState() {
  const enabled = document.getElementById("musicEnabled")?.checked;
  const hint = document.getElementById("musicDisabledHint");

  if (!hint) return;

  hint.style.display = enabled ? "none" : "block";
}


function renderCaptionView() {

  const box = document.getElementById("captionsText");
  if (!box) return;

  if (captionViewMode === "original") {
    box.value = lastSavedCaptionsText || "";
    box.readOnly = true;
  }
  else if (captionViewMode === "rewritten") {
    box.value = workingCaptionsText || lastSavedCaptionsText || "";
    box.readOnly = false;
  }
  else {
    // diff
    box.value = "";
    box.readOnly = true;
  }
}


function syncCtaUIState() {
    const enabled = document.getElementById("ctaEnabled")?.checked;
    const textEl = document.getElementById("ctaText");
    const voiceEl = document.getElementById("ctaVoiceover");
    const rowEl = document.getElementById("ctaRow");

    if (!textEl || !voiceEl || !rowEl) return;

    textEl.disabled = !enabled;
    voiceEl.disabled = !enabled;

    rowEl.style.opacity = enabled ? "1" : "0.5";
}


function showAutoSaveStatus(id, message = "Saved ✓", timeout = 1500) {
    const el = document.getElementById(id);
    if (!el) return;

    el.textContent = message;
    el.className = "status-text status-success subtle";

    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(() => {
        el.textContent = "";
    }, timeout);
}

// ================================
// OVERLAY STYLE — Save (SAFE)
// ================================
async function saveOverlayStyle({ silent = false } = {}) {
    const selectEl = document.getElementById("overlayStyle");
    const statusEl = document.getElementById("overlayStyleStatus");

    if (!selectEl || !statusEl) return;

    const style = selectEl.value;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.render = cfg.render || {};
        cfg.render.overlay_style = style;

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        // 🔑 THIS IS THE FIX
        await loadConfigAndYaml();

        if (!silent) {
            setStatus("overlayStyleStatus", "Style saved ✓", "success");
        } else {
            showAutoSaveStatus("overlayStyleStatus");
        }

    } catch (err) {
        console.error(err);
        setStatus("overlayStyleStatus", "Failed to save style", "error");
    }
}




function showPendingRewrite() {
  document.getElementById("pendingRewriteBadge")?.classList.remove("hidden");
}

function clearPendingRewrite() {
  document.getElementById("pendingRewriteBadge")?.classList.add("hidden");
}


function lockRewriteDecision() {
  const bar = document.getElementById("rewriteDecisionBar");
  if (!bar) return;

  bar.querySelectorAll("button").forEach(btn => {
    btn.disabled = true;
  });
}


function proposeRewrite(newText, sourceLabel = "Rewrite ready", source = "step3") {

    rewriteCommitted = false;
  const original = lastSavedCaptionsText || "";
  const proposed = (newText || "").trim();
  if (!proposed) return;

  workingCaptionsText = proposed;

  // Step 3 diff
  renderStep3Diff(original, proposed);

    // Only auto-scroll if coming from Step 3
    if (source === "step3") {
    focusCaptionChanges();
    }


  // Step 4 diff
  renderStep4Diff(original, proposed);

  // Switch UI into review mode
  captionViewMode = "diff";

    isInRewriteReview = true;
  rewritePending = true;
  
  enterRewriteReviewMode();
  showPendingRewrite();

  setStatus("overlayStatus", `${sourceLabel} — review & accept or reject`, "info");
}


function enterRewriteReviewMode() {
  console.log("🔥 ENTERED REWRITE REVIEW MODE");

  if (!rewritePending) return;

  // Decision bar
  const bar = document.getElementById("rewriteDecisionBar");
  bar?.classList.remove("hidden");
  bar?.querySelectorAll("button").forEach(btn => btn.disabled = false);

  // Diff UI
  document.getElementById("captionDiffHeader")?.classList.remove("hidden");
  document.getElementById("step4CaptionScroll")?.classList.remove("hidden");

  // Pending badge
  document.getElementById("pendingRewriteBadge")?.classList.remove("hidden");

  // Force Step-4 diff visible
  captionViewMode = "diff";
  renderCaptionView();
  syncCaptionToggleUI();
}

function hardClearRewriteUI() {
  // Kill rewrite state
  rewritePending = false;
  isInRewriteReview = false;
  rewriteCommitted = true;

  // Hide rewrite UI
  clearPendingRewrite();
  exitRewriteReviewMode();

  // Kill warning overlays that look like rewrite UI
  clearOverlayWarning();
  document.getElementById("rewriteWarning")?.classList.add("hidden");

  // Force normal caption mode
  captionViewMode = "rewritten";
  renderCaptionView();
  syncCaptionToggleUI();
}


function exitRewriteReviewMode() {
  rewritePending = false;
  isInRewriteReview = false;
  captionViewMode = "rewritten";   // 🔥 force exit diff mode

  document.getElementById("rewriteDecisionBar")?.classList.add("hidden");
  document.getElementById("captionDiffHeader")?.classList.add("hidden");
  document.getElementById("step4CaptionScroll")?.classList.add("hidden");
  document.getElementById("pendingRewriteBadge")?.classList.add("hidden");
}



function renderVariantCard(num, tone, text, score, cardId) {
  let badge = "";

  if (score !== null && score !== undefined) {
    if (score >= 85) badge = `<span class="hookBadge great">🔥 ${score}</span>`;
    else if (score >= 70) badge = `<span class="hookBadge ok">⭐ ${score}</span>`;
    else badge = `<span class="hookBadge weak">⚠ ${score}</span>`;
  }

  return `
    <div class="variantCard" id="${cardId}">
      <div class="variantHeader">
        <h4>Version ${num}</h4>
        ${badge}
      </div>

      ${tone ? `<div class="variantTone">${tone}</div>` : ""}

      <pre style="white-space:pre-wrap">${text}</pre>

      <button class="btn small" onclick="applyCaptionVariant(\`${text.replace(/`/g,"\\`")}\`)">
        Use This
      </button>
    </div>
  `;
}

function updateVariantStoryScore(id, flow) {
  const el = document.querySelector(`#${id} .storyScoreValue`);
  if (el) el.textContent = flow?.score ?? "—";
}


async function generateHooks() {
  const btn = document.getElementById("generateHooksBtn");
  const status = document.getElementById("hookLabStatus");

  btn.disabled = true;
  btn.textContent = "Generating…";
  status.textContent = "Generating hooks…";
  status.className = "hook-lab-status loading";

  let res = null;

  try {
    res = await jsonFetch("/api/hooks", {
      method: "POST",
      body: JSON.stringify({ session: getActiveSession() })
    });
  } catch (e) {
    console.warn("Hook fetch warning:", e);
  }

  const hooks = res?.hooks;

  if (Array.isArray(hooks) && hooks.length > 0) {
    renderHookLab(hooks);
    status.textContent = `✓ ${hooks.length} hooks generated`;
    status.className = "hook-lab-status success";
  } else {
    status.textContent = "⚠ Failed to generate hooks";
    status.className = "hook-lab-status error";
  }

  btn.disabled = false;
  btn.textContent = "Generate Hooks";
}

let selectedHook = null;

function renderHookLab(hooks) {
  const out = document.getElementById("hookLabOutput");
  out.innerHTML = "";

  if (!Array.isArray(hooks) || hooks.length === 0) {
    out.innerHTML = `<div class="hint-text subtle">No hooks generated. Try again.</div>`;
    return;
  }

  hooks
    .filter(h => h && h.text)
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .forEach(h => {
      const card = document.createElement("div");
      card.className = "hookCard";

      const textSpan = document.createElement("span");
      textSpan.className = "hookText";
      textSpan.textContent = h.text;

      const scoreSpan = document.createElement("span");
      scoreSpan.className = "hookScore";
      scoreSpan.textContent = `🔥 ${h.score ?? 0}`;

      card.appendChild(textSpan);
      card.appendChild(scoreSpan);

      card.addEventListener("click", () => selectHook(h.text));

      out.appendChild(card);
    });

  const lab = document.getElementById("hookLab");
  if (lab) lab.classList.remove("hidden");
}



function selectHook(text) {
  selectedHook = text;

  // Remove previous highlight
  document.querySelectorAll(".hookCard").forEach(c =>
    c.classList.remove("selected")
  );

  // Highlight clicked one
  const cards = document.querySelectorAll(".hookCard");
  cards.forEach(c => {
    if (c.querySelector(".hookText")?.textContent === text) {
      c.classList.add("selected");
    }
  });

  const bar = document.getElementById("selectedHookBar");
  const label = document.getElementById("selectedHookDisplay");

  if (bar && label) {
    bar.classList.remove("hidden");
    label.textContent = text;
  }
}

function highlightHookLab() {
  const lab = document.getElementById("hookLab");
  if (!lab) return;

  lab.classList.remove("hook-lab-highlight"); // reset
  void lab.offsetWidth;                       // force reflow
  lab.classList.add("hook-lab-highlight");

  // Remove class after animation finishes
  setTimeout(() => {
    lab.classList.remove("hook-lab-highlight");
  }, 2000);
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



function renderStep3Diff(oldText, newText) {
  const grid = document.getElementById("step3DiffGrid");
  const wrapper = document.getElementById("captionCompareWrapper");
  const scroll = document.getElementById("step3CaptionScroll");
  const toggleBtn = document.getElementById("step3DiffToggle");

  if (!grid || !wrapper || !scroll) return;

  // Split into blocks and REMOVE blank lines
  const oldLines = (oldText || "")
    .split("\n")
    .map(l => l.trim())
    .filter(l => l !== "");

  const newLines = (newText || "")
    .split("\n")
    .map(l => l.trim())
    .filter(l => l !== "");

  grid.innerHTML = "";

// Keep wrapper visible (button lives inside), but respect collapsed state
wrapper.classList.remove("hidden");

  const max = Math.max(oldLines.length, newLines.length);

  for (let i = 0; i < max; i++) {
    const o = oldLines[i] || "";
    const n = newLines[i] || "";

    // OLD
    const oldCard = document.createElement("div");
    oldCard.className = "diff-card old";
    oldCard.textContent = o || "—";

    // NEW
    const newCard = document.createElement("div");
    newCard.className = "diff-card new";
    newCard.textContent = n || "—";

    grid.appendChild(oldCard);
    grid.appendChild(newCard);
  }
}



function renderStep4Diff(original, rewritten) {
  const grid = document.getElementById("step4DiffGrid");
  if (!grid) return;

  grid.innerHTML = "";

  const oldLines = (original || "").split("\n").map(l=>l.trim()).filter(Boolean);
  const newLines = (rewritten || "").split("\n").map(l=>l.trim()).filter(Boolean);

  const max = Math.max(oldLines.length, newLines.length);

  for (let i=0;i<max;i++){
    const o = oldLines[i] || "—";
    const n = newLines[i] || "—";

    const oldCard = document.createElement("div");
    oldCard.className = "diff-card old";
    oldCard.textContent = o;

    const newCard = document.createElement("div");
    newCard.className = "diff-card new";
    newCard.textContent = n;

    grid.appendChild(oldCard);
    grid.appendChild(newCard);
  }
}




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

  requestAnimationFrame(() => {
    requestAnimationFrame(() => 
      {
        animateSessionGlow();
      });
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
            if (!entry.isIntersecting) return;

            const id = "#" + entry.target.id;

            stepButtons.forEach((btn) => {
                if (btn.dataset.target === id) {
                    stepButtons.forEach((b) => b.classList.remove("active"));
                    btn.classList.add("active");
                }
            });

            // 🔥 When Step 3 becomes visible, build the timeline
            if (id === "#step-3") {
                loadConfigAndYaml();
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

    el.innerHTML = items
        .map((file) => {
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
                                Used to guide captions and filename-based generation.
                                </p>

                                <div class="clip-actions">
                                <button class="btn ghost small suggest-label-btn" data-file="${file}">
                                    🧠 Suggest label
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

    // ================================
    // 🧠 SUGGEST LABEL (Vision-powered)
    // ================================
    el.querySelectorAll(".suggest-label-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
        const file = btn.dataset.file;

        btn.disabled = true;
        btn.textContent = "Analyzing…";

        try {
        const res = await jsonFetch("/repair_label", {
            method: "POST",
            body: JSON.stringify({
            session: getActiveSession(),
            file,
            label: ""   // empty → force GPT-Vision to read the video
            })
        });

        const fixed = res.fixed_label;

        const input = btn.closest(".clip-card")
                        ?.querySelector(".clip-label-input");

        if (input && fixed) {
            input.value = fixed;
            await saveClipLabel(file, fixed);
        }

        } catch (err) {
        console.error("Suggest label failed:", err);
        alert("Failed to analyze video");
        } finally {
        btn.disabled = false;
        btn.textContent = "🧠 Suggest label";
        }
    });
    });   

    // ================================
    // 🎬 LOAD CLIP PREVIEWS
    // ================================
    el.querySelectorAll(".clip-preview").forEach(img => {
    const file = img.dataset.file;
    loadClipPreview(file, img);

    img.addEventListener("click", () => {
        img.src = ""; // force refresh
        loadClipPreview(file, img);
    });
    });

    // ================================
    // ✍️ LABEL AUTO-SAVE (with feedback)
    // ================================
    el.querySelectorAll(".clip-label-input").forEach(input => {

        const save = async () => {
            const file = input.dataset.file;
            const label = input.value.trim();

            try {
                await saveClipLabel(file, label);

                // Success glow
                input.classList.remove("error");
                input.classList.add("saved");

                setTimeout(() => {
                    input.classList.remove("saved");
                }, 1200);

            } catch (e) {
                console.error("Label save failed", e);

                // Error glow
                input.classList.add("error");

                setTimeout(() => {
                    input.classList.remove("error");
                }, 1500);
            }
        };

        input.addEventListener("blur", save);

        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                input.blur(); // triggers save()
            }
        });

    });


}

    

async function saveClipLabel(file, label) {
  if (!file) return;

  try {
    const res = await jsonFetch("/api/labels", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        file,
        label
      })
    });

    const finalLabel = res?.label ?? "";
    const weak = !!res?.weak;

    const input = document.querySelector(`.clip-label-input[data-file="${file}"]`);
    const card  = input?.closest(".clip-card");

    // 🔄 Always sync with backend truth
    if (input && finalLabel !== input.value) {
      input.value = finalLabel;
    }

    // ⚠️ Show AI hint if label is weak
    if (card) {
      if (weak) {
        card.classList.add("label-weak");
      } else {
        card.classList.remove("label-weak");
      }
    }

    // UX feedback
    if (input) {
      input.classList.add("saved-flash");
      setTimeout(() => input.classList.remove("saved-flash"), 600);
    }

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
        "working",
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
        "working"
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

        // 🔥 THIS is what was missing
        renderStoryboardTimeline(data.config);

    } catch (err) {
        yamlTextEl.value = "";
        yamlPreviewEl.textContent = `Error loading config: ${err.message}`;
    }
}


function renderStoryboardTimeline(cfg) {
  const container = document.getElementById("storyboardTimeline");
  if (!container) return;

  container.innerHTML = "";

  // Initialize working order once
  if (!workingClipOrder.length) {
    workingClipOrder = [];
    if (cfg.first_clip) workingClipOrder.push(cfg.first_clip);
    (cfg.middle_clips || []).forEach(c => workingClipOrder.push(c));
    if (cfg.last_clip) workingClipOrder.push(cfg.last_clip);
  }

  workingClipOrder.forEach((clip, idx) => {
    const el = document.createElement("div");
    el.className = "storyboard-clip";

    el.innerHTML = `
      <div class="clip-content">
        <div class="clip-name">${clip.file}</div>
        <div class="clip-caption">${clip.text?.slice(0, 60) || "—"}</div>
      </div>

      <div class="clip-controls">
        <button onclick="moveClip(${idx}, -1)">▲</button>
        <button onclick="moveClip(${idx}, 1)">▼</button>
      </div>
    `;

    container.appendChild(el);
  });
}




function moveClip(index, direction) {
  const newIndex = index + direction;
  if (
    newIndex < 0 ||
    newIndex >= workingClipOrder.length
  ) return;

  [workingClipOrder[index], workingClipOrder[newIndex]] =
    [workingClipOrder[newIndex], workingClipOrder[index]];

  clipOrderDirty = true;

  renderStoryboardTimeline({
    first_clip: workingClipOrder[0],
    middle_clips: workingClipOrder.slice(1, -1),
    last_clip: workingClipOrder[workingClipOrder.length - 1]
  });

  setStatus(
    "storyboardStatus",
    "Clip order updated — apply to save",
    "working"
  );
}




async function saveYaml() {
    const yamlTextEl = document.getElementById("yamlText");
    const statusEl = document.getElementById("yamlStatus");
    if (!yamlTextEl || !statusEl) return;

    const raw = yamlTextEl.value || "";
    setStatus("yamlStatus", "Saving YAML…", "working", false);


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
if (!workingCaptionsText && !lastSavedCaptionsText) {
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

    const diffOpen = !document
    .getElementById("captionCompareBody")
    ?.classList.contains("hidden");

    if (score < 60 && !diffOpen) {
        statusEl.textContent =
            "⚠ Weak hook — click the score to explore better ones.";
    } else {
        statusEl.textContent = "";
    }


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
  statusEl.className = "status-text status-working";

  try {
    const data = await jsonFetch("/api/hook_improve", {
      method: "POST",
      body: JSON.stringify({ session: getActiveSession() }),
    });

    if (data.status === "error") throw new Error(data.error || "failed");

    // 🔥 Rewrite proposal
    if (data.status === "proposed") {
      proposeRewrite(data.proposed, "Hook rewrite ready");

      if (statusEl) {
        statusEl.textContent = "Hook rewrite ready — review & accept or reject";
      }
      return;
    }

    throw new Error("Unexpected response");

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
    rewrite: document.getElementById("mode_rewrite")?.checked,
    hook: document.getElementById("mode_hook")?.checked,
    punchy: document.getElementById("mode_punchy")?.checked,
    story: document.getElementById("mode_story")?.checked,
    influencer: document.getElementById("mode_influencer")?.checked,
    minimal: document.getElementById("mode_minimal")?.checked,
  };

  const session = getActiveSession();

  setVariantsStatus("Generating caption variants…", "loading");

  if (btn) {
    btn.disabled = true;
    btn.textContent = "Generating…";
  }

  try {
    const res = await fetch("/api/variants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session,
        modes,
        selected_hook: selectedHook || null
      })
    });

    if (!res.ok) {
      throw new Error("Variant generation failed");
    }

    const data = await res.json();

    const box = document.getElementById("variantsOutput");
    box.innerHTML = "";

    for (let i = 0; i < data.variants.length; i++) {
      const variant = data.variants[i];
      const text = variant.text || "";
      const tone = variant.tone || "";

      const cardId = `variant_${i}`;

      box.innerHTML += renderVariantCard(
        i + 1,
        tone,
        text,
        null,
        cardId
      );
    }

    // ✅ Success AFTER render
    setVariantsStatus("Variants generated ✓", "success");

    setTimeout(() => {
      document.getElementById("variantsInlineStatus")?.classList.add("hidden");
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
// =============================================
// Apply selected generated caption variant (Step 3 = COMMIT)
// =============================================
async function applyCaptionVariant(text) {
  const session = getActiveSession();

  const originalText = lastSavedCaptionsText || "";
  const originalCount = countBlocks(originalText);
  const newCount = countBlocks(text);

  if (originalCount !== newCount) {
    setStatus(
      "captionsStatus",
      `⚠ Caption count mismatch (${originalCount} → ${newCount}). Review before saving.`,
      "warning"
    );
    return;
  }

  try {
    setStatus("captionsStatus", "Applying caption…", "working")

    await jsonFetch("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({
        session,
        text
      })
    });

    // 🔑 This variant is now the truth
    lastSavedCaptionsText = text;
    workingCaptionsText = text;

    // Reload YAML + editor
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();
    await refreshOverlayPreview();

    // Show what changed (visual only)
    renderStep3Diff(originalText, text);
    focusCaptionChanges();

    // 🔥 ABSOLUTELY kill any Step-4 rewrite state
    rewritePending = false;
    isInRewriteReview = false;
    exitRewriteReviewMode();
    clearPendingRewrite();

    setStatus("captionsStatus", "Caption applied ✓", "success");

    toggleVariantsPanel(true);

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

function toggleVariantsPanel(forceClose = false) {
  const drawer = document.getElementById("variantsDrawer");
  const btn = document.getElementById("variantsToggleBtn");
  if (!drawer) return;

  if (forceClose) {
    drawer.classList.add("closed");
    if (btn) btn.textContent = "Expand";
    return;
  }

  drawer.classList.toggle("closed");
  const closed = drawer.classList.contains("closed");
  if (btn) btn.textContent = closed ? "Expand" : "Collapse";
}

async function scoreStoryFlow() {
  const session = getActiveSession();

  const res = await fetch(`/api/story_flow_score?session=${encodeURIComponent(session)}`);

  if (!res.ok) {
    throw new Error("Story flow scoring failed");
  }

  return await res.json(); // { score, reasons }
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

    const text = getCurrentCaptionsText();

    if (!text) {
    card.classList.add("hidden");
    return;
    }

    const blocks = text
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean)
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

  if (hookBtn) {
  hookBtn.classList.add("hidden");
}

  if (flowBtn && typeof storyScore === "number") {
    flowBtn.classList.toggle("hidden", storyScore >= 80);
  }
}

    // ================================
    // Disable Rewrite Mode if no captions exist
    // ================================
    function updateRewriteModeAvailability() {
    const text = getCurrentCaptionsText();
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

async function loadCaptionsFromYaml() {
  const box = document.getElementById("captionsText");
  if (!box) return;

  setCaptionInlineStatus("Loading captions from YAML…", "info");

  try {
    const session = encodeURIComponent(getActiveSession());
    const data = await jsonFetch(`/api/config?session=${session}`);
    const cfg = data.config || {};

    const yamlText = buildCaptionsFromConfig(cfg).trim();

    // 🔑 YAML baseline
    lastSavedCaptionsText = yamlText;

    rewritePending = false;
    clearPendingRewrite();
    exitRewriteReviewMode();


    // 🔥 Step 3 editor must always be editable
    workingCaptionsText = yamlText;

    // Do NOT let Step-4 lock the editor here
    captionViewMode = "rewritten";

    
    renderStoryboardTimeline(data.config);
    renderCaptionView();


    updateRewriteModeAvailability();
    await refreshHookScore();
    await refreshStoryFlowScore();

    setCaptionSource("yaml", "🔵 SOURCE: YAML");
    setCaptionInlineStatus("Captions loaded from YAML", "success");

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
        

        lastSavedCaptionsText = text;   // 🔑 THIS IS REQUIRED

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

function focusCaptionChanges() {
  const wrapper = document.getElementById("captionCompareWrapper");
  if (!wrapper || wrapper.classList.contains("hidden")) return;

  wrapper.scrollIntoView({
    behavior: "smooth",
    block: "center"
  });

  // visual cue
  wrapper.classList.remove("flash");
  void wrapper.offsetWidth;
  wrapper.classList.add("flash");
}



function updateRewriteWarning() {
    const mode = document.querySelector('input[name="captionRewriteMode"]:checked')?.value;
    const warning = document.getElementById("rewriteWarning");
    if (!warning) return console.warn("rewriteWarning element missing");

    warning.classList.toggle("hidden", mode !== "rewrite");
}


function syncCaptionToggleUI() {
  const orig = document.getElementById("showOriginal");
  const rew = document.getElementById("showRewritten");
  const diff = document.getElementById("showDiff");

  [orig, rew, diff].forEach(b => b?.classList.remove("active"));

  if (captionViewMode === "original") orig?.classList.add("active");
  if (captionViewMode === "rewritten") rew?.classList.add("active");
  if (captionViewMode === "diff") diff?.classList.add("active");
}




// ================================
// Step 4: Overlay, timings, TTS, CTA, fg scale, music
// ================================
async function applyOverlay() {
  console.log("APPLY OVERLAY CLICKED");

  const styleSel = document.getElementById("overlayStyle");
  const statusEl = document.getElementById("overlayStatus");
  if (!styleSel || !statusEl) return;

  const style = styleSel.value || "travel_blog";

  const rewriteMode =
    document.querySelector('input[name="captionRewriteMode"]:checked')?.value || "visual";

  setStatus(
    "overlayStatus",
    rewriteMode === "rewrite"
      ? "Applying overlay + rewriting captions…"
      : "Applying visual overlay only…",
    "working",
    false
  );

  try {
    const res = await jsonFetch("/api/overlay", {
      method: "POST",
      body: JSON.stringify({
        style,
        session: getActiveSession(),
        rewrite: rewriteMode === "rewrite",
      }),
    });

    // ======================================
    // ⚠ Hook weak / warning → must HARD RESET
    // ======================================
    if (res.status === "warning") {
      console.warn("Overlay blocked:", res.message);

      rewritePending = false;
      clearPendingRewrite();
      exitRewriteReviewMode();

      captionViewMode = "rewritten";
      renderCaptionView();
      syncCaptionToggleUI();

      setStatus(
        "overlayStatus",
        res.message || "Hook too weak to safely rewrite captions.",
        "warning"
      );
      return;
    }

    // ======================================
    // 🧠 Rewrite proposal path
    // ======================================
    if (res.status === "proposed") {
    // 🔥 Always unlock for a new proposal
    rewriteCommitted = false;

    proposeRewrite(res.proposed, "Overlay rewrite ready", "step4")
    return;
    }


    // ======================================
    // 🎨 Visual-only overlay path
    // ======================================
    await loadConfigAndYaml();
    await loadCaptionsFromYaml(); // updates lastSavedCaptionsText
    await previewOverlay("fast");

    workingCaptionsText = lastSavedCaptionsText;
    captionViewMode = "rewritten";
    renderCaptionView();
    syncCaptionToggleUI();

    diffDirty = false;

    setStatus("overlayStatus", "Overlay applied ✓", "success");

  } catch (err) {
    console.error(err);
    setStatus("overlayStatus", "Failed to apply overlay.", "error");
  }
}



document
  .querySelector('input[name="captionRewriteMode"][value="visual"]')
  ?.addEventListener("change", clearOverlayWarning);

  document
  .querySelector('input[name="captionRewriteMode"][value="rewrite"]')
  ?.addEventListener("change", refreshHookScore);


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

function getCurrentCaptionsText() {
  return (workingCaptionsText || lastSavedCaptionsText || "").trim();
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

    setStatus("captionModeStatus", "Saving...", "working", false);

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
    setStatus("layoutStatus", "Saving layout mode…", "working", false);

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
async function saveTtsSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("ttsEnabled");
    const voiceEl = document.getElementById("ttsVoice");
    const statusEl = document.getElementById("ttsStatus");

    if (!enabledEl || !voiceEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const voice = voiceEl.value;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.tts = {
            enabled,
            voice
        };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        // ✅ feedback
        if (!silent) {
            setStatus("ttsStatus", "TTS saved ✓", "success");
        } else {
            showAutoSaveStatus("ttsStatus");
        }

        // ✅ THIS IS THE KEY LINE
        await loadConfigAndYaml();   // refresh preview + parsed YAML

    } catch (err) {
        console.error(err);
        setStatus("ttsStatus", "Failed to save TTS", "error");
    }
}

// CTA

async function saveCtaSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("ctaEnabled");
    const textEl = document.getElementById("ctaText");
    const voiceEl = document.getElementById("ctaVoiceover");
    const statusEl = document.getElementById("ctaStatus");

    if (!enabledEl || !textEl || !voiceEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const text = textEl.value.trim();
    const voiceover = voiceEl.checked;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.cta = { enabled, text, voiceover };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        if (!silent) {
            setStatus("ctaStatus", "CTA saved ✓", "success");
        }

        await loadConfigAndYaml();

    } catch (err) {
        console.error(err);
        setStatus("ctaStatus", "Failed to save CTA", "error");
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
async function saveMusicSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("musicEnabled");
    const fileEl = document.getElementById("musicFile");
    const volEl = document.getElementById("musicVolume");
    const statusEl = document.getElementById("musicStatus");

    if (!enabledEl || !fileEl || !volEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const file = fileEl.value || "";
    const volume = parseFloat(volEl.value || "0.25");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.music = { enabled, file, volume };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        // ✅ THIS IS THE MISSING PIECE
        await loadConfigAndYaml();

        if (!silent) {
            setStatus("musicStatus", "Music saved ✓", "success");
        } else {
            showAutoSaveStatus("musicStatus");
        }

    } catch (err) {
        console.error(err);
        setStatus("musicStatus", "Failed to save music", "error");
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

// ================================
// CTA — Checkbox auto-save
// ================================
const ctaEnabledEl = document.getElementById("ctaEnabled");
const ctaVoiceoverEl = document.getElementById("ctaVoiceover");

ctaEnabledEl?.addEventListener("change", async () => {
    syncCtaUIState();

    await saveCtaSettings({ silent: true });

    flashElement(document.getElementById("ctaRow"));
    showAutoSaveStatus("ctaStatus");
});

ctaVoiceoverEl?.addEventListener("change", async () => {
    await saveCtaSettings({ silent: true });
    showAutoSaveStatus("ctaStatus");
});


// ================================
// CTA — Text auto-save (FINAL)
// ================================
const ctaTextEl = document.getElementById("ctaText");
const ctaRowEl = document.getElementById("ctaRow");

let ctaSaveTimer = null;

ctaTextEl?.addEventListener("input", () => {
    clearTimeout(ctaSaveTimer);

    ctaSaveTimer = setTimeout(async () => {
        await saveCtaSettings({ silent: true });

        // ✅ flash ONLY after save succeeds
        if (ctaRowEl) flashElement(ctaRowEl);

        showAutoSaveStatus("ctaStatus");
    }, 400);
});




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

// ================================
// Foreground Scale — Save (supports silent)
// ================================
async function saveFgScale({ silent = false } = {}) {
    const autoEl = document.getElementById("autoFgScale");
    const scaleEl = document.getElementById("fgScale");
    const statusEl = document.getElementById("fgStatus");

    if (!autoEl || !scaleEl || !statusEl) return;

    const auto = autoEl.checked;
    const scale = parseFloat(scaleEl.value || "1.0");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/config?session=${session}`);
        const cfg = data.config || {};

        cfg.foreground_scale = {
            auto,
            scale
        };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        if (!silent) {
            setStatus("fgStatus", "Foreground scale saved ✓", "success");
        } else {
            showAutoSaveStatus("fgStatus");
        }

        await loadConfigAndYaml();

    } catch (err) {
        console.error(err);
        setStatus("fgStatus", "Failed to save foreground scale", "error");
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

    syncCtaUIState();

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

    document.getElementById("generateHooksBtn")
  ?.addEventListener("click", generateHooks);

  document.getElementById("captionsText")?.addEventListener("input", () => {
  diffDirty = true;
});

document.getElementById("applyOrderBtn")?.addEventListener("click", async () => {
  if (!clipOrderDirty) {
    setStatus("storyboardStatus", "No changes to apply", "info");
    return;
  }

  const session = getActiveSession();

  const newCfg = {
    first_clip: workingClipOrder[0],
    middle_clips: workingClipOrder.slice(1, -1),
    last_clip: workingClipOrder.length > 1
      ? workingClipOrder[workingClipOrder.length - 1]
      : null
  };

  try {
    await jsonFetch("/api/save_config", {
      method: "POST",
      body: JSON.stringify({
        session,
        config: newCfg
      })
    });

    clipOrderDirty = false;
    workingClipOrder = [];

    await loadConfigAndYaml();
    syncFgScaleUI();
    await loadCaptionsFromYaml();

    setStatus("storyboardStatus", "Clip order applied ✓", "success");
  } catch (err) {
    console.error(err);
    setStatus("storyboardStatus", "Failed to save clip order", "error");
  }
});


const captionsBox = document.getElementById("captionsText");

if (captionsBox) {
  captionsBox.addEventListener("input", () => {
    const base = lastSavedCaptionsText || "";
    const current = captionsBox.value || "";

    workingCaptionsText = current;
    diffDirty = true;

    renderStep3Diff(base, current);

    const scroll = document.getElementById("step3CaptionScroll");
    const btn = document.getElementById("step3DiffToggle");

    if (scroll && btn) {
    scroll.style.display = "block";
    btn.textContent = "Collapse";
}



  });
}

    // ================================
    // Hook score → Open Variants Drawer
    // ================================
    const hookScore = document.getElementById("hookScoreValue");

    if (hookScore) {
    hookScore.classList.add("clickable");

    hookScore.addEventListener("click", () => {
  toggleVariantsPanel(false);
  document.getElementById("hookLab")?.classList.remove("hidden");

  const drawer = document.getElementById("variantsDrawer");
  drawer?.scrollIntoView({ behavior: "smooth", block: "start" });

  highlightHookLab(); 
});

    }

    // 🔍 "Find better hooks" button inside Hook Score card
    document.getElementById("openHookLabBtn")?.addEventListener("click", () => {
  toggleVariantsPanel(false);
  document.getElementById("hookLab")?.classList.remove("hidden");
  document
    .getElementById("hookLab")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });

  highlightHookLab();   
});


    

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


    // STEP 4 — Caption diff collapse
    document.getElementById("toggleDiffCollapse")?.addEventListener("click", () => {
    const scroll = document.getElementById("step4CaptionScroll");
    const btn = document.getElementById("toggleDiffCollapse");

    if (!scroll) return;

    const isHidden = scroll.style.display === "none";

    scroll.style.display = isHidden ? "block" : "none";
    btn.textContent = isHidden ? "Collapse" : "Expand";
    });

    // STEP 3 — Caption diff collapse
    document.getElementById("step3DiffToggle")?.addEventListener("click", () => {
    const scroll = document.getElementById("step3CaptionScroll");
    const btn = document.getElementById("step3DiffToggle");

    if (!scroll) return;

    const isHidden = scroll.style.display === "none";

    scroll.style.display = isHidden ? "block" : "none";
    btn.textContent = isHidden ? "Collapse" : "Expand";
    });


 // ================================
// OVERLAY STYLE — Auto-save
// ================================
const overlayStyleEl = document.getElementById("overlayStyle");

overlayStyleEl?.addEventListener("change", async () => {
    try {
        await saveOverlayStyle({ silent: true });
        showAutoSaveStatus("overlayStyleStatus");
    } catch (e) {
        setStatus("overlayStyleStatus", "Save failed", "error");
    }
});


// ================================
// Foreground Scale — Auto-save wiring
// ================================
const autoFgEl = document.getElementById("autoFgScale");
const fgScaleEl = document.getElementById("fgScale");

let fgSaveTimer = null;

// Auto zoom checkbox → instant save
autoFgEl?.addEventListener("change", async () => {
    syncFgScaleUI();
    await saveFgScale({ silent: true });
});

// Manual scale slider → debounced save
fgScaleEl?.addEventListener("input", () => {
    clearTimeout(fgSaveTimer);

    fgSaveTimer = setTimeout(async () => {
        await saveFgScale({ silent: true });
    }, 300);
});


// ================================
// TTS — Auto-save wiring
// ================================
const ttsEnabledEl = document.getElementById("ttsEnabled");
const ttsVoiceEl = document.getElementById("ttsVoice");

// Toggle → instant auto-save
ttsEnabledEl?.addEventListener("change", () => {
  syncTtsUIState();
  saveTtsSettings({ silent: true });
});


// Voice change → instant auto-save
ttsVoiceEl?.addEventListener("change", () => {
  saveTtsSettings({ silent: true });
});
  

// ================================
// MUSIC — Auto-save wiring (CLEAN)
// ================================
const musicEnabledEl = document.getElementById("musicEnabled");
const musicFileEl = document.getElementById("musicFile");
const musicVolumeEl = document.getElementById("musicVolume");

// Music auto-save
document.getElementById("musicEnabled")?.addEventListener("change", () => {
    saveMusicSettings({ silent: true });
});

// Track dropdown → instant auto-save
musicFileEl?.addEventListener("change", () => {
  saveMusicSettings({ silent: true });
});

// Volume slider → debounced auto-save
let musicSaveTimer = null;

musicVolumeEl?.addEventListener("input", () => {
  clearTimeout(musicSaveTimer);

  musicSaveTimer = setTimeout(() => {
    saveMusicSettings({ silent: true });
  }, 300);
});

// ================================
// LAYOUT MODE — Auto-save
// ================================
const layoutModeEl = document.getElementById("layoutMode");

layoutModeEl?.addEventListener("change", async () => {
    try {
        setStatus("layoutStatus", "Saving…", "working", false);
        await saveLayoutMode();
        setStatus("layoutStatus", "Saved ✓", "success");
    } catch {
        setStatus("layoutStatus", "Save failed", "error");
    }
});

// ================================
// CAPTION MODE — Auto-save
// ================================
const captionModeEl = document.getElementById("captionModeSelect");

captionModeEl?.addEventListener("change", async () => {
    try {
        setStatus("captionModeStatus", "Saving…", "working", false);
        await saveCaptionMode();
        setStatus("captionModeStatus", "Saved ✓", "success");
    } catch {
        setStatus("captionModeStatus", "Save failed", "error");
    }
});


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


// ========================================
// Step 4 — Caption View Toggles
// ========================================
document.getElementById("showOriginal")?.addEventListener("click", () => {
  captionViewMode = "original";
  renderCaptionView();          // 🔥 THIS was missing
  syncCaptionToggleUI();
  document.getElementById("step4CaptionScroll")?.classList.add("hidden");
});

document.getElementById("showRewritten")?.addEventListener("click", () => {
  captionViewMode = "rewritten";
  renderCaptionView();          // 🔥 THIS was missing
  syncCaptionToggleUI();
  document.getElementById("step4CaptionScroll")?.classList.add("hidden");
});

document.getElementById("showDiff")?.addEventListener("click", () => {
  captionViewMode = "diff";
  syncCaptionToggleUI();
  document.getElementById("step4CaptionScroll")?.classList.remove("hidden");

  // 🔥 Only show Accept/Reject when a rewrite is pending
  if (rewritePending && isInRewriteReview) {
    document.getElementById("rewriteDecisionBar")?.classList.remove("hidden");
  } else {
    document.getElementById("rewriteDecisionBar")?.classList.add("hidden");
  }

  renderStep4Diff(lastSavedCaptionsText, workingCaptionsText);
});


document.addEventListener("click", async (e) => {
  const accept = e.target.closest('[data-action="accept-rewrite"]');
  const reject = e.target.closest('[data-action="reject-rewrite"]');

  if (!accept && !reject) return;

  // ---------------- ACCEPT ----------------
  if (accept) {
    if (!workingCaptionsText || !rewritePending) return;

    try {
      await jsonFetch("/api/save_captions", {
        method: "POST",
        body: JSON.stringify({
          session: getActiveSession(),
          text: workingCaptionsText
        })
      });

      lastSavedCaptionsText = workingCaptionsText;
      rewriteCommitted = true;
      await loadConfigAndYaml();
      await loadCaptionsFromYaml();
      await refreshOverlayPreview();
      await refreshHookScore();
      await refreshStoryFlowScore();

      workingCaptionsText = lastSavedCaptionsText;

      hardClearRewriteUI();
    setStatus("overlayStatus", "Rewrite accepted ✓", "success");


    } catch (err) {
      console.error(err);
      setStatus("overlayStatus", "Failed to save rewrite", "error");
    }
  }

  // ---------------- REJECT ----------------
  if (reject) {
  workingCaptionsText = lastSavedCaptionsText;
  hardClearRewriteUI();
  setStatus("overlayStatus", "Rewrite discarded", "info");
}
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

    // Initial visual confirmation
  setTimeout(animateSessionGlow, 300);

});
