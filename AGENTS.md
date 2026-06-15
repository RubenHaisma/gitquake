# Agent instructions

This repo turns a GitHub user's commit history into an animated **seismograph**:
lines-added-per-month is the ground motion, major AI model releases are fault lines.

## Build it

```bash
gh auth login                 # once; or export GITHUB_TOKEN=...
python3 seismograph.py        # -> seismograph.html for the logged-in user
```

Useful flags:

- `--user LOGIN` chart someone else (public repos only unless you own them)
- `--out PATH` output file (default `seismograph.html`)
- `--open` open the result in the browser when done

That's the whole tool: **one stdlib-only Python file, zero pip installs.** The output
is a single self-contained HTML file (data is inlined as JSON; fonts load from Google
Fonts over the network).

## How it works (so you can extend it)

1. List every repo the target commits in (`gh`/REST `…/repos`).
2. For each, pull `GET /repos/{owner}/{repo}/stats/contributors`, keep the target's
   weekly `additions` and `commits`, aggregate by month.
3. Drop **bulk-import repos** (>100k additions and >10k additions/commit — those are
   vendored deps, datasets, lockfiles, not hand-written code). See `BULK_MIN_ADD` /
   `BULK_RATIO`.
4. Inline the series + the `EVENTS` model-release list into the HTML template and write it.

## Common changes

- **Model timeline:** edit the `EVENTS` list (`date, label, vendor, is_coding`) and
  `SHORT` labels near the top of `seismograph.py`.
- **Vendor colors:** `VENDOR_COLORS`.
- **Look & feel:** everything visual lives in the `TEMPLATE` string (CSS `:root`
  variables for palette, the `<canvas>` render loop for the trace).
- **Filter strictness:** tune `BULK_RATIO`. Set it very high to keep everything (raw).

Keep it one file. No dependencies. That's the point.
