// MV3 service worker: the single source of truth for captured jobs.
// Content scripts send one JOB_CAPTURED message per job; this worker dedupes
// by (source, job_id) in chrome.storage.local, merges repeated captures of
// the same job (keeping the richer description as pages re-render), keeps
// the toolbar badge in sync, and — when auto-send is on — forwards each job
// to the local receiver (receiver.py) immediately.

const DEFAULT_SETTINGS = { autoSend: false, receiverUrl: "http://127.0.0.1:8765" };

function jobKey(job) {
  return `${job.source}|${job.job_id}`;
}

async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  return { ...DEFAULT_SETTINGS, ...(settings || {}) };
}

async function getJobs() {
  const { jobs } = await chrome.storage.local.get("jobs");
  return jobs || {};
}

function mergeJob(existing, incoming) {
  if (!existing) return incoming;
  const merged = { ...existing };
  for (const [k, v] of Object.entries(incoming)) {
    if (v === null || v === undefined || v === "") continue;
    if (k === "description") {
      // Prefer the longer description (full job-page text beats a snippet).
      if (!merged.description || String(v).length > String(merged.description).length) {
        merged.description = v;
      }
      continue;
    }
    if (Array.isArray(v) && v.length === 0) continue;
    merged[k] = v;
  }
  merged.scraped_at = incoming.scraped_at || merged.scraped_at;
  return merged;
}

async function updateBadge() {
  const jobs = await getJobs();
  const count = Object.keys(jobs).length;
  chrome.action.setBadgeText({ text: count ? String(count) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#0a7d2c" });
}

async function captureJob(job) {
  const jobs = await getJobs();
  const key = jobKey(job);
  jobs[key] = mergeJob(jobs[key], job);
  await chrome.storage.local.set({ jobs });
  await updateBadge();

  const settings = await getSettings();
  if (settings.autoSend) {
    sendToReceiver(settings.receiverUrl, [jobs[key]]).catch(() => {
      // Receiver not running — job stays queued in storage either way.
    });
  }
}

async function sendToReceiver(receiverUrl, jobList) {
  const resp = await fetch(`${receiverUrl}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobs: jobList }),
  });
  if (!resp.ok) throw new Error(`receiver returned ${resp.status}`);
  return resp.json();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    switch (message.type) {
      case "JOB_CAPTURED":
        await captureJob(message.job);
        sendResponse({ ok: true });
        break;

      case "GET_STATE": {
        const jobs = await getJobs();
        const settings = await getSettings();
        const perSource = {};
        Object.values(jobs).forEach((j) => {
          perSource[j.source] = (perSource[j.source] || 0) + 1;
        });
        sendResponse({ total: Object.keys(jobs).length, perSource, settings });
        break;
      }

      case "SET_SETTINGS": {
        const settings = await getSettings();
        await chrome.storage.local.set({ settings: { ...settings, ...message.settings } });
        sendResponse({ ok: true });
        break;
      }

      case "CLEAR_JOBS":
        await chrome.storage.local.set({ jobs: {} });
        await updateBadge();
        sendResponse({ ok: true });
        break;

      case "SEND_ALL_NOW": {
        const settings = await getSettings();
        const jobs = await getJobs();
        try {
          const result = await sendToReceiver(settings.receiverUrl, Object.values(jobs));
          sendResponse({ ok: true, result });
        } catch (e) {
          sendResponse({ ok: false, error: String(e) });
        }
        break;
      }

      case "RUN_PIPELINE_NOW": {
        const settings = await getSettings();
        try {
          const resp = await fetch(`${settings.receiverUrl}/api/run`, { method: "POST" });
          const result = await resp.json();
          sendResponse({ ok: resp.ok, result });
        } catch (e) {
          sendResponse({ ok: false, error: String(e) });
        }
        break;
      }

      default:
        sendResponse({ ok: false, error: "unknown message type" });
    }
  })();
  return true; // keep the message channel open for the async response
});

updateBadge();
