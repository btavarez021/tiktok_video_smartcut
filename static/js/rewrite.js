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

  function clearOverlayWarning() {
  const el = document.getElementById("overlayStatus");
  if (!el) return;
  el.textContent = "";
  el.className = "status-text";
}

function updateRewriteWarning() {
    const mode = document.querySelector('input[name="captionRewriteMode"]:checked')?.value;
    const warning = document.getElementById("rewriteWarning");
    if (!warning) return console.warn("rewriteWarning element missing");

    warning.classList.toggle("hidden", mode !== "rewrite");
}

async function loadRewriteMode() {
    const data = await getConfigCached();
    const mode = data.config?.render?.rewrite_mode || "visual";

    const radio = document.querySelector(`input[name="captionRewriteMode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    updateRewriteWarning();  // Reflect state visually
}

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
// Load Caption + Rewrite Mode From YAML
// ================================

async function initRewriteBoot() {
  await loadRewriteMode();     
  updateRewriteWarning();      
}

let rewriteGlobalBound = false;

function bindRewriteGlobalEvents() {
  if (rewriteGlobalBound) return;
  rewriteGlobalBound = true;

  document.addEventListener("click", async (e) => {
    const accept = e.target.closest('[data-action="accept-rewrite"]');
    const reject = e.target.closest('[data-action="reject-rewrite"]');

    if (!accept && !reject) return;

    // ACCEPT
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

        CONFIG_CACHE = null;

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

    // REJECT
    if (reject) {
      workingCaptionsText = lastSavedCaptionsText;
      hardClearRewriteUI();
      setStatus("overlayStatus", "Rewrite discarded", "info");
    }
  });
}

function initRewriteListeners(){
  bindRewriteGlobalEvents(); // ✅ ADD HERE

  // Activate radio toggles
  document.querySelectorAll('input[name="captionRewriteMode"]').forEach(el => {
      el.addEventListener("change", updateRewriteWarning);
  });

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


  // Run once after load
  updateRewriteModeAvailability();

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
    
}


