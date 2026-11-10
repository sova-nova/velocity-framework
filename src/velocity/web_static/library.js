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
        textContent: "No books yet. Add one in a subject with Book → ＋.",
      }),
    );
    return;
  }
  const subjectName = allSubjects.find((s) => s.slug === currentSlug)?.name;
  for (const e of libraryList) {
    const item = document.createElement("li");
    const row = Object.assign(document.createElement("div"), { className: "row" });
    const what = Object.assign(document.createElement("div"), { className: "what" });
    what.append(Object.assign(document.createElement("strong"), { textContent: bookTitle(e) }));
    const meta = [e.filename, megabytes(e.size), e.kind === "pdf" ? "PDF" : "text"];
    if (e.prepared) meta.push(`${e.prepared.done.toLocaleString()} of ${e.prepared.pages.toLocaleString()} pages prepared`);
    what.append(Object.assign(document.createElement("div"), { className: "meta", textContent: meta.join(" · ") }));
    const uses = e.uses.map((u) => `${u.subject_name}${u.archived ? " (archived)" : ""} as “${u.name}”`);
    what.append(
      Object.assign(document.createElement("div"), {
        className: "uses",
        textContent: uses.length ? `Used by ${uses.join(", ")}` : "Not used by any subject",
      }),
    );
    if (e.duplicate_of) {
      const original = libraryList.find((x) => x.id === e.duplicate_of);
      what.append(
        Object.assign(document.createElement("div"), {
          className: "copy",
          textContent: `Looks like a copy of ${original ? `“${bookTitle(original)}”` : "another book"} (same file name and size).`,
        }),
      );
    }
    const actions = Object.assign(document.createElement("div"), { className: "actions" });
    if (currentSlug && !e.uses.some((u) => u.subject === currentSlug)) {
      const add = Object.assign(document.createElement("button"), { textContent: `Add to ${subjectName}` });
      add.addEventListener("click", () => addToSubject(e));
      actions.append(add);
    }
    const remove = Object.assign(document.createElement("button"), { textContent: "Remove…" });
    remove.addEventListener("click", () => askRemoveBook(item, e));
    actions.append(remove);
    row.append(what, actions);
    item.append(row);
    list.append(item);
  }
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

// Say exactly what removing does before doing it.
function askRemoveBook(item, e) {
  item.querySelector(".confirm")?.remove();
  const box = Object.assign(document.createElement("div"), { className: "confirm" });
  if (e.uses.length) {
    const names = e.uses.map((u) => u.subject_name).join(", ");
    box.append(
      Object.assign(document.createElement("span"), {
        textContent: `Still used by ${names}. Remove it there first (⚙ next to the subject).`,
      }),
    );
  } else {
    const sharesPages = e.prepared && libraryList.some((x) => x.id !== e.id && x.filename === e.filename && x.size === e.size);
    const consequence = e.managed
      ? `Deletes velocity's copy (${megabytes(e.size)})` +
        (e.prepared?.done ? (sharesPages ? "; its prepared pages stay, another copy uses them." : " and its prepared pages.") : ".")
      : "Forgets this file. Your file itself stays where it is.";
    const go = Object.assign(document.createElement("button"), {
      className: e.managed ? "danger" : "",
      textContent: e.managed ? "Delete" : "Forget",
    });
    go.addEventListener("click", () => removeBook(e));
    box.append(Object.assign(document.createElement("span"), { textContent: consequence }), go);
  }
  const cancel = Object.assign(document.createElement("button"), { textContent: "Cancel" });
  cancel.addEventListener("click", () => box.remove());
  box.append(cancel);
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
