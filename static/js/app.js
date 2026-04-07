  // ================================
  // Variables
  // ================================

  
  





 




  



  // ========================================
  // GLOBAL STATE SNAPSHOT (Debug + Stability)
  // ========================================

  


  








  








  






  





  // =======================================
  // AI Director auto refresh (debounced)
  // =======================================


  















  



  









  




  
  

  




  


  



  














  



 




















  // -------------------------
  // Session helpers
  // -------------------------
  

  



  // =========================================
  // SIDEBAR SESSION MANAGER v2
  // =========================================
  


  


 
  


  // ================================
  // Utility helpers
  // ================================











  





 



  


  








































// ================================
// Init wiring
// ================================
document.addEventListener("DOMContentLoaded", async () => {

  





  

  

  

 














  






    




  



    syncCtaUIState();

    const intentSelect = document.getElementById("intentSelect");

    
    








    

    

  








    

    


   

    document.addEventListener("keydown", (e) => {
        if (
            e.key === "Enter" &&
            e.target.classList.contains("clip-label-input")
        ) {
            e.preventDefault();
            e.target.blur(); // triggers save
        }
    });



  document.getElementById("improveStoryFlowBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("improveStoryFlowBtn");
    const status = document.getElementById("storyFlowStatus");

    if (btn) btn.disabled = true;
    if (status) status.textContent = "Improving story flow…";

    try {
      const res = await jsonFetch("/api/story_flow_improve", {
        method: "POST",
        body: JSON.stringify({ session: getActiveSession() }),
      });

      if (res.updated && res.text) {
        await jsonFetch("/api/save_captions", {
          method: "POST",
          body: JSON.stringify({
            session: getActiveSession(),
            text: res.text
          }),
        });

        CONFIG_CACHE = null;
        lastSavedCaptionsText = res.text;
        workingCaptionsText = res.text;

        if (status) status.textContent = "Story flow improved ✓";

        await loadConfigAndYaml();
        await loadCaptionsFromYaml();
        await refreshAfterChange();
        await runCreativeEngine("captions_changed");

      } else {
        if (status) status.textContent = res.reason || "No changes made.";
      }
    } catch (err) {
      console.error(err);
      if (status) status.textContent = "Failed to improve story flow.";
    } finally {
      if (btn) btn.disabled = false;
    }
  });

    

    

    



    // Sliders / fg-scale UI
    initFgScaleSlider();
    initFgScaleUI();
    initMusicVolumeSlider();

    // Music preview
    initMusicPreview();

    // Upload UI & manager
    initUploadUI();
    loadUploadManager();

    setCaptionInlineStatus(
        "Labels loaded. Regenerate captions to apply them.",
        "info"
        );


    // Music list + settings
    loadMusicTracks();
    loadMusicSettingsFromYaml();

    // YAML + analyses
    refreshAnalyses();
    await loadConfigAndYaml();
    await loadCaptionsFromYaml();   // 🔥 ADD THIS
    loadLayoutFromYaml();


    // Load caption + rewrite mode from YAML/session
    loadCaptionMode();

    


   

    

    // Buttons / actions
    

    document
  .getElementById("applyAiRecommendationBtn")
  ?.addEventListener("click", applyAIRecommendation);




    
    


    


    // STEP 4 — Caption diff collapse
    document.getElementById("toggleDiffCollapse")?.addEventListener("click", () => {
    const scroll = document.getElementById("step4CaptionScroll");
    const btn = document.getElementById("toggleDiffCollapse");

    if (!scroll) return;

    const isHidden = scroll.style.display === "none";

    scroll.style.display = isHidden ? "block" : "none";
    btn.textContent = isHidden ? "Collapse" : "Expand";
    });

    // STEP 3 — Caption diff collapse
    document.getElementById("step3DiffToggle")?.addEventListener("click", () => {
    const scroll = document.getElementById("step3CaptionScroll");
    const btn = document.getElementById("step3DiffToggle");

    if (!scroll) return;

    const isHidden = scroll.style.display === "none";

    scroll.style.display = isHidden ? "block" : "none";
    btn.textContent = isHidden ? "Collapse" : "Expand";
    });


 










    
      






// ========================================
// Step 4 — Caption View Toggles
// ========================================





    // ================================
    // Step 4 Rewrite Mode Init
    // ================================
    


    

    // Initial visual confirmation
    updateHookLockUI();

});