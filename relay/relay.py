#!/usr/bin/env python3
"""Relay: an unattended task runner that keeps coding work going on free LLMs (FreeLLMAPI router first,
local Ollama models when the router is down). Claude and Codex are never called from here.

    python3 relay.py run [lane]   # loop forever (queue, then sleep 5 min); lane X uses queue-X/, default main
    python3 relay.py once     # one pass over the queue
    python3 relay.py status   # queue/done/stuck counts + Claude usage from the status line

Task file (queue/NN-name.md): `key: value` lines, a blank line, then the task text.
    repo: /abs/path       (required)
    verify: shell command (required; exit 0 = done)
    size: big|small       (default big; small tasks may fall back to local models)
    attempts: N           (default 4, total agent runs for this task)
    timeout: S            (default 1800, seconds per agent run)
    paid: allowed|first   (optional: ChatGPT-plan Codex as last resort / first; skipped above 80% of its window)
"""
import fcntl, json, os, re, socket, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

RELAY = Path(os.environ.get("RELAY_HOME", Path.home() / "relay"))
QUEUE, DONE, STUCK, LOGS = (RELAY / d for d in ("queue", "done", "stuck", "logs"))
LANE = "main"


def set_lane(lane):
    """Parallel lanes: each has its own queue + lock (lane 'main' uses queue/). Give lanes DIFFERENT repos so two agents
    never check out branches in the same working tree."""
    global QUEUE, LANE
    LANE = lane
    QUEUE = RELAY / ("queue" if lane == "main" else f"queue-{lane}")
STATUS = Path.home() / ".cache/relay/claude_status.json"
KEY_FILE = Path.home() / ".config/freellmapi-key"
ROUTER_MODELS = ["gemini-3-flash-preview", "devstral-2", "qwen3.8-27b", "minimax-m2.5", "mistral-large-3"]  # edit files via opencode (probe 10/13); gemini fastest
CODEX_MODELS = ["codex/gpt-oss-120b"]  # Codex CLI on the free router (~/.codex/config.toml default), never the ChatGPT plan
LOCAL_MODELS = ["qwen3a3b/qwen3-coder:a3b", "ollama/qwen3:4b", "ollama/qwen2.5-coder:3b"]  # first one needs start-qwen3-a3b.sh running
if os.environ.get("RELAY_BIG_GPU"):  # 12 GB card: bigger local models
    LOCAL_MODELS = ["qwen3a3b/qwen3-coder:a3b", "ollama/gpt-oss:20b", "ollama/qwen2.5-coder:7b"]
RATE_LIMIT = re.compile(r"429|Too Many Requests|rate.limit|exhausted|on cooldown", re.I)
RULES = ("\n\nWhen you think you are done, run this check and keep fixing until it passes: {verify}\n"
         "Rules: work only inside this repo, never git push, never delete files outside the repo, "
         "do not edit tests to make them pass unless the task says so.")


STATE = RELAY / "state"


def write_state(**fields):
    """Live progress for `watch.py` and the dashboard: one JSON per lane, rewritten at every step."""
    STATE.mkdir(exist_ok=True)
    path = STATE / f"{LANE}.json"
    try:
        cur = json.loads(path.read_text())
    except Exception:
        cur = {}
    cur.update(fields, lane=LANE, updated=time.time(), queued=sorted(q.name for q in QUEUE.glob("*.md")))
    path.write_text(json.dumps(cur))


def record_history(task, result, model, started):
    with (STATE / "history.jsonl").open("a") as f:
        f.write(json.dumps({"lane": LANE, "task": task, "result": result, "model": model,
                            "started": started, "finished": time.time()}) + "\n")


def now():
    return datetime.now().isoformat(timespec="seconds")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)


def router_up():
    try:
        req = urllib.request.Request("http://127.0.0.1:3001/v1/models",
                                     headers={"Authorization": "Bearer " + KEY_FILE.read_text().strip()})
        return urllib.request.urlopen(req, timeout=5).status == 200
    except Exception:
        return False


def local_up(model):
    """A local model is usable when its server answers: Ollama on 11434, the 30B-A3B llama-server on 8087."""
    url = "http://127.0.0.1:8087/v1/models" if model.startswith("qwen3a3b/") else "http://127.0.0.1:11434/api/tags"
    try:
        return urllib.request.urlopen(url, timeout=3).status == 200
    except Exception:
        return False


CODEX_PAID_CEILING = 80.0                 # percent of the ChatGPT plan's 5 h or weekly window; above it, no paid runs


def codex_paid_ok():
    """True when the ChatGPT-plan Codex windows (from its newest session log) are both under CODEX_PAID_CEILING."""
    logs = sorted((Path.home() / ".codex/sessions").rglob("*.jsonl"), key=lambda q: q.stat().st_mtime)
    for log in reversed(logs[-20:]):
        last = None
        for line in log.read_text(errors="ignore").splitlines():
            if '"rate_limits"' in line and '"used_percent"' in line:
                last = line
        if last:
            try:
                limits = next(v for k, v in _walk(json.loads(last)) if k == "rate_limits")
                used = [limits[w]["used_percent"] for w in ("primary", "secondary") if limits.get(w)]
                return bool(used) and max(used) < CODEX_PAID_CEILING
            except Exception:
                continue
    return True                              # no record yet: the plan has not been used


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def parse(path):
    head, _, body = path.read_text().partition("\n\n")
    meta = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, body.strip()


def finish(path, folder, lines):
    (folder / path.name).write_text(path.read_text().rstrip() + "\n\n" + "\n".join(lines) + "\n")
    path.unlink()


def verify(repo, cmd):
    try:
        r = subprocess.run(cmd, shell=True, cwd=repo, capture_output=True, text=True, timeout=3600)
        return r.returncode == 0, (r.stdout + r.stderr).splitlines()[-40:]
    except subprocess.TimeoutExpired:
        return False, ["verify timed out after 3600 s"]


# relay model name -> the router's upstream ids (penalty inspector rows); a model on cooldown everywhere is skipped
UPSTREAM = {"devstral-2": ["devstral-medium", "devstral-2"], "mistral-large-3": ["mistral-large"],
            "codestral": ["codestral"], "gemini-3-flash-preview": ["gemini-3-flash-preview"],
            "qwen3.8-27b": ["qwen3.8-27b"], "minimax-m2.5": ["minimax-m2.5"], "gpt-oss-120b": ["gpt-oss-120b"]}


def cooling_down():
    """Router models whose every route is on cooldown right now (needs ~/.config/freellmapi-dashboard-token)."""
    tok = Path.home() / ".config/freellmapi-dashboard-token"
    if not tok.exists():
        return set()
    try:
        req = urllib.request.Request("http://127.0.0.1:3001/api/fallback/penalty-inspector",
                                     headers={"Authorization": "Bearer " + tok.read_text().strip()})
        rows = json.loads(urllib.request.urlopen(req, timeout=10).read())
        rows = rows if isinstance(rows, list) else rows.get("rows", rows.get("models", []))
    except Exception:
        return set()
    cold = {r.get("modelId", "") for r in rows if any(c.get("expiresInMs", 0) > 60_000 for c in r.get("cooldowns") or [])}
    out = set()
    for name, ups in UPSTREAM.items():
        if any(any(u in c for c in cold) for u in ups):
            out.add(name)
    return out


def workers(size, paid=""):
    """Models in try order: router models while the router is up, local ones when it is down (or as the
    last resort for small tasks)."""
    up = router_up()
    cold = cooling_down()
    usable = [m for m in ROUTER_MODELS if m not in cold] or ROUTER_MODELS
    router = usable if LANE == "main" else usable[1:] + usable[:1]   # lanes start on different models
    order = ([f"freellmapi/{m}" for m in router[:1]] + CODEX_MODELS + [f"freellmapi/{m}" for m in router[1:]]
             if up else [])
    if not up or size == "small":
        order += [m for m in LOCAL_MODELS if local_up(m)]
    if paid == "first":
        order = ["codex-paid"] + order
    elif paid == "allowed":
        order = order + ["codex-paid"]
    return order


def run_task(path):
    meta, text = parse(path)
    repo = Path(meta.get("repo", "")).expanduser()
    if not text or not meta.get("verify") or not repo.is_dir():
        return finish(path, STUCK, ["result: stuck", "reason: needs repo, verify and task text"])
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return finish(path, STUCK, ["result: stuck", "reason: repo has uncommitted changes, commit or stash first"])
    home = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    branch = f"relay/{path.stem}"
    if git(repo, "checkout", branch).returncode and git(repo, "checkout", "-b", branch).returncode:
        return finish(path, STUCK, ["result: stuck", f"reason: cannot check out {branch}"])
    log = LOGS / f"{path.stem}.log"
    started = time.time()
    write_state(task=path.stem, task_started=started, phase="starting", model=None, attempt=0, attempts=0)
    prompt = text + RULES.format(verify=meta["verify"])
    agent_timeout = int(meta.get("timeout", 1800))         # seconds per agent run
    tail, result = [], None
    try:
        models = workers(meta.get("size", "big"), meta.get("paid", "").lower())
        attempts = int(meta.get("attempts", 4))      # real failures allowed; throttled/timed-out tries are free
        ok = False
        real_fails = 0
        for rnd in range(3):                               # a round = every worker once; wait between rounds
            if rnd:
                with log.open("a") as f:
                    f.write(f"\n[every worker throttled or timed out; waiting 600 s before round {rnd + 1}]\n")
                time.sleep(600)
            for model in models:
                if model == "codex-paid" and real_fails < 2 and meta.get("paid", "").lower() != "first":
                    continue                               # owner rule: pay only after 2 genuine free failures
                if model == "codex-paid" and not codex_paid_ok():
                    continue
                mark = log.stat().st_size if log.exists() else 0
                write_state(phase="agent working", model=model, attempt=real_fails + 1, attempts=attempts,
                            attempt_started=time.time(), timeout=agent_timeout, waited=rnd)
                with log.open("a") as f:
                    f.write(f"\n=== {now()} {model} (round {rnd + 1}) ===\n")
                    f.flush()
                    if model == "codex-paid":
                        cmd, env = ["codex", "exec", "-p", "chatgpt", "-s", "workspace-write", "-C", str(repo), prompt], None
                    elif model.startswith("codex/"):
                        cmd = ["codex", "exec", "-m", model.split("/", 1)[1], "-s", "workspace-write", "-C", str(repo), prompt]
                        env = dict(os.environ, FREELLM_API_KEY=KEY_FILE.read_text().strip())
                    else:
                        cmd, env = ["opencode", "run", "--agent", "relay", "-m", model, "--dir", str(repo), prompt], None
                    try:
                        subprocess.run(cmd, cwd=repo, stdout=f, stderr=subprocess.STDOUT, timeout=agent_timeout, env=env)
                    except subprocess.TimeoutExpired:
                        f.write(f"\n[agent timed out after {agent_timeout} s]\n")
                write_state(phase="verifying", verify_started=time.time())
                ok, tail = verify(repo, meta["verify"])
                if ok:
                    result = model
                    break
                chunk = log.read_text(errors="ignore")[mark:]
                if not RATE_LIMIT.search(chunk) and "timed out after" not in chunk:
                    real_fails += 1                        # the agent really finished and the check still failed
                if real_fails >= attempts:
                    break
            if ok or real_fails >= attempts:
                break
        if result:
            if git(repo, "status", "--porcelain").stdout.strip():
                git(repo, "add", "-A")
                git(repo, "commit", "-m", f"relay: {path.stem} ({result})")
            finish(path, DONE, ["result: pass", f"model: {result}", f"branch: {branch}", f"finished: {now()}"])
            record_history(path.stem, "pass", result, started)
        else:
            git(repo, "add", "-A")                       # keep the partial work on the relay branch for review
            git(repo, "commit", "-m", f"relay: {path.stem} (stuck, partial)")
            record_history(path.stem, "stuck", None, started)
            finish(path, STUCK, ["result: stuck", f"branch: {branch}", f"tried: {', '.join(models) or 'none'}", f"real failures: {real_fails}",
                                 f"finished: {now()}", "```", *tail, "```"])
    finally:
        git(repo, "checkout", home)


CODE_MTIME = Path(__file__).stat().st_mtime


def reload_if_changed():
    """Pick up edits to relay.py between tasks (never mid-task): re-exec this lane in place, keeping its lock."""
    if Path(__file__).stat().st_mtime != CODE_MTIME:
        write_state(task=None, phase="reloading new relay code")
        os.execv(sys.executable, [sys.executable, __file__, *sys.argv[1:]])


def run_queue():
    for path in sorted(QUEUE.glob("*.md")):
        reload_if_changed()
        try:
            run_task(path)
        except Exception as error:                       # one bad task never stops the loop
            if path.exists():
                finish(path, STUCK, ["result: stuck", f"reason: {type(error).__name__}: {error}"])


def status():
    lanes = sorted(RELAY.glob("queue*"))
    print(" | ".join(f"{d.name} {len(list(d.glob('*.md')))}" for d in (*lanes, DONE, STUCK)),
          "| router", "up" if router_up() else "DOWN")
    try:
        r = json.loads(STATUS.read_text()).get("rate_limits") or {}
        print(f"Claude used: 5h {r['five_hour']['used_percentage']}%, week {r['seven_day']['used_percentage']}%")
    except Exception:
        print("Claude usage: unknown (status line has not run yet)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    set_lane(sys.argv[2] if len(sys.argv) > 2 else "main")
    for d in (QUEUE, DONE, STUCK, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    if cmd == "status":
        return status()
    if cmd not in ("run", "once"):
        sys.exit(__doc__)
    lock = (RELAY / (".lock" if LANE == "main" else f".lock-{LANE}")).open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"relay lane {LANE} is already running")
    while True:
        run_queue()
        write_state(task=None, phase="idle (queue empty, checking every 5 min)", model=None)
        if cmd == "once":
            return
        time.sleep(300)


if __name__ == "__main__":
    main()
