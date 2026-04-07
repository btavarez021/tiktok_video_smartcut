function setContentMode(mode) {
  window.appState.contentMode = mode;
  updateContentModeUI();

  if (typeof updateVariantModeAvailability === "function") {
    updateVariantModeAvailability();
  }
}

window.generateVoiceoverScript = async function () {
  const statusEl = document.getElementById("voiceoverStatus");
  const box = document.getElementById("voiceoverText");

  if (!box) return;

  if (getContentMode() !== "voiceover") {
    setContentMode("voiceover");
    }

  try {
    if (statusEl) {
      statusEl.textContent = "Generating voiceover script…";
      statusEl.className = "hint-text";
    }

    const captionText =
      (document.getElementById("captionsText")?.value || "").trim();

    if (!captionText) {
      if (statusEl) {
        statusEl.textContent = "No captions available to convert.";
        statusEl.className = "hint-text error";
      }
      return;
    }

    const res = await jsonFetch("/api/generate_voiceover", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        text: captionText
      })
    });

    box.value = res.script || "";

    setContentMode("voiceover");

    if (statusEl) {
      statusEl.textContent = "Voiceover script ready.";
      statusEl.className = "hint-text success";
    }
  } catch (err) {
    console.error(err);
    if (statusEl) {
      statusEl.textContent = "Failed to generate voiceover script.";
      statusEl.className = "hint-text error";
    }
  }
};
