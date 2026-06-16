# gitquake

Your GitHub history as a **seismograph**. Lines of code per month are the ground
motion; major AI model releases are the fault lines running through it. The trace
lies near-flat for years, then ruptures — often right where the coding-model fault
lines cluster.

![gitquake seismograph](docs/preview.png)

## Quickstart

One command. No clone, no install, no setup beyond Python 3.8+:

```bash
curl -fsSL https://raw.githubusercontent.com/RubenHaisma/gitquake/main/seismograph.py | python3 - --open
```

It builds `seismograph.html` and opens it. First run, it prints a GitHub device
code to paste in the browser (or silently reuses your `gh` / `GITHUB_TOKEN` if you
already have one). Chart anyone:

```bash
curl -fsSL https://raw.githubusercontent.com/RubenHaisma/gitquake/main/seismograph.py | python3 - --user torvalds --open
```

Prefer a local copy? `python3 seismograph.py --open` works exactly the same.

## Drop it in an agent

Point any coding agent at this repo and say:

> Build me a gitquake seismograph for GitHub user `<name>`.

It has everything it needs — see [`AGENTS.md`](AGENTS.md). One stdlib-only script,
one command, a self-contained HTML file out the other end.

## How it works

- Pulls your per-month **additions** and **commits** from GitHub's
  `stats/contributors` API across every repo you commit to (public + private if you own them).
- Drops **bulk-import repos** (>10k additions per commit — vendored deps, datasets,
  generated files) so the trace reflects code you wrote, not code you pasted.
- Inlines the data and a curated AI-model-release timeline into a single animated
  HTML file. The seismograph pen draws itself on load; toggle **Lines / Raw / Tremors**.
- Caches each repo's stats by last-push, so the first run takes a few minutes (it is
  almost entirely network wait) but every re-run finishes in seconds. `--no-cache`
  forces a full refresh.

## Honest caveats

- "Lines" is GitHub's additions count — a measure of activity, not artistry. The
  **Raw** channel keeps the bulk imports if you want the unfiltered truth.
- Model release dates are curated (`EVENTS` in `seismograph.py`); the most recent
  ones are approximate. Edit them freely.
- Correlation, not causation. The chart shows your output rose alongside the
  coding-model era. It can't prove one caused the other.

## License

MIT — see [LICENSE](LICENSE).
