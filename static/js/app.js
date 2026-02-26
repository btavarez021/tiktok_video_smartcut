  // ================================
  // Variables
  // ================================
  let previewAudio = null;
  let previewPlaying = false;

  let workingClipOrder = [];
  let clipOrderDirty = false;

  let LAST_READINESS_STATUS = null;

  // 🔵 Active session (hotel / batch)
  let ACTIVE_SESSION = "default";

  let ACTIVE_EXPORT_TASK = null;

  let rewriteCommitted = false;

  let intentLockedByUser = false;
  let REFRESH_LOCK = false;

  let suppressNextPreview = false;

  let lastSavedCaptionsText = "";
  let lastHookScoreBeforeEdit = null;


  let workingCaptionsText = "";

  let rewritePending = false;

  let CONFIG_LOADING = false;

  let LAST_HOOK_SCORE = null;
  let LAST_FLOW_SCORE = null;

  let LAST_DIRECTOR_SIGNATURE = null;
  let EDIT_STRATEGY_LOADING = false;
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

  let CONFIG_CACHE = null;

  // =======================================
  // GLOBAL APP STATE (Single Source of Truth)
  // =======================================

  window.appState = {
    session: null,

    hook: {
      selected: null,
      intent: "discovery",
      locked: false,
      lastGenerated: []
    },

    variants: {
      modes: {},
      list: [],
      recommendedId: null,
      generating: false
    },

    captions: {
      baseline: "",
      current: "",
      source: "none"
    },

    storyboard: {
      order: []
    },

    scores: {
      hook: null,
      storyFlow: null
    },

    ui: {
      yamlPolling: false
    }
  };

  async function getConfigCached(force = false) {
    if (CONFIG_CACHE && !force) return CONFIG_CACHE;

    const session = encodeURIComponent(getActiveSession());
    const data = await jsonFetch(`/api/config?session=${session}`);

    CONFIG_CACHE = data;
    return data;
  }


  function setCurrentVideoIntent(intent) {
    window.appState.hook.intent = intent;
    console.log("🎯 Video intent set to:", intent);
  }

  function openStep(stepId) {
    document.querySelector(`.step[data-target="${stepId}"]`)?.click();

  }

  function openVariantsDrawer() {
    const drawer = document.getElementById("variantsDrawer");
    if (!drawer) return;

    drawer.classList.remove("closed");

    const btn = document.getElementById("variantsToggleBtn");
    if (btn) btn.textContent = "Collapse";
  }

    function maybeCelebrateReadiness(state) {
      if (LAST_READINESS_STATUS !== "ready" && state.status === "ready") {
        toast("🚀 Publish Ready — AI approves this edit");
        pulseExportButton();
        // maybeConfetti?.(); // optional
      }
      LAST_READINESS_STATUS = state.status;
    }

  // ========================================
  // GLOBAL STATE SNAPSHOT (Debug + Stability)
  // ========================================
  function getAppState() {
    return {
      hookScore: LAST_HOOK_SCORE,
      flowScore: LAST_FLOW_SCORE,
      captionsLength: (lastSavedCaptionsText || "").length,
      rewritePending,
      rewriteCommitted,
      hooksReady: !!window.appState.hook.lastGenerated?.length,
      clipOrderDirty,
      intent: window.appState.hook.intent,
      yamlPolling: YAML_POLL_ACTIVE,
      variantPolling: VARIANT_POLL_ACTIVE,
    };
  }

  function logAppState(label = "STATE") {
    console.log(`🧠 ${label} →`, getAppState());
  }

  function autoExpandIfWeak(hookScore, flowScore) {
    const hookBody = document.getElementById("hookDetailsBody");
    const storyBody = document.getElementById("storyDetailsBody");

    if (!hookBody || !storyBody) return;

    if (typeof hookScore === "number" && hookScore < 60) {
      hookBody.classList.remove("collapsed");
    }

    if (typeof flowScore === "number" && flowScore < 60) {
      storyBody.classList.remove("collapsed");
    }
  }



  async function refreshAfterChange({
    hooks = true,
    flow = true,
    director = true,
    publish = true,
    progress = true,
    guidance = true
  } = {}) {

    if (REFRESH_LOCK){
      console.log("Refreshed skipped(locked)");
      return;
    }

    REFRESH_LOCK = true;

    try {
      logAppState("Before refresh");

      // 1) Scores
      if (hooks) await refreshHookScore();
      if (flow)  await refreshStoryFlowScore();

      autoExpandIfWeak(LAST_HOOK_SCORE, LAST_FLOW_SCORE);

      // 2) Publish + progress should key off the same source of truth
      // 2) Unified Evaluation Engine
      const creativeState = evaluateCreativeState();

      // Publish readiness now comes from engine
      if (publish) renderPublishReadyState(creativeState);

      // Progress still updates
      if (progress) setTimeout(renderEditProgress, 50);

      // 3) Director (only when scores exist)
      if (
        director &&
        lastSavedCaptionsText?.trim() &&
        !YAML_POLL_ACTIVE &&
        LAST_HOOK_SCORE != null &&
        LAST_FLOW_SCORE != null
      ) {
        const creativeState = evaluateCreativeState();
        await loadEditStrategy(creativeState);
      }

      // 4) Guidance
      if (guidance) updateHookLabGuidance();

      logAppState("After refresh");

    } catch (e) {
      console.warn("refreshAfterChange failed", e);
    } finally {
      REFRESH_LOCK = false;
    }
  }

  function openHookLab() {
    const lab = document.getElementById("hookLab");
    if (!lab) return;

    lab.classList.remove("hidden");

    lab.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });

    highlightHookLab?.();
    updateHookLabGuidance();
  }

  function evaluateCreativeState() {
    const hook = window.appState?.scores?.hook ?? LAST_HOOK_SCORE ?? null;
    const flow = window.appState?.scores?.storyFlow ?? LAST_FLOW_SCORE ?? null;
    const hasCaptions = !!lastSavedCaptionsText?.trim();

    if (!hasCaptions) {
      return {
        hook_score: hook,
        flow_score: flow,
        readiness_score: 0,
        publish_ready: false,
        primary_weakness: "No captions",
        status: "empty",
        message: "Create captions to begin.",
        next: "write_captions"
      };
    }

    if (hook < 50) {
      return {
        hook_score: hook,
        flow_score: flow,
        readiness_score: hook,
        publish_ready: false,
        primary_weakness: "Hook clarity",
        status: "weak_hook",
        message: "Your hook needs stronger curiosity or clarity.",
        next: "improve_hook"
      };
    }

    if (hook < 70) {
      return {
        hook_score: hook,
        flow_score: flow,
        readiness_score: hook,
        publish_ready: false,
        primary_weakness: "Hook strength",
        status: "almost_hook",
        message: `Improve hook by ${70 - hook} more points.`,
        next: "improve_hook"
      };
    }

    if (flow < 60) {
      return {
        hook_score: hook,
        flow_score: flow,
        readiness_score: Math.min(hook, flow),
        publish_ready: false,
        primary_weakness: "Story pacing",
        status: "weak_flow",
        message: "Tighten pacing and transitions.",
        next: "improve_flow"
      };
    }

    if (hook >= 75 && flow >= 65) {
      return {
        hook_score: hook,
        flow_score: flow,
        readiness_score: Math.round((hook + flow) / 2),
        publish_ready: true,
        primary_weakness: null,
        status: "ready",
        message: "Strong edit. Ready to publish.",
        next: "publish"
      };
    }

    return {
      hook_score: hook,
      flow_score: flow,
      readiness_score: Math.round((hook + flow) / 2),
      publish_ready: false,
      primary_weakness: null,
      status: "polish",
      message: "Good edit. Minor improvements possible.",
      next: "polish"
    };
  }

  function renderNextActionButton(action) {

    const actions = {
      write_captions: `<button onclick="openStep('#step-3')" class="readiness-btn">Write Captions</button>`,
      improve_hook: `<button onclick="openStep('#step-4')" class="readiness-btn">Improve Hook</button>`,
      improve_flow: `<button onclick="openStep('#step-3')" class="readiness-btn">Improve Flow</button>`,
      polish: `<button onclick="openStep('#step-4')" class="readiness-btn">Polish Edit</button>`,
      publish: `<button class="readiness-btn publish-ready">Ready to Export</button>`
    };

    return actions[action] || "";
  }


  function getHookRatingLabel(score) {
    if (score < 45) return "Needs Work";
    if (score < 60) return "Building Strength";
    if (score < 75) return "Strong Hook";
    if (score < 90) return "Standout";
    return "Viral Energy";
  }

  function getFlowRatingLabel(score) {
    if (score < 50) return "Rough";
    if (score < 65) return "Improving";
    if (score < 80) return "Smooth";
    if (score < 90) return "Excellent";
    return "Elite";
  }


  function renderPublishReadyState(state) {
    if (!state) state = evaluateCreativeState();

    const box = document.getElementById("publishReadyBanner");
    if (!box) return;

    maybeCelebrateReadiness({ status: state.status });

    box.classList.remove("hidden");

    let colorClass = "publish-neutral";

    if (state.status === "ready") colorClass = "publish-ready";
    if (state.status === "weak_hook" || state.status === "weak_flow")
      colorClass = "publish-warning";
    if (state.status === "empty") colorClass = "publish-empty";

    box.className = `publish-ready-state ${colorClass}`;

    box.innerHTML = `
      <div class="readiness-title">🧠 AI Readiness</div>
      <div class="readiness-message">${state.message}</div>
      <div class="readiness-scores">
        Hook: ${state.hook_score}/100 &nbsp; | &nbsp; Flow: ${state.flow_score}/100
      </div>
      ${renderNextActionButton(state.next)}
    `;
  }


  function evaluatePublishReadiness() {
    const state = evaluateCreativeState();

    const highIssues =
      document.querySelectorAll(".director-item.impact-high").length;

    return {
      ready: state.publish_ready,
      hookScore: state.hook_score,
      flowScore: state.flow_score,
      highIssues
    };
  }


  function celebrateImprovement(type, oldScore, newScore) {
    const delta = newScore - oldScore;

    // toast
    toast(`⬆ ${type === "hook" ? "Hook" : "Flow"} improved +${delta}`);

    // director approval
    showDirectorApproval(type);

    // small pulse animation
    animateScoreJump(type);
  }

  function maybeShowStep4Nudge() {
    const score =
      Number(document.getElementById("hookScoreValue")?.textContent?.split("/")[0]) || 0;

    const hint = document.getElementById("step3NextHint");

    if (!hint) return;

    // threshold = “good enough”
    if (score >= 50) {
      hint.classList.remove("hidden");
    }
  }


  function showDirectorApproval(type) {
    const area = document.getElementById("editStrategyContext");
    if (!area) return;

    const el = document.createElement("div");
    el.className = "director-approved";
    el.textContent = `🎬 Director approved the ${type}`;

    area.appendChild(el);

    setTimeout(() => {
      el.remove();
    }, 2500);
  }

  function animateScoreJump(type) {
    const id = type === "hook"
      ? "hookScoreValue"
      : "storyFlowScoreValue";

    const el = document.getElementById(id);
    if (!el) return;

    el.classList.remove("score-burst");
    void el.offsetWidth;
    el.classList.add("score-burst");
  }


  function openAccordionSection(title) {
    const headers = document.querySelectorAll("#step-4 .acc-header");

    headers.forEach(h => {
      if (h.textContent.includes(title)) {
        h.click();
      }
    });
  }


  function jumpToEditArea(area) {
    console.log("🎯 Jump to:", area);

    area = (area || "").toLowerCase();

    if (area === "hook") {
      openStep("#step-3");
      openVariantsDrawer();
      openHookLab();
      return;
    }

    if (area === "captions") {
      openStep("#step-3");
      document.getElementById("captionsText")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "overlay") {
      openStep("#step-4");
      openAccordionSection("✍️ Captions & Timing");
      document.getElementById("overlayStyle")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "cta") {
      openStep("#step-4");
      openAccordionSection("🎤 Voice (TTS) & CTA");
      document.getElementById("ctaText")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "pacing") {
      openStep("#step-4");
      openAccordionSection("✍️ Captions & Timing");
      document.getElementById("applyStandardTimingBtn")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    console.warn("No jump rule for:", area);
  }



  function confidenceLabel(level) {
    if (!level) return "";

    return {
    clear: "Clear winner",
    moderate: "Strong option",
    close: "Creative choice"
  }[level] || "";
  }

  // =======================================
  // AI Director auto refresh (debounced)
  // =======================================
  const refreshEditStrategySoon = debounce(() => {
    console.log("🧠 Refreshing AI edit strategy");
    loadEditStrategy();
  }, 400);


  function debounce(fn, wait = 350) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  function showGlobalStatus(text, type = "info") {
    const bar = document.getElementById("globalStatusBar");
    if (!bar) return;

    bar.textContent = text;
    bar.className = `global-status show ${type}`;

    setTimeout(() => {
      bar.classList.remove("show");
    }, 2500);
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
        window.appState.variants.list = variants;

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
            intent: window.appState.hook.intent,
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

  function updateHooksReadyUI() {
    const btn = document.getElementById("continueToHooksBtn");
    if (!btn) return;

    if (window.appState.hook.lastGenerated?.length) {
      btn.classList.add("ai-ready");
      btn.dataset.ready = "true";
    } else {
      btn.classList.remove("ai-ready");
      btn.dataset.ready = "false";
    }
  }

  function updateAIRecommendationBar() {
    const bar = document.getElementById("aiRecommendationBar");
    const applyBtn = document.getElementById("applyAiRecommendationBtn");
    const undoBtn = document.getElementById("undoAiRecommendationBtn");

    if (!bar) return;

    const hasRecommendation =
      Array.isArray(window.appState.variants.list) &&
      window.appState.variants.list.some(v => v.recommended === true);

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

  function hydrateExistingHooksIfAny() {
    const hooks = window.appState.hook.lastGenerated;
    if (!hooks?.length) return;

    renderHookLab(hooks);
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
    Array.isArray(window.appState.variants.list) &&
    window.appState.variants.list.length > 0;


    hint.style.display = hasVariants ? "none" : "block";
  }

  function updateLoadYamlVisibility() {
    const btn = document.getElementById("loadCaptionsFromYamlBtn");
    if (!btn) return;

    const hasVariants =
    Array.isArray(window.appState.variants.list) &&
    window.appState.variants.list.length > 0;

    btn.style.display = hasVariants ? "inline-block" : "none";
  }

  function syncIntentPills(intent) {
    document.querySelectorAll(".intent-pills .pill").forEach(pill => {
      pill.classList.toggle("active", pill.dataset.intent === intent);
    });
  }


  async function loadIntentFromConfig() {
    try {
      const res = await getConfigCached();

      const intent = res?.intent || "discovery";

      // 🔑 Core state
      window.userForcedIntent = false;

      window.appState.hook.intent = intent;

      // ✅ SYNC PILL UI (single source of truth)
      syncIntentPills(intent);

      // 🔔 Update intent hint
      updateIntentHint(intent);

      // Optional legacy select support
      const select = document.getElementById("intentSelect");
      if (select) select.value = intent;

      await refreshAfterChange();


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
          const data = await getConfigCached();
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
          CONFIG_CACHE = null;

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
    // AI badge (smarter hierarchy)
    // ----------------------------
    const badge = recommended
      ? `
        <div class="ai-recommended-badge"
            data-confidence="${confidence}">
          <div class="ai-badge-row">
            <span class="ai-badge-main">⭐ AI Pick</span>
            <span class="ai-badge-confidence">${confLabel}</span>
          </div>
        </div>
      `
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
            ${confidence ? `<div class="variantWhyConfidence">
              Confidence: ${confLabel}
            </div>` : ""}
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
            intent: '${window.appState.hook.intent}',
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

    if (btn) {
      btn.disabled = true;
      btn.textContent = "Generating…";
    }

    if (status) {
      status.textContent = "Generating hooks…";
      status.className = "hook-lab-status loading";
    }

    const out = document.getElementById("hookLabOutput");
    if (out) {
      out.innerHTML = `
        <div class="ai-thinking">
          🧠 AI is crafting strong openings…
        </div>
      `;
    }

    let res = null;

    try {
      res = await jsonFetch("/api/hooks", {
        method: "POST",
        body: JSON.stringify({
          session: getActiveSession(),
          intent: window.appState.hook.intent
        })
      });

    } catch (e) {
      console.warn("Hook fetch warning:", e);
    }

    const hooks = res?.hooks;

    if (Array.isArray(hooks) && hooks.length > 0) {
      // 🔑 global state for hydration / refresh
      window.appState.hook.lastGenerated = hooks;

      // 🔥 mark ready → button glows
      updateHooksReadyUI();

      // If already in Hook Lab, render immediately
      const out = document.getElementById("hookLabOutput");
      if (out) out.classList.remove("show");

      renderHookLab(hooks);
      updateHookLabGuidance();

      requestAnimationFrame(() => {
        out?.classList.add("show");
      });

      if (status) {
        status.textContent = `✓ ${hooks.length} hooks generated`;
        status.className = "hook-lab-status success";
      }

      loadEditStrategy();


    } else {
      if (status) {
        status.textContent = "⚠ Failed to generate hooks";
        status.className = "hook-lab-status error";
      }
    }

    if (btn) {
      btn.disabled = false;
      btn.textContent = "Generate Hooks";
    }

    // If user is already here → scroll to results
    requestAnimationFrame(() => {
      document
        .getElementById("hookLabOutput")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function computeReadinessState(hookScore, flowScore) {

  if (!lastSavedCaptionsText?.trim()) {
    return {
      status: "empty",
      message: "Create captions to begin.",
      next: "write_captions"
    };
  }

  if (hookScore == null || flowScore == null) {
    return {
      status: "loading",
      message: "Calculating AI readiness…",
      next: null
    };
  }

  if (hookScore < 50) {
    return {
      status: "weak_hook",
      message: "Your hook needs stronger curiosity or clarity.",
      next: "improve_hook"
    };
  }

  if (hookScore < 70) {
    return {
      status: "almost_hook",
      message: `Improve hook by ${70 - hookScore} more points.`,
      next: "improve_hook"
    };
  }

  if (flowScore < 60) {
    return {
      status: "weak_flow",
      message: "Tighten pacing and transitions.",
      next: "improve_flow"
    };
  }

  if (hookScore >= 75 && flowScore >= 65) {
    return {
      status: "ready",
      message: "Strong edit. Ready to publish.",
      next: "publish"
    };
  }

  return {
    status: "polish",
    message: "Good edit. Minor improvements possible.",
    next: "polish"
  };
}

  // ================================
  // Hook Lab — Confidence-aware UI helpers
  // ================================
  function normalizeConfidence(c) {
    const v = (c || "").toLowerCase();
    if (v === "clear" || v === "moderate" || v === "close") return v;
    return "close";
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

  function computeVictoryMargin(hooks, currentScore) {
    if (!Array.isArray(hooks)) return 0;

    const scores = hooks
      .map(h => h?.score || 0)
      .sort((a, b) => b - a);

    if (scores.length < 2) return 0;

    const secondBest = scores[0] === currentScore ? scores[1] : scores[0];

    return Math.max(0, Math.round(currentScore - secondBest));
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
        const isSelected = window.appState.hook.selected === h.text;
        const reason = h.recommend_reason || "";
        const intentLabel = h.intent_label || "";


        const confidence = Number(h.confidence || 0);

        let confLabel = "";
        if (confidence >= 0.85) confLabel = "Excellent lead";
        else if (confidence >= 0.7) confLabel = "Strong opener";
        else if (confidence >= 0.55) confLabel = "Good potential";
        else confLabel = "Experimental";


        const card = document.createElement("div");
        card.className = "hookCard";

        // 🔒 GLOBAL RULE:
        // If user selected ANY hook, AI visuals are suppressed
        const allowAiHighlight = !window.appState.hook.selected;

        if (isSelected) {
          card.classList.add("selected");
        }

        // 🤖 AI badge — confidence-aware + never overlays text
  if (isRecommended && allowAiHighlight) {
    const header = document.createElement("div");
    header.className = "hookHeader";

    const margin = computeVictoryMargin(hooks, h.score);

    if (margin >= 20) {
      card.classList.add("blowout");
    }

    // ⭐ Badge
    const badge = document.createElement("div");
    badge.className = "ai-recommended-badge";

    badge.innerHTML = `
      <div class="ai-badge-row">
        <span class="ai-badge-main">⭐ AI Pick</span>
        <span class="ai-badge-confidence">${confLabel}</span>
      </div>
      ${margin > 0 ? `<div class="ai-badge-margin">Wins by +${margin}%</div>` : ""}
      ${intentLabel ? `<div class="ai-badge-intent">${intentLabel}</div>` : ""}
    `;

    header.appendChild(badge);
    card.appendChild(header);

    // WHY SECTION
    if (reason) {
      const toggle = document.createElement("div");
      toggle.className = "variantWhyToggle";
      toggle.textContent = "Why this won ▾";

      const why = document.createElement("div");
      why.className = "variantWhy hidden";
      why.innerHTML = `
        ${reason}
        <div class="variantWhyConfidence">
          Confidence: ${confLabel}
        </div>
      `;

      toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        why.classList.toggle("hidden");
      });

      card.appendChild(toggle);
      card.appendChild(why);
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
    if (window.appState.hook.selected) {
      document.querySelectorAll(".hookCard").forEach(card => {
        card.classList.add("locked");
      });
    }

    const lab = document.getElementById("hookLab");
    if (lab) lab.classList.remove("hidden");
  }

  function prettyArea(area) {
    return {
      hook: "🎣 Hook",
      pacing: "⏱ Pacing",
      cta: "📢 CTA",
      overlay: "✨ Overlay",
      captions: "💬 Captions"
    }[area] || area;
  }

  function impactLabel(level) {
    return {
      high: "Fix now",
      medium: "Recommended",
      low: "Suggestion"
    }[level] || "";
  }

  function getHookNextMove(score, delta) {
    if (!score) return "generate";
    if (score < 50 && delta === 0) return "generate";
    if (score < 70) return "improve";
    if (score < 85) return "auto";
    return "done";
  }

  function showPublishBanner() {
    const messages = [
      "🚀 This one is ready to post.",
      "🔥 Strong hook. Clean flow.",
      "💎 Your audience will watch this.",
      "🎯 AI approves this edit.",
      "✨ Send it."
    ];

    const msg = messages[Math.floor(Math.random() * messages.length)];

    toast?.(msg);

    maybeConfetti?.(); // optional future
  }

  function pulseExportButton() {
    const btn = document.getElementById("exportBtn");
    if (!btn) return;

    btn.classList.add("publish-glow");

    setTimeout(() => {
      btn.classList.remove("publish-glow");
    }, 4000);
  }

  function highlightHookAction(move) {
    const generate = document.getElementById("generateHooksBtn");
    const improve = document.getElementById("boostHookBtn");
    const auto = document.getElementById("autoBoostHookBtn");

    // clear old highlights
    [generate, improve, auto].forEach(b => b?.classList.remove("pulse"));

    if (move === "generate") generate?.classList.add("pulse");
    if (move === "improve") improve?.classList.add("pulse");
    if (move === "auto") auto?.classList.add("pulse");
  }


  async function loadEditStrategy(force=false) {

    if (EDIT_STRATEGY_LOADING && !force) return;

    EDIT_STRATEGY_LOADING = true;

    const panel = document.getElementById("editStrategyPanel");
    const list = document.getElementById("editStrategyList");

    if (!panel || !list) {
      EDIT_STRATEGY_LOADING = false;
      return;
    }

    const delta = window.lastHookImprovementDelta || 0;

    // ⭐ live hook score
    const hookScore =
      Number(document.getElementById("hookScoreValue")?.textContent?.split("/")[0]) || 0;

    // No captions yet
    if (!lastSavedCaptionsText?.trim()) {
      list.innerHTML = `
        <div class="hint-text subtle">
          Create captions to unlock AI direction.
        </div>
      `;
      panel.classList.remove("hidden");
      EDIT_STRATEGY_LOADING = false;
      return;
    }

    

    // ================================
    // 🎥 Footage Intelligence
    // ================================
    const contextEl = document.getElementById("editStrategyContext");
    const setup = document.getElementById("aiSetupSummary");

    if (contextEl && setup?.innerText?.trim()) {
      contextEl.innerHTML = `
        <div class="context-title">🎥 Footage Intelligence</div>
        <div>${setup.innerText}</div>
      `;
      contextEl.classList.remove("hidden");
    }

    try {

      if (LAST_HOOK_SCORE == null || LAST_FLOW_SCORE == null) {
    console.log("Director waiting for scores");

    list.innerHTML = `
      <div class="hint-text subtle">
        Waiting for AI scores…
      </div>
    `;

    EDIT_STRATEGY_LOADING = false;
    return;
  }

      console.log("Director inputs →", {
        hook: LAST_HOOK_SCORE,
        flow: LAST_FLOW_SCORE
      });

      const data = await jsonFetch(
        `/api/edit_strategy?session=${getActiveSession()}`
      );

      const items = data.suggestions || [];

      // ================================
      // 🧠 Prevent useless redraws
      // ================================
      const signature = JSON.stringify({
        hook: LAST_HOOK_SCORE,
        flow: LAST_FLOW_SCORE,
        items: items.map(i => ({
          area: i.area,
          impact: i.impact,
          issue: i.issue
        }))
      });

      if (!force && signature === LAST_DIRECTOR_SIGNATURE) {
    console.log("🧠 Director unchanged — skipping render");

    EDIT_STRATEGY_LOADING = false;   // 🔥 ensure unlock
    return;
  }


      LAST_DIRECTOR_SIGNATURE = signature;

      list.innerHTML = "Analyzing edit…";

      if (!items.length) {
        list.innerHTML = `
          <div class="director-success">
            🎯 All major issues resolved — you're optimized.
          </div>
        `;
        panel.classList.remove("hidden");
        return;
      }

      // Sort high → low
      items.sort((a, b) => {
        const weight = { high: 3, medium: 2, low: 1 };
        return weight[b.impact] - weight[a.impact];
      });

      // ================================
      // Smart next action
      // ================================
      const nextMove = getHookNextMove(hookScore, delta);
      highlightHookAction(nextMove);

      // ================================
      // Render
      // ================================
      list.classList.add("fade-refresh");

      setTimeout(() => {
        list.innerHTML = items.map(s => {

          let toneIssue = s.issue;
          let toneImpact = s.impact;

          if (s.area === "hook") {
            if (hookScore >= 80) {
              toneImpact = "low";
              toneIssue = "🔥 Excellent hook. Focus on pacing or flow next.";
            }
            else if (hookScore >= 60) {
              toneImpact = "medium";
              toneIssue = "👍 Strong hook — a small upgrade could make it elite.";
            }
            else if (delta > 0) {
              toneImpact = "medium";
              toneIssue = "⚠️ Much better — keep pushing toward 70+.";
            }
          }

          let guidance = `👉 ${s.action}`;

          if (s.area === "hook") {
            if (nextMove === "generate") {
              guidance = "👉 Generate new ideas — this hook may be hard to fix";
            }
            if (nextMove === "improve") {
              guidance = "👉 Improve this hook — AI will strengthen curiosity & clarity";
            }
            if (nextMove === "auto") {
              guidance = "👉 Let AI auto-optimize for the best score";
            }
            if (nextMove === "done") {
              guidance = "✅ Strong hook — move to story flow";
            }
          }

          return `
            <div class="director-item impact-${toneImpact}" data-area="${(s.area || '').toLowerCase()}">
              <div class="director-header">
                <div class="director-area">${prettyArea(s.area)}</div>
                <div class="director-impact">
                  ${toneImpact.toUpperCase()} · ${impactLabel(toneImpact)}
                </div>
              </div>

              ${delta > 0 && s.area === "hook"
                ? `<div class="director-progress-up">↑ +${delta} points</div>`
                : ""}

              <div class="director-issue">${toneIssue}</div>
              <div class="director-action">${guidance}</div>
            </div>
          `;
        }).join("");

        const remaining = items.length;

        const footer = document.createElement("div");
        footer.className = "director-progress";
        footer.innerHTML = `
          ${remaining === 0
            ? "✅ No major issues detected"
            : `🎯 ${remaining} improvement${remaining > 1 ? "s" : ""} left`
          }
        `;

        list.appendChild(footer);

        list.querySelectorAll(".director-item").forEach(card => {
          card.addEventListener("click", () => {
            const area = card.dataset.area;
            jumpToEditArea(area);
            showGlobalStatus("Jumped to fix location ✨", "info");
          });
        });

        list.classList.remove("fade-refresh");

      }, 120);

      panel.classList.remove("hidden");
      renderPublishReadyState();
      renderEditProgress();

      const creativeState = evaluateCreativeState();
    document.body.classList.toggle("readiness-ready", creativeState.publish_ready);
    } catch (err) {
      console.error(err);
    }
    finally {
      EDIT_STRATEGY_LOADING = false;
    }
  }

  function updateHookLockUI() {

    
    const clearBtn = document.getElementById("clearHookBtn");
    const lockBar = document.getElementById("hookLockedBar");

    if (!clearBtn) return;

    if (window.appState.hook.selected) {
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

  function renderEditProgress() {
    const fill = document.getElementById("editProgressFill");
    const percentEl = document.getElementById("editProgressPercent");
    const hint = document.getElementById("editProgressHint");

    if (!fill || !percentEl || !hint) return;

    // ✅ use real scores
    const hook = Number(LAST_HOOK_SCORE) || 0;
    const flow = Number(LAST_FLOW_SCORE) || 0;

    console.log("📊 Progress using:", hook, flow);

    if (LAST_HOOK_SCORE == null || LAST_FLOW_SCORE == null) {
      fill.style.width = "0%";
      percentEl.textContent = "–";
      hint.textContent = "Scoring in progress…";
      return;
    }


    if (hook === 0 && flow === 0)
  {
      fill.style.width = "0%";
      percentEl.textContent = "0%";
      hint.textContent = "Run AI scoring to start.";
      return;
    }

    const progress = Math.min(100, Math.round((hook * 0.6) + (flow * 0.4)));

    fill.style.width = `${progress}%`;
    percentEl.textContent = `${progress}%`;

    if (progress < 50) {
      hint.textContent = "Strengthen the hook to gain momentum.";
    } else if (progress < 75) {
      hint.textContent = "Looking good — refine pacing & flow.";
    } else if (progress < 90) {
      hint.textContent = "Almost publish ready.";
    } else {
      hint.textContent = "🔥 Excellent. Your edit is elite.";
    }
  }


  function clearSelectedHook() {
    const state = window.appState;

    state.hook.selected = null;
    state.hook.locked = false;

    updateHookLockUI();
    refreshAfterChange();
  }

  function selectHook(text) {

    const state = window.appState;

    if (state.hook.locked && state.hook.selected !== text) {
      setStatus("hookLabStatus", "🔒 Hook locked — clear to change", "info");
      return;
    }

    state.hook.selected = text;
    state.hook.locked = true;

    updateHookLockUI();
    refreshAfterChange();
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

  function updateHookLabGuidance() {
    const el = document.getElementById("hookLabGuidance");
    if (!el) return;

    const score =
      Number(document.getElementById("hookScoreValue")?.textContent?.split("/")[0]) || 0;

    const hasHooks = Array.isArray(window.appState.hook.lastGenerated) && window.appState.hook.lastGenerated.length > 0;
    const selected = !!window.appState.hook.selected;

    if (!hasHooks) {
      el.textContent = "Generate hooks to explore opening ideas.";
      return;
    }

    if (hasHooks && !selected) {
      el.textContent = "Pick a hook you like, then improve or auto-optimize it.";
      return;
    }

    if (selected && score < 50) {
      el.textContent = "Improve the selected hook to raise curiosity.";
      return;
    }

    if (selected && score >= 50) {
      el.textContent = "Auto optimize can test multiple winning strategies.";
      return;
    }
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

  async function autoBoostSelectedHook() {
    const hook = window.appState.hook.selected;
  if (!hook) {
    toast?.("Select a hook first");
    return;
  }

    setStatus("hookLabStatus", "AI auto-optimizing…", "working");

    try {
      const res = await jsonFetch("/api/hook_autoboost", {
        method: "POST",
        body: JSON.stringify({
          hook,
          intent: window.appState.hook.intent
        })
      });

      if (!res?.text) throw new Error("No result");

      const newHook = res.text;
      const attempts = res.attempts || 0;
      const bestScore = res.score || 0;

      const before = lastSavedCaptionsText || "";

      const editor = document.getElementById("captionsText");
      let blocks = editor?.value?.split(/\n\s*\n/) || [];

      if (blocks.length === 0) blocks = [newHook];
      else blocks[0] = newHook;

      const newCaptions = blocks.join("\n\n");

      if (editor) editor.value = newCaptions;
      workingCaptionsText = newCaptions;

      renderStep3Diff(before, newCaptions);
      focusCaptionChanges();

      document.getElementById("saveCaptionsBtn")?.click();

      toast?.(`✨ Best score ${bestScore} after ${attempts} attempts`);

      setStatus("hookLabStatus", "Auto optimization complete ✓", "success");
      await refreshAfterChange();

    } catch (e) {
      console.error(e);
      setStatus("hookLabStatus", "Auto optimization failed", "error");
    }
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
    CONFIG_CACHE = null; // 🔥 ADD THIS

    // Reset session-dependent state
    LAST_HOOK_SCORE = null;
    LAST_FLOW_SCORE = null;

  const hookEl = document.getElementById("hookScoreValue");
    if (hookEl) hookEl.textContent = "—";

    const flowEl = document.getElementById("storyFlowScoreValue");
  if (flowEl) flowEl.textContent = "—";

    window.appState.hook.selected = null;
    window.appState.hook.locked = false;
    window.appState.hook.lastGenerated = null;

    window.appState.variants.list = [];

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

    // 🔥 VARIANTS RESET (you were missing this)
    lastVariantStatus = null;
    VARIANT_POLL_ACTIVE = false;
    window.appState.variants.list = [];
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
    await refreshAfterChange();

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

  function activateStep(stepSelector) {
    document.querySelectorAll(".step").forEach(btn => {
      btn.classList.toggle(
        "active",
        btn.dataset.target === stepSelector
      );
    });

    const stepCard = document.querySelector(stepSelector);
    stepCard?.classList.add("step-active");
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

              if (id === "#step-4") {
                refreshAfterChange();
              }




              stepButtons.forEach((btn) => {
                  if (btn.dataset.target === id) {
                      stepButtons.forEach((b) => b.classList.remove("active"));
                      btn.classList.add("active");
                  }
              });
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

  async function autoSelectIntentFromReadiness(summary) {
    if (!summary?.recommended_goal) return;

    const raw = summary.recommended_goal.toLowerCase();

    // ----------------------------
    // TRANSLATION LAYER
    // ----------------------------
    const map = {
      "general highlight": "discovery",
      "viral": "discovery",
      "attention": "discovery",

      "emotional": "personal",
      "romantic": "personal",
      "memory": "personal",

      "cinematic": "aesthetic",
      "luxury": "aesthetic",
      "vibes": "aesthetic",

      "explanation": "informational",
      "educational": "informational",
      "guide": "informational"
    };

    const intent = map[raw] || "discovery";

    // If user already changed → respect them
    if (window.userForcedIntent) {
      console.log("🧠 Intent locked by user → skipping auto-set");
      return;
    }

    console.log("🧠 Auto-selecting intent:", raw, "→", intent);

    window.appState.hook.intent = intent;

    syncIntentPills(intent);
    updateIntentHint(intent);

    if (typeof saveIntent === "function") {
      await saveIntent(intent);
    }

    await refreshAfterChange();


    setStatus(
      "hookLabStatus",
      `AI set intent → ${intent}`,
      "info"
    );
  }

  function renderSetupSummary(data) {
    const el = document.getElementById("aiSetupSummary");
    if (!el) return;

    // You can customize the copy based on your API payload
    const clips = data?.clip_count ?? data?.clips ?? null;
    const goal  = data?.recommended_goal ?? "";
    const note  = data?.summary ?? data?.message ?? "";

    el.innerHTML = `
      <div class="ai-summary-row">
        <div class="ai-summary-title">🧠 AI Setup Ready</div>
        <div class="ai-summary-sub">
          ${clips != null ? `Clips analyzed: <b>${clips}</b>.` : `Clips analyzed.`}
          ${goal ? ` Recommended goal: <b>${goal}</b>.` : ``}
        </div>
        ${note ? `<div class="ai-summary-note">${note}</div>` : ``}
      </div>

      <div class="ai-summary-actions">
        <button id="prepareStoryboardBtn" class="btn primary">
          ⚡ Prepare storyboard
        </button>
        <button id="jumpToStoryboardBtn" class="btn ghost">
          🎬 Jump to storyboard order
        </button>
      </div>
    `;
  }

  async function loadAISetupSummary() {
    const data = await jsonFetch(
      `/api/ai_setup_summary?session=${getActiveSession()}`
    );

    // Step 1 summary (near Analyze)
    renderSetupSummary(data, "aiSetupSummaryStep1");

    return data;
  }

  async function retryAnalysis() {
    const status = await jsonFetch(
      `/api/analyze_status?session=${getActiveSession()}`
    );

    if (status.status === "running") {
      setStatus("analyzeStatus", "Analysis already running…", "info");
      pollAnalyzeStatus();
      return;
    }

    setStatus("analyzeStatus", "Restarting analysis…", "working");
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
  // DONE (auto-advance to storyboard)
  // -----------------------------
  if (status === "done") {
    console.log("✅ Analysis finished");

    setStatus(
      "analyzeStatus",
      "AI analysis ready ✓",
      "success",
      false
    );

  await refreshAnalyses();

  await loadConfigAndYaml();
  await loadCaptionsFromYaml();

  const summary = await loadAISetupSummary();   // ✅ renders the panels
  await autoSelectIntentFromReadiness(summary);

    // 🔥 AUTO-ADVANCE TO STORYBOARD (safe guard)
  const yamlStatus = await jsonFetch(
    `/api/generate_yaml/status?session=${getActiveSession()}`
  );

  if (
    !YAML_POLL_ACTIVE &&
    yamlStatus &&
    (yamlStatus.status === "idle" || yamlStatus.status === "not_started")
  ) {
    console.log("⚡ Auto-generating storyboard");
    PENDING_SCROLL_TO_STORYBOARD = true;
    await generateYamlAsync();

  } else if (yamlStatus?.status === "done") {
    console.log("📦 Storyboard already exists — hydrating");
    PENDING_SCROLL_TO_STORYBOARD = true;
    await hydrateStoryboardAndScroll();
  }

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

    const variant = window.appState.variants.list
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
      // 🔒 IMPORTANT: bypass CONFIG_CACHE intentionally.
  // We need a fresh server snapshot for undo safety.
  // Using getConfigCached() could return stale or mutated state.
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

      CONFIG_CACHE = null; // 🔥 invalidate cache before reload

      await loadConfigAndYaml();
      await loadCaptionsFromYaml();
      await refreshAfterChange();


      setStatus("captionsStatus", "AI recommendation applied ✓", "success");
      maybeShowStep4Nudge();


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

  function renderSetupSummary(summary, targetId = "aiSetupSummary") {
    const el = document.getElementById(targetId);
    if (!el) return;

    if (!summary?.has_analysis) {
      el.classList.add("hidden");
      return;
    }

    el.classList.remove("hidden");

    el.innerHTML = `
      <div class="ai-summary-card premium">
        <div class="ai-summary-header">
          <h3>🧠 AI Readiness Summary</h3>
          <p class="hint-text subtle">Here’s what AI understands about your video.</p>
        </div>

        <div class="ai-summary-stats">
          <div class="stat">
            <div class="stat-value">${summary.clips ?? "-"}</div>
            <div class="stat-label">Clips</div>
          </div>

          <div class="stat">
            <div class="stat-value">${summary.hook_confidence || "unknown"}</div>
            <div class="stat-label">Hook</div>
          </div>

          <div class="stat">
            <div class="stat-value">${summary.labels?.quality || "none"}</div>
            <div class="stat-label">Labels</div>
          </div>

          <div class="stat">
            <div class="stat-value">${summary.estimated_length || "-"}</div>
            <div class="stat-label">Length</div>
          </div>
        </div>

        <div class="ai-summary-recommend">
          🎯 AI suggests: <strong>${summary.recommended_goal || "General highlight"}</strong>
        </div>
      </div>
    `;
  }

  async function enterStoryboardStep() {
    activateStep("#step-3");
    scrollToStep("#step-3");
  }

  async function improveHooksAndCaptionsFlow() {
    if (YAML_POLL_ACTIVE) {
    console.log("🔁 Restarting YAML generation");
    YAML_POLL_ACTIVE = false;
  }


    try {
      setStatus("improveHooksStatus", "Preparing storyboard…", "working");

      const s = await jsonFetch(
        `/api/generate_yaml/status?session=${getActiveSession()}`
      );

      PENDING_SCROLL_TO_STORYBOARD = true;

      if (s?.status === "done") {
        await hydrateStoryboardAndScroll();
        setStatus("improveHooksStatus", "Storyboard ready ✓", "success");
        return;
      }

      await generateYamlAsync(); // poller finishes the rest

    } catch (e) {
      console.error(e);
      setStatus("improveHooksStatus", "Failed to prepare storyboard", "error");
    }
  }

  async function hydrateStoryboardAndScroll() {
    CONFIG_CACHE = null;
    
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();

    workingCaptionsText = lastSavedCaptionsText;
    captionViewMode = "rewritten";
    renderCaptionView();

    updateCaptionBaselineHint();
    updateLoadYamlVisibility();
    updateAIRecommendationBar();

    // 🔥 Option A: auto-generate hooks once storyboard is ready
    if (!window.appState.hook.lastGenerated?.length) {
      generateHooks(); // runs async, sets hooksReady + renders if lab is open
    }

    if (PENDING_SCROLL_TO_STORYBOARD) {
    PENDING_SCROLL_TO_STORYBOARD = false;

    // tiny cinematic pause after AI work
    await new Promise(r => setTimeout(r, 120));

    activateStep("#step-3");

  document
    .getElementById("storyboardTimeline")
    ?.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });
  }

  await refreshAfterChange();

  }

  async function pollYamlStatus() {
    if (!YAML_POLL_ACTIVE) return;

    try {
      const data = await jsonFetch(
        `/api/generate_yaml/status?session=${getActiveSession()}`
      );

      const status = data?.status;

      if (status === "running") {
        setTimeout(pollYamlStatus, 1200);
        return;
      }

      if (status === "error") {
    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;
    setStatus("yamlStatus", data.error || "Storyboard failed", "error");
    return;
  }

  if (status === "idle" || status === "not_started") {
    // stop polling if job isn't running
    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;
    setStatus("yamlStatus", "Storyboard not running", "info");
    return;
  }


      if (status === "done") {
    YAML_POLL_ACTIVE = false;
    lastYamlStatus = null;

    setStatus("yamlStatus", "Finalizing storyboard…", "working", false);

    await hydrateStoryboardAndScroll();

    setStatus("yamlStatus", "Storyboard ready ✓", "success");
    setTimeout(() => setStatus("yamlStatus", ""), 1500);
    return;
  }


      // 🔁 NOT READY YET — KEEP POLLING
      setTimeout(pollYamlStatus, 1200);

    } catch (err) {
      console.warn("pollYamlStatus failed", err);

      // 🔁 RETRY instead of killing state
      setTimeout(pollYamlStatus, 2000);
    }
  }


  async function generateYamlAsync() {

    setStatus(
      "yamlStatus",
      "Building storyboard with AI…",
      "working",
      false
    );

    try {
      const res = await jsonFetch("/api/generate_yaml", {
        method: "POST",
        body: JSON.stringify({
          session: getActiveSession()
        }),
      });

      // ⭐ IF BACKEND ALREADY RETURNED YAML → DONE
      if (res?.first_clip || res?.middle_clips || res?.last_clip) {
        console.log("⚡ YAML returned immediately (sync mode)");

        YAML_POLL_ACTIVE = false;
        lastYamlStatus = null;

        PENDING_SCROLL_TO_STORYBOARD = true;
        await hydrateStoryboardAndScroll();

        setStatus("yamlStatus", "Storyboard ready ✓", "success");
        return;
      }

      // ⭐ OTHERWISE → async job started
      console.log("🕒 YAML running async");

      YAML_POLL_ACTIVE = true;
      lastYamlStatus = "running";
      pollYamlStatus();

    } catch (err) {
      console.error("generateYamlAsync failed", err);

      YAML_POLL_ACTIVE = false;
      lastYamlStatus = null;

      setStatus(
        "yamlStatus",
        "Failed to start storyboard generation",
        "error"
      );
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
          await refreshAfterChange();

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
      const data = await getConfigCached();


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

    window.appState.hook.lastGenerated = null;
    updateHooksReadyUI();

  renderStoryboardTimeline({
    first_clip: workingClipOrder[0],
    middle_clips: workingClipOrder.slice(1, -1),
    last_clip: workingClipOrder[workingClipOrder.length - 1]
  });

  // ⬇️ ADD THIS
  saveStoryboardOrder({ silent: true });

  refreshAfterChange();



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
      const data = await getConfigCached(true);
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

      CONFIG_CACHE = null;


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
        CONFIG_CACHE = null;
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

function evaluateCreativeState() {
  const hook = LAST_HOOK_SCORE ?? null;
  const flow = LAST_FLOW_SCORE ?? null;
  const intent = window.appState?.hook?.intent || "default";
  const captions = getCurrentCaptionsText() || "";

  const blocks = captions
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean);

  const captionCount = blocks.length;

  const hasCTA = captions.toLowerCase().includes("follow") ||
                 captions.toLowerCase().includes("subscribe") ||
                 captions.toLowerCase().includes("book");

  const weaknesses = [];
  const priority = [];

  // -----------------------------
  // Hook Analysis
  // -----------------------------
  if (hook !== null) {
    if (hook < 60) {
      weaknesses.push("Hook clarity");
      priority.push("Improve hook immediately");
    } else if (hook < 75) {
      weaknesses.push("Hook strength");
      priority.push("Refine hook for stronger impact");
    }
  }

  // -----------------------------
  // Flow Analysis
  // -----------------------------
  if (flow !== null) {
    if (flow < 65) {
      weaknesses.push("Story pacing");
      priority.push("Shorten middle captions");
    } else if (flow < 75) {
      weaknesses.push("Narrative progression");
      priority.push("Improve transition between captions");
    }
  }

  // -----------------------------
  // Structural Checks
  // -----------------------------
  if (captionCount < 3) {
    weaknesses.push("Video depth");
    priority.push("Add more storytelling content");
  }

  if (!hasCTA) {
    weaknesses.push("Missing CTA");
    priority.push("Add a strong closing CTA");
  }

  // -----------------------------
  // Readiness Score
  // -----------------------------
  let readiness = 0;

  if (hook !== null) readiness += hook * 0.4;
  if (flow !== null) readiness += flow * 0.4;
  if (hasCTA) readiness += 10;
  if (captionCount >= 3) readiness += 10;

  readiness = Math.min(Math.round(readiness), 100);

  const publishReady = readiness >= 80 && weaknesses.length === 0;

  return {
    hook_score: hook,
    flow_score: flow,
    readiness_score: readiness,
    caption_blocks: captionCount,
    has_cta: hasCTA,
    primary_weakness: weaknesses[0] || null,
    all_weaknesses: weaknesses,
    priority_actions: priority,
    publish_ready: publishReady
  };
}

async function refreshHookScore() {
  const card = document.querySelector(".hook-score-card");
  const scoreEl = document.getElementById("hookScoreValue");
  const reasonsEl = document.getElementById("hookScoreReasons");
  const hookEl = document.getElementById("hookScoreHook");
  const statusEl = document.getElementById("hookScoreStatus");

  if (!card || !scoreEl || !reasonsEl || !hookEl) return;

  const text = getCurrentCaptionsText();

  // ----------------------------
  // No captions yet
  // ----------------------------
  if (!text) {
    card.classList.remove("hidden");
    scoreEl.textContent = "—";
    reasonsEl.innerHTML = `<li>Generate storyboard to evaluate hook.</li>`;
    hookEl.textContent = "";

    LAST_HOOK_SCORE = null;
    window.appState.scores.hook = null;
    updateRewriteModeAvailability();
    updateImproveButtons(null, LAST_FLOW_SCORE);
    updateSmartStatus();

    return;
  }
  

  card.classList.remove("hidden");

  try {
    if (statusEl) statusEl.textContent = "Checking hook…";

    const data = await jsonFetch("/api/hook_score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session: getActiveSession(),
        text: text,
      }),
    });

    const score = Number(data.score ?? 0);

    // 🎉 Improvement animation
    if (LAST_HOOK_SCORE !== null && score > LAST_HOOK_SCORE) {
      celebrateImprovement("hook", LAST_HOOK_SCORE, score);
    }

    // ---------------------------------
    // 🔑 Core State Update (SYNC BOTH SYSTEMS)
    // ---------------------------------
    LAST_HOOK_SCORE = score;
    window.appState.scores.hook = score;

    updateRewriteModeAvailability();
    updateImproveButtons(score, LAST_FLOW_SCORE);

    const hookLabel = getHookRatingLabel(score);

    updateCollapsibleHeaders(
      LAST_HOOK_SCORE,
      hookLabel,
      LAST_FLOW_SCORE,
      LAST_FLOW_SCORE != null ? getFlowRatingLabel(LAST_FLOW_SCORE) : null
    );

    renderEditProgress();

    // -----------------------------
    // UI Rendering
    // -----------------------------
    scoreEl.textContent = `${score}/100`;
    hookEl.textContent = data.hook || "(no opening caption yet)";

    const label = document.getElementById("hookScoreLabel");
    if (label) label.textContent = hookLabel;

    scoreEl.classList.add("score-pop");
    setTimeout(() => scoreEl.classList.remove("score-pop"), 600);

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

    // -----------------------------
    // Weak hook guidance
    // -----------------------------
    const diffOpen = !document
      .getElementById("captionCompareBody")
      ?.classList.contains("hidden");

    if (score < 60 && !diffOpen) {
      statusEl.textContent =
        "⚠ Weak hook — click the score to explore better ones.";
    } else {
      statusEl.textContent = "";
    }

    updateSmartStatus();

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
  if (statusEl) {
  statusEl.textContent = "Improving hook…";
  statusEl.className = "status-text status-working";
}


try {
  const data = await jsonFetch("/api/hook_improve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session: getActiveSession() }),
  });

  if (data.status === "error") {
    throw new Error(data.error || "failed");
  }

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

async function boostSelectedHook() {
  const statusEl = document.getElementById("hookLabStatus");

  const hook = window.appState.hook.selected;
  if (!hook) {
    toast?.("Select a hook first");
    return;
  }

  const oldScore = Number(LAST_HOOK_SCORE ?? 0);
  window.lastHookScoreBeforeBoost = oldScore;

  setStatus("hookLabStatus", "AI testing stronger versions…", "working");

  try {
    const res = await jsonFetch("/api/hook_boost", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        hook,
        intent: window.appState.hook.intent
      }),
    });

    if (!res?.text) throw new Error("No upgraded hook returned");

    const newHook = res.text;
    const beforeBoost = getCurrentCaptionsText();

    // Build new captions
    const editor = document.getElementById("captionsText");
    let blocks = [];

    if (editor?.value?.trim()) {
      blocks = editor.value.split(/\n\s*\n/).filter(Boolean);
    }

    if (blocks.length === 0) blocks = [newHook];
    else blocks[0] = newHook;

    const newCaptions = blocks.join("\n\n");

    if (editor) editor.value = newCaptions;
    workingCaptionsText = newCaptions;

    renderStep3Diff(beforeBoost, newCaptions);
    focusCaptionChanges();

    // ✅ Save officially (no click)
    await jsonFetch("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        text: newCaptions,
      }),
    });

    CONFIG_CACHE = null; // 🔥 captions affect config state


    // keep baselines consistent
    lastSavedCaptionsText = newCaptions;
    window.appState.hook.lastGenerated = null;
    updateHooksReadyUI();

    await refreshAfterChange();


    const newScore = Number(LAST_HOOK_SCORE ?? 0);
    const diff = newScore - Number(window.lastHookScoreBeforeBoost ?? 0);

    if (diff > 0) {
      toast?.(`⬆ Improved by ${diff} points`);
    } else if (diff < 0) {
      // 🚨 REVERT
      if (editor) editor.value = beforeBoost;
      workingCaptionsText = beforeBoost;

      await jsonFetch("/api/save_captions", {
        method: "POST",
        body: JSON.stringify({
          session: getActiveSession(),
          text: beforeBoost,
        }),
      });

      CONFIG_CACHE = null; // 🔥 captions affect config state


      lastSavedCaptionsText = beforeBoost;

      await refreshAfterChange();
      toast?.("AI tested upgrades — your original hook performs better 💪");
    } else {
      toast?.("No performance change");
    }

    setStatus("hookLabStatus", "Test complete ✓", "success");
  } catch (err) {
    console.error(err);
    setStatus("hookLabStatus", "Failed to upgrade hook", "error");
  }
}

async function undoAIRecommendation() {
  const snapshot = window.aiUndoSnapshot;

  if (!snapshot?.config) {
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

    CONFIG_CACHE = null;


    await loadConfigAndYaml();
    await loadCaptionsFromYaml();
    await refreshAfterChange();


    window.aiUndoSnapshot = null;
    updateAIRecommendationBar();

    setStatus("captionsStatus", "AI changes undone", "info");

  } catch (err) {
    console.error(err);
    toast("Failed to undo AI changes");
  }
}


function setVariantsDrawerOpen(isOpen) {
  const drawer = document.getElementById("variantsDrawer");
  const btn = document.getElementById("variantsToggleBtn");
  if (!drawer) return;

  drawer.classList.toggle("closed", !isOpen);
  if (btn) btn.textContent = isOpen ? "Collapse" : "Expand";
}

function toggleVariantsPanel(forceClose = false) {
  const drawer = document.getElementById("variantsDrawer");
  if (!drawer) return;

  if (forceClose) return setVariantsDrawerOpen(false);

  const shouldOpen = drawer.classList.contains("closed");
  setVariantsDrawerOpen(shouldOpen);

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
    maybeShowStep4Nudge();

    await jsonFetch("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({
        session,
        text
      })
    });

    CONFIG_CACHE = null; // 🔥 captions affect config state


    // 🔑 This variant is now the truth
    lastSavedCaptionsText = text;
    workingCaptionsText = text;

    window.appState.hook.lastGenerated = null;
    updateHooksReadyUI();

    await loadConfigAndYaml();   // ok to keep for timeline
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

    maybeShowStep4Nudge();

    toggleVariantsPanel(true);

    document.getElementById("step4CaptionScroll")?.classList.add("hidden");
    document.getElementById("rewriteDecisionBar")?.classList.add("hidden");
    await refreshAfterChange();


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

function updateSmartStatus() {
  const el = document.getElementById("editSmartStatus");
  if (!el) return;

  const hook =
  window.appState?.scores?.hook ??
  LAST_HOOK_SCORE ??
  null;

  const flow =
    window.appState?.scores?.storyFlow ??
    LAST_FLOW_SCORE ??
    null;

  const flowThreshold = {
    discovery: 70,
    luxury: 80,
    informational: 75,
    personal: 72
  }[window.appState.hook.intent] || 70;

  if (hook === null || flow === null) {
    el.classList.add("hidden");
    return;
  }

  el.classList.remove("hidden");
  el.className = "edit-smart-status";

  if (hook < 60) {
    el.textContent = "🔴 Fix Hook First";
    el.classList.add("red");
  } 
  

  else if (flow < flowThreshold) {
      el.textContent = "🟡 Improve Story Flow";
      el.classList.add("yellow");
    } 
  else if (hook >= 85 && flow >= 85) {
    el.textContent = "🚀 Publish Ready";
    el.classList.add("green");
  } 
  else {
    el.textContent = "🔵 Polish & Optimize";
    el.classList.add("blue");
  }

  el.classList.remove("pulse");
  void el.offsetWidth;
  el.classList.add("pulse");

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
  LAST_FLOW_SCORE = null;
  window.appState.scores.storyFlow = null;
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
      LAST_FLOW_SCORE = null;
      window.appState.scores.storyFlow = null;
      return;
    }

    card.classList.remove("hidden");
    if (improveBtn) improveBtn.disabled = false;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await jsonFetch(`/api/story_flow_score?session=${session}`);

        const score = Number(data.score ?? 0);

        if (LAST_FLOW_SCORE !== null && score > LAST_FLOW_SCORE) {
          celebrateImprovement("flow", LAST_FLOW_SCORE, score);
        }

        LAST_FLOW_SCORE = score;
        window.appState.scores.storyFlow = score;

        const flowLabel = getFlowRatingLabel(score);

        updateCollapsibleHeaders(
          LAST_HOOK_SCORE,
          LAST_HOOK_SCORE != null ? getHookRatingLabel(LAST_HOOK_SCORE) : null,
          LAST_FLOW_SCORE,
          flowLabel
        );

        renderEditProgress();


        updateImproveButtons(LAST_HOOK_SCORE, score);
        scoreEl.textContent = `${score}/100`;

        document.getElementById("storyFlowScoreLabel").textContent =
          getFlowRatingLabel(score);


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
      setTimeout(renderEditProgress, 50);
      updateSmartStatus();
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
  const hookScore = Number(LAST_HOOK_SCORE ?? 0);

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
  if (!box) {
    setUiBusy(false);
    return;
  }

  setCaptionInlineStatus("Loading captions from YAML…", "info");

  try {
    const session = encodeURIComponent(getActiveSession());
    const data = await getConfigCached();
    const cfg = data.config || {};

    const yamlText = buildCaptionsFromConfig(cfg).trim();

    // 🔑 YAML baseline (never allow empty overwrite)
    if (yamlText) {
      lastSavedCaptionsText = yamlText;
    }


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
       
        CONFIG_CACHE = null; // 🔥 captions affect config state


        lastSavedCaptionsText = text;   // 🔑 THIS IS REQUIRED

        window.appState.hook.lastGenerated = null;
        updateHooksReadyUI();

        setStatus(
            "captionsStatus",
            `Saved ${result.count || 0} caption block(s).`,
            "success",
            true
        );

        await loadConfigAndYaml();
        await refreshAfterChange();

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

function updateCollapsibleHeaders(hookScore, hookLabel, flowScore, flowLabel) {
  const hookHeader = document.getElementById("hookHeaderScore");
  const storyHeader = document.getElementById("storyHeaderScore");

  if (hookHeader) {
    hookHeader.textContent = `${hookScore ?? "–"}/100 (${hookLabel ?? ""})`;
  }

  if (storyHeader) {
    storyHeader.textContent = `${flowScore ?? "–"}/100 (${flowLabel ?? ""})`;
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

        CONFIG_CACHE = null;

        // ✅ NOW load from YAML (this sets baseline)
        await loadCaptionsFromYaml({ preserveSource: true });

        window.appState.hook.lastGenerated = null;
        updateHooksReadyUI();

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

function toggleHookDetails() {
  const body = document.getElementById("hookDetailsBody");
  if (!body) return;

  body.classList.toggle("collapsed");
}

function toggleStoryDetails() {
  const body = document.getElementById("storyDetailsBody");
  if (!body) return;

  body.classList.toggle("collapsed");
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
    await refreshAfterChange();



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
  ?.addEventListener("change", () =>
  refreshAfterChange()
);



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
        await refreshAfterChange();

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
  try {
    const data = await getConfigCached(); 
    const mode = data.config?.render?.captions_mode || "all";

    const select = document.getElementById("captionModeSelect");
    if (select) {
      select.value = mode;
    }

  } catch (err) {
    console.error("Failed to load caption mode", err);
  }
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
            await refreshAfterChange();

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
        const data = await getConfigCached();
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
        const data = await getConfigCached();
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

        CONFIG_CACHE = null;


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
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.cta = { enabled, text, voiceover };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


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
        const data = await getConfigCached();
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
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.music = { enabled, file, volume };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


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
        const data = await getConfigCached();
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

        CONFIG_CACHE = null;


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
    // statusEl.textContent = "✅ Export complete";
    setStatus("exportStatus", "Export complete ✓", "success");


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
    const data = await getConfigCached();
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


async function goToHookLab() {
  await loadConfigAndYaml();
  await loadCaptionsFromYaml();

  setVariantsDrawerOpen(true);
  document.getElementById("hookLab")?.classList.remove("hidden");

  // if hooks exist, show them, otherwise generate
  const hooks = window.appState.hook.lastGenerated;

if (hooks?.length) {
  renderHookLab(hooks);

  } else {
    await generateHooks();
  }

  await new Promise(r => setTimeout(r, 50));

  document.getElementById("hookLab")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });

  highlightHookLab?.();
  updateHookLabGuidance();
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

  document
  .getElementById("autoBoostHookBtn")
  ?.addEventListener("click", autoBoostSelectedHook);

  // ================================
  // AI Director Toggle (3.1)
  // ================================
  const directorPanel = document.getElementById("editStrategyPanel");
  const toggleDirectorBtn = document.getElementById("toggleDirectorBtn");

  if (directorPanel) {
    // Ensure starts collapsed (even if HTML forgot the class)
    directorPanel.classList.add("collapsed");
  }

  if (toggleDirectorBtn && directorPanel) {
    toggleDirectorBtn.addEventListener("click", (e) => {
      e.preventDefault();
      directorPanel.classList.toggle("collapsed");

      // Optional: update button label
      toggleDirectorBtn.textContent = directorPanel.classList.contains("collapsed")
        ? "🧠 AI Director"
        : "🧠 Hide Director";
    });
  }

  document.getElementById("editSmartStatus")?.addEventListener("click", () => {
  const hook =
  window.appState?.scores?.hook ??
  LAST_HOOK_SCORE ??
  0;

  const flow =
    window.appState?.scores?.storyFlow ??
    LAST_FLOW_SCORE ??
    0;

  if (hook < 60) {
    goToHookLab();
  } 
  else if (flow < 70) {
    document.getElementById("improveStoryFlowBtn")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  } 
  else {
    document.getElementById("exportBtn")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});


  setTimeout(async () => {
  try {
    const s = await jsonFetch(`/api/analyze_status?session=${getActiveSession()}`);
    if (s.status === "running") {
      ANALYZE_POLL_ACTIVE = true;
      pollAnalyzeStatus();
    }
    if (s.status === "done") {
      // optional: ensure summary shows after refresh
      await refreshAnalyses();
      await loadAISetupSummary();
    }
  } catch (e) {
    console.warn("analyze resume check failed", e);
  }
}, 300);

  
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
// Intent pill wiring (STATE DRIVEN)
// -------------------------------
const pillContainer = document.querySelector(".intent-pills");

if (pillContainer) {
  pillContainer.addEventListener("click", async(e) => {

    const pill = e.target.closest(".pill");
    if (!pill) return;

    const intent = pill.dataset.intent;
    if (!intent) return;

    const state = window.appState;

    // If already selected, do nothing
    if (intent === state.hook.intent) return;

    // 🔑 Update centralized state
    state.hook.intent = intent;

    // 🎨 Sync UI from state
    syncIntentPills();

    // 🔔 Update hint
    updateIntentHint(intent);


      // 💾 Persist intent (optional)
      if (typeof saveIntent === "function") {
        await saveIntent(intent);
      }

      await refreshAfterChange();


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
try {
  const data = await jsonFetch(
    `/api/generate_yaml/status?session=${getActiveSession()}`
  );

  if (data.status === "running") {
    YAML_POLL_ACTIVE = true;
    lastYamlStatus = "running"; // 🔑 critical to catch fast "done"
    pollYamlStatus();
  }

  if (data.status === "done") {
  YAML_POLL_ACTIVE = false;
  lastYamlStatus = null;
  PENDING_SCROLL_TO_STORYBOARD = true;
  await hydrateStoryboardAndScroll();
}
} catch (err) {
  console.warn("Failed to resume YAML polling on load", err);
}

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
      intentSelect.addEventListener("change", async () => {
      window.appState.hook.intent = intentSelect.value;
      await saveIntent(window.appState.hook.intent);
      await refreshAfterChange();
    });

    }
    

document.getElementById("continueToHooksBtn")?.addEventListener("click", goToHookLab);
document.getElementById("confirmStoryboardBtn")?.addEventListener("click", goToHookLab);

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
  CONFIG_CACHE = null;
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

  document.getElementById("boostHookBtn")
  ?.addEventListener("click", boostSelectedHook);


  let captionAutoSaveTimer = null;

document.getElementById("captionsText")?.addEventListener("input", () => {
  diffDirty = true;

  const status = document.getElementById("captionInlineStatus");
  if (status) {
    status.textContent = "Saving…";
    status.className = "caption-inline-status";
    status.classList.remove("hidden");
  }

  clearTimeout(captionAutoSaveTimer);

  captionAutoSaveTimer = setTimeout(async () => {
    await refreshAfterChange();


    if (status) {
      status.textContent = "Saved ✓";
      status.className = "caption-inline-status success";
    }

    setTimeout(() => {
      status?.classList.add("hidden");
    }, 1500);

  }, 900);
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
  updateHookLabGuidance();
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
  updateHookLabGuidance();
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

        await loadConfigAndYaml();
        await loadCaptionsFromYaml();
        await refreshAfterChange();

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

    // Use snapshot for comparison
    if (window.preBoostCaptions) {
      renderStep3Diff(window.preBoostCaptions, newCaptions);
      focusCaptionChanges?.();
    }


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

      CONFIG_CACHE = null; // 🔥 invalidate cache after caption save


      lastSavedCaptionsText = workingCaptionsText;
      rewriteCommitted = true;
      await loadConfigAndYaml();
      await loadCaptionsFromYaml();
      await refreshOverlayPreview();
      await refreshAfterChange();

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

    await generateVariantsAsync(
  modes,
  window.appState.hook.selected || null
);
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
       await refreshAfterChange();


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