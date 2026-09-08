// State
let sources = [];
let keywords = [];
let presets = { sources: [], keywords: [] };
let selectedImportFile = null;
let currentSourceView = 'list'; // 'list' or 'bulk'

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  await loadPresets();
  initLucide();
});

function initLucide() {
  if (window.lucide) {
    lucide.createIcons();
  }
}

// Show Toast Message
function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  const isSuccess = type === "success";
  const isError = type === "error";

  const bgClass = isSuccess ? "bg-slate-900 border-emerald-500/50 text-emerald-300" :
                  isError ? "bg-slate-900 border-rose-500/50 text-rose-300" :
                  "bg-slate-900 border-indigo-500/50 text-indigo-300";

  toast.className = `pointer-events-auto flex items-center space-x-2 px-4 py-3 rounded-xl border shadow-xl transition-all duration-300 transform translate-y-2 opacity-0 ${bgClass}`;
  
  const icon = isSuccess ? "check-circle" : isError ? "alert-circle" : "info";
  toast.innerHTML = `
    <i data-lucide="${icon}" class="w-4 h-4 flex-shrink-0"></i>
    <span class="text-xs font-medium">${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);
  initLucide();

  setTimeout(() => {
    toast.classList.remove("translate-y-2", "opacity-0");
  }, 10);

  setTimeout(() => {
    toast.classList.add("translate-y-2", "opacity-0");
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Load Settings from API
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      const data = await res.json();
      sources = Array.isArray(data.sources) ? data.sources : [];
      keywords = Array.isArray(data.keywords) ? data.keywords : [];
      renderAll();
    } else {
      showToast("Failed to load settings", "error");
    }
  } catch (err) {
    console.error("Error:", err);
    showToast("Network error loading settings", "error");
  }
}

// Load Presets
async function loadPresets() {
  try {
    const res = await fetch("/api/presets");
    if (res.ok) {
      presets = await res.json();
      renderPresets();
    }
  } catch (err) {
    console.error("Error loading presets:", err);
  }
}

// Render Presets
function renderPresets() {
  const urlContainer = document.getElementById("urlPresetChips");
  const kwContainer = document.getElementById("keywordPresetChips");

  // URL Presets
  urlContainer.innerHTML = (presets.sources || []).map(preset => {
    const isAdded = sources.some(s => s.toLowerCase() === preset.url.toLowerCase());
    return `
      <button onclick="addPresetUrl('${escapeHtml(preset.url)}')" 
              class="text-xs px-2.5 py-1 rounded-lg border transition flex items-center gap-1.5 ${
                isAdded 
                  ? 'bg-slate-900/60 border-slate-800 text-slate-500 cursor-not-allowed' 
                  : 'bg-indigo-950/40 border-indigo-800/50 text-indigo-300 hover:bg-indigo-900/60 hover:text-white'
              }" ${isAdded ? 'disabled' : ''}>
        <i data-lucide="${isAdded ? 'check' : 'plus'}" class="w-3 h-3"></i>
        <span>${escapeHtml(preset.name)}</span>
      </button>
    `;
  }).join("");

  // Keyword Presets
  kwContainer.innerHTML = (presets.keywords || []).map(kw => {
    const isAdded = keywords.some(k => k.toLowerCase() === kw.toLowerCase());
    return `
      <button onclick="addPresetKeyword('${escapeHtml(kw)}')" 
              class="text-xs px-2.5 py-1 rounded-lg border transition flex items-center gap-1.5 ${
                isAdded 
                  ? 'bg-slate-900/60 border-slate-800 text-slate-500 cursor-not-allowed' 
                  : 'bg-emerald-950/40 border-emerald-800/50 text-emerald-300 hover:bg-emerald-900/60 hover:text-white'
              }" ${isAdded ? 'disabled' : ''}>
        <i data-lucide="${isAdded ? 'check' : 'plus'}" class="w-3 h-3"></i>
        <span>${escapeHtml(kw)}</span>
      </button>
    `;
  }).join("");

  initLucide();
}

function renderAll() {
  renderSources();
  renderKeywords();
  renderPresets();
}

// ----------------- NEWS SOURCES (URLs) -----------------

function setSourceView(view) {
  currentSourceView = view;
  const listEl = document.getElementById("sourcesListView");
  const bulkEl = document.getElementById("sourcesBulkView");
  const btnList = document.getElementById("viewBtn-list");
  const btnBulk = document.getElementById("viewBtn-bulk");

  if (view === "list") {
    listEl.classList.remove("hidden");
    bulkEl.classList.add("hidden");
    btnList.className = "px-3 py-1 rounded-md font-medium bg-indigo-600 text-white transition";
    btnBulk.className = "px-3 py-1 rounded-md font-medium text-slate-400 hover:text-white transition";
    renderSources();
  } else {
    listEl.classList.add("hidden");
    bulkEl.classList.remove("hidden");
    btnBulk.className = "px-3 py-1 rounded-md font-medium bg-indigo-600 text-white transition";
    btnList.className = "px-3 py-1 rounded-md font-medium text-slate-400 hover:text-white transition";
    document.getElementById("bulkUrlsTextarea").value = sources.join("\n");
  }
}

function renderSources() {
  const container = document.getElementById("urlItemsContainer");
  const noMsg = document.getElementById("noUrlsMsg");
  const countSpan = document.getElementById("sourcesCount");
  const listCountSpan = document.getElementById("sourcesListCount");

  countSpan.innerText = sources.length;
  listCountSpan.innerText = sources.length;

  if (sources.length === 0) {
    container.innerHTML = "";
    noMsg.classList.remove("hidden");
    return;
  }

  noMsg.classList.add("hidden");
  container.innerHTML = sources.map((url, idx) => {
    let domain = "";
    try {
      domain = new URL(url.startsWith("http") ? url : "https://" + url).hostname;
    } catch(e) {
      domain = "source";
    }

    return `
      <div class="p-3 sm:px-4 flex items-center justify-between hover:bg-slate-900/60 transition gap-3">
        <div class="flex items-center space-x-3 min-w-0 flex-1">
          <span class="text-xs font-mono text-slate-500 w-5 text-right">${idx + 1}.</span>
          <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-mono flex-shrink-0">
            ${escapeHtml(domain)}
          </span>
          <a href="${escapeHtml(url)}" target="_blank" class="text-xs font-mono text-slate-200 hover:text-indigo-400 truncate hover:underline flex items-center gap-1">
            ${escapeHtml(url)}
            <i data-lucide="external-link" class="w-3 h-3 inline opacity-50 flex-shrink-0"></i>
          </a>
        </div>
        <div class="flex items-center space-x-1.5 flex-shrink-0">
          <button onclick="testUrlConnection('${escapeHtml(url)}')" class="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition" title="Test URL Connection">
            <i data-lucide="activity" class="w-3.5 h-3.5"></i>
          </button>
          <button onclick="removeSource(${idx})" class="p-1.5 rounded-lg bg-rose-950/30 hover:bg-rose-900/50 text-rose-400 border border-rose-900/30 transition" title="Remove URL">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>
    `;
  }).join("");

  initLucide();
}

function addSingleUrl() {
  const input = document.getElementById("singleUrlInput");
  let url = input.value.trim();
  if (!url) {
    showToast("Please enter a URL", "error");
    return;
  }

  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    url = "https://" + url;
  }

  if (sources.some(s => s.toLowerCase() === url.toLowerCase())) {
    showToast("URL is already in the list", "info");
    return;
  }

  sources.push(url);
  input.value = "";
  renderSources();
  renderPresets();
  autoSave();
  showToast("URL added successfully");
}

function addPresetUrl(url) {
  if (sources.some(s => s.toLowerCase() === url.toLowerCase())) return;
  sources.push(url);
  renderSources();
  renderPresets();
  autoSave();
  showToast("Preset URL added");
}

function removeSource(index) {
  sources.splice(index, 1);
  renderSources();
  renderPresets();
  autoSave();
  showToast("URL removed");
}

function clearAllSources() {
  if (sources.length === 0) return;
  if (confirm("Are you sure you want to remove all news source URLs?")) {
    sources = [];
    renderSources();
    renderPresets();
    autoSave();
    showToast("All URLs cleared");
  }
}

function applyBulkUrls() {
  const textarea = document.getElementById("bulkUrlsTextarea");
  const lines = textarea.value.split("\n").map(l => l.trim()).filter(l => l.length > 0);
  
  const formatted = [];
  const seen = new Set();

  lines.forEach(line => {
    let url = line;
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      url = "https://" + url;
    }
    if (!seen.has(url.toLowerCase())) {
      seen.add(url.toLowerCase());
      formatted.push(url);
    }
  });

  sources = formatted;
  setSourceView('list');
  renderPresets();
  autoSave();
  showToast(`Applied ${sources.length} URLs`);
}

async function testUrlConnection(url) {
  showToast(`Testing connection to ${url}...`, "info");
  try {
    const res = await fetch("/api/test-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (data.valid) {
      showToast(`Reachable! (${data.message})`, "success");
    } else {
      showToast(`Warning: ${data.message}`, "error");
    }
  } catch (err) {
    showToast(`Test error: ${err.message}`, "error");
  }
}

// ----------------- KEYWORDS -----------------

function renderKeywords() {
  const container = document.getElementById("keywordsContainer");
  const noMsg = document.getElementById("noKeywordsMsg");
  const countSpan = document.getElementById("keywordsCount");
  const listCountSpan = document.getElementById("keywordsListCount");

  countSpan.innerText = keywords.length;
  listCountSpan.innerText = keywords.length;

  if (keywords.length === 0) {
    container.innerHTML = "";
    noMsg.classList.remove("hidden");
    return;
  }

  noMsg.classList.add("hidden");
  container.innerHTML = keywords.map((kw, idx) => {
    return `
      <div class="keyword-pill inline-flex items-center text-xs font-medium px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-800/60 text-emerald-200">
        <i data-lucide="hash" class="w-3 h-3 mr-1 text-emerald-400 opacity-80"></i>
        <span>${escapeHtml(kw)}</span>
        <button onclick="removeKeyword(${idx})" class="ml-2 text-emerald-400/70 hover:text-white transition focus:outline-none" title="Remove keyword">
          <i data-lucide="x" class="w-3 h-3"></i>
        </button>
      </div>
    `;
  }).join("");

  initLucide();
}

function addKeywordsFromInput() {
  const input = document.getElementById("keywordInput");
  const text = input.value.trim();
  if (!text) {
    showToast("Please enter at least one keyword", "error");
    return;
  }

  const items = text.split(/,|\n/).map(k => k.trim()).filter(k => k.length > 0);
  const seen = new Set(keywords.map(k => k.toLowerCase()));
  let added = 0;

  items.forEach(item => {
    if (!seen.has(item.toLowerCase())) {
      seen.add(item.toLowerCase());
      keywords.push(item);
      added++;
    }
  });

  input.value = "";
  renderKeywords();
  renderPresets();
  autoSave();

  if (added > 0) {
    showToast(`Added ${added} keyword(s)`);
  } else {
    showToast("Keywords already existed", "info");
  }
}

function addPresetKeyword(kw) {
  if (keywords.some(k => k.toLowerCase() === kw.toLowerCase())) return;
  keywords.push(kw);
  renderKeywords();
  renderPresets();
  autoSave();
  showToast(`Added: ${kw}`);
}

function removeKeyword(index) {
  keywords.splice(index, 1);
  renderKeywords();
  renderPresets();
  autoSave();
  showToast("Keyword removed");
}

function clearAllKeywords() {
  if (keywords.length === 0) return;
  if (confirm("Are you sure you want to remove all keywords?")) {
    keywords = [];
    renderKeywords();
    renderPresets();
    autoSave();
    showToast("All keywords cleared");
  }
}

// ----------------- SAVE & PERSISTENCE -----------------

async function autoSave() {
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources, keywords })
    });
  } catch (err) {
    console.error("Auto save failed:", err);
  }
}

async function saveAllSettings() {
  const btn = document.getElementById("saveBtn");
  btn.disabled = true;
  btn.innerHTML = `<i data-lucide="loader-2" class="w-3.5 h-3.5 mr-1.5 animate-spin"></i> Saving...`;
  initLucide();

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources, keywords })
    });
    if (res.ok) {
      const data = await res.json();
      sources = data.settings.sources;
      keywords = data.settings.keywords;
      renderAll();
      showToast("Settings saved successfully!", "success");
    } else {
      showToast("Failed to save settings", "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i data-lucide="save" class="w-3.5 h-3.5 mr-1.5"></i> Save Settings`;
    initLucide();
  }
}

async function resetSettings() {
  if (!confirm("Reset all URLs and keywords to default sample list?")) return;
  try {
    const res = await fetch("/api/settings/reset", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      sources = data.settings.sources;
      keywords = data.settings.keywords;
      renderAll();
      showToast("Reset to defaults", "success");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// ----------------- IMPORT MODAL -----------------

function openImportModal() {
  selectedImportFile = null;
  document.getElementById("importFileName").innerText = "No file selected";
  document.getElementById("uploadImportBtn").disabled = true;
  document.getElementById("importModal").classList.remove("hidden");
  initLucide();
}

function closeImportModal() {
  document.getElementById("importModal").classList.add("hidden");
}

function handleFileSelected(e) {
  const file = e.target.files[0];
  if (file) {
    selectedImportFile = file;
    document.getElementById("importFileName").innerText = file.name;
    document.getElementById("uploadImportBtn").disabled = false;
  }
}

async function uploadImportFile() {
  if (!selectedImportFile) return;
  const formData = new FormData();
  formData.append("file", selectedImportFile);

  try {
    const res = await fetch("/api/import", {
      method: "POST",
      body: formData
    });
    if (res.ok) {
      const data = await res.json();
      sources = data.settings.sources;
      keywords = data.settings.keywords;
      renderAll();
      closeImportModal();
      showToast("Settings imported successfully!", "success");
    } else {
      const err = await res.json();
      showToast(err.detail || "Import failed", "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// Utility
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
