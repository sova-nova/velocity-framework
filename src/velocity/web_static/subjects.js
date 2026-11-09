// velocity web UI: managing subjects (M10, decided 2026-11-09).
//
// The subject menu lists the active subjects, then "New subject…" and, when there are
// any, "Archived (n)…". ⚙ opens the settings of the current subject: name, extra
// instructions, note folders, and Archive. Archived subjects can be recovered, or deleted
// after typing their name. Uses $, getJSON, postJSON, loadSessions, loadChat and
// resetReader from app.js and reader.js.

let allSubjects = []; // [{slug, name, sources, archived, sessions}]
let currentSlug = null; // the subject in use; null when there is none yet

const NEW_SUBJECT = "__new";
const ARCHIVED = "__archived";

// Fill the menu; `wanted` stays selected if it is still active. Returns the selection.
async function loadSubjects(wanted = currentSlug) {
  allSubjects = await getJSON("/api/subjects");
  const active = allSubjects.filter((s) => !s.archived);
  const archived = allSubjects.filter((s) => s.archived);
  const menu = $("#subject");
  menu.innerHTML = "";
  for (const s of active) menu.append(new Option(s.name, s.slug));
  if (active.length) {
    const line = new Option("────────", "");
    line.disabled = true;
    menu.append(line);
  }
  menu.append(new Option("＋ New subject…", NEW_SUBJECT));
  if (archived.length) menu.append(new Option(`Archived (${archived.length})…`, ARCHIVED));
  currentSlug = active.some((s) => s.slug === wanted) ? wanted : (active[0]?.slug ?? null);
  menu.value = currentSlug ?? NEW_SUBJECT;
  showNoSubjects(!currentSlug);
  return currentSlug;
}

// With no subject yet, the chat view offers to create one instead of starting a session.
function showNoSubjects(none) {
  $("#no-subjects").hidden = !none;
  $("#subject-settings-button").hidden = none;
  $("#book-button").hidden = none; // books belong to a subject
  if (none) {
    $("#chat-start").hidden = true;
    $("#chat-live").hidden = true;
  }
}

async function switchSubject(slug) {
  currentSlug = slug;
  $("#subject").value = slug;
  showNoSubjects(false);
  await resetReader(slug);
  await Promise.all([loadSessions(slug), loadChat()]);
}

async function onSubjectMenu() {
  const value = $("#subject").value;
  if (value === NEW_SUBJECT || value === ARCHIVED) {
    $("#subject").value = currentSlug ?? NEW_SUBJECT; // the menu shows the subject in use
    if (value === NEW_SUBJECT) openNewSubject();
    else openArchived();
    return;
  }
  if (value && value !== currentSlug) await switchSubject(value);
}

function dialogStatus(id, text, isError = false) {
  const line = $(id);
  line.hidden = !text;
  line.textContent = text || "";
  line.classList.toggle("status-error", isError);
}

// -- new subject -----------------------------------------------------------------------

function openNewSubject() {
  $("#new-subject-name").value = "";
  dialogStatus("#new-subject-status", "");
  $("#new-subject").showModal();
  $("#new-subject-name").focus();
}

async function createSubject() {
  const name = $("#new-subject-name").value.trim();
  if (!name) return dialogStatus("#new-subject-status", "Give it a name.", true);
  try {
    const created = await postJSON("/api/subjects", { name });
    $("#new-subject").close();
    await loadSubjects(created.slug);
    await switchSubject(created.slug);
    showView("chat");
    showChat({ open: false }, `Created ${created.name}. Add a book with Book → ＋, or start a session.`);
  } catch (error) {
    dialogStatus("#new-subject-status", error.message, true);
  }
}

// -- settings --------------------------------------------------------------------------

let settingsFolders = [];

function drawFolders() {
  const list = $("#settings-folders");
  list.innerHTML = "";
  if (!settingsFolders.length) {
    list.append(Object.assign(document.createElement("li"), { className: "empty", textContent: "none yet" }));
  }
  for (const folder of settingsFolders) {
    const item = document.createElement("li");
    item.append(folder);
    const remove = Object.assign(document.createElement("button"), {
      textContent: "×",
      title: `Stop letting the tutor read ${folder}`,
    });
    remove.addEventListener("click", () => {
      settingsFolders = settingsFolders.filter((f) => f !== folder);
      drawFolders();
    });
    item.append(remove);
    list.append(item);
  }
}

async function openSettings() {
  if (!currentSlug) return;
  dialogStatus("#settings-status", "");
  const [settings, folders] = await Promise.all([
    getJSON(`/api/subjects/${currentSlug}/settings`),
    getJSON("/api/vault/folders").catch(() => []),
  ]);
  $("#settings-name").value = settings.name;
  $("#settings-persona").value = settings.persona;
  settingsFolders = [...settings.note_folders];
  drawFolders();
  const options = $("#vault-folders");
  options.innerHTML = "";
  for (const folder of folders) options.append(new Option(folder));
  $("#settings-folder").value = "";
  $("#subject-settings").showModal();
}

function addFolder() {
  const folder = $("#settings-folder").value.trim().replace(/\/+$/, "");
  if (!folder) return;
  if (!settingsFolders.includes(folder)) settingsFolders.push(folder);
  $("#settings-folder").value = "";
  drawFolders();
}

async function saveSettings() {
  try {
    const saved = await postJSON(`/api/subjects/${currentSlug}/settings`, {
      name: $("#settings-name").value,
      persona: $("#settings-persona").value,
      note_folders: settingsFolders,
    });
    await loadSubjects(currentSlug);
    dialogStatus("#settings-status", `Saved. The tutor uses it from your next session.`);
    settingsFolders = [...saved.note_folders];
    drawFolders();
  } catch (error) {
    dialogStatus("#settings-status", error.message, true);
  }
}

async function archiveCurrent() {
  try {
    await postJSON(`/api/subjects/${currentSlug}/archive`, { archived: true });
  } catch (error) {
    return dialogStatus("#settings-status", error.message, true);
  }
  $("#subject-settings").close();
  const next = await loadSubjects(null);
  if (next) await switchSubject(next);
  else await resetReader(null);
}

// -- archived subjects -----------------------------------------------------------------

function openArchived() {
  dialogStatus("#archived-status", "");
  drawArchived();
  $("#archived-subjects").showModal();
}

function drawArchived() {
  const list = $("#archived-list");
  list.innerHTML = "";
  const archived = allSubjects.filter((s) => s.archived);
  if (!archived.length) {
    list.append(Object.assign(document.createElement("li"), { className: "hint", textContent: "No archived subjects." }));
  }
  for (const s of archived) {
    const item = document.createElement("li");
    const row = Object.assign(document.createElement("div"), { className: "row" });
    const name = Object.assign(document.createElement("strong"), { textContent: s.name });
    const meta = Object.assign(document.createElement("span"), {
      className: "hint",
      textContent: `${s.sessions} session${s.sessions === 1 ? "" : "s"}`,
    });
    const recover = Object.assign(document.createElement("button"), { textContent: "Recover" });
    recover.addEventListener("click", () => recoverSubject(s));
    const remove = Object.assign(document.createElement("button"), { textContent: "Delete…" });
    remove.addEventListener("click", () => askDelete(item, s));
    row.append(name, meta, recover, remove);
    item.append(row);
    list.append(item);
  }
}

async function recoverSubject(s) {
  try {
    await postJSON(`/api/subjects/${s.slug}/archive`, { archived: false });
  } catch (error) {
    return dialogStatus("#archived-status", error.message, true);
  }
  $("#archived-subjects").close();
  await loadSubjects(s.slug);
  await switchSubject(s.slug);
}

// Deleting is for good, so the learner types the subject's name first.
function askDelete(item, s) {
  if (item.querySelector(".confirm")) return;
  const confirm = Object.assign(document.createElement("div"), { className: "confirm" });
  const warning = Object.assign(document.createElement("span"), {
    textContent:
      `This deletes ${s.name}'s session logs, progress and settings for good. ` +
      `Your book files are kept. Type ${s.name} to confirm:`,
  });
  const typed = Object.assign(document.createElement("input"), { type: "text" });
  const go = Object.assign(document.createElement("button"), {
    className: "danger",
    textContent: "Delete permanently",
    disabled: true,
  });
  typed.addEventListener("input", () => (go.disabled = typed.value.trim() !== s.name));
  go.addEventListener("click", async () => {
    try {
      await postJSON(`/api/subjects/${s.slug}/delete`, { confirm: typed.value.trim() });
    } catch (error) {
      return dialogStatus("#archived-status", error.message, true);
    }
    await loadSubjects();
    dialogStatus("#archived-status", `Deleted ${s.name}.`);
    drawArchived();
  });
  confirm.append(warning, typed, go);
  item.append(confirm);
  typed.focus();
}

// -- set-up ----------------------------------------------------------------------------

function setUpSubjects() {
  $("#subject").addEventListener("change", onSubjectMenu);
  $("#subject-settings-button").addEventListener("click", openSettings);
  $("#first-subject").addEventListener("click", openNewSubject);
  $("#new-subject-go").addEventListener("click", createSubject);
  $("#new-subject-close").addEventListener("click", () => $("#new-subject").close());
  $("#new-subject-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") createSubject();
  });
  $("#settings-folder-add").addEventListener("click", addFolder);
  $("#settings-folder").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addFolder();
  });
  $("#settings-save").addEventListener("click", saveSettings);
  $("#settings-close").addEventListener("click", () => $("#subject-settings").close());
  $("#settings-archive").addEventListener("click", archiveCurrent);
  $("#archived-close").addEventListener("click", () => $("#archived-subjects").close());
}
