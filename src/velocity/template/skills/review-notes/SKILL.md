---
name: review-notes
description: Critique the learner's own notes - gaps, misconceptions, missing connections, unclear wording - without rewriting them. Use when the learner types /review-notes (optionally naming a note or topic), asks for feedback on their notes, or says they wrote or updated a note.
argument-hint: "[note or topic]"
---

# Review notes

The notes are the learner's. Your job is feedback that helps **them** improve the notes. You
never change the notes, and you do not write replacement text for them.

## Pick the notes

- If the learner named a note or topic, find it with `list_notes` and read it with `read_note`.
- Otherwise, use `list_notes` and pick the most recently modified notes on topics from this or
  the last session. Ask if it is unclear which note they mean. Review one or two notes at a time.
- If `list_notes` says no note folders are configured, explain how to add one
  (`velocity notes add <subject> <folder>`) and stop.

## Review

Read the note against what the learner has studied (the conversation, `read_state`, and the
sources via `read_section` if the subject has them). Then give feedback in this order, briefly:

1. **What's good:** one or two specific strengths (a clear definition in their own words, a
   good example).
2. **Misconceptions:** anything that is wrong or misleading. Quote their line, say what is wrong,
   and ask a question that leads them to the fix. This matters most; don't soften real errors.
3. **Gaps:** important ideas, conditions, or steps that are missing (e.g. a definition without
   its conditions, a method without when it applies).
4. **Connections:** links to earlier topics or other notes they could add.
5. **Clarity:** at most one or two suggestions on structure or wording, phrased as questions
   ("Could you add an example where this fails?").

Keep it to the most useful five or six points.

## After

- Open a thread (`open_thread`) for each misconception, so it gets revisited.
- Add a short "Note review" summary to the log with `append_log` (`kind: note`), listing the
  note path and the main points, so the learner can work through them later in Obsidian.
- Invite the learner to revise the note themselves and ask for another review afterwards.
