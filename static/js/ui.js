// Auto-fading status helper
  let _statusTimers = {};
  let uiInitialized = false;

  function setStatus(id, msg, type = "info", autoHide = true) {
    const el = document.getElementById(id);
    if (!el) return; // ✅ correct place

    el.className = "status-text status-" + type;
    el.textContent = msg;

    if (_statusTimers[id]) {
      clearTimeout(_statusTimers[id]);
      delete _statusTimers[id];
    }

    if (!autoHide) return;

    _statusTimers[id] = setTimeout(() => {
      el.textContent = "";
      el.className = "status-text status-info";
      delete _statusTimers[id];
    }, 5000);
  }

   function toast(message, duration = 2500) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;

    document.body.appendChild(el);

    requestAnimationFrame(() => el.classList.add("show"));

    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 300);
    }, duration);
  }

    function flashElement(el) {
    if (!el) return;
    el.classList.remove("flash");
    void el.offsetWidth; // force reflow
    el.classList.add("flash");
  }

  function setCaptionSource(type, text, noChange = false) {
    const el = document.getElementById("captionStatus");
    if (!el) return;

    el.textContent = text;

    el.className = "caption-source"; // reset

    if (type === "yaml") el.classList.add("source-yaml");
    if (type === "filenames") el.classList.add("source-filenames");

    if (noChange) {
      el.classList.add("no-change");
      el.textContent += " · No changes";
    }
  }

  function setUiBusy(busy) {
    document.body.classList.toggle("ui-busy", busy);
  }


    // ================================
  // Stepper behavior
  // ================================
  function initStepper() {
      const stepButtons = document.querySelectorAll(".stepper .step");

      stepButtons.forEach((btn) => {
          btn.addEventListener("click", () => {
              const targetSel = btn.dataset.target;
              const targetEl = document.querySelector(targetSel);
              if (targetEl) {
                  targetEl.scrollIntoView({
                      behavior: "smooth",
                      block: "start",
                  });
              }
              stepButtons.forEach((b) => b.classList.remove("active"));
              btn.classList.add("active");
          });
      });

      const steps = Array.from(document.querySelectorAll(".step-card"));
      if (!steps.length) return;

      const observer = new IntersectionObserver(
      (entries) => {
          entries.forEach((entry) => {
              if (!entry.isIntersecting) return;

              const id = "#" + entry.target.id;

              if (id === "#step-4" && typeof refreshAfterChange === "function") {
                refreshAfterChange();
              }




              stepButtons.forEach((btn) => {
                  if (btn.dataset.target === id) {
                      stepButtons.forEach((b) => b.classList.remove("active"));
                      btn.classList.add("active");
                  }
              });
          });
      },
      { threshold: 0.4 }
  );


      steps.forEach((s) => observer.observe(s));
  }

  function toggleVariantsPanel(forceClose = false) {
  const drawer = document.getElementById("variantsDrawer");
  if (!drawer) return;

  if (forceClose) return setVariantsDrawerOpen(false);

  const shouldOpen = drawer.classList.contains("closed");
  setVariantsDrawerOpen(shouldOpen);

}



function setVariantsDrawerOpen(isOpen) {
  const drawer = document.getElementById("variantsDrawer");
  const btn = document.getElementById("variantsToggleBtn");
  if (!drawer) return;

  drawer.classList.toggle("closed", !isOpen);
  if (btn) btn.textContent = isOpen ? "Collapse" : "Expand";
}

  function highlightHookLab() {
    const lab = document.getElementById("hookLab");
    if (!lab) return;

    lab.classList.remove("hook-lab-highlight"); // reset
    void lab.offsetWidth;                       // force reflow
    lab.classList.add("hook-lab-highlight");

    // Remove class after animation finishes
    setTimeout(() => {
      lab.classList.remove("hook-lab-highlight");
    }, 2000);
  }

    function showAutoSaveStatus(id, message = "Saved ✓", timeout = 1500) {
      const el = document.getElementById(id);
      if (!el) return;

      el.textContent = message;
      el.className = "status-text status-success subtle";

      clearTimeout(el._hideTimer);
      el._hideTimer = setTimeout(() => {
          el.textContent = "";
      }, timeout);
  }

    function setCaptionInlineStatus(text, type = "info") {
    const el = document.getElementById("captionInlineStatus");
    if (!el) return;

    el.textContent = text;
    el.className = `caption-inline-status ${type}`;
    el.classList.remove("hidden");

      // Auto-hide after short delay
    setTimeout(() => {
      el.classList.add("hidden");
    }, 2200);

  }

    // Status hint helper (bottom style line)
  function showStatus(msg, type = "info") {
      const el = document.getElementById("styleStatus");
      if (!el) return;
      el.textContent = msg;
      el.className = "hint-text " + type;
  }

    function toggleUploadManager() {
      const content = document.getElementById("uploadManagerContent");
      const icon = document.getElementById("uploadManagerToggle");
      if (!content || !icon) return;

      content.classList.toggle("collapsed");

      if (content.classList.contains("collapsed")) {
          icon.textContent = "▲";
      } else {
          icon.textContent = "▼";
      }
  }

    // ================================
  // Mobile Session Panel Toggle
  // ================================
  function toggleMobileSessionPanel() {
    const panel = document.getElementById("sidebarSessionCard");
    const btn = document.getElementById("mobileSessionBtn");
    if (!panel || !btn) return;

    const isOpen = panel.classList.toggle("open");
    console.log("[MOBILE] toggle session panel", { isOpen, panel });

    document.body.classList.toggle("no-scroll", isOpen);
    btn.textContent = isOpen ? "Close Sessions" : "Sessions";
  }

    function activateStep(stepSelector) {
    document.querySelectorAll(".step").forEach(btn => {
      btn.classList.toggle(
        "active",
        btn.dataset.target === stepSelector
      );
    });

    const stepCard = document.querySelector(stepSelector);
    stepCard?.classList.add("step-active");
  }

    function scrollToStep(stepSelector) {
    const el = document.querySelector(stepSelector);
    if (!el) return;

    el.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });

    // Optional: sync stepper UI if you already do this elsewhere
    document.querySelectorAll(".step").forEach(btn => {
      btn.classList.toggle(
        "active",
        btn.dataset.target === stepSelector
      );
    });
  }

    function animateSessionGlow() {
    const tags = document.querySelectorAll(".active-session-tag");

    if (!tags.length) {
      console.warn("[SESSION] No active-session-tag found");
      return;
    }

    tags.forEach(tag => {
      tag.classList.remove("session-glow");
      void tag.offsetWidth; // force reflow
      tag.classList.add("session-glow");
    });
  }


    function pulseExportButton() {
    const btn = document.getElementById("exportBtn");
    if (!btn) return;

    btn.classList.add("publish-glow");

    setTimeout(() => {
      btn.classList.remove("publish-glow");
    }, 4000);
  }

    function animateScoreJump(type) {
    const id = type === "hook"
      ? "hookScoreValue"
      : "storyFlowScoreValue";

    const el = document.getElementById(id);
    if (!el) return;

    el.classList.remove("score-burst");
    void el.offsetWidth;
    el.classList.add("score-burst");
  }

    function syncIntentPills(intent) {
    document.querySelectorAll(".intent-pills .pill").forEach(pill => {
      pill.classList.toggle("active", pill.dataset.intent === intent);
    });
  }

  function syncCaptionToggleUI() {
  const orig = document.getElementById("showOriginal");
  const rew = document.getElementById("showRewritten");
  const diff = document.getElementById("showDiff");

  [orig, rew, diff].forEach(b => b?.classList.remove("active"));

  if (captionViewMode === "original") orig?.classList.add("active");
  if (captionViewMode === "rewritten") rew?.classList.add("active");
  if (captionViewMode === "diff") diff?.classList.add("active");
}

function toggleHookDetails() {
  const body = document.getElementById("hookDetailsBody");
  if (!body) return;

  body.classList.toggle("collapsed");
}

function toggleStoryDetails() {
  const body = document.getElementById("storyDetailsBody");
  if (!body) return;

  body.classList.toggle("collapsed");
}

// ================================
// Display / Label Helpers
// ================================

function prettyArea(area) {
  return {
    hook: "🎣 Hook",
    pacing: "⏱ Pacing",
    cta: "📢 CTA",
    overlay: "✨ Overlay",
    captions: "💬 Captions"
  }[area] || area;
}

function impactLabel(level) {
  return {
    high: "Fix now",
    medium: "Recommended",
    low: "Suggestion"
  }[level] || "";
}

// ================================
// Feedback / Celebration
// ================================

function showPublishBanner() {
  const messages = [
    "🚀 This one is ready to post.",
    "🔥 Strong hook. Clean flow.",
    "💎 Your audience will watch this.",
    "🎯 AI approves this edit.",
    "✨ Send it."
  ];

  const msg = messages[Math.floor(Math.random() * messages.length)];

  if (typeof toast === "function") toast(msg);

  maybeConfetti?.(); // optional
}

function addStepEnterHandler(stepNumber, callback) {
  const stepCard = document.querySelector(`.step-card:nth-of-type(${stepNumber})`);
  if (!stepCard) return;

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        callback();
        obs.disconnect(); // fire once only
      }
    });
  }, { threshold: 0.4 });

  observer.observe(stepCard);
}

function sidebarToast(msg) {
      const area = document.getElementById("sidebarSessionToastArea");
      if (!area) return;

      const div = document.createElement("div");
      div.className = "sidebar-toast";
      div.textContent = msg;

      area.appendChild(div);
      setTimeout(() => div.classList.add("fade-out"), 1300);
      setTimeout(() => div.remove(), 1600);
  }

function showSessionToast(msg) {
      const area = document.getElementById("sessionToastArea");
      if (!area) return;

      const el = document.createElement("div");
      el.className = "session-toast";
      el.textContent = msg;

      area.appendChild(el);

      setTimeout(() => {
          el.classList.add("fade-out");
          setTimeout(() => el.remove(), 500);
      }, 1300);
  }

function initUIListeners() {
  if (uiInitialized) return;
  uiInitialized = true;
    

  // Director panel
  const directorPanel = document.getElementById("editStrategyPanel");
  const toggleDirectorBtn = document.getElementById("toggleDirectorBtn");

  if (directorPanel) {
    directorPanel.classList.add("collapsed");
  }

  if (toggleDirectorBtn && directorPanel) {
    toggleDirectorBtn.addEventListener("click", (e) => {
      e.preventDefault();
      directorPanel.classList.toggle("collapsed");

      toggleDirectorBtn.textContent = directorPanel.classList.contains("collapsed")
        ? "🧠 AI Director"
        : "🧠 Hide Director";
    });
  }

  // Export scroll
  document.getElementById("exportAnywayBtn")
    ?.addEventListener("click", () => {
      document.getElementById("exportBtn")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
    });

  // Accordion
  document.querySelectorAll(".acc-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sec = btn.parentElement;
      sec.classList.toggle("open");
    });
  });

  // Session glow
  setTimeout(animateSessionGlow, 300);
}
