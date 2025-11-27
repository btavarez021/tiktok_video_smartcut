// Utility: small helper for fetch with JSON
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

function showStatus(msg, type = "info") {
    const el = document.getElementById("styleStatus");
    if (!el) return;

    el.textContent = msg;
    el.className = "hint-text " + type;
}


// Stepper behavior
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

    // Also track scroll to update active step
    const steps = Array.from(document.querySelectorAll(".step-card"));
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

// Status log polling
let statusLogTimer = null;

async function refreshStatusLog() {
    try {
        const data = await jsonFetch("/api/status");
        const log = data.status_log || [];
        const el = document.getElementById("statusLog");
        el.textContent = log.join("\n");
        el.scrollTop = el.scrollHeight;
    } catch (err) {
        // Fail silently in UI for logs
    }
}

function startStatusLogPolling() {
    if (statusLogTimer) clearInterval(statusLogTimer);
    refreshStatusLog();
    statusLogTimer = setInterval(refreshStatusLog, 2000);
}

// Upload

async function uploadFiles() {
    const input = document.getElementById("uploadFiles");
    const status = document.getElementById("uploadStatus");

    if (!input.files.length) {
        status.textContent = "❌ No files selected.";
        return;
    }

    const formData = new FormData();
    for (let f of input.files) {
        formData.append("files", f);
    }

    status.textContent = "⬆ Uploading…";

    try {
        const resp = await fetch("/api/upload", {
            method: "POST",
            body: formData
        });

        const data = await resp.json();
        console.log("Uploaded:", data);

        if (data.uploaded?.length) {
            status.textContent = `✅ Uploaded ${data.uploaded.length} file(s).`;
        } else {
            status.textContent = `⚠ No files uploaded (check logs).`;
        }

    } catch (err) {
        console.error(err);
        status.textContent = `❌ Upload failed: ${err.message}`;
    }
}

// Download mobile helper

function safeDownload(url, filename = "export.mp4") {
    const a = document.createElement("a");
    a.href = url;
    a.style.display = "none";
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

// Drag and Drop upload

function initUploadUI() {
    const dropZone = document.getElementById("dropZone");
    const fileInput = document.getElementById("uploadFiles");
    const preview = document.getElementById("uploadPreview");
    const uploadBtn = document.getElementById("uploadBtn");
    const progressWrapper = document.getElementById("uploadProgressWrapper");
    const progressBar = document.getElementById("uploadProgress");
    const statusEl = document.getElementById("uploadStatus");

    let selectedFiles = [];

    // ----- CLICK TO OPEN FILE PICKER -----
    dropZone.addEventListener("click", () => fileInput.click());

    // ----- FILE SELECTED -----
    fileInput.addEventListener("change", (e) => {
        selectedFiles = Array.from(e.target.files);
        updatePreview();
    });

    // ----- DRAG OVER -----
    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    // ----- DRAG LEAVE -----
    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("dragover");
    });

    // ----- DROP FILES -----
    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");

        selectedFiles = Array.from(e.dataTransfer.files);
        updatePreview();
    });

   // ----- UPDATE PREVIEW -----
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


    // ----- UPLOAD -----
    uploadBtn.addEventListener("click", async () => {
        if (!selectedFiles.length) return;

        statusEl.textContent = "Uploading…";
        progressWrapper.classList.remove("hidden");
        progressBar.style.width = "0%";

        const formData = new FormData();
        selectedFiles.forEach((f) => formData.append("files", f));

        try {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/upload");

            // Upload progress bar
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

        } catch (err) {
            console.error(err);
            statusEl.textContent = `❌ Upload failed: ${err.message}`;
        }
    });
}

// Step 1: Analysis
async function analyzeClips() {
    const analyzeBtn = document.getElementById("analyzeBtn");
    const statusEl = document.getElementById("analyzeStatus");
    analyzeBtn.disabled = true;
    statusEl.textContent = "Analyzing clips from S3… this can take a bit depending on video length.";

    try {
        const data = await jsonFetch("/api/analyze", { method: "POST", body: "{}" });
        const count = Object.keys(data || {}).length;
        statusEl.textContent = `Analysis complete. ${count} video(s) described.`;
        await refreshAnalyses();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error during analysis: ${err.message}`;
    } finally {
        analyzeBtn.disabled = false;
    }
}

async function refreshAnalyses() {
    const listEl = document.getElementById("analysesList");
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

// Step 2: YAML generation & config

async function generateYaml() {
    const statusEl = document.getElementById("yamlStatus");
    statusEl.textContent = "Calling LLM to build config.yml storyboard…";
    try {
        const cfg = await jsonFetch("/api/generate_yaml", { method: "POST", body: "{}" });
        statusEl.textContent = "YAML generated and saved to config.yml.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error generating YAML: ${err.message}`;
    }
}

async function loadConfigAndYaml() {
    const yamlTextEl = document.getElementById("yamlText");
    const yamlPreviewEl = document.getElementById("yamlPreview");
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
    const raw = yamlTextEl.value || "";
    statusEl.textContent = "Saving YAML…";
    try {
        await jsonFetch("/api/save_yaml", {
            method: "POST",
            body: JSON.stringify({ yaml: raw }),
        });
        statusEl.textContent = "YAML saved to config.yml.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error saving YAML: ${err.message}`;
    }
}

// Step 3: Captions

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
    statusEl.textContent = "Loading captions from config.yml…";

    try {
        const data = await jsonFetch("/api/config");
        const cfg = data.config || {};
        captionsEl.value = buildCaptionsFromConfig(cfg);
        statusEl.textContent = "Captions loaded. Edit and click “Save captions”.";
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error loading captions: ${err.message}`;
    }
}

async function saveCaptions() {
    const statusEl = document.getElementById("captionsStatus");
    const captionsEl = document.getElementById("captionsText");
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

// Step 4: Overlay, timings, TTS, CTA, fg scale, music

async function applyOverlay() {
    const styleSel = document.getElementById("overlayStyle");
    const statusEl = document.getElementById("overlayStatus");
    const style = styleSel.value || "travel_blog";
    statusEl.textContent = `Applying overlay style “${style}”…`;
    try {
        await jsonFetch("/api/overlay", {
            method: "POST",
            body: JSON.stringify({ style }),
        });
        statusEl.textContent = "Overlay applied. YAML updated.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error applying overlay: ${err.message}`;
    }
}

async function applyTiming(smart) {
    const statusEl = document.getElementById("timingStatus");
    statusEl.textContent = smart
        ? "Applying cinematic smart timings…"
        : "Applying standard timing tweaks…";
    try {
        await jsonFetch("/api/timings", {
            method: "POST",
            body: JSON.stringify({ smart }),
        });
        statusEl.textContent = "Timings updated in config.yml.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error adjusting timings: ${err.message}`;
    }
}

async function saveTtsSettings() {
    const enabled = document.getElementById("ttsEnabled").checked;
    const voice = document.getElementById("ttsVoice").value || "alloy";
    const styleStatus = document.getElementById("styleStatus");

    styleStatus.textContent = "Saving TTS settings…";
    try {
        await jsonFetch("/api/tts", {
            method: "POST",
            body: JSON.stringify({ enabled, voice }),
        });
        styleStatus.textContent = "TTS settings saved.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        styleStatus.textContent = `Error saving TTS: ${err.message}`;
    }
}

async function saveCtaSettings() {
    const enabled = document.getElementById("ctaEnabled").checked;
    const text = document.getElementById("ctaText").value || "";
    const voiceover = document.getElementById("ctaVoiceover").checked;
    const styleStatus = document.getElementById("styleStatus");

    styleStatus.textContent = "Saving CTA settings…";
    try {
        await jsonFetch("/api/cta", {
            method: "POST",
            body: JSON.stringify({ enabled, text, voiceover }),
        });
        styleStatus.textContent = "CTA settings saved.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        styleStatus.textContent = `Error saving CTA: ${err.message}`;
    }
}

async function loadMusicTracks() {
    const sel = document.getElementById("musicFile");
    sel.innerHTML = `<option value="">– No music –</option>`;

    try {
        const data = await jsonFetch("/api/music_list");
        const files = data.files || [];
        files.forEach(f => {
            const opt = document.createElement("option");
            opt.value = f;
            opt.textContent = f;
            sel.appendChild(opt);
        });
    } catch(err) {
        console.error(err);
    }
}

async function loadMusicSettingsFromYaml() {
    try {
        const data = await jsonFetch("/api/config");
        const cfg = data.config?.render || {};

        document.getElementById("musicEnabled").checked = cfg.music_enabled || false;
        document.getElementById("musicFile").value = cfg.music_file || "";
        document.getElementById("musicVolume").value = cfg.music_volume || 0.25;
        document.getElementById("musicVolumeLabel").textContent =
            cfg.music_volume?.toFixed(2) || "0.25";
    } catch (err) {
        console.error("Music settings load failed", err);
    }
}

function saveMusicSettings() {
    const enabled = document.getElementById("musicEnabled").checked;
    const file = document.getElementById("musicFile").value || "";
    const volume = parseFloat(document.getElementById("musicVolume").value || "0.25");

    fetch("/api/config")
        .then(res => res.json())
        .then(data => {
            const cfg = data.config || {};
            if (!cfg.render) cfg.render = {};

            cfg.render.music_enabled = enabled;
            cfg.render.music_file = file;
            cfg.render.music_volume = volume;

            if (cfg.music) delete cfg.music;

            const yamlText = jsyaml.dump(cfg);

            return fetch("/api/save_yaml", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ yaml: yamlText })
            });
        })
        .then(() => {
            showStatus("Music saved!", "success");
            loadConfigAndYaml();
        })
        .catch(err =>
            showStatus("Error saving music: " + err.message, "error")
        );
}





function initMusicVolumeSlider() {
    const slider = document.getElementById("musicVolume");
    const lbl = document.getElementById("musicVolumeLabel");
    slider.addEventListener("input", () => {
        lbl.textContent = slider.value;
    });
}


async function saveFgScale() {
    const value = parseFloat(document.getElementById("fgScale").value || "1.0");
    const styleStatus = document.getElementById("styleStatus");
    styleStatus.textContent = "Saving foreground scale…";
    try {
        await jsonFetch("/api/fgscale", {
            method: "POST",
            body: JSON.stringify({ value }),
        });
        styleStatus.textContent = "Foreground scale saved.";
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        styleStatus.textContent = `Error saving scale: ${err.message}`;
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

// Step 5: Export

async function exportVideo() {
    const exportStatus = document.getElementById("exportStatus");
    const downloadArea = document.getElementById("downloadArea");
    const btn = document.getElementById("exportBtn");

    const mode = document.querySelector('input[name="exportMode"]:checked')?.value;
    const optimized = mode === "optimized";

    exportStatus.textContent = optimized
        ? "Rendering in optimized mode…"
        : "Rendering in standard mode…";
    downloadArea.innerHTML = "";
    btn.disabled = true;

    try {
        const data = await jsonFetch("/api/export", {
            method: "POST",
            body: JSON.stringify({ optimized }),
        });

        exportStatus.textContent = "Export complete.";

        const s3_url = data.s3_url;
        const filename = data.local_filename;

        if (s3_url) {
          downloadArea.innerHTML = `
              <div>✅ Video ready:</div>
              <button id="directDownloadBtn" class="btn primary full">
                  ⬇ Download ${filename}
              </button>
          `;

          document.getElementById("directDownloadBtn").onclick = () => {
              safeDownload(s3_url, filename);
          };

      } else {
          downloadArea.innerHTML = `
              <div>⚠ Local file only (S3 upload missing):</div>
              <button id="directLocalBtn" class="btn primary full">
                  ⬇ Download ${filename}
              </button>
          `;
        
          document.getElementById("directLocalBtn").onclick = () => {
              safeDownload(`/api/download/${encodeURIComponent(filename)}`, filename);
          };
      }
    } catch (err) {
        console.error(err);
        exportStatus.textContent = `Error during export: ${err.message}`;
    } finally {
        btn.disabled = false;
    }
}


// Chat

async function sendChat() {
    const input = document.getElementById("chatInput");
    const output = document.getElementById("chatOutput");
    const btn = document.getElementById("chatSendBtn");
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

// Wire everything up

document.addEventListener("DOMContentLoaded", () => {
    initStepper();
    startStatusLogPolling();
    initFgScaleSlider();
    initMusicVolumeSlider();
    // Load track list & settings
    loadMusicTracks();
    loadMusicSettingsFromYaml();
    document.getElementById("saveMusicBtn")
    ?.addEventListener("click", saveMusicSettings);


    document.getElementById("analyzeBtn")?.addEventListener("click", analyzeClips);
    document
        .getElementById("refreshAnalysesBtn")
        ?.addEventListener("click", refreshAnalyses);

    document
        .getElementById("generateYamlBtn")
        ?.addEventListener("click", generateYaml);
    document
        .getElementById("refreshYamlBtn")
        ?.addEventListener("click", loadConfigAndYaml);
    document.getElementById("saveYamlBtn")?.addEventListener("click", saveYaml);

    document
        .getElementById("loadCaptionsFromYamlBtn")
        ?.addEventListener("click", loadCaptionsFromYaml);
    document
        .getElementById("saveCaptionsBtn")
        ?.addEventListener("click", saveCaptions);

    document
        .getElementById("applyOverlayBtn")
        ?.addEventListener("click", applyOverlay);
    document
        .getElementById("applyStandardTimingBtn")
        ?.addEventListener("click", () => applyTiming(false));
    document
        .getElementById("applyCinematicTimingBtn")
        ?.addEventListener("click", () => applyTiming(true));

    document.getElementById("saveTtsBtn")?.addEventListener("click", saveTtsSettings);
    document.getElementById("saveCtaBtn")?.addEventListener("click", saveCtaSettings);
    document
        .getElementById("saveFgScaleBtn")
        ?.addEventListener("click", saveFgScale);

    document.getElementById("exportBtn")?.addEventListener("click", exportVideo);

    document.getElementById("chatSendBtn")?.addEventListener("click", sendChat);

    initUploadUI();

    // Initial loads
    refreshAnalyses();
    loadConfigAndYaml();
});
