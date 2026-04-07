async function generateVoiceoverScript() {
  const text = workingCaptionsText || lastSavedCaptionsText;

  if (!text) {
    setStatus("captionsStatus", "No captions to convert", "warning");
    return;
  }

  setStatus("captionsStatus", "Generating voiceover script…", "working");

  try {
    const res = await jsonFetch("/api/generate_voiceover", {
      method: "POST",
      body: JSON.stringify({
        session: getActiveSession(),
        text
      })
    });

    workingCaptionsText = res.script;

    window.appState.contentMode = "voiceover"; // 🔥 switch mode

    renderCaptionView();

    setStatus("captionsStatus", "Voiceover script ready 🎙", "success");

  } catch (err) {
    console.error(err);
    setStatus("captionsStatus", "Failed to generate script", "error");
  }
}