# Wiki Inspi

Capture any image from the web in one gesture into a dedicated [Obsidian](https://obsidian.md) vault, have a vision model classify it in detail (style, palette with hex values, main color, technique, era, mood, subject, composition...), search everything from the terminal, Obsidian or a Pinterest-style gallery page, and generate Excalidraw moodboards.

macOS only. Everything runs on your machine; images stay in your vault. The gallery's pixel-based features ("more like this", text read in pictures) use Apple's Vision framework, so they need macOS and the Xcode command line tools (`xcode-select --install`); everything else works without them.

## Install in one line

- **Give this repo URL to Claude Code.** It reads `CLAUDE.md`, installs everything it can, and walks you through the few clicks only you can do.
- **Manual install.** `git clone <this repo> && cd wiki-inspi && ./install.sh`, then follow the printed steps (details below). `inspi doctor` checks every link and prints the fix for what is missing.

### Your 3-minute checklist

1. Open the vault folder in Obsidian, turn on Settings > Community plugins (Excalidraw, Dataview and Note Gallery are fetched by `scripts/install_plugins.py`).
2. `chrome://extensions` (or `brave://extensions`) > Developer mode > Load unpacked > the repo's `extension/` folder.
3. Run `scripts/setup-token.sh`, then within 60 s: the extension's options page > **Pair** > **Test**.
4. Right-click any image > **Send to Wiki Inspi**. A note appears in `Inbox/`, then moves to `Library/` once classified.
5. Optional: Obsidian Web Clipper > Settings > Templates > Import the four files in `~/.config/wiki-inspi/clipper/`.
6. Pick the classifier: Claude login (default), `ANTHROPIC_API_KEY`, or local Ollama (see Configuration).
7. Open **Wiki Inspi** from Applications (or `inspi map`): the gallery of everything you saved. Drag the icon to the Dock if you want it one click away.

## Why

Inspiration folders rot: files named `image (3).jpg`, no source, no way to ask "dark blue, risograph, calm". Wiki Inspi makes capture cost one right-click and does the filing for you, so the library stays searchable and sourced (page URL, author when the page states it).

## How it works

```mermaid
flowchart LR
  A["Chrome/Brave extension<br/>right-click: Send to Wiki Inspi"] -->|image bytes, localhost| R["receiver<br/>127.0.0.1"]
  B["Obsidian Web Clipper<br/>one template per site"] -->|note in Inbox| I[("vault/Inbox")]
  C["Drop folder<br/>watched by launchd"] -->|stub note| I
  R -->|note + image| I
  I --> E["enrich.py<br/>downscale, classify, validate"]
  E -->|"claude-cli / anthropic-api / ollama"| M(("vision model"))
  M --> E
  E --> L[("vault/Library<br/>classified notes")]
  L --> S["inspi search"]
  L --> O["Obsidian Base + Dataview views"]
  L --> X["inspi board<br/>Excalidraw moodboard"]
```

Three doors in, one pipeline:

1. **Extension** (`extension/`, Chrome/Brave MV3, loaded unpacked): right-click an image. The extension fetches the bytes itself, in the page with your cookies, and posts them to the local receiver. If the bytes cannot be read it saves the note display-only (remote URL embedded, nothing downloaded).
2. **Obsidian Web Clipper** templates (`clipper/`): generic, Pinterest, Are.na, Wikimedia Commons. The enricher downloads the image later.
3. **Drop folder**: put an image in `~/Inspi Drop`, a launchd watcher files it.

The enricher shrinks each image (1568 px long side, JPEG under 5 MB), asks the model for strict JSON, normalises tags through `_taxonomy.yml`, validates the note with a deterministic validator before it reaches `Library/`, and gives up after 3 failed attempts per note. `author` is never produced by the model: it is copied from the page metadata only (the model may give an `author_guess` with a confidence).

## Install

Requirements: macOS, Python 3.10+, Obsidian, Chrome or Brave. Optional, for the gallery's "more like this" and text-in-pictures search: the Xcode command line tools (`xcode-select --install`, they provide `swiftc`). Without them those two features stay empty and nothing else is affected. For classification, one of: the [Claude Code](https://claude.com/claude-code) CLI logged in (default), an `ANTHROPIC_API_KEY`, or [Ollama](https://ollama.com) with a vision model.

```sh
git clone <this repo> wiki-inspi && cd wiki-inspi
./install.sh            # asks for the vault folder (default ~/Wiki Inspi)
```

Options: `--vault PATH --port N --backend claude-cli|anthropic-api|ollama --drop-folder PATH --extension-id ID --non-interactive --no-launchd --no-keychain` (the last stores the token in a 0600 file instead of the Keychain, for CI and dry runs). The script is idempotent; it never overwrites your notes, taxonomy or existing config values. It installs missing Python dependencies (Pillow, PyYAML, anthropic, networkx, numpy) into a private venv, creates the vault skeleton, writes the config, stores a token in the macOS Keychain, installs the `inspi` command in `~/.local/bin` and three launchd agents (`io.github.wiki-inspi.receiver`, `.watcher`, `.enricher`). `./uninstall.sh` removes them (your vault is left alone).

The `inspi` command lands in `~/.local/bin`: add it to your `PATH` if `inspi` is not found (`export PATH="$HOME/.local/bin:$PATH"` in your shell profile). Until the extension is paired, the plugins are installed and the launchd agents loaded, `inspi doctor` reports MISSING lines; that is expected, each one prints its fix.

Then, once:

1. `chrome://extensions` > Developer mode > Load unpacked > the repo's `extension/` folder.
2. `scripts/setup-token.sh` (rotates the token and opens a 60 s pairing window), then in the extension's options page click **Pair**, then **Test**. The extension id is not shipped: the first extension that pairs inside the window is remembered in the config and is the only one accepted afterwards. To pin it yourself, run `scripts/setup-token.sh --extension-id <id shown on chrome://extensions>` first.
3. `python3 scripts/install_plugins.py --vault <vault>` downloads the community plugins **Dataview**, **Note Gallery** and **Excalidraw** from their official releases (not bundled in this repo). Open the vault in Obsidian and turn on Community plugins.
4. Optional: in Obsidian Web Clipper, Settings > Templates > Import each file from `~/.config/wiki-inspi/clipper/` (the installer rewrites the vault name in them).

## Configuration

`~/.config/wiki-inspi/config.json` (all keys optional):

| key | default | meaning |
|---|---|---|
| `vault` | `~/Wiki Inspi` | the Obsidian vault (a real path, not a symlink) |
| `port` | `18765` | receiver port on 127.0.0.1 |
| `extension_id` | empty | learned at pairing, or set up front |
| `backend` | `claude-cli` | `claude-cli`, `anthropic-api`, `ollama`, or a comma list used as fallback chain |
| `model` | `claude-haiku-5-5` | model for `claude-cli` and `anthropic-api` |
| `ollama_model` | `gemma3:12b` | local vision model |
| `drop_folder` | `~/Inspi Drop` | watched folder |
| `token_store` | `keychain` | `keychain` or `file` (0600 `token_file`) |
| `log_dir` | `~/Library/Logs/wiki-inspi` | `receiver.log` (refused requests), `enrich.log`, `watcher.log` |

`anthropic-api` reads `ANTHROPIC_API_KEY` from the environment; for the launchd agents add it to the `EnvironmentVariables` of the installed plists. The `claude-cli` backend runs the CLI with only the Read tool, in the image's temp directory, with the API key stripped from its environment. Vocabulary (the closed `use_for` list and tag synonyms) lives in `<vault>/_taxonomy.yml`: replace the example list with your own.

## Usage

New here? [docs/TUTORIAL.md](docs/TUTORIAL.md) walks the full loop, from saving an image to opening a moodboard.

```sh
inspi search "dark blue"                       # free text over title, tags, style, mood, subject
inspi search --color "#1e3a8a" --style minimalism --mood calm --limit 10
inspi search "ferrofluid" --json
inspi board "ferrofluid" --name ferrofluid     # writes Moodboards/ferrofluid.excalidraw.md
inspi open                                     # open the hub note in Obsidian
inspi map                                      # rebuild the gallery page and open it in the browser
inspi doctor                                   # check every link, print the fix for what is missing
```

`--color` takes a hex or a name and ranks by perceptual distance to each note's palette. Free text also matches the text read inside the pictures (see the gallery section) once `inspi map` has read them. Other filters: `--technique --author --domain --use-for`. Reclassify old notes with `python3 src/enrich.py --reenrich-all` (use `~/.local/share/wiki-inspi/venv/bin/python` instead if `install.sh` created that venv); check one note with `--validate NOTE.md`.

### The gallery

`install.sh` (unless `--no-launchd`) adds a launchd agent (`io.github.wiki-inspi.map`) that serves the gallery on `http://127.0.0.1:18766/` and a **Wiki Inspi** app (in `/Applications`, or `~/Applications` if that is not writable) that opens it, starting the server first if needed. The page is rebuilt after each classification pass. Rebuild it by hand with `inspi map` (`inspi map --vision-limit N` reads at most N new pictures per run; the launchd rebuild uses 50, so a big library fills in over a few passes); rebuild the app with `scripts/make_app.sh`. Installed with `--no-launchd`? Nothing serves the page for you: run `inspi serve` in a terminal and open `http://127.0.0.1:18766/` (or run `inspi map` once the server is up).

- **Wall**: every image in columns, with search, sort (newest, oldest, color, title, board, family) and filters by type (photography, cinematic, illustration...), technique (ink, watercolor, mixed media...), what it shows (abstract, geometric, people...), color and temperature. Filters are keyword rules over what the classifier wrote, recomputed on every build, no extra model call.
- **Families**: a map where close images attract (same style, technique, mood, neighbouring colors). Families are communities in a nearest-neighbour graph (TF-IDF over the classification fields plus Lab color distance, Louvain).
- **Moodboards**: pick images with **+**, then create a new Excalidraw board or add them to an existing one. Adding never moves what is already on the board; a copy of the board is kept in `~/.local/state/wiki-inspi/board-backups/` before each write. After a board is made, the page shows similar images.
- **More like this**: select one or more images and click **More like this**: the closest ones by what the pictures look like (composition, texture, subject), not by tags. Computed on your machine by macOS Vision (a small Swift program, `src/inspi_vision.swift`, compiled once to `~/.local/bin/inspi-vision` the first time it is needed) and cached in `<vault>/.wiki-inspi/vision.jsonl`.
- **Text in pictures**: the same pass reads the text printed in each image (French and English), so the search box and `inspi search` find a poster by a word on it.
- **Exact color**: pick a color and a tolerance; images whose main color or palette has a close match (perceptual Lab distance) stay.
- **Saved searches**: name the current search (words, filters, exact color) and keep it as a button. Tick **board that fills itself** and the search owns a moodboard `Moodboards/auto-<name>.excalidraw.md`: each pass only appends the new matches, a picture you removed from the board never comes back, and a board edited in the last 2 minutes is left alone. Boards you made by hand are never touched. Saved searches live in `<vault>/.wiki-inspi/saved.json`.
- **Picture of the day**: `http://127.0.0.1:18766/today` opens one old capture you have not seen yet (one per day, from your oldest pictures first); `/today.jpg` returns it as an image, handy for a desktop widget.
- **Duplicates and delete**: identical files (same bytes) and near-identical ones (perceptual hash) are grouped, the classified, larger, older copy is kept by default. **×** moves a note to `<vault>/.trash/wiki-inspi/`, with its image unless another note or board still uses it, and **Undo** puts it back. Nothing is deleted for good from the page.
- The UI is in French when the browser is in French, English otherwise (`?lang=en` forces it).

## Privacy and security

- Images, notes and the index live in your vault. Nothing is uploaded except the classifier call: with `claude-cli` or `anthropic-api` the downscaled image and the page text (title, URL, alt) go to Anthropic; with `ollama` nothing leaves the machine.
- The receiver listens on 127.0.0.1 only. Any web page can make your browser send requests there, so every request must pass, in order: `Host == 127.0.0.1:<port>` (DNS rebinding), `Origin == chrome-extension://<paired id>` (web pages and other extensions), and a bearer token compared in constant time against the Keychain value on each request. No CORS headers are sent. Image bytes are verified by magic bytes and Pillow, names are server-made, writes are confined to the vault. The one URL the receiver fetches itself is a Pinterest pin page (https, Pinterest host, `pinimg.com` images only).
- The gallery server also listens on 127.0.0.1 only and checks `Host` on every request; writes (board, delete, restore, saved searches) also need `Origin == http://127.0.0.1:<map_port>` and a JSON body, so another web page cannot trigger them. It serves only the gallery folder and the vault's `_attachments/`; it never fetches a URL. It has no token: any program already running as you on the machine can call it, same as it can edit the vault directly.
- The token is generated in-process and stored in the Keychain (service `wiki-inspi-token`, account = your user). Pairing hands it to the extension once, inside a 60 s window; the extension keeps it in `chrome.storage.local` (readable from your browser profile: it is a dedicated token, rotate it with `scripts/setup-token.sh`). The value is handed to `security add-generic-password -w` through its password prompt (a pseudo-terminal), never as a command-line argument.

## Limitations

- macOS only (Keychain, launchd, `sips`/Pillow, Vision for the gallery's pixel features).
- The extension is loaded in developer mode; it is not on the Chrome Web Store.
- Pinterest: the original-size image is fetched through Pinterest's own pages, which is fragile and subject to their terms. Use it for personal reference only and respect authors' copyright. Same for anything you capture: the tool stores a source URL and, when the page says so, an author, but rights stay with the creator.
- Classification quality depends on the model; the validator rejects malformed output, not wrong opinions.
- Obsidian plugins are not bundled; the Base and Dataview views need them.
- "More like this" and text in pictures need the Xcode command line tools; the first build after install compiles the Swift helper and reading a large library takes a while.
- The gallery's similar images and families only cover classified notes; notes still in `Inbox/` sit in a "not classified yet" group. Facet rules are keyword matches, so a few images land in the wrong filter.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q tests --ignore=tests/e2e
node tests/js/inspi_filter_cases.test.js   # the page filter agrees with the Python one (also run by pytest when node exists)
```

`tests/e2e/` drives real Chromium with the unpacked extension in a fresh temporary profile (never your real one); it needs Playwright and is optional.

## En bref (FR)

Un clic droit sur une image web l'envoie dans un vault Obsidian dédié; un modèle de vision la classe (style, palette, couleur principale, technique, époque, ambiance); `inspi search` et `inspi board` cherchent et fabriquent des moodboards Excalidraw ; l'app **Wiki Inspi** ouvre une galerie (mur, filtres, couleur exacte, « plus comme ça » par les pixels, texte lu dans les images, recherches enregistrées avec tableaux qui se remplissent seuls, familles d'images, doublons, moodboards en quelques clics). Les fonctions par les pixels demandent macOS et les outils en ligne de commande Xcode (`xcode-select --install`). Tout reste en local, sauf l'appel au classifieur si vous choisissez un modèle cloud. Installation : `./install.sh`, puis chargez `extension/` en mode développeur et appairez avec `scripts/setup-token.sh`.

## License

MIT, see `LICENSE`.
