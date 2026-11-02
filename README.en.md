# Astrel

[Version française](README.md)

Astrel is a voice assistant that runs on your computer and hands the reasoning to Claude Code,
Anthropic's CLI, on the user's own subscription. You say "Astrel", you talk, it acts (terminal,
files, browser, desktop) and answers out loud. A local dashboard, the HUD, shows what it is doing
while it does it.

![The HUD: conversation thread, multiple-choice question, running agents](docs/images/hud.png)

![The bubble: idle, listening, thinking, speaking, asking, mic muted](docs/images/bulle.gif)

## What sets it apart

**A guardrail before every action.** Claude runs without the CLI's permission prompts, but a
`PreToolUse` hook (`hooks/permission_hook.py`, logic in `astrel/guard.py`) judges every tool call.
Bash commands are parsed structurally: recursive deletes, writes to sensitive files, disguised
commands. Anything serious (deleting, sending, paying, `sudo`, `git push`…) waits for an answer by
voice, a click in the HUD or a gesture in front of the webcam. A hook that crashes denies. After three refusals in a row within one message, a circuit breaker
tells Claude to stop working around it and ask. Before each write by Claude, the hook keeps the
previous version: "undo what you just did" restores the last batch of files. Claude
can neither say "yes" to its own request nor loosen the guardrail: on Linux the core recognises
what comes from Claude through the process tree and the service's cgroup.

**Speech sentence by sentence.** The answer is read as it streams, one sentence as soon as it is
complete, never in the middle of a code block. Wake word and transcription run on the machine
(faster-whisper, end of speech detected by Silero VAD); only the Edge voice receives the text of
the answers, with Piper as a local fallback. The microphone is muted while Astrel speaks, and
nothing unrequested is said while the user has the floor.

**French or English.** The conversation language is set in Settings › Voice, audio and language ›
"Conversation language". In English, Whisper transcribes English, Astrel answers with an English
voice and English instructions, and voice commands have English versions. The wake word stays
"Astrel". On the same page, an assistant ("Calibrate this mic") measures background noise, then
your voice, and suggests a gain and thresholds for that microphone next to the current settings;
nothing changes until you click "Save".

**A HUD that stays on the machine.** A web page served on 127.0.0.1 only, with a token that never
appears on a command line and a signed cookie. Markdown in answers is rendered as DOM nodes from a
closed list, without `innerHTML`. A floating bubble (GTK) shows the state at a glance and receives
confirmation questions.

**A team of agents for the user's projects.** Astrel delegates to sub-agents defined in
`config/agents/` (coder, tester, reviewer, planner, researcher…), each with its role sheet. After a
coder, a tester then a reviewer check the work. A hook picks each sub-agent's model: a coding task
always runs on Opus. Large jobs go through a plan mode: a written plan, reviewed in the HUD, then
carried out.

**Memory, and tokens kept in check.** Markdown notes in a local vault, recalled before each
message; conversation history indexed with SQLite FTS5 (`history_search`). When the context goes
past 150,000 tokens, the session is handed over to a fresh one with a summary. Simple checks (a
file changed, a command answers) run without a model.

**Claude by default, other providers possible.** The brain is Claude Code on the Claude
subscription. Settings › Provider also offers Claude with an API key, then OpenAI, Gemini,
OpenRouter and Grok with an API key, through a local LiteLLM gateway (127.0.0.1, installed on
demand). Keys stay in a 0600 file and never go through the environment or the command line.
Outside Claude, Astrel chats without tools by default: tools only open after a successful test
bench, in cautious mode, and never in "No confirmation" mode or with the guardrail lifted. The Codex and
Grok Build CLIs, with those tools' own subscriptions, are wired in but refused until a real run
has shown that the guardrail holds them.

Also: per-project channels in the HUD, a per-project task list, WhatsApp restricted to the
"message yourself" chat, connectors (CalDAV calendar, Home Assistant…), image and video generation
through the browser on the user's accounts (it stops at any anti-bot challenge), and an interface
in French and English. For reliability, child processes die with the core, a message received
during a restart is picked up on the next start, and a watchdog reports a stuck loop without
stopping anything on its own.

## In numbers

Measured on this repository on 1 November 2026:

| | |
|---|---|
| Python (core `astrel/` and hooks) | 56,613 lines, comments included |
| HUD JavaScript (`hud/src/`) | 11,673 lines |
| Python tests (`tests/`) | 3,956 tests (`pytest --collect-only`), 36,347 lines |
| MCP tools exposed to Claude | 68 (59 in Astrel's server, 9 in the connectors server) |

## How it works

```mermaid
flowchart LR
    Mic((Mic)) --> Wake["“Astrel”<br/>faster-whisper, local"]
    Wake --> End["End of speech<br/>Silero VAD, local"]
    End --> STT["Transcription<br/>faster-whisper, local"]
    STT --> Core["Astrel core<br/>astrel/core.py"]
    Core <-->|"JSON stream, --resume"| Claude["Claude Code<br/>(CLI, subscription)"]
    Claude --> Hook{"PreToolUse hook<br/>guardrail"}
    Hook -->|allowed| Tools["Terminal, files,<br/>Astrel MCP, browser"]
    Hook -->|serious| Consent(["User's consent:<br/>voice, HUD, bubble, gesture"])
    Claude --> Agents["Sub-agents<br/>coder, tester, reviewer"]
    Core --> Voice["Sentence-by-sentence voice<br/>Edge, Piper fallback"] --> Spk((Speaker))
    Core <--> HUD["Web HUD<br/>127.0.0.1 only"]
    Core <--> Bubble["GTK bubble"]
    Core <--> Memory["Markdown vault<br/>SQLite FTS5 history"]
```

Everything happens on the machine except the reasoning (Claude) and the Edge voice, which only
receives the text of the answers, never the microphone audio.

## Where it runs

| System | Status |
|---|---|
| **Debian 13**, GNOME, Wayland | Used every day: this is the reference machine. |
| Ubuntu 24.04, Debian 12 | Same packages; not yet tried on a fresh machine. |
| Windows 11 + WSL2 | Planned (sound through WSLg), not verified. No bubble, no shortcuts. |
| macOS, native Windows | Ported on paper (`astrel/platform/` layer), **never run on a real machine**. |

The bubble, global shortcuts, autostart and GPU transcription work on Linux only. Outside Linux,
protection against a program started by Claude is weaker: there is no cgroup to recognise it.

## Installation

### Debian or Ubuntu

You need Claude Code installed and signed in to a Claude subscription (Pro or Max), Python 3.11 or
later, and about 6 GB of disk. An NVIDIA card speeds up transcription; without one, the CPU does
it, more slowly.

```bash
sudo apt install git python3 python3-venv python3-gi gir1.2-gtk-3.0 \
  gstreamer1.0-pipewire gir1.2-gstreamer-1.0 pulseaudio-utils libnotify-bin \
  xclip wireplumber nodejs npm curl
git clone https://github.com/Zaudrax/Astrel.git ~/Astrel && cd ~/Astrel
python3 tools/check_install.py        # checks the machine, changes nothing
./install.sh --projets ~/Projets      # no sudo; --projets: the folder where Claude works
systemctl --user start astrel         # the assistant
./hud.sh                              # the HUD
```

The step-by-step guide is in [docs/INSTALLATION.md](docs/INSTALLATION.md) (in French). On first
launch the HUD opens a getting-started guide ([docs/GUIDE-DEMARRAGE.en.md](docs/GUIDE-DEMARRAGE.en.md)).

A new installation starts in cautious mode: it also asks before commits, connected accounts and
writes in the home folder. `./run.sh doctor` checks that the guardrail is in place and unchanged.

To try the HUD without a microphone or a subscription: `.venv/bin/python tools/hud_demo.py`, then
`./hud.sh`.

### macOS (experimental: never run on a real machine)

The installer is shared (`install.py`) and the machine check knows macOS, but nobody has run
Astrel on a Mac yet. You need Claude Code installed and signed in (`claude`), then:

```bash
xcode-select --install                    # git
brew install python@3.12 node             # or Python from python.org
git clone https://github.com/Zaudrax/Astrel.git ~/Astrel && cd ~/Astrel
python3.12 tools/check_install.py
python3.12 install.py                     # shared installer (no sudo)
.venv/bin/python -m astrel                # the assistant
.venv/bin/python -m astrel --hud          # the HUD, in another terminal
```

Sound goes through sounddevice, installed with Astrel; macOS asks for microphone access on first
launch. The projects folder is `~/Projets`, or the one in the `ASTREL_WORKDIR` variable.

### Windows (experimental)

**Through WSL2, the recommended route, not verified.** In PowerShell run as administrator:
`wsl --install -d Ubuntu-24.04`, restart, then follow the Debian or Ubuntu part in the Ubuntu
window. WSL does not always have systemd: in that case start Astrel with
`ASTREL_WORKDIR=~/Projets ./run.sh` and the HUD with `./hud.sh`. Sound goes through WSLg; the
microphone has not been tried (`./run.sh --listen` to check it).

**Native, never run on a real machine.** In PowerShell:

```powershell
winget install Python.Python.3.12        # tick "Add to PATH"
winget install Git.Git
winget install OpenJS.NodeJS.LTS
git clone https://github.com/Zaudrax/Astrel.git $HOME\Astrel
cd $HOME\Astrel
py tools\check_install.py
.\install.ps1                            # refused? PowerShell -ExecutionPolicy Bypass -File .\install.ps1
.venv\Scripts\python -m astrel
.venv\Scripts\python -m astrel --hud     # in another terminal
```

Install Astrel in your profile (`C:\Users\<you>\…`), not at the root of a drive: the local channel
goes through 127.0.0.1 with a token written in Astrel's folder.

**Still missing outside Linux:** the bubble, global shortcuts, autostart (Astrel is started by
hand), GPU transcription (the CPU does it) and the `doctor` health check. The guardrail is less
able to recognise a program started by Claude, as there is no cgroup.

## Known limitations

- macOS and Windows have never run on a real machine.
- The HUD calibration assistant has not been tried with a real microphone yet.
- WhatsApp has been tried on a real account; the calendar and home automation connectors have
  not yet.
- Video generation depends on sites that change often; it has worked for real on two sites, never
  with several accounts.
- No key-based provider, Claude included, has been tried with a real key yet; Codex and Grok
  Build have never run.

## Licence

Astrel is released under the [PolyForm Noncommercial License 1.0.0](LICENSE): non-commercial use
only (personal use, research, teaching, charities and public bodies). Commercial use needs the
author's permission. Components installed separately keep their own licences, listed in
[NOTICE](NOTICE).

Author: Baptiste Renaud ([Zaudrax](https://github.com/Zaudrax)).
