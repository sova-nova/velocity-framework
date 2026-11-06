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
      go.addEventListener("click", () => {
        if (chatStatus.open) {
          askToSwitch(action[0], s.title || s.id, action[1]);
          return;
        }
        showView("chat");
        startChat(action[1]);
      });
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
}

// -- views -----------------------------------------------------------------------------

function showView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  $("#chat").hidden = name !== "chat";
  $("#history").hidden = name !== "history";
  // Chats start, pause, and end while History is hidden: show the sessions as they are now.
  if (name === "history") loadSessions(subject());
}

// -- chat ------------------------------------------------------------------------------

const subject = () => $("#subject").value;
const MODEL_KEY = "velocity.model";

function savedModel() {
  try {
    return localStorage.getItem(MODEL_KEY) || "sonnet";
  } catch {
    return "sonnet";
  }
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
  $("#chat-meta").textContent = status.open
    ? [status.session, status.model].filter(Boolean).join(" · ")
    : "";
  setBusy(status.busy);
}

async function startChat(options = {}) {
  const model = $("#model").value;
  try {
    localStorage.setItem(MODEL_KEY, model);
  } catch {}
  try {
    const status = await postJSON(`/api/subjects/${subject()}/chat/start`, {
      model,
      ...options,
    });
    $("#messages").innerHTML = "";
    for (const m of status.messages) addMessage(m.role, m.text, m.title);
    showChat(status);
    if (options.resume) addNote(`Resumed session ${options.resume}. Say hello to carry on.`);
    else if (options.continues) addNote(`Continuing from session ${options.continues}.`);
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
  } else if (role === "learner") {
    div.textContent = text;
  } else {
    div.append(renderMarkdown(text));
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

function setBusy(busy) {
  for (const el of document.querySelectorAll("#send, #input, #skills button, #end-session, #pause")) {
    el.disabled = busy;
  }
}

function describeTool(name, input = {}) {
  const labels = {
    record_question: () => `recorded your answer (${input.outcome || "?"})`,
    read_section: () => `read ${input.source} §${input.section_id}`,
    get_exercises: () => `looked up exercises in ${input.source} §${input.section_id}`,
    get_outline: () => `read the outline of ${input.source}`,
    set_session_title: () => `titled the session "${input.title}"`,
    append_log: () => `added "${input.title}" to your log`,
    read_note: () => `read your note ${input.path}`,
    Skill: () => `started /${input.skill || input.command || "a skill"}`,
  };
  return `· ${(labels[name] || (() => name.replaceAll("_", " ")))()}`;
}

async function send(text) {
  if (!text.trim()) return;
  addMessage("learner", text);
  const reply = addMessage("tutor", "");
  let replyText = "";
  let pending = false;
  const redraw = () => {
    pending = false;
    reply.replaceChildren(renderMarkdown(replyText));
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
    addNote(`Could not reach the tutor: ${error.message}`, "error");
  } finally {
    if (pending) redraw();
    if (!replyText) reply.remove();
    setBusy(false);
    $("#input").focus();
  }

  function handleEvent(event) {
    if (event.type === "text") {
      replyText += event.text;
      if (!pending) {
        pending = true;
        requestAnimationFrame(redraw);
      }
    } else if (event.type === "tool") {
      reply.before(Object.assign(document.createElement("p"), {
        className: "tool",
        textContent: describeTool(event.name, event.input || {}),
      }));
    } else if (event.type === "error") {
      addNote(event.text, "error");
    } else if (event.type === "ended") {
      chatEnded(event.text);
    }
  }
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
  $("#model").value = savedModel();
  $("#start-new").addEventListener("click", () => startChat());
  $("#close-other").addEventListener("click", () => startChat({ close_other: true }));
  $("#end-session").addEventListener("click", () => send("/end-session"));
  $("#pause").addEventListener("click", pauseChat);
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
  for (const tab of document.querySelectorAll(".tab")) {
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
  setUpChat();
  select.addEventListener("change", () => {
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
