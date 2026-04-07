function getActiveSession() {
    if (!ACTIVE_SESSION) {
      console.warn("[SESSION] ACTIVE_SESSION unset, forcing default");
      ACTIVE_SESSION = "default";
    }
    return ACTIVE_SESSION;
  }



  async function setActiveSession(name) {
    const safe = sanitizeSessionName(name);
    
    ACTIVE_SESSION = safe;
    CONFIG_CACHE = null; // 🔥 ADD THIS

    const uploadStatusEl = document.getElementById("uploadStatus");
    if (uploadStatusEl) {
      uploadStatusEl.textContent = "";
      uploadStatusEl.className = "status-text status-info";
    }

    const uploadPreviewEl = document.getElementById("uploadPreview");
    if (uploadPreviewEl) {
      uploadPreviewEl.innerHTML = "";
    }

document.getElementById("reselectPendingUploadsBtn")?.classList.add("hidden");

    if (UPLOAD_IN_PROGRESS) {
      toast("Finish or cancel the current upload before switching sessions.");
      return;
    }

    // Reset session-dependent state
    LAST_HOOK_SCORE = null;
    LAST_FLOW_SCORE = null;
    LAST_AUTO_ASSIST_TRIGGER = null;
    AUTO_ASSIST_RUNNING = false;

  const hookEl = document.getElementById("hookScoreValue");
    if (hookEl) hookEl.textContent = "—";

    const flowEl = document.getElementById("storyFlowScoreValue");
  if (flowEl) flowEl.textContent = "—";

    window.appState.hook.selected = null;
    window.appState.hook.locked = false;
    window.appState.hook.lastGenerated = null;

    window.appState.variants.list = [];

    // ----------------------------
    // Reset AI apply / undo state
    // ----------------------------
    window.aiUndoSnapshot = null;

    document
      .getElementById("undoAiRecommendationBtn")
      ?.classList.add("hidden");

    // ----------------------------
    // Reset per-session frontend state
    // ----------------------------
    workingClipOrder = [];
    clipOrderDirty = false;

    // 🔥 VARIANTS RESET (you were missing this)
    lastVariantStatus = null;
    VARIANT_POLL_ACTIVE = false;
    window.appState.variants.list = [];
    updateVariantRunningBadge("idle");

    // 🔥 ANALYSIS badge reset (safe default)
    updateAnalyzingBadge?.("idle");

    // ----------------------------
    // Persist + sync session UI
    // ----------------------------
    updateSessionLabels();
    sidebarSyncActiveLabel();
    localStorage.setItem("activeSession", ACTIVE_SESSION);

    await loadAutoAssistSetting();


    // ----------------------------
    // Load core state
    // ----------------------------
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();
    updateCaptionBaselineHint();
    updateLoadYamlVisibility();
    await refreshAfterChange();

    // ----------------------------
    // Secondary refreshes
    // ----------------------------
    loadUploadManager();
    refreshAnalyses();
    loadSessionDropdown();
    loadSessions();
    sidebarLoadSessions();

    requestAnimationFrame(() =>
      requestAnimationFrame(animateSessionGlow)
    );

    // ----------------------------
    // Resume analysis polling ONLY if needed
    // ----------------------------
    pollAnalyzeStatus();

    // ----------------------------
    // AI readiness summary
    // ----------------------------
    loadAISetupSummary();

    const restoredPending = loadPendingUploadState(getActiveSession());
    if (restoredPending.length) {
      renderPendingUploadGhosts(restoredPending);
      setStatus(
        "uploadStatus",
        "⚠ Pending upload restored. Re-select the same file(s) and click Upload to retry.",
        "warning",
        false
      );
    } else {
      setStatus("uploadStatus", "", "info", false);
}
  }


function sanitizeSessionName(raw) {
      let s = (raw || "").toLowerCase().trim();

      try {
          s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
      } catch {
          // ignore
      }

      s = s.replace(/[^a-z0-9]+/g, "_");
      s = s.replace(/^_+|_+$/g, "");

      if (!s) s = "default";
      return s;
  }


  // OLD session list (if legacy card exists)
async function loadSessions() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();

        const list = document.getElementById("sessionList");
        if (!list) return;

        list.innerHTML = "";

        (data.sessions || []).forEach((session) => {
            const li = document.createElement("li");
            li.className = "analysis-item";
            li.innerHTML = `
                <span class="analysis-file">${session}</span>
                <button class="btn renameSessionBtn" data-session="${session}">
                    ✏️ Rename
                </button>
                <button class="btn btn-delete deleteSessionBtn" data-session="${session}">
                    🗑 Delete
                </button>
            `;
            list.appendChild(li);
        });
    } catch (err) {
        console.error("[SESSION] loadSessions failed:", err);
    }
}

// Populate quick-switch session dropdown (legacy)
async function loadSessionDropdown() {
    try {
        const res = await fetch("/api/sessions");
        const data = await res.json();

        const ddl = document.getElementById("sessionDropdown");
        if (!ddl) return;

        ddl.innerHTML = "";

        const sessions = data.sessions || [];

        if (sessions.length === 0) {
            ddl.innerHTML = `<option value="">(no sessions found)</option>`;
            return;
        }

        sessions.forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s;
            opt.textContent = s;
            ddl.appendChild(opt);
        });

        ddl.value = getActiveSession();

        ddl.classList.add("force-restyle");
        setTimeout(() => ddl.classList.remove("force-restyle"), 0);
    } catch (err) {
        console.error("[SESSION] dropdown load failed:", err);
    }
}

async function renameSession(oldSession) {

    if (getActiveSession() === "default") {
      alert("Default session cannot be renamed.");
      return;
    }
    
    const raw = prompt(`Rename session "${oldSession}" to:`);

    if (!raw) return;

    const newSession = sanitizeSessionName(raw);

    if (!newSession) {
        alert("Invalid session name");
        return;
    }

    if (newSession === oldSession) {
        alert("New session name must be different");
        return;
    }

    try {
        const res = await jsonFetch("/api/session/rename", {
            method: "POST",
            body: JSON.stringify({
                old_session: oldSession,
                new_session: newSession
            })
        });

        if (getActiveSession() === oldSession) {
            await setActiveSession(newSession);
        }

        await loadSessions();
        await loadSessionDropdown();
        await sidebarLoadSessions();
        sidebarSyncActiveLabel();

        showSessionToast?.(`Renamed "${oldSession}" → "${newSession}"`);
    } catch (err) {
        console.error("[SESSION] renameSession failed:", err);
        alert(err?.message || "Failed to rename session");
    }
}

async function deleteSession(session) {
    if (!confirm(`Delete session '${session}' including all its videos?`)) return;

    try {
        await fetch(`/api/session/${encodeURIComponent(session)}`, {
            method: "DELETE",
        });

        if (getActiveSession() === session) {
            setActiveSession("default");
        }

        loadSessions();
        loadSessionDropdown();
        sidebarLoadSessions();
        sidebarSyncActiveLabel();
    } catch (err) {
        console.error("[SESSION] deleteSession failed:", err);
    }
}

function updateSessionLabels() {
      const labels = document.querySelectorAll(".sessionLabel");
      labels.forEach((l) => (l.textContent = getActiveSession()));
  }

  
function updateSessionTags() {
      document.querySelectorAll("#currentSessionTag").forEach((el) => {
          el.textContent = getActiveSession();
      });
  }

  

function sessionQS() {
    const s = getActiveSession();
    console.log("[API] Using session:", s);
    return "?session=" + encodeURIComponent(s);
  }

  async function sidebarLoadSessions() {
      try {
          const res = await fetch("/api/sessions");
          const data = await res.json();

          const ddl = document.getElementById("sidebarSessionDropdown");
          if (!ddl) return;

          ddl.innerHTML = "";

          (data.sessions || []).forEach((s) => {
              const opt = document.createElement("option");
              opt.value = s;
              opt.textContent = s;
              ddl.appendChild(opt);
          });

          ddl.value = getActiveSession();
      } catch (err) {
          console.error("Failed loading sessions:", err);
      }
  }

  function sidebarSyncActiveLabel() {
      const el = document.getElementById("sidebarActiveSession");
      if (!el) return;
      el.textContent = getActiveSession();
  }

  async function initSessionBoot() {

    // 🔥 MUST BE FIRST — before ANY fetch
  try {
    const stored = localStorage.getItem("activeSession");
    ACTIVE_SESSION = sanitizeSessionName(stored || "default");
  } catch {
    ACTIVE_SESSION = "default";
  }

  console.log("[SESSION INIT]", ACTIVE_SESSION);

  // 🔥 LOAD SESSIONS EARLY (before any async work can block it)
  await loadSessions();
  await loadSessionDropdown();
  await sidebarLoadSessions();
  sidebarSyncActiveLabel();
  // Sync labels
updateSessionLabels();
sidebarSyncActiveLabel();

  }

  function initSessionListeners() {
    // SIDEBAR SESSION BUTTONS
    document.getElementById("sidebarCreateBtn")?.addEventListener("click", async () => {
        const input = document.getElementById("sidebarNewSessionInput");
        if (!input) return;

        const raw = input.value.trim();
        if (!raw) {
            input.classList.add("shake");
            setTimeout(() => input.classList.remove("shake"), 300);
            return;
        }

        const safe = sanitizeSessionName(raw);

        // 👇 Create session on backend
        await fetch(`/api/session/${safe}`, { method: "POST" });

        // 👇 Sync UI immediately
        setActiveSession(safe);
        await sidebarLoadSessions();
        await loadSessionDropdown();
        sidebarSyncActiveLabel();

        sidebarToast(`Created & switched to “${safe}”`);
        input.value = "";
    });


    document.getElementById("sidebarSwitchBtn")?.addEventListener("click", () => {
        const ddl = document.getElementById("sidebarSessionDropdown");
        if (!ddl || !ddl.value) return;

        setActiveSession(ddl.value);
        sidebarSyncActiveLabel();
        sidebarToast(`Switched to “${ddl.value}”`);
    });

    

  document.getElementById("sidebarRenameBtn")
  ?.addEventListener("click", () => {
      renameSession(getActiveSession());
  });



    document.getElementById("sidebarDeleteBtn")?.addEventListener("click", async () => {
        const session = getActiveSession();

        if (session === "default") {
            sidebarToast("Cannot delete default");
            return;
        }

        const ok = confirm(
            `Delete session "${session}"?\n\nThis deletes ALL videos, config.yml, and analysis for that session.`
        );
        if (!ok) {
            sidebarToast("Deletion cancelled");
            return;
        }

        await fetch(`/api/session/${session}`, { method: "DELETE" });

        setActiveSession("default");
        sidebarLoadSessions();
        sidebarSyncActiveLabel();

        sidebarToast(`Deleted session “${session}”`);
    });

    document.addEventListener("click", (e) => {
    const btn = e.target.closest(".renameSessionBtn");
    if (!btn) return;

    const session = btn.dataset.session;
    if (session) renameSession(session);
});

    // Legacy delete buttons in other card (if present)
    document.addEventListener("click", (e) => {
        const btn = e.target.closest(".deleteSessionBtn");
        if (!btn) return;
        const session = btn.dataset.session;
        if (session) deleteSession(session);
    });

    // Legacy top session bar hooks (safe no-op if missing)
    const headerLabel = document.getElementById("activeSessionLabel");
    const headerInput = document.getElementById("sessionInput");
    if (headerLabel) headerLabel.textContent = getActiveSession();
    if (headerInput) headerInput.value = getActiveSession();

    document.getElementById("setSessionBtn")?.addEventListener("click", () => {
        const val = document.getElementById("sessionInput")?.value || "";
        setActiveSession(val);
        loadSessionDropdown();
    });

    document.getElementById("refreshSessionsBtn")?.addEventListener("click", loadSessions);

    document
  .getElementById("undoAiRecommendationBtn")
  ?.addEventListener("click", undoAIRecommendation);

// Legacy quick-switch for sessions (top bar)
document.getElementById("switchSessionBtn")?.addEventListener("click", () => {
    const ddl = document.getElementById("sessionDropdown");
    if (!ddl) return;

    const selected = ddl.value || "default";

    setActiveSession(selected);
    loadSessionDropdown();

    const label = document.getElementById("activeSessionLabel");
    if (label) {
        label.classList.add("session-pulse");
        setTimeout(() => label.classList.remove("session-pulse"), 800);
    }

    const ddlWrapper = ddl.closest(".select-wrapper");
    if (ddlWrapper) {
        ddlWrapper.classList.add("session-ddl-pulse");
        setTimeout(
            () => ddlWrapper.classList.remove("session-ddl-pulse"),
            600
        );
    }

    showSessionToast(`Switched to “${selected}”`);
});

// ================================
// Mobile Session Panel toggle
// ================================

document.getElementById("mobileSessionBtn")?.addEventListener("click", toggleMobileSessionPanel);

document.getElementById("mobileCloseSessionBtn")?.addEventListener("click", toggleMobileSessionPanel);

  }



  



