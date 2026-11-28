# Araniko plugins

Plugins give your agents extra jobs to do in a project. This repo is the public list the Plugins page in Araniko shows. Every plugin here was read and approved on GitHub before it was listed.

## Make your own

A plugin is a folder. Put it in any GitHub repository, either on its own or as a folder inside a bigger repo.

```
my-plugin/
  plugin.yaml   required
  README.md     required; shown on the plugin's page
  on.md         required; what to do when it's turned on in a project
  off.md        required; how to take it out of a project cleanly
  update.md     optional; what to do in a project when the plugin is updated
  skills/       optional; agent skills
  templates/    optional; files the instructions can copy into the project
  screens/      optional; the plugin's own screens
  logo.png      optional; the picture `logo:` names
  screenshots/  optional; the pictures `screenshots:` names
```

`on.md`, `off.md` and `update.md` are instructions for the project's agent, written in plain words. Araniko sends them to the agent when someone turns your plugin on, takes it out, or updates it.

### plugin.yaml

```yaml
name: launch-checklist       # a-z, 0-9 and hyphens; 2-40 characters; unique in this list
title: Launch checklist      # the name people see, up to 40 characters
summary: Checks a project over before you ship it   # one line, up to 90 characters
author: Your name
version: 0.1.0               # major.minor.patch
icon: CheckmarkSquare02Icon  # a Hugeicons name
logo: logo.png               # optional; your own picture for its tile
category: shipping           # one of the ids in categories.yaml
screenshots:                 # optional; up to 4
  - file: screenshots/notes.png
    alt: Release notes drafted from this week's merged work
needs:                       # optional
  araniko: ">=0.9.0"         # the oldest Araniko it works with
  connections: [github]      # today only "github" (the gh login)
settings:                    # optional; up to 8
  - key: destination
    label: Feedback goes to
    type: choice             # choice | text | secret
    options: [GitHub issues]
    default: GitHub issues
```

- **`logo`:** optional. A PNG, JPEG, WebP or SVG in the folder, 256 KB or smaller. Araniko shows `icon:` until the picture loads, and instead of it if the file is missing or broken, so keep a good `icon:` too.
- **`screenshots`:** optional. Up to 4 PNG or JPEG pictures in the folder, 1 MB or smaller each; a `screenshots/` folder keeps them tidy. Each has a `file` and an `alt`: one line, up to 140 characters, saying what the picture shows. They're shown on the plugin's page on araniko.dev; Araniko itself doesn't show them yet. A missing or bad screenshot fails the check.

Plugins with their own screens also have `screens:` (where they show up in the app) and `can:` (everything those screens may do, such as `reach: [api.github.com]` for the websites they talk to). Leave `can:` out and the plugin can do nothing beyond its instructions.

Keep each value on one line. Quote text that starts with a symbol or contains `: ` or ` #`.

### Try it

```
araniko plugin new my-plugin     # makes the folder with every file it needs
araniko plugin check my-plugin   # tells you what's missing or wrong
```

To try it, open Plugins in Araniko and choose **Add from a folder**.

**Privately:** to share a plugin only with people you choose, give them the folder (a zip, a shared drive or a private repository they can clone). They add it with Plugins, **Add from a folder**. There's no other way to add a private plugin.

## Get listed

1. Push your plugin to a public GitHub repository and note the full commit (all 40 characters) you want listed.
2. Run `araniko plugin check` on that folder until it passes.
3. Open a pull request here that adds one file, `plugins/<name>.yaml`, where `<name>` is the `name` in your `plugin.yaml`:

   ```yaml
   repo: your-name/your-repo
   commit: 0123456789abcdef0123456789abcdef01234567   # the full 40-character commit
   path: .                                            # the plugin's folder inside the repo
   ```

4. The automatic check downloads your plugin at that commit, runs `araniko plugin check`, and comments with what your plugin can do.
5. Subaz and Claude review every pull request. Merged means listed: it shows up in Araniko within the hour.

**Updating:** open a new pull request that changes the `commit` in your file to the new one. It's reviewed the same way. If the new version asks for anything new in `can:` (a new website in `reach`, for example), the check points it out, and people who already use your plugin are asked before they get it.

**Taking it down:** open a pull request that removes your file.

## How this repo works

- `plugins/<name>.yaml`: one file per listed plugin, pinned to the exact commit that was reviewed.
- `categories.yaml`: the groups on the Browse page and their order.
- `index.json`: built from the two above by `scripts/build_index.py` on every change to main. The app reads only this file.
- `.github/workflows/check.yml`: the pull request check. `build.yml`: rebuilds `index.json`.
- `examples/index.sample.json`: a made-up `index.json` for testing the app against.

Run the tests with `python3 -m unittest discover -s tests`.
