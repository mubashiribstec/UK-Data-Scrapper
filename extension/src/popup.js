const countEl = document.getElementById("count");
const perSourceEl = document.getElementById("perSource");
const autoSendEl = document.getElementById("autoSend");
const receiverUrlEl = document.getElementById("receiverUrl");
const statusEl = document.getElementById("status");

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.className = `status ${ok ? "ok" : "err"}`;
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function refresh() {
  const state = await send({ type: "GET_STATE" });
  countEl.textContent = state.total;
  const parts = Object.entries(state.perSource || {}).map(([s, n]) => `${s}: ${n}`);
  perSourceEl.textContent = parts.length ? parts.join(" · ") : "No jobs captured yet";
  autoSendEl.checked = !!state.settings.autoSend;
  receiverUrlEl.value = state.settings.receiverUrl;
}

autoSendEl.addEventListener("change", async () => {
  await send({ type: "SET_SETTINGS", settings: { autoSend: autoSendEl.checked } });
  setStatus(autoSendEl.checked ? "Auto-send enabled" : "Auto-send disabled", true);
});

receiverUrlEl.addEventListener("change", async () => {
  await send({ type: "SET_SETTINGS", settings: { receiverUrl: receiverUrlEl.value.trim() } });
});

document.getElementById("downloadBtn").addEventListener("click", async () => {
  const { jobs } = await chrome.storage.local.get("jobs");
  const jobList = Object.values(jobs || {});
  const payload = {
    exported_at: new Date().toISOString(),
    total_jobs: jobList.length,
    jobs: jobList,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const filename = `extension_inbox_${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  chrome.downloads.download({ url, filename, saveAs: true }, () => URL.revokeObjectURL(url));
  setStatus(`Downloading ${jobList.length} jobs…`, true);
});

document.getElementById("sendNowBtn").addEventListener("click", async () => {
  setStatus("Sending…", true);
  const res = await send({ type: "SEND_ALL_NOW" });
  if (res.ok) setStatus(`Sent — receiver inbox: ${res.result.total_in_inbox}`, true);
  else setStatus(`Failed: ${res.error}`, false);
});

document.getElementById("runBtn").addEventListener("click", async () => {
  setStatus("Running pipeline…", true);
  const res = await send({ type: "RUN_PIPELINE_NOW" });
  if (res.ok) setStatus(`Done — ${res.result.jobs} jobs, ${res.result.contacts} contacts`, true);
  else setStatus(`Failed: ${res.error}`, false);
});

document.getElementById("clearBtn").addEventListener("click", async () => {
  await send({ type: "CLEAR_JOBS" });
  setStatus("Cleared", true);
  refresh();
});

refresh();
