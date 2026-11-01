"""Print the ChatGPT-plan Codex limits from its newest session log: used % and reset time per window."""
import json, time
from pathlib import Path


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def latest():
    logs = sorted((Path.home() / ".codex/sessions").rglob("*.jsonl"), key=lambda q: q.stat().st_mtime)
    for log in reversed(logs[-30:]):
        lines = [l for l in log.read_text(errors="ignore").splitlines() if '"rate_limits"' in l and '"used_percent"' in l]
        if lines:
            return log, next(v for k, v in _walk(json.loads(lines[-1])) if k == "rate_limits")
    return None, None


if __name__ == "__main__":
    log, rl = latest()
    if not rl:
        print("no Codex rate-limit record yet")
    else:
        print(f"as of {time.ctime(log.stat().st_mtime)} (plan {rl.get('plan_type')})")
        for w, name in (("primary", "5 h"), ("secondary", "week")):
            x = rl.get(w) or {}
            reset = x.get("resets_at") or 0
            note = " (already reset)" if reset and reset < time.time() else ""
            print(f"  {name:5} {x.get('used_percent', '?'):>5}% used, resets {time.ctime(reset)}{note}")
