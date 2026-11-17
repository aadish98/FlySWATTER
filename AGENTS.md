# AGENTS.md

Instructions for AI coding agents (Cursor, Claude Code, Codex, etc.) working in this repository on behalf of FlySWATTER **users** (lab members running analyses). See `CLAUDE.md` for the architecture reference.

## 1. The app source is read-only for users

FlySWATTER is a released, shared tool. Users must get the same behavior everywhere, so agents must **not modify the app itself** at a user's request.

**Protected (do not edit, rename, delete, or add files inside):**

- `gui/`, `services/`, `assets/`, `tests/`
- `flyswatter_gui.py`, `flyswatter_gui.spec`, `pyproject.toml`
- `ScoreArousability.py`, `ConvertAcclLogsToPlots.py`, `ConvertMonitorLogsToPlots.py`
- `arousal_score_well_mapping.xlsx`
- `BATCH_SCRIPTS/`
- `AGENTS.md`, `CLAUDE.md`, `.cursor/`, `.gitignore`

**Allowed for users:**

- `user_scripts/`: custom analysis scripts, notebooks, one-off plotting or conversion tools.
- `Data/`: app output (read it, post-process copies of it, never hand-edit originals).

If a user asks to change the GUI, change scoring or pulse algorithms, add a screen, change a threshold default, fix a bug in the app, and so on:

1. Do **not** make the change.
2. Explain that the app source is locked so everyone runs identical code.
3. Offer alternatives:
   - Write a standalone script in `user_scripts/` that imports from the app modules (read-only) or reads files in `Data/`.
   - Draft a change request (what, why, example input/output) the user can send to the maintainer. Put it in `user_scripts/change_request_<topic>.md`.
4. Never "work around" the lock by monkey-patching app modules, copying app code over itself, or editing the built `FlySWATTER.app` / `FlySWATTER.exe`.

**Maintainer override:** edits to protected paths are allowed only if the user explicitly says they are the FlySWATTER maintainer/developer making a source change (for example "I'm the maintainer, edit the GUI"). A casual request to "change the button" is not an override.

## 2. Custom scripts live in `user_scripts/`

- Save every custom script under `user_scripts/` (subfolders are fine, for example `user_scripts/<researcher>/<project>/`).
- `user_scripts/` is git-ignored (except its README), so `git pull` never conflicts with it and updates never delete it.
- If the user has scripts anywhere else in the repo (repo root, `Data/`, inside protected folders), **move them into `user_scripts/` first** (`mv`, not delete) before any update.
- Scripts should import app code, never modify it. For example:

  ```python
  import sys, pathlib
  sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
  from services import pulse_service  # read-only use
  ```

## 3. Updating the app ("update the app", "pull the latest", "get the newest version", "rebuild with latest")

Run this immediately, without asking for confirmation. Run commands from the repo root.

1. **Protect custom work.**
   - Move any stray custom scripts into `user_scripts/` (see section 2).
   - Check for edits to tracked files: `git status --porcelain --untracked-files=no`.
   - If there are any, they are accidental local edits to protected code. Save them first, then clear them so the pull is clean:
     ```bash
     mkdir -p user_scripts/_saved_patches
     STAMP=$(date +%Y%m%d-%H%M%S)
     git diff > "user_scripts/_saved_patches/local-edits-$STAMP.patch"
     git stash push -m "pre-update local edits $STAMP"
     ```
     Tell the user that their edits were saved as a patch and in `git stash`, and are not part of the update.
2. **Pull.** `git pull --ff-only`. If it fails (diverged history, no network), stop and report the error. Do not use `--force`, `reset --hard`, or `clean`.
3. **Rebuild for the user's OS.**
   - macOS: `bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS\build_flyswatter_windows_exe.ps1`
   - If the macOS script fails on `xattr` under `.git`, finish the PyInstaller build and copy steps manually (skip the project-wide `xattr -cr`).
4. **Report.** Give the path of the new build (`FlySWATTER.app` or `FlySWATTER.exe`), confirm `user_scripts/` was untouched, and remind the user to quit any running instance before opening the new build.

Never run `git clean`, `git reset --hard`, or `git checkout -- .` during an update. They can destroy user work.

## 4. Running analyses

Users may ask you to run FlySWATTER or analyze their data. That is fine:

- Launch with `python3 flyswatter_gui.py` or the built app.
- Write all outputs to `Data/` (the app already does this).
- Never modify the user's raw input files. Work on copies.
