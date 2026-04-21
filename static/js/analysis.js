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


  const summary = await loadAISetupSummary();
  await autoSelectIntentFromReadiness(summary);
  await autoSelectContentContextFromReadiness();

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

async function autoSelectContentContextFromReadiness(summary) {
  if (!summary) return;

  const select = document.getElementById("contentContext");
  if (!select) return;

  // only auto-set if user hasn't changed it
  const current = select.value || "auto";
  if (current !== "auto") return;

  const signals = Array.isArray(summary?.signals) ? summary.signals.join(" ").toLowerCase() : "";
  const recommendedGoal = (summary?.recommended_goal || "").toLowerCase();

  let context = "auto";

  if (signals.includes("hotel")) context = "hotel stay";
  else if (signals.includes("zoo")) context = "adventure";
  else if (signals.includes("travel")) context = "travel vlog";
  else if (signals.includes("restaurant")) context = "restaurant";
  else if (signals.includes("cocktail") || signals.includes("bar")) context = "cocktails / bar";
  else if (signals.includes("gym") || signals.includes("fitness")) context = "fitness";
  else if (recommendedGoal.includes("luxury")) context = "luxury experience";

  if (context === "auto") return;

  select.value = context;

  await jsonFetch("/api/session/context", {
    method: "POST",
    body: JSON.stringify({
      session: getActiveSession(),
      context
    })
  });

  CONFIG_CACHE = null;

  setStatus("hookLabStatus", `AI set content context → ${context}`, "info");
}

function renderSessionContext(data) {
  const el = document.getElementById("sessionContextSummary");
  if (!el) return;

  if (!data?.label) {
    el.classList.add("hidden");
    return;
  }

  const label = (data.label || "general_lifestyle").replace(/_/g, " ");
  const confidence = data.confidence || "low";
  const signals = Array.isArray(data.signals) ? data.signals : [];

  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="ai-summary-card">
      <div class="ai-summary-title">🧭 AI Reel Context</div>
      <div class="ai-summary-sub">
        AI sees this reel as: <strong>${label}</strong>
      </div>
      <div class="ai-summary-sub">
        Confidence: <strong>${confidence}</strong>
      </div>
      <div class="ai-summary-note">
        Signals: ${signals.length ? signals.join(", ") : "none"}
      </div>
    </div>
  `;
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

  async function loadAISetupSummary() {
  const data = await jsonFetch(
    `/api/ai_setup_summary?session=${getActiveSession()}`
  );

  // -----------------------------
  // 🔑 Persist Setup Intelligence
  // -----------------------------
  window.appState = window.appState || {};
  window.appState.setup = window.appState.setup || {};

  window.appState.setup.hookConfidence =
    data?.hook_confidence || "unknown";

  window.appState.setup.labelQuality =
    data?.labels?.quality || "none";

  window.appState.setup.clipCount =
    data?.clips || 0;

  // -----------------------------
  // Render UI
  // -----------------------------
  renderSetupSummary(data, "aiSetupSummaryStep1");

  return data;
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


async function autoSelectContentContextFromReadiness() {
  const select = document.getElementById("contentContext");
  if (!select) return;

  const current = select.value || "auto";
  if (current !== "auto") return;

  const contextData = await loadSessionContext();
  const label = (contextData?.label || "").toLowerCase();

  let context = "auto";

  if (label.includes("hotel")) context = "hotel stay";
  else if (label.includes("travel")) context = "travel vlog";
  else if (label.includes("zoo")) context = "adventure";
  else if (label.includes("restaurant")) context = "restaurant";
  else if (label.includes("bar") || label.includes("cocktail")) context = "cocktails / bar";
  else if (label.includes("fitness") || label.includes("gym")) context = "fitness";

  if (context === "auto") return;

  select.value = context;

  await jsonFetch("/api/session/context", {
    method: "POST",
    body: JSON.stringify({
      session: getActiveSession(),
      context
    })
  });

  CONFIG_CACHE = null;
  setStatus("hookLabStatus", `AI set content context → ${context}`, "info");
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

let analysisInitialized = false;

function initAnalysisListeners() {
  if (analysisInitialized) return;
  analysisInitialized = true;

  // -----------------------------
  // Buttons
  // -----------------------------
  document.getElementById("analyzeBtn")
    ?.addEventListener("click", analyzeClips);

  document.getElementById("refreshAnalysesBtn")
    ?.addEventListener("click", refreshAnalyses);

  // -----------------------------
  // Context dropdown
  // -----------------------------
  document.getElementById("contentContext")
    ?.addEventListener("change", async (e) => {
      const context = e.target.value;
      const session = getActiveSession();

      try {
        await jsonFetch("/api/session/context", {
          method: "POST",
          body: JSON.stringify({ session, context })
        });

        CONFIG_CACHE = null;

        window.appState.hook.lastGenerated = [];
        window.appState.variants.list = [];
        window.appState.variants.recommendedId = null;

        updateHooksReadyUI();
        updateAIRecommendationBar();

        const hookLabOutput = document.getElementById("hookLabOutput");
        const variantsOutput = document.getElementById("variantsOutput");

        if (hookLabOutput) hookLabOutput.innerHTML = "";
        if (variantsOutput) variantsOutput.innerHTML = "";

        setStatus(
          "hookLabStatus",
          `Content context set to "${context}"`,
          "info"
        );

        await generateHooks();
        if (typeof refreshAfterChange === "function") {
          await refreshAfterChange();
        }

      } catch (err) {
        console.error("Failed to save content context", err);
        setStatus("hookLabStatus", "Failed to update content context", "error");
      }
    });

}

async function initAnalysisBoot() {
  await refreshAnalyses();

  setCaptionInlineStatus(
    "Labels loaded. Regenerate captions to apply them.",
    "info"
  );

  setTimeout(async () => {
    try {
      const s = await jsonFetch(`/api/analyze_status?session=${getActiveSession()}`);

      if (s.status === "running") {
        ANALYZE_POLL_ACTIVE = true;
        pollAnalyzeStatus();
      }

      if (s.status === "done") {
        await refreshAnalyses();
        await loadAISetupSummary();
      }
    } catch (e) {
      console.warn("analyze resume check failed", e);
    }
  }, 300);
}