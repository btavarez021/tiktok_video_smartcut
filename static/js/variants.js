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

        showAutoAssistUpdate(
        "🧠 Auto Assist optimized captions for stronger storytelling"
      );

      const rawVariants = data.result?.variants || [];
      const intent = window.appState?.hook?.intent || "discovery";

      const variants = rawVariants.map(v => {
      const flowScore = v.flow_score ?? v.story_flow ?? null;

        return {
          ...v,
          flow_score: flowScore,
          smart_score: computeVariantStrength(
            {
              ...v,
              flow_score: flowScore
            },
            intent
          )
        };
      });

      console.log("VARIANTS RAW:", rawVariants);
      console.log("VARIANTS FINAL:", variants);

        // 🔑 Global state
        window.appState.variants.list = variants;

        // Sort: AI recommended first
        variants.forEach(v => {
          v.recommended = false;
        });

        const bestIndex = variants.reduce((bestIdx, current, idx, arr) => {
          if (idx === 0) return 0;
          return (current.smart_score || 0) > (arr[bestIdx].smart_score || 0)
            ? idx
            : bestIdx;
        }, 0);

        if (variants[bestIndex]) {
          variants[bestIndex].recommended = true;
        }

        variants.sort((a, b) => {
          if (a.recommended) return -1;
          if (b.recommended) return 1;
          return (b.smart_score || 0) - (a.smart_score || 0);
        });

        const box = document.getElementById("variantsOutput");
        box.innerHTML = "";
        box.dataset.rendered = "false";

        variants.forEach((variant, i) => {
        try {
          const cardId = `variant_${i}`;
          variant._cardId = cardId;

          box.innerHTML += renderVariantCard(i + 1, variant, cardId);

          sendVariantFeedback({
            variantId: cardId,
            intent: window.appState.hook.intent,
            tone: variant.tone,
            confidence: variant.confidence,
            recommended: variant.recommended === true,
            action: "viewed"
          });
        } catch (err) {
          console.error("Variant render failed:", variant, err);
        }
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

  function rerenderVariantsList() {
  const box = document.getElementById("variantsOutput");
  if (!box) return;

  let variants = window.appState?.variants?.list || [];

variants = [...variants].sort((a, b) => {
  if (a.applied && !b.applied) return -1;
  if (!a.applied && b.applied) return 1;

  if (a.recommended && !b.recommended) return -1;
  if (!a.recommended && b.recommended) return 1;

  return (b.smart_score || 0) - (a.smart_score || 0);
});

  box.innerHTML = "";

  variants.forEach((variant, i) => {
    const cardId = variant._cardId || `variant_${i}`;
    box.innerHTML += renderVariantCard(i + 1, variant, cardId);
  });

  updateAIRecommendationBar();
}

function renderVariantCard(num, variant, cardId) {
  const text = variant.text || "";
  const tone = variant.tone || "";
  const recommended = variant.recommended === true;
  const reason = variant.recommend_reason || "";
  const confidence = variant.confidence || "close";
  const confLabel = confidenceLabel(normalizeConfidence(confidence));
  const escaped = text.replace(/`/g, "\\`");

  const hookScore = variant.hook_score ?? "—";
  const flowScore = variant.flow_score ?? "—";
  const appliedBadge = variant.applied
  ? `<div class="variantAppliedBadge">🟢 Current Version</div>`
  : "";

  const strength = computeVariantDisplayStrength(variant);

  const fallbackReason = recommended
    ? `Smart score ${variant.smart_score ?? strength}. Best match for ${window.appState?.hook?.intent || "your current"} goal.`
    : "";

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
      recommended && (reason || fallbackReason)
        ? `
          <div class="variantWhyToggle"
              onclick="toggleVariantWhy('${cardId}')">
            Why this won ▾
          </div>

          <div class="variantWhy hidden" id="${cardId}_why">
            ${reason || fallbackReason}
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
    <div class="variantCard ${recommended ? "recommended" : ""} ${variant.applied ? "applied" : ""}" id="${cardId}">

      <div class="variantHeader">
        <h4>Version ${num}</h4>
        ${badge}
        ${appliedBadge}
      </div>

      <div class="variantScores">
        Hook: ${hookScore} · Flow: ${flowScore}
        <span class="variantStrength">Strength: ${strength}</span>
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
          applyCaptionVariant(\`${escaped}\`, {
          id: '${cardId}',
          tone: '${tone}',
          intent: '${window.appState.hook.intent}'
        });
        ">
          ${variant.applied ? "Currently Applied" : "Use This"}
        </button>
      </div>
    `;
  }


    function toggleVariantWhy(cardId) {
    const el = document.getElementById(`${cardId}_why`);
    if (!el) return;

    el.classList.toggle("hidden");
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

  function confidenceLabel(level) {
    if (!level) return "";

    return {
    clear: "Clear winner",
    moderate: "Strong option",
    close: "Creative choice"
  }[level] || "";
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

    function updateVariantRunningBadge(status) {
    const el = document.getElementById("variantRunningBadge");
    if (!el) return;

    el.classList.toggle("hidden", status !== "running");
  }

  function setVariantsStatus(message, state = "loading") {
    const el = document.getElementById("variantsInlineStatus");
    if (!el) return;

    el.textContent = message;
    el.className = `inline-status ${state}`;
    el.classList.remove("hidden");
  }

    function scoreCaptionRhythm(text) {
  if (!text) return 0;

  const blocks = text
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean);

  if (blocks.length === 0) return 0;

  const lengths = blocks.map(b => b.split(/\s+/).filter(Boolean).length);

  const avg = lengths.reduce((a, b) => a + b, 0) / lengths.length;

  let variancePenalty = 0;
  lengths.forEach(len => {
    variancePenalty += Math.abs(len - avg);
  });

  variancePenalty = variancePenalty / lengths.length;

  let score = 100;

  // Too short or too long overall
  if (avg < 3) score -= 20;
  if (avg > 14) score -= 20;

  // Uneven caption rhythm
  score -= Math.min(35, Math.round(variancePenalty * 4));

  return Math.max(0, Math.min(100, Math.round(score)));
}

function scoreCtaPresence(text) {
  if (!text) return 0;

  const blocks = text
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean);

  if (!blocks.length) return 0;

  const lastBlock = blocks[blocks.length - 1].toLowerCase();

  const hasCTA =
    lastBlock.includes("follow") ||
    lastBlock.includes("book") ||
    lastBlock.includes("save") ||
    lastBlock.includes("visit") ||
    lastBlock.includes("come back") ||
    lastBlock.includes("don’t miss") ||
    lastBlock.includes("dont miss") ||
    lastBlock.includes("check it out");

  return hasCTA ? 100 : 0;
}

function computeVariantStrength(variant) {
  const hook = variant.hook_score ?? 0;
  const flow = variant.flow_score ?? null;
  const rhythm = scoreCaptionRhythm(variant.text);
  const cta = scoreCtaPresence(variant.text);

  if (flow == null) {
    return Math.round(
      (hook * 0.7) +
      (rhythm * 0.2) +
      (cta * 0.1)
    );
  }

  return Math.round(
    (hook * 0.45) +
    (flow * 0.30) +
    (rhythm * 0.15) +
    (cta * 0.10)
  );
}

function computeVariantDisplayStrength(variant) {
  const hook = variant.hook_score ?? 0;
  const flow = variant.flow_score ?? null;

  return flow != null
    ? Math.round((hook * 0.6) + (flow * 0.4))
    : hook;
}


  function updateVariantStoryScore(id, flow) {
    const el = document.querySelector(`#${id} .storyScoreValue`);
    if (el) el.textContent = flow?.score ?? "—";
  }

  function openVariantsDrawer() {
    const drawer = document.getElementById("variantsDrawer");
    if (!drawer) return;

    drawer.classList.remove("closed");

    const btn = document.getElementById("variantsToggleBtn");
    if (btn) btn.textContent = "Collapse";
  }


let variantsInitialized = false;

async function initVariantBoot() {
  updateVariantModeAvailability();
  
  try {
    const data = await jsonFetch(
      `/api/variants/status?session=${getActiveSession()}`
    );

    if (data?.status === "running") {
      updateVariantRunningBadge("running");

      if (!VARIANT_POLL_ACTIVE) {
        VARIANT_POLL_ACTIVE = true;
        pollVariantStatus();
      }
    }
  } catch (err) {
    console.warn("Failed to resume variant polling on load", err);
  }
}

function updateVariantModeAvailability() {
  const mode = getContentMode();

  const rewrite = document.getElementById("mode_rewrite");
  const story = document.getElementById("mode_story");
  const minimal = document.getElementById("mode_minimal");
  const punchy = document.getElementById("mode_punchy");
  const influencer = document.getElementById("mode_influencer");

  const hint = document.getElementById("voiceoverVariantHint");

  if (!rewrite || !story || !minimal || !punchy || !influencer) return;

  if (mode === "voiceover") {
    punchy.checked = false;
    influencer.checked = false;

    punchy.disabled = true;
    influencer.disabled = true;

    rewrite.disabled = false;
    story.disabled = false;
    minimal.disabled = false;

    punchy.parentElement?.classList.add("disabled");
    influencer.parentElement?.classList.add("disabled");

    rewrite.parentElement?.classList.remove("disabled");
    story.parentElement?.classList.remove("disabled");
    minimal.parentElement?.classList.remove("disabled");

    if (hint) hint.classList.remove("hidden");
  } else {
    punchy.disabled = false;
    influencer.disabled = false;

    punchy.parentElement?.classList.remove("disabled");
    influencer.parentElement?.classList.remove("disabled");

    if (hint) hint.classList.add("hidden");
  }
}

function initVariantListeners() {
  if (variantsInitialized) return;
  variantsInitialized = true;

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

  document
    .getElementById("applyAiRecommendationBtn")
    ?.addEventListener("click", applyAIRecommendation);

  document
    .getElementById("undoAiRecommendationBtn")
    ?.addEventListener("click", undoAIRecommendation);
}