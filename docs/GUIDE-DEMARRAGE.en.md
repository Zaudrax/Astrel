# Astrel getting started guide

Welcome. Astrel is an assistant that lives on your computer: you talk to it,
it thinks with **Claude** (your own subscription), acts on your machine
(terminal, files, web, browser, desktop) and answers you out loud. Everything
it does shows up in the **HUD**, the window you're reading this guide in.

This guide has two parts:

- **Setup**: the settings to do once, in order. Each step has
  its own buttons ("Test", setting right there) and gets checked off when it's done.
  Some check themselves off when Astrel can verify them.
- **How it works**: everything Astrel can do, and how to keep it
  under control.

Setup takes about twenty minutes. You can close the guide at
any time and reopen it with the **Guide** pill, top right. The
**Done** button stops it from opening by itself at startup.

This document can also be read as is in Astrel's `docs/` folder.

## Check your machine
<!-- id: controle | etape | test: demarrage/check | reglage: demarrage -->
First, Astrel checks what it needs: Python, Claude Code, sound,
Node.js, disk space. The result shows just below, one line per
item:

- **green**: ready;
- **red**: blocking, fix it first (the → arrow tells you what to do);
- **orange**: Astrel will work, but without this feature (for example no
  screen capture);
- **no color**: for information.

Click **Test** (or "Check again") after each fix. The same check
exists in a terminal, without changing anything:

```bash
python3 tools/check_install.py
```

To ask for help, the **Settings › First launch and diagnostics** page
prepares a **report to paste** with no personal data (no email, no
username, no path to your folder).

## Connect your Claude account
<!-- id: claude | etape | test: compte/refresh | reglage: compte -->
Astrel has no brain of its own: it goes through **Claude Code**, connected to **your**
Claude account (Pro or Max subscription). No API key, nothing is shared with
the person who gave you Astrel, and Astrel never sees or keeps any token.

If the line below says "connected", this step is done: it checks itself
off. Otherwise, click **Sign in**: a Claude sign-in page
opens in your browser. If nothing opens, a link shows up here
instead. You can also do it in a terminal:

```bash
claude
```

then `/exit` once you're signed in, and **Test** to check again.

Good to know: every exchange uses your subscription, just as if you were writing to
Claude yourself. Long tasks (agents, missions) use more. The
model badge, at the top of the HUD, shows which one is answering: Sonnet by default,
Opus for hard questions, Haiku for speed.

## Choose and test the mic
<!-- id: micro | etape | test: audio.test/mic | reglage: audio.mic -->
Choose the mic Astrel should listen to ("System mic" follows your
desktop's sound setting), then click **Test**: Astrel listens for 3 seconds and tells you
whether sound is coming through. Speak normally during the test.

The step checks itself off when the machine check sees a mic;
check it off yourself once the test works.

If the wake word doesn't come through well afterwards, a few settings:

- **Name variants that wake it up** (Settings › Voice, audio and language):
  words accepted as the name at the start of a sentence, separated by commas
  (speech recognition often writes "astral" for "Astrel").
- **Old wake word sensitivity** (same section): threshold for the old wake
  word only, no effect on the name; the lower it is, the more easily it wakes up,
  with more false wake-ups.
- **Calibration**, which measures YOUR mic (4 seconds of silence, then 10
  seconds of speech) and saves its settings:

```bash
./run.sh --calibrate
```

Other handy tools: `./run.sh --mics` (is the mic detected?),
`./run.sh --listen` (your sentence should show up), `./run.sh --diag` (live level
meter and wake word score).

## Choose the voice
<!-- id: voix | etape | test: audio.test/voice | reglage: audio.voice -->
Choose Astrel's voice, then **Test**: it says a test sentence. If you
don't hear it, check the selected **speaker** (Settings › Voice, audio and
language) and your system volume.

The voices offered are free online voices. They only receive the
**text** of the replies, never what your mic picks up. Without Internet, or if the
service goes down, Astrel switches by itself to a local voice, less natural, and
goes back to your chosen voice when the service returns. The voice never goes quiet
because of an outage.

While Astrel is speaking, its mic is deaf: it doesn't hear itself.
To interrupt it, see "Talking to Astrel".

## The language you speak
<!-- id: langue | etape | reglage: stt_language -->
Choose the language you speak to Astrel in: it's the one
speech recognition expects. French by default; English, Spanish, German
and Italian are available.

Astrel's replies stay in French, whatever language you pick here. The
voices offered follow this choice: only those that speak this language
appear in the list.

For a conversation entirely in English (Astrel listens, answers and speaks
in English, with an English voice), choose **Conversation language ›
English**: a fresh session starts with your next message. The wake word
stays "Astrel".

The language of the HUD, bubble and mini-chat texts is chosen separately:
**Settings › Voice, audio and language › Interface language** (French or
English), applied right away, without restarting Astrel.

Check off the step once it's set, even if you keep French.

## Your projects folder
<!-- id: projets | etape -->
Claude works in **a projects folder**: that's where it reads, writes and
runs its commands without asking your permission. Outside this folder, writing
to your home folder requires your approval in careful mode.

You choose this folder at install time:

```bash
mkdir -p ~/Projets
./install.sh --projets ~/Projets
```

A reinstall keeps the folder already chosen. If Astrel doesn't start by
itself (no service, for example under WSL2), give the folder when you launch it:

```bash
ASTREL_WORKDIR=~/Projets ./run.sh
```

Each top-level subfolder becomes a **project** that Astrel
recognizes by voice. To sort the conversation by project, see "The
channels". Check off the step once your folder exists and contains your projects.

## Link WhatsApp (optional)
<!-- id: whatsapp | etape | reglage: whatsapp -->
With WhatsApp, you talk to Astrel from your phone when you're not at
the computer. Astrel reads **only one chat**: your "Message
yourself" chat (the chat with your own number). It doesn't see your contacts, your
groups or your other conversations, and only writes to you.

The computer must be on and Astrel running: WhatsApp doesn't wake it
up. To link, right here:

1. **Set up WhatsApp**, if the button shows up: downloads the WhatsApp
   bridge once (one to two minutes, needs Node.js and Internet).
2. **Link with a QR code**: a QR code shows up. On your phone: WhatsApp ›
   Settings › **Linked devices** › **Link a device**, then point at the
   QR code. It refreshes by itself about every twenty seconds.
3. If the camera can't read it: **Link with a code**, type
   your number with the country code (for example 33 followed by your number without the
   leading zero), then on the phone "Link with phone
   number instead" and type the 8-character code shown here.
4. Astrel shows the detected number: **Yes, that's my number** if it really is
   yours. Otherwise, **No, cancel**: the device is unlinked right away.

The QR code and the code give access to your account: don't show them to anyone. They're
never saved, and a pairing that doesn't complete within 3 minutes
stops by itself.

Once linked, write yourself "Hello" in your Message yourself chat: the reply
arrives, marked at the start with a small robot (that's how Astrel recognizes
its own messages). See "Away mode" for what's next.

**Unlink WhatsApp** (by typing the word "unlink") removes the "Astrel" device
from your phone and erases the credentials from this computer. If the phone
can't be notified, Astrel tells you: then remove "Astrel" yourself in
Linked devices. To stop it without unlinking: **Turn off WhatsApp**.

What you should know: WhatsApp goes through an unofficial library.
Astrel writes little and slowly so it doesn't look like a bot, but
WhatsApp banning the number can't be ruled out. WhatsApp also unlinks
every device when the phone stays offline for 14 days. If you don't want
WhatsApp, just check off this step.

## Start with your session
<!-- id: demarrage-auto | etape | reglage: systeme.autostart -->
On Linux with systemd, Astrel can start by itself when you log in
to your session: turn on **Start with session**. Nothing is started or stopped
right away, it applies to the next login. In the same group:

- **Bubble at startup**: the small floating window opens too (see "The
  bubble");
- **Keyboard shortcuts** (GNOME desktop only): Ctrl+Alt+J to talk,
  Ctrl+Alt+M for the mic, Ctrl+Alt+X to interrupt, Ctrl+Alt+P to
  pause the voice, Ctrl+Alt+H for the HUD.

To start or restart by hand:

```bash
systemctl --user start astrel      # or restart
./hud.sh                           # reopen the HUD
journalctl --user -u astrel -f     # the live log
```

If these settings are grayed out, your system doesn't allow them (for
example WSL2 without systemd): run Astrel in a terminal you leave
open, with `./run.sh`, then the HUD with `./hud.sh` in another terminal.
Check off the step once you've made your choice.

## Talking to Astrel
<!-- id: parler -->
Say **"Astrel"** (the assistant's name, or "Hey Astrel"), wait for the little
chime, then speak naturally. Say the name ON ITS OWN: a sentence said in one go
after the name (« Astrel, quelle heure est-il », "Astrel, what time is it")
wakes it up, but the question is lost; say it again after the chime. Astrel
detects the end of your sentence by itself. It answers sentence by sentence, as
Claude writes.

- The wake word follows the **Assistant name** (Settings › Appearance). During
  the transition, the old wake word works too, as long as "Also
  accept the old wake word" (Settings › Voice, audio and language) is set to
  Yes (it is by default).
- After its reply, you have **8 seconds** to follow up without saying the name
  again: the bubble shows "you can keep going".
- « Au revoir » ("goodbye"), « merci c'est tout » ("thanks, that's all") put it back on standby right away.
- To cut it off: the HUD's **Stop** button (or Ctrl+Alt+X) cuts the voice
  and the current reply; background tasks keep going.

Without speaking: the input bar at the bottom of the HUD. **Enter** sends, **Shift+Enter**
starts a new line. You can attach a file or an image (paper clip, Ctrl+V or
drag and drop). Keyboard mode also exists in a terminal:
`./run.sh --text`.

Three modes, at the bottom of the HUD:

- **Conversation**: it listens and answers out loud;
- **Silent**: it listens and answers on screen, without saying anything;
- **Dictation**: what you say is copied to the clipboard, nothing goes to
  Claude (« fin de dictée », "end of dictation", to exit).

Three mute buttons: the **mic** only, the **voice** only, or **both**.
A muted mic stays muted after a restart: only you can turn it back on.

**Pause** (⏸ button, Ctrl+Alt+P, or « mets ta voix en pause », "pause your voice") cuts the sound
instantly, like a video player: Astrel keeps working and picks up where it
left off.

## The HUD
<!-- id: hud -->
The HUD is Astrel's screen, in your browser. It only listens to your
computer (address 127.0.0.1) and requires private access: nobody else on
the network can open it. To open it: `./hud.sh`, or Ctrl+Alt+H on
GNOME.

What you see there:

- **The orb**, which moves with Astrel's voice, and below it what Astrel is
  doing (listening, thinking, speaking);
- **The conversation**, sorted into **channels** (see "The channels"); its height can be
  adjusted with the handle above it (double-click: original size);
- **Running**: the tasks and agents at work, with their cost;
- **The badges**: Claude's model, the voice, the mic (one click to change);
- **The sensors**: EYES (your presence in front of the screen, locally), WATCH (a
  screen capture attached to each request, more costly), HOLO (gestures: thumbs
  up for yes, thumbs down for no), FOCUS (do not disturb), REFLEX (the
  reflexes).

The pills at the top: **Plans**, **Log** (who allowed what),
**Settings**, **Guide**, and the safety controls (see "The guardrail").
The power button shuts Astrel down to free up memory.

If the HUD stays empty: Astrel must be running before `./hud.sh`. If the page refuses
the connection, close it and run `./hud.sh` again.

## The channels
<!-- id: canaux -->
Above the conversation, tabs: **General**, then one per project.
Each channel is a separate thread, so topics don't get mixed up when several
agents are working. Click a tab, or **Alt+1 to 9**, to switch threads.

Only the display is sorted: Claude keeps **a single conversation** and
remembers everything, whatever the tab.

Where a message goes:

1. into **the open channel** when you talk or type;
2. into the channel you **name at the start of the sentence**: "My project, rerun the
   tests" (handy by voice);
3. from General only, a request that names **a single** project goes to
   its channel.

The reply follows your last message. A background agent's report goes to
the channel it was started in, even if you've switched tabs. Right-click a
message to move it elsewhere.

To add a project: an entry in `config/channels.json` (an `id`, a
`label`, and optional `keywords`), then reload the HUD. You can also
ask Astrel to create the channel.

## Memory
<!-- id: memoire -->
Astrel has a long-term memory: the **vault**, a folder of Markdown notes
(`vault/`), also readable with Obsidian. It searches there before answering about you
or your projects, and writes what it learns there: your preferences, the state of a
project, a decision. Every write keeps the previous version.

You can read, fix and organize these notes yourself: that's intended. You can also ask it
« souviens-toi que… » ("remember that…") or « qu'est-ce que tu sais sur… » ("what do you know about…").

Useful settings (Settings › Brain, and Privacy and data):

- **Memory recall**: with each message, Astrel attaches the notes that
  mention it (names and excerpts, names only, or nothing);
- **Keep history**: how many days to keep the conversation index;
- **Export memory** (a zip) or **erase** the history or the memory, by
  typing a confirmation word;
- **What leaves this machine**: the list of what is sent, and to whom.

Over time, the conversation is summarized and restarted fresh when it gets
too long (the "session handover"): ongoing projects are noted in the vault
beforehand, nothing important is lost. After a long silence (2 hours by default),
the conversation also starts fresh.

## Agents and background tasks
<!-- id: agents -->
For long work, Astrel starts **agents** that work in parallel
while you keep talking to it: a researcher (read-only
research), a reviewer, an auditor, a planner, a coder agent (always on the
strongest model) and others. You see them in **Running**, each with the
project it's working for, its duration and its usage.

When an agent finishes, Astrel doesn't interrupt you: the **report** arrives
silently in the project's channel, with a **Read** button to listen to it whenever you
want. To have them read out as soon as they arrive: Settings › Voice, audio and language ›
**Background task reports**. Astrel never speaks while you're talking: whatever
comes in is held and said once you're done.

An agent that goes over a duration or usage threshold triggers an
**alert**, never a stop (Settings › Alert thresholds). To stop everything
at once: **Stop all** (see "The guardrail").

Agents that browse the web each have their own browser; Settings ›
Browsers says whether they show on screen or work without a window.

## The guardrail and confirmations
<!-- id: garde-fou -->
Before **every** Claude action, a guardrail decides: it goes through, or it
waits for your approval. Without a clear answer, or if Astrel can't reach you,
the action is **refused**.

Two modes, on the **Autonomous** switch at the top:

- **Careful** (the default for a new install): it asks for commits,
  your connected accounts, writing to your home folder, scheduled
  tasks, and always for anything irreversible;
- **Autonomous**: it only asks for the **irreversible**: `sudo`,
  disks, shutdown, `git push`, deleting outside your projects, **sending** an
  email, **deleting**, paying, deploying, sharing.

To answer: **by voice** (« oui » "yes", « non » "no"), **with a click** on the
HUD or bubble card, with a **thumbs up** in front of the webcam (HOLO sensor), or on
WhatsApp when you're away. "**Yes for 30 min**" allows the same kind
of action for the rest of the task.

Touching the guardrail itself always requires your approval, whatever the
mode. On WhatsApp, such a question is only approved by writing exactly
« oui garde-fou » ("yes guardrail", in French).

The other safety controls, at the top of the HUD:

- **TOTAL autonomy**: no more questions at all (except about the guardrail), goes back to
  OFF at every restart;
- **Mission**: Astrel works on its own toward a goal, within a folder and a
  budget you set (Settings › Guardrail › **Start a mission**); a purple banner
  shows it under the bar, with Pause and Stop;
- **Stop all**: emergency stop; voice cut, work and agents stopped, nothing
  new until "Resume".

Claude can't change **any** of these settings itself: only you, through
the HUD or your terminal. The **Log** keeps track of who allowed what, and
`./run.sh doctor` checks that the guardrail is in place and hasn't changed.

## Reflexes
<!-- id: reflexes -->
A **reflex** is a "when… then…" rule that Astrel applies on its own:

- **when**: at a given time (for example 8:30 on weekdays), at a specific
  moment, when the battery or disk drops below a threshold, after a long
  stretch in front of the screen, or after a regular check;
- **then**: say a sentence, or ask Claude a question (a morning
  briefing, for example).

A few examples are provided, some turned off: low battery, disk
almost full, morning briefing, screen break. The easiest way to create one
is to ask: « tous les jours de semaine à 8 h 30, fais-moi un briefing
de mes projets » ("every weekday at 8:30, give me a briefing on my projects"). Astrel creates, enables or deletes it on request. They're
stored in `config/reflexes.json`.

Reflexes that only check something cost nothing; only
those that ask Claude use your subscription. If it has nothing to say,
Astrel stays quiet. A reflex never talks over you, and **Stop all**
suspends them.

## Plan mode
<!-- id: mode-plan -->
For a big coding project (several files, several agents), Astrel
first has its planner write a **plan**: the files touched,
the risks, the steps, and sometimes questions for you. The plan shows up
on the HUD's **Plans** page.

You review it point by point, you can comment on a point, ask for a revision,
reject it or **approve** it. In **Manual** mode (default), no coder agent
starts before you approve. In **Automatic** mode, a plan is approved as soon as
it's submitted. You choose this on the Plans page or in Settings ›
Brain.

Only you can approve a plan, from the HUD: Claude never approves its own.

## Away mode
<!-- id: absent -->
When you leave the computer, say or write « **je m'absente** » ("I'm stepping away") (or the
"I'm stepping away" button in Settings › WhatsApp). WhatsApp must be linked.

- Astrel goes quiet on the computer: nothing talks in an empty room;
- it sends you a recap on WhatsApp (the last exchange, the tasks
  running);
- then everything that comes in without you asking goes to WhatsApp: finished
  tasks, reports, errors, reminders, and its **confirmation
  questions**, which you answer « oui » or « non » ("yes" or "no").

« **Je suis de retour** » ("I'm back"), or any real message by voice or
keyboard, brings you back to present mode.

Even when you're not away, a WhatsApp message is enough to talk to Astrel: its
reply comes back on WhatsApp, in the same conversation as in the HUD. From
WhatsApp, « **stop** » ("stop") stops the current reply and « **stop tout** » ("stop all") triggers
the emergency stop (which can only be lifted on the computer). Your voice notes are
transcribed on the computer; images and documents you send aren't read
yet. Messages written while Astrel was off are ignored: send again
whatever still matters.

Astrel can also send you a file on WhatsApp, only from the
allowed folders (variable `ASTREL_WHATSAPP_FILE_ROOTS`), never its own
settings, secrets or memory.

## The bubble
<!-- id: bulle -->
The **bubble** is a small floating window, always visible, that shows whether
Astrel is listening, thinking or speaking. It never takes the keyboard.

- **One click**: a small field to write to Astrel;
- **double-click**: it listens to you right away;
- **confirmation questions** show up as a card next to it.

Start it with `./bubble.sh &`, or automatically when you log in to your session
(Settings › System › Bubble at startup). It only exists on Linux for
now, and can be moved with the mouse; its position is remembered.

## Reminders
<!-- id: rappels -->
Just ask: « rappelle-moi dans 20 minutes de sortir le linge » ("remind me in 20 minutes to take out the laundry"), or
« rappelle-moi demain à 9 h d'appeler le garage » ("remind me tomorrow at 9 to call the garage"). At the set time, a
notification shows up and Astrel says it out loud, unless you're talking at that
moment: it waits until you're done. In Away mode, the reminder goes to
WhatsApp.

« Quels sont mes rappels ? » ("what are my reminders?") lists them, « supprime le rappel du garage »
("delete the garage reminder") deletes it.

A reminder survives a Astrel restart, but not a computer reboot
before the time. On Linux with systemd, it's handed to the system:
if Astrel is stopped at the set time, the notification still shows up.

## Email, calendar and other services (optional)
<!-- id: connecteurs -->
None of this is needed to get started. Two ways to plug in
services:

**Your Claude account's connectors** (Gmail, Google Calendar, Google Drive…)
are added on the Claude website, in your account settings, under
connectors. Claude Code then uses them on its own. In Settings ›
claude.ai connectors, you can **block** some for Astrel only: their tools then no
longer weigh on every exchange. Reading, labeling or organizing goes through freely
in autonomous mode; **sending** an email or **deleting** always requires your
approval.

**Astrel's connectors**, to fill in in `config/secrets.json`:

- a **CalDAV calendar** (« qu'est-ce que j'ai demain ? », "what do I have tomorrow?");
- **train** and public transport **times**;
- DeepL **translation**;
- Home Assistant **home automation** (« allume la lumière du salon », "turn on the living room light").

Each one stays inactive as long as its credentials are missing, and Astrel says so
clearly if you ask. The step-by-step instructions, key by key, are in
`docs/CONNECTEURS.md`. After a change, pending settings
take effect with **Apply now** in Settings.

## Troubleshooting
<!-- id: depannage -->
Always start with these two checks, which change nothing:

```bash
python3 tools/check_install.py     # the machine
./run.sh doctor                    # Astrel: guardrail, tools, file permissions
```

Common cases:

- **It doesn't hear its name**: is the mic muted in the HUD? A notice "I can
  no longer hear the wake word" means local speech recognition won't load and
  the old word is off. Then `./run.sh --mics`, `./run.sh --listen`, and
  `./run.sh --calibrate`.
- **It hears but doesn't speak**: is the voice muted or paused in the
  HUD, or Silent mode on? Then `./run.sh --say "bonjour"`, and the
  speaker selected in Settings.
- **It doesn't start**: `journalctl --user -u astrel -n 50`. "Not connected":
  reconnect your Claude account (first step), then
  `systemctl --user restart astrel`.
- **The HUD is empty**: Astrel must be running before `./hud.sh`.
- **It keeps asking for permission**: that's careful mode; switch to
  autonomous if you want (see "The guardrail").
- **WhatsApp stopped answering**: check Settings › WhatsApp. "Session lost":
  unlink, then link again.

To ask for help: Settings › First launch and diagnostics ›
**Prepare report**, then Copy. The report contains no email, no
username, no personal path. Details are in `docs/DEPANNAGE.md`.

To update Astrel: `./update.sh` (Settings › Updates offers it
too). Your settings, memory and logs are never touched.

## Known limits, depending on your system
<!-- id: limites -->
Said plainly, so you don't hunt for a bug where there isn't one:

- **Linux (Debian or Ubuntu, GNOME desktop)**: this is where everything works. On
  another desktop, shortcuts, theme and screen capture may be missing.
- **Windows 11 via WSL2**: the recommended route on Windows, but **not yet
  tested end to end**. Sound goes through WSLg: check the mic
  with `./run.sh --listen` (and Windows must allow the mic: Settings ›
  Privacy › Microphone). Often without systemd: no automatic
  startup, run `ASTREL_WORKDIR=~/Projets ./run.sh` in a terminal left open.
  No bubble, no shortcuts, no Windows desktop capture. Opening
  the HUD from the Windows browser hasn't been checked yet: if
  `./hud.sh` doesn't open anything, install Chromium in Ubuntu.
- **macOS and Windows without WSL**: **not ready yet**. The code is partly
  ported but has never run on a real machine; Astrel probably
  won't start.
- **Language**: the interface, the voice and the instructions are in French.
- **WhatsApp**: a recent feature, through an unofficial library (see
  the WhatsApp step). Report any odd behavior.
- **License**: PolyForm Noncommercial 1.0.0:
  **non-commercial** use only.

If something doesn't work on your system, the report to paste (see
"Troubleshooting") is the best way to get it fixed.
