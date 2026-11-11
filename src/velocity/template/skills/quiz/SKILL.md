---
name: quiz
description: Run a short quiz for the learner, one question at a time, hints before answers, with every result recorded. Use when the learner asks to be quizzed or tested, or types /quiz (optionally with a topic and/or number of questions, e.g. "/quiz limits 3").
argument-hint: "[topic] [number of questions]"
---

# Quiz

Run a short quiz. Default: 5 questions. If the learner named a topic or a number, use those.

## Choose the questions

1. If no topic was given, call `read_state` and draw from, in order: topics **due for review**,
   **weak spots**, then the topics of the last session. Mix in at least one earlier topic
   (interleaving), unless the learner asked for a single topic.
2. If the subject has sources, base questions on the relevant sections (`read_section`) and
   cite them (e.g. "Stewart §2.4").
3. Prefer questions that make the learner produce something (explain, compute, prove a step,
   give an example) over recognition questions. Vary the difficulty.

## Ask

- Tell the learner how many questions there are, then ask **one question at a time**, each in
  a numbered question callout (`> [!question] Question 2 of 5`). Do not reveal the answer in
  the same message.
- Open your feedback on each answer with `> [!success]`, `> [!partial]` (main idea right, a
  piece off; say what is right first) or `> [!failure]`, and put hints in
  `> [!tip]`.
- Wait for the learner's answer. If it is wrong or incomplete, give a **hint** and let them try
  again (at most two hints). Only then explain the answer.
- After each question is settled, call `record_question` with the topic, the question, and the
  outcome: `correct` (right without hints), `hinted` (right after hints), `partial` (settled
  with the main idea right but a piece still off), or `incorrect`. Add a short note on the
  mistake or the insight.
- If a misconception remains unresolved, call `open_thread` for it.
- If the learner wants to stop early, stop and go to the results.

## Results

When the quiz ends:

1. Tell the learner their result (e.g. "3 of 5 without hints, 1 with hints") and the one or two
   things most worth reviewing.
2. Call `append_log` with `kind: summary`, title "Quiz results", and a short markdown table of
   questions, outcomes, and topics, followed by what to review.
3. If the learner answered every question on a topic correctly without hints, and that topic
   was already `practicing`, consider `set_topic_status` with `solid`.
4. Suggest writing a note in their own words about the trickiest question.
