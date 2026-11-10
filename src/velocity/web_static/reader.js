// velocity web UI: the source reader beside the chat and History (M9, decided 2026-11-09).
//
// PDFs are drawn by PDF.js (loaded from jsDelivr only when the reader first opens), so
// the page always knows which page the learner is on; text sources show the section's
// markdown. Citations ("Stewart §2.4") and page references ("PDF p. 133") in replies and
// logs become links that open the reader there. Uses $, getJSON, renderMarkdown,
// subject and showView from app.js.

const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299";
const READER_KEY = (slug) => `velocity.reader.${slug}`; // {source, page, section}
const READER_WIDTH_KEY = "velocity.reader.width";

// The subject's sources: [{name, kind: "pdf"|"text", outline: [...], error?}]
let readerSources = [];
// What the reader shows: the source, and the page (PDFs) or section (text sources).
const reader = { source: null, page: null, section: null };
// PDF.js state: the library, one viewer, and the open document.
const pdf = { lib: null, viewer: null, eventBus: null, links: null, task: null, doc: null, name: null, ready: false, pending: null };

// -- sources ---------------------------------------------------------------------------

async function loadReaderSources(slug) {
  if (!slug) {
    readerSources = [];
    return readerSources;
  }
  try {
    readerSources = await getJSON(`/api/subjects/${slug}/sources`);
  } catch {
    readerSources = [];
  }
  return readerSources;
}

const sourceNamed = (name) => readerSources.find((s) => s.name === name);

// The outline entry a PDF page belongs to: the last one starting on or before it.
function sectionAt(source, page) {
  let found = null;
  for (const s of source.outline) if (s.start_page <= page) found = s;
  return found;
}

function remembered(slug) {
  try {
    return JSON.parse(localStorage.getItem(READER_KEY(slug))) || {};
  } catch {
    return {};
  }
}

// Saved per subject: the source last shown, and where each source was left
// ({source, page, section, places: {name: {page, section}}}), so switching between
// books returns to the same place in each.
function remember() {
  const saved = remembered(subject());
  const places = { ...(saved.places || {}) };
  if (reader.source) places[reader.source] = { page: reader.page, section: reader.section };
  try {
    localStorage.setItem(READER_KEY(subject()), JSON.stringify({ ...reader, places }));
  } catch {}
}

const placeOf = (name) => remembered(subject()).places?.[name] || {};

// -- opening and closing ---------------------------------------------------------------

const readerOpen = () => !$("#reader").hidden;

function closeReader() {
  $("#reader").hidden = true;
  $("#reader-divider").hidden = true;
  $("#book-button").classList.remove("open");
  stopPrepareUpdates();
  updateLayout();
}

// With no session open on the Chat tab there is nothing to sit beside, so the book takes
// the middle of the page on its own (the Chat tab then goes back to the start panel).
// Otherwise it sits to the right of the chat or the History log.
function updateLayout() {
  const bookOnly = readerOpen() && activeView === "chat" && !chatStatus.open;
  $("#workspace").classList.toggle("book-only", bookOnly);
  $('.tab[data-view="chat"]').classList.toggle("active", activeView === "chat" && !bookOnly);
}

async function toggleReader() {
  if (readerOpen()) {
    closeReader();
    return;
  }
  const last = remembered(subject());
  const source = sourceNamed(last.source) || readerSources.find((s) => !s.error);
  if (source) await openAt(source.name, { page: last.page, section: last.section });
  else showReaderPanel(); // no sources yet: the "add a source" screen
}

// Open the panel; with no source to show, it offers to add one.
function showReaderPanel() {
  $("#reader").hidden = false;
  $("#reader-divider").hidden = false;
  $("#book-button").classList.add("open");
  const empty = !readerSources.length;
  $("#reader-empty").hidden = !empty;
  for (const el of ["#reader-source", "#reader-section", "#reader-ask"]) $(el).hidden = empty;
  if (empty) {
    $("#pdf-container").hidden = true;
    $("#reader-text").hidden = true;
    $("#reader-page").hidden = true;
    hidePrepare();
    status("");
  }
  updateLayout();
}

// Open the reader at a source's page or section (citations, the Book button, pickers).
async function openAt(name, { page = null, section = null } = {}) {
  const source = sourceNamed(name);
  if (!source) return;
  showReaderPanel();
  if (source.error) {
    status(source.error);
    return;
  }
  if (reader.source !== name) fillSections(source);
  reader.source = name;
  $("#reader-source").value = name;
  if (source.kind === "pdf") {
    if (!page && section) page = source.outline.find((s) => s.id === section)?.start_page;
    await showPdf(source, page || 1);
  } else {
    await showTextSection(source, section || source.outline[0]?.id);
  }
}

function status(text) {
  $("#reader-status").hidden = !text;
  $("#reader-status").textContent = text || "";
}

function fillSources() {
  const select = $("#reader-source");
  select.innerHTML = "";
  for (const s of readerSources) {
    const option = new Option(s.name + (s.error ? " (unavailable)" : ""), s.name);
    option.disabled = Boolean(s.error);
    select.append(option);
  }
}

function fillSections(source) {
  const select = $("#reader-section");
  select.innerHTML = "";
  for (const s of source.outline) {
    const pages = s.start_page ? ` (p. ${s.start_page})` : "";
    const indent = "  ".repeat(Math.max(0, s.level - 1));
    select.append(new Option(indent + s.title + pages, s.id));
  }
}

// -- PDFs ------------------------------------------------------------------------------

async function loadPdfJs() {
  if (pdf.lib) return;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = `${PDFJS}/web/pdf_viewer.css`;
  document.head.append(css);
  const lib = await import(`${PDFJS}/build/pdf.min.mjs`);
  // A worker from another origin is wrapped by PDF.js itself (a blob that imports it).
  lib.GlobalWorkerOptions.workerSrc = `${PDFJS}/build/pdf.worker.min.mjs`;
  globalThis.pdfjsLib = lib; // the viewer module reads the library from here
  const viewer = await import(`${PDFJS}/web/pdf_viewer.mjs`);
  pdf.eventBus = new viewer.EventBus();
  pdf.links = new viewer.PDFLinkService({ eventBus: pdf.eventBus });
  pdf.viewer = new viewer.PDFViewer({
    container: $("#pdf-container"),
    eventBus: pdf.eventBus,
    linkService: pdf.links,
  });
  pdf.links.setViewer(pdf.viewer);
  pdf.eventBus.on("pagesinit", () => {
    pdf.viewer.currentScaleValue = "page-width";
    pdf.ready = true;
    if (pdf.pending) goToPage(pdf.pending);
    pdf.pending = null;
  });
  pdf.eventBus.on("pagechanging", ({ pageNumber }) => pageChanged(pageNumber));
  // Fit the page to the panel's width again whenever the panel changes size (the
  // divider, or the book moving between the middle and the side).
  let fitting = false;
  new ResizeObserver(() => {
    if (fitting || !pdf.ready || !$("#pdf-container").clientWidth) return;
    fitting = true;
    requestAnimationFrame(() => {
      fitting = false;
      pdf.viewer.currentScaleValue = "page-width";
    });
  }).observe($("#pdf-container"));
  pdf.lib = lib;
}

async function showPdf(source, page) {
  $("#reader-text").hidden = true;
  $("#pdf-container").hidden = false;
  try {
    if (pdf.name !== source.name) {
      status(`Opening ${source.name}…`);
      await loadPdfJs();
      await closePdf();
      const url = `/api/subjects/${subject()}/sources/${encodeURIComponent(source.name)}/file`;
      // Fetched in ranges as pages are shown, not all at once.
      pdf.task = pdf.lib.getDocument({ url, disableAutoFetch: true, disableStream: true });
      pdf.doc = await pdf.task.promise;
      pdf.name = source.name;
      pdf.viewer.setDocument(pdf.doc);
      pdf.links.setDocument(pdf.doc, null);
      status("");
    }
  } catch (error) {
    pdf.name = null;
    status(`Could not open ${source.name}: ${error.message}`);
    return;
  }
  goToPage(page);
  refreshPrepare();
}

// Close the open PDF, if any, before opening another. In PDF.js 6, destroy() is on the
// loading task, not on the document.
async function closePdf() {
  pdf.ready = false;
  pdf.pending = null;
  if (!pdf.task) return;
  pdf.viewer.setDocument(null);
  pdf.links.setDocument(null, null);
  const task = pdf.task;
  pdf.task = pdf.doc = pdf.name = null;
  await task.destroy();
}

function goToPage(page) {
  if (!pdf.ready) {
    pdf.pending = page;
    return;
  }
  pdf.viewer.currentPageNumber = Math.min(Math.max(1, page), pdf.doc.numPages);
  pageChanged(pdf.viewer.currentPageNumber);
}

function pageChanged(page) {
  const source = sourceNamed(reader.source);
  if (!source || source.kind !== "pdf") return;
  reader.page = page;
  reader.section = sectionAt(source, page)?.id || null;
  if (reader.section) $("#reader-section").value = reader.section;
  $("#reader-page").hidden = !pdf.doc;
  // Don't overwrite a number the learner is typing.
  if (document.activeElement !== $("#reader-page-input")) $("#reader-page-input").value = page;
  if (pdf.doc) {
    $("#reader-page-input").max = pdf.doc.numPages;
    $("#reader-pages").textContent = pdf.doc.numPages;
  }
  remember();
}

// -- text sources ----------------------------------------------------------------------

async function showTextSection(source, sectionId) {
  $("#pdf-container").hidden = true;
  hidePrepare(); // only PDFs are OCR'd
  const article = $("#reader-text");
  article.hidden = false;
  $("#reader-page").hidden = true; // text sources have no pages
  if (!sectionId) {
    article.innerHTML = '<p class="hint">This source has no sections to show.</p>';
    return;
  }
  try {
    const url = `/api/subjects/${subject()}/sources/${encodeURIComponent(source.name)}/sections/${encodeURIComponent(sectionId)}`;
    const section = await getJSON(url);
    article.replaceChildren(renderMarkdown(section.markdown));
    article.scrollTop = 0;
    reader.page = null;
    reader.section = sectionId;
    $("#reader-section").value = sectionId;
    status("");
    remember();
  } catch (error) {
    status(`Could not show this section: ${error.message}`);
  }
}

// -- citations -------------------------------------------------------------------------

const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Turn "Stewart §2.4" and "PDF p. 133" in rendered text into links that open the reader.
// A page reference belongs to the source named last before it, else to the open PDF,
// else to the first PDF source.
function linkifyCitations(container) {
  if (!readerSources.length) return;
  const names = readerSources.map((s) => escapeRegExp(s.name)).join("|");
  const pattern = new RegExp(`(${names})\\s+§\\s?([A-Za-z0-9][\\w.-]*)|PDF pp?\\.\\s?(\\d+)`, "g");
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentElement.closest("a, code, pre, .katex") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let lastSource = null;
  for (const node of nodes) {
    const text = node.textContent;
    let match;
    let at = 0;
    const parts = [];
    pattern.lastIndex = 0;
    while ((match = pattern.exec(text))) {
      const link = citationLink(match, lastSource);
      if (match[1]) lastSource = match[1];
      if (!link) continue;
      parts.push(text.slice(at, match.index), link);
      // The link may leave out trailing punctuation, which stays as text.
      at = match.index + link.textContent.length;
    }
    if (!parts.length) continue;
    parts.push(text.slice(at));
    node.replaceWith(...parts);
  }
}

function citationLink(match, lastSource) {
  const [whole, name, rawId, page] = match;
  const a = document.createElement("a");
  a.className = "cite";
  a.textContent = whole;
  if (name) {
    const source = sourceNamed(name);
    // "§2.4." at the end of a sentence: drop trailing punctuation the id can't have.
    let id = rawId;
    while (id && !source.outline.some((s) => s.id === id) && /[.-]$/.test(id)) id = id.slice(0, -1);
    if (!source.outline.some((s) => s.id === id)) return null;
    a.dataset.source = name;
    a.dataset.section = id;
    a.title = `Open ${name} §${id} in the reader`;
    a.textContent = whole.slice(0, whole.length - (rawId.length - id.length));
    return a;
  }
  const pdfs = readerSources.filter((s) => s.kind === "pdf" && !s.error);
  const source =
    pdfs.find((s) => s.name === lastSource) || pdfs.find((s) => s.name === reader.source) || pdfs[0];
  if (!source) return null;
  a.dataset.source = source.name;
  a.dataset.page = page;
  a.title = `Open ${source.name} at PDF page ${page}`;
  return a;
}

// -- asking about the page -------------------------------------------------------------

// Start a message with where the learner is in the book; they type the question after it.
function askAboutPage() {
  const source = sourceNamed(reader.source);
  if (!source) return;
  if (!chatStatus.open) {
    status("Start or resume a session first, then ask about the page.");
    return;
  }
  let where = `${source.name}`;
  if (source.kind === "pdf" && reader.page) {
    where += ` PDF p. ${reader.page}` + (reader.section ? ` (§${reader.section})` : "");
  } else if (reader.section) {
    where += ` §${reader.section}`;
  }
  showView("chat");
  const input = $("#input");
  input.value = `About ${where}: ` + input.value.replace(/^About [^\n]*?: /, "");
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

// -- preparing ahead of time (OCR) -----------------------------------------------------

// The open PDF's prepare status from the server: {pages, prepared: [page numbers],
// progress: the job's prepare.json or null, starting, seconds_per_page, startup_seconds}.
const prepare = { status: null, timer: null, dismissed: null, cancelling: false };

const prepareUrl = (name, rest = "") =>
  `/api/subjects/${subject()}/sources/${encodeURIComponent(name)}/prepare${rest}`;

function formatDuration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `about ${minutes} min`;
  return `about ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

function stopPrepareUpdates() {
  clearInterval(prepare.timer);
  prepare.timer = null;
}

function hidePrepare() {
  stopPrepareUpdates();
  $("#reader-prepare").hidden = true;
  $("#prepare-progress").hidden = true;
}

// Fetch the status, then update the ✓ marks and the progress strip. While a job runs,
// this repeats every 3 seconds.
async function refreshPrepare() {
  const source = sourceNamed(reader.source);
  if (!source || source.kind !== "pdf" || !readerOpen()) {
    stopPrepareUpdates();
    return;
  }
  try {
    prepare.status = await getJSON(prepareUrl(source.name));
  } catch {
    return;
  }
  $("#reader-prepare").hidden = false;
  markPrepared(source);
  showPrepareProgress();
  const state = prepare.status.progress?.state;
  const active = prepare.status.starting || state === "running";
  if (active && !prepare.timer) prepare.timer = setInterval(refreshPrepare, 3000);
  if (!active) {
    stopPrepareUpdates();
    prepare.cancelling = false;
  }
}

function missingIn(first, last) {
  const prepared = new Set(prepare.status.prepared);
  let missing = 0;
  for (let p = first; p <= last; p++) if (!prepared.has(p)) missing++;
  return missing;
}

// A ✓ after each section in the menu whose pages are all prepared (instant to read).
function markPrepared(source) {
  for (const option of $("#reader-section").options) {
    const section = source.outline.find((s) => s.id === option.value);
    if (!section?.start_page) continue;
    option.dataset.label ??= option.textContent;
    const ready = missingIn(section.start_page, section.end_page) === 0;
    option.textContent = option.dataset.label + (ready ? " ✓" : "");
  }
}

function showPrepareProgress() {
  const { progress, starting } = prepare.status;
  const strip = $("#prepare-progress");
  const running = progress?.state === "running";
  // A finished job's message stays for an hour, until dismissed.
  const recent = progress && Date.now() / 1000 - progress.updated < 3600;
  const show = starting || running || (recent && progress.started !== prepare.dismissed);
  strip.hidden = !show;
  if (!show) return;
  strip.classList.toggle("failed", ["failed", "interrupted"].includes(progress?.state));
  $("#prepare-cancel").hidden = !(starting || running) || prepare.cancelling;
  $("#prepare-dismiss").hidden = starting || running;
  const bar = $("#prepare-bar");
  if (!running && starting) {
    $("#prepare-title").textContent = "Starting…";
    $("#prepare-detail").textContent = "loading the OCR models, about half a minute";
    bar.removeAttribute("value"); // an indeterminate bar
    return;
  }
  const { done, total, label, state } = progress;
  bar.value = total ? done / total : 1;
  const recheck = progress.kind === "recheck";
  const titles = recheck
    ? {
        running: `Reading again: ${label}`,
        done: `Read again: ${label}`,
        cancelled: `Stopped reading again: ${label}`,
        failed: `Reading again failed: ${label}`,
        interrupted: `Reading again was interrupted: ${label}`,
      }
    : {
        running: `Preparing ${label}`,
        done: `Prepared ${label}`,
        cancelled: `Stopped preparing ${label}`,
        failed: `Preparing ${label} failed`,
        interrupted: `Preparing ${label} was interrupted`,
      };
  $("#prepare-title").textContent = titles[state] || label;
  const parts = [`${done} of ${total} pages (${total ? Math.floor((100 * done) / total) : 100}%)`];
  if (running && prepare.cancelling) {
    parts.push("stopping after the current batch");
  } else if (running) {
    if (progress.next_section) parts.push(`now ${progress.next_section}`);
    parts.push(`${formatDuration(progress.eta_seconds)} ${done ? "left" : "in all"}`);
  } else if (state === "failed") {
    parts.push(progress.error || "see prepare.log in the source's cache folder");
  } else if (state !== "done") {
    parts.push(
      recheck
        ? "the pages read again so far are kept"
        : "finished pages are kept; Prepare again to continue",
    );
  }
  $("#prepare-detail").textContent = parts.join(" · ");
}

// The chapter (top-level outline entry) a page is in.
function chapterAt(source, page) {
  return source.outline.find((s) => s.level === 1 && s.start_page <= page && page <= s.end_page);
}

async function openPrepareDialog() {
  const source = sourceNamed(reader.source);
  if (!source) return;
  // Always this book's numbers: the status refreshes in the background after a switch,
  // and the dialog must not show (or act on) the previous book's.
  try {
    prepare.status = await getJSON(prepareUrl(source.name));
  } catch (error) {
    status(`Could not check this book: ${error.message}`);
    return;
  }
  const st = prepare.status;
  const estimate = (pages) => formatDuration(st.startup_seconds + pages * st.seconds_per_page);
  const chapterChoice = document.querySelector('input[name="prepare-scope"][value="chapter"]');
  const allChoice = document.querySelector('input[name="prepare-scope"][value="all"]');
  $("#prepare-source").textContent = source.name;

  const chapter = chapterAt(source, reader.page || 1);
  const chapterLeft = chapter ? missingIn(chapter.start_page, chapter.end_page) : 0;
  chapterChoice.closest("label").hidden = !chapter;
  chapterChoice.disabled = !chapterLeft;
  if (chapter) {
    const pages = chapter.end_page - chapter.start_page + 1;
    chapterChoice.dataset.section = chapter.id;
    $("#prepare-chapter").textContent = chapterLeft
      ? `This chapter, ${chapter.title} (pp. ${chapter.start_page}–${chapter.end_page}): ` +
        `${chapterLeft} of ${pages} pages to go, ${estimate(chapterLeft)}`
      : `This chapter, ${chapter.title}: already prepared`;
  }
  const allLeft = missingIn(1, st.pages);
  allChoice.disabled = !allLeft;
  $("#prepare-all").textContent = allLeft
    ? `The whole book: ${allLeft.toLocaleString()} of ${st.pages.toLocaleString()} pages ` +
      `to go, ${estimate(allLeft)}`
    : "The whole book: already prepared";

  // Reading prepared pages again: those prepared before the dropped-text check, or all
  // (when a section's text looks wrong).
  const prepared = st.prepared.length;
  $("#prepare-again").hidden = !prepared;
  $("#prepare-unchecked-choice").hidden = !st.unchecked;
  $("#prepare-unchecked").textContent =
    `Read again the ${st.unchecked} pages prepared before velocity checked for ` +
    `dropped text, ${estimate(st.unchecked)}`;
  $("#prepare-recheck-all").textContent =
    `Read all ${prepared.toLocaleString()} prepared pages again, ${estimate(prepared)} ` +
    "(if a section's text looks wrong)";

  (chapterLeft ? chapterChoice : allLeft ? allChoice : null)?.click();
  if (!chapterLeft && !allLeft) {
    const again = st.unchecked ? "recheck-unchecked" : "recheck-all";
    document.querySelector(`input[name="prepare-scope"][value="${again}"]`).checked = true;
  }
  $("#prepare-go").disabled = !(chapterLeft || allLeft || prepared);
  $("#prepare-dialog").showModal();
}

async function startPrepare() {
  const choice = document.querySelector('input[name="prepare-scope"]:checked');
  $("#prepare-dialog").close();
  if (!choice) return;
  const bodies = {
    chapter: { section: choice.dataset.section },
    "recheck-unchecked": { recheck: "unchecked" },
    "recheck-all": { recheck: "all" },
  };
  const body = bodies[choice.value] || {};
  try {
    prepare.status = await postJSON(prepareUrl(reader.source), body);
  } catch (error) {
    status(`Could not start preparing: ${error.message}`);
    return;
  }
  prepare.dismissed = null;
  prepare.cancelling = false;
  showPrepareProgress();
  refreshPrepare();
}

async function cancelPrepare() {
  prepare.cancelling = true;
  showPrepareProgress();
  await postJSON(prepareUrl(reader.source, "/cancel")).catch(() => {});
  refreshPrepare();
}

// -- adding a source -------------------------------------------------------------------

let addedSource = null; // the source just added, for "Prepare it now…"

function addStatus(text, isError = false) {
  const line = $("#add-status");
  line.hidden = !text;
  line.textContent = text;
  line.classList.toggle("error", isError);
}

let libraryEntries = []; // books this subject could add from the library

async function openAddDialog() {
  $("#add-file").value = "";
  $("#add-name").value = "";
  $("#add-cite").textContent = "Name";
  $("#add-go").disabled = false;
  $("#add-prepare").hidden = true;
  addedSource = null;
  addStatus("");
  await fillLibraryPicker();
  $("#add-source").showModal();
}

// The library's books that this subject doesn't use yet, with where each is used.
async function fillLibraryPicker() {
  const all = await getJSON("/api/library").catch(() => []);
  libraryEntries = all.filter((e) => !e.uses.some((u) => u.subject === subject()));
  $("#add-library").hidden = !libraryEntries.length;
  const pick = $("#add-library-pick");
  pick.innerHTML = "";
  pick.append(new Option("Choose a book…", ""));
  for (const e of libraryEntries) pick.append(new Option(`${e.suggested_name} (${e.filename})`, e.id));
  $("#add-library-hint").textContent = "";
}

function onLibraryPick() {
  const entry = libraryEntries.find((e) => e.id === $("#add-library-pick").value);
  if (!entry) {
    $("#add-library-hint").textContent = "";
    return;
  }
  $("#add-file").value = ""; // one or the other
  $("#add-name").value = entry.suggested_name;
  $("#add-cite").textContent = entry.suggested_name;
  const where = entry.uses.map((u) => `${u.subject_name} as “${u.name}”`);
  $("#add-library-hint").textContent = where.length
    ? `Already in ${where.join(", ")}. Prepared pages are shared.`
    : "Uploaded earlier, not used by any subject yet.";
}

// A default name from the file name; the tutor cites sources by name, so short is
// better. "James Stewart - Calculus (8th Edition).pdf" (author - title) gives the
// author's surname, "Stewart"; otherwise the title, shortened at a word.
function nameFromFile(filename) {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[_]+/g, " ").trim();
  const [before, after] = stem.split(/\s+[-–]\s+/);
  if (after !== undefined) {
    const author = before.trim().split(/\s+/);
    if (author.length <= 4) return author[author.length - 1];
  }
  let title = stem.split("(")[0].replace(/-/g, " ").replace(/\s+/g, " ").trim() || stem;
  while (title.length > 30 && title.includes(" ")) title = title.slice(0, title.lastIndexOf(" "));
  return title.replace(/[\s,;:.]+$/, "").slice(0, 40);
}

async function addSource() {
  const file = $("#add-file").files[0];
  const fromLibrary = $("#add-library-pick").value;
  const name = $("#add-name").value.trim();
  if (!file && !fromLibrary) return addStatus("Choose a book from your library, or a file.", true);
  if (!name) return addStatus("Give it a name.", true);
  $("#add-go").disabled = true;
  try {
    if (fromLibrary) {
      addStatus("Adding it from your library…");
      addedSource = await postJSON(`/api/subjects/${subject()}/sources/library`, {
        id: fromLibrary,
        name,
      });
    } else {
      addStatus(`Uploading ${file.name} (${(file.size / 1e6).toFixed(1)} MB) and checking it…`);
      const params = new URLSearchParams({ name, filename: file.name });
      const response = await fetch(`/api/subjects/${subject()}/sources?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `${response.status}`);
      addedSource = data;
    }
  } catch (error) {
    $("#add-go").disabled = false;
    return addStatus(`Could not add it: ${error.message}`, true);
  }
  const found = addedSource.sections
    ? `${addedSource.sections} sections found.`
    : addedSource.warning;
  addStatus(
    `Added ${addedSource.name}. ${found} The tutor can use it from your next session ` +
      "(start a new one, or pause and resume).",
  );
  $("#add-prepare").hidden = addedSource.kind !== "pdf";
  await loadReaderSources(subject());
  fillSources();
  await openAt(addedSource.name);
}

async function prepareAdded() {
  $("#add-source").close();
  await refreshPrepare(); // the status of the new source, for the estimate
  openPrepareDialog();
}

// -- set-up ----------------------------------------------------------------------------

function setUpReader() {
  fillSources();
  try {
    const width = localStorage.getItem(READER_WIDTH_KEY);
    if (width) $("#reader").style.setProperty("--reader-width", width);
  } catch {}
  $("#book-button").addEventListener("click", toggleReader);
  // From the book on its own, the Chat tab goes back to the start panel.
  $('.tab[data-view="chat"]').addEventListener("click", () => {
    if ($("#workspace").classList.contains("book-only")) closeReader();
  });
  $("#reader-close").addEventListener("click", closeReader);
  $("#reader-ask").addEventListener("click", askAboutPage);
  $("#reader-prepare").addEventListener("click", openPrepareDialog);
  $("#reader-add").addEventListener("click", openAddDialog);
  $("#reader-empty-add").addEventListener("click", openAddDialog);
  $("#add-go").addEventListener("click", addSource);
  $("#add-prepare").addEventListener("click", prepareAdded);
  $("#add-close").addEventListener("click", () => $("#add-source").close());
  $("#add-file").addEventListener("change", () => {
    const file = $("#add-file").files[0];
    if (file && $("#add-library-pick").value) {
      $("#add-library-pick").value = ""; // one or the other
      $("#add-library-hint").textContent = "";
      $("#add-name").value = "";
    }
    if (file && !$("#add-name").value.trim()) $("#add-name").value = nameFromFile(file.name);
    $("#add-cite").textContent = $("#add-name").value.trim() || "Name";
  });
  $("#add-library-pick").addEventListener("change", onLibraryPick);
  $("#add-name").addEventListener("input", () => {
    $("#add-cite").textContent = $("#add-name").value.trim() || "Name";
  });
  $("#prepare-go").addEventListener("click", startPrepare);
  $("#prepare-close").addEventListener("click", () => $("#prepare-dialog").close());
  $("#prepare-cancel").addEventListener("click", cancelPrepare);
  $("#prepare-dismiss").addEventListener("click", () => {
    prepare.dismissed = prepare.status?.progress?.started ?? null;
    $("#prepare-progress").hidden = true;
  });
  $("#reader-source").addEventListener("change", (event) =>
    openAt(event.target.value, placeOf(event.target.value)),
  );
  $("#reader-section").addEventListener("change", (event) =>
    openAt(reader.source, { section: event.target.value }),
  );
  // Typing a page number and pressing Enter (or leaving the box) jumps there.
  $("#reader-page-input").addEventListener("change", (event) => {
    const page = Number.parseInt(event.target.value, 10);
    if (page > 0) goToPage(page);
    event.target.blur();
  });
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a.cite");
    if (!link) return;
    event.preventDefault();
    openAt(link.dataset.source, {
      page: link.dataset.page ? Number(link.dataset.page) : null,
      section: link.dataset.section || null,
    });
  });
  setUpDivider();
}

// Drag the divider to resize the reader; the width is remembered.
function setUpDivider() {
  const divider = $("#reader-divider");
  divider.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    divider.setPointerCapture(event.pointerId);
    divider.classList.add("dragging");
    const right = $("#workspace").getBoundingClientRect().right;
    const move = (e) => $("#reader").style.setProperty("--reader-width", `${Math.round(right - e.clientX)}px`);
    const up = () => {
      divider.classList.remove("dragging");
      divider.removeEventListener("pointermove", move);
      try {
        localStorage.setItem(READER_WIDTH_KEY, $("#reader").style.getPropertyValue("--reader-width"));
      } catch {}
    };
    divider.addEventListener("pointermove", move);
    divider.addEventListener("pointerup", up, { once: true });
  });
}

// After a source was removed or renamed (subject settings): reload the list, and keep
// the reader on the same book under its new name, or leave a book that is gone.
async function sourcesChanged(oldName = null, newName = null) {
  await loadReaderSources(subject());
  fillSources();
  if (!reader.source) return;
  const current = reader.source === oldName ? newName : reader.source;
  if (current && sourceNamed(current)) {
    if (current !== reader.source) {
      reader.source = current;
      $("#reader-source").value = current;
      if (pdf.name === oldName) pdf.name = current;
      remember();
    }
    return;
  }
  const wasOpen = readerOpen();
  await resetReader(subject());
  if (wasOpen) await toggleReader();
}

// Another subject: its own sources, and its own last place in the reader.
async function resetReader(slug) {
  closeReader();
  hidePrepare();
  prepare.status = null;
  reader.source = reader.page = reader.section = null;
  await closePdf();
  await loadReaderSources(slug);
  fillSources();
}
