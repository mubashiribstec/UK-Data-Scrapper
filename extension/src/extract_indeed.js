// Isolated-world content script for uk.indeed.com. Receives the structured
// mosaic data relayed by indeed_main_world.js, with a DOM-selector fallback
// (mirroring scrapers/indeed.py's _extract_job_cards) for pages where the
// embedded JSON isn't present. Also upgrades descriptions to the full text
// when the user opens an individual job page — exactly the data a real
// browser session has access to that the headless scraper loses to CAPTCHA.

const SOURCE_TAG = "uk-data-scrapper-indeed-mosaic";

function mapMosaicItem(item) {
  const jobId = item.jobkey || "";
  if (!jobId) return null;

  let salaryMin = null, salaryMax = null, salaryPeriod = null, salaryText = null;
  const snippet = item.salarySnippet;
  if (snippet && snippet.text) salaryText = snippet.text;
  for (const key of ["extractedSalary", "estimatedSalary"]) {
    const sal = item[key];
    if (sal && (sal.min || sal.max)) {
      salaryMin = sal.min != null ? Number(sal.min) : null;
      salaryMax = sal.max != null ? Number(sal.max) : null;
      const t = String(sal.type || "").toLowerCase();
      salaryPeriod = t.includes("hour") ? "hourly" : (t.includes("year") || t.includes("annual")) ? "annual" : null;
      break;
    }
  }

  let postedAt = null;
  if (item.pubDate) {
    try {
      postedAt = new Date(Number(item.pubDate)).toISOString().slice(0, 10);
    } catch (e) { /* ignore */ }
  }

  const jobTypes = item.jobTypes || [];
  const jobType = jobTypes.length ? jobTypes.map((t) => String(t).toLowerCase()).join(", ") : null;

  let applyUrl = null;
  const link = item.viewJobLink || "";
  if (link.startsWith("/")) applyUrl = `https://uk.indeed.com${link}`;
  else if (jobId) applyUrl = `https://uk.indeed.com/viewjob?jk=${jobId}`;

  return buildJobRecord({
    job_id: jobId,
    source: "indeed",
    title: item.displayTitle || item.title || "Unknown",
    company: item.company || null,
    location: item.formattedLocation || item.jobLocationCity || null,
    location_city: item.jobLocationCity || null,
    location_postcode: item.jobLocationPostal || null,
    salary_text: salaryText,
    salary_min: salaryMin,
    salary_max: salaryMax,
    salary_period: salaryPeriod,
    job_type: jobType,
    posted_at: postedAt,
    apply_url: applyUrl,
    description: stripHtml(item.snippet || ""),
  });
}

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const msg = event.data;
  if (!msg || msg.source !== SOURCE_TAG || !Array.isArray(msg.results)) return;
  const jobs = msg.results.map(mapMosaicItem).filter(Boolean);
  sendJobs(jobs);
});

// DOM fallback (search-results cards) — same selector chains as the Python
// scraper's _extract_job_cards, used when the embedded mosaic JSON is absent.
function extractCardsFromDom() {
  const cards = document.querySelectorAll("[data-jk]");
  const jobs = [];
  cards.forEach((card) => {
    const jobId = card.getAttribute("data-jk") || "";
    if (!jobId) return;

    const pick = (selectors, attr) => {
      for (const sel of selectors) {
        const el = card.querySelector(sel);
        if (el) {
          const val = (attr && el.getAttribute(attr)) || el.textContent.trim();
          if (val) return val.trim();
        }
      }
      return null;
    };

    const title = pick([
      "h2.jobTitle a span[title]", "h2.jobTitle span[title]", "h2.jobTitle a", "h2.jobTitle",
      "[data-testid='jobTitle'] a span[title]", "[data-testid='jobTitle'] a", "[data-testid='jobTitle']",
      "a[id^='job_'] span[title]", "span[title]",
    ], "title") || pick([
      "h2.jobTitle a span[title]", "h2.jobTitle span[title]", "h2.jobTitle a", "h2.jobTitle",
      "[data-testid='jobTitle'] a", "[data-testid='jobTitle']",
    ]) || "Unknown";

    const company = pick([
      "[data-testid='company-name']", ".companyName", "span.companyName",
      "[class*='companyName']", "[class*='company-name']", "a[data-tn-element='companyName']",
    ]);
    const location = pick([
      "[data-testid='text-location']", ".companyLocation", "[class*='companyLocation']",
      "[class*='job-location']", "[class*='resultContent'] [class*='location']",
    ]);
    const salaryText = pick([
      "[data-testid='attribute_snippet_testid']", ".salary-snippet-container",
      "[class*='salary-snippet']", "[class*='salaryText']", ".metadata.salary-snippet-container",
    ]);
    const postedAt = pick(["[data-testid='myJobsStateDate']", "span.date", "[class*='date']"]);

    jobs.push(buildJobRecord({
      job_id: jobId,
      source: "indeed",
      title,
      company,
      location,
      salary_text: salaryText,
      posted_at: postedAt,
      apply_url: `https://uk.indeed.com/viewjob?jk=${jobId}`,
    }));
  });
  return jobs;
}

// Individual job page: full description text the headless scraper often
// loses to CAPTCHA after a handful of page loads.
function extractJobPage() {
  const url = new URL(window.location.href);
  const jobId = url.searchParams.get("jk");
  if (!jobId) return null;

  const descEl = document.querySelector("div#jobDescriptionText");
  if (!descEl) return null;

  const titleEl = document.querySelector("h1, [data-testid='jobsearch-JobInfoHeader-title'], .jobsearch-JobInfoHeader-title");
  const companyEl = document.querySelector("[data-testid='inlineHeader-companyName'], .jobsearch-InlineCompanyRating div, [data-company-name='true']");
  const locationEl = document.querySelector("[data-testid='inlineHeader-companyLocation'], .jobsearch-JobInfoHeader-subtitle div");

  return buildJobRecord({
    job_id: jobId,
    source: "indeed",
    title: titleEl ? titleEl.textContent.trim() : "Unknown",
    company: companyEl ? companyEl.textContent.trim() : null,
    location: locationEl ? locationEl.textContent.trim() : null,
    description: descEl.textContent.trim().slice(0, 8000),
    apply_url: `https://uk.indeed.com/viewjob?jk=${jobId}`,
  });
}

const runDomFallback = debounce(() => {
  // Only used as a fallback — the MAIN-world mosaic message is preferred and
  // arrives first on pages where it's available, so duplicate sends just
  // refresh the same job_id in the background store.
  sendJobs(extractCardsFromDom());
  const jobPageRecord = extractJobPage();
  if (jobPageRecord) sendJob(jobPageRecord);
}, 800);

runDomFallback();
const domObserver = new MutationObserver(runDomFallback);
domObserver.observe(document.documentElement, { childList: true, subtree: true });
