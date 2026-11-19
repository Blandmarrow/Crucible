# Analyst Chat

The **Analyst** page is a chat about one dataset with an AI model you choose. The model
reads the dataset's findings, your goal and your notes, explains what matters most for
what you are making, and suggests what to do. It only advises: it changes nothing in the
dataset. The findings and notes it reads are on the Health page, described in
[Dataset Analyst: Health and Notes](analyst.md).

The chat needs an LLM provider: a local server such as LM Studio, or a hosted API. Add one
in Settings → LLM Providers ([details](settings.md#llm-providers)).

To keep the chat in view while you work, click the panel button beside **Analyst** in the
sidebar, or **Ask the analyst** on the Health page. The chat opens in a pane beside the page,
in [split view](workspace.md#split-view); the sidebar then changes the other pane and leaves
the chat where it is. Asking again goes to the chat already open. Each pane remembers its own
chat, and the layout comes back after a reload.

**Start a chat** with the button on the Analyst page, or **New chat** beside the list of this
dataset's chats. At the top of a chat, pick the provider and the model. A blank model means
the provider's default model. The first message you send names the chat; click the name to
change it.

Type in the box at the bottom. **Enter** sends, and **Shift+Enter** starts a new line. The reply
appears as it is written. **Stop** ends it early and keeps what arrived. Leaving the page does
not stop a reply: it carries on, and the chat shows it when you come back, even after a
reload.

Under each reply, **Related findings** lists up to six of the dataset's findings as cards,
with the same buttons as on the Health page: Show images, Open in gallery and the page that
fixes it. Crucible picks these cards itself, starting with the ones the reply mentions, so
the buttons work even with a model that ignores the request to name findings. A finding the
reply names in its text shows as a small badge. A name that matches no real finding stays
plain text, so a model cannot invent a button.

Replies can use headings, lists, tables and links. A picture in a reply is never loaded,
since its address could carry data out.

## What gets sent

**What gets sent**, at the top of a chat, shows exactly what the next message would send to
the provider: the instructions, the facts about the dataset and the earlier messages. It also
lets you choose what this chat may share:

| Category | What it shares |
|---|---|
| Numbers | Counts, distributions and the findings |
| Name and goal | The dataset's name and what it is for |
| Notes | This dataset's notes and About me |
| Tags | The most common tags in tag-style captions |
| Captions | A sample of up to 20 captions. This also shows tags, since tags are written in captions |
| File names | The file names of the same sample. The folder they sit in is never sent |
| Images | Not sent yet |

A provider marked **billed** gets only Numbers by default, and a free one gets everything.
With Numbers alone the analyst sees the findings but not your goal or your notes, so its
advice is more generic and it cannot act on a note by number. Turn on **Name and goal** and
**Notes** if you are happy to send them.
You can change those defaults per provider in Settings → [Analyst](settings.md#analyst). A
change in **What gets sent** applies to this chat only, and **Follow the provider's defaults
again** undoes it. Switching the chat to another provider also undoes it, so a choice made
for a local server never follows the chat to a paid one.

If you turn something off, earlier replies that saw it are left out of what is sent from then
on, since a reply can quote captions and names. Tags, captions and file names are sent marked
as data, never as instructions. A caption can still argue for something, which is why the
analyst only ever suggests.

## Which model to use

The analyst has been tried on two models, with three fixed conversations: ranking what to
fix, recording a decision, and removing an open item. Both passed all three:

- **Gemini 2.5 Flash**, a hosted model, through its OpenAI-compatible address.
- **Qwen 3.8 27B**, a local model in LM Studio.

Qwen 3.8 27B was also tried on a fourth conversation that asks it to look up the captions
behind a finding, and passed: it looked them up and described them correctly.

Smaller local models have not been tried yet. A model that reasons before it answers
needs a generous Max tokens; both above ran at 16,000.

## Tools

A model that passes **Check tool support** can look things up while it answers, instead of
working only from what was sent up front. Settings → [Analyst](settings.md#analyst) checks
each provider's default model; a chat on another model checks its own under **What gets
sent**. The analyst can look up:

- the detail of one finding, dismissed ones included;
- the captions behind a finding or a filter, up to 20 at a time;
- tag counts, or the tags containing some text;
- the dataset's snapshots, and what changed since one.

Each lookup shows above the reply, such as "Read captions for low_resolution". Click it to
see exactly what was sent. The boxes in **What gets sent** apply to lookups too, and the
dialog lists the tools this chat may use. With Captions off the analyst cannot read
captions, and a search by text is refused while File names are off, since it would match
them. A reply looks things up in four steps at most, a few lookups each, and every step
counts toward the budgets. A model that has not passed the check chats without tools. Gemini 2.5 Flash and
Qwen 3.8 27B pass it.

## Budgets

For a billed provider, two token budgets apply: one per chat and one per day, set in
Settings → [Analyst](settings.md#analyst). The line under the message box shows the most the
next message could use, and how much of each budget is spent. A message that could pass
either budget is not sent, and the line turns red with both figures and where to raise them. A free provider has no
budget. Deleting a chat does not give its tokens back to the day.

## Suggested notes

When you and the analyst agree on something worth keeping, it can suggest changes to the
notes: a decision to record, an open item to add, a preference to reword, or a note to
remove. Each suggestion shows under the reply as a card, with the old text struck out and the
new text below it.

**Nothing changes until you accept.** **Accept** applies one suggestion, **Reject** sets it
aside, and **Undo** reverses either: an added note is removed again, a changed one is put
back, a removed one returns. A note you have edited by hand since is not undone over your
edit. A suggestion about a note you have since removed or reworded shows as out of date,
since accepting it would change something you have not seen. A suggestion to remove a note
that dismisses a health finding says that removing it brings the finding back.

If the analyst agrees to something but suggests no notes, **Ask it to note this**, under the
latest reply, asks it to write them. Deleting a chat keeps the notes it added.

## When a reply goes wrong

- **Cut off at Max tokens**: the reply hit the provider's Max tokens. A model that reasons
  before it answers spends its thinking from the same allowance. Gemini 2.5 Flash needed
  2,048 where 1,024 cut it short. Raise Max tokens in Settings → LLM Providers.
- **The provider went quiet**: no text for a minute, or none at all for three minutes, so
  Crucible stopped waiting. What arrived is kept.
- **An error from the provider** (a rate limit, a wrong key) is shown under the message. Try
  again, or check the provider's settings.
- **Stopped by a budget**: a reply that looked something up stops before its next step if
  that step could pass a budget. What it wrote is kept.
- **Kept asking for tools**: the model wanted more lookups after its limit and wrote no
  answer. Ask again, more narrowly.
