async function refreshStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const box = document.getElementById("status-log");
        box.textContent = (data.status_log || []).join("\n");
    } catch (e) {
        console.error(e);
    }
}

async function refreshAnalyses() {
    try {
        const res = await fetch("/api/analyses_cache");
        const data = await res.json();
        const list = document.getElementById("analysis-list");
        list.innerHTML = "";

        const entries = Object.entries(data || {});
        if (!entries.length) {
            list.innerHTML = "<li>No analyses yet.</li>";
            return;
        }
        for (const [file, desc] of entries) {
            const li = document.createElement("li");
            li.innerHTML = `<strong>${file}</strong>: ${desc}`;
            list.appendChild(li);
        }
    } catch (e) {
        console.error(e);
    }
}

// ---------------------------
// Upload
// ---------------------------
async function uploadToS3() {
    const input = document.getElementById("file-upload");
    const files = input.files;
    if (!files || !files.length) {
        alert("Select at least one video file.");
        return;
    }

    const formData = new FormData();
    for (const f of files) {
        formData.append("files", f);
    }

    const res = await fetch("/api/upload", {
        method: "POST",
        body: formData
    });

    const data = await res.json();
    if (data.error) {
        alert("Upload error: " + data.error);
    } else {
        alert("Uploaded keys:\n" + (data.uploaded || []).join("\n"));
    }

    refreshStatus();
}

// ---------------------------
// Analyze (step-based)
// ---------------------------
async function analyzeStart() {
    const res = await fetch("/api/analyze_start", { method: "POST" });
    const data = await res.json();
    document.getElementById("analyze-info").textContent =
        `Found ${data.total || 0} videos in S3. Starting analysis...`;
    refreshStatus();

    if ((data.total || 0) > 0) {
        analyzeSteps();
    }
}

async function analyzeSteps() {
    const res = await fetch("/api/analyze_step", { method: "POST" });
    const data = await res.json();
    refreshStatus();

    if (data.key) {
        document.getElementById("analyze-info").textContent =
            `Processed: ${data.index}/${data.total} - ${data.key}`;
    }

    if (!data.done) {
        setTimeout(analyzeSteps, 800);
    } else {
        document.getElementById("analyze-info").textContent =
            "Analysis complete.";
        refreshAnalyses();
    }
}

// ---------------------------
// YAML
// ---------------------------
async function generateYaml() {
    const res = await fetch("/api/generate_yaml", { method: "POST" });
    const data = await res.json();
    const yamlText = document.getElementById("yaml-editor");
    yamlText.value = YAML.stringify(data); // requires js-yaml in HTML
    refreshStatus();
}

async function loadYaml() {
    const res = await fetch("/api/config");
    const data = await res.json();
    document.getElementById("yaml-editor").value = data.yaml || "";
}

async function saveYaml() {
    const yamlText = document.getElementById("yaml-editor").value;
    const res = await fetch("/api/save_yaml", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yaml: yamlText })
    });
    const data = await res.json();
    alert("YAML saved: " + data.status);
}

// ---------------------------
// Captions
// ---------------------------
async function loadCaptions() {
    const res = await fetch("/api/get_captions");
    const data = await res.json();
    document.getElementById("caption-editor").value = data.captions || "";
}

async function saveCaptions() {
    const text = document.getElementById("caption-editor").value;
    const res = await fetch("/api/save_captions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
    });
    const data = await res.json();
    alert("Saved " + data.captions_applied + " captions.");
}

// ---------------------------
// Overlay & timings
// ---------------------------
async function applyOverlay(style) {
    await fetch("/api/overlay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style })
    });
    alert("Overlay applied: " + style);
    refreshStatus();
}

async function applyTimings(smart) {
    await fetch("/api/timings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ smart })
    });
    alert("Timings updated (" + (smart ? "cinematic" : "standard") + ").");
    refreshStatus();
}

// ---------------------------
// TTS, CTA, FG Scale
// ---------------------------
async function toggleTTS() {
    const enabled = document.getElementById("tts-enabled").checked;
    const voice = document.getElementById("tts-voice").value || null;
    await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled, voice })
    });
    refreshStatus();
}

async function saveCTA() {
    const enabled = document.getElementById("cta-enabled").checked;
    const text = document.getElementById("cta-text").value;
    const voiceover = document.getElementById("cta-voiceover").checked;
    await fetch("/api/cta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled, text, voiceover })
    });
    refreshStatus();
}

async function updateFgScale(value) {
    document.getElementById("fgscale-value").textContent = value;
    await fetch("/api/fgscale", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: parseFloat(value) })
    });
}

// ---------------------------
// Export
// ---------------------------
async function exportVideo(optimized) {
    const res = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ optimized })
    });
    const data = await res.json();
    if (data.error) {
        alert("Export error: " + data.error);
        return;
    }
    const link = document.getElementById("download-link");
    link.innerHTML = `<a href="/api/download/${data.filename}" target="_blank">Download ${data.filename}</a>`;
    refreshStatus();
}

// ---------------------------
// Export mode toggle (UI-level flag)
// ---------------------------
async function loadExportMode() {
    const res = await fetch("/api/export_mode");
    const data = await res.json();
    const mode = data.mode || "standard";
    document.getElementById("mode-standard").checked = mode === "standard";
    document.getElementById("mode-optimized").checked = mode === "optimized";
}

async function setExportMode(mode) {
    await fetch("/api/export_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode })
    });
}

// ---------------------------
// Chat assistant
// ---------------------------
async function sendChat() {
    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;

    const history = document.getElementById("chat-history");
    history.value += "You: " + text + "\n";

    const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    history.value += "Assistant: " + (data.reply || "") + "\n\n";
    history.scrollTop = history.scrollHeight;
    input.value = "";
}

// ---------------------------
// Init
// ---------------------------
window.addEventListener("load", () => {
    refreshStatus();
    refreshAnalyses();
    loadYaml();
    loadExportMode();
    setInterval(refreshStatus, 3000);
});
