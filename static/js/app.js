
  // ================================
  // Variables
  // ================================

  let previewAudio = null;
  let previewPlaying = false;



  // ================================
  // Utility helpers
  // ================================

  // Universal colored status helper
  function setStatus(id, msg, type = "info") {
      const el = document.getElementById(id);
      if (!el) return;

      el.textContent = msg;
      el.classList.remove("status-info", "status-success", "status-error");

      if (type === "success") el.classList.add("status-success");
      else if (type === "error") el.classList.add("status-error");
      else el.classList.add("status-info");
  }


  // JSON fetch helper with sane defaults
  async function jsonFetch(url, options = {}) {
      const resp = await fetch(url, {
          headers: { "Content-Type": "application/json" },
          ...options,
      });

      if (!resp.ok) {
          const text = await resp.text();
          throw new Error(text || `Request failed: ${resp.status}`);
      }

      try {
          return await resp.json();
      } catch {
          return {};
      }
  }


  // Status hint helper (bottom style line)
  function showStatus(msg, type = "info") {
      const el = document.getElementById("styleStatus");
      if (!el) return;
      el.textContent = msg;
      el.className = "hint-text " + type;
  }

  // Simple download helper (works on mobile/desktop)
  function safeDownload(url, filename = "export.mp4") {
      const a = document.createElement("a");
      a.href = url;
      a.style.display = "none";
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
  }

  // ================================
  // Stepper behavior
  // ================================
  function initStepper() {
      const stepButtons = document.querySelectorAll(".stepper .step");

      stepButtons.forEach((btn) => {
          btn.addEventListener("click", () => {
              const targetSel = btn.dataset.target;
              const targetEl = document.querySelector(targetSel);
              if (targetEl) {
                  targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
              }
              stepButtons.forEach((b) => b.classList.remove("active"));
              btn.classList.add("active");
          });
      });

      const steps = Array.from(document.querySelectorAll(".step-card"));
      if (!steps.length) return;

      const observer = new IntersectionObserver(
          (entries) => {
              entries.forEach((entry) => {
                  if (entry.isIntersecting) {
                      const id = "#" + entry.target.id;
                      stepButtons.forEach((btn) => {
                          if (btn.dataset.target === id) {
                              stepButtons.forEach((b) => b.classList.remove("active"));
                              btn.classList.add("active");
                          }
                      });
                  }
              });
          },
          { threshold: 0.4 }
      );

      steps.forEach((s) => observer.observe(s));
  }

  // ================================
  // Status log polling
  // ================================
  let statusLogTimer = null;

  async function refreshStatusLog() {
      try {
          const data = await jsonFetch("/api/status");
          const log = data.status_log || [];
          const el = document.getElementById("statusLog");
          if (!el) return;
          el.textContent = log.join("\n");
          el.scrollTop = el.scrollHeight;
      } catch {
          // silent fail for logs
      }
  }

  function startStatusLogPolling() {
      if (statusLogTimer) clearInterval(statusLogTimer);
      refreshStatusLog();
      statusLogTimer = setInterval(refreshStatusLog, 2000);
  }

  // ================================
  // Upload: plain + drag & drop UI
  // ================================
  async function uploadFiles() {
      // Used if you call uploadFiles() from HTML onclick
      const input = document.getElementById("uploadFiles");
      const status = document.getElementById("uploadStatus");
      if (!input || !status) return;

      if (!input.files.length) {
          status.textContent = "❌ No files selected.";
          return;
      }

      const formData = new FormData();
      for (let f of input.files) {
          formData.append("files", f);
      }

      setStatus("uploadStatus", "⬆ Uploading…", "info");

      try {
          const resp = await fetch("/api/upload", {
              method: "POST",
              body: formData,
          });
          const data = await resp.json();
          if (data.uploaded?.length) {
              setStatus("uploadStatus", `✅ Uploaded ${data.uploaded.length} file(s).`, "success");
          } else {
              status.textContent = `⚠ No files uploaded (check logs).`;
          }
      } catch (err) {
          console.error(err);
          setStatus("uploadStatus", `❌ Upload failed: ${err.message}`, "error");
      }
  }

  function initUploadUI() {
      const dropZone = document.getElementById("dropZone");
      const fileInput = document.getElementById("uploadFiles");
      const preview = document.getElementById("uploadPreview");
      const uploadBtn = document.getElementById("uploadBtn");
      const progressWrapper = document.getElementById("uploadProgressWrapper");
      const progressBar = document.getElementById("uploadProgress");
      const statusEl = document.getElementById("uploadStatus");

      if (!dropZone || !fileInput || !preview || !uploadBtn || !progressWrapper || !progressBar || !statusEl) {
          return;
      }

      let selectedFiles = [];

      function updatePreview() {
          preview.innerHTML = "";
          selectedFiles.forEach((file, idx) => {
              const wrapper = document.createElement("div");
              wrapper.className = "preview-item";

              const name = document.createElement("div");
              name.className = "preview-name";
              name.textContent = file.name;

              const removeBtn = document.createElement("button");
              removeBtn.className = "preview-remove";
              removeBtn.innerHTML = "✖";

              removeBtn.onclick = () => {
                  selectedFiles.splice(idx, 1);
                  updatePreview();
              };

              wrapper.appendChild(name);
              wrapper.appendChild(removeBtn);
              preview.appendChild(wrapper);
          });

          uploadBtn.disabled = selectedFiles.length === 0;
      }

      dropZone.addEventListener("click", () => fileInput.click());

      fileInput.addEventListener("change", (e) => {
          selectedFiles = Array.from(e.target.files);
          updatePreview();
      });

      dropZone.addEventListener("dragover", (e) => {
          e.preventDefault();
          dropZone.classList.add("dragover");
      });

      dropZone.addEventListener("dragleave", () => {
          dropZone.classList.remove("dragover");
      });

      dropZone.addEventListener("drop", (e) => {
          e.preventDefault();
          dropZone.classList.remove("dragover");
          selectedFiles = Array.from(e.dataTransfer.files);
          updatePreview();
      });

      uploadBtn.addEventListener("click", () => {
          if (!selectedFiles.length) return;

          statusEl.textContent = "Uploading…";
          progressWrapper.classList.remove("hidden");
          progressBar.style.width = "0%";

          const formData = new FormData();
          selectedFiles.forEach((f) => formData.append("files", f));

          const xhr = new XMLHttpRequest();
          xhr.open("POST", "/api/upload");

          xhr.upload.onprogress = (e) => {
              if (e.lengthComputable) {
                  const pct = (e.loaded / e.total) * 100;
                  progressBar.style.width = pct.toFixed(1) + "%";
              }
          };

          xhr.onload = () => {
              if (xhr.status === 200) {
                  const resp = JSON.parse(xhr.responseText);
                  statusEl.textContent = `✅ Uploaded ${resp.uploaded?.length || 0} file(s).`;
                  progressBar.style.width = "100%";
              } else {
                  statusEl.textContent = `❌ Upload failed: ${xhr.statusText}`;
              }
          };

          xhr.onerror = () => {
              statusEl.textContent = "❌ Upload error.";
          };

          xhr.send(formData);
      });
  }

  // ================================
  // Step 1: Analysis
  // ================================
  async function analyzeClips() {
      const analyzeBtn = document.getElementById("analyzeBtn");
      const statusEl = document.getElementById("analyzeStatus");
      if (!analyzeBtn || !statusEl) return;

      analyzeBtn.disabled = true;
      setStatus("analyzeStatus", "Analyzing clips from S3… this can take a bit…", "info");

      try {
          const data = await jsonFetch("/api/analyze", {
              method: "POST",
              body: "{}",
          });
          const count = data.count ?? Object.keys(data || {}).length;
          setStatus("analyzeStatus", `Analysis complete. ${count} video(s).`, "success");
          await refreshAnalyses();
      } catch (err) {
          console.error(err);
          setStatus("analyzeStatus", `Error during analysis: ${err.message}`, "error");
      } finally {
          analyzeBtn.disabled = false;
      }
  }

  async function refreshAnalyses() {
      const listEl = document.getElementById("analysesList");
      if (!listEl) return;

      listEl.innerHTML = "";
      try {
          const data = await jsonFetch("/api/analyses_cache");
          const entries = Object.entries(data || {});
          if (!entries.length) {
              listEl.innerHTML =
                  '<li><span class="analysis-desc">No analyses found yet. Run "Analyze clips" first.</span></li>';
              return;
          }
          entries.forEach(([file, desc]) => {
              const li = document.createElement("li");
              const f = document.createElement("div");
              f.className = "analysis-file";
              f.textContent = file;
              const d = document.createElement("div");
              d.className = "analysis-desc";
              d.textContent = desc || "(no description)";
              li.appendChild(f);
              li.appendChild(d);
              listEl.appendChild(li);
          });
      } catch (err) {
          listEl.innerHTML = `<li><span class="analysis-desc">Error loading analyses: ${err.message}</span></li>`;
      }
  }

  // ================================
  // Step 2: YAML generation & config
  // ================================
  async function generateYaml() {
      const statusEl = document.getElementById("yamlStatus");
      if (!statusEl) return;
      setStatus("yamlStatus", "Calling LLM to build config.yml storyboard…", "info");
      try {
          await jsonFetch("/api/generate_yaml", {
              method: "POST",
              body: "{}",
          });
          setStatus("yamlStatus", "YAML generated!", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          setStatus("yamlStatus", `Error generating YAML: ${err.message}`, "error");
      }
  }

  async function loadConfigAndYaml() {
      const yamlTextEl = document.getElementById("yamlText");
      const yamlPreviewEl = document.getElementById("yamlPreview");
      if (!yamlTextEl || !yamlPreviewEl) return;

      try {
          const data = await jsonFetch("/api/config");
          yamlTextEl.value = data.yaml || "# No config.yml yet.";
          yamlPreviewEl.textContent = JSON.stringify(data.config || {}, null, 2);
      } catch (err) {
          yamlTextEl.value = "";
          yamlPreviewEl.textContent = `Error loading config: ${err.message}`;
      }
  }

  async function saveYaml() {
      const yamlTextEl = document.getElementById("yamlText");
      const statusEl = document.getElementById("yamlStatus");
      if (!yamlTextEl || !statusEl) return;

      const raw = yamlTextEl.value || "";
      setStatus("yamlStatus", "Saving YAML…", "info");

      try {
          await jsonFetch("/api/save_yaml", {
              method: "POST",
              body: JSON.stringify({ yaml: raw }),
          });
          setStatus("yamlStatus", "YAML saved to config.yml.", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          setStatus("yamlStatus", `Error saving YAML: ${err.message}`, "error");
      }
  }

  // ================================
  // Step 3: Captions
  // ================================
  function buildCaptionsFromConfig(cfg) {
      if (!cfg || typeof cfg !== "object") return "";
      const parts = [];

      if (cfg.first_clip && cfg.first_clip.text) parts.push(cfg.first_clip.text);

      if (Array.isArray(cfg.middle_clips)) {
          cfg.middle_clips.forEach((clip) => {
              if (clip && clip.text) parts.push(clip.text);
          });
      }

      if (cfg.last_clip && cfg.last_clip.text) parts.push(cfg.last_clip.text);

      return parts.join("\n\n");
  }

  async function loadCaptionsFromYaml() {
      const statusEl = document.getElementById("captionsStatus");
      const captionsEl = document.getElementById("captionsText");
      if (!statusEl || !captionsEl) return;

      setStatus("captionsStatus", "Loading captions…", "info");

      try {
          const data = await jsonFetch("/api/config");
          const cfg = data.config || {};
          captionsEl.value = buildCaptionsFromConfig(cfg);
          setStatus("captionsStatus", "Captions loaded.", "success");
      } catch (err) {
          console.error(err);
          setStatus("captionsStatus", `Error loading captions: ${err.message}`, "error");
      }
  }

  async function saveCaptions() {
      const statusEl = document.getElementById("captionsStatus");
      const captionsEl = document.getElementById("captionsText");
      if (!statusEl || !captionsEl) return;

      const text = captionsEl.value || "";
      statusEl.textContent = "Saving captions into config.yml…";

      try {
          const result = await jsonFetch("/api/save_captions", {
              method: "POST",
              body: JSON.stringify({ text }),
          });
          statusEl.textContent = `Saved ${result.count || 0} caption block(s).`;
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          statusEl.textContent = `Error saving captions: ${err.message}`;
      }
  }

  // ================================
  // Step 4: Overlay, timings, TTS, CTA, fg scale, music
  // ================================

  // Overlay style
  async function applyOverlay() {
      const styleSel = document.getElementById("overlayStyle");
      const statusEl = document.getElementById("overlayStatus");
      if (!styleSel || !statusEl) return;

      const style = styleSel.value || "travel_blog";
      setStatus("overlayStatus", `Applying overlay style “${style}”…`, "info");

      try {
          await jsonFetch("/api/overlay", {
              method: "POST",
              body: JSON.stringify({ style }),
          });
          setStatus("overlayStatus", "Overlay applied.", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          setStatus("overlayStatus", `Error applying overlay: ${err.message}`, "error");
      }
  }

  // Timings
  async function applyTiming(smart) {
      const statusEl = document.getElementById("timingStatus");
      if (!statusEl) return;

      setStatus("timingStatus", smart ? "Applying cinematic smart timings…" : "Applying standard timing…", "info");

      try {
          await jsonFetch("/api/timings", {
              method: "POST",
              body: JSON.stringify({ smart }),
          });
          setStatus("timingStatus", "Timings updated.", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          setStatus("timingStatus", `Error adjusting timings: ${err.message}`, "error");
      }
  }

  // ================================
  // Layout Mode (TikTok / Classic)
  // ================================
  async function loadLayoutFromYaml() {
      const sel = document.getElementById("layoutMode");
      if (!sel) return;

      try {
          const data = await jsonFetch("/api/config");
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
    status.textContent = "Saving layout mode…";

    try {
        await jsonFetch("/api/layout", {
            method: "POST",
            body: JSON.stringify({ mode }),
        });

        status.textContent = "Layout saved!";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        status.textContent = "Error saving layout: " + err.message;
    }
}

  // TTS
  async function saveTtsSettings() {
      const enabledEl = document.getElementById("ttsEnabled");
      const voiceEl = document.getElementById("ttsVoice");
      const statusEl = document.getElementById("ttsStatus");
      if (!enabledEl || !voiceEl || !statusEl) return;

      setStatus("ttsStatus", "Saving TTS settings…", "info");

      try {
          const data = await jsonFetch("/api/config");
          const cfg = data.config || {};

          cfg.tts = {
              enabled: enabledEl.checked,
              voice: voiceEl.value || "alloy",
          };

          if (cfg.render) {
              delete cfg.render.tts_enabled;
              delete cfg.render.tts_voice;
          }

          const yamlText = jsyaml.dump(cfg);

          await jsonFetch("/api/save_yaml", {
              method: "POST",
              body: JSON.stringify({ yaml: yamlText }),
          });

          setStatus("ttsStatus", "TTS settings saved.", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          setStatus("ttsStatus", `Error saving TTS: ${err.message}`, "error");
      }
  }


  // CTA
  async function saveCtaSettings() {
    const enabledEl = document.getElementById("ctaEnabled");
    const textEl = document.getElementById("ctaText");
    const voiceoverEl = document.getElementById("ctaVoiceover");
    const statusEl = document.getElementById("ctaStatus");
    if (!enabledEl || !textEl || !voiceoverEl || !statusEl) return;

    setStatus("ctaStatus", "Saving CTA settings…", "info");

    try {
        await jsonFetch("/api/cta", {
            method: "POST",
            body: JSON.stringify({
                enabled: enabledEl.checked,
                text: textEl.value || "",
                voiceover: voiceoverEl.checked,
            }),
        });

        setStatus("ctaStatus", "CTA settings saved.", "success");
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        setStatus("ctaStatus", `Error saving CTA: ${err.message}`, "error");
    }
}


  // Music: load available tracks
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

  // Music: read settings from YAML (top-level music block; fallback to legacy render.*)
  async function loadMusicSettingsFromYaml() {
      const enabledEl = document.getElementById("musicEnabled");
      const fileEl = document.getElementById("musicFile");
      const volEl = document.getElementById("musicVolume");
      const volLbl = document.getElementById("musicVolumeLabel");
      if (!enabledEl || !fileEl || !volEl || !volLbl) return;

      try {
          const data = await jsonFetch("/api/config");
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

  // Music: save settings into YAML (top-level music block)
  async function saveMusicSettings() {
      const enabledEl = document.getElementById("musicEnabled");
      const fileEl = document.getElementById("musicFile");
      const volEl = document.getElementById("musicVolume");
      if (!enabledEl || !fileEl || !volEl) return;

      const enabled = enabledEl.checked;
      const file = fileEl.value || "";
      const volume = parseFloat(volEl.value || "0.25");

      try {
          const data = await jsonFetch("/api/config");
          const cfg = data.config || {};

          // Write clean top-level music block that tiktok_template.py expects
          cfg.music = {
              enabled,
              file,
              volume,
          };

          // Remove legacy render.music_* if present
          if (cfg.render) {
              delete cfg.render.music_enabled;
              delete cfg.render.music_file;
              delete cfg.render.music_volume;
          }

          // Convert JSON → YAML string using js-yaml (must be loaded in HTML)
          const yamlText = jsyaml.dump(cfg);

          await jsonFetch("/api/save_yaml", {
              method: "POST",
              body: JSON.stringify({ yaml: yamlText }),
          });

          showStatus("Music saved!", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          showStatus("Error saving music: " + err.message, "error");
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

  // ================================
  // Auto Caption Style Selector
  // ================================
  // Auto Caption Layout Selector (merged)
function autoSelectCaptionStyle(selectedMode) {
    const layoutSelect = document.getElementById("layoutMode");
    if (!layoutSelect) return;

    // Map: standard → TikTok vertical; optimized → Classic look
    const isTikTok = selectedMode === "standard";

    layoutSelect.value = isTikTok ? "tiktok" : "classic";

    showStatus(
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
      const status = document.getElementById("musicPreviewStatus");

      if (!btn || !select || !status) return;

      // Reset player when switching songs
      select.addEventListener("change", () => {
          if (previewAudio) {
              previewAudio.pause();
              previewAudio.currentTime = 0;
          }
          previewAudio = null;
          previewPlaying = false;
          btn.textContent = "▶ Preview";
          status.textContent = "";                        // 🔥 Clear status
      });

      btn.addEventListener("click", () => {
          const file = select.value;

          if (!file) {
              alert("Select a music track first.");
              return;
          }

          // Create new Audio instance if needed
          if (!previewAudio) {
              previewAudio = new Audio(`/api/music_file/${file}`);
              previewAudio.volume = 0.8;

              previewAudio.onplay = () => {
                  previewPlaying = true;
                  btn.textContent = "⏸ Pause";
                  status.textContent = `🎵 Now Playing: ${file}`;   // 🔥 NEW
              };

              previewAudio.onpause = () => {
                  previewPlaying = false;
                  btn.textContent = "▶ Preview";
                  status.textContent = `⏸ Paused: ${file}`;        // 🔥 NEW
              };

              previewAudio.onended = () => {
                  previewPlaying = false;
                  btn.textContent = "▶ Preview";
                  status.textContent = "";                         // 🔥 Clear when done
              };
          }

          if (previewAudio.paused) {
              previewAudio.play();
          } else {
              previewAudio.pause();
          }
      });
  }




  // Foreground scale
  async function saveFgScale() {
      const range = document.getElementById("fgScale");
      if (!range) return;

      const value = parseFloat(range.value || "1.0");
      showStatus("Saving foreground scale…", "info");

      try {
          await jsonFetch("/api/fgscale", {
              method: "POST",
              body: JSON.stringify({ value }),
          });
          showStatus("Foreground scale saved.", "success");
          await loadConfigAndYaml();
      } catch (err) {
          console.error(err);
          showStatus(`Error saving scale: ${err.message}`, "error");
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

  // ================================
  // Step 5: Export
  // ================================
  async function exportVideo() {
      const exportStatus = document.getElementById("exportStatus");
      const downloadArea = document.getElementById("downloadArea");
      const btn = document.getElementById("exportBtn");
      if (!exportStatus || !downloadArea || !btn) return;

      const mode = document.querySelector('input[name="exportMode"]:checked')?.value;
      const optimized = mode === "optimized";

      setStatus("exportStatus", optimized ? "Rendering (HQ)..." : "Rendering…", "info");

      downloadArea.innerHTML = "";
      btn.disabled = true;

      try {
          const data = await jsonFetch("/api/export", {
              method: "POST",
              body: JSON.stringify({ optimized }),
          });

          if (data.status !== "ok") {
              throw new Error(data.error || "Unknown export error");
          }

          setStatus("exportStatus", "Export complete!", "success");

          const downloadUrl = data.download_url; // signed S3 URL from backend
          const filename = data.local_filename || "export.mp4";

          if (downloadUrl) {
              downloadArea.innerHTML = `
                  <div>✅ Video ready:</div>
                  <button id="directDownloadBtn" class="btn primary full">
                      ⬇ Download ${filename}
                  </button>
              `;
              const directBtn = document.getElementById("directDownloadBtn");
              if (directBtn) {
                  directBtn.onclick = () => safeDownload(downloadUrl, filename);
              }
          } else {
              downloadArea.innerHTML = `
                  <div>⚠ Local file only (S3 upload missing):</div>
                  <button id="directLocalBtn" class="btn primary full">
                      ⬇ Download ${filename}
                  </button>
              `;
              const localBtn = document.getElementById("directLocalBtn");
              if (localBtn) {
                  localBtn.onclick = () =>
                      safeDownload(`/api/download/${encodeURIComponent(filename)}`, filename);
              }
          }
      } catch (err) {
          console.error(err);
          setStatus("exportStatus", `Error during export: ${err.message}`, "error");
      } finally {
          btn.disabled = false;
      }
  }

  // ================================
  // Chat
  // ================================
  async function sendChat() {
      const input = document.getElementById("chatInput");
      const output = document.getElementById("chatOutput");
      const btn = document.getElementById("chatSendBtn");
      if (!input || !output || !btn) return;

      const msg = (input.value || "").trim();
      if (!msg) return;

      btn.disabled = true;
      output.textContent = "Thinking…";

      try {
          const data = await jsonFetch("/api/chat", {
              method: "POST",
              body: JSON.stringify({ message: msg }),
          });
          output.textContent = data.reply || "(no reply)";
      } catch (err) {
          console.error(err);
          output.textContent = `Error: ${err.message}`;
      } finally {
          btn.disabled = false;
      }
  }

  // ================================
  // Init wiring
  // ================================
  document.addEventListener("DOMContentLoaded", () => {
      // Stepper & logs
      initStepper();
      startStatusLogPolling();

      // Sliders
      initFgScaleSlider();
      initMusicVolumeSlider();

      // Preview Music
      initMusicPreview();

      // Upload UI
      initUploadUI();

      // Music list + settings
      loadMusicTracks();
      loadMusicSettingsFromYaml();

      // Initial YAML + analyses
      refreshAnalyses();
      loadConfigAndYaml();

      document.querySelectorAll(".acc-header").forEach((btn) => {
          btn.addEventListener("click", () => {
              const sec = btn.parentElement;
              sec.classList.toggle("open");
          });
      });

      // ================================
      // Export Mode Change Listener
      // ================================
      // Export Mode Change Listener (radio buttons)
      document.querySelectorAll('input[name="exportMode"]')
    .forEach((radio) => {
        radio.addEventListener("change", (e) => {
            autoSelectCaptionStyle(e.target.value);
        });
    });


      // Buttons / actions (all optional-chained)
      document.getElementById("analyzeBtn")?.addEventListener("click", analyzeClips);
      document.getElementById("refreshAnalysesBtn")?.addEventListener("click", refreshAnalyses);

      document.getElementById("generateYamlBtn")?.addEventListener("click", generateYaml);
      document.getElementById("refreshYamlBtn")?.addEventListener("click", loadConfigAndYaml);
      document.getElementById("saveYamlBtn")?.addEventListener("click", saveYaml);

      document.getElementById("loadCaptionsFromYamlBtn")?.addEventListener("click", loadCaptionsFromYaml);
      document.getElementById("saveCaptionsBtn")?.addEventListener("click", saveCaptions);

      document.getElementById("applyOverlayBtn")?.addEventListener("click", applyOverlay);
      document.getElementById("applyStandardTimingBtn")?.addEventListener("click", () => applyTiming(false));
      document.getElementById("applyCinematicTimingBtn")?.addEventListener("click", () => applyTiming(true));

      document.getElementById("saveTtsBtn")?.addEventListener("click", saveTtsSettings);
      document.getElementById("saveCtaBtn")?.addEventListener("click", saveCtaSettings);
      document.getElementById("saveFgScaleBtn")?.addEventListener("click", saveFgScale);
      document.getElementById("saveLayoutBtn")?.addEventListener("click", saveLayoutMode);
      loadLayoutFromYaml();

      document.getElementById("saveMusicBtn")?.addEventListener("click", saveMusicSettings);

      document.getElementById("exportBtn")?.addEventListener("click", exportVideo);

      document.getElementById("chatSendBtn")?.addEventListener("click", sendChat);
  });