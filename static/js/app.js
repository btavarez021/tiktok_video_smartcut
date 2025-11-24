document.addEventListener("DOMContentLoaded", () => {

  const btnUpload = document.getElementById("btn-upload");
  const uploadInput = document.getElementById("upload-input");
  const statusUpload = document.getElementById("status-upload");

  const btnAnalyze = document.getElementById("btn-analyze");
  const statusAnalyze = document.getElementById("status-analyze");
  const analysisList = document.getElementById("analysis-list");

  const btnGenerateYaml = document.getElementById("btn-generate-yaml");
  const yamlEditor = document.getElementById("yaml-editor");
  const captionsEditor = document.getElementById("captions-editor");

  const captionChips = document.querySelectorAll(".btn.chip");
  const statusYaml = document.getElementById("status-yaml");

  const btnExport = document.getElementById("btn-export");
  const statusExport = document.getElementById("status-export");
  const exportOptimizedToggle = document.getElementById("export-optimized");

  const liveLogBox = document.getElementById("live-log");

  const btnSaveCaptions = document.getElementById("btn-save-captions");
  const statusCaptions = document.getElementById("status-captions");

  const btnSaveYaml = document.getElementById("btn-save-yaml");
  const statusSaveYaml = document.getElementById("status-save-yaml");

  const btnApplyTts = document.getElementById("btn-apply-tts");
  const statusTts = document.getElementById("status-tts");

  const btnApplyCta = document.getElementById("btn-apply-cta");
  const statusCta = document.getElementById("status-cta");

  const btnApplyFg = document.getElementById("btn-apply-fgscale");
  const statusFg = document.getElementById("status-fgscale");

  const fgSlider = document.getElementById("fg-scale");
  const fgValue = document.getElementById("fg-scale-value");

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function refreshYamlPreview() {
    const data = await postJSON("/api/config", {});
    yamlEditor.value = data.yaml || "";
    const cfg = data.config || {};
    captionsEditor.value = extractCaptions(cfg);
    return cfg;
  }

  function extractCaptions(cfg) {
    const caps = [];
    if (cfg.first_clip?.text) caps.push(cfg.first_clip.text);
    (cfg.middle_clips || []).forEach(c => caps.push(c.text));
    if (cfg.last_clip?.text) caps.push(cfg.last_clip.text);
    return caps.join("\n\n");
  }

  setInterval(async () => {
    const res = await fetch("/api/status");
    const data = await res.json();
    liveLogBox.textContent = (data.log || []).join("\n");
    liveLogBox.scrollTop = liveLogBox.scrollHeight;
  }, 1200);

  // UPLOAD
  btnUpload.addEventListener("click", async () => {
    const files = uploadInput.files;
    if (!files.length) return;
    statusUpload.textContent = "Uploading...";
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      await fetch("/api/upload", { method: "POST", body: fd });
    }
    statusUpload.textContent = "✅ Uploaded. You can Analyze now.";
  });

  // ANALYZE
  btnAnalyze.addEventListener("click", async () => {
    statusAnalyze.textContent = "Analyzing...";
    analysisList.innerHTML = "";
    const data = await postJSON("/api/analyze", {});
    Object.entries(data).forEach(([file, desc]) => {
      analysisList.innerHTML += `<div><b>${file}</b><br>${desc}</div>`;
    });
    await refreshYamlPreview();
    statusAnalyze.textContent = "✅ Analysis complete.";
  });

  // GENERATE YAML
  btnGenerateYaml.addEventListener("click", async () => {
    statusYaml.textContent = "Generating YAML...";
    await postJSON("/api/generate_yaml", {});
    await refreshYamlPreview();
    statusYaml.textContent = "✅ YAML created.";
  });

  // CAPTION STYLE CHIPS
  captionChips.forEach(chip => {
    chip.addEventListener("click", async () => {
      captionChips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      statusYaml.textContent = "Applying style...";
      await postJSON("/api/overlay", { style: chip.dataset.style });
      await refreshYamlPreview();
      statusYaml.textContent = "✅ Captions updated.";
    });
  });

  // EXPORT
  btnExport.addEventListener("click", async () => {
    statusExport.textContent = "Exporting...";
    const optimized = exportOptimizedToggle.checked;
    const resp = await postJSON("/api/export", { optimized });
    statusExport.innerHTML = `
      ✅ Export complete!<br>
      <a href="${resp.file_url}" target="_blank">Download Final Video</a>
    `;
  });

  // ============================================
// SAVE EDITED CAPTIONS BACK TO CONFIG
// ============================================
if (btnSaveCaptions && captionsEditor) {
  btnSaveCaptions.addEventListener("click", async () => {
    const text = captionsEditor.value.trim();

    statusCaptions.textContent = "Saving captions…";
    statusCaptions.className = "status-text";

    try {
      await postJSON("/api/save_captions", { text });

      statusCaptions.textContent = "✅ Captions saved.";
      statusCaptions.classList.add("success");

      // ✅ REFRESH YAML + sync editor again
      const cfg = await refreshYamlPreview();
      if (captionsEditor && cfg) {
        captionsEditor.value = extractCaptions(cfg);
      }

    } catch (err) {
      console.error(err);
      statusCaptions.textContent = "❌ Failed saving captions.";
      statusCaptions.classList.add("error");
    } 
  });
}

// ============================================
// SAVE YAML BUTTON
// ============================================
if (btnSaveYaml && yamlEditor) {
  btnSaveYaml.addEventListener("click", async () => {
    statusSaveYaml.textContent = "Saving YAML…";
    statusSaveYaml.className = "status-text";

    try {
      await postJSON("/api/save_yaml", { yaml: yamlEditor.value });

      statusSaveYaml.textContent = "✅ YAML saved.";
      statusSaveYaml.classList.add("success");

      // refresh + sync captions editor
      const cfg = await refreshYamlPreview();
      if (captionsEditor && cfg) {
        captionsEditor.value = extractCaptions(cfg);
      }

    } catch (err) {
      console.error(err);
      statusSaveYaml.textContent = "❌ Error saving YAML.";
      statusSaveYaml.classList.add("error");
    }
  });
}

// ============================================
// TTS Function
// ============================================
if (btnApplyTts) {
  btnApplyTts.addEventListener("click", async () => {
    statusTts.textContent = "Updating voiceover…";
    const enabled = ttsEnabled.checked;
    const voice = ttsVoice.value;
    try {
      await postJSON("/api/tts", { enabled, voice });
      statusTts.textContent = "✅ Voiceover updated.";
      await refreshYamlPreview();
    } catch (err) {
      console.error(err);
      statusTts.textContent = "❌ Error updating voiceover.";
    }
  });
}


// ============================================
// CTA Function
// ============================================
if (btnApplyCta) {
  btnApplyCta.addEventListener("click", async () => {
    statusCta.textContent = "Updating CTA…";
    const enabled = ctaEnabled.checked;
    const text = ctaText.value;
    const voiceover = ctaVoiceover.checked;
    try {
      await postJSON("/api/cta", { enabled, text, voiceover });
      statusCta.textContent = "✅ CTA updated.";
      await refreshYamlPreview();
    } catch (err) {
      console.error(err);
      statusCta.textContent = "❌ Error updating CTA.";
    }
  });
}

// ============================================
// FG Scale Function
// ============================================
if (btnApplyFg) {
  btnApplyFg.addEventListener("click", async () => {
    statusFg.textContent = "Updating scale…";
    const value = parseFloat(fgSlider.value);
    try {
      await postJSON("/api/fgscale", { value });
      statusFg.textContent = "✅ Scale updated.";
      await refreshYamlPreview();
    } catch (err) {
      console.error(err);
      statusFg.textContent = "❌ Error updating scale.";
    }
  });
}

  refreshYamlPreview();
});
