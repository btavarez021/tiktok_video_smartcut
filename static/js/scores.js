function evaluateCreativeState() {
  window.appState = window.appState || {};
  window.appState.setup = window.appState.setup || {
    hookConfidence: "unknown",
    labelQuality: "unknown",
    clipCount: 0
  };

  const hook = window.appState?.scores?.hook ?? LAST_HOOK_SCORE ?? null;
  const flow = window.appState?.scores?.storyFlow ?? LAST_FLOW_SCORE ?? null;
  const effectiveFlow =
    hook !== null && hook < 60
      ? null
      : flow;
  const intent = window.appState?.hook?.intent || "default";
  const captions =
    (typeof getCurrentCaptionsText === "function" ? getCurrentCaptionsText() : "") ||
    lastSavedCaptionsText ||
    "";

  const blocks = captions
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean);

  const captionCount = blocks.length;

  const lowerCaptions = captions.toLowerCase();
  const hasCTA =
    lowerCaptions.includes("follow") ||
    lowerCaptions.includes("subscribe") ||
    lowerCaptions.includes("book");
  
  const weaknesses = [];
  const priority = [];
  
  const orderNeedsImprovement =
  window.appState?.storyboard?.suggestedOrderAvailable === true;

  if (orderNeedsImprovement) {
    weaknesses.unshift("Storyboard order");
    priority.unshift("Optimize clip sequence");
  }

  console.log(
    "ORDER CHECK",
    window.appState?.storyboard?.suggestedOrderAvailable,
    orderNeedsImprovement
  );

  // Hook Analysis
  if (hook !== null) {
    if (hook < 60) {
      weaknesses.push("Hook clarity");
      priority.push("Improve hook immediately");
    } else if (hook < 75) {
      weaknesses.push("Hook strength");
      priority.push("Refine hook for stronger impact");
    }
  }

  // Flow Analysis
if (effectiveFlow !== null) {
  if (effectiveFlow < 65) {
    weaknesses.push("Story pacing");
    priority.push("Shorten middle captions");
  } else if (effectiveFlow < 75) {
    weaknesses.push("Narrative progression");
    priority.push("Improve transition between captions");
  }
}

  // Structural Checks
  if (captionCount < 3) {
    weaknesses.push("Video depth");
    priority.push("Add more storytelling content");
  }

  if (!hasCTA) {
    priority.push("Optional: add a closing CTA");
  }

  // Readiness Score
  let readiness = 0;

  if (hook !== null) readiness += hook * 0.4;
  if (effectiveFlow !== null) readiness += effectiveFlow * 0.4;
  if (hasCTA) readiness += 10;
  if (captionCount >= 3) readiness += 10;

  // Setup Confidence Multiplier
  const setup = window.appState?.setup || {};

  let confidenceMultiplier = 1;

  if (setup.hookConfidence === "high") {
    confidenceMultiplier += 0.05;
  } else if (setup.hookConfidence === "low") {
    confidenceMultiplier -= 0.05;
  }

  if (setup.labelQuality === "strong") {
    confidenceMultiplier += 0.03;
  } else if (setup.labelQuality === "weak") {
    confidenceMultiplier -= 0.03;
  }

  if (setup.clipCount >= 6) {
    confidenceMultiplier += 0.02;
  } else if (setup.clipCount <= 2) {
    confidenceMultiplier -= 0.02;
  }

  readiness = Math.round(readiness * confidenceMultiplier);
  readiness = Math.max(0, Math.min(readiness, 100));

  const publishReady =
    hook !== null &&
    hook >= 75 &&
    (effectiveFlow === null || effectiveFlow >= 65);

  let status = "polish";
  let message = "Good edit. Minor improvements possible.";
  let next = "polish";

  if (orderNeedsImprovement) {
  status = "reorder_storyboard";
  message = "Clip order can be improved before polishing captions.";
  next = "reorder_storyboard";
} else if (!captions.trim()) {
  status = "empty";
  message = "Create captions to begin.";
  next = "write_captions";
} else if (hook !== null && hook < 60) {
    status = "weak_hook";
    message = "Your hook needs stronger curiosity or clarity.";
    next = "improve_hook";
  } else if (effectiveFlow !== null && effectiveFlow < 65) {
    status = "weak_flow";
    message = "Tighten pacing and transitions.";
    next = "improve_flow";
  } else if (publishReady) {
    status = "ready";
    message = "Strong edit. Ready to publish.";
    next = "publish";
  } else {
  
  status = "polish";

  if (hook != null && hook < 75) {
    message = "Your edit is good, but the hook could be stronger.";
  }
  else if (effectiveFlow != null && effectiveFlow < 75) {
    message = "Your story flow could be smoother.";
  }
  else if (!hasCTA) {
    message = "A CTA could improve engagement.";
  }
  else {
    message = "Optimizing final details.";
  }

  if (orderNeedsImprovement) {
  next = "reorder_storyboard";
  }

  // 🚨 Broken hook
  else if (hook !== null && hook < 60) {
    next = "improve_hook";
  }

  // 🚨 Broken story flow takes priority over hook polish
  else if (effectiveFlow !== null && effectiveFlow < 60) {
    next = "improve_flow";
  }

  // 📉 Fix the weaker element
  else if (hook !== null && effectiveFlow !== null && effectiveFlow < hook) {
    next = "improve_flow";
  }

  // ✨ Polish hook
  else if (hook !== null && hook < 75) {
    next = "improve_hook";
  }

  // ✨ Polish flow
  else if (effectiveFlow !== null && effectiveFlow < 75) {
    next = "improve_flow";
  }

  else {
    next = "publish";
  }
}

  const result = {
    hook_score: hook,
    flow_score: effectiveFlow,
    readiness_score: readiness,
    caption_blocks: captionCount,
    has_cta: hasCTA,
    order_needs_improvement: orderNeedsImprovement,
    primary_weakness: weaknesses[0] || null,
    all_weaknesses: weaknesses,
    priority_actions: priority,
    publish_ready: publishReady,
    status,
    message,
    next,
    intent
  };

  console.log("NEXT ACTION", next);

  result.primary_focus = getPrimaryCreativeFocus(result);

  window.appState.creative = result;
  return result;
}

function getPrimaryCreativeFocus(state) {
  if (!state) return "scoring";

  const hook = state.hook_score;
  const flow = state.flow_score;

  // Nothing scored yet
  if (hook == null && flow == null) return "scoring";

  // Weak hook takes priority, even if flow is locked/null
  if (hook != null && hook < 60) return "hook";

  // If hook exists but flow is locked/unavailable, keep focusing on hook polish
  if (hook != null && flow == null) {
    return hook < 75 ? "hook" : "publish";
  }

  // If somehow hook is missing but flow exists
  if (hook == null && flow != null) return "flow";

  if (flow < 60) return "flow";

  if (flow < hook && flow < 75) return "flow";
  if (hook < 75) return "hook";
  if (flow < 75) return "flow";

  return "publish";
}

async function refreshHookScore() {
  const card = document.querySelector(".hook-score-card");
  const scoreEl = document.getElementById("hookScoreValue");
  const reasonsEl = document.getElementById("hookScoreReasons");
  const hookEl = document.getElementById("hookScoreHook");
  const statusEl = document.getElementById("hookScoreStatus");

  if (!card || !scoreEl || !reasonsEl || !hookEl) return;

  const selectedHook = window.appState?.hook?.selected?.trim();
  const currentText = getCurrentCaptionsText();

  const text = selectedHook
    ? [selectedHook, ...currentText.split(/\n\s*\n/).slice(1)].join("\n\n")
    : currentText;

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
      intent: window.appState?.hook?.intent || "discovery"
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

    handleHookScoreSideEffects(score);

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


// Story Flow Score
// Evaluates ONLY middle captions (excludes hook + CTA)
// Read-only score to assess pacing & narrative progression

async function refreshStoryFlowScore() {
    const captionsEl = document.getElementById("captionsText");
    const card = document.querySelector(".story-flow-card");
    const scoreEl = document.getElementById("storyFlowScoreValue");
    const reasonsEl = document.getElementById("storyFlowReasons");
    const improveBtn = document.getElementById("improveStoryFlowBtn");
    const labelEl = document.getElementById("storyFlowScoreLabel");

    if (!captionsEl || !card || !scoreEl || !reasonsEl) return;

    const text = getCurrentCaptionsText();
    const hookScore =
      window.appState?.scores?.hook ??
      LAST_HOOK_SCORE ??
      null;

    if (!text) {
      card.classList.add("hidden");
      LAST_FLOW_SCORE = null;
      window.appState.scores.storyFlow = null;
      return;
    }
    
    
    // 🔒 Weak hook locks flow scoring
    const hookChosen = !!window.appState?.hook?.selected;

    if (hookScore != null && hookScore < 60 && !hookChosen) {
      card.classList.remove("hidden");

      LAST_FLOW_SCORE = null;
      window.appState.scores.storyFlow = null;

      scoreEl.textContent = "—";

      if (labelEl) labelEl.textContent = "Locked";

      reasonsEl.innerHTML = `<li>Improve the opening hook to unlock story flow scoring.</li>`;

      if (improveBtn) improveBtn.disabled = true;

      card.classList.remove("good", "ok", "bad");
      scoreEl.classList.remove("good", "ok", "bad");

      updateCollapsibleHeaders(
        LAST_HOOK_SCORE,
        LAST_HOOK_SCORE != null ? getHookRatingLabel(LAST_HOOK_SCORE) : null,
        null,
        "Locked"
      );

      const creativeState = evaluateCreativeState();
      renderPublishReadyState(creativeState);
      renderEditProgress();
      updateSmartStatus();

      return;
    }

    const blocks = text
        .split(/\n\s*\n/)
        .map(b => b.trim())
        .filter(Boolean);

    // Need at least: hook + 2 middle captions
    if (blocks.length < 3) {
      if (labelEl) labelEl.textContent = "—";
      card.classList.add("hidden");
      if (improveBtn) improveBtn.disabled = true;
      LAST_FLOW_SCORE = null;
      window.appState.scores.storyFlow = null;
      scoreEl.textContent = "—";
      reasonsEl.innerHTML = "";
      card.classList.remove("good", "ok", "bad");
      scoreEl.classList.remove("good", "ok", "bad");
      updateCollapsibleHeaders(
        LAST_HOOK_SCORE,
        LAST_HOOK_SCORE != null ? getHookRatingLabel(LAST_HOOK_SCORE) : null,
        null,
        null
      );
      return;
    }

    card.classList.remove("hidden");
    if (improveBtn) improveBtn.disabled = false;

    // ⚠ Weak hook but user accepted it
    if (hookScore != null && hookScore < 60 && hookChosen) {
      reasonsEl.innerHTML = `
        <li>⚠ Hook is weak but accepted — scoring story flow anyway.</li>
      `;
    }

    try {
        const data = await jsonFetch("/api/story_flow_score", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session: getActiveSession(),
            captions: text
          })
        });

        const score = Number(data.score ?? 0);

        if (LAST_FLOW_SCORE !== null && score > LAST_FLOW_SCORE) {
          celebrateImprovement("flow", LAST_FLOW_SCORE, score);
        }

        LAST_FLOW_SCORE = score;
        window.appState.scores.storyFlow = score;

        const creativeState = evaluateCreativeState();
        renderPublishReadyState(creativeState);

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

        if (labelEl) labelEl.textContent = getFlowRatingLabel(score);


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
        const acceptedWeakHookNote =
          hookScore != null && hookScore < 60 && hookChosen
            ? `<li>⚠ Hook is weak but accepted — scoring story flow anyway.</li>`
            : "";

        reasonsEl.innerHTML =
          acceptedWeakHookNote +
          (reasons.length
            ? reasons.map(r => `<li>${r}</li>`).join("")
            : `<li>Flow looks solid ✅</li>`);
      updateSmartStatus();
    } catch (err) {
        console.error("Story flow score error:", err);
        card.classList.add("hidden");
    }
}

async function scoreStoryFlow() {
  const session = getActiveSession();

  const res = await fetch(`/api/story_flow_score?session=${encodeURIComponent(session)}`);

  if (!res.ok) {
    throw new Error("Story flow scoring failed");
  }

  return await res.json(); // { score, reasons }
}


function renderEditProgress() {
    const fill = document.getElementById("editProgressFill");
    const percentEl = document.getElementById("editProgressPercent");
    const hint = document.getElementById("editProgressHint");

    if (!fill || !percentEl || !hint) return;

    const state = evaluateCreativeState();
    const hook = state.hook_score ?? 0;
    const flow = state.flow_score ?? 0;

    console.log("📊 Progress using:", hook, flow);

    if (LAST_HOOK_SCORE == null) {
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

    const progress = Math.min(
      100,
      Math.round(
        flow > 0
          ? (hook * 0.6) + (flow * 0.4)
          : hook
      )
    );

    fill.style.width = `${progress}%`;
    percentEl.textContent = `${progress}%`;

    const focus = state.primary_focus;

    if (focus === "hook") {
      hint.textContent =
        hook < 60
          ? "Strengthen the hook to gain momentum."
          : "Strong hook — refine it for more impact.";
    } else if (focus === "flow") {
      hint.textContent = "Looking good — refine pacing & flow.";
    } else if (focus === "publish") {
      hint.textContent = progress < 90
        ? "Almost publish ready."
        : "🔥 Excellent. Your edit is elite.";
    } else {
      hint.textContent = "Scoring in progress…";
    }
  }


  function renderPublishReadyState(state) {
  const wrap = document.getElementById("publishReadyBanner");
  const pill = document.getElementById("publishReadyPill");
  const message = document.getElementById("publishReadyMessage");
  const readinessEl = document.getElementById("publishReadinessScore");
  const hookEl = document.getElementById("publishHookScore");
  const flowEl = document.getElementById("publishFlowScore");
  const weaknessEl = document.getElementById("publishPrimaryWeakness");
  const actionsEl = document.getElementById("publishPriorityActions");
  const fixBtn = document.getElementById("exportFixBtn");
  const anywayBtn = document.getElementById("exportAnywayBtn");
  const exportBtn = document.getElementById("exportBtn");

  if (!wrap) return;

  const safeState = state || window.appState?.creative || evaluateCreativeState();

  wrap.classList.remove("hidden", "ready", "needs-work", "polish");

  const readiness = safeState?.readiness_score ?? null;
  const hook = safeState?.hook_score ?? null;
  const flow = safeState?.flow_score ?? null;
  const primaryWeakness = safeState?.primary_weakness || null;
  const priorityActions = safeState?.priority_actions || [];
  const publishReady = safeState?.publish_ready === true;

  if (readinessEl) {
    readinessEl.textContent =
      readiness == null ? "Calculating..." : `${readiness}%`;
  }

  if (hookEl) hookEl.textContent = hook == null ? "—" : `${hook}/100`;
  if (flowEl) flowEl.textContent = flow == null ? "—" : `${flow}/100`;

  if (message) {
    message.textContent = safeState?.message || "Reviewing final edit quality…";
  }

  if (pill) {
    if (publishReady) {
      pill.textContent = "Ready to Export";
      wrap.classList.add("ready");
    } else if (readiness == null) {
      pill.textContent = "Calculating";
      wrap.classList.add("polish");
    } else if (readiness >= 65) {
      pill.textContent = "Can Be Improved";
      wrap.classList.add("polish");
    } else {
      pill.textContent = "Needs Work";
      wrap.classList.add("needs-work");
    }
  }

  if (weaknessEl) {
    if (primaryWeakness) {
      weaknessEl.classList.remove("hidden");
      weaknessEl.textContent = `Primary weakness: ${primaryWeakness}`;
    } else {
      weaknessEl.classList.add("hidden");
      weaknessEl.textContent = "";
    }
  }

  if (actionsEl) {
    if (priorityActions.length) {
      actionsEl.classList.remove("hidden");
      actionsEl.innerHTML = `
        <div class="publish-actions-title">Suggested next steps</div>
        <ul>
          ${priorityActions.slice(0, 2).map(a => `<li>${a}</li>`).join("")}
        </ul>
      `;
    } else {
      actionsEl.classList.add("hidden");
      actionsEl.innerHTML = "";
    }
  }

  if (fixBtn) {
    fixBtn.classList.toggle("hidden", publishReady || safeState?.next === "publish");
    fixBtn.disabled = readiness == null;
  }

  if (anywayBtn) {
    anywayBtn.classList.toggle("hidden", publishReady);
  }

  if (exportBtn) {
    exportBtn.textContent = publishReady ? "🎥 Render & Export" : "🎥 Export Anyway";
    exportBtn.disabled = false;
  }
}

function renderNextActionButton(state) {
  if (!state?.next) return "";

  const labelMap = {
    improve_hook: "⚡ Improve Hook Automatically",
    improve_flow: "⚡ Tighten Story Flow",
    write_captions: "✍️ Generate Captions",
    publish: "🚀 Export Video",
    polish: "✨ Polish Video",
    reorder_storyboard: "🧭 Optimize Storyboard Order"
  };

  const label = labelMap[state.next];
  if (!label) return "";

  return `
    <button class="ai-next-action-btn" data-action="${state.next}">
      ${label}
    </button>
  `;
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

  if (hook === null || flow === null) {
    el.classList.add("hidden");
    return;
  }

  const state = evaluateCreativeState();
  const focus = state.primary_focus;

  el.classList.remove("hidden");
  el.className = "edit-smart-status";

  if (focus === "hook") {
    const hook = state.hook_score ?? 0;

    if (hook < 60) {
      el.textContent = "🔴 Fix Hook First";
      el.classList.add("red");
    } else {
      el.textContent = "🟡 Improve Hook";
      el.classList.add("yellow");
    }
  }
  else if (focus === "flow") {
    el.textContent = "🟡 Improve Story Flow";
    el.classList.add("yellow");
  }
  else if (focus === "publish") {
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


async function refreshAfterChange({
    hooks = true,
    flow = true,
    director = true,
    publish = true,
    progress = true,
    guidance = true
  } = {}) {

    const oldHook = LAST_HOOK_SCORE;
    const oldFlow = LAST_FLOW_SCORE;

    if (REFRESH_LOCK){
      console.log("🧠 Refresh skipped: already in progress");
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

      const newHook = LAST_HOOK_SCORE;
      const newFlow = LAST_FLOW_SCORE;

      if (oldHook && newHook && newHook > oldHook) {
        showAutoAssistUpdate(
          `🧠 Auto Assist improved hook ${oldHook} → ${newHook}`
        );
      }

      if (oldFlow && newFlow && newFlow > oldFlow) {
        showAutoAssistUpdate(
          `🧠 Auto Assist improved story flow ${oldFlow} → ${newFlow}`
        );
      }

    } catch (e) {
      console.warn("refreshAfterChange failed", e);
    } finally {
      REFRESH_LOCK = false;
    }
  }

  function maybeCelebrateReadiness(state) {
      if (LAST_READINESS_STATUS !== "ready" && state.status === "ready") {
        toast("🚀 Publish Ready — AI approves this edit");
        pulseExportButton();
        // maybeConfetti?.(); // optional
      }
      LAST_READINESS_STATUS = state.status;
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

  
  function getHookRatingLabel(score) {
    if (score < 45) return "Needs Work";
    if (score < 60) return "Building Strength";
    if (score < 75) return "Strong Hook";
    if (score < 90) return "Standout";
    return "Viral Energy";
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


  function handleHookScoreSideEffects(score) {
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

  const creativeState = evaluateCreativeState();
  renderPublishReadyState(creativeState);
}

function scoreCaptionRhythm(text) {
  if (!text) return 0;

  const blocks = text
    .split(/\n\s*\n/)
    .map(b => b.trim())
    .filter(Boolean);

  if (!blocks.length) return 0;

  const lengths = blocks.map(b => b.split(/\s+/).filter(Boolean).length);
  const avg = lengths.reduce((a, b) => a + b, 0) / lengths.length;

  let variancePenalty = 0;
  lengths.forEach(len => {
    variancePenalty += Math.abs(len - avg);
  });
  variancePenalty = variancePenalty / lengths.length;

  let score = 100;

  if (avg < 3) score -= 20;
  if (avg > 14) score -= 20;

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
    lastBlock.includes("check it out") ||
    lastBlock.includes("don’t miss") ||
    lastBlock.includes("dont miss");

  return hasCTA ? 100 : 0;
}

function scoreIntentMatch(variant, intent) {
  const tone = (variant?.tone || "").toLowerCase();
  const text = (variant?.text || "").toLowerCase();

  let score = 70;

  if (intent === "discovery") {
    if (tone.includes("punchy")) score += 15;
    if (tone.includes("tiktok")) score += 10;
    if (text.includes("secret") || text.includes("hidden") || text.includes("why")) score += 5;
  }

  if (intent === "personal") {
    if (tone.includes("story")) score += 15;
    if (tone.includes("creator")) score += 10;
    if (text.includes("i ") || text.includes("my ")) score += 5;
  }

  if (intent === "aesthetic") {
    if (tone.includes("luxury")) score += 15;
    if (tone.includes("minimal")) score += 10;
    if (text.includes("views") || text.includes("breeze") || text.includes("rooftop")) score += 5;
  }

  if (intent === "informational") {
    if (tone.includes("info")) score += 15;
    if (text.includes("what") || text.includes("how")) score += 5;
  }

  return Math.max(0, Math.min(100, score));
}

function computeVariantStrength(variant, intent = "discovery") {
  const hook = variant.hook_score ?? 0;
  const flow = variant.flow_score ?? null;
  const rhythm = scoreCaptionRhythm(variant.text);
  const cta = scoreCtaPresence(variant.text);
  const intentMatch = scoreIntentMatch(variant, intent);

  if (flow == null) {
    return Math.round(
      (hook * 0.45) +
      (rhythm * 0.25) +
      (intentMatch * 0.20) +
      (cta * 0.10)
    );
  }

  return Math.round(
    (hook * 0.35) +
    (flow * 0.25) +
    (rhythm * 0.20) +
    (intentMatch * 0.10) +
    (cta * 0.10)
  );
}

  function getFlowRatingLabel(score) {
    if (score < 50) return "Rough";
    if (score < 65) return "Improving";
    if (score < 80) return "Smooth";
    if (score < 90) return "Excellent";
    return "Elite";
  }

function initScoreListeners() {
  const btn = document.getElementById("improveStoryFlowBtn");
  const status = document.getElementById("storyFlowStatus");

  if (!btn) return;

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    if (status) status.textContent = "Improving story flow…";

    try {
      const res = await jsonFetch("/api/story_flow_improve", {
        method: "POST",
        body: JSON.stringify({ session: getActiveSession() }),
      });

      if (res.updated && res.text) {
        await jsonFetch("/api/save_captions", {
          method: "POST",
          body: JSON.stringify({
            session: getActiveSession(),
            text: res.text
          }),
        });

        CONFIG_CACHE = null;
        lastSavedCaptionsText = res.text;
        workingCaptionsText = res.text;

        if (status) status.textContent = "Story flow improved ✓";

        await loadConfigAndYaml();
        await loadCaptionsFromYaml();
        await refreshAfterChange();
        await runCreativeEngine("captions_changed");

      } else {
        if (status) status.textContent = res.reason || "No changes made.";
      }
    } catch (err) {
      console.error(err);
      if (status) status.textContent = "Failed to improve story flow.";
    } finally {
      btn.disabled = false;
    }
  });
}