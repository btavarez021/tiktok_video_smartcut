// static/app.js

// Simple DOM helpers
function $(id) {
  return document.getElementById(id);
}

const spinAnalyze = $("spinAnalyze");
const statusAnalyze = $("statusAnalyze");
const analysisList = $("analysisList");

// Polling control
let analyzeStepTimer = null;
let isStepping = false;

async function callJson(url, options = {}) {
  const resp = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
    },
    ...options,
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return resp.json();
}

// ----------------------
// STATUS POLLING
// ----------------------
async function refreshStatusLog() {
  try {
    const data = await callJson("/api/status");
    if (statusAnalyze) {
      const log = (data.status_log || []).join("\n");
      statusAnalyze.textContent = log || "Idle.";
    }
  } catch (err) {
    console.error("Status error:", err);
  }
}

setInterval(refreshStatusLog, 2000);

// ----------------------
// ANALYSIS
// ----------------------
async function refreshAnalysesList() {
  if (!analysisList) return;
  try {
    const data = await callJson("/api/analyses_cache");
    const entries = Object.entries(data || {});
    if (entries.length === 0) {
      analysisList.innerHTML = "<li>No analyses yet.</li>";
      return;
    }
    analysisList.innerHTML = entries
      .map(
        ([file, desc]) =>
          `<li><strong>${file}</strong>: ${desc ? desc : "(no description)"} </li>`
      )
      .join("");
  } catch (err) {
    console.error("analyses_cache error:", err);
  }
}

async function startAnalysis() {
  if (spinAnalyze) spinAnalyze.classList.add("active");
  if (statusAnalyze) {
    statusAnalyze.textContent = "Starting analysis…";
    statusAnalyze.classList.add("working");
  }

  try {
    const res = await callJson("/api/analyze_start", { method: "POST" });
    console.log("analyze_start:", res);

    if (res.total === 0) {
      if (statusAnalyze) {
        statusAnalyze.textContent = "No videos found in S3 raw_uploads/.";
        statusAnalyze.classList.remove("working");
      }
      if (spinAnalyze) spinAnalyze.classList.remove("active");
      return;
    }

    if (statusAnalyze) {
      statusAnalyze.textContent = `Running analysis… (0 / ${res.total})`;
      statusAnalyze.classList.add("working");
    }

    // Begin step-based polling
    if (analyzeStepTimer) clearInterval(analyzeStepTimer);
    analyzeStepTimer = setInterval(runAnalysisStep, 2500);
  } catch (err) {
    console.error("startAnalysis error:", err);
    if (statusAnalyze) {
      statusAnalyze.textContent = "Error starting analysis.";
      statusAnalyze.classList.remove("working");
      statusAnalyze.classList.add("error");
    }
    if (spinAnalyze) spinAnalyze.classList.remove("active");
  }
}

async function runAnalysisStep() {
  if (isStepping) return;
  isStepping = true;

  try {
    const res = await callJson("/api/analyze_step", { method: "POST" });
    console.log("analyze_step:", res);

    const total = res.total || 0;
    const processed = res.processed || 0;

    if (statusAnalyze) {
      let msg = `Analyzing… (${processed} / ${total})`;
      if (res.last_file) {
        msg += ` | Last: ${res.last_file}`;
      }
      statusAnalyze.textContent = msg;
      statusAnalyze.classList.add("working");
    }

    if (res.status === "done" || res.status === "idle") {
      if (analyzeStepTimer) clearInterval(analyzeStepTimer);
      analyzeStepTimer = null;

      if (statusAnalyze) {
        statusAnalyze.textContent = `✅ Analysis complete (${processed} / ${total})`;
        statusAnalyze.classList.remove("working");
        statusAnalyze.classList.add("success");
      }
      if (spinAnalyze) spinAnalyze.classList.remove("active");

      await refreshAnalysesList();
    } else if (res.status === "error") {
      if (analyzeStepTimer) clearInterval(analyzeStepTimer);
      analyzeStepTimer = null;

      if (statusAnalyze) {
        statusAnalyze.textContent = `❌ Error during analysis: ${res.error || "Unknown error"}`;
        statusAnalyze.classList.remove("working");
        statusAnalyze.classList.add("error");
      }
      if (spinAnalyze) spinAnalyze.classList.remove("active");
    }
  } catch (err) {
    console.error("runAnalysisStep error:", err);
    if (analyzeStepTimer) clearInterval(analyzeStepTimer);
    analyzeStepTimer = null;

    if (statusAnalyze) {
      statusAnalyze.textContent = "❌ Error calling /api/analyze_step";
      statusAnalyze.classList.remove("working");
      statusAnalyze.classList.add("error");
    }
    if (spinAnalyze) spinAnalyze.classList.remove("active");
  } finally {
    isStepping = false;
  }
}

// ----------------------
// YAML CONFIG
// ----------------------
async function generateYaml() {
  const status = $("statusYaml");
  if (status) {
    status.textContent = "Generating YAML from analyses…";
    status.classList.add("working");
  }

  try {
    const cfg = await callJson("/api/generate_yaml", { method: "POST" });
    const yamlTextArea = $("yamlText");
    if (yamlTextArea) {
      yamlTextArea.value = (cfg && Object.keys(cfg).length)
        ? JSON.stringify(cfg, null, 2)
        : "# YAML generated (see config.yml on server)";
    }
    if (status) {
      status.textContent = "YAML generated and saved to config.yml ✅";
      status.classList.remove("working");
      status.classList.add("success");
    }
  } catch (err) {
    console.error("generateYaml error:", err);
    if (status) {
      status.textContent = "Error generating YAML.";
      status.classList.remove("working");
      status.classList.add("error");
    }
  }
}

async function loadConfig() {
  try {
    const data = await callJson("/api/config");
    const yamlTextArea = $("yamlText");
    if (yamlTextArea) {
      yamlTextArea.value = data.yaml || "# No config.yml found yet.";
    }
  } catch (err) {
    console.error("loadConfig error:", err);
  }
}

async function saveYaml() {
  const yamlTextArea = $("yamlText");
  const status = $("statusYaml");
  if (!yamlTextArea) return;

  const text = yamlTextArea.value || "";
  try {
    await callJson("/api/save_yaml", {
      method: "POST",
      body: JSON.stringify({ yaml: text }),
    });
    if (status) {
      status.textContent = "YAML saved ✅";
      status.classList.add("success");
      status.classList.remove("working");
    }
  } catch (err) {
    console.error("saveYaml error:", err);
    if (status) {
      status.textContent = "Error saving YAML.";
      status.classList.add("error");
      status.classList.remove("working");
    }
  }
}

// ----------------------
// Export
// ----------------------
async function exportVideo(optimized) {
  const status = $("statusExport");
  if (status) {
    status.textContent = optimized
      ? "Exporting optimized video…"
      : "Exporting standard video…";
    status.classList.add("working");
  }

  try {
    const res = await callJson("/api/export", {
      method: "POST",
      body: JSON.stringify({ optimized: !!optimized }),
    });
    const filename = res.filename;
    if (status) {
      status.textContent = `Export finished: ${filename}`;
      status.classList.remove("working");
      status.classList.add("success");
    }

    const link = $("downloadLink");
    if (link && filename) {
      link.href = `/api/download/${encodeURIComponent(filename)}`;
      link.textContent = "Download video";
      link.style.display = "inline-block";
    }
  } catch (err) {
    console.error("exportVideo error:", err);
    if (status) {
      status.textContent = "Error exporting video.";
      status.classList.add("error");
      status.classList.remove("working");
    }
  }
}

// ----------------------
// TTS / CTA / Captions / Timings / FG Scale
// ----------------------
async function setTts(enabled, voice) {
  try {
    await callJson("/api/tts", {
      method: "POST",
      body: JSON.stringify({ enabled, voice }),
    });
  } catch (err) {
    console.error("setTts error:", err);
  }
}

async function setCta(enabled, text, voiceover) {
  try {
    await callJson("/api/cta", {
      method: "POST",
      body: JSON.stringify({ enabled, text, voiceover }),
    });
  } catch (err) {
    console.error("setCta error:", err);
  }
}

async function applyOverlayStyle(style) {
  const status = $("statusOverlay");
  if (status) {
    status.textContent = `Applying overlay style: ${style}…`;
    status.classList.add("working");
  }
  try {
    await callJson("/api/overlay", {
      method: "POST",
      body: JSON.stringify({ style }),
    });
    if (status) {
      status.textContent = "Overlay updated ✅";
      status.classList.remove("working");
      status.classList.add("success");
    }
  } catch (err) {
    console.error("applyOverlayStyle error:", err);
    if (status) {
      status.textContent = "Error applying overlay.";
      status.classList.remove("working");
      status.classList.add("error");
    }
  }
}

async function saveCaptionsFromTextarea() {
  const captionsArea = $("captionsText");
  const status = $("statusCaptions");
  if (!captionsArea) return;

  const text = captionsArea.value || "";
  try {
    const res = await callJson("/api/save_captions", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    if (status) {
      status.textContent = `Captions saved (${res.count || 0} blocks) ✅`;
      status.classList.add("success");
      status.classList.remove("working");
    }
  } catch (err) {
    console.error("saveCaptionsFromTextarea error:", err);
    if (status) {
      status.textContent = "Error saving captions.";
      status.classList.add("error");
      status.classList.remove("working");
    }
  }
}

async function applyTimings(smart) {
  const status = $("statusTimings");
  if (status) {
    status.textContent = smart
      ? "Applying cinematic smart timings…"
      : "Applying standard timings…";
    status.classList.add("working");
  }

  try {
    await callJson("/api/timings", {
      method: "POST",
      body: JSON.stringify({ smart: !!smart }),
    });
    if (status) {
      status.textContent = "Timings updated ✅";
      status.classList.remove("working");
      status.classList.add("success");
    }
  } catch (err) {
    console.error("applyTimings error:", err);
    if (status) {
      status.textContent = "Error applying timings.";
      status.classList.add("error");
      status.classList.remove("working");
    }
  }
}

async function setFgScale(value) {
  try {
    await callJson("/api/fgscale", {
      method: "POST",
      body: JSON.stringify({ value }),
    });
  } catch (err) {
    console.error("setFgScale error:", err);
  }
}

// ----------------------
// Chat
// ----------------------
async function sendChat() {
  const input = $("chatInput");
  const output = $("chatOutput");
  if (!input || !output) return;

  const message = input.value.trim();
  if (!message) return;

  output.value += `You: ${message}\n`;
  input.value = "";

  try {
    const res = await callJson("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    output.value += `Assistant: ${res.reply}\n\n`;
    output.scrollTop = output.scrollHeight;
  } catch (err) {
    console.error("sendChat error:", err);
    output.value += "Assistant: [Error]\n\n";
  }
}

// ----------------------
// Hook up buttons
// ----------------------
document.addEventListener("DOMContentLoaded", () => {
  if ($("btnAnalyze")) $("btnAnalyze").onclick = startAnalysis;
  if ($("btnGenerateYaml")) $("btnGenerateYaml").onclick = generateYaml;
  if ($("btnLoadConfig")) $("btnLoadConfig").onclick = loadConfig;
  if ($("btnSaveYaml")) $("btnSaveYaml").onclick = saveYaml;
  if ($("btnExportStandard")) $("btnExportStandard").onclick = () => exportVideo(false);
  if ($("btnExportOptimized")) $("btnExportOptimized").onclick = () => exportVideo(true);
  if ($("btnSaveCaptions")) $("btnSaveCaptions").onclick = saveCaptionsFromTextarea;
  if ($("btnApplyTimingsStandard"))
    $("btnApplyTimingsStandard").onclick = () => applyTimings(false);
  if ($("btnApplyTimingsSmart"))
    $("btnApplyTimingsSmart").onclick = () => applyTimings(true);
  if ($("btnChatSend")) $("btnChatSend").onclick = sendChat;

  // Initial loads
  refreshAnalysesList();
  loadConfig();
});