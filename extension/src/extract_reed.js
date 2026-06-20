// Content script for www.reed.co.uk. Reed's job pages embed a standard
// schema.org JobPosting JSON-LD block with the full description and
// structured fields — far richer than what's reachable via the API-only
// Python scraper for some listings, and immune to layout/class churn.
// Search-results pages get a DOM-selector fallback for cards.

function jobIdFromUrl(href) {
  try {
    const url = new URL(href, window.location.origin);
    const match = url.pathname.match(/\/jobs\/[^/]*\/([a-zA-Z0-9-]+)/);
    return match ? match[1] : null;
  } catch (e) {
    return null;
  }
}

function parseSalaryFromJsonLd(baseSalary) {
  if (!baseSalary) return { text: null, min: null, max: null, period: null };
  const value = baseSalary.value || {};
  const min = value.minValue != null ? Number(value.minValue) : null;
  const max = value.maxValue != null ? Number(value.maxValue) : null;
  const unit = String(value.unitText || "").toLowerCase();
  const period = unit.includes("hour") ? "hourly" : unit.includes("year") || unit.includes("annum") ? "annual" : null;
  const text = min || max ? `£${min ?? max}${max && min && max !== min ? `–£${max}` : ""}${unit ? ` per ${unit}` : ""}` : null;
  return { text, min, max, period };
}

function mapJsonLdJobPosting(item) {
  const jobId = jobIdFromUrl(item.url || window.location.href) || jobIdFromUrl(window.location.href);
  if (!jobId) return null;

  const salary = parseSalaryFromJsonLd(item.baseSalary);
  const locationObj = item.jobLocation && item.jobLocation.address;
  const location = locationObj
    ? [locationObj.addressLocality, locationObj.postalCode].filter(Boolean).join(", ")
    : null;

  let jobType = null;
  if (item.employmentType) {
    const types = Array.isArray(item.employmentType) ? item.employmentType : [item.employmentType];
    jobType = types.map((t) => String(t).toLowerCase().replace("_", "-")).join(", ");
  }

  return buildJobRecord({
    job_id: jobId,
    source: "reed",
    title: item.title || "Unknown",
    company: item.hiringOrganization ? item.hiringOrganization.name : null,
    company_url: item.hiringOrganization ? item.hiringOrganization.sameAs || null : null,
    location,
    location_postcode: locationObj ? locationObj.postalCode || null : null,
    salary_text: salary.text,
    salary_min: salary.min,
    salary_max: salary.max,
    salary_period: salary.period,
    job_type: jobType,
    description: stripHtml(item.description || ""),
    posted_at: item.datePosted || null,
    expires_at: item.validThrough || null,
    apply_url: item.url || window.location.href,
  });
}

// Returns every JobPosting found across all ld+json blocks on the page —
// some Reed pages (e.g. "similar jobs" rails) embed more than one.
function extractJsonLdJobPostings() {
  const scripts = document.querySelectorAll("script[type='application/ld+json']");
  const jobs = [];
  for (const script of scripts) {
    let data;
    try {
      data = JSON.parse(script.textContent);
    } catch (e) {
      continue;
    }
    const candidates = Array.isArray(data) ? data : [data];
    for (const item of candidates) {
      if (!item || item["@type"] !== "JobPosting") continue;
      const job = mapJsonLdJobPosting(item);
      if (job) jobs.push(job);
    }
  }
  return jobs;
}

// Search-results listing: DOM fallback for job cards (best-effort selectors;
// the JSON-LD path above handles individual job pages reliably).
function extractCardsFromDom() {
  const cardSelectors = [
    "article[data-qa='job-card']",
    "article.job-result",
    "[data-qa='job-card']",
    "article",
  ];
  let cards = [];
  for (const sel of cardSelectors) {
    cards = Array.from(document.querySelectorAll(sel));
    if (cards.length) break;
  }

  const jobs = [];
  cards.forEach((card) => {
    const link = card.querySelector("a[href*='/jobs/']");
    if (!link) return;
    const jobId = jobIdFromUrl(link.href);
    if (!jobId) return;

    const pick = (selectors) => {
      for (const sel of selectors) {
        const el = card.querySelector(sel);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };

    const title = pick(["h2 a", "h3 a", "a[href*='/jobs/']", "h2", "h3"]) || "Unknown";
    const company = pick(["[data-qa='job-card-company']", ".job-result-company", "a[href*='/companies/']"]);
    const location = pick(["[data-qa='job-card-location']", ".job-result-location"]);
    const salaryText = pick(["[data-qa='job-card-salary']", ".job-result-salary"]);

    jobs.push(buildJobRecord({
      job_id: jobId,
      source: "reed",
      title,
      company,
      location,
      salary_text: salaryText,
      apply_url: link.href,
    }));
  });
  return jobs;
}

const run = debounce(() => {
  const jsonLdJobs = extractJsonLdJobPostings();
  if (jsonLdJobs.length) {
    sendJobs(jsonLdJobs);
  } else {
    sendJobs(extractCardsFromDom());
  }
}, 800);

run();
const observer = new MutationObserver(run);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("pagehide", () => observer.disconnect());
