---
name: assign-problems
description: Assign practice problems (from the source's exercise sets when there are sources, otherwise written for the topic), let the learner work them on their own, then check their answers and reasoning. Use when the learner types /assign-problems (optionally with a section or topic and a number), or asks for homework or practice problems.
argument-hint: "[section or topic] [number of problems]"
---

# Assign problems

Default: 3 problems. Use the section, topic, and number the learner gave, if any.

## Choose the problems

- **With sources:** find the section (`get_outline` if needed), then `get_exercises`. Pick a
  mix: one routine problem, one that needs a careful step, and one harder or conceptual one. If
  the learner gave no section, use the section of the last topic studied or one due for review
  (`read_state`).
- **Without sources (or no exercise set):** write problems for the topic with the same mix of
  difficulty. Make sure each has a definite, checkable answer, or a clear standard for a proof.
- Before assigning, **solve every problem yourself**, silently. Do not show your solutions.

## Assign

1. Put each problem in the log with `append_log` (`kind: example`, title like "Problem 1
   (Stewart §2.4 #13)" with the source and exercise number when there is one), so the learner
   can see them rendered in Obsidian.
2. In your reply, list the problems briefly, each in a numbered question callout
   (`> [!question] Problem 1 of 3`), and tell the learner to work them on paper and send
   their answers (all at once or one by one). Offer hints on request, in `> [!tip]`; give a
   hint, not the solution.

## Check

For each answer the learner sends:

- Compare it with your own solution. Check the **reasoning**, not just the final answer: for a
  proof, go step by step and point to the first step that doesn't follow.
- If it is right, say so in `> [!success]` and name what they did well. If the approach is
  right but a step or number is off, say so in `> [!partial]`: what is right first, then the
  step that goes wrong. If it is wrong at its core, say so in `> [!failure]`, credit any
  correct pieces, and point to where it goes wrong. Either way, let them fix it before showing
  the correct solution.
- Call `record_question` per problem: `correct`, `hinted` (needed hints or a fix after
  feedback), `partial` (left with the approach right but a piece still off), or `incorrect`. Use the source and exercise number in `source` when there is one.
- After all problems, add a short "Problem set results" summary with `append_log`
  (`kind: summary`) and open threads for recurring mistakes.

Be honest when you are unsure whether an unusual approach is valid: say so, and check it
carefully rather than accepting or rejecting it quickly.
