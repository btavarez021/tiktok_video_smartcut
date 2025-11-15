document.addEventListener("DOMContentLoaded", () => {
  // ---------------------------
  // Element references
  // ---------------------------
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
  const yamlPreview = document.getElementById("yaml-preview");
  const captionChips = document.querySelectorAll(".btn.chip");

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

  // Chat
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const chatSendBtn = document.getElementById("chat-send-btn");
  const spinChat = document.getElementById("spinner-chat");
  const chatMessages = document.getElementById("chat-messages");

  // ---------------------------
  // Helpers
  // ---------------------------
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

  async function refreshYamlPreview() {
    try {
      const data = await postJSON("/api/config", {});
      if (yamlPreview) {
        yamlPreview.textContent = data.yaml || "# Empty config.yml";
      }
    } catch (err) {
      if (yamlPreview) {
        yamlPreview.textContent = "# Error loading config.yml";
      }
      console.error(err);
    }
  }

  // Initialize YAML preview on page load
  refreshYamlPreview();

  // ---------------------------
  // Step 1: Analyze
  // ---------------------------
  if (btnAnalyze) {
    btnAnalyze.addEventListener("click", async () => {
      if (statusAnalyze) {
        statusAnalyze.textContent = "Analyzing videos in tik_tok_downloads/…";
        statusAnalyze.className = "status-text";
      }
      if (analysisList) {
        analysisList.innerHTML = "";
      }

      showLoader("Analyzing videos…");
      setButtonLoading(btnAnalyze, spinAnalyze, true);

      try {
        const data = await postJSON("/api/analyze", {});
        const count = Object.keys(data || {}).length;
        if (statusAnalyze) {
          statusAnalyze.textContent = `Found and analyzed ${count} video(s).`;
          statusAnalyze.classList.add("success");
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
          statusAnalyze.textContent = "Error during analysis. Check logs.";
          statusAnalyze.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnAnalyze, spinAnalyze, false);
      }
    });
  }

  // ---------------------------
  // Step 2: YAML generation
  // ---------------------------
  if (btnGenerateYaml) {
    btnGenerateYaml.addEventListener("click", async () => {
      if (statusYaml) {
        statusYaml.textContent = "Calling LLM to generate config.yml…";
        statusYaml.className = "status-text";
      }
      if (yamlPreview) {
        yamlPreview.textContent = "";
      }

      showLoader("Generating YAML storyboard…");
      setButtonLoading(btnGenerateYaml, spinYaml, true);

      try {
        await postJSON("/api/generate_yaml", {});
        if (statusYaml) {
          statusYaml.textContent = "YAML generated and saved to config.yml.";
          statusYaml.classList.add("success");
        }
        await refreshYamlPreview();
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
      if (statusYaml) {
        statusYaml.textContent = "Refreshing from config.yml…";
        statusYaml.className = "status-text";
      }
      showLoader("Refreshing config…");
      try {
        await refreshYamlPreview();
        if (statusYaml) {
          statusYaml.textContent = "Config reloaded from config.yml.";
          statusYaml.classList.add("success");
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

  // Caption style chips → /api/overlay
  if (captionChips && captionChips.length) {
    captionChips.forEach((chip) => {
      chip.addEventListener("click", async () => {
        const style = chip.dataset.style || "punchy";

        captionChips.forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");

        if (statusYaml) {
          statusYaml.textContent = `Applying “${style}” overlay style…`;
          statusYaml.className = "status-text";
        }

        showLoader("Updating captions via overlay…");
        try {
          await postJSON("/api/overlay", { style });
          if (statusYaml) {
            statusYaml.textContent = `Overlay style “${style}” applied.`;
            statusYaml.classList.add("success");
          }
          await refreshYamlPreview();
        } catch (err) {
          console.error(err);
          if (statusYaml) {
            statusYaml.textContent = "Error applying overlay style.";
            statusYaml.classList.add("error");
          }
        } finally {
          hideLoader();
        }
      });
    });
  }

  // ---------------------------
  // Step 3: TTS
  // ---------------------------
  if (btnApplyTts) {
    btnApplyTts.addEventListener("click", async () => {
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

  // ---------------------------
  // Step 3: CTA
  // ---------------------------
  if (btnApplyCta) {
    btnApplyCta.addEventListener("click", async () => {
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

  // ---------------------------
  // Step 3: Foreground scale
  // ---------------------------
  if (fgSlider && fgValue) {
    fgValue.textContent = parseFloat(fgSlider.value).toFixed(2);
    fgSlider.addEventListener("input", () => {
      fgValue.textContent = parseFloat(fgSlider.value).toFixed(2);
    });
  }

  if (btnApplyFg) {
    btnApplyFg.addEventListener("click", async () => {
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

  // ---------------------------
  // Step 4: Timings
  // ---------------------------
  if (btnTimingsFixc) {
    btnTimingsFixc.addEventListener("click", async () => {
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

  // ---------------------------
  // Step 5: Export
  // ---------------------------
  if (btnExport) {
    btnExport.addEventListener("click", async () => {
      if (statusExport) {
        statusExport.textContent = "Rendering video with current config…";
        statusExport.className = "status-text";
      }

      showLoader("Exporting final TikTok…");
      setButtonLoading(btnExport, spinExport, true);

      try {
        const res = await postJSON("/api/export", {});
        if (statusExport) {
          statusExport.textContent = `Export complete → ${res.output}`;
          statusExport.classList.add("success");
        }
      } catch (err) {
        console.error(err);
        if (statusExport) {
          statusExport.textContent = "Export failed. Check logs.";
          statusExport.classList.add("error");
        }
      } finally {
        hideLoader();
        setButtonLoading(btnExport, spinExport, false);
      }
    });
  }

  // ---------------------------
  // LLM Chat Panel
  // ---------------------------
  if (chatForm && chatMessages && chatSendBtn) {
    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = (chatInput.value || "").trim();
      if (!msg) return;

      // User bubble
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
