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
let AUTO_CYCLE_COUNT = 0;
let AUTO_ASSIST_RUNNING = false;
let AUTO_ASSIST_INITIALIZING = true;
let LAST_AUTO_ASSIST_TRIGGER = null;
let AUTO_ASSIST_PENDING_SOURCE = null;

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
let UPLOAD_IN_PROGRESS = false;
let CURRENT_UPLOAD_XHR = null;
let pendingUploadFiles = [];
let STORYBOARD_CONTINUE_RUNNING = false;

// =======================================
// GLOBAL APP STATE (Single Source of Truth)
// =======================================

window.appState = {
  ...(window.appState || {}),

  session: window.appState?.session || null,
  contentMode: window.appState?.contentMode || "caption",

  hook: {
    ...(window.appState?.hook || {}),
    selected: window.appState?.hook?.selected || null,
    intent: window.appState?.hook?.intent || "discovery",
    locked: window.appState?.hook?.locked || false,
    lastGenerated: window.appState?.hook?.lastGenerated || []
  },

  variants: {
    ...(window.appState?.variants || {}),
    modes: window.appState?.variants?.modes || {},
    list: window.appState?.variants?.list || [],
    recommendedId: window.appState?.variants?.recommendedId || null,
    generating: window.appState?.variants?.generating || false,
    generatedForHook: window.appState?.variants?.generatedForHook || null
  },

  captions: {
    ...(window.appState?.captions || {}),
    baseline: window.appState?.captions?.baseline || "",
    current: window.appState?.captions?.current || "",
    source: window.appState?.captions?.source || "none"
  },

  storyboard: {
    ...(window.appState?.storyboard || {}),
    order: window.appState?.storyboard?.order || []
  },

  scores: {
    ...(window.appState?.scores || {}),
    hook: window.appState?.scores?.hook ?? null,
    storyFlow: window.appState?.scores?.storyFlow ?? null
  },

  ui: {
    ...(window.appState?.ui || {}),
    yamlPolling: window.appState?.ui?.yamlPolling || false
  },

  setup: {
    ...(window.appState?.setup || {}),
    hookConfidence: window.appState?.setup?.hookConfidence || "unknown",
    labelQuality: window.appState?.setup?.labelQuality || "unknown",
    clipCount: window.appState?.setup?.clipCount || 0
  },

  settings: {
    ...(window.appState?.settings || {}),
    autoAssist: window.appState?.settings?.autoAssist ?? false
  }
};

const AUTO_ASSIST_PIPELINE = [
  "improve_hook",
  "improve_flow",
  "polish"
];

async function getConfigCached(force = false) {
  if (CONFIG_CACHE && !force) return CONFIG_CACHE;

  const session = encodeURIComponent(getActiveSession());
  const data = await jsonFetch(`/api/config?session=${session}`);

  CONFIG_CACHE = data;
  return data;
}

function getAppState() {
  return {
    hookScore: LAST_HOOK_SCORE,
    flowScore: LAST_FLOW_SCORE,
    captionsLength: (lastSavedCaptionsText || "").length,
    rewritePending,
    rewriteCommitted,
    hooksReady: !!window.appState?.hook?.lastGenerated?.length,
    clipOrderDirty,
    intent: window.appState?.hook?.intent || "discovery",
    contentMode: window.appState?.contentMode || "caption",
    yamlPolling: YAML_POLL_ACTIVE,
    variantPolling: VARIANT_POLL_ACTIVE,
  };
}

function logAppState(label = "STATE") {
  console.log(`🧠 ${label} →`, getAppState());
}