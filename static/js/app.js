document.addEventListener("DOMContentLoaded", () => {
  // ============================================
  // ELEMENT REFERENCES
  // ============================================
  const loader = document.getElementById("global-loader");

  // Step 1
  const btnAnalyze = document.getElementById("btn-analyze");
  const spinAnalyze = document.getElementById("spinner-analyze");
  const statusAnalyze = document.getElementById("status-analyze");
  const analysisList = document.getElementById("analysis-list");

  // Step 2
  const btnGenerateYaml = document.getElementById("btn-generate-yaml");
  const spinYaml = document.getElementById("spinner-yaml");
  const btnRefreshConfig = document.getElementById("btn-refresh-config");
  const statusYaml = document.getElementById("status-yaml");
  const captionChips = document.querySelectorAll(".btn.chip");
  const captionsEditor = document.getElementById("captions-editor");
  const btnSaveCaptions = document.getElementById("btn-save-captions");
  const statusCaptions = document.getElementById("status-captions");

  // YAML editor (right side)
  const yamlEditor = document.getElementById("yaml-editor");
  const btnSaveYaml = document.getElementById("btn-save-yaml");
  const statusSaveYaml = document.getElementById("status-save-yaml");

  // Step 3: TTS
  const ttsEnabled = document.getElementById("tts-enabled");
  const ttsVoice = document.getElementById("tts-voice");
  const btnApplyTts = document.getElementById("btn-apply-tts");
  const spinTts = document.getElementById("spinner-tts");
  const statusTts = document.getElementById("status-tts");

  // Step 3: CTA
  const ctaEnabled = document.getElementById("cta-enabled");
  const ctaText = document.getElementById("cta-text");
  const ctaVoiceover = document.getElementById("cta-voiceover");
  const btnApplyCta = document.getElementById("btn-apply-cta");
  const spinCta = document.getElementById("spinner-cta");
  const statusCta = document.getElementById("status-cta");

  // Step 3: Foreground scale
  const fgSlider = document.getElementById("fg-scale");
  const fgValue = document.getElementById("fg-scale-value");
  const btnApplyFg = document.getElementById("btn-apply-fgscale");
  const spinFg = document.getElementById("spinner-fgscale");
  const statusFg = document.getElementById("status-fgscale");

  // Step 4: Timings
  const btnTimingsFixc = document.getElementById("btn-timings-fixc");
  const spinTimingsFixc = document.getElementById("spinner-timings-fixc");
  const btnTimingsSmart = document.getElementById("btn-timings-smart");
  const spinTimingsSmart = document.getElementById("spinner-timings-smart");
  const statusTimings = document.getElementById("status-timings");

  // Step 5: Export
  const btnExport = document.getElementById("btn-export");
  const spinExport = document.getElementById("spinner-export");
  const statusExport = document.getElementById("status-export");
  const exportOptimizedToggle = document.getElementById("export-optimized");

  // Chat
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const chatSendBtn = document.getElementById("chat-send-btn");
  const spinChat = document.getElementById("spinner-chat");
  const chatMessages = document.getElementById("chat-messages");

  //upload video
  const uploadForm = document.getElementById("upload-form");
  const uploadInput = document.getElementById("upload-input");
  const uploadStatus = document.getElementById("upload-status");


  // Processing log panel (right side)
  const liveLogBox = document.getElementById("live-log");

  // ============================================
  // GENERIC HELPERS
  // ============================================

  function extractCaptionsFromConfig(cfg) {
    const captions = [];

    if (cfg.first_clip && cfg.first_clip.text) {
      captions.push(cfg.first_clip.text);
    }

    (cfg.middle_clips || []).forEach((c) => {
      if (c.text) captions.push(c.text);
    });

    if (cfg.last_clip && cfg.last_clip.text) {
      captions.push(cfg.last_clip.text);
    }

    // simple paragraph breaks between clips
    return captions.join("\n\n");
  }

  function showLoader(message) {
    if (!loader) return;
    loader.classList.remove("hidden");
    const text = loader.querySelector(".loader-text");
    if (text && message) text.textContent = message;
  }

  function hideLoader() {
    if (!loader) return;
    loader.classList.add("hidden");
  }

  function setButtonLoading(btn, spinner, isLoading) {
    if (!btn || !spinner) return;
    if (isLoading) {
      btn.disabled = true;
      spinner.classList.add("active");
    } else {
      btn.disabled = false;
      spinner.classList.remove("active");
    }
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function markStepDone(stepIndex) {
    const steps = document.querySelectorAll(".stepper .step");
    if (!steps[stepIndex]) return;

    const step = steps[stepIndex];
    const circle = step.querySelector(".step-number");

    step.classList.add("success");
    circle.classList.add("checkmark");
  }

  function resetProcessingLog(title = "") {
    if (!liveLogBox) return;
    liveLogBox.textContent = title ? `${title}\n` : "";
    liveLogBox.scrollTop = liveLogBox.scrollHeight;
  }

  // ============================================
  // YAML PREVIEW / EDITOR
  // ============================================
  async function refreshYamlPreview() {
    try {
      const data = await postJSON("/api/config", {});
      if (yamlEditor) {
        yamlEditor.value = data.yaml || "# Empty config.yml";
      }
      // Also return parsed config for captions editor
      return data.config || {};
    } catch (err) {
      console.error(err);
      if (yamlEditor) {
        yamlEditor.value = "# Error loading config.yml";
      }
      return {};
    }
  }

  if (btnSaveYaml) {
    btnSaveYaml.addEventListener("click", async () => {
      if (!yamlEditor) return;

      statusSaveYaml.textContent = "Saving YAML…";
      statusSaveYaml.className = "status-text";

      try {
        await postJSON("/api/save_yaml", { yaml: yamlEditor.value });
        statusSaveYaml.textContent = "YAML saved.";
        statusSaveYaml.classList.add("success");
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        statusSaveYaml.textContent = "Error saving YAML.";
        statusSaveYaml.classList.add("error");
      }
    });
  }

  // ============================================
  // Upload Video Handler
  // ============================================

  if (uploadForm) {
  uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const file = uploadInput.files[0];
    if (!file) {
      uploadStatus.textContent = "Please select a video file to upload.";
      uploadStatus.className = "status-text error";
      return;
    }

    uploadStatus.textContent = "Uploading...";
    uploadStatus.className = "status-text";

    const formData = new FormData();
    formData.append("video", file);

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (data.success) {
        uploadStatus.textContent = "✅ Upload successful! You can now Analyze.";
        uploadStatus.classList.add("success");
      } else {
        uploadStatus.textContent = "❌ Upload failed.";
        uploadStatus.classList.add("error");
      }
    } catch (err) {
      uploadStatus.textContent = "❌ Upload error.";
      uploadStatus.classList.add("error");
    }
  });
}


  // ============================================
  // LIVE LOG AUTO-REFRESH (1s) → /api/status
  // ============================================
  async function refreshLogs() {
    if (!liveLogBox) return;
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();
      const lines = data.log || [];
      liveLogBox.textContent = lines.join("\n") || "";
      liveLogBox.scrollTop = liveLogBox.scrollHeight;
    } catch (err) {
      console.error("Log refresh failed:", err);
    }
  }
  setInterval(refreshLogs, 1000);

  // ============================================
  // EXPORT MODE INIT (toggle)
  // ============================================
  async function initExportMode() {
    if (!exportOptimizedToggle) return;
    try {
      const res = await fetch("/api/export_mode");
      if (!res.ok) return;
      const data = await res.json();
      const mode = data.mode || "standard";
      exportOptimizedToggle.checked = mode === "optimized";
    } catch (err) {
      console.error("Failed to init export mode:", err);
    }
  }

  if (exportOptimizedToggle) {
    exportOptimizedToggle.addEventListener("change", async () => {
      const mode = exportOptimizedToggle.checked ? "optimized" : "standard";
      try {
        await fetch("/api/export_mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode }),
        });
      } catch (err) {
        console.error("Failed to save export mode:", err);
      }
    });
  }

  // ============================================
  // INITIAL LOAD
  // ============================================
  (async () => {
    const cfg = await refreshYamlPreview();
    if (captionsEditor && cfg) {
      captionsEditor.value = extractCaptionsFromConfig(cfg);
    }
  })();
  initExportMode();

  // ============================================
  // STEP 1: ANALYZE
  // ============================================
  if (btnAnalyze) {
    btnAnalyze.addEventListener("click", async () => {
      resetProcessingLog();

      if (statusAnalyze) {
        statusAnalyze.textContent = "Analyzing videos in tik_tok_downloads/…";
        statusAnalyze.className = "status-text";
      }
      if (analysisList) {
        analysisList.innerHTML = "";
      }

      setButtonLoading(btnAnalyze, spinAnalyze, true);

      try {
        const data = await postJSON("/api/analyze", {});
        const count = Object.keys(data || {}).length;

        if (statusAnalyze) {
          statusAnalyze.textContent = `Found and analyzed ${count} video(s).`;
          statusAnalyze.classList.add("success");
          markStepDone(0);
        }

        if (analysisList) {
          if (!count) {
            analysisList.innerHTML =
              '<div class="analysis-item">No videos found in tik_tok_downloads/.</div>';
          } else {
            analysisList.innerHTML = Object.entries(data)
              .map(
                ([file, desc]) =>
                  `<div class="analysis-item"><strong>${file}</strong><br>${desc}</div>`
              )
              .join("");
          }
        }

        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusAnalyze) {
          statusAnalyze.textContent =
            "Error during analysis. Check logs.";
          statusAnalyze.classList.add("error");
        }
      } finally {
        setButtonLoading(btnAnalyze, spinAnalyze, false);
      }
    });
  }

  // ============================================
  // STEP 2: YAML GENERATION
  // ============================================
  if (btnGenerateYaml) {
    btnGenerateYaml.addEventListener("click", async () => {
      resetProcessingLog();

      if (statusYaml) {
        statusYaml.textContent = "Calling LLM to generate config.yml…";
        statusYaml.className = "status-text";
      }

      showLoader("Generating YAML storyboard…");
      setButtonLoading(btnGenerateYaml, spinYaml, true);

      try {
        await postJSON("/api/generate_yaml", {});
        if (statusYaml) {
          statusYaml.textContent = "YAML generated and saved to config.yml.";
          statusYaml.classList.add("success");
          markStepDone(1);
        }
        const cfg = await refreshYamlPreview();
        if (captionsEditor && cfg) {
          captionsEditor.value = extractCaptionsFromConfig(cfg);
        }
      } catch (err) {
        console.error(err);
        if (statusYaml) {
          statusYaml.textContent = "Error generating YAML. Check logs.";
          statusYaml.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnGenerateYaml, spinYaml, false);
      }
    });
  }

  if (btnRefreshConfig) {
    btnRefreshConfig.addEventListener("click", async () => {
      resetProcessingLog();

      if (statusYaml) {
        statusYaml.textContent = "Refreshing from config.yml…";
        statusYaml.className = "status-text";
      }

      showLoader("Refreshing config…");
      try {
        const cfg = await refreshYamlPreview();
        if (captionsEditor && cfg) {
          captionsEditor.value = extractCaptionsFromConfig(cfg);
        }
        if (statusYaml) {
          statusYaml.textContent = "Config reloaded from config.yml.";
          statusYaml.classList.add("success");
          markStepDone(2);
        }
      } catch (err) {
        console.error(err);
        if (statusYaml) {
          statusYaml.textContent = "Error refreshing config.";
          statusYaml.classList.add("error");
        }
      } finally {
        hideLoader();
      }
    });
  }

  // ============================================
  // CAPTION STYLE CHIPS → /api/overlay
  // ============================================
  if (captionChips && captionChips.length) {
    captionChips.forEach((chip) => {
      chip.addEventListener("click", async () => {
        resetProcessingLog();

        const style = chip.dataset.style || "punchy";

        captionChips.forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");

        if (statusYaml) {
          statusYaml.textContent = `Applying "${style}" overlay style…`;
          statusYaml.className = "status-text";
        }

        showLoader("Updating captions via overlay…");

        try {
          await postJSON("/api/overlay", { style });

          // Fetch updated config + YAML
          const cfg = await refreshYamlPreview();

          // Fill captions editor
          if (captionsEditor && cfg) {
            captionsEditor.value = extractCaptionsFromConfig(cfg);
          }

          if (statusYaml) {
            statusYaml.textContent = `✅ Captions rewritten using: ${style}`;
            statusYaml.classList.add("success");
            if (!chip.classList.contains("visited")) {
              markStepDone(3);
              chip.classList.add("visited");
            }
          }
        } catch (err) {
          console.error(err);
          if (statusYaml) {
            statusYaml.textContent = "❌ Failed updating captions.";
            statusYaml.classList.add("error");
          }
        } finally {
          hideLoader();
        }
      });
    });
  }

  // ============================================
  // SAVE EDITED CAPTIONS BACK TO CONFIG
  // ============================================
  if (btnSaveCaptions && captionsEditor) {
    btnSaveCaptions.addEventListener("click", async () => {
      const text = captionsEditor.value.trim();

      statusCaptions.textContent = "Saving captions…";
      statusCaptions.className = "status-text";

      showLoader("Saving edited captions…");

      try {
        await postJSON("/api/save_captions", { text });

        statusCaptions.textContent = "✅ Captions saved.";
        statusCaptions.classList.add("success");

        // show sync suggestion banner
        const ttsHint = document.getElementById("tts-sync-hint");
        if (ttsHint) ttsHint.classList.remove("hidden");

        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        statusCaptions.textContent = "❌ Failed saving captions.";
        statusCaptions.classList.add("error");
      } finally {
        hideLoader();
      }
    });
  }

  // ============================================
  // SYNC NARRATION TO CAPTIONS (Option A)
  // ============================================
  const btnSyncTts = document.getElementById("btn-sync-tts");
  if (btnSyncTts) {
    btnSyncTts.addEventListener("click", async () => {
      const ttsHint = document.getElementById("tts-sync-hint");
      if (ttsHint) ttsHint.classList.add("hidden");

      const enabled = true;
      const voice = ttsVoice ? ttsVoice.value : "alloy";

      if (statusTts) {
        statusTts.textContent = "Updating narration to match captions…";
        statusTts.className = "status-text";
      }

      showLoader("Generating narration…");
      setButtonLoading(btnApplyTts, spinTts, true);

      try {
        await postJSON("/api/tts", { enabled, voice });
        if (statusTts) {
          statusTts.textContent = "✅ Narration synced to captions.";
          statusTts.classList.add("success");
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusTts) {
          statusTts.textContent = "❌ Failed updating narration.";
          statusTts.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnApplyTts, spinTts, false);
      }
    });
  }

  // ============================================
  // STEP 3: TTS
  // ============================================
  if (btnApplyTts) {
    btnApplyTts.addEventListener("click", async () => {
      resetProcessingLog();

      const enabled = ttsEnabled ? ttsEnabled.checked : false;
      const voice = ttsVoice ? ttsVoice.value : "alloy";

      if (statusTts) {
        statusTts.textContent = enabled
          ? "Enabling voiceover narration…"
          : "Disabling voiceover…";
        statusTts.className = "status-text";
      }

      showLoader("Updating TTS settings…");
      setButtonLoading(btnApplyTts, spinTts, true);

      try {
        const res = await postJSON("/api/tts", { enabled, voice });
        if (statusTts) {
          statusTts.textContent = `Voiceover is now ${
            res.tts_enabled ? "ON" : "OFF"
          } (voice: ${res.tts_voice || voice}).`;
          statusTts.classList.add("success");
          markStepDone(4);
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusTts) {
          statusTts.textContent = "Error updating TTS. Check logs.";
          statusTts.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnApplyTts, spinTts, false);
      }
    });
  }

  // ============================================
  // STEP 3: CTA
  // ============================================
  if (btnApplyCta) {
    btnApplyCta.addEventListener("click", async () => {
      resetProcessingLog();

      const enabled = ctaEnabled ? ctaEnabled.checked : false;
      const text = ctaText ? ctaText.value : "";
      const voiceover = ctaVoiceover ? ctaVoiceover.checked : false;

      if (statusCta) {
        statusCta.textContent = "Saving CTA settings…";
        statusCta.className = "status-text";
      }

      showLoader("Updating CTA…");
      setButtonLoading(btnApplyCta, spinCta, true);

      try {
        const res = await postJSON("/api/cta", {
          enabled,
          text,
          voiceover,
        });
        if (statusCta) {
          statusCta.textContent = `CTA ${
            res.enabled ? "enabled" : "disabled"
          }${res.text ? " — text updated." : "."}`;
          statusCta.classList.add("success");
          markStepDone(5);
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusCta) {
          statusCta.textContent = "Error saving CTA. Check logs.";
          statusCta.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnApplyCta, spinCta, false);
      }
    });
  }

  // ============================================
  // STEP 3: FOREGROUND SCALE
  // ============================================
  if (fgSlider && fgValue) {
    fgValue.textContent = parseFloat(fgSlider.value).toFixed(2);
    fgSlider.addEventListener("input", () => {
      fgValue.textContent = parseFloat(fgSlider.value).toFixed(2);
    });
  }

  if (btnApplyFg) {
    btnApplyFg.addEventListener("click", async () => {
      resetProcessingLog();

      const value = fgSlider ? parseFloat(fgSlider.value) : 1.0;

      if (statusFg) {
        statusFg.textContent = "Updating foreground scale…";
        statusFg.className = "status-text";
      }

      showLoader("Applying foreground scale…");
      setButtonLoading(btnApplyFg, spinFg, true);

      try {
        const res = await postJSON("/api/fgscale", { value });
        if (statusFg) {
          statusFg.textContent = `Foreground scale set to ${Number(
            res.fg_scale_default
          ).toFixed(2)}.`;
          statusFg.classList.add("success");
          markStepDone(5);
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusFg) {
          statusFg.textContent = "Error updating foreground scale.";
          statusFg.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnApplyFg, spinFg, false);
      }
    });
  }

  // ============================================
  // STEP 4: TIMINGS
  // ============================================
  if (btnTimingsFixc) {
    btnTimingsFixc.addEventListener("click", async () => {
      resetProcessingLog();

      if (statusTimings) {
        statusTimings.textContent = "Applying standard FIX-C timings…";
        statusTimings.className = "status-text";
      }

      showLoader("Applying FIX-C timings…");
      setButtonLoading(btnTimingsFixc, spinTimingsFixc, true);

      try {
        await postJSON("/api/timings", { smart: false });
        if (statusTimings) {
          statusTimings.textContent = "Standard FIX-C timings applied.";
          statusTimings.classList.add("success");
          markStepDone(6);
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusTimings) {
          statusTimings.textContent = "Error applying timings.";
          statusTimings.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnTimingsFixc, spinTimingsFixc, false);
      }
    });
  }

  if (btnTimingsSmart) {
    btnTimingsSmart.addEventListener("click", async () => {
      resetProcessingLog();

      if (statusTimings) {
        statusTimings.textContent = "Applying smart, cinematic pacing…";
        statusTimings.className = "status-text";
      }

      showLoader("Applying smart pacing…");
      setButtonLoading(btnTimingsSmart, spinTimingsSmart, true);

      try {
        await postJSON("/api/timings", { smart: true });
        if (statusTimings) {
          statusTimings.textContent = "Smart, cinematic pacing applied.";
          statusTimings.classList.add("success");
          markStepDone(7);
        }
        await refreshYamlPreview();
      } catch (err) {
        console.error(err);
        if (statusTimings) {
          statusTimings.textContent = "Error applying smart pacing.";
          statusTimings.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnTimingsSmart, spinTimingsSmart, false);
      }
    });
  }

  // ============================================
  // STEP 5: EXPORT (with timing summary update)
  // ============================================
  if (btnExport) {
    btnExport.addEventListener("click", async () => {
      resetProcessingLog("Starting export...");

      const mode =
        exportOptimizedToggle && exportOptimizedToggle.checked
          ? "optimized"
          : "standard";

      const summaryBox = document.getElementById("timing-summary");
      if (summaryBox) {
        summaryBox.innerHTML = `⏳ Export started in ${
          mode === "optimized" ? "Optimized" : "Standard"
        } mode…`;
      }

      if (statusExport) {
        statusExport.textContent =
          mode === "optimized"
            ? "Rendering video (optimized quality)…"
            : "Rendering video (standard quality)…";
        statusExport.className = "status-text";
      }

      const start = performance.now();

      showLoader("Exporting final TikTok…");
      setButtonLoading(btnExport, spinExport, true);

      try {
        const res = await postJSON("/api/export", { mode });

        const end = performance.now();
        const seconds = ((end - start) / 1000).toFixed(1);

        if (statusExport) {
          statusExport.textContent = `Export complete → ${res.output}`;
          statusExport.classList.add("success");
          markStepDone(4);
        }

        if (summaryBox) {
          summaryBox.innerHTML = `
            ✅ Export finished<br>
            🕒 Duration: <strong>${seconds}s</strong><br>
            🎞 Mode: <strong>${mode}</strong><br>
            📁 File: ${res.output}
          `;
        }
      } catch (err) {
        console.error(err);

        if (statusExport) {
          statusExport.textContent = "Export failed. Check logs.";
          statusExport.classList.add("error");
        }

        if (summaryBox) {
          summaryBox.innerHTML = `
            ❌ Export failed<br>
            Error: ${err.message}
          `;
        }
      } finally {
        hideLoader();
        setButtonLoading(btnExport, spinExport, false);
      }
    });
  }

  // ============================================
  // LLM CHAT PANEL
  // ============================================
  if (chatForm && chatMessages && chatSendBtn) {
    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = (chatInput.value || "").trim();
      if (!msg) return;

      chatMessages.innerHTML += `
        <div class="chat-message user">
          <div class="bubble">${msg}</div>
        </div>
      `;
      chatInput.value = "";
      chatMessages.scrollTop = chatMessages.scrollHeight;

      setButtonLoading(chatSendBtn, spinChat, true);

      try {
        const res = await postJSON("/api/chat", { message: msg });
        const reply = res.reply || "(No response)";
        chatMessages.innerHTML += `
          <div class="chat-message assistant">
            <div class="bubble">${reply}</div>
          </div>
        `;
        chatMessages.scrollTop = chatMessages.scrollHeight;
      } catch (err) {
        console.error(err);
        chatMessages.innerHTML += `
          <div class="chat-message assistant">
            <div class="bubble">(Error talking to assistant.)</div>
          </div>
        `;
        chatMessages.scrollTop = chatMessages.scrollHeight;
      } finally {
        setButtonLoading(chatSendBtn, spinChat, false);
      }
    });
  }
});
