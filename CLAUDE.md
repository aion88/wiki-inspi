# Wiki Inspi: install playbook for a Claude Code agent

You were given this repository to set Wiki Inspi up for a human on their Mac. Do the parts an
agent can do, then guide the human through the parts only they can do, one step at a time,
verifying each step before the next. Tell them what you are about to run before you run it.

## Hard rules

- Never ask the human to paste a secret (token, API key) into the chat. Never print one. Do not
  run `env`, `printenv`, `security ... -w`, or read `~/.config/wiki-inspi/token`.
- Do not touch the Keychain yourself except through `install.sh` / `scripts/setup-token.sh`. If a
  macOS keychain dialog appears, stop and tell the human what it is for; do not click through it
  on their behalf.
- Do not open the human's real browser profile with another binary (no Playwright or Puppeteer
  on their Chrome/Brave profile). The extension is loaded by the human, in their own browser.
- Do not create a GitHub repo, push, or send anything anywhere. Everything here is local.
- Ask before installing software (Homebrew casks, Python packages outside the private venv).

## Part A: what you do

1. **Check the machine.** `uname -s` must print `Darwin`; otherwise stop, this tool is macOS only.
   `python3 --version` must be 3.10 or newer; if not, offer `brew install python` (only if
   `command -v brew` succeeds and the human agrees).
2. **Ask the human two questions**, then proceed: where the vault should live (default
   `~/Wiki Inspi`), and which classifier they want: (a) their Claude subscription via the Claude
   Code CLI, (b) an Anthropic API key, (c) local Ollama. Only (b) and the cloud part of (a) send
   the downscaled image and page text off the machine; say so.
3. **Run the installer** from the repo root:
   `./install.sh --vault "<path>" --backend <claude-cli|anthropic-api|ollama> --non-interactive`.
   It creates the vault skeleton, config, Keychain token, `inspi` command and launchd agents. It
   is idempotent. Read its output; it prints the next steps.
4. **Obsidian.** If `/Applications/Obsidian.app` is missing: if `command -v brew` succeeds and the
   human agrees, run `brew install --cask obsidian`; otherwise point them to https://obsidian.md.
5. **Plugins.** Run `python3 scripts/install_plugins.py --vault "<path>"` (uses the interpreter
   `install.sh` reported). It resolves the three plugins (Excalidraw, Dataview, Note Gallery) in
   Obsidian's official registry, downloads `main.js`, `manifest.json` and `styles.css` from each
   latest GitHub release into `<vault>/.obsidian/plugins/<id>/`, and lists them in
   `community-plugins.json`. If a plugin reports a failure, say which and why; do not substitute
   another source.
6. **Backend prerequisites** (do what you can, report the rest):
   - claude-cli: `command -v claude`; if absent, tell the human to install Claude Code and log in.
   - anthropic-api: the key must be in the launchd environment. Tell the human to add it
     themselves to `EnvironmentVariables` in `~/Library/LaunchAgents/io.github.wiki-inspi.*.plist`
     (the enricher and receiver), then reload; never ask for the value.
   - ollama: `curl -s localhost:11434/api/tags`; if the model in the config (`ollama_model`) is
     absent, offer `ollama pull <model>`.
7. Run `inspi doctor` (or `python3 bin/inspi doctor`). Expect MISSING for the extension, the
   pairing and the receiver until Part B is done. Anything else MISSING: apply the printed fix.

## Part B: guide the human, one step at a time

Give ONE step, wait for the human to say it is done, verify (command or question), then continue.

1. **Open the vault in Obsidian.** Obsidian > Open folder as vault > the vault path. Then
   Settings > Community plugins > Turn on community plugins (Obsidian asks once because plugins are
   third-party code). Verify: Settings > Community plugins lists Excalidraw, Dataview and Note
   Gallery as enabled; `inspi doctor` shows the three plugins OK.
2. **Load the extension.** In Chrome or Brave open `chrome://extensions` (or `brave://extensions`),
   switch on Developer mode, Load unpacked, choose this repo's `extension/` folder. Verify: the
   card "Wiki Inspi" appears, no error badge. Optional: they can copy the 32-letter extension id
   shown on the card and you run `./scripts/setup-token.sh --extension-id <id>` in step 3.
3. **Pair.** Tell them to be ready, then run `./scripts/setup-token.sh` (opens a 60 s window).
   Within 60 s: card > Details > Extension options > click **Pair**, then **Test**. Expected
   message: "OK: receiver reachable, token accepted." If the window closed, run the script again.
   Verify: `inspi doctor` shows "receiver answers /health" and "extension paired and used" OK.
4. **First capture.** Ask them to right-click any image on a web page > "Send to Wiki Inspi".
   Verify: a notification appears and a new note exists in `<vault>/Inbox/` (or `Library/` once
   classified). Look at `~/Library/Logs/wiki-inspi/enrich.log` if the note stays in Inbox.
5. **Web Clipper templates (optional).** Install the Obsidian Web Clipper browser extension from
   its official store page. Then, per template, Settings > Templates > Import and choose one file
   from `~/.config/wiki-inspi/clipper/`: `wiki-inspi-template.json`, `wiki-inspi-pinterest.json`,
   `wiki-inspi-arena.json`, `wiki-inspi-commons.json`. Four imports, one file each. Verify: the four
   names appear in the template list and the vault name on each equals the vault folder name.
6. **Classifier check.** Confirm the backend works with one capture: the note moves from `Inbox/`
   to `Library/` with `style`, `palette` and `main_color` filled. If not: `enrich.log`, then
   `inspi doctor`.
7. **Finish.** Show them `inspi search "<word>"` and `inspi board "<word>" --name test`, and that
   `inspi doctor` is the first thing to run when something stops working.

## If something fails

Run `inspi doctor` first: every MISSING line carries the exact fix. Receiver refusals are in
`~/Library/Logs/wiki-inspi/receiver.log` (one JSON line each; `decision: ok` lines are successes).
`./uninstall.sh` removes the agents, command and token and leaves the vault alone.
