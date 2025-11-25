// static/js/app.js

// ---------------------------
// Helper: simple status logger
// ---------------------------
function appendStatus(message) {
  const box = document.getElementById("statusBox");
  if (!box) return;
  const line = document.createElement("div");
  line.textContent = message;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// ---------------------------
// Helper: fetch JSON wrapper
// ---------------------------
async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${text}`);
  }
  return resp.json();
}

// ----------------------------------
// Upload videos → S3 (raw_uploads/)
// ----------------------------------
async function handleUpload(event) {
  event.preventDefault();

  const input = document.getElementById("fileInput");
  if (!input || !input.files.length) {
    alert("Please choose one or more video files first.");
    return;
  }

  const formData = new FormData();
  for (const file of input.files) {
    formData.append("files", file);
  }

  appendStatus("Uploading videos to S3…");

  try {
    const data = await fetchJSON("/api/upload_s3", {
      method: "POST",
      body: formData,
    });
    appendStatus(`Upload complete: ${JSON.stringify(data)}`);
    alert("Upload complete! Now click Analyze.");
  } catch (err) {
    console.error(err);
    appendStatus(`Upload failed: ${err.message}`);
    alert("Upload failed, see status log.");
  }
}

// ----------------------------------
// ANALYZE – step-based calls
// ----------------------------------
async function startAnalyze() {
  appendStatus("🔍 Starting video analysis…");

  try {
    const start = await fetchJSON("/api/analyze_start", {
      method: "POST",
    });
    if (!start.queue || !start.queue.length) {
      appendStatus("No videos found in S3 raw_uploads/.");
      return;
    }
    appendStatus(`Found ${start.queue.length} video(s). Beginning step analysis…`);
    await runAnalyzeSteps();
  } catch (err) {
    console.error(err);
    appendStatus(`Analyze start failed: ${err.message}`);
  }
}

async function runAnalyzeSteps() {
  let done = false;
  let lastResults = {};

  while (!done) {
    try {
      const data = await fetchJSON("/api/analyze_step", {
        method: "POST",
      });
      done = data.done;
      lastResults = data.results || {};

      if (data.current) {
        appendStatus(`Analyzed: ${data.current} (remaining: ${data.remaining})`);
      }

      // update UI analyses list
      renderAnalysesList(lastResults);

      // small delay to avoid hammering backend
      if (!done) {
        await new Promise((res) => setTimeout(res, 400));
      }
    } catch (err) {
      console.error(err);
      appendStatus(`Analyze step error: ${err.message}`);
      break;
    }
  }

  appendStatus("✅ Analysis complete.");
  // make sure cache is synced
  await refreshAnalysesCache();
}

// ----------------------------------
// Analyses cache (GET)
// ----------------------------------
async function refreshAnalysesCache() {
  try {
    const cache = await fetchJSON("/api/analyses_cache");
    renderAnalysesList(cache);
  } catch (err) {
    console.error(err);
    appendStatus(`Failed to load analyses cache: ${err.message}`);
  }
}

function renderAnalysesList(analyses) {
  const list = document.getElementById("analysisList");
  if (!list) return;

  list.innerHTML = "";

  const entries = Object.entries(analyses || {});
  if (!entries.length) {
    list.innerHTML = "<li>No analyses yet.</li>";
    return;
  }

  for (const [file, desc] of entries) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${file}</strong>: ${desc}`;
    list.appendChild(li);
  }
}

// ----------------------------------
// Status polling (/api/status)
// ----------------------------------
async function pollStatus() {
  try {
    const data = await fetchJSON("/api/status");
    const log = data.status_log || [];
    const box = document.getElementById("statusBox");
    if (!box) return;
    box.innerHTML = "";
    for (const line of log) {
      const div = document.createElement("div");
      div.textContent = line;
      box.appendChild(div);
    }
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    // Silently ignore status errors
    console.warn("Status poll error:", err.message);
  }
}

// ----------------------------------
// YAML (generate, load, save)
// ----------------------------------
async function handleGenerateYaml() {
  appendStatus("📝 Generating YAML storyboard…");

  try {
    const cfg = await fetchJSON("/api/generate_yaml", {
      method: "POST",
    });
    await loadConfigIntoEditors(cfg);
    appendStatus("YAML generated and loaded.");
  } catch (err) {
    console.error(err);
    appendStatus(`Generate YAML failed: ${err.message}`);
  }
}

async function loadConfigIntoEditors(optionalConfig) {
  try {
    const data = optionalConfig || (await fetchJSON("/api/config"));
    const yamlEditor = document.getElementById("yamlEditor");
    const captionsEditor = document.getElementById("captionsEditor");

    if (yamlEditor) {
      yamlEditor.value = data.yaml || "";
    }

    if (captionsEditor && data.config) {
      const parts = [];
      if (data.config.first_clip && data.config.first_clip.text) {
        parts.push(data.config.first_clip.text);
      }
      if (Array.isArray(data.config.middle_clips)) {
        for (const mc of data.config.middle_clips) {
          if (mc.text) parts.push(mc.text);
        }
      }
      if (data.config.last_clip && data.config.last_clip.text) {
        parts.push(data.config.last_clip.text);
      }
      captionsEditor.value = parts.join("\n\n");
    }
  } catch (err) {
    console.error(err);
    appendStatus(`Load config failed: ${err.message}`);
  }
}

async function handleSaveYaml() {
  const yamlEditor = document.getElementById("yamlEditor");
  if (!yamlEditor) return;

  const yamlText = yamlEditor.value;
  appendStatus("Saving YAML…");

  try {
    const resp = await fetchJSON("/api/save_yaml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml: yamlText }),
    });

    if (resp.error) {
      appendStatus(`Save YAML error: ${resp.error}`);
      alert("YAML save failed. See status log.");
    } else {
      appendStatus("YAML saved.");
      await loadConfigIntoEditors();
    }
  } catch (err) {
    console.error(err);
    appendStatus(`Save YAML failed: ${err.message}`);
  }
}

// ----------------------------------
// Captions editing (/api/save_captions)
// ----------------------------------
async function handleSaveCaptions() {
  const captionsEditor = document.getElementById("captionsEditor");
  if (!captionsEditor) return;

  const text = captionsEditor.value || "";
  appendStatus("Saving captions…");

  try {
    const resp = await fetchJSON("/api/save_captions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (resp.error) {
      appendStatus(`Save captions error: ${resp.error}`);
    } else {
      appendStatus("Captions saved. YAML updated.");
      await loadConfigIntoEditors();
    }
  } catch (err) {
    console.error(err);
    appendStatus(`Save captions failed: ${err.message}`);
  }
}

// ----------------------------------
// Overlay styles
// ----------------------------------
async function handleOverlay(style) {
  style = style || "travel_blog";
  appendStatus(`Applying overlay style: ${style}…`);

  try {
    await fetchJSON("/api/overlay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ style }),
    });
    appendStatus("Overlay applied.");
    await loadConfigIntoEditors();
  } catch (err) {
    console.error(err);
    appendStatus(`Overlay failed: ${err.message}`);
  }
}

// ----------------------------------
// Smart timings
// ----------------------------------
async function handleTimings(smart) {
  appendStatus(smart ? "Applying cinematic timings…" : "Applying standard timings…");

  try {
    await fetchJSON("/api/timings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smart: !!smart }),
    });
    appendStatus("Timings updated.");
    await loadConfigIntoEditors();
  } catch (err) {
    console.error(err);
    appendStatus(`Timings failed: ${err.message}`);
  }
}

// ----------------------------------
// TTS toggle
// ----------------------------------
async function handleTtsChange() {
  const enabled = !!document.getElementById("chkTtsEnabled")?.checked;
  const voice = document.getElementById("ttsVoice")?.value || null;

  appendStatus(`Updating TTS: enabled=${enabled}, voice=${voice || "default"}`);

  try {
    await fetchJSON("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, voice }),
    });
    appendStatus("TTS updated.");
  } catch (err) {
    console.error(err);
    appendStatus(`TTS update failed: ${err.message}`);
  }
}

// ----------------------------------
// CTA toggle
// ----------------------------------
async function handleCtaChange() {
  const enabled = !!document.getElementById("chkCtaEnabled")?.checked;
  const text = document.getElementById("ctaText")?.value || "";
  const voiceover = !!document.getElementById("chkCtaVoiceover")?.checked;

  appendStatus(`Updating CTA: enabled=${enabled}`);

  try {
    await fetchJSON("/api/cta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, text, voiceover }),
    });
    appendStatus("CTA updated.");
  } catch (err) {
    console.error(err);
    appendStatus(`CTA update failed: ${err.message}`);
  }
}

// ----------------------------------
// FG scale slider
// ----------------------------------
async function handleFgScaleChange() {
  const slider = document.getElementById("fgScaleRange");
  const label = document.getElementById("fgScaleValue");
  if (!slider) return;
  const value = parseFloat(slider.value || "1.0");

  if (label) label.textContent = value.toFixed(2);
  appendStatus(`Updating FG scale: ${value.toFixed(2)}…`);

  try {
    await fetchJSON("/api/fgscale", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    appendStatus("FG scale updated.");
  } catch (err) {
    console.error(err);
    appendStatus(`FG scale update failed: ${err.message}`);
  }
}

// ----------------------------------
// Export (standard / optimized)
// ----------------------------------
async function handleExport(optimized) {
  appendStatus(optimized ? "🎬 Exporting (optimized)…" : "🎬 Exporting (standard)…");

  try {
    const data = await fetchJSON("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ optimized: !!optimized }),
    });

    if (data.error) {
      appendStatus(`Export error: ${data.error}`);
      alert("Export failed. See status log.");
      return;
    }

    const filename = data.filename;
    appendStatus(`Export complete: ${filename}`);
    alert("Export complete! Use the Download link to grab the MP4.");

    const dlLink = document.getElementById("downloadLink");
    if (dlLink && filename) {
      dlLink.href = `/api/download/${encodeURIComponent(filename)}`;
      dlLink.style.display = "inline-block";
    }
  } catch (err) {
    console.error(err);
    appendStatus(`Export failed: ${err.message}`);
    alert("Export failed. See status log.");
  }
}

// ----------------------------------
// Wire up all controls on load
// ----------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // Upload
  const uploadForm = document.getElementById("uploadForm");
  if (uploadForm) {
    uploadForm.addEventListener("submit", handleUpload);
  }

  // Analyze
  const btnAnalyze = document.getElementById("btnAnalyze");
  if (btnAnalyze) {
    btnAnalyze.addEventListener("click", startAnalyze);
  }

  // Generate YAML
  const btnGenerateYaml = document.getElementById("btnGenerateYaml");
  if (btnGenerateYaml) {
    btnGenerateYaml.addEventListener("click", handleGenerateYaml);
  }

  // Save YAML
  const btnSaveYaml = document.getElementById("btnSaveYaml");
  if (btnSaveYaml) {
    btnSaveYaml.addEventListener("click", handleSaveYaml);
  }

  // Save captions
  const btnSaveCaptions = document.getElementById("btnSaveCaptions");
  if (btnSaveCaptions) {
    btnSaveCaptions.addEventListener("click", handleSaveCaptions);
  }

  // Overlay style dropdown
  const overlaySelect = document.getElementById("overlayStyle");
  if (overlaySelect) {
    overlaySelect.addEventListener("change", () => {
      handleOverlay(overlaySelect.value);
    });
  }

  // Timings buttons
  const btnTimingsStandard = document.getElementById("btnTimingsStandard");
  const btnTimingsCinematic = document.getElementById("btnTimingsCinematic");
  if (btnTimingsStandard) {
    btnTimingsStandard.addEventListener("click", () => handleTimings(false));
  }
  if (btnTimingsCinematic) {
    btnTimingsCinematic.addEventListener("click", () => handleTimings(true));
  }

  // TTS
  const chkTtsEnabled = document.getElementById("chkTtsEnabled");
  const ttsVoice = document.getElementById("ttsVoice");
  if (chkTtsEnabled) chkTtsEnabled.addEventListener("change", handleTtsChange);
  if (ttsVoice) ttsVoice.addEventListener("change", handleTtsChange);

  // CTA
  const chkCtaEnabled = document.getElementById("chkCtaEnabled");
  const ctaText = document.getElementById("ctaText");
  const chkCtaVoiceover = document.getElementById("chkCtaVoiceover");
  if (chkCtaEnabled) chkCtaEnabled.addEventListener("change", handleCtaChange);
  if (ctaText) ctaText.addEventListener("input", handleCtaChange);
  if (chkCtaVoiceover) chkCtaVoiceover.addEventListener("change", handleCtaChange);

  // FG scale
  const fgScaleRange = document.getElementById("fgScaleRange");
  if (fgScaleRange) {
    fgScaleRange.addEventListener("input", handleFgScaleChange);
  }

  // Export buttons
  const btnExportStandard = document.getElementById("btnExportStandard");
  const btnExportOptimized = document.getElementById("btnExportOptimized");
  if (btnExportStandard) {
    btnExportStandard.addEventListener("click", () => handleExport(false));
  }
  if (btnExportOptimized) {
    btnExportOptimized.addEventListener("click", () => handleExport(true));
  }

  // Initial pulls
  refreshAnalysesCache();
  loadConfigIntoEditors();

  // Poll status every 2s
  setInterval(pollStatus, 2000);
});
