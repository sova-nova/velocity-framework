// velocity web UI: the Library tab (M10, 2026-11-09): every book once, with the subjects
// that use it, how much is prepared, likely duplicates, "Add to <subject>", and removing
// unused books. Removing deletes only copies velocity made (uploads) and their prepared
// pages; a file of the learner's elsewhere is only forgotten. Uses $, getJSON, postJSON,
// currentSlug, allSubjects, loadReaderSources and fillSources.

let libraryList = [];

const megabytes = (bytes) => `${(bytes / 1e6).toFixed(1)} MB`;

function libraryStatus(text, isError = false) {
  const line = $("#library-status");
  line.hidden = !text;
  line.textContent = text || "";
  line.classList.toggle("status-error", isError);
}

async function loadLibrary() {
  try {
    libraryList = await getJSON("/api/library");
  } catch (error) {
    libraryStatus(`Could not load the library: ${error.message}`, true);
    return;
  }
  drawLibrary();
}

function bookTitle(e) {
  const names = [...new Set(e.uses.map((u) => u.name))];
  return names.length ? names.join(" / ") : e.filename.replace(/\.[^.]+$/, "");
}

function drawLibrary() {
  const list = $("#library-list");
  list.innerHTML = "";
  if (!libraryList.length) {
    list.append(
      Object.assign(document.createElement("li"), {
        className: "hint",
        textContent: "No books yet. Add one with Book → ⋯ → Add a book.",
      }),
    );
    return;
  }
  const subjectName = allSubjects.find((s) => s.slug === currentSlug)?.name;
  for (const e of libraryList) {
    const item = document.createElement("li");
    const row = Object.assign(document.createElement("div"), { className: "row" });
    const what = Object.assign(document.createElement("div"), { className: "what" });
    what.append(
      Object.assign(document.createElement("strong"), { textContent: bookTitle(e), title: e.filename }),
    );
    // One line of facts (L2): kind, size, prepared pages, where it's used.
    const facts = [e.kind === "pdf" ? "PDF" : "Text", megabytes(e.size)];
    if (e.prepared?.done) {
      facts.push(`${e.prepared.done.toLocaleString()} of ${e.prepared.pages.toLocaleString()} pages prepared`);
    }
    const places = [...new Set(e.uses.map((u) => `${u.subject_name}${u.archived ? " (archived)" : ""}`))];
    facts.push(places.length ? `in ${places.join(", ")}` : "not used");
    const meta = Object.assign(document.createElement("div"), { className: "meta", textContent: facts.join(" · ") });
    if (e.duplicate_of) {
      const original = libraryList.find((x) => x.id === e.duplicate_of);
      meta.append(
        " · ",
        Object.assign(document.createElement("span"), {
          className: "copy",
          textContent: `copy of ${original ? `“${bookTitle(original)}”` : "another book"}`,
          title: "Same file name and size as another book in the library",
        }),
      );
    }
    what.append(meta);
    // Only the action that applies (L1).
    const actions = Object.assign(document.createElement("div"), { className: "actions" });
    const here = e.uses.find((u) => u.subject === currentSlug);
    let action = null;
    if (here) {
      action = Object.assign(document.createElement("button"), { textContent: "Open" });
      action.addEventListener("click", () => openFromLibrary(here.name));
    } else if (currentSlug) {
      action = Object.assign(document.createElement("button"), { textContent: `Add to ${subjectName}` });
      action.addEventListener("click", () => addToSubject(e));
    }
    if (action) actions.append(action);
    if (!e.uses.length) {
      const remove = Object.assign(document.createElement("button"), {
        className: "quiet",
        textContent: e.managed ? "Delete…" : "Forget…",
        title: e.managed ? "Delete velocity's copy of this book" : "Take it out of the library; your file stays",
      });
      remove.addEventListener("click", () => askRemoveBook(item, e));
      actions.append(remove);
    }
    row.append(what, actions);
    item.append(row);
    list.append(item);
  }
}

// Open the book in the reader: on its own, or beside the chat when a session is open.
async function openFromLibrary(name) {
  showView("chat");
  await openAt(name, placeOf(name));
}

async function addToSubject(e) {
  try {
    const added = await postJSON(`/api/subjects/${currentSlug}/sources/library`, {
      id: e.id,
      name: e.suggested_name,
    });
    libraryStatus(`Added “${added.name}” to ${allSubjects.find((s) => s.slug === currentSlug)?.name}.`);
    await loadReaderSources(currentSlug);
    fillSources();
    await loadLibrary();
  } catch (error) {
    libraryStatus(error.message, true);
  }
}

// Say exactly what removing does before doing it. Only unused books can be removed.
function askRemoveBook(item, e) {
  item.querySelector(".confirm")?.remove();
  const box = Object.assign(document.createElement("div"), { className: "confirm" });
  const sharesPages = e.prepared && libraryList.some((x) => x.id !== e.id && x.filename === e.filename && x.size === e.size);
  const consequence = e.managed
    ? `Deletes velocity's copy (${megabytes(e.size)})` +
      (e.prepared?.done ? (sharesPages ? "; its prepared pages stay, another copy uses them." : " and its prepared pages.") : ".")
    : "Forgets this file. Your file itself stays where it is.";
  const cancel = Object.assign(document.createElement("button"), { textContent: "Cancel" });
  cancel.addEventListener("click", () => box.remove());
  const go = Object.assign(document.createElement("button"), {
    className: e.managed ? "danger" : "",
    textContent: e.managed ? "Delete" : "Forget",
  });
  go.addEventListener("click", () => removeBook(e));
  const spacer = Object.assign(document.createElement("span"), { className: "spacer" });
  box.append(Object.assign(document.createElement("span"), { textContent: consequence }), spacer, cancel, go);
  item.append(box);
}

async function removeBook(e) {
  try {
    const done = await postJSON(`/api/library/${e.id}/remove`);
    libraryStatus(
      done.deleted
        ? `Deleted ${done.filename}, freeing ${megabytes(done.freed)}.`
        : `Forgot ${done.filename}. The file itself was not touched.`,
    );
    await loadLibrary();
  } catch (error) {
    libraryStatus(error.message, true);
  }
}
