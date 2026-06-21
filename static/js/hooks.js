async function generateHooks() {

  if (typeof saveStoryboardOrder === "function" && Array.isArray(workingClipOrder) && workingClipOrder.length) {
    await saveStoryboardOrder({ silent: true });
  }

    await autoSelectContentContextFromReadiness();
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
        intent: window.appState?.hook?.intent || "discovery",
        content_mode: getContentMode(),
        content_context: document.getElementById("contentContext")?.value || "auto"
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

      if (isAutoAssistEnabled()) {
        await autoPickBestHook(hooks);
      }

      requestAnimationFrame(() => {
        out?.classList.add("show");
      });

      if (status) {
        if (isAutoAssistEnabled() && !window.appState?.hook?.userSelected && window.appState?.hook?.selected) {
          status.textContent = "🧠 AI selected best hook";
        } else {
          status.textContent = `✓ ${hooks.length} hooks generated`;
        }
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


async function autoPickBestHook(hooks) {
  if (!Array.isArray(hooks) || hooks.length === 0) return;

  // only manual user choice should block auto-pick
  if (window.appState?.hook?.userSelected) return;

  const sorted = [...hooks].sort((a, b) => (b.score || 0) - (a.score || 0));
  const best = sorted[0];

  if ((best.score || 0) < 60) return;

  console.log("🧠 Auto Assist picked hook:", best.text);

  await selectHook(best.text, false);
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
        const reason = h.why || h.recommend_reason || "";
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
        const allowAiHighlight = !window.appState.hook.userSelected;

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
        <span class="ai-badge-main">⭐ Best Available</span>
        <span class="ai-badge-confidence">
          ${h.score >= 75 ? confLabel : "Needs improvement"}
        </span>
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

        card.addEventListener("click", () => selectHook(h.text, true));

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

async function selectHook(text, isUser = true) {

  window.appState.captionsDirty = true;
  const state = window.appState || {};
  state.hook = state.hook || {};

  if (state.hook.locked && state.hook.userSelected && state.hook.selected !== text) {
    setStatus("hookLabStatus", "🔒 Hook locked — clear to change", "info");
    return;
  }

  state.hook.selected = text;
  state.hook.locked = true;
  state.hook.userSelected = isUser;


document.querySelectorAll(".hookCard").forEach(card => {
  const hookText = card.querySelector(".hookText")?.textContent?.trim();

  if (hookText === window.appState.hook.selected?.trim()) {
    card.classList.add("selected");
    card.classList.remove("locked", "hook-locked");
  } else {
    card.classList.remove("selected");
    card.classList.add("locked");
  }
});

  const current = getCurrentCaptionsText();
  const blocks = current
    ? current.split(/\n\s*\n/).map(b => b.trim()).filter(Boolean)
    : [];

  if (blocks.length === 0) {
    workingCaptionsText = text;
  } else {
    blocks[0] = text;
    workingCaptionsText = blocks.join("\n\n");
  }

  const updatedBlocks = workingCaptionsText
  .split(/\n\s*\n/)
  .map(b => b.trim())
  .filter(Boolean);

if (workingClipOrder.length) {
  workingClipOrder = workingClipOrder.map((clip, i) => ({
    ...clip,
    text: updatedBlocks[i] ?? clip.text
  }));

  renderStoryboardTimeline({
    first_clip: workingClipOrder[0],
    middle_clips: workingClipOrder.slice(1, -1),
    last_clip: workingClipOrder[workingClipOrder.length - 1]
  });
}

  // ✅ keep textarea in sync
  const editor = document.getElementById("captionsText");
  if (editor) {
    editor.value = workingCaptionsText;
  }

  updateHookLockUI();
  renderStep3Diff(lastSavedCaptionsText || "", workingCaptionsText || "");

  // ✅ persist to backend so refresh/reload doesn't revert it
  try {
    await jsonFetch("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        text: workingCaptionsText,
      }),
    });

    CONFIG_CACHE = null;
    lastSavedCaptionsText = workingCaptionsText;
    window.appState.captionsDirty = false;
    workingCaptionsText = lastSavedCaptionsText;
    await loadConfigAndYaml();

    captionViewMode = 'rewritten';
    renderCaptionView();

  } catch (err) {
    console.error("Failed to save auto-selected hook:", err);
  }

  await refreshAfterChange();
}

function clearSelectedHook() {
  const state = window.appState || {};
  state.hook = state.hook || {};

  state.hook.selected = null;
  state.hook.locked = false;
  state.hook.userSelected = false;

  // 🔥 IMPORTANT
  window.appState.captionsDirty = false;

  workingCaptionsText = lastSavedCaptionsText || "";

  const editor = document.getElementById("captionsText");
  if (editor) editor.value = workingCaptionsText;

  updateHookLockUI();
  renderStep3Diff(lastSavedCaptionsText || "", workingCaptionsText || "");
  refreshAfterChange();

  console.log("🧠 Hook cleared — unlocked");
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
        card.classList.add("locked");
      });

    } else {
      // 🔓 Unlocked state
      lockBar?.classList.add("hidden");
      clearBtn.classList.add("hidden");

      document.querySelectorAll(".hookCard").forEach(card => {
        card.classList.remove("locked");
      });
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

async function autoBoostSelectedHook() {
    const hook = window.appState.hook.selected;

    console.log("AUTO BOOST selected hook:", window.appState?.hook?.selected);
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

      await jsonFetch("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        text: newCaptions
      }),
    });

    CONFIG_CACHE = null;
    lastSavedCaptionsText = newCaptions;
    workingCaptionsText = newCaptions;
    window.appState.captionsDirty = true;

    // keep hook state in sync with boosted result
    window.appState.hook.selected = newHook;
    window.appState.hook.locked = true;
    window.appState.hook.userSelected = false;
    updateHookLockUI();

    // force Step 3 editor to show the committed text
    captionViewMode = "rewritten";
    renderCaptionView();

    toast?.(`✨ Best score ${bestScore} after ${attempts} attempts`);

    setStatus("hookLabStatus", "Auto optimization complete ✓", "success");

    await loadConfigAndYaml();
    await refreshAfterChange();

    } catch (e) {
      console.error(e);
      setStatus("hookLabStatus", "Auto optimization failed", "error");
    }
  }


  async function improveHook() {
  console.log("improveHook() fired");

  const selected = window.appState?.hook?.selected || null;
  if (!selected) {
    console.warn("improveHook() aborted: no selected hook");
    return;
  }

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

    if (data.status === "proposed") {

      proposeRewrite(data.proposed, "Hook rewrite ready");

      if (statusEl) {
        statusEl.textContent =
          "Hook rewrite ready — review & accept or reject";
      }

      refreshAfterChange();

      return;
    }

    throw new Error("Unexpected response");

  } catch (err) {

    console.error(err);

    if (statusEl) {
      statusEl.textContent = "Failed to improve hook.";
    }

  } finally {

    btn.disabled = false;

  }

}


 function setCurrentVideoIntent(intent) {
    window.appState.hook.intent = intent;
    console.log("🎯 Video intent set to:", intent);
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

    function getHookNextMove(score, delta) {
    if (!score) return "generate";
    if (score < 50 && delta === 0) return "generate";
    if (score < 70) return "improve";
    if (score < 85) return "auto";
    return "done";
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

async function goToHookLab() {
  await loadConfigAndYaml();

  if (!window.appState?.captionsDirty) {
    await loadCaptionsFromYaml();
  } else {
    console.log("🧠 Skipping caption reload in goToHookLab — captionsDirty");
  }

  setVariantsDrawerOpen(true);
  document.getElementById("variantsDrawer")?.classList.remove("closed");
  document.getElementById("hookLab")?.classList.remove("hidden");

  const hooks = window.appState.hook.lastGenerated;

  if (hooks?.length) {
    renderHookLab(hooks);
  } else {
    await generateHooks();
  }

  await new Promise(r => setTimeout(r, 50));

  // document.getElementById("hookLab")
  //   ?.scrollIntoView({ behavior: "smooth", block: "start" });

  highlightHookLab?.();
  updateHookLabGuidance();
}


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



let hooksInitialized = false;

function initHookListeners() {
  if (hooksInitialized) return;
  hooksInitialized = true;

  const qs = (id) => document.getElementById(id);

  qs("generateHooksBtn")?.addEventListener("click", generateHooks);
  qs("boostHookBtn")?.addEventListener("click", boostSelectedHook);
  qs("autoBoostHookBtn")?.addEventListener("click", autoBoostSelectedHook);

  qs("improveHookBtn")?.addEventListener("click", async () => {
    clearSelectedHook();
    await improveHook();
  });

  qs("clearHookBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    clearSelectedHook();
  });

  qs("openHookLabBtn")?.addEventListener("click", () => {
    toggleVariantsPanel(false);
    qs("hookLab")?.classList.remove("hidden");
    qs("hookLab")?.scrollIntoView({ behavior: "smooth", block: "start" });
    highlightHookLab?.();
    updateHookLabGuidance();
  });

  qs("editSmartStatus")?.addEventListener("click", () => {
    const hook =
      window.appState?.scores?.hook ??
      LAST_HOOK_SCORE ?? 0;

    const flow =
      window.appState?.scores?.storyFlow ??
      LAST_FLOW_SCORE ?? 0;

    if (hook < 60) {
      goToHookLab();
    } else if (flow < 70) {
      qs("improveStoryFlowBtn")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      qs("exportBtn")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });

  // Hook score click
  const hookScore = qs("hookScoreValue");
  if (hookScore) {
    hookScore.classList.add("clickable");

    hookScore.addEventListener("click", () => {
      toggleVariantsPanel(false);
      qs("hookLab")?.classList.remove("hidden");

      qs("variantsDrawer")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });

      highlightHookLab?.();
      updateHookLabGuidance();
    });
  }

  // Intent pills (delegated)
  const pillContainer = document.querySelector(".intent-pills");
  if (pillContainer) {
    pillContainer.addEventListener("click", async (e) => {
      const pill = e.target.closest(".pill");
      if (!pill) return;

      const intent = pill.dataset.intent;
      if (!intent) return;

      const state = window.appState || {};
      if (intent === state.hook.intent) return;

      state.hook.intent = intent;

      syncIntentPills(intent);
      updateIntentHint(intent);

      if (typeof saveIntent === "function") {
        await saveIntent(intent);
      }

      await refreshAfterChange();

      setStatus("hookLabStatus", `Intent set to “${intent}”`, "info");
    });
  }

  // Intent select (optional legacy)
  const intentSelect = qs("intentSelect");
  if (intentSelect) {
    intentSelect.addEventListener("change", async () => {
      window.appState.hook.intent = intentSelect.value;
      await saveIntent(window.appState.hook.intent);
      await refreshAfterChange();
    });
  }

  qs("applyAiRecommendationBtn")?.addEventListener("click", applyAIRecommendation);
  qs("undoAiRecommendationBtn")?.addEventListener("click", undoAIRecommendation);

  updateHookLockUI();
}

async function initHookBoot() {
  await loadIntentFromConfig();
  hydrateExistingHooksIfAny();
}
