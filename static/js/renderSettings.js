async function applyOverlay() {
  setUiBusy(true);
  console.log("APPLY OVERLAY CLICKED");

  const styleSel = document.getElementById("overlayStyle");
  const statusEl = document.getElementById("overlayStatus");
  if (!styleSel || !statusEl) return;

  const style = styleSel.value || "travel_blog";

  const rewriteMode =
    document.querySelector('input[name="captionRewriteMode"]:checked')?.value || "visual";

  setStatus(
    "overlayStatus",
    rewriteMode === "rewrite"
      ? "Applying overlay + rewriting captions…"
      : "Applying visual overlay only…",
    "working",
    false
  );

  try {
    const res = await jsonFetch("/api/overlay", {
      method: "POST",
      body: JSON.stringify({
        style,
        session: getActiveSession(),
        rewrite: rewriteMode === "rewrite",
      }),
    });

    // ======================================
    // ⚠ Hook weak / warning → must HARD RESET
    // ======================================
    if (res.status === "warning") {
      console.warn("Overlay blocked:", res.message);

      rewritePending = false;
      clearPendingRewrite();
      exitRewriteReviewMode();

      captionViewMode = "rewritten";
      renderCaptionView();
      syncCaptionToggleUI();

      setStatus(
        "overlayStatus",
        res.message || "Hook too weak to safely rewrite captions.",
        "warning"
      );
      return;
    }

    // ======================================
    // 🧠 Rewrite proposal path
    // ======================================
    if (res.status === "proposed") {
    // 🔥 Always unlock for a new proposal
    rewriteCommitted = false;

    proposeRewrite(res.proposed, "Overlay rewrite ready", "step4")
    return;
    }


    // ======================================
    // 🎨 Visual-only overlay path
    // ======================================
    await loadConfigAndYaml();
    await loadCaptionsFromYaml(); // updates lastSavedCaptionsText
    await previewOverlay("fast");
    await refreshAfterChange();



    workingCaptionsText = lastSavedCaptionsText;
    captionViewMode = "rewritten";
    renderCaptionView();
    syncCaptionToggleUI();

    diffDirty = false;

    setStatus("overlayStatus", "Overlay applied ✓", "success");

  } catch (err) {
    console.error(err);
    setStatus("overlayStatus", "Failed to apply overlay.", "error");
  }
  finally{
    setUiBusy(false);
  }
}

// Timings
async function applyTiming(smart) {
    const statusEl = document.getElementById("timingStatus");
    if (!statusEl) return;

    setStatus(
        "timingStatus",
        smart
            ? "Applying cinematic smart timings…"
            : "Applying standard timing…",
        "info"
    );

    try {
        await jsonFetch("/api/timings", {
            method: "POST",
            body: JSON.stringify({
                smart,
                session: getActiveSession(),
            }),
        });
        setStatus("timingStatus", "Timings updated.", "success", true);
        await loadConfigAndYaml();
        await refreshAfterChange();

    } catch (err) {
        console.error(err);
        setStatus(
            "timingStatus",
            `Error adjusting timings: ${err.message}`,
            "error"
        );
    }
}

// ===============================
  // 🔥 Overlay Preview System
  // ===============================
  async function previewOverlay(mode = "fast") {

      if (!document.getElementById("overlayPreviewBox")) return;

      const session = getActiveSession();
      const box = document.getElementById("overlayPreviewBox");
      if (!box) return;

      box.innerHTML = "⏳ generating preview…";

      try {
          let res;

          if (mode === "fast") {
              res = await jsonFetch("/api/overlay_preview", {
                  method: "POST",
                  body: JSON.stringify({
                      session,
                      style: getOverlayStyle()
                  }),
              });
          } else {
      // Full preview is STILL READ-ONLY
      // It just asks for a higher-quality preview image

      res = await jsonFetch("/api/overlay_preview", {
          method: "POST",
          body: JSON.stringify({
              session,
              style: getOverlayStyle(),
              quality: "full"   // optional hint to backend
          }),
      });
  }

          if (res?.image) {
              box.innerHTML = "";
              const img = document.createElement("img");
              img.src = res.image;
              img.style.width = "100%";
              img.style.height = "100%";
              img.style.objectFit = "cover";
              img.style.position = "absolute";
              img.style.zIndex = "3";

              box.appendChild(img);
          } else {
              box.innerHTML = "⚠ No preview returned.";
          }
      } catch (e) {
          console.error(e);
          box.innerHTML = "❌ Preview failed — check logs.";
      }
  }


  // Alias used by caption system
async function refreshOverlayPreview() {
  return previewOverlay("fast");
}

// ================================
  // OVERLAY STYLE — Save (SAFE)
  // ================================
  async function saveOverlayStyle({ silent = false } = {}) {
      const selectEl = document.getElementById("overlayStyle");
      const statusEl = document.getElementById("overlayStyleStatus");

      if (!selectEl || !statusEl) return;

      const style = selectEl.value;

      try {
          const session = encodeURIComponent(getActiveSession());
          const data = await getConfigCached();
          const cfg = data.config || {};

          cfg.render = cfg.render || {};
          cfg.render.overlay_style = style;

          await jsonFetch("/api/save_config", {
              method: "POST",
              body: JSON.stringify({
                  session: getActiveSession(),
                  config: cfg
              })
          });
          CONFIG_CACHE = null;

          // 🔑 THIS IS THE FIX
          await loadConfigAndYaml();

          if (!silent) {
              setStatus("overlayStyleStatus", "Style saved ✓", "success");
          } else {
              showAutoSaveStatus("overlayStyleStatus");
          }
          

      } catch (err) {
          console.error(err);
          setStatus("overlayStyleStatus", "Failed to save style", "error");
      }
  }

  // TTS
async function saveTtsSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("ttsEnabled");
    const voiceEl = document.getElementById("ttsVoice");
    const statusEl = document.getElementById("ttsStatus");

    if (!enabledEl || !voiceEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const voice = voiceEl.value;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.tts = {
            enabled,
            voice
        };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


        // ✅ feedback
        if (!silent) {
            setStatus("ttsStatus", "TTS saved ✓", "success");
        } else {
            showAutoSaveStatus("ttsStatus");
        }

        // ✅ THIS IS THE KEY LINE
        await loadConfigAndYaml();   // refresh preview + parsed YAML

    } catch (err) {
        console.error(err);
        setStatus("ttsStatus", "Failed to save TTS", "error");
    }
}

// CTA

async function saveCtaSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("ctaEnabled");
    const textEl = document.getElementById("ctaText");
    const voiceEl = document.getElementById("ctaVoiceover");
    const statusEl = document.getElementById("ctaStatus");

    if (!enabledEl || !textEl || !voiceEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const text = textEl.value.trim();
    const voiceover = voiceEl.checked;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.cta = { enabled, text, voiceover };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


        if (!silent) {
            setStatus("ctaStatus", "CTA saved ✓", "success");
        }

        await loadConfigAndYaml();
        
    } catch (err) {
        console.error(err);
        setStatus("ctaStatus", "Failed to save CTA", "error");
    }
}




// Music: load available tracks (global)
async function loadMusicTracks() {
    const sel = document.getElementById("musicFile");
    if (!sel) return;

    sel.innerHTML = `<option value="">– No music –</option>`;

    try {
        const data = await jsonFetch("/api/music_list");
        const files = data.files || [];
        files.forEach((f) => {
            const opt = document.createElement("option");
            opt.value = f;
            opt.textContent = f;
            sel.appendChild(opt);
        });
    } catch (err) {
        console.error("Music list load failed", err);
    }
}

// Music: read settings from YAML
async function loadMusicSettingsFromYaml() {
    const enabledEl = document.getElementById("musicEnabled");
    const fileEl = document.getElementById("musicFile");
    const volEl = document.getElementById("musicVolume");
    const volLbl = document.getElementById("musicVolumeLabel");
    if (!enabledEl || !fileEl || !volEl || !volLbl) return;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};

        const music = cfg.music || {};
        const render = cfg.render || {};

        const enabled =
            music.enabled ??
            render.music_enabled ??
            false;

        const file =
            music.file ??
            render.music_file ??
            "";

        const volume =
            music.volume ??
            render.music_volume ??
            0.25;

        enabledEl.checked = !!enabled;
        fileEl.value = file;
        volEl.value = volume;
        volLbl.textContent = Number(volume).toFixed(2);
    } catch (err) {
        console.error("Music settings load failed", err);
    }
}

// Music: save settings into YAML
async function saveMusicSettings({ silent = false } = {}) {
    const enabledEl = document.getElementById("musicEnabled");
    const fileEl = document.getElementById("musicFile");
    const volEl = document.getElementById("musicVolume");
    const statusEl = document.getElementById("musicStatus");

    if (!enabledEl || !fileEl || !volEl || !statusEl) return;

    const enabled = enabledEl.checked;
    const file = fileEl.value || "";
    const volume = parseFloat(volEl.value || "0.25");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.music = { enabled, file, volume };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


        // ✅ THIS IS THE MISSING PIECE
        await loadConfigAndYaml();

        if (!silent) {
            setStatus("musicStatus", "Music saved ✓", "success");
        } else {
            showAutoSaveStatus("musicStatus");
        }

    } catch (err) {
        console.error(err);
        setStatus("musicStatus", "Failed to save music", "error");
    }
}


// Music volume label live update
function initMusicVolumeSlider() {
    const slider = document.getElementById("musicVolume");
    const lbl = document.getElementById("musicVolumeLabel");
    if (!slider || !lbl) return;

    slider.addEventListener("input", () => {
        lbl.textContent = Number(slider.value).toFixed(2);
    });
}

function getRewriteMode(){
  return document.querySelector('input[name="captionRewriteMode"]:checked')?.value || "visual";
}

function getCurrentCaptionsText() {
  return (workingCaptionsText || lastSavedCaptionsText || "").trim();
}



function getCaptionMode(){
  return document.getElementById("captionModeSelect")?.value || "all";
}

async function loadCaptionMode() {
  try {
    const data = await getConfigCached(); 
    const mode = data.config?.render?.captions_mode || "all";

    const select = document.getElementById("captionModeSelect");
    if (select) {
      select.value = mode;
    }

  } catch (err) {
    console.error("Failed to load caption mode", err);
  }
}


async function saveCaptionMode() {
    const mode = getCaptionMode();
    const session = getActiveSession();

    setStatus("captionModeStatus", "Saving...", "working", false);

    try {
        const resp = await fetch("/api/captions_mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session, mode })
        });

        const data = await resp.json();

        if (data.status !== "ok") {
            setStatus("captionModeStatus", data.error || "Error saving", "error");
            return;
        }

        const savedMode = data.captions_mode || mode;

        const select = document.getElementById("captionModeSelect");
        if (select) {
            select.value = savedMode;
        }

        CONFIG_CACHE = null;

        await loadConfigAndYaml();

        setStatus(
            "captionModeStatus",
            `Saved → ${savedMode}`,
            "success"
        );

        // await refreshAfterChange();
        // refreshAnalyses?.();
        // await loadCaptionMode();

    } catch (err) {
        console.error(err);
        setStatus("captionModeStatus", "Save failed", "error");
    }
}

// Layout Mode (TikTok / Classic)
async function loadLayoutFromYaml() {
    const sel = document.getElementById("layoutMode");
    if (!sel) return;

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};
        const render = cfg.render || {};

        const mode = render.layout_mode || "tiktok";
        sel.value = mode;
    } catch (err) {
        console.error("Failed loading layout mode", err);
    }
}

async function saveLayoutMode() {
    const sel = document.getElementById("layoutMode");
    const status = document.getElementById("layoutStatus");
    if (!sel || !status) return;

    const mode = sel.value || "tiktok";
    setStatus("layoutStatus", "Saving layout mode…", "working", false);

    try {
        await jsonFetch("/api/layout", {
            method: "POST",
            body: JSON.stringify({
                mode,
                session: getActiveSession(),
            }),
        });

        setStatus("layoutStatus", "Layout saved!", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("layoutStatus", "Error saving layout: " + err.message, "error");
    }
}

// Auto Caption Style Selector
function autoSelectCaptionStyle(selectedMode) {
    const layoutSelect = document.getElementById("layoutMode");
    if (!layoutSelect) return;

    const isTikTok = selectedMode === "standard";

    layoutSelect.value = isTikTok ? "tiktok" : "classic";

    setStatus(
        "captionStyleStatus",
        isTikTok
            ? "🟣 Auto-set caption layout to TikTok Style"
            : "🔵 Auto-set caption layout to Classic Style",
        "info"
    );
}

// Preview music
function initMusicPreview() {
    const btn = document.getElementById("musicPreviewBtn");
    const select = document.getElementById("musicFile");
    const status = document.getElementById("musicStatus");

    if (!btn || !select || !status) return;

    select.addEventListener("change", () => {
        if (previewAudio) {
            previewAudio.pause();
            previewAudio.currentTime = 0;
        }
        previewAudio = null;
        previewPlaying = false;
        btn.textContent = "▶ Preview";
        status.textContent = "";
    });

    btn.addEventListener("click", () => {
        const file = select.value;

        if (!file) {
            alert("Select a music track first.");
            return;
        }

        if (!previewAudio) {
            previewAudio = new Audio(`/api/music_file/${file}`);
            previewAudio.volume = 0.8;

            previewAudio.onplay = () => {
                previewPlaying = true;
                btn.textContent = "⏸ Pause";
                status.textContent = `🎵 Now Playing: ${file}`;
            };

            previewAudio.onpause = () => {
                previewPlaying = false;
                btn.textContent = "▶ Preview";
                status.textContent = `⏸ Paused: ${file}`;
            };

            previewAudio.onended = () => {
                previewPlaying = false;
                btn.textContent = "▶ Preview";
                status.textContent = "";
            };
        }

        if (previewAudio.paused) {
            previewAudio.play();
        } else {
            previewAudio.pause();
        }
    });
}

// ================================
// Foreground Scale — Save (supports silent)
// ================================
async function saveFgScale({ silent = false } = {}) {
    const autoEl = document.getElementById("autoFgScale");
    const scaleEl = document.getElementById("fgScale");
    const statusEl = document.getElementById("fgStatus");

    if (!autoEl || !scaleEl || !statusEl) return;

    const auto = autoEl.checked;
    const scale = parseFloat(scaleEl.value || "1.0");

    try {
        const session = encodeURIComponent(getActiveSession());
        const data = await getConfigCached();
        const cfg = data.config || {};

        cfg.foreground_scale = {
            auto,
            scale
        };

        await jsonFetch("/api/save_config", {
            method: "POST",
            body: JSON.stringify({
                session: getActiveSession(),
                config: cfg
            })
        });

        CONFIG_CACHE = null;


        if (!silent) {
            setStatus("fgStatus", "Foreground scale saved ✓", "success");
        } else {
            showAutoSaveStatus("fgStatus");
        }

        await loadConfigAndYaml();


    } catch (err) {
        console.error(err);
        setStatus("fgStatus", "Failed to save foreground scale", "error");
    }
}


function initFgScaleSlider() {
    const range = document.getElementById("fgScale");
    const label = document.getElementById("fgScaleValue");
    if (!range || !label) return;

    label.textContent = range.value;
    range.addEventListener("input", () => {
        label.textContent = range.value;
    });
}

function initFgScaleUI() {
    const autoFgScaleEl = document.getElementById("autoFgScale");
    const manualFgContainer = document.getElementById("manualFgScaleContainer");
    const fgScaleEl = document.getElementById("fgScale");
    const fgScaleValue = document.getElementById("fgScaleValue");

    if (!autoFgScaleEl || !manualFgContainer || !fgScaleEl || !fgScaleValue) {
        return;
    }

    function updateFgScaleUI() {
        if (autoFgScaleEl.checked) {
            manualFgContainer.classList.add("hidden");
        } else {
            manualFgContainer.classList.remove("hidden");
        }
    }

    autoFgScaleEl.addEventListener("change", updateFgScaleUI);

    fgScaleEl.addEventListener("input", () => {
        fgScaleValue.textContent = fgScaleEl.value;
    });

    updateFgScaleUI();
}

  function syncTtsUIState() {
    const enabled = document.getElementById("ttsEnabled")?.checked;
    const voiceSelect = document.getElementById("ttsVoice");

    if (!voiceSelect) return;

    voiceSelect.disabled = !enabled;
    voiceSelect.style.opacity = enabled ? "1" : "0.5";
  }

function syncFgScaleUI() {
      const autoEl = document.getElementById("autoFgScale");
      const manualContainer = document.getElementById("manualFgScaleContainer");

      if (!autoEl || !manualContainer) return;

      manualContainer.style.display = autoEl.checked ? "none" : "block";
  }

function syncMusicUIState() {
    const enabled = document.getElementById("musicEnabled")?.checked;
    const hint = document.getElementById("musicDisabledHint");

    if (!hint) return;

    hint.style.display = enabled ? "none" : "block";
  }

function syncCtaUIState() {
      const enabled = document.getElementById("ctaEnabled")?.checked;
      const textEl = document.getElementById("ctaText");
      const voiceEl = document.getElementById("ctaVoiceover");
      const rowEl = document.getElementById("ctaRow");

      if (!textEl || !voiceEl || !rowEl) return;

      textEl.disabled = !enabled;
      voiceEl.disabled = !enabled;

      rowEl.style.opacity = enabled ? "1" : "0.5";
  }

function getOverlayStyle() {
      return (document.getElementById("overlayStyle")?.value || "ai_recommended").toLowerCase();
  }

let renderSettingsInitialized = false;

function initRenderSettingsListeners() {
  if (renderSettingsInitialized) return;
  renderSettingsInitialized = true;

  const qs = (id) => document.getElementById(id);

  // Buttons
  qs("applyOverlayBtn")?.addEventListener("click", applyOverlay);
  qs("applyStandardTimingBtn")?.addEventListener("click", () => applyTiming(false));
  qs("applyCinematicTimingBtn")?.addEventListener("click", () => applyTiming(true));

  qs("previewFast")?.addEventListener("click", () => previewOverlay("fast"));
  qs("previewFull")?.addEventListener("click", () => previewOverlay("full"));
  qs("previewStyleBtn")?.addEventListener("click", () => previewOverlay("fast"));

  qs("previewRewriteBtn")?.addEventListener("click", () => {
    if (typeof previewRewrite === "function") previewRewrite();
  });

  // Overlay style
  qs("overlayStyle")?.addEventListener("change", async () => {
    if (suppressNextPreview) {
      suppressNextPreview = false;
      return;
    }

    await saveOverlayStyle({ silent: true });
    showAutoSaveStatus("overlayStyleStatus");
    await previewOverlay("fast");
  });

  // CTA
  qs("ctaEnabled")?.addEventListener("change", async () => {
    syncCtaUIState();
    await saveCtaSettings({ silent: true });
    flashElement(qs("ctaRow"));
    showAutoSaveStatus("ctaStatus");
  });

  qs("ctaVoiceover")?.addEventListener("change", async () => {
    await saveCtaSettings({ silent: true });
    showAutoSaveStatus("ctaStatus");
  });

  let ctaTimer = null;
  qs("ctaText")?.addEventListener("input", () => {
    clearTimeout(ctaTimer);
    ctaTimer = setTimeout(async () => {
      await saveCtaSettings({ silent: true });
      flashElement(qs("ctaRow"));
      showAutoSaveStatus("ctaStatus");
    }, 400);
  });

  // TTS
  qs("ttsEnabled")?.addEventListener("change", () => {
    syncTtsUIState();
    saveTtsSettings({ silent: true });
  });

  qs("ttsVoice")?.addEventListener("change", () => {
    saveTtsSettings({ silent: true });
  });

  // Music
  qs("musicEnabled")?.addEventListener("change", () => {
    saveMusicSettings({ silent: true });
  });

  qs("musicFile")?.addEventListener("change", () => {
    saveMusicSettings({ silent: true });
  });

  let musicTimer = null;
  qs("musicVolume")?.addEventListener("input", () => {
    clearTimeout(musicTimer);
    musicTimer = setTimeout(() => {
      saveMusicSettings({ silent: true });
    }, 300);
  });

  // Layout + caption mode
  qs("layoutMode")?.addEventListener("change", saveLayoutMode);
  qs("captionModeSelect")?.addEventListener("change", saveCaptionMode);

  // FG scale
  qs("autoFgScale")?.addEventListener("change", async () => {
    syncFgScaleUI();
    await saveFgScale({ silent: true });
  });

  let fgTimer = null;
  qs("fgScale")?.addEventListener("input", () => {
    clearTimeout(fgTimer);
    fgTimer = setTimeout(async () => {
      await saveFgScale({ silent: true });
    }, 300);
  });

  // Rewrite mode toggle
  document
    .querySelector('input[name="captionRewriteMode"][value="visual"]')
    ?.addEventListener("change", clearOverlayWarning);

  document
    .querySelector('input[name="captionRewriteMode"][value="rewrite"]')
    ?.addEventListener("change", refreshAfterChange);

  // Export mode → caption layout
  document.querySelectorAll('input[name="exportMode"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      autoSelectCaptionStyle(e.target.value);
    });
  });
}

async function initRenderSettingsBoot() {
  window.appState.settings = window.appState.settings || {};

  syncCtaUIState();
  syncTtsUIState();
  syncFgScaleUI();
  syncMusicUIState();

  initFgScaleSlider();
  initFgScaleUI();
  initMusicVolumeSlider();
  initMusicPreview();

  await loadMusicTracks();
  await loadMusicSettingsFromYaml();
  await loadLayoutFromYaml();
  await loadCaptionMode();
}