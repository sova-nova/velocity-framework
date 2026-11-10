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
    });
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

function setUpSettings() {
  $("#settings-button").addEventListener("click", openAppSettings);
  $("#app-settings-save").addEventListener("click", saveAppSettings);
  $("#app-settings-close").addEventListener("click", () => $("#app-settings").close());
}
