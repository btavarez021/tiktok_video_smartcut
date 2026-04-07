// ================================
// Step 5: EXPORT (Async)
// ================================
async function exportVideo() {
    const btn = document.getElementById("exportBtn");
    const cancelBtn = document.getElementById("cancelExportBtn");
    const statusEl = document.getElementById("exportStatus");

    // 🧼 Clear old download button immediately
    const box = document.getElementById("downloadArea");
    if (box) box.innerHTML = "";

    btn.disabled = true;
    cancelBtn.classList.remove("hidden");
    statusEl.textContent = "⏳ Rendering… you can leave this page.";

    try {
    const startResp = await jsonFetch("/api/export/start", {
        method: "POST",
        body: JSON.stringify({ session: getActiveSession() })
    });

    const taskId = startResp.task_id;
    ACTIVE_EXPORT_TASK = taskId;

    const downloadUrl = await pollExportStatus(taskId);

    cancelBtn.classList.add("hidden");

    // ❗ Remove the Download Video <a> link
    // OLD:
    // statusEl.innerHTML = `
    //    ✅ Export complete<br>
    //    <a href="${downloadUrl}" target="_blank">Download Video</a>
    // `;

    // NEW:
    // statusEl.textContent = "✅ Export complete";
    setStatus("exportStatus", "Export complete ✓", "success");


    // 🔥 Show your nice styled button
    showDownloadButton(downloadUrl);

} catch (err) {
    statusEl.textContent = "❌ " + err;
} finally {
    btn.disabled = false;
    ACTIVE_EXPORT_TASK = null;
    cancelBtn.classList.add("hidden");
}
}


async function pollExportStatus(taskId) {
    return new Promise((resolve, reject) => {
        const interval = setInterval(async () => {

            const res = await fetch(`/api/export/status?task_id=${taskId}`);
            const data = await res.json();

            const statusEl = document.getElementById("exportStatus");
            const cancelBtn = document.getElementById("cancelExportBtn");
            const exportBtn = document.getElementById("exportBtn");

            // ------------------------------
            // SUCCESS
            // ------------------------------
            if (data.status === "done") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) {
                  box.innerHTML = `
                      <a id="downloadLink"
                        href="${data.download_url}"
                        class="btn-download"
                        target="_blank"
                        download>
                          ⬇️ Download Export
                      </a>
                  `;
              }

              resolve(data.download_url);
              return;
          }


            // ------------------------------
            // CANCELLED
            // ------------------------------
            if (data.status === "cancelled") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) box.innerHTML = "";

              statusEl.textContent = "";
              statusEl.className = "status-text";

              reject("cancelled");
              return;
          }


            // ------------------------------
            // ERROR
            // ------------------------------
            if (data.status === "error") {
              clearInterval(interval);

              cancelBtn.classList.add("hidden");
              if (exportBtn) exportBtn.disabled = false;

              const box = document.getElementById("downloadArea");
              if (box) box.innerHTML = "";

              statusEl.textContent = "Export failed.";
              statusEl.className = "status-text error";

              reject(data.error || "error");
              return;
          }



        }, 1500); // slightly faster polling = snappier UI
    });
}

function showDownloadButton(url) {
    const area = document.getElementById("downloadArea");
    area.innerHTML = `
        <button class="btn-download" onclick="window.open('${url}', '_blank')">
            ⬇️ Download Video
        </button>
    `;
}

  function disableDownloadButton() {
      const btn = document.getElementById("downloadLink");
      if (!btn) return;

      btn.classList.add("disabled");
      btn.textContent = "Exporting…";
      btn.removeAttribute("href");   // remove old link
  }

   // Simple download helper
  function safeDownload(url, filename = "export.mp4") {
      const a = document.createElement("a");
      a.href = url;
      a.style.display = "none";
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
  }

let exportInitialized = false;

function initExportListeners() {
  if (exportInitialized) return;
  exportInitialized = true;

  const exportBtn = document.getElementById("exportBtn");
  const cancelBtn = document.getElementById("cancelExportBtn");

  exportBtn?.addEventListener("click", exportVideo);

  cancelBtn?.addEventListener("click", async () => {
    if (!ACTIVE_EXPORT_TASK) return;

    const statusEl = document.getElementById("exportStatus");

    statusEl.textContent = "⛔ Canceling export…";

    await jsonFetch("/api/export/cancel", {
      method: "POST",
      body: JSON.stringify({ task_id: ACTIVE_EXPORT_TASK })
    });

    // Let poller handle UI cleanup
    ACTIVE_EXPORT_TASK = null;
  });
}