// velocity web UI: the Progress tab (decided 2026-11-10). Show-only: the learner state the
// tutor records, in the order the learner decides what to do next: due for review, weak
// spots, open threads, all topics (click one for its questions), the latest questions.
// To correct something, the learner tells the tutor. Uses $, getJSON, currentSlug,
// renderMarkdown and linkifyCitations.

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

// The tutor's text (questions, notes, threads) with math and citation links.
function richText(text, className = "rich") {
  const container = renderMarkdown(text || "");
  container.className = className;
  linkifyCitations(container);
  return container;
}

// "4 Nov" for a date-only ISO string, read as a local date.
function dayText(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { day: "numeric", month: "short" });
}

function reviewText(days) {
  if (days === null || days === undefined) return "no review set";
  if (days < -1) return `overdue ${-days} days`;
  if (days === -1) return "overdue 1 day";
  if (days === 0) return "due today";
  if (days === 1) return "review tomorrow";
  return `review in ${days} days`;
}

const OUTCOME = { correct: ["✓", "correct"], hinted: ["hint", "hinted"], incorrect: ["✗", "incorrect"] };

function outcomeMark(outcome) {
  const [mark, label] = OUTCOME[outcome] || [outcome, outcome];
  return el("span", { className: `outcome ${outcome}`, textContent: mark, title: label });
}

function section(title, ...content) {
  return el("section", { className: "progress-section" }, el("h3", { textContent: title }), ...content);
}

function questionItem(q, { showTopic = false } = {}) {
  const facts = [dayText(q.date), showTopic ? q.topic_name : null].filter(Boolean).join(" · ");
  return el(
    "li",
    { className: "question" },
    outcomeMark(q.outcome),
    el(
      "div",
      { className: "what" },
      richText(q.question),
      q.note ? richText(q.note, "rich note") : null,
      el("span", { className: "meta", textContent: facts }),
    ),
  );
}

function topicItem(t) {
  const questions = el("ul", { className: "questions", hidden: true });
  if (t.questions.length) questions.append(...t.questions.map((q) => questionItem(q)));
  else questions.append(el("li", { className: "hint", textContent: "No questions on this topic yet." }));
  const count = `${t.questions.length} question${t.questions.length === 1 ? "" : "s"}`;
  const head = el(
    "button",
    { className: "topic-head", title: "Show the questions on this topic" },
    el("strong", { textContent: t.name }),
    el("span", { className: `chip ${t.status}`, textContent: t.status }),
    el("span", { className: `review${t.days !== null && t.days <= 0 ? " due" : ""}`, textContent: reviewText(t.days) }),
    el("span", { className: "meta", textContent: count }),
  );
  head.setAttribute("aria-expanded", "false");
  head.addEventListener("click", () => {
    questions.hidden = !questions.hidden;
    head.setAttribute("aria-expanded", String(!questions.hidden));
  });
  // The source sits outside the button, so its citation link opens the reader.
  const source = t.source ? richText(t.source, "rich source") : null;
  return el("li", { className: "topic" }, head, source, questions);
}

function drawProgress(p) {
  const body = $("#progress-body");
  body.innerHTML = "";
  if (!p.topics.length && !p.threads.length) {
    body.append(
      el("p", {
        className: "hint",
        textContent: "Nothing recorded yet. The tutor records topics, your answers and open threads as you study.",
      }),
    );
    return;
  }
  if (p.due.length) {
    body.append(
      section(
        "Due for review",
        el(
          "ul",
          { className: "due-list" },
          ...p.due.map((t) =>
            el("li", {}, el("strong", { textContent: t.name }), el("span", { className: "review due", textContent: reviewText(t.days) })),
          ),
        ),
      ),
    );
  }
  if (p.weak.length) {
    body.append(
      section(
        "Weak spots",
        el("p", { className: "hint", textContent: "Topics whose last answer wasn't fully right." }),
        el(
          "ul",
          { className: "question-list" },
          ...p.weak.map((w) => questionItem(w.last, { showTopic: true })),
        ),
      ),
    );
  }
  if (p.threads.length) {
    body.append(
      section(
        "Open threads",
        el(
          "ul",
          { className: "thread-list" },
          ...p.threads.map((t) =>
            el("li", {}, richText(t.text), el("span", { className: "meta", textContent: `since ${dayText(t.opened)}` })),
          ),
        ),
      ),
    );
  }
  body.append(
    section(
      "All topics",
      el("p", { className: "hint", textContent: "Click a topic for its questions." }),
      el("ul", { className: "topic-list" }, ...p.topics.map(topicItem)),
    ),
  );
  if (p.recent.length) {
    body.append(
      section(
        "Recent questions",
        el("ul", { className: "question-list" }, ...p.recent.map((q) => questionItem(q, { showTopic: true }))),
      ),
    );
  }
}

// Reloaded each time the tab opens: the tutor records progress during chats.
async function loadProgress() {
  const status = $("#progress-status");
  status.hidden = true;
  if (!currentSlug) {
    $("#progress-body").innerHTML = "";
    return;
  }
  try {
    drawProgress(await getJSON(`/api/subjects/${currentSlug}/progress`));
  } catch (error) {
    $("#progress-body").innerHTML = "";
    status.hidden = false;
    status.textContent = `Could not load the progress: ${error.message}`;
  }
}
