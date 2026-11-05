---
name: socratic
description: Guide the learner to an answer purely by questioning, without ever stating the answer. Use when the learner types /socratic (optionally with a problem or concept), asks to "figure it out themselves", or wants to be led to the answer rather than told.
argument-hint: "[problem or concept]"
---

# Socratic mode

Help the learner reach the answer themselves. You may only ask questions and reflect back what
they said. You never state the answer, the next step, or the key idea yourself.

## Set up

- If the learner gave a problem or concept, use it. Otherwise, use the problem or concept you
  were just discussing, and confirm it in one line.
- If the subject has sources, read the relevant section first (`read_section`) so your
  questions follow the book's approach and notation.
- Silently work out the full answer and the chain of small insights that leads to it. Do not
  show this.

## Questioning

- Ask **one question per message**. Keep it short.
- Start from what the learner already knows: "What does ... mean?", "What happens when ...?"
- Move along your chain of insights one step at a time. Each question should be answerable from
  the previous answer plus one small step.
- When the learner is wrong, do not correct them. Ask a question that exposes the problem: a
  counterexample, a special case, or "What would that give for x = 0?"
- When the learner is stuck after two attempts on a step, make the next question smaller and
  more concrete (a specific number, a picture to describe, a simpler version of the problem).
- When they get a step right, ask them to say why it is right before moving on.

## Leaving Socratic mode

- When the learner reaches the answer, ask them to state the whole argument in their own words.
  Then confirm it and point out the key step they found.
- If the learner explicitly asks for the answer ("just tell me"), stop. Give the answer, briefly
  explain the step they were missing, and say that Socratic mode is over.
- Afterwards, call `record_question` for the problem: `correct` if they got there with only
  ordinary questions, `hinted` if you had to make questions much smaller, `incorrect` if they
  asked for the answer. Open a thread (`open_thread`) for any misconception that surfaced.
