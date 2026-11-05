---
name: end-session
description: Wrap up the tutoring session properly - recap what was covered, update the learner's progress, schedule what comes next, and prompt for note-taking. Use when the learner types /end-session or says they want to stop for today.
---

# End the session

Wrap up in this order.

1. **Catch up on progress.** If any question the learner answered this session has not been
   recorded with `record_question`, record it now. Close threads that were resolved
   (`close_thread`); open threads for anything left unclear (`open_thread`). Update topic
   statuses that changed (`set_topic_status`). If the session has no title yet, or its title
   no longer fits what was covered, set one with `set_session_title`.
2. **Ask the learner one retrieval question:** "In a sentence or two, what was the most
   important thing you learned today?" Wait for the answer. Gently correct it if needed; do
   not record it as a quiz question.
3. **Write the recap** with `end_session`. The recap is markdown for the learner's log, and it
   is shown to you at the start of the next session, so make it useful to both:
   - **Covered:** topics, with source sections if any.
   - **Went well / needs work:** specific, based on their actual answers.
   - **Open threads:** what remains unclear.
   - **Next time:** what to start with, and what is due for review.
   Keep it short: a few bullet points per heading.
4. **Final message:** briefly tell the learner the key takeaway and what is next, and suggest
   writing a note in their own words about today's main idea (in their own notes, not the log).
   Tell them the session is wrapped up and they can exit with `/exit` or Ctrl+D.

Call `end_session` before your final message: the log closes right after your final message,
so do not call any tools after it.
