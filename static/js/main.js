document.addEventListener("DOMContentLoaded", async () => {
  await initSessionBoot();
  await initAutoAssist(); 
  await initAnalysisBoot();
  await initStoryboardBoot();
  await initCaptionsBoot();
  await initRenderSettingsBoot();
  await initVariantBoot();
  await initCaptionBoot();

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