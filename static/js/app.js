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
        // ignore log errors in UI
    }
}

function startStatusLogPolling() {
    if (statusLogTimer) clearInterval(statusLogTimer);
    refreshStatusLog();
    statusLogTimer = setInterval(refreshStatusLog, 2000);
}

// Step 1: Upload to S3
async function uploadFilesToS3() {
    const input = document.getElementById("uploadFiles");
    const statusEl = document.getElementById("uploadStatus");
    if (!input || !input.files || input.files.length === 0) {
        statusEl.textContent = "No files selected.";
        return;
    }

    const formData = new FormData();
    for (const file of input.files) {
        formData.append("files", file);
    }

    statusEl.textContent = "Uploading to S3...";
    try {
        const resp = await fetch("/api/upload", {
            method: "POST",
            body: formData,
        });
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(text || `Upload failed: ${resp.status}`);
        }
        const data = await resp.json();
        const uploaded = data.uploaded || [];
        if (uploaded.length) {
            statusEl.textContent = `Uploaded ${uploaded.length} file(s) to S3.`;
        } else {
            statusEl.textContent = "Upload completed but no files returned. Check logs.";
        }
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error uploading: ${err.message}`;
    }
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
        statusEl.textContent = `Saved ${result.captions_applied || 0} caption block(s).`;
        await loadConfigAndYaml();
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error saving captions: ${err.message}`;
    }
}

// ------------------------------
// STEP 4: Overlay, timings, TTS, CTA, fg scale
// ------------------------------

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
        document.getElementById("overlayCheck").classList.add("active");
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

        if (smart) {
            document.getElementById("timingCheckCinema").classList.add("active");
        } else {
            document.getElementById("timingCheckStd").classList.add("active");
        }

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
        document.getElementById("ttsCheck").classList.add("active");
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
        document.getElementById("ctaCheck").classList.add("active");
    } catch (err) {
        console.error(err);
        styleStatus.textContent = `Error saving CTA: ${err.message}`;
    }
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
        document.getElementById("fgCheck").classList.add("active");
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
        const filename = data.filename;
        exportStatus.textContent = "Export complete.";
        if (filename) {
            const url = `/api/download/${encodeURIComponent(filename)}`;
            downloadArea.innerHTML = `
                <div>✅ Ready to download:</div>
                <a href="${url}" download>Download ${filename}</a>
            `;
        } else {
            downloadArea.textContent =
                "Export finished but no filename returned. Check server logs.";
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

    document.getElementById("uploadBtn")?.addEventListener("click", uploadFilesToS3);

    document.getElementById("analyzeBtn")?.addEventListener("click", analyzeClips);
    document.getElementById("refreshAnalysesBtn")?.addEventListener("click", refreshAnalyses);

    document.getElementById("generateYamlBtn")?.addEventListener("click", generateYaml);
    document.getElementById("refreshYamlBtn")?.addEventListener("click", loadConfigAndYaml);
    document.getElementById("saveYamlBtn")?.addEventListener("click", saveYaml);

    document
        .getElementById("loadCaptionsFromYamlBtn")
        ?.addEventListener("click", loadCaptionsFromYaml);
    document.getElementById("saveCaptionsBtn")?.addEventListener("click", saveCaptions);

    document.getElementById("applyOverlayBtn")?.addEventListener("click", applyOverlay);
    document
        .getElementById("applyStandardTimingBtn")
        ?.addEventListener("click", () => applyTiming(false));
    document
        .getElementById("applyCinematicTimingBtn")
        ?.addEventListener("click", () => applyTiming(true));

    document.getElementById("saveTtsBtn")?.addEventListener("click", saveTtsSettings);
    document.getElementById("saveCtaBtn")?.addEventListener("click", saveCtaSettings);
    document.getElementById("saveFgScaleBtn")?.addEventListener("click", saveFgScale);

    document.getElementById("exportBtn")?.addEventListener("click", exportVideo);

    document.getElementById("chatSendBtn")?.addEventListener("click", sendChat);

    // Initial loads
    refreshAnalyses();
    loadConfigAndYaml();
});
