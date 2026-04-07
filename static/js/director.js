async function loadEditStrategy(force=false) {

    if (EDIT_STRATEGY_LOADING && !force) return;

    EDIT_STRATEGY_LOADING = true;

    const panel = document.getElementById("editStrategyPanel");
    const list = document.getElementById("editStrategyList");

    if (!panel || !list) {
      EDIT_STRATEGY_LOADING = false;
      return;
    }

    const delta = window.lastHookImprovementDelta || 0;

    // ⭐ live hook score
    const hookScore =
      Number(document.getElementById("hookScoreValue")?.textContent?.split("/")[0]) || 0;

    // No captions yet
    if (!lastSavedCaptionsText?.trim()) {
      list.innerHTML = `
        <div class="hint-text subtle">
          Create captions to unlock AI direction.
        </div>
      `;
      panel.classList.remove("hidden");
      EDIT_STRATEGY_LOADING = false;
      return;
    }

    

    // ================================
    // 🎥 Footage Intelligence
    // ================================
    const contextEl = document.getElementById("editStrategyContext");
    const setup = document.getElementById("aiSetupSummary");

    if (contextEl && setup?.innerText?.trim()) {
      contextEl.innerHTML = `
        <div class="context-title">🎥 Footage Intelligence</div>
        <div>${setup.innerText}</div>
      `;
      contextEl.classList.remove("hidden");
    }

    try {

      if (LAST_HOOK_SCORE == null || LAST_FLOW_SCORE == null) {
    console.log("Director waiting for scores");

    list.innerHTML = `
      <div class="hint-text subtle">
        Waiting for AI scores…
      </div>
    `;

    EDIT_STRATEGY_LOADING = false;
    return;
  }

      console.log("Director inputs →", {
        hook: LAST_HOOK_SCORE,
        flow: LAST_FLOW_SCORE
      });

      const data = await jsonFetch(
        `/api/edit_strategy?session=${getActiveSession()}`
      );

      const items = data.suggestions || [];

      // ================================
      // 🧠 Prevent useless redraws
      // ================================
      const signature = JSON.stringify({
        hook: LAST_HOOK_SCORE,
        flow: LAST_FLOW_SCORE,
        items: items.map(i => ({
          area: i.area,
          impact: i.impact,
          issue: i.issue
        }))
      });

      if (!force && signature === LAST_DIRECTOR_SIGNATURE) {
    console.log("🧠 Director unchanged — skipping render");

    EDIT_STRATEGY_LOADING = false;   // 🔥 ensure unlock
    return;
  }


      LAST_DIRECTOR_SIGNATURE = signature;

      list.innerHTML = "Analyzing edit…";

      if (!items.length) {
        list.innerHTML = `
          <div class="director-success">
            🎯 All major issues resolved — you're optimized.
          </div>
        `;
        panel.classList.remove("hidden");
        return;
      }

      // ================================
      // Smart next action
      // ================================
      let creativeState = evaluateCreativeState();
      const primaryFocus = creativeState.primary_focus;
      const nextMove =
        primaryFocus === "flow"
          ? "flow"
          : getHookNextMove(hookScore, delta);

      highlightHookAction(
        nextMove === "flow" ? "done" : nextMove
      );

      // Sort high → low
      items.sort((a, b) => {
        return (
          getDirectorPriorityScore(b, creativeState, hookScore) -
          getDirectorPriorityScore(a, creativeState, hookScore)
        );
      });

      

      // ================================
      // Render
      // ================================
      const renderState = creativeState;
      list.classList.add("fade-refresh");

      setTimeout(() => {
        list.innerHTML = items.map(s => {

          const area = (s.area || "").toLowerCase();
          let toneIssue = s.issue;
          let toneImpact = s.impact;

          if (area === "hook") {
            if (hookScore >= 80) {
              toneImpact = "low";
              toneIssue = "🔥 Excellent hook. Focus on pacing or flow next.";
            }
            else if (hookScore >= 60) {
              toneImpact = "medium";
              toneIssue = "👍 Strong hook — a small upgrade could make it elite.";
            }
            else if (delta > 0) {
              toneImpact = "medium";
              toneIssue = "⚠️ Much better — keep pushing toward 70+.";
            }
          }

          // If flow is the primary issue, suppress hook urgency
          if (primaryFocus === "flow" && area === "hook" && hookScore < 75) {
            toneImpact = "low";
            toneIssue = "👍 Hook is good enough for now — fix story flow first.";
          }

          if (primaryFocus === "flow" && (area === "flow" || area === "pacing" || area === "captions")) {
            if (toneImpact !== "high") {
              toneImpact = "high";
            }

            if (!toneIssue || toneIssue.trim() === "") {
              toneIssue = "Story flow needs clearer pacing and stronger transitions.";
            }
          }

          let guidance = `👉 ${s.action}`;

          if (area === "hook") {
            if (nextMove === "generate") {
              guidance = "👉 Generate new ideas — this hook may be hard to fix";
            }
            if (nextMove === "improve") {
              guidance = "👉 Improve this hook — AI will strengthen curiosity & clarity";
            }
            if (nextMove === "auto") {
              guidance = "👉 Let AI auto-optimize for the best score";
            }
            if (nextMove === "done") {
              guidance = "✅ Strong hook — move to story flow";
            }
          }

          // If flow is the real priority, pause hook optimization
          if (primaryFocus === "flow" && area === "hook") {
            guidance = "⏸ Hold hook changes until story flow is improved";
          }

          return `
            <div class="director-item impact-${toneImpact}" data-area="${area}">
              <div class="director-header">
                <div class="director-area">${prettyArea(area)}</div>
                <div class="director-impact">
                  ${toneImpact.toUpperCase()} · ${impactLabel(toneImpact)}
                </div>
              </div>

              ${delta > 0 && area === "hook"
                ? `<div class="director-progress-up">↑ +${delta} points</div>`
                : ""}

              <div class="director-issue">${toneIssue}</div>
              <div class="director-action">${guidance}</div>
            </div>
          `;
        }).join("");

        const remaining = items.length;

        const footer = document.createElement("div");
        footer.className = "director-progress";
        footer.innerHTML = `
          ${remaining === 0
            ? "✅ No major issues detected"
            : `🎯 ${remaining} improvement${remaining > 1 ? "s" : ""} left`
          }
        `;

        list.appendChild(footer);

        list.querySelectorAll(".director-item").forEach(card => {
          card.addEventListener("click", () => {
            const area = card.dataset.area;
            jumpToEditArea(area);
            showGlobalStatus("Jumped to fix location ✨", "info");
          });
        });

        list.classList.remove("fade-refresh");

      }, 120);

      panel.classList.remove("hidden");
      creativeState = evaluateCreativeState();
      renderPublishReadyState(creativeState);
      renderEditProgress();
      document.body.classList.toggle("readiness-ready", creativeState.publish_ready);
    } catch (err) {
      console.error(err);
    }
    finally {
      EDIT_STRATEGY_LOADING = false;
    }
  }


  function getDirectorPriorityScore(item, creativeState, hookScore) {
  const area = (item?.area || "").toLowerCase();
  const focus = creativeState?.primary_focus;
  const impactWeight = { high: 30, medium: 20, low: 10 };

  let score = impactWeight[item?.impact] || 0;

  if (focus === "flow") {
    if (area === "pacing" || area === "captions" || area === "flow") score += 40;
    if (area === "hook") score -= 15;
  }

  if (focus === "hook") {
    if (area === "hook") score += 40;
    if (area === "pacing" || area === "captions" || area === "flow") score -= 10;
  }

  if (creativeState?.publish_ready) {
    score -= 20;
  }

  if (area === "hook" && hookScore >= 80) {
    score -= 20;
  }

  return score;
}

function showDirectorApproval(type) {
    const area = document.getElementById("editStrategyContext");
    if (!area) return;

    const el = document.createElement("div");
    el.className = "director-approved";
    el.textContent = `🎬 Director approved the ${type}`;

    area.appendChild(el);

    setTimeout(() => {
      el.remove();
    }, 2500);
  }

  function jumpToEditArea(area) {
    console.log("🎯 Jump to:", area);

    area = (area || "").toLowerCase();

    if (area === "hook") {
      openStep("#step-3");
      openVariantsDrawer();
      openHookLab();
      return;
    }

    if (area === "captions") {
      openStep("#step-3");
      document.getElementById("captionsText")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "overlay") {
      openStep("#step-4");
      openAccordionSection("✍️ Captions & Timing");
      document.getElementById("overlayStyle")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "cta") {
      openStep("#step-4");
      openAccordionSection("🎤 Voice (TTS) & CTA");
      document.getElementById("ctaText")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    if (area === "pacing") {
      openStep("#step-4");
      openAccordionSection("✍️ Captions & Timing");
      document.getElementById("applyStandardTimingBtn")?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      return;
    }

    console.warn("No jump rule for:", area);
  }

    function openAccordionSection(title) {
    const headers = document.querySelectorAll("#step-4 .acc-header");

    headers.forEach(h => {
      if (h.textContent.includes(title)) {
        h.click();
      }
    });
  }

    function showGlobalStatus(text, type = "info") {
    const bar = document.getElementById("globalStatusBar");
    if (!bar) return;

    bar.textContent = text;
    bar.className = `global-status show ${type}`;

    setTimeout(() => {
      bar.classList.remove("show");
    }, 2500);
  }

  const refreshEditStrategySoon = debounce(() => {
    console.log("🧠 Refreshing AI edit strategy");
    loadEditStrategy();
  }, 400);