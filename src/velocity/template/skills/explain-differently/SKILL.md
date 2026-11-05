---
name: explain-differently
description: Re-explain the last concept from a genuinely different angle (visual, numeric, analogy, worked example, formal, or contrast) when the first explanation didn't click. Use when the learner types /explain-differently or says they don't get it, are confused, or want another way to see it.
argument-hint: "[concept, or what didn't click]"
---

# Explain differently

The previous explanation didn't work. Repeating it in other words won't help; change the
**angle**.

1. **Find what didn't click.** Unless the learner already said, ask one short question: "Which
   part lost you: the idea, a step, or the notation?" Skip this if it is obvious.
2. **Pick an angle you haven't used yet** for this concept in this session (check the
   conversation; if the subject has sources, check how the book explained it with
   `read_section`, and choose something different):
   - **Visual:** describe a picture or graph the learner can sketch.
   - **Numeric:** a table of concrete values that shows the pattern.
   - **Analogy:** an everyday situation with the same structure. Say where the analogy breaks.
   - **Worked example:** a specific case, step by step, before the general statement.
   - **Contrast:** a non-example, or a case where the idea fails, and why.
   - **Formal:** the precise definition, unpacked term by term (best for learners who distrust
     hand-waving).
3. **Explain briefly** with that angle. Keep it shorter than the first explanation. One angle per
   message; offer another if this one doesn't land either.
4. **Check understanding** with one question that requires using the idea, not repeating it.
   Record the outcome with `record_question` when they answer.
5. If the new angle worked, offer to put it in the log with `append_log` (`kind: note`) so they
   can find it later, and suggest they write it in their own notes in their own words.
