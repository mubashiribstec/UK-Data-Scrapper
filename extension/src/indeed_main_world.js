// Runs in the page's MAIN world (see manifest.json "world": "MAIN") so it can
// read window.mosaic.providerData — the same embedded structured-data blob
// scrapers/indeed.py's MOSAIC_RE parses server-side. Relayed to the isolated
// content script (extract_indeed.js) via postMessage, since MAIN-world
// scripts can't use chrome.runtime directly.
(function () {
  const SOURCE_TAG = "uk-data-scrapper-indeed-mosaic";
  let lastSentKey = null;

  function readMosaic() {
    try {
      const data = window.mosaic && window.mosaic.providerData
        && window.mosaic.providerData["mosaic-provider-jobcards"];
      const results = data
        && data.metaData
        && data.metaData.mosaicProviderJobCardsModel
        && data.metaData.mosaicProviderJobCardsModel.results;
      return Array.isArray(results) ? results : null;
    } catch (e) {
      return null;
    }
  }

  function postIfNew(results) {
    if (!results || !results.length) return;
    const key = results.map((r) => r.jobkey).join(",");
    if (key === lastSentKey) return;
    lastSentKey = key;
    window.postMessage({ source: SOURCE_TAG, results }, "*");
  }

  function check() {
    postIfNew(readMosaic());
  }

  check();
  // Indeed re-renders results client-side on pagination/filter changes
  // without a full navigation; a light observer catches that.
  const observer = new MutationObserver(() => check());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  setInterval(check, 2000);
})();
