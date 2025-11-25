// =========================================================
// TikTok Smart Cut — Frontend Controller (Full File)
// =========================================================

// -----------------------------
// Cached DOM references
// -----------------------------

const statusBox = document.getElementById("status");
const logBox = document.getElementById("log");
const cacheBox = document.getElementById("cache");
const yamlEditor = document.getElementById("yamlEditor");
const captionEditor = document.getElementById("captionEditor");
const chatInput = document.getElementById("chatInput");
const chatOutput = document.getElementById("chatOutput");

// -----------------------------
// Helpers
// -----------------------------

function log(msg) {
    if (!logBox) return;
    logBox.textContent += msg + "\n";
    logBox.scrollTop = logBox.scrollHeight;
}

function setStatus(msg) {
    if (!statusBox) return;
    statusBox.textContent = msg;
}

async function jsonPost(url, data = {}) {
    const res = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data),
    });
    return res.json();
}

// Refresh status log every 1 sec
setInterval(async () => {
    const res = await fetch("/api/status");
    const json = await res.json();
    if (json.status_log) {
        logBox.textContent = json.status_log.join("\n");
        logBox.scrollTop = logBox.scrollHeight;
    }
}, 1000);

// Refresh  analyses cache every 2 sec
setInterval(refreshCache, 2000);

async function refreshCache() {
    const res = await fetch("/api/analyses_cache");
    const data = await res.json();

    if (!cacheBox) return;
    cacheBox.innerHTML = "";

    const keys = Object.keys(data);
    if (keys.length === 0) {
        cacheBox.textContent = "No analyses yet.";
        return;
    }

    keys.forEach(k => {
        const div = document.createElement("div");
        div.style.marginBottom = "8px";
        div.innerHTML = `<strong>${k}</strong><br>${data[k]}`;
        cacheBox.appendChild(div);
    });
}

// =========================================================
// 1. S3 Upload
// =========================================================

const uploadInput = document.getElementById("uploadInput");
const uploadBtn = document.getElementById("uploadBtn");

if (uploadBtn) {
    uploadBtn.onclick = async () => {
        const files = uploadInput.files;
        if (!files.length) {
            alert("Select files first.");
            return;
        }

        setStatus("Uploading to S3…");

        for (const f of files) {
            const formData = new FormData();
            formData.append("file", f);

            await fetch("/api/upload", {
                method: "POST",
                body: formData,
            });

            log(`Uploaded: ${f.name}`);
        }

        setStatus("Upload complete.");
        refreshCache();
    };
}


// =========================================================
// 2. Step-Based Analyze
// =========================================================

const analyzeBtn = document.getElementById("analyzeBtn");

if (analyzeBtn) {
    analyzeBtn.onclick = async () => {
        setStatus("Starting multi-step analysis…");
        logBox.textContent = "";

        const start = await jsonPost("/api/analyze_start");
        log("Analysis session created.");

        async function runStep() {
            const step = await jsonPost("/api/analyze_step");

            if (step.done) {
                setStatus("Analysis complete!");
                refreshCache();
                return;
            }

            setStatus(`Analyzing ${step.current || ""}`);
            setTimeout(runStep, 500);
        }

        runStep();
    };
}


// =========================================================
// 3. Generate YAML
// =========================================================

const genYamlBtn = document.getElementById("genYamlBtn");

if (genYamlBtn) {
    genYamlBtn.onclick = async () => {
        setStatus("Generating YAML…");

        const res = await jsonPost("/api/generate_yaml");
        yamlEditor.value = JSON.stringify(res, null, 2);

        setStatus("YAML generated.");
    };
}


// =========================================================
// 4. Load YAML / Config
// =========================================================

async function loadConfig() {
    const res = await fetch("/api/config");
    const json = await res.json();

    if (yamlEditor) {
        yamlEditor.value = json.yaml || "";
    }
}

document.addEventListener("DOMContentLoaded", loadConfig);


// =========================================================
// 5. Save YAML
// =========================================================

const saveYamlBtn = document.getElementById("saveYamlBtn");

if (saveYamlBtn) {
    saveYamlBtn.onclick = async () => {
        const yamlText = yamlEditor.value;
        const res = await jsonPost("/api/save_yaml", { yaml: yamlText });

        setStatus("YAML saved.");
    };
}


// =========================================================
// 6. Export video
// =========================================================

const exportStdBtn = document.getElementById("exportStdBtn");
const exportOptBtn = document.getElementById("exportOptBtn");
const downloadLink = document.getElementById("downloadLink");

async function runExport(optimized) {
    setStatus("Exporting video…");
    log("Export started.");

    const data = await jsonPost("/api/export", { optimized });
    const filename = data.filename;

    if (downloadLink) {
        downloadLink.href = "/api/download/" + filename;
        downloadLink.textContent = "Download " + filename;
        downloadLink.style.display = "block";
    }

    setStatus("Export complete.");
    log("Export complete → " + filename);
}

if (exportStdBtn) exportStdBtn.onclick = () => runExport(false);
if (exportOptBtn) exportOptBtn.onclick = () => runExport(true);


// =========================================================
// 7. TTS Toggle
// =========================================================

const ttsToggle = document.getElementById("ttsToggle");
const ttsVoice = document.getElementById("ttsVoice");

if (ttsToggle) {
    ttsToggle.onchange = async () => {
        await jsonPost("/api/tts", {
            enabled: ttsToggle.checked,
            voice: ttsVoice.value,
        });
        setStatus("Updated TTS settings.");
    };
}


// =========================================================
// 8. CTA Toggle
// =========================================================

const ctaToggle = document.getElementById("ctaToggle");
const ctaText = document.getElementById("ctaText");
const ctaVoiceToggle = document.getElementById("ctaVoiceToggle");

if (ctaToggle) {
    ctaToggle.onchange = saveCTA;
}
if (ctaText) {
    ctaText.oninput = saveCTA;
}
if (ctaVoiceToggle) {
    ctaVoiceToggle.onchange = saveCTA;
}

async function saveCTA() {
    await jsonPost("/api/cta", {
        enabled: ctaToggle.checked,
        text: ctaText.value,
        voiceover: ctaVoiceToggle.checked,
    });
    setStatus("Updated CTA settings.");
}


// =========================================================
// 9. Overlay style
// =========================================================

const overlaySelect = document.getElementById("overlaySelect");
const overlayBtn = document.getElementById("overlayBtn");

if (overlayBtn) {
    overlayBtn.onclick = async () => {
        const style = overlaySelect.value;
        await jsonPost("/api/overlay", { style });
        setStatus("Overlay applied.");
    };
}


// =========================================================
// 10. Apply Timings
// =========================================================

const timingStdBtn = document.getElementById("timingStdBtn");
const timingSmartBtn = document.getElementById("timingSmartBtn");

if (timingStdBtn) {
    timingStdBtn.onclick = async () => {
        await jsonPost("/api/timings", { smart: false });
        setStatus("Applied standard timings.");
        loadConfig();
    };
}

if (timingSmartBtn) {
    timingSmartBtn.onclick = async () => {
        await jsonPost("/api/timings", { smart: true });
        setStatus("Applied cinematic timings.");
        loadConfig();
    };
}


// =========================================================
// 11. FG Scale
// =========================================================

const fgScaleInput = document.getElementById("fgScaleInput");
const fgScaleBtn = document.getElementById("fgScaleBtn");

if (fgScaleBtn) {
    fgScaleBtn.onclick = async () => {
        const value = parseFloat(fgScaleInput.value);
        await jsonPost("/api/fgscale", { value });
        setStatus("Foreground scale updated.");
    };
}


// =========================================================
// 12. Caption Save
// =========================================================

const saveCaptionsBtn = document.getElementById("saveCaptionsBtn");

if (saveCaptionsBtn) {
    saveCaptionsBtn.onclick = async () => {
        const text = captionEditor.value;
        await jsonPost("/api/save_captions", { text });
        setStatus("Captions saved.");
    };
}


// =========================================================
// 13. Chat Assistant
// =========================================================

const chatBtn = document.getElementById("chatBtn");

if (chatBtn) {
    chatBtn.onclick = async () => {
        const message = chatInput.value;
        const res = await jsonPost("/api/chat", { message });
        chatOutput.textContent = res.reply;
    };
}
