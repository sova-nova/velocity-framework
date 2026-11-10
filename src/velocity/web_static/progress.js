// velocity web UI: the learner's progress (decided 2026-11-10), shown, never edited (to
// correct something, the learner tells the tutor).
// - The Progress tab: due for review, weak spots, open threads, all topics (click one for
//   its questions), the latest questions. Each section is a card with its own color.
// - "Where you are" on the Chat start panel: due topics, a weak spot, open threads.
// - Practice buttons (Review these, Work on this, Practice) put a ready-made message in the
//   chat (practice() in app.js); the learner edits it and sends it.
// Uses $, getJSON, currentSlug, renderMarkdown, linkifyCitations, practice and showView.

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

// "a, b and c"
function listText(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

// The ready-made messages. Thread text is quoted as the tutor wrote it.
const reviewMessage = (due) => `Let's review the topics that are due: ${listText(due.map((t) => t.name))}.`;
const threadMessage = (t) => `Let's work on this open thread: ${t.text}`;
const topicMessage = (t) => `Let's practice ${t.name}.`;

function practiceButton(label, message, title) {
  const button = el("button", { className: "practice", textContent: label, title });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    practice(message);
  });
  return button;
}

const OUTCOME = { correct: ["✓", "correct"], hinted: ["hint", "hinted"], incorrect: ["✗", "incorrect"] };

function outcomeMark(outcome) {
  const [mark, label] = OUTCOME[outcome] || [outcome, outcome];
  return el("span", { className: `outcome ${outcome}`, textContent: mark, title: label });
}

// A card: a colored bar on the left, a title with a count, an optional action on the right.
function section(kind, title, count, action, ...content) {
  const head = el(
    "div",
    { className: "section-head" },
    el("h3", { textContent: title }),
    count === null ? null : el("span", { className: "count", textContent: String(count) }),
    el("span", { className: "spacer" }),
    action,
  );
  return el("section", { className: `progress-section ${kind}` }, head, ...content);
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
  const row = el(
    "div",
    { className: "topic-row" },
    head,
    practiceButton("Practice", topicMessage(t), "Start practicing this topic in the chat"),
  );
  // The source sits outside the button, so its citation link opens the reader.
  const source = t.source ? richText(t.source, "rich source") : null;
  return el("li", { className: "topic" }, row, source, questions);
}

function threadItem(t) {
  return el(
    "li",
    { className: "thread" },
    el("div", { className: "what" }, richText(t.text), el("span", { className: "meta", textContent: `since ${dayText(t.opened)}` })),
    practiceButton("Work on this", threadMessage(t), "Pick this thread up in the chat"),
  );
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
        "due",
        "Due for review",
        p.due.length,
        practiceButton("Review these", reviewMessage(p.due), "Review the due topics in the chat"),
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
        "weak",
        "Weak spots",
        p.weak.length,
        null,
        el("p", { className: "hint", textContent: "Topics whose last answer wasn't fully right." }),
        el("ul", { className: "item-list" }, ...p.weak.map((w) => questionItem(w.last, { showTopic: true }))),
      ),
    );
  }
  if (p.threads.length) {
    body.append(
      section("threads", "Open threads", p.threads.length, null, el("ul", { className: "item-list" }, ...p.threads.map(threadItem))),
    );
  }
  body.append(
    section(
      "topics",
      "All topics",
      p.topics.length,
      null,
      el("p", { className: "hint", textContent: "Click a topic for its questions." }),
      el("ul", { className: "item-list" }, ...p.topics.map(topicItem)),
    ),
  );
  if (p.recent.length) {
    body.append(
      section(
        "recent",
        "Recent questions",
        null,
        null,
        el("ul", { className: "item-list" }, ...p.recent.map((q) => questionItem(q, { showTopic: true }))),
      ),
    );
  }
}

async function fetchProgress(slug) {
  return getJSON(`/api/subjects/${slug}/progress`);
}

// Reloaded each time the tab opens: the tutor records progress during chats.
async function loadProgress() {
  const status = $("#progress-status");
  status.hidden = true;
  const slug = currentSlug;
  if (!slug) {
    $("#progress-body").innerHTML = "";
    return;
  }
  try {
    const p = await fetchProgress(slug);
    if (slug === currentSlug) drawProgress(p);
  } catch (error) {
    $("#progress-body").innerHTML = "";
    status.hidden = false;
    status.textContent = `Could not load the progress: ${error.message}`;
  }
}

// -- "Where you are" on the Chat start panel ------------------------------------------

const WHERE_NAMES = 2;

function whereLine(label, text, action = null) {
  return el(
    "li",
    {},
    el("span", { className: "what" }, el("strong", { textContent: `${label}: ` }), text),
    action,
  );
}

// At most three lines: due topics, a weak spot, open threads. Hidden when nothing is
// recorded, or when progress can't be read (the Progress tab says why).
async function loadWhere() {
  const box = $("#where");
  const slug = currentSlug;
  if (!slug) {
    box.hidden = true;
    return;
  }
  const p = await fetchProgress(slug).catch(() => null);
  if (slug !== currentSlug) return; // switched meanwhile
  const list = $("#where-list");
  list.innerHTML = "";
  if (p?.due.length) {
    const names = p.due.slice(0, WHERE_NAMES).map((t) => t.name);
    const more = p.due.length - names.length;
    list.append(
      whereLine(
        "Due for review",
        names.join(", ") + (more ? ` and ${more} more` : ""),
        practiceButton("Review these", reviewMessage(p.due), "Review the due topics in the chat"),
      ),
    );
  }
  if (p?.weak.length) {
    const more = p.weak.length - 1;
    list.append(whereLine("Weak spot", p.weak[0].name + (more ? ` (and ${more} more)` : "")));
  }
  if (p?.threads.length) {
    const newest = p.threads.at(-1);
    const text = el("span", {}, `${p.threads.length}. Newest: `, richText(newest.text, "rich inline"));
    list.append(
      whereLine(
        "Open threads",
        text,
        practiceButton("Work on this", threadMessage(newest), "Pick the newest thread up in the chat"),
      ),
    );
  }
  box.hidden = !list.children.length;
}

function setUpProgress() {
  $("#where-all").addEventListener("click", () => showView("progress"));
}
