document.addEventListener("DOMContentLoaded", async () => {
  await initSessionBoot();
  await initAutoAssist(); 
  await initAnalysisBoot();
  await initStoryboardBoot();
  await initCaptionsBoot();
  await initRenderSettingsBoot();

  initSessionListeners();
  initAnalysisListeners();
  initStoryboardListeners();
  initHookListeners();
  initVariantListeners();
  initCaptionListeners();
  initRewriteListeners();
  initUploadListeners();
  initRenderSettingsListeners();
  initExportListeners();
  initChatListeners();
  initStatusLogListeners();
  initScoreListeners();
  initUIListeners();
});