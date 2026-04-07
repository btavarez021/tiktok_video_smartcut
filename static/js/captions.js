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

    window.appState.hook = window.appState.hook || {};
    window.appState.hook.selected = text.split(/\n\s*\n/)[0]?.trim() || null;
    window.appState.hook.locked = true;

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

    const appliedId = meta?.id;

    if (appliedId && Array.isArray(window.appState?.variants?.list)) {
      const hookScore =
        window.appState?.scores?.hook ??
        LAST_HOOK_SCORE ??
        null;

      const flowScore =
        window.appState?.scores?.storyFlow ??
        LAST_FLOW_SCORE ??
        null;

      window.appState.variants.list = window.appState.variants.list.map(v => {
        if (v._cardId !== appliedId) {
          return {
            ...v,
            applied: false
          };
        }

        return {
          ...v,
          hook_score: hookScore,
          flow_score: flowScore,
          applied: true
        };
      });

      rerenderVariantsList();
    }

  } catch (err) {
    console.error(err);
    setStatus("captionsStatus", "Failed to apply caption", "error");
  }
}

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


let captionAutoSaveTimer = null;
let rewriteInitialized = false;

function handleCaptionAutosave() {
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
}

function toggleStep4Diff() {
  const scroll = document.getElementById("step4CaptionScroll");
  const btn = document.getElementById("toggleDiffCollapse");
  if (!scroll) return;

  const isHidden = scroll.style.display === "none";
  scroll.style.display = isHidden ? "block" : "none";

  if (btn) {
    btn.textContent = isHidden ? "Collapse" : "Expand";
  }
}

function toggleStep3Diff() {
  const scroll = document.getElementById("step3CaptionScroll");
  const btn = document.getElementById("step3DiffToggle");
  if (!scroll) return;

  const isHidden = scroll.style.display === "none";
  scroll.style.display = isHidden ? "block" : "none";

  if (btn) {
    btn.textContent = isHidden ? "Collapse" : "Expand";
  }
}

async function initRewriteBoot() {
  await loadCaptionsFromYaml();

  if (window.preBoostCaptions) {
    renderStep3Diff(
      window.preBoostCaptions,
      workingCaptionsText || lastSavedCaptionsText || ""
    );
    focusCaptionChanges?.();
  }
}

function initRewriteListeners() {
  if (rewriteInitialized) return;
  rewriteInitialized = true;

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

    captionsBox.addEventListener("input", updateRewriteModeAvailability);

    captionsBox.addEventListener("input", () => {
      const el = document.getElementById("captionInlineStatus");
      if (el) el.classList.add("hidden");
    });

    captionsBox.addEventListener("input", handleCaptionAutosave);
  }

  document
    .getElementById("loadCaptionsFromYamlBtn")
    ?.addEventListener("click", loadCaptionsFromYaml);

  document
    .getElementById("saveCaptionsBtn")
    ?.addEventListener("click", saveCaptions);

  document
    .getElementById("toggleDiffCollapse")
    ?.addEventListener("click", toggleStep4Diff);

  document
    .getElementById("step3DiffToggle")
    ?.addEventListener("click", toggleStep3Diff);
}