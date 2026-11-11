// velocity web UI: first-run setup and settings (M10, 2026-11-09).
//
// Before velocity is set up (no config.toml), the page shows only the setup screen: pick
// the notes folder (Obsidian vaults found in the home folder are suggested), see what
// this computer can do, and Start. Later, Settings (top right) changes the notes folder,
// the llama.cpp program and the API defaults. Saving rewrites config.toml; the server
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
  drawChecks($("#app-checks"), s.checks);
  $("#app-config-path").textContent = s.config_path;
  setupStatus("#app-settings-status", "");
  $("#app-settings").showModal();
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
}
