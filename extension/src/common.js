// Shared helpers for the content scripts (extract_indeed.js / extract_reed.js).
// Loaded as a regular (isolated-world) content script before the per-site
// extractor, so functions here are available as globals to it.

// Strip tags + collapse whitespace — mirrors scrapers/jsonld.py's strip_html
// closely enough for description text, which is all we need client-side.
function stripHtml(html) {
  if (!html) return null;
  const div = document.createElement("div");
  div.innerHTML = html;
  const text = (div.textContent || div.innerText || "").replace(/\s+/g, " ").trim();
  return text || null;
}

// Build the exact JobRecord JSON shape scrapers/base.py's from_dict() expects.
// Only known fields are set; everything else is left undefined so the Python
// side's own defaulting (scraped_at, etc.) applies.
function buildJobRecord({
  job_id, source, title, company, company_url, location, location_city,
  location_postcode, salary_text, salary_min, salary_max, salary_period,
  job_type, description, requirements, benefits, posted_at, expires_at, apply_url,
}) {
  return {
    job_id: String(job_id || "").trim(),
    source,
    title: (title || "").trim(),
    company: company || null,
    company_url: company_url || null,
    location: location || null,
    location_city: location_city || null,
    location_postcode: location_postcode || null,
    salary_text: salary_text || null,
    salary_min: salary_min ?? null,
    salary_max: salary_max ?? null,
    salary_period: salary_period || null,
    job_type: job_type || null,
    description: description || null,
    requirements: requirements || [],
    benefits: benefits || [],
    posted_at: posted_at || null,
    expires_at: expires_at || null,
    apply_url: apply_url || null,
    scraped_at: new Date().toISOString(),
  };
}

// Send one captured job to the background service worker, which dedupes by
// (source, job_id) in chrome.storage.local and tracks the badge count.
function sendJob(job) {
  if (!job || !job.job_id || !job.title) return;
  chrome.runtime.sendMessage({ type: "JOB_CAPTURED", job }).catch(() => {});
}

function sendJobs(jobs) {
  (jobs || []).filter((j) => j && j.job_id && j.title).forEach(sendJob);
}

// Debounce so a flurry of DOM mutations (infinite scroll, SPA re-renders)
// triggers one extraction pass instead of dozens.
function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
