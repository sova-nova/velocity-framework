// velocity web UI: managing subjects (M10, decided 2026-11-09).
//
// The subject menu (clarity pass) is one button with the subject's color and name. It
// opens a list of the active subjects, then Edit <subject>…, New subject… and, when
// there are any, Archived (n)…. Edit opens the subject's settings: name, extra
// instructions, note folders, and Archive. Archived subjects can be recovered, or
// deleted after typing their name. Uses $, getJSON, postJSON, loadSessions, loadChat and
// resetReader from app.js and reader.js.

let allSubjects = []; // [{slug, name, color, sources, archived, sessions}]
let currentSlug = null; // the subject in use; null when there is none yet

const currentSubject = () => allSubjects.find((s) => s.slug === currentSlug);

// Load the subjects; `wanted` stays in use if it is still active. Returns the one in use.
async function loadSubjects(wanted = currentSlug) {
  allSubjects = await getJSON("/api/subjects");
  const active = allSubjects.filter((s) => !s.archived);
  currentSlug = active.some((s) => s.slug === wanted) ? wanted : (active[0]?.slug ?? null);
  drawSubjectMenu();
  showNoSubjects(!currentSlug);
  return currentSlug;
}

function drawSubjectMenu() {
  const active = allSubjects.filter((s) => !s.archived);
  const archived = allSubjects.filter((s) => s.archived);
  const current = currentSubject();
  $("#subject-name").textContent = current ? current.name : "No subject yet";
  // The open subject's color: a dot here, the edge of the chat bar, and so on.
  document.documentElement.style.setProperty("--subject", current?.color || "var(--accent)");
  const list = $("#subject-list");
  list.innerHTML = "";
  for (const s of active) {
    const item = document.createElement("button");
    item.className = "menu-item";
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", String(s.slug === currentSlug));
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = s.color;
    const name = document.createElement("span");
    name.textContent = s.name;
    item.append(dot, name);
    item.addEventListener("click", () => {
      closeSubjectMenu();
      if (s.slug !== currentSlug) switchSubject(s.slug);
    });
    list.append(item);
  }
  list.hidden = !active.length;
  $("#subject-edit").hidden = !current;
  $("#subject-edit").textContent = current ? `Edit ${current.name}…` : "";
  $("#subject-archived").hidden = !archived.length;
  $("#subject-archived").textContent = `Archived (${archived.length})…`;
}

function openSubjectMenu() {
  $("#subject-popover").hidden = false;
  $("#subject-button").setAttribute("aria-expanded", "true");
  ($("#subject-list [aria-checked='true']") || $("#subject-popover .menu-item:not([hidden])"))?.focus();
}

function closeSubjectMenu() {
  $("#subject-popover").hidden = true;
  $("#subject-button").setAttribute("aria-expanded", "false");
}

// With no subject yet, the chat view offers to create one instead of starting a session.
function showNoSubjects(none) {
  $("#no-subjects").hidden = !none;
  $("#book-button").hidden = none; // books belong to a subject
  if (none) {
    $("#chat-start").hidden = true;
    $("#chat-live").hidden = true;
  }
}

async function switchSubject(slug) {
  currentSlug = slug;
  drawSubjectMenu();
  showNoSubjects(false);
  await resetReader(slug);
  await Promise.all([loadSessions(slug), loadChat()]);
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
  drawSources(settings.sources);
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

// The subject's books and notes, each with Rename and Remove (changes apply at once).
function drawSources(sources) {
  const list = $("#settings-sources");
  list.innerHTML = "";
  if (!sources.length) {
    list.append(Object.assign(document.createElement("li"), { className: "empty", textContent: "none yet" }));
  }
  for (const s of sources) {
    const item = document.createElement("li");
    const row = Object.assign(document.createElement("div"), { className: "row" });
    const what = Object.assign(document.createElement("span"), { className: "what" });
    what.append(Object.assign(document.createElement("strong"), { textContent: s.name }));
    what.append(
      Object.assign(document.createElement("small"), {
        textContent: s.missing ? `${s.filename} (file not found)` : s.filename,
      }),
    );
    const rename = Object.assign(document.createElement("button"), { textContent: "Rename" });
    rename.addEventListener("click", () => askRename(item, s));
    const remove = Object.assign(document.createElement("button"), { textContent: "Remove" });
    remove.addEventListener("click", () => askRemove(item, s));
    row.append(what, rename, remove);
    item.append(row);
    list.append(item);
  }
}

function inlineConfirm(item, parts) {
  item.querySelector(".confirm")?.remove();
  const box = Object.assign(document.createElement("div"), { className: "confirm" });
  box.append(...parts);
  item.append(box);
  return box;
}

async function refreshSources(oldName, newName) {
  const settings = await getJSON(`/api/subjects/${currentSlug}/settings`);
  drawSources(settings.sources);
  await sourcesChanged(oldName, newName);
}

function askRename(item, s) {
  const input = Object.assign(document.createElement("input"), { type: "text", value: s.name });
  const save = Object.assign(document.createElement("button"), { textContent: "Save" });
  const note = Object.assign(document.createElement("small"), {
    className: "hint",
    textContent: "The tutor cites it by this name. Earlier logs keep the old one.",
  });
  save.addEventListener("click", async () => {
    try {
      const renamed = await postJSON(
        `/api/subjects/${currentSlug}/sources/${encodeURIComponent(s.name)}/rename`,
        { name: input.value },
      );
      dialogStatus("#settings-status", `Renamed to ${renamed.name}.`);
      await refreshSources(s.name, renamed.name);
    } catch (error) {
      dialogStatus("#settings-status", error.message, true);
    }
  });
  inlineConfirm(item, [input, save, note]);
  input.focus();
  input.select();
}

function askRemove(item, s) {
  const subjectName = allSubjects.find((x) => x.slug === currentSlug)?.name ?? "this subject";
  const text = Object.assign(document.createElement("span"), {
    textContent: `Remove ${s.name} from ${subjectName}? The file stays in your library.`,
  });
  const go = Object.assign(document.createElement("button"), { textContent: "Remove" });
  const keep = Object.assign(document.createElement("button"), { textContent: "Keep" });
  keep.addEventListener("click", () => item.querySelector(".confirm")?.remove());
  go.addEventListener("click", async () => {
    try {
      await postJSON(`/api/subjects/${currentSlug}/sources/${encodeURIComponent(s.name)}/remove`);
      dialogStatus("#settings-status", `Removed ${s.name}. It's still in your library.`);
      await refreshSources(s.name, null);
    } catch (error) {
      dialogStatus("#settings-status", error.message, true);
    }
  });
  inlineConfirm(item, [text, go, keep]);
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
  $("#subject-button").addEventListener("click", (event) => {
    event.stopPropagation();
    if ($("#subject-popover").hidden) openSubjectMenu();
    else closeSubjectMenu();
  });
  const fromMenu = (open) => () => {
    closeSubjectMenu();
    open();
  };
  $("#subject-edit").addEventListener("click", fromMenu(openSettings));
  $("#subject-new").addEventListener("click", fromMenu(openNewSubject));
  $("#subject-archived").addEventListener("click", fromMenu(openArchived));
  document.addEventListener("click", (event) => {
    if (!$("#subject-menu").contains(event.target)) closeSubjectMenu();
  });
  $("#subject-menu").addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSubjectMenu();
      $("#subject-button").focus();
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if ($("#subject-popover").hidden) return openSubjectMenu();
      const items = [...document.querySelectorAll("#subject-popover .menu-item")].filter((b) => !b.hidden);
      const at = items.indexOf(document.activeElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  });
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
