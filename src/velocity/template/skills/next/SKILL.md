---
name: next
description: Suggest two or three things the learner could do next, drawn from their learner state and the conversation, without starting any of them. Use when the learner types /next, or asks what to do next, what to study, or where to go from here.
---

# What's next

Give the learner a short menu, not a lesson. They choose; you don't start anything until they
do.

## Gather

- Call `read_state` for due reviews, weak spots, open threads, and topics studied.
- Look at this conversation: what was just covered, and anything left unfinished.
- If the subject has sources, use `get_outline` to find the section after the one being
  studied, so you can name it.

## Suggest

Offer **two or three** options, one line each, with a reason the learner will recognise. Pick
the most useful from:

- an open thread or an unresolved confusion ("We left the $y=x$ symmetry question open"),
- a weak spot or a topic due for review ("Odd functions tripped you up on Tuesday"),
- going deeper on what was just covered (a harder problem, or a related case),
- the next section of the source (cite it, e.g. "Stewart §1.4"),
- writing a note on what clicked, if the session taught something new,
- wrapping up with `/end-session`, if the session has been long.

If there is no learner state yet, suggest ways to begin: a short diagnostic question, the
first section of a source, or a topic the learner names.

Write the options as a short numbered list in plain text, without callouts. End with one plain
line such as "Pick one, or tell me something else." Don't add a question callout, and don't
begin any option until the learner picks it.
