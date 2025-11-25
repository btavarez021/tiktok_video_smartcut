// static/js/app.js

// Simple helper to update text in a DOM element
function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// Helper to set innerHTML (for download link etc.)
function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

// -----------------------------
// On page load
// -----------------------------
document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    pollStatusLog();
    pollAnalysesCache();
    loadConfigAndYaml();
    loadExportMode();
});

// -----------------------------
// Wire all buttons / inputs
// -----------------------------
function wireEvents() {
    const uploadBtn = document.getElementById("uploadBtn");
    if (uploadBtn) uploadBtn.addEventListener("click", handleUpload);

    const startAnalyzeBtn = document.getElementById("startAnalyzeBtn");
    if (startAnalyzeBtn) startAnalyzeBtn.addEventListener("click", handleAnalyzeStartAndLoop);

    const runStepBtn = document.getElementById("runStepBtn");
    if (runStepBtn) runStepBtn.addEventListener("click", handleAnalyzeStepOnce);

    const generateYamlBtn = document.getElementById("generateYamlBtn");
    if (generateYamlBtn) generateYamlBtn.addEventListener("click", handleGenerateYaml);

    const saveYamlBtn = document.getElementById("saveYamlBtn");
    if (saveYamlBtn) saveYamlBtn.addEventListener("click", handleSaveYaml);

    const saveCaptionsBtn = document.getElementById("saveCaptionsBtn");
    if (saveCaptionsBtn) saveCaptionsBtn.addEventListener("click", handleSaveCaptions);

    const saveTTSBtn = document.getElementById("saveTTSBtn");
    if (saveTTSBtn) saveTTSBtn.addEventListener("click", handleSaveTTS);

    const saveCTABtn = document.getElementById("saveCTABtn");
    if (saveCTABtn) saveCTABtn.addEventListener("click", handleSaveCTA);

    const applyOverlayBtn = document.getElementById("applyOverlayBtn");
    if (applyOverlayBtn) applyOverlayBtn.addEventListener("click", handleApplyOverlay);

    const standardTimingBtn = document.getElementById("standardTimingBtn");
    if (standardTimingBtn) standardTimingBtn.addEventListener("click", () => handleApplyTimings(false));

    const cinematicTimingBtn = document.getElementById("cinematicTimingBtn");
    if (cinematicTimingBtn) cinematicTimingBtn.addEventListener("click", () => handleApplyTimings(true));

    const saveFGScaleBtn = document.getElementById("saveFGScaleBtn");
    if (saveFGScaleBtn) saveFGScaleBtn.addEventListener("click", handleSaveFGScale);

    const exportBtn = document.getElementById("exportBtn");
    if (exportBtn) exportBtn.addEventListener("click", handleExport);

    const chatBtn = document.getElementById("chatBtn");
    if (chatBtn) chatBtn.addEventListener("click", handleChat);

    const exportModeSelect = document.getElementById("exportMode");
    if (exportModeSelect) {
        exportModeSelect.addEventListener("change", handleSaveExportMode);
    }
}

// -----------------------------
// Upload to S3
// -----------------------------
async function handleUpload() {
    const input = document.getElementById("uploadInput");
    const statusId = "uploadStatus";

    if (!input || !input.files || input.files.length === 0) {
        setText(statusId, "No files selected.");
        return;
    }

    const formData = new FormData();
    for (const file of input.files) {
        formData.append("files", file);
    }

    setText(statusId, "Uploading to S3…");

    try {
        const res = await fetch("/api/upload_s3", {
            method: "POST",
            body: formData,
        });
        if (!res.ok) {
            const txt = await res.text();
            setText(statusId, "Upload failed: " + txt);
            return;
        }
        const data = await res.json();
        if (data && data.uploaded) {
            setText(statusId, `Uploaded ${data.uploaded.length} file(s) to S3.`);
        } else {
            setText(statusId, "Upload completed.");
        }
    } catch (err) {
        console.error("Upload error", err);
        setText(statusId, "Upload error: " + err);
    }
}

// -----------------------------
// Analyze: Start + auto loop
// -----------------------------
let analyzeIsRunning = false;

async function handleAnalyzeStartAndLoop() {
    const statusId = "analyzeStatus";
    setText(statusId, "Starting analysis…");
    analyzeIsRunning = true;

    try {
        // Kick off
        const res = await fetch("/api/analyze_start", { method: "POST" });
        if (!res.ok) {
            const txt = await res.text();
            setText(statusId, "Analyze start failed: " + txt);
            analyzeIsRunning = false;
            return;
        }
        const data = await res.json();
        if (data && data.message) {
            setText(statusId, data.message);
        }

        // Now auto-run steps until done
        await runAnalyzeStepsLoop();
    } catch (err) {
        console.error("Analyze start error", err);
        setText(statusId, "Analyze start error: " + err);
        analyzeIsRunning = false;
    }
}

async function runAnalyzeStepsLoop() {
    const statusId = "analyzeStatus";
    while (analyzeIsRunning) {
        try {
            const res = await fetch("/api/analyze_step", { method: "POST" });
            if (!res.ok) {
                const txt = await res.text();
                setText(statusId, "Analyze step failed: " + txt);
                analyzeIsRunning = false;
                break;
            }
            const data = await res.json();

            if (data.message) {
                setText(statusId, data.message);
            }

            // If backend signals that we are done
            if (data.done) {
                analyzeIsRunning = false;
                setText(statusId, data.message || "Analysis complete.");
                // Refresh analyses cache display
                await fetchAnalysesCache();
                break;
            }

            // Avoid hammering the server too fast
            await new Promise((r) => setTimeout(r, 500));
        } catch (err) {
            console.error("Analyze loop error", err);
            setText(statusId, "Analyze loop error: " + err);
            analyzeIsRunning = false;
            break;
        }
    }
}

// Manual step for debugging
async function handleAnalyzeStepOnce() {
    const statusId = "analyzeStatus";
    try {
        const res = await fetch("/api/analyze_step", { method: "POST" });
        if (!res.ok) {
            const txt = await res.text();
            setText(statusId, "Analyze step failed: " + txt);
            return;
        }
        const data = await res.json();
        if (data.message) {
            setText(statusId, data.message);
        }
        if (data.done) {
            setText(statusId, data.message || "Analysis complete (manual step).");
        }
        await fetchAnalysesCache();
    } catch (err) {
        console.error("Analyze step error", err);
        setText(statusId, "Analyze step error: " + err);
    }
}

// -----------------------------
// YAML & Config
// -----------------------------
async function loadConfigAndYaml() {
    try {
        const res = await fetch("/api/config");
        if (!res.ok) return;

        const data = await res.json();
        const yamlTextArea = document.getElementById("yamlText");
        if (yamlTextArea) {
            if (data.yaml) {
                yamlTextArea.value = data.yaml;
            } else if (data.config) {
                yamlTextArea.value = JSON.stringify(data.config, null, 2);
            }
        }

        // Also pre-fill captions area if possible
        if (data.config) {
            populateCaptionsFromConfig(data.config);
        }
    } catch (err) {
        console.error("loadConfigAndYaml error", err);
    }
}

function populateCaptionsFromConfig(cfg) {
    try {
        const parts = [];
        if (cfg.first_clip && cfg.first_clip.text) {
            parts.push(cfg.first_clip.text);
        }
        if (Array.isArray(cfg.middle_clips)) {
            for (const c of cfg.middle_clips) {
                if (c && c.text) parts.push(c.text);
            }
        }
        if (cfg.last_clip && cfg.last_clip.text) {
            parts.push(cfg.last_clip.text);
        }
        const captionsArea = document.getElementById("captionsInput");
        if (captionsArea && parts.length > 0) {
            captionsArea.value = parts.join("\n\n");
        }
    } catch (err) {
        console.error("populateCaptionsFromConfig error", err);
    }
}

async function handleGenerateYaml() {
    const statusId = "analyzeStatus";
    setText(statusId, "Generating YAML from analyses…");

    try {
        const res = await fetch("/api/generate_yaml", { method: "POST" });
        if (!res.ok) {
            const txt = await res.text();
            setText(statusId, "YAML generation failed: " + txt);
            return;
        }
        const cfg = await res.json();

        // Refresh config + yaml to show what backend wrote
        await loadConfigAndYaml();
        setText(statusId, "YAML generated and saved.");
    } catch (err) {
        console.error("generateYaml error", err);
        setText(statusId, "YAML generation error: " + err);
    }
}

async function handleSaveYaml() {
    const yamlTextArea = document.getElementById("yamlText");
    if (!yamlTextArea) return;

    const yamlText = yamlTextArea.value;
    try {
        const res = await fetch("/api/save_yaml", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ yaml: yamlText }),
        });
        const data = await res.json();
        setText("analyzeStatus", "YAML saved. Status: " + (data.status || "ok"));

        // Reload config so captions view gets updated as well
        await loadConfigAndYaml();
    } catch (err) {
        console.error("saveYaml error", err);
        setText("analyzeStatus", "Save YAML error: " + err);
    }
}

// -----------------------------
// Captions
// -----------------------------
async function handleSaveCaptions() {
    const captionsArea = document.getElementById("captionsInput");
    if (!captionsArea) return;

    const text = captionsArea.value || "";
    try {
        const res = await fetch("/api/save_captions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();
        setText("analyzeStatus", "Captions saved. Status: " + (data.status || "ok"));
        // Reload config + YAML to reflect the new captions
        await loadConfigAndYaml();
    } catch (err) {
        console.error("saveCaptions error", err);
        setText("analyzeStatus", "Save captions error: " + err);
    }
}

// -----------------------------
// Settings: TTS, CTA, Overlay, Timings, FG Scale, Export Mode
// -----------------------------
async function handleSaveTTS() {
    const enabled = document.getElementById("ttsEnabled")?.checked || false;
    const voice = document.getElementById("ttsVoice")?.value || "alloy";

    try {
        const res = await fetch("/api/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled, voice }),
        });
        const data = await res.json();
        setText("analyzeStatus", "TTS updated: " + JSON.stringify(data));
    } catch (err) {
        console.error("saveTTS error", err);
        setText("analyzeStatus", "TTS error: " + err);
    }
}

async function handleSaveCTA() {
    const enabled = document.getElementById("ctaEnabled")?.checked || false;
    const text = document.getElementById("ctaText")?.value || "";
    const voiceover = document.getElementById("ctaVoiceover")?.checked || false;

    try {
        const res = await fetch("/api/cta", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled, text, voiceover }),
        });
        const data = await res.json();
        setText("analyzeStatus", "CTA updated: " + JSON.stringify(data));
    } catch (err) {
        console.error("saveCTA error", err);
        setText("analyzeStatus", "CTA error: " + err);
    }
}

async function handleApplyOverlay() {
    const style = document.getElementById("overlayStyle")?.value || "travel_blog";

    try {
        const res = await fetch("/api/overlay", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ style }),
        });
        const data = await res.json();
        setText("analyzeStatus", "Overlay applied: " + JSON.stringify(data));

        // Reload config + yaml to see new captions
        await loadConfigAndYaml();
    } catch (err) {
        console.error("applyOverlay error", err);
        setText("analyzeStatus", "Overlay error: " + err);
    }
}

async function handleApplyTimings(smart) {
    try {
        const res = await fetch("/api/timings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ smart: !!smart }),
        });
        const data = await res.json();
        setText(
            "analyzeStatus",
            "Timings updated (" + (smart ? "cinematic" : "standard") + "): " + JSON.stringify(data)
        );
        await loadConfigAndYaml();
    } catch (err) {
        console.error("applyTimings error", err);
        setText("analyzeStatus", "Timings error: " + err);
    }
}

async function handleSaveFGScale() {
    const scaleInput = document.getElementById("fgScale");
    if (!scaleInput) return;

    const value = parseFloat(scaleInput.value || "1.0") || 1.0;

    try {
        const res = await fetch("/api/fgscale", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value }),
        });
        const data = await res.json();
        setText("analyzeStatus", "FG scale updated: " + JSON.stringify(data));
    } catch (err) {
        console.error("fgScale error", err);
        setText("analyzeStatus", "FG scale error: " + err);
    }
}

async function loadExportMode() {
    try {
        const res = await fetch("/api/export_mode");
        if (!res.ok) return;
        const data = await res.json();
        const select = document.getElementById("exportMode");
        if (select && data && data.mode) {
            select.value = data.mode;
        }
    } catch (err) {
        console.error("loadExportMode error", err);
    }
}

async function handleSaveExportMode() {
    const select = document.getElementById("exportMode");
    if (!select) return;

    const mode = select.value || "standard";
    try {
        const res = await fetch("/api/export_mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode }),
        });
        const data = await res.json();
        setText("analyzeStatus", "Export mode set: " + JSON.stringify(data));
    } catch (err) {
        console.error("saveExportMode error", err);
        setText("analyzeStatus", "Export mode error: " + err);
    }
}

// -----------------------------
// Export
// -----------------------------
async function handleExport() {
    const exportStatusId = "exportStatus";
    setText(exportStatusId, "Starting export…");
    setHtml("downloadLink", "");

    const modeSelect = document.getElementById("exportMode");
    const mode = modeSelect ? modeSelect.value : "standard";
    const optimized = mode === "optimized";

    try {
        const res = await fetch("/api/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ optimized }),
        });

        if (!res.ok) {
            const txt = await res.text();
            setText(exportStatusId, "Export failed: " + txt);
            return;
        }

        const data = await res.json();
        const filename = data.filename;
        setText(exportStatusId, "Export finished: " + filename);

        if (filename) {
            const url = "/api/download/" + encodeURIComponent(filename);
            setHtml(
                "downloadLink",
                `<a href="${url}" target="_blank">⬇ Download Final Video</a>`
            );
        }
    } catch (err) {
        console.error("export error", err);
        setText(exportStatusId, "Export error: " + err);
    }
}

// -----------------------------
// Chat
// -----------------------------
async function handleChat() {
    const input = document.getElementById("chatInput");
    const replyEl = document.getElementById("chatReply");
    if (!input || !replyEl) return;

    const message = input.value.trim();
    if (!message) return;

    replyEl.textContent = "Thinking…";

    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        });

        if (!res.ok) {
            const txt = await res.text();
            replyEl.textContent = "Chat error: " + txt;
            return;
        }

        const data = await res.json();
        replyEl.textContent = data.reply || "(no reply)";
    } catch (err) {
        console.error("chat error", err);
        replyEl.textContent = "Chat error: " + err;
    }
}

// -----------------------------
// Live status log & analyses cache polling
// -----------------------------
function pollStatusLog() {
    async function fetchStatus() {
        try {
            const res = await fetch("/api/status");
            if (!res.ok) return;
            const data = await res.json();
            const log = data.status_log || [];
            const text = Array.isArray(log) ? log.join("\n") : String(log);
            setText("liveLog", text);
        } catch (err) {
            console.error("status poll error", err);
        }
    }

    fetchStatus();
    setInterval(fetchStatus, 3000);
}

function pollAnalysesCache() {
    fetchAnalysesCache();
    setInterval(fetchAnalysesCache, 5000);
}

async function fetchAnalysesCache() {
    try {
        const res = await fetch("/api/analyses_cache");
        if (!res.ok) return;
        const data = await res.json();

        const lines = [];
        for (const [file, desc] of Object.entries(data || {})) {
            lines.push(`${file}: ${desc}`);
        }

        const text = lines.join("\n");
        setText("analysisCache", text);
    } catch (err) {
        console.error("analyses_cache error", err);
    }
}
