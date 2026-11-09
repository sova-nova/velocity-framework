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
  "no-wrap-up": "paused",
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
    const status = s.ended ? ENDED[s.ended] : "open now";
    button.innerHTML = `<strong></strong><span class="meta"></span><span class="preview"></span>`;
    button.querySelector("strong").textContent = s.title || s.id;
    const meta = [s.title ? s.id : null, `${s.turns} messages`, status];
    if (s.continues) meta.push(`continues ${s.continues}`);
    button.querySelector(".meta").textContent = meta.filter(Boolean).join(" · ");
    button.querySelector(".preview").textContent = s.title ? "" : s.first_message;
    button.addEventListener("click", () => showSession(slug, s.id, button));
    item.append(button);
    // Paused sessions can be resumed in place; finished ones continued in a new session.
    const action =
      s.ended === "no-wrap-up" ? ["Resume", { resume: s.id }]
      : s.ended === "wrapped-up" ? ["Continue", { continues: s.id }]
      : null;
    if (action) {
      const go = document.createElement("button");
      go.className = "session-action";
      go.textContent = action[0];
      const target = s.title || s.id;
      const goOn = (options) => {
        if (chatStatus.open) {
          askToSwitch(action[0], target, options);
          return;
        }
        showView("chat");
        startChat(options);
      };
      go.addEventListener("click", () =>
        action[0] === "Resume"
          ? askResume(target, s.last_engine || {}, (choice) => goOn({ ...action[1], ...choice }))
          : goOn(action[1]),
      );
      item.append(go);
    }
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
  linkifyCitations(article);
}

// -- views -----------------------------------------------------------------------------

let activeView = "chat";

function showView(name) {
  activeView = name;
  for (const tab of document.querySelectorAll(".tab[data-view]")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  $("#chat").hidden = name !== "chat";
  $("#history").hidden = name !== "history";
  updateLayout();
  // Chats start, pause, and end while History is hidden: show the sessions as they are now.
  if (name === "history") loadSessions(subject());
}

// -- chat ------------------------------------------------------------------------------

const subject = () => $("#subject").value;
const ENGINE_KEY = "velocity.engine";
// The last model per engine ("velocity.model" was Claude Code's before there were two).
const modelKey = (engine) => (engine === "claude-code" ? "velocity.model" : `velocity.model.${engine}`);
const DEFAULT_ENGINE = "claude-code";
const DEFAULT_MODEL = "sonnet";

// {engine: {label, models: {alias: name}, available, reason}}, from the server.
let engines = {};

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}

const savedModel = (engine) => load(modelKey(engine)) || DEFAULT_MODEL;

// The engine to preselect: the wanted one if it can run here, else another that can.
function usableEngine(wanted) {
  if (engines[wanted]?.available) return wanted;
  return Object.keys(engines).find((e) => engines[e].available) || wanted;
}

function fillEngines(select, chosen) {
  select.innerHTML = "";
  for (const [name, engine] of Object.entries(engines)) {
    const option = new Option(engine.label, name, false, name === chosen);
    option.disabled = !engine.available;
    if (!engine.available) option.title = engine.reason;
    select.append(option);
  }
}

function fillModels(select, engine, chosen) {
  select.innerHTML = "";
  const models = engines[engine]?.models || {};
  for (const [alias, name] of Object.entries(models)) {
    select.append(new Option(name, alias, false, alias === chosen));
  }
  if (!(chosen in models)) select.value = Object.keys(models).includes(DEFAULT_MODEL) ? DEFAULT_MODEL : select.options[0]?.value;
}

// Why an engine can't be picked, or what picking it means.
function engineNote(engine) {
  const unavailable = Object.values(engines).filter((e) => !e.available);
  const notes = unavailable.map((e) => `${e.label}: ${e.reason}`);
  if (engine === "api") notes.unshift("Billed per token on your API key; the cost is shown as you go.");
  else notes.unshift("Runs Claude Code with your Claude plan.");
  return notes.join(" ");
}

function showStartChoice() {
  const engine = $("#engine").value;
  fillModels($("#model"), engine, savedModel(engine));
  $("#engine-hint").textContent = engineNote(engine);
}

const money = (dollars) => (dollars > 0 && dollars < 0.01 ? "<$0.01" : `$${dollars.toFixed(2)}`);

function showCost(total) {
  const cost = $("#chat-cost");
  cost.hidden = total === null || total === undefined;
  if (!cost.hidden) cost.textContent = `${money(total)} so far`;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status}`);
  return data;
}

async function loadChat() {
  const status = await getJSON(`/api/subjects/${subject()}/chat`);
  showChat(status);
  if (status.open) {
    $("#messages").innerHTML = "";
    for (const m of status.messages) addMessage(m.role, m.text, m.title);
    scrollToEnd();
  }
}

let chatStatus = { open: false };

function showChat(status, notice = "") {
  chatStatus = status;
  $("#chat-start").hidden = status.open;
  $("#chat-live").hidden = !status.open;
  $("#chat-notice").textContent =
    notice ||
    (status.elsewhere
      ? `Session ${status.session} is still open somewhere else (velocity tutor, velocity ` +
        "chat or another window). End it there, or close it here if nothing is using it."
      : "");
  $("#close-other").hidden = !status.elsewhere;
  $("#chat-title").textContent = status.title || "New session";
  const engine = engines[status.engine];
  const model = engine?.models[status.model] || status.model;
  $("#chat-meta").textContent = status.open
    ? [status.session, engine && engine.label.replace(/ \(.*\)$/, ""), model]
        .filter(Boolean)
        .join(" · ")
    : "";
  showCost(status.open ? status.cost : null);
  setBusy(status.busy);
  updateLayout();
}

// Start a chat. The engine and model come from `options` (Resume's dialog) or else from
// the start panel, whose choice is remembered for new sessions.
async function startChat(options = {}) {
  const engine = options.engine || $("#engine").value;
  const model = options.model || $("#model").value;
  if (!options.engine) {
    save(ENGINE_KEY, engine);
    save(modelKey(engine), model);
  }
  try {
    const status = await postJSON(`/api/subjects/${subject()}/chat/start`, {
      ...options,
      engine,
      model,
    });
    $("#messages").innerHTML = "";
    for (const m of status.messages) addMessage(m.role, m.text, m.title);
    showChat(status);
    if (options.resume) addNote(`Resumed session ${options.resume}. Say hello to carry on.`);
    else if (options.continues) {
      // Continue starts a new session without passing the start panel and its reminder.
      addNote(`Continuing from session ${options.continues}.`);
      addNote($("#reminder").textContent.replace(/\s+/g, " "), "reminder");
    }
    $("#input").focus();
  } catch (error) {
    const status = await getJSON(`/api/subjects/${subject()}/chat`).catch(() => ({}));
    showChat({ ...status, open: false }, `Could not start: ${error.message}`);
  }
}

function addMessage(role, text, title) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "block") {
    div.append(renderMarkdown(`> [!example] ${title || ""}\n> ${text.split("\n").join("\n> ")}`));
    linkifyCitations(div);
  } else if (role === "learner") {
    div.textContent = text;
  } else {
    div.append(renderMarkdown(text));
    linkifyCitations(div);
  }
  $("#messages").append(div);
  return div;
}

function addNote(text, className = "note") {
  const p = document.createElement("p");
  p.className = className;
  p.textContent = text;
  $("#messages").append(p);
  scrollToEnd();
  return p;
}

function scrollToEnd() {
  const messages = $("#messages");
  messages.scrollTop = messages.scrollHeight;
}

// While a reply streams, nothing else can be sent; Send turns into Stop.
function setBusy(busy) {
  for (const el of document.querySelectorAll("#send, #input, #skills button, #end-session, #pause")) {
    el.disabled = busy;
  }
  $("#send").hidden = busy;
  $("#stop").hidden = !busy;
  $("#stop").disabled = false;
}

async function stopReply() {
  $("#stop").disabled = true;
  await postJSON(`/api/subjects/${subject()}/chat/stop`).catch(() => {});
}

function describeTool(name, input = {}) {
  const labels = {
    record_question: () => `recorded your answer (${input.outcome || "?"})`,
    read_section: () => `read ${input.source} §${input.section_id}`,
    view_page: () => {
      const count = Number(input.count) || 1;
      const pages = count > 1 ? `pp. ${input.page}–${Number(input.page) + count - 1}` : `p. ${input.page}`;
      return `looked at ${input.source} PDF ${pages}`;
    },
    search_source: () => `searched ${input.source || "the sources"} for "${input.query}"`,
    get_exercises: () => `looked up exercises in ${input.source} §${input.section_id}`,
    get_outline: () => `read the outline of ${input.source}`,
    set_session_title: () => `titled the session "${input.title}"`,
    append_log: () => `added "${input.title}" to your log`,
    read_note: () => `read your note ${input.path}`,
    Skill: () => `started /${input.skill || input.command || "a skill"}`,
  };
  return `· ${(labels[name] || (() => name.replaceAll("_", " ")))()}`;
}

// Send a message and stream the reply. `retry` resends a message whose reply failed
// (it is already on the page, and the server doesn't log it twice).
async function send(text, retry = false) {
  if (!text.trim()) return;
  for (const old of document.querySelectorAll(".retry")) old.remove();
  if (!retry) addMessage("learner", text);
  const reply = addMessage("tutor", "");
  // Shown until the tutor's first words, and again while it uses a tool.
  const thinking = Object.assign(document.createElement("p"), {
    className: "thinking",
    textContent: "thinking…",
  });
  reply.after(thinking);
  let replyText = "";
  let pending = false;
  let failed = false;
  const redraw = () => {
    pending = false;
    reply.replaceChildren(renderMarkdown(replyText));
    linkifyCitations(reply);
    scrollToEnd();
  };
  setBusy(true);
  scrollToEnd();
  try {
    const response = await fetch(`/api/subjects/${subject()}/chat/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (!chunk.startsWith("data: ")) continue;
        handleEvent(JSON.parse(chunk.slice(6)));
      }
    }
  } catch (error) {
    failed = true;
    addNote(`Could not reach the tutor: ${error.message}`, "error");
  } finally {
    if (pending) redraw();
    thinking.remove();
    if (!replyText) reply.remove();
    setBusy(false);
    if (failed && chatStatus.open) offerRetry(text);
    $("#input").focus();
  }

  function handleEvent(event) {
    if (event.type === "text") {
      replyText += event.text;
      thinking.hidden = true;
      if (!pending) {
        pending = true;
        requestAnimationFrame(redraw);
      }
    } else if (event.type === "tool") {
      thinking.hidden = false;
      reply.before(Object.assign(document.createElement("p"), {
        className: "tool",
        textContent: describeTool(event.name, event.input || {}),
      }));
    } else if (event.type === "error") {
      failed = true;
      addNote(event.text, "error");
    } else if (event.type === "stopped") {
      // Kept as it was when stopped, marked the same way as in the log.
      replyText = (replyText.trim() ? `${replyText.trim()}\n\n` : "") + "*(stopped)*";
      redraw();
    }
    if ((event.type === "done" || event.type === "stopped") && event.cost) {
      // The own loop: what this reply cost, and the session's total so far.
      if (event.cost.reply) {
        reply.after(Object.assign(document.createElement("p"), {
          className: "reply-cost",
          textContent: money(event.cost.reply),
          title: "Estimated API cost of this reply",
        }));
      }
      showCost(event.cost.total);
    } else if (event.type === "ended") {
      chatEnded(event.text);
    }
  }
}

// After a failed reply: send the same message again with one click.
function offerRetry(text) {
  const button = Object.assign(document.createElement("button"), {
    className: "retry",
    textContent: "Retry",
  });
  button.addEventListener("click", () => {
    button.remove();
    send(text, true);
  });
  $("#messages").append(button);
  scrollToEnd();
}

function chatEnded(how) {
  const notes = {
    "wrapped-up": "Session wrapped up and the log is closed. Well done.",
    crashed: "The tutor stopped unexpectedly. Your session is saved: resume it from History.",
  };
  showChat({ open: false }, notes[how] || "The session ended.");
  loadSessions(subject());
}

async function pauseChat() {
  await postJSON(`/api/subjects/${subject()}/chat/pause`);
  showChat({ open: false }, "Paused. Resume this session any time from History.");
  loadSessions(subject());
}

// Resume: preselect the engine and model that ran the session last (decided 2026-11-09),
// or another engine if that one can't run here, and say so.
function askResume(target, last, onChoice) {
  const dialog = $("#resume");
  const lastEngine = last.engine || DEFAULT_ENGINE;
  const engine = usableEngine(lastEngine);
  $("#resume-title").textContent = `Resume "${target}"`;
  fillEngines($("#resume-engine"), engine);
  const showChoice = () => {
    const chosen = $("#resume-engine").value;
    const model = chosen === lastEngine && last.model ? last.model : savedModel(chosen);
    fillModels($("#resume-model"), chosen, model);
    const notes = [];
    if (engine !== lastEngine) {
      notes.push(`It ran on ${engines[lastEngine]?.label || lastEngine} last, which can't run here: ${engines[lastEngine]?.reason || ""}`);
    }
    if (chosen === "api") {
      notes.push("The first reply re-sends the session so far, so it costs more than the rest.");
    }
    if (chosen === "claude-code" && lastEngine !== "claude-code") {
      notes.push("Claude Code starts a fresh conversation that reads the session log first.");
    }
    $("#resume-note").textContent = notes.join(" ");
  };
  $("#resume-engine").onchange = showChoice;
  showChoice();
  $("#resume-go").onclick = () => {
    dialog.close();
    onChoice({ engine: $("#resume-engine").value, model: $("#resume-model").value });
  };
  $("#resume-cancel").onclick = () => dialog.close();
  dialog.showModal();
}

// Resume/Continue while a chat is open: only one session per subject can be open, so
// offer to pause the current one first, or to go back and end it properly.
function askToSwitch(verb, target, options) {
  const dialog = $("#switch");
  const current = chatStatus.title || chatStatus.session || "Your current session";
  $("#switch-title").textContent = `"${current}" is still open.`;
  $("#switch-pause").textContent = `Pause it and ${verb.toLowerCase()} "${target}"`;
  $("#switch-pause").onclick = async () => {
    dialog.close();
    await pauseChat();
    showView("chat");
    await startChat(options);
  };
  $("#switch-back").onclick = () => {
    dialog.close();
    showView("chat");
  };
  $("#switch-cancel").onclick = () => dialog.close();
  dialog.showModal();
}

function setUpChat() {
  fillEngines($("#engine"), usableEngine(load(ENGINE_KEY) || DEFAULT_ENGINE));
  showStartChoice();
  $("#engine").addEventListener("change", showStartChoice);
  $("#start-new").addEventListener("click", () => startChat());
  $("#close-other").addEventListener("click", () => startChat({ close_other: true }));
  $("#end-session").addEventListener("click", () => send("/end-session"));
  $("#pause").addEventListener("click", pauseChat);
  $("#stop").addEventListener("click", stopReply);
  $("#help-button").addEventListener("click", () => $("#help").showModal());
  for (const button of document.querySelectorAll("#skills button")) {
    button.addEventListener("click", () => send(button.dataset.command));
  }
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("#input").value;
    $("#input").value = "";
    send(text);
  });
  $("#input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  // Only the view tabs: the Book button looks like a tab but opens the reader instead.
  for (const tab of document.querySelectorAll(".tab[data-view]")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }
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
  engines = await getJSON("/api/engines");
  // Before the chat and History draw anything, so citations in them become links.
  await loadReaderSources(select.value);
  setUpChat();
  setUpReader();
  select.addEventListener("change", async () => {
    await resetReader(select.value);
    loadSessions(select.value);
    loadChat();
  });
  await Promise.all([loadSessions(select.value), loadChat()]);
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
