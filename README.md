# CP Coach (Linux desktop)

A competitive programming training app that runs in its own desktop window. It plans your
daily practice, keeps hard techniques fresh with spaced repetition, gives you timed Codeforces
problems around your level, keeps a problem journal with flashcards, and tracks your progress.
It also has an optional mode that blocks AI tools while you practise or simulate a contest.

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

## Requirements

- Linux with Python 3.
- For the native window and the AI block: a normal user account with `sudo` access.
  The installer targets Ubuntu/Debian.

## Install

Extract the archive to a permanent folder, then:

```sh
cd cp-coach-desktop
./install.sh
```

Run it as your normal user, not as root. On Ubuntu/Debian the installer:

- installs the missing GTK/WebKit packages and pywebview 6.2.1 (`pip --user`, no Qt);
- adds a "CP Coach" menu entry and icons;
- installs a root-owned helper at `/usr/local/lib/cp-coach/cp-coach-hosts-helper`;
- adds a sudo rule (`/etc/sudoers.d/cp-coach`) limited to that helper's `apply`, `remove` and `status`.

On other distributions, install Python, pywebview 6.2.1 and GTK/WebKit yourself.

Installing is optional: without it the app still runs (in your browser if there is no
working native window), but the AI network block is unavailable.

To update: close the app, extract the new version over the old folder and run `./install.sh` again.
`.venv` folders from older versions are no longer used and can be deleted.

## Run

Open **CP Coach** from the menu, or:

```sh
python3 cp-coach.py
```

| Option | Effect |
|---|---|
| `--browser` | Open in your browser instead of a native window (keep the terminal open, Ctrl+C to quit). |
| `--serve-only` | Only start the local server; the address is printed in the terminal. |
| `--port N` | Use port `N` for the local server (default: a free port). |

The training tools also work without CP Coach: `python3 cp-tools-server.py` serves
`cp-tools.html` on `http://localhost:8765` with the same data file, but without the AI block.

## Data and sync

| File | Contents |
|---|---|
| `cp-tools-data.json` | All your training data. Put your existing file in this folder. |
| `cp-coach-blocker.json` | AI-block sessions, flagged URLs and violations. Treat as private. |
| `~/.local/share/cp-coach/webview` | The window's browser storage. Local to each machine; kept on uninstall. |

To use the same data on several computers, put the whole `cp-coach-desktop` folder in Syncthing
(or similar) and run `./install.sh` once on each machine. Changes made on another computer are
merged with yours. Starting AI-block sessions on two machines at the same moment may produce a
Syncthing conflict copy of `cp-coach-blocker.json`.

Nothing is sent to third parties. The app only talks to Codeforces (public API) and to the sites
it proposes problems from.

## AI-free practice

A small panel lets you choose how you are training:

| Mode | AI block | How it ends |
|---|---|---|
| **Prática com IA** | Off | Any time. |
| **Prática sem IA** | On | Any time. |
| **Contest** | On | Automatically after the duration you set. Ending early requires typing `TERMINAR CONTEST` and is logged as a violation. |

While the block is on:

- known AI sites and APIs are redirected to `0.0.0.0` / `::` in a marked `cp-coach` block in
  `/etc/hosts` (the full list is in `cp-coach-hosts-helper`). **Restart your browser after a block starts.**
- about every 30 s it checks the block is still there, and puts it back if it was removed;
- it checks local browser history (Chrome, Chromium, Brave, Edge, Vivaldi, Opera, Firefox) for
  visits to AI sites, during and shortly after the session;
- about every 5 s it stops AI terminal agents and desktop apps (Claude Code, Codex, Gemini CLI,
  Aider, Copilot CLI, Ollama and similar). Unsaved work in those processes can be lost.

Each event is logged as a violation in the panel. Closing the window does not end a session or
remove the block; reopen the app to resume.

This is a self-control tool, not an anti-cheat system:

- anyone with `sudo` can edit `/etc/hosts` or remove the helper (a missing block is logged);
- VPNs, proxies, custom DNS, direct IPs, other devices, local models and unlisted services are not covered;
- private-window or deleted history cannot be detected, and renamed agents can escape detection;
- blocking and checks only run while the app is open;
- screen sharing and screen capture are not detected.

## Known issues

Reported on an earlier build and not yet confirmed fixed:

- **Ficheiro de dados → Exportar cópia** does nothing in the native window (it works with `--browser`).
- The mode panel can cover the flashcard **Fácil** button, and more of the app in small windows.

## Uninstall and recovery

Close the app, then:

```sh
./uninstall.sh
```

It refuses to run while CP Coach is open. It removes the hosts block (even if its markers are
damaged), the helper, the sudo rule, the menu entry and the icons. Your data and shared system
packages are kept. It is safe to run more than once.

To remove the block manually without uninstalling (close the app first, or it will put the
block back):

```sh
sudo /usr/local/lib/cp-coach/cp-coach-hosts-helper remove
```

## Files

| File | Purpose |
|---|---|
| `cp-tools.html` | The training app (all six tabs). |
| `cp-tools-server.py` | Minimal local server for `cp-tools.html` and the data file. |
| `cp-coach.py` | Desktop launcher, local server, AI block and its panel. |
| `cp-coach-hosts-helper` | Root helper that edits the `cp-coach` block in `/etc/hosts`. |
| `install.sh`, `uninstall.sh` | Installer and uninstaller. |
| `test_blocker.py` | Unit tests: `python3 -m unittest test_blocker`. |
| `cp-coach.png`, `cp-coach.svg` | App icon. |
