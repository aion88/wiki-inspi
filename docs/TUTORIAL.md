# Tutorial: from one image to a moodboard

This walks through a full loop: save an image, watch it get classified, find it again, and turn a search into an Excalidraw moodboard. It assumes the install is done and `inspi doctor` is all green (see the [README](../README.md)).

## 1. Save an image

Pick whichever door fits the moment. All three end up in the same place.

**Right-click (the main one).** On any web page, right-click an image > **Send to Wiki Inspi**. A small notification says `Wiki Inspi: saved` (or `failed`, with the reason). The extension sends the image bytes, the page URL, its title and the author when the page states one.

**Obsidian Web Clipper.** If you imported the templates the installer wrote to `~/.config/wiki-inspi/clipper/` (they carry your vault name), click the Web Clipper button on a Pinterest pin, an Are.na block or a Wikimedia Commons file. The matching template is picked automatically. The image itself is downloaded later, at classification time.

**Drop folder.** Drag any image file into `~/Inspi Drop` (or the folder you chose at install). It is filed within a few seconds.

## 2. Watch it arrive

Open the vault in Obsidian, then the hub note **_Wiki Inspi**.

- A new capture first lands in `Inbox/` as a short note with the image and its source. It is listed at the bottom of the hub, under *Inbox (waiting to be classified)*.
- The enricher picks it up on its own (no command to run). It shrinks the image, asks the vision model for a strict description, checks the result, then moves the note to `Library/`.
- Expect a few seconds per image with a cloud model, longer with Ollama. Big batches are processed in small rounds, so a large import can take hours: notes keep moving from `Inbox/` to `Library/` in the background.

If a note stays in `Inbox/`, open it: an `enrich_error` field says why the last attempt failed, and after 3 failures it is marked `enrich_failed` and skipped. `tail ~/Library/Logs/wiki-inspi/enrich.log` shows each attempt.

## 3. What the classification gives you

Open any note in `Library/`. Its properties (frontmatter) now hold, among others:

- `style`, `movement`, `technique`, `medium_detail`, `era`
- `palette` (named hex colors with their share of the image), `main_color`, `color_temperature`
- `mood`, `subject`, `composition`, `lighting`, `texture`, `typography` when there is text
- `author` only when the page stated it; otherwise, at most an `author_guess` with a confidence
- `suggested_mocs`: links to theme notes in `MOCs/`, so related images connect in the graph

Tags go through `_taxonomy.yml`, so "brutalist" and "brutalism" become one tag. Edit that file to add your own vocabulary and your own `use_for` list (your projects).

## 4. Find things again

**In Obsidian.** The hub shows recent captures as a gallery. Open **_Wiki Inspi.base** for one view per axis: by main color, style, mood, author, technique, use, domain, plus a palette view. Type in the **Filter** bar to narrow any view.

**From the terminal.**

```sh
inspi search "dark blue"                         # free text: title, tags, style, mood, subject
inspi search --color "dark blue" --mood calm     # color by name or #hex, nearest palette color wins
inspi search --style risograph --technique print --limit 10
inspi search "ferrofluid" --json                 # machine-readable, for scripts
```

Each result line gives the note path, its title, its style and its main color; with `--color` it adds `dE`, the distance to the nearest palette color (lower is closer).

## 5. Make a moodboard

Any search becomes a board: same filters, plus a name.

```sh
inspi board --color "dark blue" --mood calm --name night-water --limit 24
```

This writes `Moodboards/night-water.excalidraw.md`. Open it in Obsidian: the images are laid out in a grid, each captioned with its title, the author when known and the source site, with a palette strip at the bottom. Only notes with a local image are placed; display-only captures are counted as skipped. It is a normal Excalidraw drawing, so you can move, resize, annotate and draw on it. The hub lists every board under *Moodboards*.

Tips:

- Check the count with `inspi search` first. Fewer than 6 results makes a thin board: drop the narrowest filter.
- Re-running `inspi board` with the same `--name` rebuilds the board from the current library, so a board can follow your collection as it grows.

## 6. Browse the gallery

Open **Wiki Inspi** from Applications (or run `inspi map`). It is the same library as a page in your browser:

1. **Wall**: scroll everything, type in the search box, or open **Filters** and pick, say, *Ink* and *People*. Within one row the choices add up (ink OR watercolor); across rows they narrow down (ink AND people).
2. **Families**: switch to the map to see which images belong together. Click a family chip to show only that family on the wall.
3. **A board in three clicks**: press **+** on the images you want, type a name, **Create moodboard**. To grow a board later, pick images and choose **Add to: <board>** in the menu. The page then proposes similar images to keep going.
4. **Clean up**: the **Duplicates** chip groups copies; the green one is kept. **×** on any card sends it to the vault trash, and the **Undo** button in the toast brings it back.

## 7. Optional: ask in plain words

If you use [Claude Code](https://claude.com/claude-code), you can skip the flags: open it in the vault folder (or in an Obsidian plugin that embeds it) and ask "make me a moodboard of calm dark-blue images with organic shapes". It can translate the sentence into `inspi search` / `inspi board` filters, check the count, and open the board. Read what a third-party Obsidian plugin can access before installing one: some let the agent run commands in your vault.

## 8. Housekeeping

- `inspi doctor` checks every link (receiver, token, launchd agents, model access) and prints the fix for what is broken.
- Notes classified by an older version (schema v1) can be brought up to date with `python3 src/enrich.py --reenrich-all` (or the venv's Python if `install.sh` created one). Notes already on the current schema are left as they are.
- Rotate the extension token any time with `scripts/setup-token.sh`, then **Pair** again from the extension's options page.
