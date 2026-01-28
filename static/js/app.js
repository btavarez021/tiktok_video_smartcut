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

let currentIntent = "discovery";

let CONFIG_LOADING = false;

let isInRewriteReview = false;
let captionViewMode = "rewritten";
let diffDirty = false;
let lastAnalyzeStatus = null;
let ANALYZE_POLL_ACTIVE = false;
let lastVariantStatus = null;
let VARIANT_POLL_ACTIVE = false;
let YAML_POLL_ACTIVE = false;
let lastYamlStatus = null;
let PENDING_SCROLL_TO_STORYBOARD = false;

function setCurrentVideoIntent(intent) {
  currentIntent = intent;
  console.log("🎯 Video intent set to:", intent);
}


function debounce(fn, wait = 350) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

async function pollVariantStatus() {
  if (!VARIANT_POLL_ACTIVE) return;

  const session = getActiveSession(); // ✅ FIXED

  try {
    const data = await jsonFetch(
      `/api/variants/status?session=${session}`
    );

    // 🧠 Session switched mid-poll → stop safely
    if (session !== getActiveSession()) {
      VARIANT_POLL_ACTIVE = false;
      updateVariantRunningBadge("idle");
      return;
    }

    const status = data.status;

    // 🔴 Badge: visible while running
    updateVariantRunningBadge(status);

    // Show inline "working" once
    if (status === "running" && lastVariantStatus !== "running") {
      setStatus(
        "variantsInlineStatus",
        "Generating AI variants…",
        "working"
      );
    }

    // ✅ Transition: running → done
    if (lastVariantStatus === "running" && status === "done") {
      console.log("✅ Variants ready");

      const variants = data.result?.variants || [];

      // 🔑 Global state
      window.lastGeneratedVariants = variants;

      // Sort: AI recommended first
      variants.sort((a, b) => {
        if (a.recommended) return -1;
        if (b.recommended) return 1;
        return (
          (b.hook_score || 0) + (b.story_flow || 0) -
          ((a.hook_score || 0) + (a.story_flow || 0))
        );
      });

      const box = document.getElementById("variantsOutput");
      box.innerHTML = "";
      box.dataset.rendered = "false";

      variants.forEach((variant, i) => {
        const cardId = `variant_${i}`;

        box.innerHTML += renderVariantCard(
          i + 1,
          variant,
          cardId
        );

        // 🔥 Feedback: viewed
        sendVariantFeedback({
          variantId: cardId,
          intent: currentIntent || "discovery",
          tone: variant.tone,
          confidence: variant.confidence,
          recommended: variant.recommended === true,
          action: "viewed"
        });
      });

      box.dataset.rendered = "true";
      updateAIRecommendationBar();

      // 🟢 Inline success
      setStatus(
        "variantsInlineStatus",
        "AI variants ready ✓",
        "success"
      );

      setTimeout(() => {
        setStatus("variantsInlineStatus", "");
      }, 2000);

      // 🔴 Hide badge immediately
      updateVariantRunningBadge("idle");

      // 🔓 Unlock button
      document
        .getElementById("generateVariantsBtn")
        ?.removeAttribute("disabled");

      VARIANT_POLL_ACTIVE = false;
    }

    lastVariantStatus = status;

    if (status === "running") {
      setTimeout(pollVariantStatus, 1200);
    }

  } catch (err) {
    console.warn("pollVariantStatus failed", err);

    if (VARIANT_POLL_ACTIVE) {
      setTimeout(pollVariantStatus, 2000);
    }
  }
}


function updateAIRecommendationBar() {
  const bar = document.getElementById("aiRecommendationBar");
  const applyBtn = document.getElementById("applyAiRecommendationBtn");
  const undoBtn = document.getElementById("undoAiRecommendationBtn");

  if (!bar) return;

  const hasRecommendation =
    Array.isArray(window.lastGeneratedVariants) &&
    window.lastGeneratedVariants.some(v => v.recommended === true);

  // 1️⃣ Show / hide bar
  bar.classList.toggle("hidden", !hasRecommendation);

  // 2️⃣ Apply button state
  if (applyBtn) {
    applyBtn.disabled = !hasRecommendation || !!window.aiUndoSnapshot;
    applyBtn.textContent = window.aiUndoSnapshot
      ? "Applied ✓"
      : "Apply AI recommendation";
  }

  // 3️⃣ Undo button state
  if (undoBtn) {
    const canUndo =
      window.aiUndoSnapshot &&
      window.aiUndoSnapshot.session === getActiveSession();

    undoBtn.classList.toggle("hidden", !canUndo);
    undoBtn.disabled = false;
  }
}


function updateIntentHint(intent) {
  const hint = document.getElementById("intentHint");
  if (!hint) return;

  const copy = {
    discovery: "Optimized for reach, virality, and scroll-stopping hooks.",
    personal: "Optimized for emotion, story, and connection.",
    aesthetic: "Optimized for calm pacing and visual flow.",
    informational: "Optimized for clarity, structure, and explanation."
  };

  hint.textContent = copy[intent] || "";
}

function setUiBusy(busy) {
  document.body.classList.toggle("ui-busy", busy);
}


function syncTtsUIState() {
  const enabled = document.getElementById("ttsEnabled")?.checked;
  const voiceSelect = document.getElementById("ttsVoice");

  if (!voiceSelect) return;

  voiceSelect.disabled = !enabled;
  voiceSelect.style.opacity = enabled ? "1" : "0.5";
}

const autoSaveStoryboardOrder = debounce(() => {
  saveStoryboardOrder({ silent: true });
}, 600);

function updateCaptionBaselineHint() {
  const hint = document.getElementById("captionBaselineHint");
  if (!hint) return;

  const hasVariants =
  Array.isArray(window.lastGeneratedVariants) &&
  window.lastGeneratedVariants.length > 0;


  hint.style.display = hasVariants ? "none" : "block";
}

function updateLoadYamlVisibility() {
  const btn = document.getElementById("loadCaptionsFromYamlBtn");
  if (!btn) return;

  const hasVariants =
  Array.isArray(window.lastGeneratedVariants) &&
  window.lastGeneratedVariants.length > 0;

  btn.style.display = hasVariants ? "inline-block" : "none";
}

function syncIntentPills(intent) {
  document.querySelectorAll(".intent-pills .pill").forEach(pill => {
    pill.classList.toggle("active", pill.dataset.intent === intent);
  });
}


async function loadIntentFromConfig() {
  try {
    const res = await jsonFetch(
      `/api/config?session=${encodeURIComponent(getActiveSession())}`
    );

    const intent = res?.intent || "discovery";

    // 🔑 Core state
    currentIntent = intent;

    // ✅ SYNC PILL UI (single source of truth)
    syncIntentPills(intent);

    // 🔔 Update intent hint
    updateIntentHint(intent);

    // Optional legacy select support
    const select = document.getElementById("intentSelect");
    if (select) select.value = intent;

    // Refresh dependent systems
    refreshHookScore();

    setStatus(
      "captionStatus",
      `Intent set to “${intent}”`,
      "info"
    );

  } catch (e) {
    console.warn("Failed to load intent, using default");
  }
}


function sendVariantFeedback({ variantId, intent, tone, action, confidence = null, recommended = false }) {
  return fetch("/api/variant_feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session: getActiveSession(),
      variant_id: variantId,          // match backend naming
      intent,
      tone,
      confidence,                     // "clear" | "moderate" | "close" | null
      recommended: recommended === true,
      action                          // "viewed" | "chosen"
    })
  });
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

// ================================
// Mobile Session Panel Toggle
// ================================
function toggleMobileSessionPanel() {
  const panel = document.getElementById("sidebarSessionCard");
  const btn = document.getElementById("mobileSessionBtn");
  if (!panel || !btn) return;

  const isOpen = panel.classList.toggle("open");
  console.log("[MOBILE] toggle session panel", { isOpen, panel });

  document.body.classList.toggle("no-scroll", isOpen);
  btn.textContent = isOpen ? "Close Sessions" : "Sessions";
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


function updateVariantRunningBadge(status) {
  const el = document.getElementById("variantRunningBadge");
  if (!el) return;

  el.classList.toggle("hidden", status !== "running");
}

function showPendingRewrite() {
  document.getElementById("pendingRewriteBadge")?.classList.remove("hidden");
}

function clearPendingRewrite() {
  document.getElementById("pendingRewriteBadge")?.classList.add("hidden");
}

async function generateVariantsAsync(modes, selectedHook) {

lastVariantStatus = null;

  // 🔒 Lock button
  const btn = document.getElementById("generateVariantsBtn");
  if (btn) btn.disabled = true;

  // Inline + badge feedback
  setStatus(
    "variantsInlineStatus",
    "Generating AI variants…",
    "working",
    false
  );

  updateVariantRunningBadge("running");

  // Optional UX polish: auto-open drawer
  if (typeof openVariantsPanel === "function") {
    openVariantsPanel({ silent: true });
  }

  try {
    const res = await jsonFetch("/api/variants/start", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        modes,
        selected_hook: selectedHook
      })
    });

    // 🔁 Already running → just poll
    VARIANT_POLL_ACTIVE = true;
    pollVariantStatus();

  } catch (err) {
    console.error(err);

    setStatus(
      "variantsInlineStatus",
      "Failed to start AI variants",
      "error"
    );

    updateVariantRunningBadge("idle");

    // 🔓 Unlock button on failure
    if (btn) btn.disabled = false;
  }
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

function toggleVariantWhy(cardId) {
  const el = document.getElementById(`${cardId}_why`);
  if (!el) return;

  el.classList.toggle("hidden");
}



function renderVariantCard(num, variant, cardId) {
  const text = variant.text || "";
  const tone = variant.tone || "";
  const recommended = variant.recommended === true;
  const reason = variant.recommend_reason || "";
  const confidence = variant.confidence || "close";
  const confLabel = confidenceLabel(normalizeConfidence(confidence));
  const escaped = text.replace(/`/g, "\\`");

  // ----------------------------
  // AI badge
  // ----------------------------
  const badge = recommended
    ? `<div class="ai-recommended-badge"
           data-confidence="${confidence}"
           title="Recommended based on hook strength, story flow, and your selected intent.">
         <span class="ai-badge-main">🤖 AI Recommended</span>
         <span class="ai-badge-confidence">${confLabel}</span>
       </div>`
    : "";

  // ----------------------------
  // Why this won
  // ----------------------------
  const whyToggle =
  recommended && reason
    ? `
      <div class="variantWhyToggle"
           onclick="toggleVariantWhy('${cardId}')">
        Why this won ▾
      </div>
      <div class="variantWhy hidden" id="${cardId}_why">
        ${reason}
      </div>
    `
    : "";

  // ----------------------------
  // Final render (CORRECT)
  // ----------------------------
  return `
  <div class="variantCard ${recommended ? "recommended" : ""}" id="${cardId}">

    <div class="variantHeader">
      <h4>Version ${num}</h4>
      ${badge}
    </div>

      ${tone ? `<div class="variantTone">${tone}</div>` : ""}

      ${whyToggle}

      <pre style="white-space:pre-wrap">${text}</pre>

      <button onclick="
        event.stopPropagation();
        sendVariantFeedback({
          variantId: '${cardId}',
          intent: '${currentIntent}',
          tone: '${tone}',
          confidence: '${confidence}',
          recommended: ${recommended},
          action: 'chosen'
        });
        applyCaptionVariant(\`${escaped}\`);
      ">
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
      body: JSON.stringify({
      session: getActiveSession(),
      intent: currentIntent || "discovery"
    })

    });
  } catch (e) {
    console.warn("Hook fetch warning:", e);
  }

  const hooks = res?.hooks;

  if (Array.isArray(hooks) && hooks.length > 0) {
  lastGeneratedHooks = hooks;      // ✅ ADD THIS LINE
  renderHookLab(hooks);
  status.textContent = `✓ ${hooks.length} hooks generated`;
  status.className = "hook-lab-status success";
  } else {
    status.textContent = "⚠ Failed to generate hooks";
    status.className = "hook-lab-status error";
  }

  btn.disabled = false;
  btn.textContent = "Generate Hooks";

  requestAnimationFrame(() => {
  document
    .getElementById("hookLabOutput")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
});
}

let lastGeneratedHooks = [];
let selectedHook = null;


// ================================
// Hook Lab — Confidence-aware UI helpers
// ================================
function normalizeConfidence(c) {
  const v = (c || "").toLowerCase();
  if (v === "clear" || v === "moderate" || v === "close") return v;
  return "close";
}

function confidenceLabel(c) {
  if (c === "clear") return "Clear winner";
  if (c === "moderate") return "Strong pick";
  return "Close call";
}

function shouldHighlightRecommended(conf) {
  // Confidence-aware highlight rules
  // - clear: strong highlight
  // - moderate: normal highlight
  // - close: no green border highlight (reduces “AI yelling”)
  return conf !== "close";
}

function shouldAutoShowWhy(conf) {
  // Only auto-show why for CLEAR picks (otherwise too noisy)
  return conf === "clear";
}


function renderHookLab(hooks) {
  const out = document.getElementById("hookLabOutput");
  out.innerHTML = "";

  if (!Array.isArray(hooks) || hooks.length === 0) {
    out.innerHTML = `<div class="hint-text subtle">No hooks generated. Try again.</div>`;
    return;
  }

  hooks
    .filter(h => h && h.text)
    .sort((a, b) => {
      if (a.recommended) return -1;
      if (b.recommended) return 1;
      return (b.score || 0) - (a.score || 0);
    })
    .forEach(h => {
      const isRecommended = h.recommended === true;
      const isSelected = selectedHook === h.text;
      const reason = h.recommend_reason || "";

      const card = document.createElement("div");
      card.className = "hookCard";

      // 🔒 GLOBAL RULE:
      // If user selected ANY hook, AI visuals are suppressed
      const allowAiHighlight = !selectedHook;

      if (isSelected) {
        card.classList.add("selected");
      }

      // 🤖 AI badge — confidence-aware + never overlays text
      if (isRecommended && allowAiHighlight) {
        const conf = normalizeConfidence(h.confidence);
        const confText = confidenceLabel(conf);

        // Confidence-aware highlight:
        // close call => no "recommended" green border highlight
        if (shouldHighlightRecommended(conf)) {
          card.classList.add("recommended");
        }

        card.classList.add("hook-ai-pick");

        // Add a confidence class for CSS styling
        card.classList.add(`conf-${conf}`);

        const badge = document.createElement("div");
        badge.className = `ai-recommended-badge conf-${conf}`;
        badge.dataset.confidence = conf;

        badge.innerHTML = `
          <div class="ai-badge-row">
            <span class="ai-badge-main">🤖 AI Recommended</span>
            <span class="ai-badge-confidence">${confText}</span>
          </div>
        `;

        // Keep the tooltip, but don’t rely on it
        badge.title = reason || "";

        const header = document.createElement("div");
        header.className = "hookHeader";

        header.appendChild(badge);
        card.appendChild(header);

        // Why text:
        // - auto-show for CLEAR
        // - otherwise collapsed/hidden unless you want it always
        if (reason) {
          const why = document.createElement("div");
          why.className = "hookWhy subtle";
          why.textContent = reason;

          if (!shouldAutoShowWhy(conf)) {
            why.classList.add("hidden"); // keep it quiet for close/moderate
          }

          card.appendChild(why);

          // For moderate/close: clicking the badge toggles WHY
          badge.addEventListener("click", (e) => {
            e.stopPropagation();
            why.classList.toggle("hidden");
          });
        }
      }


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

  // Lock visual state when user selects a hook
  if (selectedHook) {
    document.querySelectorAll(".hookCard").forEach(card => {
      card.classList.add("locked");
    });
  }

  const lab = document.getElementById("hookLab");
  if (lab) lab.classList.remove("hidden");
}

function updateHookLockUI() {

  
  const clearBtn = document.getElementById("clearHookBtn");
  const lockBar = document.getElementById("hookLockedBar");

  if (!clearBtn) return;

  if (selectedHook) {
    // 🔒 Locked state
    lockBar?.classList.remove("hidden");
    clearBtn.classList.remove("hidden");

    // Visual lock on hook cards
    document.querySelectorAll(".hookCard").forEach(card => {
      card.classList.add("hook-locked");
    });

  } else {
    // 🔓 Unlocked state
    lockBar?.classList.add("hidden");
    clearBtn.classList.add("hidden");

    document.querySelectorAll(".hookCard").forEach(card => {
      card.classList.remove("hook-locked");
    });
  }
}

function clearSelectedHook() {
  selectedHook = null;

  // Remove selection visuals
  document.querySelectorAll(".hookCard").forEach(card => {
    card.classList.remove("selected", "hook-locked");
  });

  // Hide selected hook bar
  document.getElementById("selectedHookBar")?.classList.add("hidden");

  // Re-render hooks so AI recommendations re-appear
  if (lastGeneratedHooks.length) {
    renderHookLab(lastGeneratedHooks);
  }

  updateHookLockUI();
}

function selectHook(text) {

  // 🚫 HARD LOCK: do nothing if already locked
  if (selectedHook && selectedHook !== text) {


    setStatus(
      "hookLabStatus",
      "🔒 Hook is locked — clear it to choose another",
      "info"
    );
    return;
  }

  selectedHook = text;

  updateHookLockUI();

  // 🔥 Re-render so AI green recommended border is removed after user selection
if (lastGeneratedHooks.length) {
  renderHookLab(lastGeneratedHooks);
}

  // Remove previous highlight
  document.querySelectorAll(".hookCard").forEach(c =>
    c.classList.remove("selected")
  );

  // Highlight selected
  document.querySelectorAll(".hookCard").forEach(c => {
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

function sessionQS() {
  const s = getActiveSession();
  console.log("[API] Using session:", s);
  return "?session=" + encodeURIComponent(s);
}


function getActiveSession() {
  if (!ACTIVE_SESSION) {
    console.warn("[SESSION] ACTIVE_SESSION unset, forcing default");
    ACTIVE_SESSION = "default";
  }
  return ACTIVE_SESSION;
}

function toast(message, duration = 2500) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;

  document.body.appendChild(el);

  requestAnimationFrame(() => el.classList.add("show"));

  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, duration);
}

async function setActiveSession(name) {
  const safe = sanitizeSessionName(name);
  ACTIVE_SESSION = safe;

  // ----------------------------
  // Reset AI apply / undo state
  // ----------------------------
  window.aiUndoSnapshot = null;

  document
    .getElementById("undoAiRecommendationBtn")
    ?.classList.add("hidden");

  // ----------------------------
  // Reset per-session frontend state
  // ----------------------------
  workingClipOrder = [];
  clipOrderDirty = false;
  selectedHook = null;

  // 🔥 VARIANTS RESET (you were missing this)
  lastVariantStatus = null;
  VARIANT_POLL_ACTIVE = false;
  window.lastGeneratedVariants = [];
  updateVariantRunningBadge("idle");

  // 🔥 ANALYSIS badge reset (safe default)
  updateAnalyzingBadge?.("idle");

  // ----------------------------
  // Persist + sync session UI
  // ----------------------------
  updateSessionLabels();
  sidebarSyncActiveLabel();
  localStorage.setItem("activeSession", ACTIVE_SESSION);

  // ----------------------------
  // Load core state
  // ----------------------------
  await loadConfigAndYaml();
  await loadCaptionsFromYaml();
  updateCaptionBaselineHint();
  updateLoadYamlVisibility();
  await refreshHookScore();
  await refreshStoryFlowScore();

  // ----------------------------
  // Secondary refreshes
  // ----------------------------
  loadUploadManager();
  refreshAnalyses();
  loadSessionDropdown();
  loadSessions();
  sidebarLoadSessions();

  requestAnimationFrame(() =>
    requestAnimationFrame(animateSessionGlow)
  );

  // ----------------------------
  // Resume analysis polling ONLY if needed
  // ----------------------------
  pollAnalyzeStatus();

  // ----------------------------
  // AI readiness summary
  // ----------------------------
  loadAISetupSummary();
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
  if (!el) return; // ✅ correct place

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


async function jsonFetch(url, opts = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(opts.headers || {})
  };

  const res = await fetch(url, {
    credentials: "same-origin",
    ...opts,
    headers
  });

  if (!res.ok) {
    throw new Error(`[jsonFetch] ${url} failed (${res.status})`);
  }

  const text = await res.text();
  if (!text || text.startsWith("<")) return null;
  return JSON.parse(text);
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

    function markPreviewUploaded() {
        // Visually mark the preview rows as done before clearing
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

    function clearSelectedUploadsUI({ showToast = true, delayMs = 2200 } = {}) {
        // Show a short success pause so user sees confirmation
        setTimeout(() => {
            selectedFiles = [];
            preview.innerHTML = "";
            fileInput.value = ""; // important: allows re-uploading same filename(s)
            uploadBtn.disabled = true;

            // Optional: collapse progress UI after done
            progressWrapper.classList.add("hidden");
            progressBar.style.width = "0%";

            if (showToast) {
                // Keep your existing status line
                // (no-op if you prefer)
            }
        }, delayMs);
    }

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
                const count = resp.uploaded?.length || 0;

                statusEl.textContent = `✅ Uploaded ${count} file(s).`;
                progressBar.style.width = "100%";

                // ✅ visually mark as completed (optional polish)
                markPreviewUploaded();

                // Refresh S3 manager list (raw/processed)
                loadUploadManager();

                // ✅ auto-clear selected uploads list after a short pause
                clearSelectedUploadsUI({ delayMs: 2200 });

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
  const originalText = analyzeBtn.textContent;
  analyzeBtn.textContent = "Analyzing…";

  setStatus(
    "analyzeStatus",
    "Starting analysis…",
    "working",
    false
  );

  try {
    const data = await jsonFetch(
      `/api/analyze?session=${encodeURIComponent(getActiveSession())}`,
      { method: "POST" }
    );

    if (data.status === "no_videos") {
      throw new Error("No raw uploads found in session");
    }

    // 🔁 already running OR just started → same behavior
    if (data.status === "already_running" || data.status === "started") {
      setStatus(
        "analyzeStatus",
        "Analysis running in background…",
        "working"
      );

      updateAnalyzingBadge("running");
      ANALYZE_POLL_ACTIVE = true;
      pollAnalyzeStatus();
      return;
    }

  } catch (err) {
    console.error(err);
    setStatus(
      "analyzeStatus",
      `Error during analysis: ${err.message}`,
      "error"
    );
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = originalText;
  }
}

function scrollToStep(stepSelector) {
  const el = document.querySelector(stepSelector);
  if (!el) return;

  el.scrollIntoView({
    behavior: "smooth",
    block: "start"
  });

  // Optional: sync stepper UI if you already do this elsewhere
  document.querySelectorAll(".step").forEach(btn => {
    btn.classList.toggle(
      "active",
      btn.dataset.target === stepSelector
    );
  });
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

async function loadAISetupSummary() {
  const data = await jsonFetch(
    `/api/ai_setup_summary?session=${getActiveSession()}`
  );

  const el = document.getElementById("aiSetupSummary");
  if (!el || !data) return;

  // 🔒 Still not ready → keep polling alive
  if (!data.has_analysis) {
    el.classList.add("hidden");
    return;
  }

  // ✅ ANALYSIS IS FULLY READY — END ANALYZE STATE HERE
  ANALYZE_POLL_ACTIVE = false;
  updateAnalyzingBadge("idle");

  setStatus("analyzeStatus", "Analysis complete ✓", "success");
  setTimeout(() => setStatus("analyzeStatus", ""), 2000);

  // ---------------------------
  // Render summary card
  // ---------------------------
  el.innerHTML = `
    <div class="ai-summary-card">
      <h3>🧠 AI Readiness Summary</h3>
      <p class="hint-text subtle">
        Next: generate hooks and captions to see AI recommendations.
      </p>

      <ul>
        <li>🎬 <b>${data.clips}</b> clips analyzed</li>
        <li>🏷 Labels: <b>${data.labels.quality}</b>
          ${data.labels.weak ? `( ${data.labels.weak} improved )` : ""}
        </li>
        <li>🔥 Best hook confidence: <b>${data.hook_confidence}</b></li>
        <li>🎯 Recommended goal: <b>${data.recommended_goal}</b></li>
        <li>⏱ Estimated length: <b>${data.estimated_length ?? "—"}</b></li>
      </ul>

      <button id="goToVariantsBtn" class="btn primary small">
        Improve hooks and captions →
      </button>

      <div id="improveHooksStatus" class="status-text subtle"></div>
    </div>
  `;

  el.classList.remove("hidden");

  // ---------------------------
  // CTA button logic
  // ---------------------------
  const goBtn = el.querySelector("#goToVariantsBtn");
  if (!goBtn) return;

  goBtn.onclick = async () => {
    goBtn.disabled = true;
    goBtn.classList.add("ui-busy");

    try {
      setStatus("improveHooksStatus", "Preparing storyboard…", "working");

      const yaml = await jsonFetch(
        `/api/config?session=${encodeURIComponent(getActiveSession())}`
      );

      const hasYaml =
        yaml?.yaml &&
        yaml.yaml.includes("first_clip") &&
        yaml.yaml.includes("middle_clips");

      // 🔍 Check YAML state first (race-condition safe)
const yamlStatus = await jsonFetch(
  `/api/generate_yaml/status?session=${getActiveSession()}`
);

if (yamlStatus?.status === "done") {
  // ✅ YAML already ready → scroll immediately
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      scrollToStep("#step-3");
    });
  });
  return;
}

// ⏳ YAML not ready → async flow
PENDING_SCROLL_TO_STORYBOARD = true;
await generateYamlAsync();
return;

      // Show storyboard continue CTA
      document
        .querySelector(".storyboard-continue")
        ?.classList.remove("hidden");

      // Reveal hook lab
      document.getElementById("hookLab")?.classList.remove("hidden");

      // Open variants drawer silently
      if (typeof openVariantsPanel === "function") {
        openVariantsPanel({ silent: true });
      }

      // Scroll to storyboard (source of truth)
      requestAnimationFrame(() => {
        document
          .querySelector(".storyboard-panel")
          ?.scrollIntoView({
            behavior: "smooth",
            block: "start"
          });
      });

      setStatus(
        "captionStatus",
        "Review clip order first — hooks and captions build from this.",
        "info",
        false
      );

      setStatus("improveHooksStatus", "Hook Lab ready ✓", "success");
      setTimeout(() => setStatus("improveHooksStatus", ""), 2000);

    } catch (err) {
      console.error(err);
      setStatus(
        "improveHooksStatus",
        "Something went wrong preparing hooks",
        "error"
      );
    } finally {
      goBtn.disabled = false;
      goBtn.classList.remove("ui-busy");
    }
  };
}

async function retryAnalysis() {
  const status = await jsonFetch(
    `/api/analyze_status?session=${getActiveSession()}`
  );

  if (status.status === "running") {
    toast("Analysis already running…");
    pollAnalyzeStatus();
    return;
  }

  toast("Restarting analysis…");
  await analyzeClips();
}

function updateAnalyzingBadge(status) {
  const badge = document.getElementById("analyzingBadge");
  if (!badge) return;

  if (status === "running") {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

async function pollAnalyzeStatus() {
  if (!ANALYZE_POLL_ACTIVE) return;

  try {
    const data = await jsonFetch(
      `/api/analyze_status?session=${getActiveSession()}`
    );

    const status = data.status;
    updateAnalyzingBadge(status);

    // -----------------------------
    // RUNNING
    // -----------------------------
    if (status === "running") {
      setStatus(
        "analyzeStatus",
        "Analyzing clips & preparing AI insights…",
        "working",
        false
      );

      lastAnalyzeStatus = "running";
      setTimeout(pollAnalyzeStatus, 1200);
      return;
    }

    // -----------------------------
    // DONE (robust — no transition required)
    // -----------------------------
    if (status === "done") {
      console.log("✅ Analysis finished");

      setStatus(
        "analyzeStatus",
        "AI analysis ready ✓",
        "success",
        false
      );

      // 🔄 Refresh analysis-driven UI
      await refreshAnalyses();
      loadAISetupSummary();

      ANALYZE_POLL_ACTIVE = false;
      lastAnalyzeStatus = null;
      return;
    }

    // -----------------------------
    // UNKNOWN / IDLE → stop polling
    // -----------------------------
    ANALYZE_POLL_ACTIVE = false;
    lastAnalyzeStatus = null;

  } catch (err) {
    console.warn("pollAnalyzeStatus failed", err);

    // retry only if still active
    if (ANALYZE_POLL_ACTIVE) {
      setTimeout(pollAnalyzeStatus, 2000);
    }
  }
}


async function applyAIRecommendation() {
  const session = getActiveSession();

  const applyBtn = document.getElementById("applyAiRecommendationBtn");
  const undoBtn  = document.getElementById("undoAiRecommendationBtn");

  const variant = window.lastGeneratedVariants
    ?.find(v => v.recommended === true);

  if (!variant) {
    alert("No AI recommendation available.");
    return;
  }

  const ok = confirm(
    "Apply AI-recommended captions, timings, and overlay?\n\nYou can undo this."
  );
  if (!ok) return;

  try {
    // 🔒 SNAPSHOT FULL YAML (UNDO SAFETY)
    const before = await jsonFetch(
      `/api/config?session=${encodeURIComponent(session)}`
    );

    window.aiUndoSnapshot = {
  session,
  yaml: before.yaml,
  config: before.config   // IMPORTANT for full restore
};
    updateAIRecommendationBar();

    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = "Applying…";
    }

    // 1️⃣ Apply captions
    await jsonFetch("/api/apply_variant", {
      method: "POST",
      body: JSON.stringify({
        session,
        text: variant.text
      })
    });

    // 2️⃣ Apply smart timings
    await jsonFetch("/api/timings", {
      method: "POST",
      body: JSON.stringify({
        session,
        smart: true
      })
    });

    // 3️⃣ Apply overlay style
    await jsonFetch("/api/overlay", {
      method: "POST",
      body: JSON.stringify({
        session,
        style: "ai_recommended"
      })
    });

    // 🔄 Refresh everything
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();
    await refreshHookScore();
    await refreshStoryFlowScore();

    toast("AI recommendation applied ✅");


    if (applyBtn) {
      applyBtn.textContent = "Applied ✓";
      applyBtn.disabled = true;
    }

    if (undoBtn) {
      undoBtn.classList.remove("hidden");
      undoBtn.disabled = false;
    }

  } catch (err) {
    console.error(err);
    toast("Failed to apply AI recommendation");

    if (applyBtn) {
      applyBtn.disabled = false;
      applyBtn.textContent = "Apply AI recommendation";
    }
  }
}

function renderSetupSummary(summary) {
  const el = document.getElementById("aiSetupSummary");
  if (!el) return;

  if (!summary.has_analysis) {
    el.classList.add("hidden");
    return;
  }

  el.classList.remove("hidden");

  el.innerHTML = `
    <div class="ai-summary-card">
      <div class="ai-summary-title">🧠 AI Setup Summary</div>

      <div class="ai-summary-row">
        <strong>🎬 Clips analyzed</strong>
        <span>${summary.clips}</span>
      </div>

      <div class="ai-summary-row">
        <strong>🏷 Labels</strong>
        <span>${summary.labels.quality}</span>
      </div>

      <div class="ai-summary-row">
        <strong>🔥 Best hook confidence</strong>
        <span>${summary.hook_confidence}</span>
      </div>

      <div class="ai-summary-row">
        <strong>🎯 Recommended goal</strong>
        <span>${summary.recommended_goal}</span>
      </div>

      <div class="ai-summary-row">
        <strong>⏱ Estimated length</strong>
        <span>${summary.estimated_length}</span>
      </div>
    </div>
  `;
}

async function pollYamlStatus() {
  if (!YAML_POLL_ACTIVE) return;

  try {
    const data = await jsonFetch(
      `/api/generate_yaml/status?session=${getActiveSession()}`
    );

    const status = data?.status;

    // -----------------------------
    // RUNNING
    // -----------------------------
    if (status === "running") {
      if (lastYamlStatus !== "running") {
        setStatus(
          "yamlStatus",
          "Building storyboard with AI…",
          "working",
          false
        );
      }

      lastYamlStatus = "running";
      setTimeout(pollYamlStatus, 1200);
      return;
    }

    // -----------------------------
    // DONE (transition-based)
    // -----------------------------
    if (lastYamlStatus === "running" && status === "done") {
      setStatus("yamlStatus", "Finalizing storyboard…", "working", false);

      await loadConfigAndYaml();
      await loadCaptionsFromYaml();

      setStatus("yamlStatus", "Storyboard ready ✓", "success");

      YAML_POLL_ACTIVE = false;
      lastYamlStatus = null;

      // ✅ Scroll ONLY if user intended it
      if (PENDING_SCROLL_TO_STORYBOARD) {
        PENDING_SCROLL_TO_STORYBOARD = false;

        requestAnimationFrame(() => {
          scrollToStep("#step-3");
        });
      }

      return;
    }

    // -----------------------------
    // IDLE / UNKNOWN → stop polling
    // -----------------------------
    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;

  } catch (err) {
    console.warn("pollYamlStatus failed", err);

    // ⛔ Stop polling on hard failures (404, server restart)
    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;

    setStatus(
      "yamlStatus",
      "Storyboard generation interrupted — try again",
      "error"
    );
  }
}

async function generateYamlAsync() {
  // ----------------------------------
  // Init + intent
  // ----------------------------------
  YAML_POLL_ACTIVE = true;
  lastYamlStatus = "running"; // 🔑 prevents missing fast 'done'
  setStatus(
    "yamlStatus",
    "Building storyboard with AI…",
    "working",
    false
  );

  // ----------------------------------
  // Start polling BEFORE request
  // ----------------------------------
  pollYamlStatus();

  try {
    // ----------------------------------
    // Kick off backend generation
    // ----------------------------------
    await jsonFetch("/api/generate_yaml", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession()
      }),
    });
  } catch (err) {
    console.error("generateYamlAsync failed", err);

    setStatus(
      "yamlStatus",
      "Failed to start storyboard generation",
      "error"
    );

    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;
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

  if (!yamlTextEl || !yamlPreviewEl) {
    console.warn("[CONFIG] YAML elements missing, skipping load");
    return;
  }

  if (CONFIG_LOADING) return;
  CONFIG_LOADING = true;

  try {
    const session = encodeURIComponent(getActiveSession());
    const data = await jsonFetch(`/api/config?session=${session}`);

    yamlTextEl.value = data.yaml || "# No config.yml yet.";
    yamlPreviewEl.textContent = JSON.stringify(data.config || {}, null, 2);

    renderStoryboardTimeline(data.config);
  } catch (err) {
    console.error("loadConfigAndYaml failed", err);
  } finally {
    CONFIG_LOADING = false;
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

// ⬇️ ADD THIS
autoSaveStoryboardOrder();

setStatus(
  "storyboardStatus",
  "Saving clip order…",
  "working"
);
}

// ================================
// Storyboard Order — Save (AUTO)
// ================================
async function saveStoryboardOrder({ silent = false } = {}) {
  try {
    const session = getActiveSession();
    const sessionQ = encodeURIComponent(session);

    // 1️⃣ Load latest config
    const data = await jsonFetch(`/api/config?session=${sessionQ}`);
    const cfg = data.config || {};

    // 2️⃣ Rebuild storyboard from workingClipOrder
    cfg.first_clip = workingClipOrder[0] || null;

    if (workingClipOrder.length > 2) {
      cfg.middle_clips = workingClipOrder.slice(1, -1);
    } else {
      cfg.middle_clips = [];
    }

    cfg.last_clip =
      workingClipOrder.length > 1
        ? workingClipOrder[workingClipOrder.length - 1]
        : null;

    // 3️⃣ Save config
    await jsonFetch("/api/save_config", {
      method: "POST",
      body: JSON.stringify({
        session,
        config: cfg
      })
    });

    // 🔑 THIS is what you were missing
    await loadCaptionsFromYaml();

    if (!silent) {
      setStatus("storyboardStatus", "Clip order saved ✓", "success");
    } else {
      showAutoSaveStatus("storyboardStatus");
    }

    clipOrderDirty = false;

  } catch (err) {
    console.error("Failed to save storyboard order:", err);
    setStatus("storyboardStatus", "Failed to save order", "error");
  }
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

function handleHookScoreSideEffects(score) {
  // Story Flow lock
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

  // Improve buttons
  updateImproveButtons(score, null);

  // Rewrite warning
  if (score < 60) {
    setStatus(
      "overlayStatus",
      "⚠ Hook is weak — improve hook before rewriting captions.",
      "warning",
      false
    );
  } else {
    clearOverlayWarning();
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

// Hooks only require analysis, not captions
if (!window.lastGeneratedHooks || !window.lastGeneratedHooks.length) {
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
    handleHookScoreSideEffects(score);

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

async function undoAIRecommendation() {
  const snapshot = window.aiUndoSnapshot;

  if (!snapshot?.yaml) {
    toast("Nothing to undo");
    return;
  }

  if (snapshot.session !== getActiveSession()) {
    alert("Undo is only available for the last AI apply in this session.");
    return;
  }

  try {
    await jsonFetch("/api/save_config", {
      method: "POST",
      body: JSON.stringify({
        session: snapshot.session,
        config: snapshot.config
      })
    });

    // 🔄 FULL UI RESTORE
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();
    await refreshHookScore();
    await refreshStoryFlowScore();

    window.aiUndoSnapshot = null;
    updateAIRecommendationBar();

    toast("AI changes undone ✓");

  } catch (err) {
    console.error(err);
    toast("Failed to undo AI changes");
  }
}


function openVariantsPanel() {
  const drawer = document.getElementById("variantsDrawer");
  const btn = document.getElementById("variantsToggleBtn");

  if (!drawer) return;

  drawer.classList.remove("closed");
  drawer.classList.add("open");

  if (btn) btn.textContent = "Collapse";
}


// Alias used by caption system
async function refreshOverlayPreview() {
  return previewOverlay("fast");
}

// =============================================
// Apply selected generated caption variant
// =============================================

async function applyCaptionVariant(text, meta = {}) {

  // 🔒 User took control — AI no longer owns state
  window.aiUndoSnapshot = null;
  updateAIRecommendationBar();

  const { id, tone, intent } = meta;
  
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

    document.getElementById("step4CaptionScroll")?.classList.add("hidden");
    document.getElementById("rewriteDecisionBar")?.classList.add("hidden");

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

  if (hookBtn && typeof hookScore === "number") {
  hookBtn.classList.toggle("hidden", hookScore >= 80); // show if weak
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
  const rewriteRadio = document.querySelector(
    'input[name="captionRewriteMode"][value="rewrite"]'
  );
  const captionBox = document.querySelector(".caption-mode");

  if (!rewriteRadio) return;

  const hasText = text && text.length > 3;

  const hookScore = Number(
    document.getElementById("hookScoreValue")
      ?.textContent?.split("/")[0] || 0
  );

  const rewriteAllowed =
    hasText &&
    hookScore >= 60 &&
    !rewritePending &&
    !isInRewriteReview;

  rewriteRadio.disabled = !rewriteAllowed;
  rewriteRadio.parentElement.style.opacity = rewriteAllowed ? "1" : "0.4";

  if (!rewriteAllowed) {
    clearOverlayWarning();
  }

  if (rewriteAllowed && rewriteRadio.checked) {
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
  setUiBusy(true);
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

    lastGeneratedVariants = [];
    updateCaptionBaselineHint();
    updateLoadYamlVisibility();
  } catch (err) {
    console.error(err);
    setCaptionInlineStatus("Failed to load captions", "error");
    setCaptionSource("yaml", "⚠ SOURCE: YAML (failed)");
  }
  finally{
    setUiBusy(false);
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
  setUiBusy(true);
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
  finally{
    setUiBusy(false);
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

  // 🔥 MUST BE FIRST — before ANY fetch
  try {
    const stored = localStorage.getItem("activeSession");
    ACTIVE_SESSION = sanitizeSessionName(stored || "default");
  } catch {
    ACTIVE_SESSION = "default";
  }

  console.log("[SESSION INIT]", ACTIVE_SESSION);
  
  setTimeout(() => {
  pollAnalyzeStatus();
}, 300);

  document
  .getElementById("applyAiRecommendationBtn")
  ?.addEventListener("click", applyAIRecommendation);

  const clearHookBtn = document.getElementById("clearHookBtn");

if (clearHookBtn) {
  clearHookBtn.addEventListener("click", (e) => {
    e.stopPropagation(); // safety
    clearSelectedHook();
  });
}


   // -------------------------------
  // Intent pill wiring (FIXED)
  // -------------------------------
  const pillContainer = document.querySelector(".intent-pills");
  if (pillContainer) {
    pillContainer.addEventListener("click", async (e) => {
      const pill = e.target.closest(".pill");
      if (!pill) return;

      const intent = pill.dataset.intent;
      if (!intent || intent === currentIntent) return;

      // 🔑 Update core state
      currentIntent = intent;

      // 🎨 Sync UI
      syncIntentPills(intent);

      // 🔔 Update hint
      updateIntentHint(intent);

      // 💾 Persist intent (optional)
      if (typeof saveIntent === "function") {
        await saveIntent(intent);
      }

      // 🔄 Re-score hooks if needed
      if (typeof refreshHookScore === "function") {
        refreshHookScore();
      }

      // 📣 Feedback
      setStatus(
        "hookLabStatus",
        `Intent set to “${intent}”`,
        "info"
      );
    });
  }

    // 🔄 Resume AI variants if page refreshed mid-run
  try {
    const data = await jsonFetch(
      `/api/variants/status?session=${getActiveSession()}`
    );

    if (data.status === "running") {
      updateVariantRunningBadge("running");

      if (!VARIANT_POLL_ACTIVE) {
        VARIANT_POLL_ACTIVE = true;
        pollVariantStatus();
      }
    }
  } catch (err) {
    console.warn("Failed to resume variant polling on load", err);
  }

  // 🔄 Resume YAML generation if page refreshed mid-run
// try {
//   const data = await jsonFetch(
//     `/api/generate_yaml/status?session=${getActiveSession()}`
//   );

//   if (data.status === "running") {
//     YAML_POLL_ACTIVE = true;
//     pollYamlStatus();
//   }
// } catch (err) {
//   console.warn("Failed to resume YAML polling", err);
// }

document
  .getElementById("undoAiRecommendationBtn")
  ?.addEventListener("click", undoAIRecommendation);
  
  loadIntentFromConfig();

    // Sync labels
    updateSessionLabels();
    sidebarSyncActiveLabel();

    syncCtaUIState();

    const intentSelect = document.getElementById("intentSelect");

    if (intentSelect) {
      intentSelect.addEventListener("change", () => {
        currentIntent = intentSelect.value;

        // Persist intent in session
        saveIntent(currentIntent);

        // Refresh hook score + AI recommendations
        refreshHookScore();
      });
    }
    
     document
 .getElementById("continueToHooksBtn")
 ?.addEventListener("click", async () => {

   // 🔄 CRITICAL: sync YAML → captions BEFORE storyboard UI
   await loadConfigAndYaml();
   await loadCaptionsFromYaml();

   // Optional but recommended
   await refreshHookScore();
   await refreshStoryFlowScore();

   // ➡️ NOW move into storyboard / hook lab
   openVariantsPanel();

   requestAnimationFrame(() => {
     document
       .getElementById("hookLab")
       ?.scrollIntoView({
         behavior: "smooth",
         block: "start"
       });
   });

document
  .getElementById("confirmStoryboardBtn")
  ?.addEventListener("click", () => {
    // Scroll to Hook Lab
    requestAnimationFrame(() => {
      document
        .getElementById("hookLab")
        ?.scrollIntoView({
          behavior: "smooth",
          block: "start"
        });
    });

    // Optional: highlight Hook Lab
    highlightHookLab?.();
  });

    const hookLab =
      document.getElementById("hookLab") ||
      document.getElementById("variantsDrawer");

    requestAnimationFrame(() => {
      hookLab?.scrollIntoView({
        behavior: "smooth",
        block: "start"
      });
    });
  });


async function saveIntent(intent) {
  const session = getActiveSession();
  const sessionQ = encodeURIComponent(session);

  // 1) Load current config
  const data = await jsonFetch(`/api/config?session=${sessionQ}`);
  const cfg = data.config || {};

  // 2) Update ONLY intent
  cfg.intent = intent;

  // 3) Save merged config
  await jsonFetch("/api/save_config", {
    method: "POST",
    body: JSON.stringify({
      session,
      config: cfg
    })
  });
}

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

// ================================
// Mobile Session Panel toggle
// ================================

  document
    .getElementById("mobileSessionBtn")
    ?.addEventListener("click", toggleMobileSessionPanel);

  document
    .getElementById("mobileCloseSessionBtn")
    ?.addEventListener("click", toggleMobileSessionPanel);

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

    document
  .getElementById("undoAiRecommendationBtn")
  ?.addEventListener("click", undoAIRecommendation);


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
  .getElementById("applyAiRecommendationBtn")
  ?.addEventListener("click", applyAIRecommendation);


    document
    .getElementById("generateYamlBtn")
    ?.addEventListener("click", generateYamlAsync);

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
  if (suppressNextPreview) {
    suppressNextPreview = false;
    return;
  }

  await saveOverlayStyle({ silent: true });     // saves + reloads config/yaml
  showAutoSaveStatus("overlayStyleStatus");     // shows "Saved ✓"
  await previewOverlay("fast");                 // preview refresh
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
    document.getElementById("improveHookBtn")?.addEventListener("click", async () => {
    clearSelectedHook();   // 🔓 unlock user-forced hook
    await improveHook();   // 🤖 give control back to AI
  });
      // PREVIEW REWRITE — must be inside DOMContentLoaded so button exists
    document.getElementById("previewRewriteBtn")?.addEventListener("click", () => {
      if (typeof previewRewrite === "function") {
       previewRewrite();
      }
      else console.warn("previewRewrite() not defined");
});


    // BUTTON EVENTS
    document.getElementById("previewFast")?.addEventListener("click", () => previewOverlay("fast"));
    document.getElementById("previewFull")?.addEventListener("click", () => previewOverlay("full"));
    document.getElementById("previewStyleBtn")?.addEventListener("click", () => previewOverlay("fast")); // button you already have


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
document
  .getElementById("generateVariantsBtn")
  ?.addEventListener("click", async () => {

    const modes = {
      rewrite: document.getElementById("mode_rewrite")?.checked,
      hook: document.getElementById("mode_hook")?.checked,
      punchy: document.getElementById("mode_punchy")?.checked,
      story: document.getElementById("mode_story")?.checked,
      influencer: document.getElementById("mode_influencer")?.checked,
      minimal: document.getElementById("mode_minimal")?.checked,
    };

    await generateVariantsAsync(modes, selectedHook || null);
  });

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
    updateHookLockUI();

});