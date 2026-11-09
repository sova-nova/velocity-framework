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
const pdf = { lib: null, viewer: null, eventBus: null, links: null, doc: null, name: null, ready: false, pending: null };

// -- sources ---------------------------------------------------------------------------

async function loadReaderSources(slug) {
  try {
    readerSources = await getJSON(`/api/subjects/${slug}/sources`);
  } catch {
    readerSources = [];
  }
  $("#book-button").hidden = !readerSources.length;
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

function remember() {
  try {
    localStorage.setItem(READER_KEY(subject()), JSON.stringify(reader));
  } catch {}
}

// -- opening and closing ---------------------------------------------------------------

const readerOpen = () => !$("#reader").hidden;

function closeReader() {
  $("#reader").hidden = true;
  $("#reader-divider").hidden = true;
  $("#book-button").classList.remove("open");
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
}

// Open the reader at a source's page or section (citations, the Book button, pickers).
async function openAt(name, { page = null, section = null } = {}) {
  const source = sourceNamed(name);
  if (!source) return;
  $("#reader").hidden = false;
  $("#reader-divider").hidden = false;
  $("#book-button").classList.add("open");
  updateLayout();
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
      pdf.ready = false;
      if (pdf.doc) await pdf.doc.destroy();
      const url = `/api/subjects/${subject()}/sources/${encodeURIComponent(source.name)}/file`;
      // Fetched in ranges as pages are shown, not all at once.
      pdf.doc = await pdf.lib.getDocument({ url, disableAutoFetch: true, disableStream: true }).promise;
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
  $("#reader-source").addEventListener("change", (event) => openAt(event.target.value));
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

// Another subject: its own sources, and its own last place in the reader.
async function resetReader(slug) {
  closeReader();
  reader.source = reader.page = reader.section = null;
  if (pdf.doc) {
    await pdf.doc.destroy();
    pdf.doc = null;
    pdf.name = null;
    pdf.ready = false;
  }
  await loadReaderSources(slug);
  fillSources();
}
