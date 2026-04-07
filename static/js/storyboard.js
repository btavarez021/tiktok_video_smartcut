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


  async function suggestStoryboardOrder() {
  try {
    setStatus("storyboardStatus", "AI suggesting better clip order…", "working", false);

    const res = await jsonFetch("/api/storyboard/suggest_order", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession()
      })
    });

    const suggested = res?.suggested_order || [];

    if (!suggested.length) {
      setStatus("storyboardStatus", "No order suggestion available", "info");
      return;
    }

    workingClipOrder = suggested;
    clipOrderDirty = true;

    renderStoryboardTimeline({
      first_clip: workingClipOrder[0],
      middle_clips: workingClipOrder.slice(1, -1),
      last_clip: workingClipOrder[workingClipOrder.length - 1]
    });

    await saveStoryboardOrder({ silent: true });
    await refreshAfterChange();

    setStatus("storyboardStatus", "AI suggested a smoother story order ✓", "success");
  } catch (err) {
    console.error(err);
    setStatus("storyboardStatus", "Failed to suggest clip order", "error");
  }
}


async function hydrateStoryboardAndScroll() {
  CONFIG_CACHE = null;
 
  await loadConfigAndYaml();
  await loadCaptionsFromYaml();
  await loadSessionContext();
  await loadContentContext();

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

    await showStoryboardHandoffMessage();

    activateStep("#step-3");

    const el = document.getElementById("storyboardTimeline");

    el?.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });

    // wait for smooth scroll to settle slightly
    setTimeout(() => {
      highlightStoryboardTimeline();
    }, 500);
  }

  await refreshAfterChange();
}


function showStoryboardHandoffMessage() {
  return new Promise(resolve => {
    const el = document.getElementById("analyzeStatus");
    if (!el) {
      resolve();
      return;
    }

    el.textContent = `🧠 AI setup complete

✓ Clips analyzed
✓ Storyboard prepared
✓ Captions drafted

Opening clip order review…`;

    el.className = "status-text status-success aiSetupStatus";
    el.classList.remove("fade-out");

    setTimeout(() => {
      el.classList.add("fade-out");

      setTimeout(() => {
        el.textContent = "";
        el.className = "status-text status-info";
        el.classList.remove("fade-out");
        resolve();
      }, 600); // match CSS fade duration
    }, 2200);
  });
}

  function highlightStoryboardTimeline() {
    const el = document.getElementById("storyboardTimeline");
    if (!el) return;

    el.classList.add("clip-highlight");
    setTimeout(() => el.classList.remove("clip-highlight"), 1200);
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


  async function handleStoryboardContinue() {
  if (STORYBOARD_CONTINUE_RUNNING) {
    console.log("🧠 Storyboard continue skipped: already running");
    return;
  }

  STORYBOARD_CONTINUE_RUNNING = true;

  try {
    const autoAssist = window.appState?.settings?.autoAssist === true;

    if (autoAssist) {
      console.log("🧠 Auto Assist triggered from storyboard");
      await runCreativeEngine("storyboard_complete");
    }

    await goToHookLab();
  } finally {
    STORYBOARD_CONTINUE_RUNNING = false;
  }
}

  function openStep(stepId) {
    document.querySelector(`.step[data-target="${stepId}"]`)?.click();

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

async function initStoryboardBoot() {
  try {
    const data = await jsonFetch(
      `/api/generate_yaml/status?session=${getActiveSession()}`
    );

    if (data.status === "running") {
      YAML_POLL_ACTIVE = true;
      lastYamlStatus = "running";
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
}

function initStoryboardListeners() {

  document.getElementById("continueToHooksBtn")
    ?.addEventListener("click", handleStoryboardContinue);

  document.getElementById("confirmStoryboardBtn")
    ?.addEventListener("click", handleStoryboardContinue);

  document.getElementById("generateYamlBtn")
    ?.addEventListener("click", generateYamlAsync);

  document.getElementById("refreshYamlBtn")
    ?.addEventListener("click", loadConfigAndYaml);

  document.getElementById("saveYamlBtn")
    ?.addEventListener("click", saveYaml);

  document.getElementById("suggestStoryboardOrderBtn")
    ?.addEventListener("click", suggestStoryboardOrder);

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
}

const autoSaveStoryboardOrder = debounce(() => {
    saveStoryboardOrder({ silent: true });
  }, 600);


