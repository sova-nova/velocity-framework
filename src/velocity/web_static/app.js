// velocity web UI: subject picker and session history.

const $ = (selector) => document.querySelector(selector);

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

// -- markdown with math, the way Obsidian renders the session log ----------------------

function renderMarkdown(source) {
  // Set code aside first, so "$" inside code isn't taken for math.
  const code = [];
  source = source.replace(/```[\s\S]*?```|`[^`\n]*`/g, (m) => {
    code.push(m);
    return `@@CODE${code.length - 1}@@`;
  });
  const math = [];
  const keep = (tex, display) => {
    math.push({ tex, display });
    return `@@MATH${math.length - 1}@@`;
  };
  source = source
    .replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => keep(tex, true))
    .replace(/\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g, (m, tex) => keep(tex, false))
    .replace(/@@CODE(\d+)@@/g, (m, i) => code[Number(i)]);

  let html = DOMPurify.sanitize(marked.parse(source));
  html = html.replace(/@@MATH(\d+)@@/g, (m, i) => {
    const { tex, display } = math[Number(i)];
    return katex.renderToString(tex, { displayMode: display, throwOnError: false });
  });
  const container = document.createElement("div");
  container.innerHTML = html;
  renderCallouts(container);
  return container;
}

// "> [!example] Title" blockquotes become styled callouts, as in Obsidian.
function renderCallouts(container) {
  for (const quote of container.querySelectorAll("blockquote")) {
    const first = quote.querySelector("p");
    const match = first && first.innerHTML.match(/^\[!(\w+)\][ \t]*([^\n<]*)(?:\n|<br>)?/);
    if (!match) continue;
    first.innerHTML = first.innerHTML.slice(match[0].length);
    if (!first.innerHTML.trim()) first.remove();
    const title = document.createElement("div");
    title.className = "callout-title";
    title.textContent = match[2] || match[1];
    quote.prepend(title);
    quote.classList.add("callout", `callout-${match[1].toLowerCase()}`);
  }
}

// -- history ---------------------------------------------------------------------------

const ENDED = {
  "wrapped-up": "wrapped up",
  "no-wrap-up": "ended without wrap-up",
};

async function loadSessions(slug) {
  const list = $("#sessions");
  list.innerHTML = "";
  $("#log").innerHTML = '<p class="hint">Pick a session to read its log.</p>';
  const sessions = await getJSON(`/api/subjects/${slug}/sessions`);
  if (!sessions.length) {
    list.innerHTML = '<li class="hint">No sessions yet.</li>';
    return;
  }
  for (const s of sessions) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.className = "session";
    const status = s.ended ? ENDED[s.ended] : "open";
    button.innerHTML = `<strong></strong><span class="meta"></span><span class="preview"></span>`;
    button.querySelector("strong").textContent = s.id;
    button.querySelector(".meta").textContent = `${s.turns} messages · ${status}`;
    button.querySelector(".preview").textContent = s.first_message;
    button.addEventListener("click", () => showSession(slug, s.id, button));
    item.append(button);
    list.append(item);
  }
}

async function showSession(slug, id, button) {
  for (const b of document.querySelectorAll(".session")) b.classList.remove("active");
  button.classList.add("active");
  const log = await getJSON(`/api/subjects/${slug}/sessions/${id}`);
  const article = $("#log");
  article.innerHTML = "";
  if (!log.from_log) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "The markdown log is missing; this is rebuilt from the session record.";
    article.append(note);
  }
  article.append(renderMarkdown(log.markdown));
}

// -- start -----------------------------------------------------------------------------

async function start() {
  const subjects = await getJSON("/api/subjects");
  const select = $("#subject");
  if (!subjects.length) {
    $("#log").innerHTML =
      '<p class="hint">No subjects yet. Create one with <code>velocity new "Calculus"</code>.</p>';
    return;
  }
  for (const s of subjects) {
    const option = document.createElement("option");
    option.value = s.slug;
    option.textContent = s.name;
    select.append(option);
  }
  const wanted = new URLSearchParams(location.search).get("subject");
  if (wanted && subjects.some((s) => s.slug === wanted)) select.value = wanted;
  select.addEventListener("change", () => loadSessions(select.value));
  await loadSessions(select.value);
}

window.addEventListener("DOMContentLoaded", () => {
  start().catch((error) => {
    $("#log").innerHTML = "";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = `Could not load: ${error.message}`;
    $("#log").append(p);
  });
});
