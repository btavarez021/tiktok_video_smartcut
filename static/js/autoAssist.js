function shouldAutoAssistForReason(reason) { 
  return (
    reason === "storyboard_complete" ||
    reason === "manual_auto_assist" ||
    reason === "export_fix"
  );
}


function getAutoAssistHookCandidate() {
  const selected = window.appState?.hook?.selected;
  if (selected) return selected;

  const hooks = window.appState?.hook?.lastGenerated || [];
  if (!Array.isArray(hooks) || hooks.length === 0) return null;

  const recommended = hooks.find(h => h?.recommended && h?.text);
  if (recommended?.text) return recommended.text;

  const first = hooks.find(h => h?.text);
  return first?.text || null;
}

async function runAutoAssistPipeline(state) {
  const action = state.next;

  if (!action || action === "publish") return state;

  console.log("⚡ Auto Assist executing:", action);

  addAutoAssistActivity(`Running: ${action.replace("_", " ")}`);

  const fn = CREATIVE_ACTIONS[action];
  if (!fn) return state;

  await fn();
  await refreshAfterChange();

  state = evaluateCreativeState();
  console.log("🧠 Pipeline state:", state.status);

  return state;
}

async function runCreativeEngine(reason = "update") {
  let state = evaluateCreativeState();

  const autoAssist = window.appState?.settings?.autoAssist === true;
  const allowAutoAssist = autoAssist && shouldAutoAssistForReason(reason);

  if (
    allowAutoAssist &&
    state.hook_score >= 85 &&
    state.flow_score >= 75
  ) {
    console.log("🎯 High confidence — stopping Auto Assist");
    AUTO_CYCLE_COUNT = 0;
    renderPublishReadyState(state);
    renderNextActionButton(state);
    updateSmartStatus();
    return state;
  }

  if (allowAutoAssist) {
    const triggerKey = `${getActiveSession()}::${reason}`;

    if (AUTO_ASSIST_RUNNING) {
      console.log("🧠 Auto Assist skipped: already running");
    } else if (LAST_AUTO_ASSIST_TRIGGER === triggerKey) {
      console.log("🧠 Auto Assist skipped: duplicate trigger", triggerKey);
    } else if (state.next !== "publish") {
      AUTO_ASSIST_RUNNING = true;
      LAST_AUTO_ASSIST_TRIGGER = triggerKey;

      console.log("⚡ Auto Assist pipeline starting", { reason });
      document.getElementById("autoAssistActivityList")?.replaceChildren();
      addAutoAssistActivity(
        reason === "export_fix"
          ? "⚡ Fix with AI started"
          : "⚡ Auto Assist started"
      );

      try {
        state = await runAutoAssistPipeline(state);
        addAutoAssistActivity("✅ Auto Assist complete");
      } finally {
        AUTO_ASSIST_RUNNING = false;
      }
    }
  }

  renderPublishReadyState(state);
  renderNextActionButton(state);
  updateSmartStatus();

  console.log("🧠 Creative Engine Run:", reason, state.status);

  return state;
}

function addAutoAssistActivity(message) {
  const feed = document.getElementById("autoAssistActivityFeed");
  const list = document.getElementById("autoAssistActivityList");

  if (!feed || !list) return;

  feed.classList.remove("hidden");

  const item = document.createElement("div");
  item.className = "auto-assist-activity-item";
  item.textContent = message;

  list.appendChild(item);
}

function showAutoAssistUpdate(message) {
  const el = document.getElementById("editSmartStatus");
  if (!el) return;

  el.classList.remove("hidden");
  el.textContent = message;
}

const CREATIVE_ACTIONS = {

  reorder_storyboard: async () => {

  addAutoAssistActivity(
    "🎬 Optimizing clip order"
  );

  await autoApplySuggestedStoryboardOrder();

  addAutoAssistActivity(
    "✅ Storyboard reordered"
  );
},

  improve_hook: async () => {
  console.log("CREATIVE_ACTIONS improve_hook fired");
  console.log("selected hook before action:", window.appState?.hook?.selected);

  addAutoAssistActivity("🎣 Looking for the strongest hook");

  let hook = window.appState?.hook?.selected || null;

  if (!hook) {
    hook = getAutoAssistHookCandidate();
  }

  if (!hook) {
    console.log("🧠 Auto Assist stopped: no hook available");
    addAutoAssistActivity("⚠️ No hook available to improve");
    return;
  }

  addAutoAssistActivity(`✅ Selected hook: ${hook}`);

  const beforeScore =
    window.appState?.scores?.hook ??
    LAST_HOOK_SCORE ??
    null;

  await selectHook(hook, false);

  addAutoAssistActivity("⚡ Improving hook");

  await autoBoostSelectedHook();

  await refreshAfterChange({
    hooks: true,
    flow: true,
    director: true,
    publish: true,
    progress: true,
    guidance: true
  });

  if (window.appState?.hook?.lastGenerated?.length) {
    renderHookLab(window.appState.hook.lastGenerated);
    updateHookLabGuidance();
  }

  const afterScore =
    window.appState?.scores?.hook ??
    LAST_HOOK_SCORE ??
    null;

  if (beforeScore != null && afterScore != null) {
    addAutoAssistActivity(`✅ Hook score ${beforeScore} → ${afterScore}`);
  } else if (afterScore != null) {
    addAutoAssistActivity(`✅ Hook score now ${afterScore}/100`);
  }
},

  improve_flow: async () => {
  addAutoAssistActivity("🎬 Improving story flow");
  await improveHooksAndCaptionsFlow();

  const flowScore =
    window.appState?.scores?.storyFlow ??
    LAST_FLOW_SCORE ??
    null;

  if (flowScore != null) {
    addAutoAssistActivity(`✅ Story flow now ${flowScore}/100`);
  }
},

  write_captions: async () => {
    await regenerateCaptionsFromClips();
  },

  publish: async () => {
    document.getElementById("exportBtn")?.click();
  },

  polish: async () => {
    await autoBoostSelectedHook();
  }
};

async function loadContentContext() {
  try {
    const data = await getConfigCached();
    const context = data?.config?.content_context || "auto";

    const select = document.getElementById("contentContext");
    if (select) select.value = context;
  } catch (err) {
    console.warn("Failed to load content context", err);
  }
}

function isAutoAssistEnabled() {
  return document.getElementById("autoAssistToggle")?.checked;
}

async function loadAutoAssistSetting() {
  try {
    const data = await getConfigCached(true);
    const enabled = !!data?.config?.settings?.auto_assist;

    window.appState = window.appState || {};
    window.appState.settings = window.appState.settings || {};
    window.appState.settings.autoAssist = enabled;

    const toggle = document.getElementById("autoAssistToggle");
    if (toggle) {
      toggle.checked = enabled;
    }

    console.log("🧠 Auto Assist loaded:", enabled);

    AUTO_ASSIST_INITIALIZING = false; // ✅ unlock listener

  } catch (err) {
    console.warn("Failed to load auto assist setting", err);
  }
}

async function saveAutoAssistSetting(enabled) {
  try {
    await jsonFetch("/api/auto_assist", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        enabled: !!enabled
      })
    });

    CONFIG_CACHE = null;

    window.appState = window.appState || {};
    window.appState.settings = window.appState.settings || {};
    window.appState.settings.autoAssist = !!enabled;

    console.log("🧠 Auto Assist saved:", enabled);
  } catch (err) {
    console.error("Failed to save auto assist setting", err);
    throw err;
  }
}

let autoAssistInitialized = false;

async function initAutoAssist() {
  if (autoAssistInitialized) return;
  autoAssistInitialized = true;

  // -----------------------------
  // Load initial setting
  // -----------------------------
  await loadAutoAssistSetting();

  // -----------------------------
  // Toggle listener
  // -----------------------------
  const autoAssistToggleEl = document.getElementById("autoAssistToggle");

  autoAssistToggleEl?.addEventListener("change", async (e) => {
    if (AUTO_ASSIST_INITIALIZING) return;

    const enabled = e.target.checked === true;

    try {
      await saveAutoAssistSetting(enabled);

      if (!enabled) {
        AUTO_ASSIST_RUNNING = false;
        LAST_AUTO_ASSIST_TRIGGER = null;
      }

      showAutoAssistUpdate(
        enabled
          ? "🧠 Auto Assist enabled"
          : "🧠 Auto Assist disabled"
      );
    } catch (err) {
      e.target.checked = !enabled;
      console.error(err);
    }
  });

  // -----------------------------
  // Export fix button
  // -----------------------------
  document.getElementById("exportFixBtn")
    ?.addEventListener("click", async () => {
      await runCreativeEngine("export_fix");
      await refreshAfterChange();
    });

  // -----------------------------
  // Next action buttons (delegation)
  // -----------------------------
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".ai-next-action-btn");
    if (!btn) return;

    const action = btn.dataset.action;
    const fn = CREATIVE_ACTIONS[action];
    if (!fn) return;

    btn.disabled = true;
    btn.textContent = "AI Working…";

    try {
      await fn();
      await refreshAfterChange();
    } catch (err) {
      console.error("AI action failed", err);
    }

    btn.disabled = false;
  });
}
