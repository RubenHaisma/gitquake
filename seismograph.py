#!/usr/bin/env python3
"""
gitquake - render a GitHub user's coding history as a seismograph,
with major AI model releases drawn as fault lines.

The trace amplitude is the lines you added per month (from GitHub's
per-contributor weekly stats). It lies near-flat for years, then ruptures
where your output exploded - often right where the coding-model fault
lines cluster.

Agent-native CLI: run it where GitHub creds already exist (GITHUB_TOKEN env, or
the `gh` CLI authed - both standard in a coding-agent environment).

    python3 seismograph.py                        # seismograph.html for the authed user
    python3 seismograph.py --user torvalds --open

No third-party dependencies; Python 3.8+ stdlib only.
"""
import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error
import datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---- bulk-import filter: a repo whose additions/commit is absurd is a
# ---- vendored/generated dump (node_modules, datasets, lockfiles), not code.
BULK_MIN_ADD = 100_000
BULK_RATIO   = 10_000

# ---- curated major model releases. Edit freely. (date, label, vendor, is_coding)
EVENTS = [
    ("2020-06-11", "GPT-3", "OpenAI", False),
    ("2021-06-29", "GitHub Copilot", "OpenAI", True),
    ("2022-11-30", "ChatGPT", "OpenAI", False),
    ("2023-03-14", "GPT-4", "OpenAI", False),
    ("2023-07-11", "Claude 2", "Anthropic", False),
    ("2023-07-18", "Llama 2", "Meta", False),
    ("2023-11-06", "GPT-4 Turbo", "OpenAI", False),
    ("2024-03-04", "Claude 3", "Anthropic", False),
    ("2024-05-13", "GPT-4o", "OpenAI", False),
    ("2024-06-20", "Claude 3.5 Sonnet", "Anthropic", True),
    ("2024-09-12", "OpenAI o1-preview", "OpenAI", False),
    ("2024-12-05", "OpenAI o1", "OpenAI", False),
    ("2025-01-20", "DeepSeek R1", "DeepSeek", False),
    ("2025-02-24", "Claude 3.7 + Claude Code", "Anthropic", True),
    ("2025-03-25", "Gemini 2.5 Pro", "Google", True),
    ("2025-04-16", "OpenAI o3 / o4-mini", "OpenAI", False),
    ("2025-05-22", "Claude 4 (Opus/Sonnet)", "Anthropic", True),
    ("2025-08-07", "GPT-5", "OpenAI", False),
    ("2025-09-29", "Claude Sonnet 4.5", "Anthropic", True),
    ("2026-02-01", "Claude Opus 4.x", "Anthropic", True),
]
SHORT = {
    "GitHub Copilot": "COPILOT", "ChatGPT": "CHATGPT", "Claude 2": "CLAUDE 2",
    "Llama 2": "LLAMA 2", "GPT-4 Turbo": "GPT-4 TURBO", "Claude 3": "CLAUDE 3",
    "Claude 3.5 Sonnet": "CLAUDE 3.5", "OpenAI o1-preview": "o1-PREVIEW", "OpenAI o1": "o1",
    "DeepSeek R1": "DEEPSEEK R1", "Claude 3.7 + Claude Code": "CLAUDE CODE",
    "Gemini 2.5 Pro": "GEMINI 2.5", "OpenAI o3 / o4-mini": "o3 / o4-MINI",
    "Claude 4 (Opus/Sonnet)": "CLAUDE 4", "Claude Sonnet 4.5": "CLAUDE 4.5",
    "Claude Opus 4.x": "OPUS 4.x",
}
VENDOR_COLORS = {"OpenAI": "#19c37d", "Anthropic": "#ff8a5b", "Google": "#5b9bff",
                 "Meta": "#6f86d6", "DeepSeek": "#b58cff"}


def gh_token():
    """Agent environments always have one of these. Keep it simple."""
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok.strip()
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        sys.exit("No GitHub credentials. Set GITHUB_TOKEN or run `gh auth login`.")


TOKEN = None


def api(path, retries=8):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Accept": "application/vnd.github+json", "User-Agent": "gitquake"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=75) as r:
                if r.status == 202:                 # stats still computing - retry
                    time.sleep(2 + attempt * 2); continue
                if r.status == 204:                 # no content (empty repo) - permanent
                    return []
                data = r.read()
                if not data:
                    time.sleep(2 + attempt * 2); continue
                return json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code in (202, 403, 429):
                time.sleep(3 + attempt * 2); continue
            return None
        except Exception:
            time.sleep(2)
    return None


def whoami():
    me = api("/user")
    return me.get("login") if me else None


def list_repos(target, is_self):
    """{full_name: pushed_at} for every repo we can score the target in.
    pushed_at is the cache key: if a repo hasn't been pushed to, its stats
    can't have changed, so we never refetch it."""
    base = "user/repos" if is_self else f"users/{target}/repos"
    repos, page = {}, 1
    while True:
        chunk = api(f"/{base}?per_page=100&page={page}")
        if not chunk:
            break
        for r in chunk:
            repos[r["full_name"]] = (r.get("pushed_at") or "")[:19]
        if len(chunk) < 100:
            break
        page += 1
    return repos


def collect_repo(repo, target, patience=8):
    """Returns (repo, data_or_None, status) where status is ok | empty | fail.
    'fail' means GitHub never returned the stats (202/timeout) - worth a retry;
    'empty' means the target has no commits here - don't retry.
    `patience` is the api retry budget (low = quick triage, high = wait it out)."""
    stats = api(f"/repos/{repo}/stats/contributors", retries=patience)
    if not isinstance(stats, list):
        return repo, None, "fail"
    add_m, com_m, total_add, total_com = defaultdict(int), defaultdict(int), 0, 0
    for c in stats:
        author = c.get("author") or {}
        if (author.get("login") or "").lower() != target.lower():
            continue
        for w in c.get("weeks", []):
            month = dt.datetime.fromtimestamp(w["w"], dt.timezone.utc).strftime("%Y-%m")
            if w.get("a"):
                add_m[month] += w["a"]; total_add += w["a"]
            if w.get("c"):
                com_m[month] += w["c"]; total_com += w["c"]
    if total_add == 0 and total_com == 0:
        return repo, None, "empty"
    return repo, {"add": dict(add_m), "com": dict(com_m),
                  "total_add": total_add, "total_com": total_com}, "ok"


CACHE_VERSION = 1


def _cache_file():
    return os.path.join(os.path.expanduser("~/.cache/gitquake"), "stats.json")


def load_cache():
    try:
        with open(_cache_file()) as f:
            blob = json.load(f)
        if blob.get("version") == CACHE_VERSION:
            return blob.get("entries", {})
    except Exception:
        pass
    return {}


def save_cache(entries):
    try:
        path = _cache_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": CACHE_VERSION, "entries": entries}, f)
    except Exception:
        pass


def ckey(target, repo, pushed):
    return f"{target.lower()}::{repo}::{pushed or ''}"


def _sweep(repos, target, results, cache, meta, workers, patience, phase):
    """One concurrent sweep. Records ok/empty results into the cache (empty is
    cached too, so untouched repos are never refetched). Returns the timeouts."""
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(collect_repo, r, target, patience): r for r in repos}
        for f in as_completed(futs):
            repo, res, status = f.result()
            if status in ("ok", "empty"):
                cache[ckey(target, repo, meta.get(repo))] = res   # dict, or None for empty
                if status == "ok":
                    results[repo] = res
            else:
                failed.append(repo)
            print(f"\r  {phase}: {len(results)} repos with commits",
                  end="", file=sys.stderr, flush=True)
    return failed


def collect(repos_meta, target, use_cache=True):
    cache = load_cache() if use_cache else {}
    results, to_scan, hits = {}, [], 0
    for repo, pushed in repos_meta.items():
        key = ckey(target, repo, pushed)
        if use_cache and key in cache:
            hits += 1
            if cache[key] is not None:
                results[repo] = cache[key]
        else:
            to_scan.append(repo)
    if use_cache:
        print(f"  cache: {hits} unchanged, {len(to_scan)} to fetch", file=sys.stderr)

    if to_scan:
        # Phase 1 warms GitHub's lazy stats (quick, fire-and-move-on); repos
        # already computed return immediately. Phase 2 collects what was still
        # computing after a short wait; phase 3 mops up any stragglers.
        pending = _sweep(to_scan, target, results, cache, repos_meta,
                         workers=16, patience=1, phase="warming")
        if pending:
            time.sleep(4)
            pending = _sweep(pending, target, results, cache, repos_meta,
                             workers=10, patience=8, phase="collecting")
        if pending:
            pending = _sweep(pending, target, results, cache, repos_meta,
                             workers=4, patience=8, phase="retrying")
        print(file=sys.stderr)
        if pending:
            print(f"  note: {len(pending)} repos could not be scored (stats unavailable)",
                  file=sys.stderr)

    if use_cache:
        current = {ckey(target, r, p) for r, p in repos_meta.items()}
        prefix = target.lower() + "::"
        cache = {k: v for k, v in cache.items()
                 if not k.startswith(prefix) or k in current}   # drop this target's stale keys
        save_cache(cache)
    return results


def build_payload(repos, target):
    flagged = {n: r for n, r in repos.items()
               if r["total_add"] >= BULK_MIN_ADD
               and r["total_add"] / max(r["total_com"], 1) >= BULK_RATIO}
    raw_add, code_add, commits = defaultdict(int), defaultdict(int), defaultdict(int)
    for n, r in repos.items():
        bulk = n in flagged
        for m, a in r["add"].items():
            raw_add[m] += a
            if not bulk:
                code_add[m] += a
        for m, c in r["com"].items():
            commits[m] += c

    months = sorted(set(raw_add) | set(commits))
    if not months:
        sys.exit(f"No commits found for '{target}'. Check the username / auth scopes.")

    def pm(s): return (int(s[:4]), int(s[5:7]))
    (sy, sm), (ey, em) = pm(months[0]), pm(months[-1])
    axis, y, mo = [], sy, sm
    while (y, mo) <= (ey, em):
        axis.append(f"{y:04d}-{mo:02d}")
        mo += 1
        if mo > 12:
            mo = 1; y += 1

    def series(d): return [d.get(k, 0) for k in axis]
    def cumul(s):
        out, run = [], 0
        for v in s:
            run += v; out.append(run)
        return out

    def x_for(d):
        by, bm = pm(axis[0])
        return round((d.year - by) * 12 + (d.month - bm) + (d.day - 1) / 30.0, 3)

    events = []
    for ds, label, vendor, coding in EVENTS:
        d = dt.date(int(ds[:4]), int(ds[5:7]), int(ds[8:10]))
        x = x_for(d)
        if x < -0.5 or x > len(axis) + 0.5:
            continue
        events.append({"x": x, "date": ds, "label": label, "short": SHORT.get(label, label.upper()),
                       "vendor": vendor, "color": VENDOR_COLORS.get(vendor, "#cccccc"), "coding": coding})

    code_s, raw_s, com_s = series(code_add), series(raw_add), series(commits)
    peak_i = max(range(len(code_s)), key=lambda i: code_s[i])
    return {
        "axis": axis, "user": target,
        "metrics": {
            "code": {"series": code_s, "cumul": cumul(code_s)},
            "raw": {"series": raw_s, "cumul": cumul(raw_s)},
            "commits": {"series": com_s, "cumul": cumul(com_s)},
        },
        "events": events, "vendorColors": VENDOR_COLORS,
        "stats": {"codeTotal": sum(code_s), "rawTotal": sum(raw_s), "comTotal": sum(com_s),
                  "repos": len(repos), "start": axis[0], "end": axis[-1],
                  "peakMonth": axis[peak_i], "peakVal": code_s[peak_i], "peakIndex": peak_i,
                  "bulkCount": len(flagged)},
    }


def render_html(payload):
    return TEMPLATE.replace("__PAYLOAD__", json.dumps(payload)).replace("__USER__", payload["user"])


def main():
    global TOKEN
    ap = argparse.ArgumentParser(description="Render a GitHub history as a seismograph.")
    ap.add_argument("--user", help="GitHub login (default: the authenticated user)")
    ap.add_argument("--out", default="seismograph.html", help="output HTML file")
    ap.add_argument("--open", action="store_true", help="open the result in your browser")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the on-disk cache and refetch every repo")
    args = ap.parse_args()

    TOKEN = gh_token()
    me = whoami()
    target = args.user or me
    if not target:
        sys.exit("Could not determine a username. Pass --user.")
    is_self = bool(me) and target.lower() == me.lower()

    print(f"gitquake: charting @{target}", file=sys.stderr)
    repos = list_repos(target, is_self)
    print(f"  found {len(repos)} repositories", file=sys.stderr)
    data = collect(repos, target, use_cache=not args.no_cache)
    payload = build_payload(data, target)
    with open(args.out, "w") as f:
        f.write(render_html(payload))

    s = payload["stats"]
    print(f"\n  {s['codeTotal']:,} lines (filtered) · {s['comTotal']:,} commits · "
          f"{s['repos']} repos · {s['start']}..{s['end']}", file=sys.stderr)
    print(f"  wrote {args.out}", file=sys.stderr)
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.out))


# ===================== the seismograph (self-contained HTML) =====================
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEISMOGRAPH OF THE MACHINE AGE</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Stencil+Display:wght@500;700;900&family=IBM+Plex+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --void:#070808; --amber:#ff9e42; --hot:#fff4e2;
    --ink:#ece2cf; --mute:#8a8169;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{height:100%;background:var(--void);overflow:hidden}
  body{font-family:'IBM Plex Mono',ui-monospace,monospace;color:var(--ink);
    cursor:crosshair;-webkit-font-smoothing:antialiased}
  #stage{position:fixed;inset:0}
  canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
  .ui{position:absolute;z-index:5;pointer-events:none}
  .tl{top:34px;left:40px;max-width:60vw}
  .tag{font-size:11px;letter-spacing:.42em;color:var(--mute);text-transform:uppercase;
    display:flex;align-items:center;gap:11px;margin-bottom:14px}
  .tag::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--amber);
    box-shadow:0 0 12px var(--amber),0 0 22px var(--amber);animation:beat 1.7s ease-in-out infinite}
  @keyframes beat{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.72)}}
  h1{font-family:'Big Shoulders Stencil Display',sans-serif;font-weight:900;
    font-size:clamp(34px,6.6vw,86px);line-height:.84;letter-spacing:.01em;color:var(--ink);
    text-transform:uppercase;text-shadow:0 0 38px rgba(255,158,66,.18)}
  h1 .glow{color:var(--amber);text-shadow:0 0 26px rgba(255,158,66,.55),0 0 60px rgba(255,158,66,.25)}
  .sub{margin-top:16px;font-size:12px;letter-spacing:.08em;color:var(--mute);line-height:1.7;max-width:560px}
  .sub b{color:var(--ink);font-weight:500}
  .tr{top:34px;right:40px;text-align:right}
  .keycap{font-size:10px;letter-spacing:.34em;color:var(--mute);text-transform:uppercase;margin-bottom:12px}
  .vk{display:flex;flex-direction:column;gap:7px;align-items:flex-end}
  .vk .row{font-size:11px;letter-spacing:.12em;color:var(--mute);display:flex;align-items:center;gap:9px}
  .vk .row i{width:22px;height:2px;display:inline-block;border-radius:2px}
  .strip{left:0;right:0;bottom:0;display:flex;border-top:1px solid rgba(150,140,110,.13);
    background:linear-gradient(180deg,rgba(8,9,10,0),rgba(8,9,10,.86) 38%)}
  .cell{flex:1;padding:16px 22px 20px;border-right:1px solid rgba(150,140,110,.09)}
  .cell:last-child{border-right:0}
  .cell .k{font-size:9.5px;letter-spacing:.26em;color:var(--mute);text-transform:uppercase}
  .cell .v{font-size:clamp(17px,2vw,26px);font-weight:500;color:var(--ink);margin-top:7px}
  .cell .v em{font-style:normal;color:var(--amber)}
  .cell .v small{font-size:11px;color:var(--mute)}
  .ctl{bottom:108px;right:40px;display:flex;gap:10px;align-items:center;pointer-events:auto}
  .chan{display:inline-flex;border:1px solid rgba(150,140,110,.2);border-radius:2px;overflow:hidden}
  .chan button{font-family:inherit;background:transparent;border:0;color:var(--mute);cursor:pointer;
    font-size:10.5px;letter-spacing:.18em;padding:9px 14px;text-transform:uppercase;transition:.18s}
  .chan button+button{border-left:1px solid rgba(150,140,110,.16)}
  .chan button:hover{color:var(--ink)}
  .chan button.on{background:var(--amber);color:#1a0f04;font-weight:600}
  .rec{font-family:inherit;background:transparent;color:var(--amber);border:1px solid rgba(255,158,66,.42);
    border-radius:2px;cursor:pointer;font-size:10.5px;letter-spacing:.2em;padding:9px 16px;text-transform:uppercase;transition:.18s}
  .rec:hover{background:rgba(255,158,66,.12);box-shadow:0 0 22px rgba(255,158,66,.18)}
  #tip{position:absolute;z-index:6;pointer-events:none;opacity:0;transition:opacity .15s;
    border:1px solid rgba(255,158,66,.4);background:rgba(10,9,8,.92);padding:7px 11px;border-radius:2px;
    font-size:11px;letter-spacing:.06em;white-space:nowrap;transform:translate(-50%,-140%)}
  #tip b{color:var(--amber)} #tip span{color:var(--mute)}
  .scan{position:fixed;inset:0;z-index:7;pointer-events:none;mix-blend-mode:overlay;opacity:.5;
    background:repeating-linear-gradient(0deg,rgba(255,255,255,.035) 0 1px,transparent 1px 3px)}
  .vig{position:fixed;inset:0;z-index:7;pointer-events:none;
    background:radial-gradient(120% 90% at 50% 42%,transparent 52%,rgba(0,0,0,.62) 100%)}
  .grain{position:fixed;inset:-50%;z-index:7;pointer-events:none;opacity:.05;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    animation:grain 7s steps(6) infinite}
  @keyframes grain{0%{transform:translate(0,0)}20%{transform:translate(-6%,4%)}40%{transform:translate(4%,-5%)}
    60%{transform:translate(-3%,2%)}80%{transform:translate(5%,3%)}100%{transform:translate(0,0)}}
  .boot{position:fixed;inset:0;z-index:9;background:var(--void);pointer-events:none;
    transition:opacity 1s ease .2s;display:flex;align-items:center;justify-content:center}
  .boot.off{opacity:0}
  .boot span{font-size:11px;letter-spacing:.4em;color:var(--amber);text-transform:uppercase;animation:beat 1.2s infinite}
</style>
</head>
<body>
<div id="stage">
  <canvas id="seis"></canvas>
  <div class="ui tl">
    <div class="tag" id="station">@__USER__ · Observatory</div>
    <h1>Seismograph<br>of the <span class="glow">Machine&nbsp;Age</span></h1>
    <p class="sub">Continuous recording of every line committed to git, plotted as ground
      motion. The trace lies still for years, then ruptures - often right where the
      <b>coding-model fault lines</b> cluster. <span id="subline"></span></p>
  </div>
  <div class="ui tr"><div class="keycap">Fault Lines / Vendors</div><div class="vk" id="vk"></div></div>
  <div class="ui ctl">
    <div class="chan" id="chan">
      <button data-c="code" class="on">Lines &fnof;</button>
      <button data-c="raw">Raw</button>
      <button data-c="commits">Tremors</button>
    </div>
    <button class="rec" id="rec">&#8635; Re-record</button>
  </div>
  <div class="ui strip" id="strip"></div>
  <div id="tip"></div>
</div>
<div class="scan"></div><div class="vig"></div><div class="grain"></div>
<div class="boot" id="boot"><span>calibrating seismometer&hellip;</span></div>
<script>
const D = __PAYLOAD__;
const AXIS = D.axis, N = AXIS.length;
const CH = {code:D.metrics.code.series, raw:D.metrics.raw.series, commits:D.metrics.commits.series};
const UNIT = {code:"lines", raw:"lines", commits:"commits"};
const EV = D.events, S = D.stats;
const fmt = n => n.toLocaleString('en-US');
const monthLbl = k => {const [y,m]=k.split('-');return new Date(y,m-1).toLocaleString('en-US',{month:'short',year:"2-digit"});};
document.getElementById('subline').innerHTML = S.repos+' stations · '+monthLbl(S.start)+' → '+monthLbl(S.end)+'.';
document.getElementById('vk').innerHTML = Object.entries(D.vendorColors).map(([k,c])=>
  `<div class="row"><span>${k.toUpperCase()}</span><i style="background:${c};box-shadow:0 0 8px ${c}"></i></div>`).join('');
(function(){const yr=(((S.end.split('-')[0]-S.start.split('-')[0])*12+(+S.end.split('-')[1]-+S.start.split('-')[1])+1)/12).toFixed(1);
  const cells=[['Cumulative displacement',`<em>${fmt(S.codeTotal)}</em> <small>lines &fnof;</small>`],
    ['Raw additions',`${fmt(S.rawTotal)} <small>incl. imports</small>`],
    ['Tremors logged',`${fmt(S.comTotal)} <small>commits</small>`],
    ['Strongest quake',`<em>${fmt(S.peakVal)}</em> <small>${monthLbl(S.peakMonth)}</small>`],
    ['Stations · span',`${S.repos} <small>repos · ${yr} yr</small>`]];
  document.getElementById('strip').innerHTML=cells.map(([k,v])=>`<div class="cell"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');})();

const cv=document.getElementById('seis'), cx=cv.getContext('2d');
let W,H,DPR,P,channel='code',amp=[],maxV=1;
const NT=[]; (function(){let s=20260615; for(let i=0;i<4096;i++){s=(s*1103515245+12345)&0x7fffffff; NT.push((s/0x7fffffff)*2-1);} })();
function vn(x){const xi=Math.floor(x),xf=x-xi;const a=NT[((xi%4096)+4096)%4096],b=NT[(((xi+1)%4096)+4096)%4096];const u=xf*xf*(3-2*xf);return a+(b-a)*u;}
function tremor(x,t){return vn(x*0.42+t)*0.58 + vn(x*1.21-t*1.3)*0.30 + vn(x*3.7+t*0.4)*0.12;}
function setChannel(c){channel=c; const s=CH[c]; maxV=Math.max(...s,1); amp=s.map(v=>Math.pow(Math.max(v,0)/maxV,0.62));}
setChannel('code');
function layout(){DPR=Math.min(2,window.devicePixelRatio||1);W=cv.clientWidth;H=cv.clientHeight;
  cv.width=Math.round(W*DPR);cv.height=Math.round(H*DPR);cx.setTransform(DPR,0,0,DPR,0,0);
  const ml=64,mr=64,mt=Math.max(210,H*0.40),mb=132;
  P={l:ml,r:W-mr,t:mt,b:H-mb,w:W-ml-mr};P.cy=(P.t+P.b)/2;P.maxAmp=(P.b-P.t)*0.48;}
const xAt=i=>P.l+(i/(N-1))*P.w, iAt=px=>(px-P.l)/P.w*(N-1);
function ampAt(fi){fi=Math.max(0,Math.min(N-1,fi));const i=Math.floor(fi),f=fi-i;return amp[i]+((amp[Math.min(N-1,i+1)]-amp[i])*f);}
function grid(){cx.strokeStyle='rgba(150,140,110,.055)';cx.lineWidth=1;
  for(let g=0;g<=8;g++){const y=P.t+(P.b-P.t)*g/8;cx.beginPath();cx.moveTo(P.l,y);cx.lineTo(P.r,y);cx.stroke();}
  cx.fillStyle='rgba(138,129,105,.55)';cx.font='10px "IBM Plex Mono"';cx.textAlign='center';let last=null;
  for(let i=0;i<N;i++){const y=AXIS[i].slice(0,4);if(y!==last){last=y;const x=xAt(i);
    cx.strokeStyle='rgba(150,140,110,.10)';cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,P.b);cx.stroke();cx.fillText("'"+y.slice(2),x,P.b+22);}}
  cx.strokeStyle='rgba(255,158,66,.16)';cx.setLineDash([2,5]);cx.beginPath();cx.moveTo(P.l,P.cy);cx.lineTo(P.r,P.cy);cx.stroke();cx.setLineDash([]);}
function heat(){const ex=xAt(S.peakIndex);const g=cx.createRadialGradient(ex,P.cy,0,ex,P.cy,P.w*0.34);
  g.addColorStop(0,'rgba(255,140,50,.16)');g.addColorStop(.5,'rgba(255,120,40,.05)');g.addColorStop(1,'rgba(0,0,0,0)');cx.fillStyle=g;cx.fillRect(0,0,W,H);}
function buildTrace(revealPx,t){const pts=[];const step=1.4;
  for(let x=P.l;x<=Math.min(revealPx,P.r)+0.1;x+=step){const a=ampAt(iAt(x));const env=(0.035+a*0.965)*P.maxAmp;const w=tremor(x,t);pts.push({x,y:P.cy+w*env,a,env});}return pts;}
function drawEnvelope(pts){if(pts.length<2)return;const g=cx.createLinearGradient(0,P.cy-P.maxAmp,0,P.cy+P.maxAmp);
  g.addColorStop(0,'rgba(255,158,66,0)');g.addColorStop(.5,'rgba(255,158,66,.10)');g.addColorStop(1,'rgba(255,158,66,0)');cx.fillStyle=g;cx.beginPath();
  cx.moveTo(pts[0].x,P.cy-pts[0].env);for(const p of pts)cx.lineTo(p.x,P.cy-p.env);for(let i=pts.length-1;i>=0;i--)cx.lineTo(pts[i].x,P.cy+pts[i].env);cx.closePath();cx.fill();}
function drawTrace(pts){if(pts.length<2)return;cx.save();cx.lineJoin='round';cx.lineCap='round';
  cx.shadowColor='rgba(255,158,66,.85)';cx.shadowBlur=14;cx.strokeStyle='rgba(255,158,66,.92)';cx.lineWidth=1.4;
  cx.beginPath();cx.moveTo(pts[0].x,pts[0].y);for(const p of pts)cx.lineTo(p.x,p.y);cx.stroke();
  cx.shadowColor='rgba(255,244,226,.9)';cx.shadowBlur=10;cx.strokeStyle='rgba(255,247,235,.95)';cx.lineWidth=1;
  cx.beginPath();let pen=false;for(const p of pts){if(p.a>0.5){if(!pen){cx.moveTo(p.x,p.y);pen=true;}else cx.lineTo(p.x,p.y);}else pen=false;}cx.stroke();cx.restore();}
function hex(c,a){const n=parseInt(c.slice(1),16);return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;}
function drawRifts(revealPx,now){let lastX=-99,tier=0;
  for(const e of EV){const ex=xAt(e.x);if(ex<P.l-2||ex>P.r+2)continue;const on=ex<=revealPx+0.5;if(on&&e._ign==null)e._ign=now;const col=e.color;
    const g=cx.createLinearGradient(0,P.t,0,P.b);g.addColorStop(0,hex(col,e.coding?.55:.30));g.addColorStop(.5,hex(col,e.coding?.34:.16));g.addColorStop(1,hex(col,0));
    cx.save();if(e.coding){cx.shadowColor=col;cx.shadowBlur=12;}cx.strokeStyle=g;cx.lineWidth=e.coding?1.5:1;if(!e.coding)cx.setLineDash([3,5]);
    cx.globalAlpha=on?1:0;cx.beginPath();cx.moveTo(ex,P.t-6);cx.lineTo(ex,P.b);cx.stroke();cx.restore();if(!on)continue;
    if(e._ign!=null){const dt=now-e._ign,dur=1100;if(dt<dur){const k=dt/dur,r=k*P.maxAmp*1.7;
      cx.save();cx.globalAlpha=(1-k)*(e.coding?.7:.4);cx.strokeStyle=col;cx.lineWidth=1.4;cx.beginPath();cx.arc(ex,P.cy,r,0,Math.PI*2);cx.stroke();cx.restore();}}
    cx.save();cx.fillStyle=col;cx.shadowColor=col;cx.shadowBlur=e.coding?14:7;cx.beginPath();cx.arc(ex,P.cy,e.coding?3:2,0,Math.PI*2);cx.fill();cx.restore();
    if(ex-lastX<16)tier=(tier+1)%2;else tier=0;lastX=ex;const baseY=P.t+92+tier*70;
    cx.save();cx.translate(ex+4,baseY);cx.rotate(-Math.PI/2);cx.textAlign='left';cx.font=(e.coding?'600 ':'400 ')+'10px "IBM Plex Mono"';
    cx.fillStyle=e.coding?col:hex(col,.78);if(e.coding){cx.shadowColor=col;cx.shadowBlur=8;}cx.fillText((e.coding?'▲ ':'')+e.short,0,0);
    cx.shadowBlur=0;cx.font='400 8px "IBM Plex Mono"';cx.fillStyle='rgba(138,129,105,.7)';cx.fillText(e.date,0,12);cx.restore();}}
function drawPen(revealPx,pts){const last=pts[pts.length-1];if(!last)return;cx.save();
  cx.strokeStyle='rgba(255,247,235,.5)';cx.lineWidth=1;cx.shadowColor='#fff4e2';cx.shadowBlur=16;cx.beginPath();cx.moveTo(revealPx,P.t);cx.lineTo(revealPx,P.b);cx.stroke();
  cx.fillStyle='#fff7eb';cx.shadowColor='#ffd9a0';cx.shadowBlur=20;cx.beginPath();cx.arc(revealPx,last.y,3.2,0,Math.PI*2);cx.fill();cx.restore();}
let hoverPx=null;
function drawHover(){if(hoverPx==null)return;const fi=iAt(hoverPx);if(fi<0||fi>N-1)return;
  const a=ampAt(fi),env=(0.035+a*0.965)*P.maxAmp,y=P.cy-tremor(hoverPx,phase)*env;cx.save();cx.strokeStyle='rgba(236,226,207,.22)';cx.lineWidth=1;
  cx.beginPath();cx.moveTo(hoverPx,P.t);cx.lineTo(hoverPx,P.b);cx.stroke();cx.fillStyle='#fff4e2';cx.shadowColor='#ffd9a0';cx.shadowBlur=14;cx.beginPath();cx.arc(hoverPx,y,3,0,Math.PI*2);cx.fill();cx.restore();}
let start=null,DUR=4200,progress=0,phase=0,running=true;
function ease(t){return 1-Math.pow(1-t,3);}
function frame(now){if(start==null)start=now;phase=now*0.00002;
  if(running){progress=ease(Math.min(1,(now-start)/DUR));if(progress>=1)running=false;}
  const revealPx=P.l+progress*P.w;cx.clearRect(0,0,W,H);heat();grid();
  const pts=buildTrace(revealPx,phase);drawEnvelope(pts);drawRifts(revealPx,now);drawTrace(pts);
  if(running)drawPen(revealPx,pts);else drawHover();requestAnimationFrame(frame);}
function replay(){EV.forEach(e=>e._ign=null);start=null;progress=0;running=true;}
const tip=document.getElementById('tip');
cv.addEventListener('mousemove',ev=>{const r=cv.getBoundingClientRect();const px=ev.clientX-r.left;
  if(px<P.l||px>P.r||running){hoverPx=null;tip.style.opacity=0;return;}hoverPx=px;const i=Math.round(iAt(px));const k=AXIS[Math.max(0,Math.min(N-1,i))];
  tip.style.opacity=1;tip.style.left=px+'px';tip.style.top=P.cy+'px';tip.innerHTML=`<b>${fmt(CH[channel][i]||0)}</b> <span>${UNIT[channel]} · ${k}</span>`;});
cv.addEventListener('mouseleave',()=>{hoverPx=null;tip.style.opacity=0;});
document.getElementById('rec').onclick=replay;
document.querySelectorAll('#chan button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#chan button').forEach(x=>x.classList.toggle('on',x===b));setChannel(b.dataset.c);replay();});
addEventListener('resize',layout);
document.fonts.ready.then(()=>{layout();requestAnimationFrame(frame);
  setTimeout(()=>document.getElementById('boot').classList.add('off'),650);
  setTimeout(()=>{const b=document.getElementById('boot');b&&b.remove();},2000);});
setTimeout(()=>{if(start==null){layout();requestAnimationFrame(frame);document.getElementById('boot').classList.add('off');}},1400);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
