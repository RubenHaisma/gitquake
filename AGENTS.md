# Agent instructions

This repo turns a GitHub user's commit history into an animated **seismograph**:
lines-added-per-month is the ground motion, major AI model releases are fault lines.

## Build it

```bash
python3 seismograph.py --open
```

Auth uses what your environment already has: `GITHUB_TOKEN` env, else the `gh` CLI.
No login flow, no extra installs - it's stdlib only.

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

## Performance

The whole job is I/O-bound: it waits on GitHub's `stats/contributors` endpoint,
which computes lazily and answers `202` while it works (and `204` for empty repos).
A cold run is minutes of network wait but ~1s of CPU - rewriting it in a "faster
language" buys nothing. The levers that actually matter are already here:

- **Per-repo cache** keyed by last-push at `~/.cache/gitquake/stats.json`. Unchanged
  repos are never refetched, so re-runs finish in seconds. `--no-cache` bypasses it.
- **Warm → collect → retry** sweep that triggers GitHub's lazy computation up front
  instead of blocking on it repo-by-repo.

If you want it faster still, reduce *requests*, not CPU.

Keep it one file. No dependencies. That's the point.
