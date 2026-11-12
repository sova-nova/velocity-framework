// velocity web UI: first-run setup and settings (M10, 2026-11-09).
//
// Before velocity is set up (no config.toml), the page shows only the setup screen: pick
// the notes folder (Obsidian vaults found in the home folder are suggested), see what
// this computer can do, and Start. Later, Settings (top right, in tabs) changes the notes
// folder, the colors, the llama.cpp program, the API defaults and the free model. Saving rewrites config.toml; the server
// then runs on the new settings, so the page reloads when the notes folder changes.
// Uses $, getJSON and postJSON from app.js.

function drawChecks(list, checks) {
  list.innerHTML = "";
  for (const c of checks) {
    const item = Object.assign(document.createElement("li"), { className: c.ok ? "ok" : "missing" });
    item.append(Object.assign(document.createElement("span"), { className: "mark", textContent: c.ok ? "✓" : "!" }));
    item.append(Object.assign(document.createElement("strong"), { textContent: c.name }));
    item.append(` ${c.detail}`);
    if (!c.ok) item.append(Object.assign(document.createElement("span"), { className: "fix", textContent: c.fix }));
    list.append(item);
  }
}

function setupStatus(id, text, isError = false) {
  const line = $(id);
  line.hidden = !text;
  line.textContent = text || "";
  line.classList.toggle("status-error", isError);
}

// -- first run -------------------------------------------------------------------------

function showSetup(settings) {
  document.body.classList.add("in-setup");
  $("#setup").hidden = false;
  const box = $("#setup-suggestions");
  box.innerHTML = "";
  if (settings.suggestions.length) {
    box.append(Object.assign(document.createElement("span"), { className: "hint", textContent: "Obsidian vaults found:" }));
  }
  // Recommended, not required: shown only to people who don't seem to use it yet.
  $("#setup-no-obsidian").hidden = settings.suggestions.length > 0;
  for (const vault of settings.suggestions) {
    const pick = Object.assign(document.createElement("button"), {
      textContent: vault.replace(settings.home, "~"),
      title: vault,
    });
    pick.addEventListener("click", () => {
      $("#setup-vault").value = vault;
      $("#setup-create").checked = false;
    });
    box.append(pick);
  }
  $("#setup-vault").value = settings.suggestions[0] || `${settings.home}/velocity`;
  $("#setup-create").checked = !settings.suggestions.length;
  // Offered once, here (desktop app, 2026-11-11); later it is in Settings.
  $("#setup-launcher-choice").hidden = !settings.launcher.supported || settings.launcher.installed;
  drawChecks($("#setup-checks"), settings.checks);
  $("#setup-go").addEventListener("click", finishSetup);
}

async function finishSetup() {
  setupStatus("#setup-status", "Setting up…");
  try {
    await postJSON("/api/settings", {
      vault_path: $("#setup-vault").value,
      create_vault: $("#setup-create").checked,
    });
  } catch (error) {
    return setupStatus("#setup-status", error.message, true);
  }
  if (!$("#setup-launcher-choice").hidden && $("#setup-launcher").checked) {
    // Not worth stopping setup for: Settings offers it again.
    await postJSON("/api/settings/launcher").catch(() => {});
  }
  location.reload(); // now the full app
}

// -- settings --------------------------------------------------------------------------

async function openAppSettings() {
  const s = await getJSON("/api/settings");
  $("#app-vault").value = s.vault_path || "";
  $("#app-llama").value = s.llama_cpp_binary || "";
  const model = $("#app-model");
  model.innerHTML = "";
  model.append(new Option("Model: velocity's default (Opus)", ""));
  for (const m of s.models) model.append(new Option(`Model: ${m[0].toUpperCase()}${m.slice(1)}`, m));
  if (s.api_model && !s.models.includes(s.api_model)) model.append(new Option(`Model: ${s.api_model}`, s.api_model));
  model.value = s.api_model;
  const effort = $("#app-effort");
  effort.innerHTML = "";
  effort.append(new Option("Effort: velocity's default (medium)", ""));
  for (const e of s.efforts) effort.append(new Option(`Effort: ${e}`, e));
  effort.value = s.api_effort;
  showFreeSettings(s);
  showKeys(s.keys);
  ollamaAction = null;
  showOllama();
  drawChecks($("#app-checks"), s.checks);
  showLauncher(s.launcher);
  $("#app-config-path").textContent = s.config_path;
  setupStatus("#app-settings-status", "");
  $("#update-status").textContent = `This is velocity ${s.version}.`;
  $("#update-link").hidden = true;
  showTheme(s.theme);
  showSettingsTab("general");
  $("#app-settings").showModal();
}

// -- Settings → tabs and colors (decided 2026-11-11) -------------------------------------
// One part of the settings at a time; Save still saves the fields of every tab. The colors
// change at once and are saved on their own (config.toml, so the browser page and the app
// share them); the server puts the choice on <html> when it sends the page.

function showSettingsTab(name) {
  for (const tab of document.querySelectorAll(".settings-tabs [data-tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  }
  for (const pane of document.querySelectorAll(".settings-pane")) pane.hidden = pane.dataset.pane !== name;
  $(".settings-panes").scrollTop = 0;
}

function showTheme(theme) {
  for (const b of $("#app-theme").children) b.setAttribute("aria-checked", String(b.dataset.theme === theme));
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

async function pickTheme(theme) {
  const before = document.documentElement.dataset.theme || "system";
  showTheme(theme);
  try {
    await postJSON("/api/settings/theme", { theme });
  } catch (error) {
    showTheme(before);
    setupStatus("#app-settings-status", error.message, true);
  }
}

async function saveAppSettings() {
  try {
    const saved = await postJSON("/api/settings", {
      vault_path: $("#app-vault").value,
      llama_cpp_binary: $("#app-llama").value,
      api_model: $("#app-model").value,
      api_effort: $("#app-effort").value,
      ...freeSettings(),
    });
    engines = await getJSON("/api/engines"); // app.js: the free engine may be usable now
    fillEngines($("#engine"), usableEngine($("#engine").value));
    showStartChoice();
    if (saved.reload) {
      location.reload(); // another notes folder: other subjects
      return;
    }
    drawChecks($("#app-checks"), saved.checks);
    setupStatus("#app-settings-status", "Saved.");
  } catch (error) {
    setupStatus("#app-settings-status", error.message, true);
  }
}

// -- Settings → Ollama and Download model (desktop app PR 3, 2026-11-11) -----------------
// Finds Ollama on this computer; Download model pulls the suggested model and makes
// velocity's copy with room for the tutor's instructions; Use it fills in the free model
// settings and saves them. The download runs in the server; the page polls it.

let ollamaPoll = null;
let ollamaAction = null;
let ollamaInfo = null;
// Set when this page starts a download, so only that download is put to use when done.
let ollamaStartedHere = false;

const gb = (bytes) => (bytes / 1e9).toFixed(1);

async function showOllama() {
  clearTimeout(ollamaPoll);
  let o;
  try {
    o = await getJSON("/api/ollama");
  } catch {
    return;
  }
  const state = $("#ollama-state");
  const go = $("#ollama-go");
  const job = o.download;
  const busy = job && !["done", "failed", "cancelled"].includes(job.phase);
  $("#ollama-get").hidden = o.running;
  $("#ollama-get").href = o.download_page;
  $("#ollama-cancel").hidden = !busy;
  $("#ollama-progress").hidden = !busy;
  go.hidden = busy;
  if (busy) {
    const p = $("#ollama-progress");
    if (job.phase === "downloading" && job.total) {
      p.value = job.completed / job.total;
      state.textContent = `Downloading ${job.model}: ${gb(job.completed)} of ${gb(job.total)} GB…`;
    } else {
      p.removeAttribute("value"); // an indeterminate bar
      state.textContent = job.phase === "preparing"
        ? "Preparing velocity's copy of the model…"
        : `Starting the download of ${job.model}…`;
    }
    if (!$("#app-settings").open) return; // stop polling once Settings is closed
    ollamaPoll = setTimeout(showOllama, 1000);
    return;
  }
  ollamaInfo = o;
  if (job?.phase === "done" && ollamaStartedHere) {
    ollamaStartedHere = false;
    await useOllama(o); // just finished: use it right away
    return;
  }
  if (!o.running) {
    state.textContent =
      "Ollama runs free models on your computer, privately. It isn't running here: install it " +
      "(on Linux it may also be in your app store as “ollama”), start it, then check again.";
    go.textContent = "Check again";
    ollamaAction = "check";
  } else if (o.ready) {
    const inUse = $("#free-model").value === o.velocity_model && $("#free-url").value === o.base_url;
    state.textContent = inUse
      ? `Ollama ${o.version} is running, and velocity's copy of ${o.suggested} is set up.`
      : `Ollama ${o.version} is running, and velocity's copy of ${o.suggested} is downloaded.`;
    go.textContent = "Use it";
    go.hidden = inUse;
    ollamaAction = "use";
  } else {
    const failed = job?.phase === "failed" ? `The download failed: ${job.error} ` : "";
    state.textContent =
      `${failed}Ollama ${o.version} is running. Download ${o.suggested} (about 2.5 GB), the ` +
      "suggested model, with room for the tutor's instructions built in.";
    go.textContent = job?.phase === "failed" ? "Try again" : "Download model";
    ollamaAction = "download";
  }
}

async function ollamaGo() {
  if (ollamaAction === "download") {
    try {
      await postJSON("/api/ollama/download");
      ollamaStartedHere = true;
    } catch (error) {
      $("#ollama-state").textContent = error.message;
      return;
    }
  } else if (ollamaAction === "use") {
    await useOllama(ollamaInfo);
    return;
  }
  showOllama();
}

// Fill in the free model settings for velocity's Ollama model, and save them.
async function useOllama(o) {
  $("#free-preset").value = "ollama";
  showFreeLink();
  $("#free-url").value = o.base_url;
  $("#free-model").value = o.velocity_model;
  $("#free-key-env").value = "";
  $("#free-context").value = o.context;
  await saveAppSettings();
  refreshFreeKey();
  showOllama();
}

async function cancelOllama() {
  await postJSON("/api/ollama/cancel").catch(() => {});
  showOllama();
}

// -- Settings → keys (decided 2026-11-11) -----------------------------------------------
// A key is sent once, to be saved in the system keyring; the page only ever hears back
// where each key comes from. The free model's key belongs to the server in its address.

let keyStatuses = null;

function showKeys(statuses) {
  keyStatuses = statuses;
  for (const field of document.querySelectorAll(".key-field")) {
    const which = field.dataset.key;
    const s = statuses[which];
    const status = field.querySelector(".key-status");
    const save = field.querySelector(".key-save");
    field.querySelector(".key-remove").hidden = !s.saved;
    save.disabled = !statuses.keyring || (which === "openai" && !s.host);
    if (which === "openai" && !s.host) {
      status.textContent = "Fill in the server address first: the key is saved for that server.";
    } else if (s.source === "env") {
      status.textContent = `From ${s.env_var} in the environment, which wins over a saved key.`;
    } else if (s.source === "keyring") {
      status.textContent = `Saved in your system keyring${which === "openai" ? ` for ${s.host}` : ""}.`;
    } else if (which === "openai" && /^(localhost|127\.|::1$|0\.0\.0\.0$)/.test(s.host)) {
      status.textContent = "Not needed: this server is on your computer.";
    } else if (!statuses.keyring) {
      status.textContent =
        "This computer has no system keyring velocity can use. Set the key in an environment variable instead.";
    } else {
      status.textContent =
        "Not set. It's saved in your system keyring (your desktop may ask to unlock it), never in velocity's files.";
    }
  }
}

async function saveKey(field, remove = false) {
  const input = field.querySelector(".key-input");
  const status = field.querySelector(".key-status");
  try {
    const statuses = await postJSON("/api/settings/key", {
      which: field.dataset.key,
      key: input.value,
      remove,
      base_url: $("#free-url").value,
    });
    input.value = ""; // never kept on the page
    showKeys(statuses);
    engines = await getJSON("/api/engines"); // app.js: the API may be usable now
    fillEngines($("#engine"), usableEngine($("#engine").value));
    showStartChoice();
  } catch (error) {
    status.textContent = error.message;
  }
}

async function refreshFreeKey() {
  const url = encodeURIComponent($("#free-url").value);
  showKeys(await getJSON(`/api/settings/keys?base_url=${url}`));
}

// -- Settings → Check for updates (desktop app, 2026-11-11) ----------------------------
// Asks GitHub for the latest release only when pressed; the download is the learner's.

async function checkUpdate() {
  const status = $("#update-status");
  const link = $("#update-link");
  link.hidden = true;
  status.textContent = "Checking…";
  try {
    const r = await postJSON("/api/settings/update");
    if (r.error) {
      status.textContent = `${r.error} (this is version ${r.current})`;
    } else if (r.newer) {
      status.textContent = `Version ${r.latest} is out (this is ${r.current}).`;
      link.href = r.url;
      link.hidden = false;
    } else {
      status.textContent = `You have the latest version (${r.current}).`;
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

// -- Settings → the app menu (desktop app, 2026-11-11) ---------------------------------

function showLauncher(launcher) {
  $("#launcher-row").hidden = !launcher.supported;
  $("#launcher-add").textContent = launcher.installed
    ? "Update velocity in the app menu"
    : "Add velocity to the app menu";
  $("#launcher-status").textContent = launcher.installed
    ? "velocity is in your app menu."
    : "Opens velocity in its own window, without a terminal.";
}

async function addLauncher() {
  try {
    await postJSON("/api/settings/launcher");
    showLauncher({ supported: true, installed: true });
    $("#launcher-status").textContent = "Done: look for velocity in your app menu.";
  } catch (error) {
    $("#launcher-status").textContent = error.message;
  }
}

// -- Settings → Free / other model (decided 2026-11-11) ---------------------------------
// Presets fill in the server (and the key's variable); Test tries the settings as typed,
// before they are saved: does the server answer, and can the model use a tool?

let freePresets = [];

function showFreeSettings(s) {
  freePresets = s.openai_presets || [];
  const preset = $("#free-preset");
  preset.innerHTML = "";
  preset.append(new Option("Your own settings", ""));
  for (const p of freePresets) preset.append(new Option(p.label, p.id));
  $("#free-url").value = s.openai_base_url;
  $("#free-model").value = s.openai_model;
  $("#free-key-env").value = s.openai_api_key_env;
  $("#free-context").value = s.openai_context_size;
  $("#free-images").checked = s.openai_images;
  preset.value = freePresets.find((p) => p.base_url === s.openai_base_url)?.id || "";
  showFreeLink();
  $("#free-test-result").textContent = "";
}

function showFreeLink() {
  const p = freePresets.find((p) => p.id === $("#free-preset").value);
  $("#free-link").hidden = !p?.link;
  if (p?.link) $("#free-link").href = p.link;
}

function pickFreePreset() {
  const p = freePresets.find((p) => p.id === $("#free-preset").value);
  showFreeLink();
  if (!p) return;
  $("#free-url").value = p.base_url;
  $("#free-key-env").value = p.api_key_env;
  // Model names differ between servers, so a preset always replaces the model.
  $("#free-model").value = p.model;
  $("#free-model").placeholder = p.model_hint || "e.g. qwen3:4b";
  $("#free-test-result").textContent = "";
  refreshFreeKey(); // the key belongs to this server
}

function freeSettings() {
  return {
    openai_base_url: $("#free-url").value,
    openai_model: $("#free-model").value,
    openai_api_key_env: $("#free-key-env").value,
    openai_context_size: $("#free-context").value,
    openai_images: $("#free-images").checked,
  };
}

async function testFreeModel() {
  const result = $("#free-test-result");
  const button = $("#free-test");
  button.disabled = true;
  result.classList.remove("status-error");
  result.textContent = "Testing… a model on your computer can take a minute to load.";
  try {
    const r = await postJSON("/api/settings/test-model", freeSettings());
    result.textContent = (r.ok && r.tools ? "✓ " : "! ") + r.message;
    result.classList.toggle("status-error", !r.ok);
  } catch (error) {
    result.textContent = error.message;
    result.classList.add("status-error");
  } finally {
    button.disabled = false;
  }
}

// -- Settings → Find empty sessions… (decided 2026-11-10) -------------------------------
// Lists empty and wrap-up-only sessions in every subject; the open session is never listed,
// and one the learner wrote in (or that a later session continues) is shown as kept.

let cleanupFound = [];

async function openCleanup() {
  $("#cleanup-list").innerHTML = "";
  $("#cleanup-confirm").hidden = true;
  setupStatus("#cleanup-status", "Looking…");
  $("#cleanup").showModal();
  try {
    cleanupFound = await getJSON("/api/cleanup/sessions");
  } catch (error) {
    setupStatus("#cleanup-status", error.message, true);
    return;
  }
  drawCleanup();
}

function drawCleanup() {
  const list = $("#cleanup-list");
  list.innerHTML = "";
  const removable = cleanupFound.filter((c) => !c.kept);
  for (const c of cleanupFound) {
    const item = document.createElement("li");
    item.classList.toggle("kept", Boolean(c.kept));
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = `${c.subject_name} · ${whenText(c.started) || c.session_id}`;
    const why = document.createElement("span");
    why.className = "reason";
    why.textContent = c.kept ? `${c.reason}, kept: ${c.kept}` : c.reason;
    item.append(what, why);
    list.append(item);
  }
  setupStatus("#cleanup-status", cleanupFound.length ? "" : "No empty sessions. Nothing to clean up.");
  const button = $("#cleanup-remove");
  button.hidden = !removable.length;
  button.textContent = `Remove ${removable.length} session${removable.length === 1 ? "" : "s"}…`;
}

function askCleanup() {
  const n = cleanupFound.filter((c) => !c.kept).length;
  $("#cleanup-question").textContent = `Remove ${n} session${n === 1 ? "" : "s"}? This can't be undone.`;
  $("#cleanup-confirm").hidden = false;
}

async function removeCleanup() {
  $("#cleanup-confirm").hidden = true;
  const sessions = cleanupFound.filter((c) => !c.kept).map((c) => ({ subject: c.subject, id: c.session_id }));
  try {
    const { removed } = await postJSON("/api/cleanup/sessions", { sessions });
    cleanupFound = await getJSON("/api/cleanup/sessions");
    drawCleanup();
    setupStatus("#cleanup-status", `Removed ${removed} session${removed === 1 ? "" : "s"}.`);
    if (currentSlug) loadSessions(currentSlug); // History and "Pick up" show what's left
    if (currentSlug && typeof loadPickUp === "function") loadPickUp();
  } catch (error) {
    setupStatus("#cleanup-status", error.message, true);
  }
}

function setUpSettings() {
  $("#cleanup-open").addEventListener("click", openCleanup);
  $("#cleanup-close").addEventListener("click", () => $("#cleanup").close());
  $("#cleanup-remove").addEventListener("click", askCleanup);
  $("#cleanup-no").addEventListener("click", () => ($("#cleanup-confirm").hidden = true));
  $("#cleanup-yes").addEventListener("click", removeCleanup);
  $("#settings-button").addEventListener("click", openAppSettings);
  $("#app-settings-save").addEventListener("click", saveAppSettings);
  $("#app-settings-close").addEventListener("click", () => $("#app-settings").close());
  $("#free-preset").addEventListener("change", pickFreePreset);
  $("#free-test").addEventListener("click", testFreeModel);
  $("#launcher-add").addEventListener("click", addLauncher);
  $("#update-check").addEventListener("click", checkUpdate);
  for (const field of document.querySelectorAll(".key-field")) {
    field.querySelector(".key-save").addEventListener("click", () => saveKey(field));
    field.querySelector(".key-remove").addEventListener("click", () => saveKey(field, true));
  }
  $("#free-url").addEventListener("change", refreshFreeKey);
  $("#ollama-go").addEventListener("click", ollamaGo);
  $("#ollama-cancel").addEventListener("click", cancelOllama);
  for (const tab of document.querySelectorAll(".settings-tabs [data-tab]")) {
    tab.addEventListener("click", () => showSettingsTab(tab.dataset.tab));
  }
  for (const b of $("#app-theme").children) b.addEventListener("click", () => pickTheme(b.dataset.theme));
}
