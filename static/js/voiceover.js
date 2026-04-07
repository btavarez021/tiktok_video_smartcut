async function generateVoiceoverScript() {
  const statusEl = document.getElementById("voiceoverStatus");
  const box = document.getElementById("voiceoverText");

  if (!box) return;

  const mode = getContentMode();
  if (mode !== "voiceover") {
    if (statusEl) {
      statusEl.textContent = "Switch to Voiceover mode first.";
      statusEl.className = "hint-text";
    }
    return;
  }

  try {
    if (statusEl) {
      statusEl.textContent = "Generating voiceover script…";
      statusEl.className = "hint-text";
    }

    const captionText =
      (document.getElementById("captionsText")?.value || "").trim();

    // Placeholder v1
    const script = captionText
      ? `Come with me—${captionText.replace(/\n+/g, " ")}`
      : "Come with me as I show you this spot.";

    box.value = script;

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
}