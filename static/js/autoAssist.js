function shouldAutoAssistForReason(reason) {
  return reason === "storyboard_complete" || reason === "manual_auto_assist";
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

      try {
        state = await runAutoAssistPipeline(state);
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

function showAutoAssistUpdate(message) {
  const el = document.getElementById("editSmartStatus");
  if (!el) return;

  el.classList.remove("hidden");
  el.textContent = message;
}

const CREATIVE_ACTIONS = {
  improve_hook: async () => {
  console.log("CREATIVE_ACTIONS improve_hook fired");
  console.log("selected hook before action:", window.appState?.hook?.selected);

  let hook = window.appState?.hook?.selected || null;

  if (!hook) {
    hook = getAutoAssistHookCandidate();

    if (hook) {
      window.appState.hook.selected = hook;
      window.appState.hook.locked = true;
      console.log("🧠 Auto Assist selected hook:", hook);
      updateHookLockUI?.();
      updateHookLabGuidance?.();
    }
  }

  if (window.appState?.hook?.selected) {
    console.log("path = autoBoostSelectedHook");
    await autoBoostSelectedHook();
  } else {
    console.log("🧠 Auto Assist stopped: no hook available");
  }
},

  improve_flow: async () => {
    await improveHooksAndCaptionsFlow();
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