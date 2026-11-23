# CP Coach (Linux desktop)

A competitive programming training app that runs in its own desktop window. It plans your
daily practice, keeps hard techniques fresh with spaced repetition, gives you timed Codeforces
problems around your level, keeps a problem journal with flashcards, and tracks your progress.
It also has an AI control panel that blocks AI tools while you practise or simulate a contest.

The interface is in Portuguese. Everything runs locally; your data stays on your machine.

## The tools

The app has six tabs. The first time you open it, **Início** walks you through three first
steps: set your Codeforces handle, pick the techniques to keep fresh, and set this week's goals.

### Início (home)

Your plan for today, in order: upsolving from your latest contests, technique sessions,
CF Trainer time, contest simulations, flashcards and journal entries.

Below it are the weekly review and the weekly goals (technique exercises per day, Trainer
minutes, simulations). You set next week's goals until the Sunday before; after that they are
locked. Weeks without their own goals repeat the most recent ones. A week can also be marked as
reduced load (reviews only, or techniques paused).

Codeforces contests and 3-problem CF Trainer contests count automatically as simulations;
others (VJudge, on paper) you add with **+1 feita**.

### Técnicas (spaced technique practice)

A list of hard techniques you want to keep fresh (sparse table, LCA, centroid decomposition…).
Each one goes through a fixed cycle:

1. **First pass:** a direct problem.
2. **Review 1:** a new problem, 4 days later.
3. **Review 2:** a harder problem, 10 days later.
4. **Blind rewrites:** every 21, 35 and then 60 days, forever.

The intervals are configurable. The next session is scheduled from the day you actually did the
previous one. If you mark a session as "consultei apontamentos" (I checked my notes), the next
one comes at a third of the interval and the rewrite does not level up. Each day takes at most
the number of technique exercises in your weekly goals; the rest waits, reviews first.

For each session the app proposes problems from the USACO Guide and cp-algorithms pages matching
the technique name and, if you give tags, from Codeforces. It skips problems you already solved
on Codeforces or CSES. Matching is by name, so check the proposals under "Procurar problemas".

The tab also has a calendar view and the training rules: write each technique from scratch
without templates or AI, stress-test before asking for help, and keep a list of typical bugs.

### CF Trainer

Timed practice on random Codeforces problems around an internal Elo-style rating (K = 32).

- **Novo treino:** generates 3 problems of different difficulty (below, at and above your
  rating). With your handle set, problems you already solved are excluded.
- Solve one at a time with a time limit (40 min by default), or start a **virtual contest**
  with all 3.
- Mark the result yourself (**Resolvi**, **Desisto**, **+1 WA**) or let it check your
  submissions on Codeforces.
- **Filters:** contest type, contests from a given date or contest number, and Codeforces tags.
- A history table shows each problem, result, time and rating change.

### Diário e flashcards (journal and flashcards)

Pick a problem you solved in the last 14 days and write down what is worth remembering:
the statement in your own words, key idea, solution sketch, complexity, where you got stuck,
pitfalls, takeaway, related problems and code.

Each entry with a key idea becomes a flashcard ("How is this problem solved?"), and you can add
extra cards. Cards are reviewed with spaced repetition: the ones you get right come back at
longer and longer intervals.

Solved problems are imported under **Contas e importação**:

| Source | How |
|---|---|
| Codeforces | Directly from the public API using your handle (last 500 submissions, with dates). |
| VJudge | Open your accepted-submissions data page, copy all the text and paste it (last 100 accepted, with dates). |
| CSES | Log in, open the problem set page, copy the whole page and paste it. CSES shows no solve dates, so the first import is undated; later imports date new problems on the day they first appear. |

### Estatísticas (statistics)

- Codeforces rating chart with **recessions** (drops of at least a configurable number of
  points from a peak) and periods you annotate to explain them.
- Weaknesses by Codeforces tag across your contests.
- Weekly goal completion over the last weeks, and your weekly reviews.
- The CF Trainer internal rating over time.
- Contests: results of the contests you record (for example yearly MIUP or SWERC entries).

### Definições (settings)

Your Codeforces handle (used by every tab; only public data is read, no password), default
daily technique minutes and flashcards, the recession threshold, the CF Trainer starting
rating, yearly contests to pre-create (e.g. `MIUP, SWERC`), and the data file.
Settings specific to one tool (review intervals, Trainer time limit, journal imports) live
inside that tool.

**Ficheiro de dados** exports and imports copies of your data. In the native window, exporting
opens a "Guardar como" dialog (up to 32 MiB per download).

## Requirements

- Linux with Python 3. The installer targets Ubuntu/Debian; tested on Ubuntu 22.04 (24.04 is untested).
- Native window: pywebview 6.2.1, PyGObject/GTK and WebKit2GTK.
- AI block: a normal user account with `sudo` (or `pkexec`) access, and nftables for the strict mode.

## Install

Extract the archive (or clone the repository) to a permanent folder, then:

```sh
cd cp-coach-desktop
./install.sh
```

Run it as your normal user, not as root. On Ubuntu/Debian the installer:

- installs the missing packages (`python3-gi`, `python3-pip`, `nftables`, `wmctrl`, WebKit2GTK) and
  pywebview 6.2.1 (`pip --user`, no Qt);
- adds a "CP Coach" menu entry, icons, and an autostart entry that resumes monitoring at login
  only if a session is still active;
- installs a root-owned helper at `/usr/local/lib/cp-coach/cp-coach-hosts-helper`;
- adds a sudo rule (`/etc/sudoers.d/cp-coach`) limited to that helper's fixed verbs.

On other distributions, install Python, pywebview 6.2.1 and GTK/WebKit yourself.

Installing is optional: without it the app still runs (in your browser if there is no
working native window), but the AI network block is unavailable.

To update: end the session, close the app, wait about a minute, replace the program files with
the new version (keep `cp-tools-data.json` and `cp-coach-blocker.json`) and run `./install.sh` again.

## Run

Open **CP Coach** from the menu, or:

```sh
python3 cp-coach.py
```

| Option | Effect |
|---|---|
| `--browser` | Open in your browser instead of a native window. |
| `--serve-only` | Only start the local server; the address is printed in the terminal. |
| `--port N` | Use port `N` for the local server (default: a free port). |

Only one window runs per user: launching the app again raises the existing window.

The training tools also work without CP Coach: `python3 cp-tools-server.py` serves
`cp-tools.html` on `http://localhost:8765` with the same data file, but without the AI block.

## Data and sync

| File | Contents |
|---|---|
| `cp-tools-data.json` | All your training data. Put your existing file in this folder. |
| `cp-coach-blocker.json` | AI-control sessions, violations, the allowed-site list. Treat as private. |
| `~/.local/share/cp-coach/webview` | The window's browser storage. Local to each machine; kept on uninstall. |

To use the same data on several computers, put the folder in Syncthing (or similar) and run
`./install.sh` once on each machine. Changes to the training data made on another computer are
merged with yours. `cp-coach-blocker.json` has no conflict merging: consider excluding it from
sync to keep sessions per machine. Before switching computers, end the session, close the window
and let the sync finish.

Nothing is sent to third parties. The app only talks to Codeforces (public API) and to the sites
it proposes problems from.

## AI control

A small collapsible panel lets you choose how you are training:

| Mode | AI block | How it ends |
|---|---|---|
| **Prática com IA** | Off | After the minutes you set, or any time. |
| **Prática sem IA** | On | After the minutes you set, or any time. |
| **Contest** | On | After the minutes you set. Ending early requires typing `TERMINAR CONTEST` and is logged as a violation. |

Every session is timed and logged (start, end, duration, how it ended). The mode cannot be
changed while a session runs.

### Network option

In **Prática sem IA** and **Contest**, choose one before starting:

| Option | What it does |
|---|---|
| **Só sites permitidos** (default) | Browsers only open the sites on your allowed list, and an nftables firewall limits your user's traffic to those sites, the local network and loopback. Connections already open to other destinations are cut. |
| **Pesquisa web permitida** | Blocks a list of known AI sites and APIs and keeps web search. Weaker: an AI site that is not on the list can still answer. |

Use **Só sites permitidos** for real contests. Every new session starts with it selected.

- **Allowed list:** edit it under "Sites permitidos" while no session is running, one host per
  line (`*.` prefix allowed). It starts with Codeforces, AtCoder, CSES, CodeChef, Kattis, VJudge,
  LeetCode, USACO Guide, CP-Algorithms, cppreference, cplusplus.com and docs.python.org. Google,
  Stack Overflow and YouTube are not on it.
- **Cost of the strict option:** anything running as your user (`pip`, `npm`, `git push`, editor
  extensions, chat apps) loses access to sites outside the list until the session ends.
- **Searching in Pesquisa web permitida:** in Chrome, search from the address bar (Google
  Web-only results). In Firefox, Google is blocked; open `https://noai.duckduckgo.com/html/`.
- **Restart your browser** after a session starts and after it ends, so it loads and drops the policies.

### What runs during a restricted session

- **Hosts:** known AI sites and APIs point to `0.0.0.0` / `::` in a marked `cp-coach` block in `/etc/hosts`.
- **Browser policies:** Chrome/Chromium, Brave, Edge and Firefox get managed policies that turn
  off DNS-over-HTTPS and HTTP/3, block AI URLs and Google AI Mode, and (strict option) allow
  only the listed sites. Previous policy files are restored when the session ends.
- **Tampering:** removed hosts entries, policies or firewall rules are put back within about a second.
- **Browser history:** Chrome, Chromium, Brave, Edge, Vivaldi, Opera and Firefox histories are
  checked for AI visits during the session and again about 15 s and 60 s after it ends.
- **Processes:** about every second it stops AI terminal agents and desktop apps of your user
  (Claude Code, Devin CLI, Codex, Gemini CLI, Aider, Copilot CLI, Ollama, Cursor, Jan and
  similar). Unsaved work in those processes can be lost.

Closing the window does not end a session: monitoring continues in the background, and reopening
the app reconnects to it.

### Emergency exit

You can always get out as administrator:

```sh
sudo /usr/local/lib/cp-coach/cp-coach-hosts-helper remove
```

This is recorded in a root-owned journal (`/var/lib/cp-coach/emergency-journal.json`) and the
session is marked as interrupted. Killing the monitor or suspending the computer for more than
10 s does not lift the block; the gap is logged and the session is marked as broken in the log.

### Limits

This is a self-control tool, not an anti-cheat system:

- root can undo every layer and delete the journal;
- **Pesquisa web permitida** is an incomplete block list, and it does not stop direct-IP access or renamed CLI tools;
- in the strict option, sites that share IP addresses with an allowed site stay reachable from
  the terminal, and local models, LAN proxies and other devices are not covered;
- a visit the browser blocks is usually not saved to history, so blocked attempts may not be logged;
- private-window or deleted history cannot be detected;
- nothing is checked while the computer is off or you are logged out;
- screen sharing and screen capture are not detected.

## Uninstall and recovery

End the session, close the app, then:

```sh
./uninstall.sh
```

It closes your CP Coach processes and removes the hosts block, browser policies, firewall, helper,
sudo rule, menu entry, autostart entry and icons. Your data and shared system packages are kept.
With a restriction active it warns and records an emergency exit. The emergency journal is kept
unless you pass `--purge`. It is safe to run more than once.

To remove only the firewall table by hand: `sudo nft delete table inet cp_coach` (the monitor
puts it back while a restricted session is active).

## Files

| File | Purpose |
|---|---|
| `cp-tools.html` | The training app (all six tabs). |
| `cp-tools-server.py` | Minimal local server for `cp-tools.html` and the data file. |
| `cp-coach.py` | Desktop launcher, local server, monitor and AI control. |
| `cp-coach-panel.html` | The AI control panel injected into the page. |
| `cp-coach-downloads.js` | Download bridge for the native "Guardar como" dialog. |
| `cp-coach-hosts-helper` | Root helper for `/etc/hosts`, browser policies and the firewall. |
| `install.sh`, `uninstall.sh` | Installer and uninstaller. |
| `test_blocker.py` | Unit tests: `python3 test_blocker.py`. |
| `cp-coach.png`, `cp-coach.svg` | App icon. |
