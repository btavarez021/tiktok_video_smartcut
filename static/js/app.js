// static/js/app.js
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

  // YAML editor
  const yamlEditor = document.getElementById("yaml-editor");
  const btnSaveYaml = document.getElementById("btn-save-yaml");
  const statusSaveYaml = document.getElementById("status-save-yaml");

  // Step 3: TTS
  const ttsEnabled = document.getElementById("tts-enabled");
  const ttsVoice = document.getElementById("tts-voice");
  const btnApplyTts = document.getElementById("btn-apply-tts");
  const spinTts = document.getElementById("spinner-tts");
  const statusTts = document.getElementById("status-tts");
  const btnSyncTts = document.getElementById("btn-sync-tts");

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

  // Upload
  const uploadInput = document.getElementById("upload-input");
  const btnUpload = document.getElementById("btn-upload");
  const statusUpload = document.getElementById("status-upload");

  // Processing log panel
  const liveLogBox = document.getElementById("live-log");

  const analyzeOverlay = document.getElementById("analyze-overlay");

  // ============================================
  // GENERIC HELPERS
  // ============================================

  function updateProcessingLog(lines) {
    if (!liveLogBox) return;
    liveLogBox.textContent = (lines || []).join("\n");
    liveLogBox.scrollTop = liveLogBox.scrollHeight;
  }

  function showAnalyzeOverlay() {
    if (analyzeOverlay) analyzeOverlay.classList.remove("hidden");
  }

  function hideAnalyzeOverlay() {
    if (analyzeOverlay) analyzeOverlay.classList.add("hidden");
  }

  function setStepsLocked(isLocked) {
    const allSelectors = [
      "#btn-generate-yaml",
      "#btn-refresh-config",
      "#btn-save-yaml",
      "#btn-save-captions",
      "#btn-apply-tts",
      "#btn-sync-tts",
      "#btn-apply-cta",
      "#btn-apply-fgscale",
      "#btn-timings-fixc",
      "#btn-timings-smart",
      "#btn-export",
      ".btn.chip"
    ];

    allSelectors.forEach(sel => {
      document.querySelectorAll(sel).forEach(el => {
        el.disabled = isLocked;
        if (isLocked) el.classList.add("disabled-ui");
        else el.classList.remove("disabled-ui");
      });
    });
  }

  // ============================================
  // LIVE LOG POLLING
  // ============================================
  let analyzePollInterval = null;

  function startAnalyzeStatusPolling() {
    if (analyzePollInterval) clearInterval(analyzePollInterval);

    analyzePollInterval = setInterval(async () => {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        updateProcessingLog(data.log);

        const stamp = document.getElementById("log-timestamp");
        if (stamp) {
          const now = new Date();
          stamp.textContent = `Last updated: ${now.toLocaleTimeString()}`;
        }

      } catch (e) {
        console.warn("Status polling stopped.", e);
        clearInterval(analyzePollInterval);
      }
    }, 1500);
  }

  // ============================================
  // STABLE COMPLETION WATCHER (NO SPINNING)
  // ============================================
  let lastCount = 0;
  let stablePolls = 0;

  async function watchForAnalysisCompletion() {
    const poll = setInterval(async () => {
      try {
        const res = await fetch("/api/analyses_cache", { method: "GET" });
        if (!res.ok) return;

        const data = await res.json();
        const count = Object.keys(data || {}).length;

        // ✅ Require at least one result
        if (count > 0) {

          // ✅ Detect stability (no new analyses)
          if (count === lastCount) {
            stablePolls++;
          } else {
            stablePolls = 0;
          }

          lastCount = count;

          // ✅ After ~3 stable polls (~4.5 seconds), we are done
          if (stablePolls >= 3) {
            clearInterval(poll);

            if (analyzePollInterval) {
              clearInterval(analyzePollInterval);
              analyzePollInterval = null;
            }

            // ✅ Stop spinner
            spinAnalyze.classList.remove("active");

            // ✅ Update UI status
            statusAnalyze.textContent = `✅ Analysis complete (${count} videos)`;
            statusAnalyze.classList.remove("working");
            statusAnalyze.classList.add("success");

            // ✅ Show all results
            if (analysisList) {
              analysisList.innerHTML = Object.entries(data)
                .map(([file, desc]) =>
                  `<div class="analysis-item"><strong>${file}</strong><br>${desc}</div>`
                )
                .join("");
            }

            // ✅ Mark Step 1 complete
            markStepDone(0);

            // ✅ Unlock rest of UI
            setStepsLocked(false);

            // ✅ Hide overlay
            if (typeof hideAnalyzeOverlay === "function") hideAnalyzeOverlay();

            return;
          }
        }

      } catch (err) {
        console.warn("Analysis polling stopped.", err);
        clearInterval(poll);
      }
    }, 1500);
  }

  // ============================================
  // STEP 1: ANALYZE BUTTON HANDLER
  // ============================================
  if (btnAnalyze) {
    btnAnalyze.addEventListener("click", async () => {
      resetProcessingLog();
      showAnalyzeOverlay();
      setStepsLocked(true);

      if (statusAnalyze) {
        statusAnalyze.textContent =
          "Analyzing videos… this will take 1–3 minutes.";
        statusAnalyze.className = "status-text working";
      }

      if (analysisList) analysisList.innerHTML = "";

      // ✅ Start spinner
      spinAnalyze.classList.add("active");
      btnAnalyze.disabled = true;

      // ✅ Reset stability counters
      lastCount = 0;
      stablePolls = 0;

      try {
        await postJSON("/api/analyze", {});

        // ✅ Begin polling loops
        startAnalyzeStatusPolling();
        watchForAnalysisCompletion();

      } catch (err) {
        console.error(err);
        statusAnalyze.textContent = "Error during analysis. Check logs.";
        statusAnalyze.classList.add("error");
        setStepsLocked(false);
        btnAnalyze.disabled = false;
        spinAnalyze.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 2: GENERATE YAML
  // ============================================
  if (btnGenerateYaml) {
    btnGenerateYaml.addEventListener("click", async () => {
      spinYaml.classList.add("active");
      statusYaml.textContent = "Generating storyboard YAML...";
      statusYaml.classList.remove("success", "error");

      try {
        const data = await postJSON("/api/generate_yaml", {});
        yamlEditor.value = data.yaml || "";
        statusYaml.textContent = "✅ YAML generated";
        statusYaml.classList.add("success");
        markStepDone(1);
      } catch (err) {
        console.error(err);
        statusYaml.textContent = "❌ Error generating YAML";
        statusYaml.classList.add("error");
      } finally {
        spinYaml.classList.remove("active");
      }
    });
  }

  // ============================================
  // SAVE YAML
  // ============================================
  if (btnSaveYaml) {
    btnSaveYaml.addEventListener("click", async () => {
      spinYaml.classList.add("active");
      statusSaveYaml.textContent = "Saving YAML...";
      statusSaveYaml.classList.remove("success", "error");

      try {
        await postJSON("/api/save_yaml", { yaml: yamlEditor.value });
        statusSaveYaml.textContent = "✅ YAML saved";
        statusSaveYaml.classList.add("success");
      } catch (err) {
        statusSaveYaml.textContent = "❌ Error saving YAML";
        statusSaveYaml.classList.add("error");
      } finally {
        spinYaml.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 3: APPLY TTS
  // ============================================
  if (btnApplyTts) {
    btnApplyTts.addEventListener("click", async () => {
      spinTts.classList.add("active");
      statusTts.textContent = "Applying TTS settings...";
      statusTts.classList.remove("success", "error");

      try {
        await postJSON("/api/apply_tts", {
          enabled: ttsEnabled.checked,
          voice: ttsVoice.value
        });

        statusTts.textContent = "✅ TTS applied";
        statusTts.classList.add("success");

      } catch (err) {
        statusTts.textContent = "❌ Error applying TTS";
        statusTts.classList.add("error");
      } finally {
        spinTts.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 3: APPLY CTA
  // ============================================
  if (btnApplyCta) {
    btnApplyCta.addEventListener("click", async () => {
      spinCta.classList.add("active");
      statusCta.textContent = "Applying CTA...";
      statusCta.classList.remove("success", "error");

      try {
        await postJSON("/api/apply_cta", {
          enabled: ctaEnabled.checked,
          text: ctaText.value,
          voiceover: ctaVoiceover.value
        });

        statusCta.textContent = "✅ CTA applied";
        statusCta.classList.add("success");

      } catch (err) {
        statusCta.textContent = "❌ Error applying CTA";
        statusCta.classList.add("error");
      } finally {
        spinCta.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 3: APPLY FG SCALE
  // ============================================
  if (btnApplyFg) {
    btnApplyFg.addEventListener("click", async () => {
      spinFg.classList.add("active");
      statusFg.textContent = "Applying foreground scale...";
      statusFg.classList.remove("success", "error");

      try {
        await postJSON("/api/apply_fgscale", {
          scale: fgSlider.value
        });

        statusFg.textContent = "✅ Foreground scale applied";
        statusFg.classList.add("success");

      } catch (err) {
        statusFg.textContent = "❌ Error applying FG scale";
        statusFg.classList.add("error");
      } finally {
        spinFg.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 4: TIMINGS
  // ============================================
  if (btnTimingsSmart) {
    btnTimingsSmart.addEventListener("click", async () => {
      spinTimingsSmart.classList.add("active");
      statusTimings.textContent = "Smart timing in progress...";
      statusTimings.classList.remove("success", "error");

      try {
        await postJSON("/api/timings_smart", {});
        statusTimings.textContent = "✅ Timings optimized";
        statusTimings.classList.add("success");

      } catch (err) {
        statusTimings.textContent = "❌ Error adjusting timings";
        statusTimings.classList.add("error");
      } finally {
        spinTimingsSmart.classList.remove("active");
      }
    });
  }

  // ============================================
  // STEP 5: EXPORT FINAL VIDEO
  // ============================================
  if (btnExport) {
    btnExport.addEventListener("click", async () => {
      spinExport.classList.add("active");
      statusExport.textContent = "Exporting final video...";
      statusExport.classList.remove("success", "error");

      try {
        const data = await postJSON("/api/export", {
          optimized: exportOptimizedToggle.checked
        });

        statusExport.textContent = "✅ Export complete";
        statusExport.classList.add("success");

        if (data.url) {
          window.open(data.url, "_blank");
        }

      } catch (err) {
        statusExport.textContent = "❌ Export failed";
        statusExport.classList.add("error");
      } finally {
        spinExport.classList.remove("active");
      }
    });
  }

  // ============================================
  // CHAT
  // ============================================
  if (chatForm) {
    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = chatInput.value.trim();
      if (!msg) return;

      chatSendBtn.disabled = true;
      spinChat.classList.add("active");

      try {
        const data = await postJSON("/api/chat", { message: msg });

        const div = document.createElement("div");
        div.className = "chat-message assistant";
        div.textContent = data.reply;
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;

      } catch (err) {
        console.error(err);
      } finally {
        chatSendBtn.disabled = false;
        spinChat.classList.remove("active");
        chatInput.value = "";
      }
    });
  }

  // ============================================
  // UPLOAD
  // ============================================
  if (btnUpload) {
    btnUpload.addEventListener("click", async () => {
      const file = uploadInput.files[0];
      if (!file) return;

      statusUpload.textContent = "Uploading...";
      statusUpload.classList.remove("success", "error");

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/api/upload", {
          method: "POST",
          body: formData
        });
        const data = await res.json();

        statusUpload.textContent = "✅ Uploaded";
        statusUpload.classList.add("success");

      } catch (err) {
        statusUpload.textContent = "❌ Upload failed";
        statusUpload.classList.add("error");
      }
    });
  }

  // ============================================
  // RESET LOG
  // ============================================
  function resetProcessingLog() {
    updateProcessingLog([]);
    const stamp = document.getElementById("log-timestamp");
    if (stamp) stamp.textContent = "";
  }

  // ============================================
  // UTIL: POST JSON
  // ============================================
  async function postJSON(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return res.json();
  }

  // ============================================
  // STEP MARKING
  // ============================================
  function markStepDone(stepIndex) {
    const steps = document.querySelectorAll(".step");
    if (steps[stepIndex]) steps[stepIndex].classList.add("done");
  }

});