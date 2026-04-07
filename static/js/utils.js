  function debounce(fn, wait = 350) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

    async function jsonFetch(url, opts = {}) {
    const headers = {
      "Content-Type": "application/json",
      ...(opts.headers || {})
    };

    const res = await fetch(url, {
      credentials: "same-origin",
      ...opts,
      headers
    });

    if (!res.ok) {
      throw new Error(`[jsonFetch] ${url} failed (${res.status})`);
    }

    const text = await res.text();
    if (!text || text.startsWith("<")) return null;
    return JSON.parse(text);
  }

    function countBlocks(text) {
    if (!text) return 0;
    return text.split(/\n\s*\n/).filter(Boolean).length;
  }


    // ================================
  // Emoji-safe overlay text helper
  // ================================
function stripEmojis(text) {
    if (!text) return text;
    return text.replace(/[\p{Extended_Pictographic}]/gu, "").trim();
  }


    // EXPORT URL helper – checks if S3 link is live
async function probeUrl(url) {
      try {
          const res = await fetch(url, { method: "HEAD" });
          return res.ok;
      } catch {
          return false;
      }
  }




