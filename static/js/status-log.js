
let statusLogListenersInitialized = false;
// ================================
  // Status log polling
  // ================================
  let statusLogTimer = null;

  async function refreshStatusLog() {
    try {
      const data = await jsonFetch("/api/status");
      const log = data.status_log || [];
      const el = document.getElementById("statusLog");
      const autoScroll = document.getElementById("autoScrollLogs")?.checked;

      if (!el) return;

      const wasAtBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < 40;

      el.textContent = log.join("\n");

      // ✅ Only scroll if:
      // - Auto-scroll is ON
      // - User was already near the bottom
      if (autoScroll && wasAtBottom) {
        el.scrollTop = el.scrollHeight;
      }
    } catch {
      // silent
    }
  }


  function startStatusLogPolling() {
    if (statusLogTimer) clearInterval(statusLogTimer);
    refreshStatusLog();
    statusLogTimer = setInterval(refreshStatusLog, 2000);
  }

  function stopStatusLogPolling() {
    if (statusLogTimer) {
        clearInterval(statusLogTimer);
        statusLogTimer = null;
    }
}



// ================================
// Live Log: disable auto-scroll on user interaction
// ================================
function initStatusLogListeners() {
  if (statusLogListenersInitialized) return;
  statusLogListenersInitialized = true;

  initStepper();
  startStatusLogPolling();

  const logEl = document.getElementById("statusLog");
  const autoScrollToggle = document.getElementById("autoScrollLogs");

  if (logEl && autoScrollToggle) {
    const disableAutoScroll = () => {
      autoScrollToggle.checked = false;
    };

    logEl.addEventListener("wheel", disableAutoScroll);
    logEl.addEventListener("mousedown", disableAutoScroll);
    logEl.addEventListener("touchstart", disableAutoScroll);
  }

  window.addEventListener("beforeunload", stopStatusLogPolling);
}