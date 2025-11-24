// static/js/app.js
document.addEventListener("DOMContentLoaded", () => {

  // --------------------------------------------------
  // ELEMENT REFERENCES
  // --------------------------------------------------
  const btnUpload = document.getElementById("btn-upload");
  const uploadInput = document.getElementById("upload-input");
  const statusUpload = document.getElementById("status-upload");

  const btnAnalyze = document.getElementById("btn-analyze");
  const statusAnalyze = document.getElementById("status-analyze");
  const analysisList = document.getElementById("analysis-list");

  const btnGenerateYaml = document.getElementById("btn-generate-yaml");
  const btnRefreshConfig = document.getElementById("btn-refresh-config");
  const yamlEditor = document.getElementById("yaml-editor");
  const captionsEditor = document.getElementById("captions-editor");
  const statusYaml = document.getElementById("status-yaml");

  const captionChips = document.querySelectorAll(".btn.chip");

  const btnSaveCaptions = document.getElementById("btn-save-captions");
  const statusCaptions = document.getElementById("status-captions");

  const ttsEnabled = document.getElementById("tts-enabled");
  const ttsVoice = document.getElementById("tts-voice");
  const btnApplyTts = document.getElementById("btn-apply-tts");
  const statusTts = document.getElementById("status-tts");

  const ctaEnabled = document.getElementById("cta-enabled");
  const ctaText = document.getElementById("cta-text");
  const ctaVoiceover = document.getElementById("cta-voiceover");
  const btnApplyCta = document.getElementById("btn-apply-cta");
  const statusCta = document.getElementById("status-cta");

  const fgSlider = document.getElementById("fg-scale");
  const fgValue = document.getElementById("fg-scale-value");
  const btnApplyFg = document.getElementById("btn-apply-fgscale");
  const statusFg = document.getElementById("status-fgscale");

  const btnTimingsFixc = document.getElementById("btn-timings-fixc");
  const btnTimingsSmart = document.getElementById("btn-timings-smart");
  const statusTimings = document.getElementById("status-timings");

  const btnExport = document.getElementById("btn-export");
  const exportOptimizedToggle = document.getElementById("export-optimized");
  const statusExport = document.getElementById("status-export");

  const liveLogBox = document.getElementById("live-log");

  // --------------------------------------------------
  // HELPERS
  // --------------------------------------------------
  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function extractCaptions(cfg) {
    const caps = [];
    if (cfg.first_clip?.text) caps.push(cfg.first_clip.text);
    (cfg.middle_clips || []).forEach(c => { if (c.text) caps.push(c.text); });
    if (cfg.last_clip?.text) caps.push(cfg.last_clip.text);
    return caps.join("\n\n");
  }

  async function refreshYamlPreview() {
    const data = await postJSON("/api/config", {});
    yamlEditor.value = data.yaml || "";
    const cfg = data.config || {};
    captionsEditor.value = extractCaptions(cfg);
    return cfg;
  }

  // --------------------------------------------------
  // LIVE LOGS AUTO REFRESH
  // --------------------------------------------------
  setInterval(async () => {
    const res = await fetch("/api/status");
    const data = await res.json();
    liveLogBox.textContent = (data.log || []).join("\n");
    liveLogBox.scrollTop = liveLogBox.scrollHeight;
  }, 1200);

  // --------------------------------------------------
  // UPLOAD
  // --------------------------------------------------
  btnUpload.addEventListener("click", async () => {
    const files = uploadInput.files;
    if (!files.length) return;
    statusUpload.textContent = "Uploading…";
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      await fetch("/api/upload", { method: "POST", body: fd });
    }
    statusUpload.textContent = "✅ Uploaded. You can Analyze now.";
  });

  // --------------------------------------------------
  // ANALYZE
  // --------------------------------------------------
  btnAnalyze.addEventListener("click", async () => {
    statusAnalyze.textContent = "Analyzing…";
    const data = await postJSON("/api/analyze", {});
    analysisList.innerHTML = "";
    Object.entries(data).forEach(([file, desc]) => {
      analysisList.innerHTML += `<div><b>${file}</b><br>${desc}</div>`;
    });
    await refreshYamlPreview();
    statusAnalyze.textContent = "✅ Analysis complete.";
  });

  // --------------------------------------------------
  // GENERATE YAML
  // --------------------------------------------------
  btnGenerateYaml.addEventListener("click", async () => {
    statusYaml.textContent = "Generating YAML…";
    await postJSON("/api/generate_yaml", {});
    await refreshYamlPreview();
    statusYaml.textContent = "✅ YAML generated.";
  });

  // --------------------------------------------------
  // REFRESH CONFIG
  // --------------------------------------------------
  btnRefreshConfig.addEventListener("click", async () => {
    statusYaml.textContent = "Refreshing…";
    await refreshYamlPreview();
    statusYaml.textContent = "✅ Config refreshed.";
  });

  // --------------------------------------------------
  // CAPTION STYLE CHIPS
  // --------------------------------------------------
  captionChips.forEach(chip => {
    chip.addEventListener("click", async () => {
      captionChips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      statusYaml.textContent = "Applying style…";
      await postJSON("/api/overlay", { style: chip.dataset.style });
      await refreshYamlPreview();
      statusYaml.textContent = "✅ Captions updated.";
    });
  });

  // --------------------------------------------------
  // SAVE EDITED CAPTIONS
  // --------------------------------------------------
  btnSaveCaptions.addEventListener("click", async () => {
    statusCaptions.textContent = "Saving…";
    const text = captionsEditor.value.trim();
    await postJSON("/api/save_captions", { text });
    await refreshYamlPreview();
    statusCaptions.textContent = "✅ Captions saved.";
  });

  // --------------------------------------------------
  // TTS APPLY
  // --------------------------------------------------
  btnApplyTts.addEventListener("click", async () => {
    statusTts.textContent = "Updating narration…";
    await postJSON("/api/tts", {
      enabled: ttsEnabled.checked,
      voice: ttsVoice.value
    });
    await refreshYamlPreview();
    statusTts.textContent = "✅ Narration updated.";
  });

  // --------------------------------------------------
  // CTA APPLY
  // --------------------------------------------------
  btnApplyCta.addEventListener("click", async () => {
    statusCta.textContent = "Saving CTA…";
    await postJSON("/api/cta", {
      enabled: ctaEnabled.checked,
      text: ctaText.value,
      voiceover: ctaVoiceover.checked,
    });
    await refreshYamlPreview();
    statusCta.textContent = "✅ CTA updated.";
  });

  // --------------------------------------------------
  // FOREGROUND SCALE LIVE UPDATE
  // --------------------------------------------------
  fgSlider.addEventListener("input", () => {
    fgValue.textContent = parseFloat(fgSlider.value).toFixed(2);
  });

  // --------------------------------------------------
  // APPLY FG SCALE
  // --------------------------------------------------
  btnApplyFg.addEventListener("click", async () => {
    statusFg.textContent = "Updating scale…";
    await postJSON("/api/fgscale", { value: parseFloat(fgSlider.value) });
    await refreshYamlPreview();
    statusFg.textContent = "✅ Scale updated.";
  });

  // --------------------------------------------------
  // TIMINGS FIX-C
  // --------------------------------------------------
  btnTimingsFixc.addEventListener("click", async () => {
    statusTimings.textContent = "Applying FIX-C…";
    await postJSON("/api/timings", { smart: false });
    await refreshYamlPreview();
    statusTimings.textContent = "✅ FIX-C applied.";
  });

  // --------------------------------------------------
  // TIMINGS SMART
  // --------------------------------------------------
  btnTimingsSmart.addEventListener("click", async () => {
    statusTimings.textContent = "Applying cinematic pacing…";
    await postJSON("/api/timings", { smart: true });
    await refreshYamlPreview();
    statusTimings.textContent = "✅ Smart pacing applied.";
  });

  // --------------------------------------------------
  // EXPORT
  // --------------------------------------------------
  btnExport.addEventListener("click", async () => {
    statusExport.textContent = "Exporting…";
    const optimized = exportOptimizedToggle.checked;
    const resp = await postJSON("/api/export", { optimized });
    statusExport.innerHTML = `
      ✅ Export complete!<br>
      <a href="${resp.file_url}" target="_blank">Download Final Video</a>
    `;
  });

  // --------------------------------------------------
  // INITIAL LOAD
  // --------------------------------------------------
  refreshYamlPreview();

});