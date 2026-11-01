"""Claude Code status line: saves the session JSON (incl. rate_limits) for the relay, prints a short line."""
import json, pathlib, sys

raw = sys.stdin.read()
out = pathlib.Path.home() / ".cache/relay"
out.mkdir(parents=True, exist_ok=True)
(out / "claude_status.json").write_text(raw)
d = json.loads(raw or "{}")
r = d.get("rate_limits") or {}


def used(k):
    v = (r.get(k) or {}).get("used_percentage")
    return "?" if v is None else round(v)


def reset(k):
    import time
    t = (r.get(k) or {}).get("resets_at")
    return time.strftime(" (resets %a %H:%M)", time.localtime(t)) if isinstance(t, (int, float)) else ""


print(f"{(d.get('model') or {}).get('display_name', '')} | 5h {used('five_hour')}%{reset('five_hour')} | "
      f"week {used('seven_day')}%{reset('seven_day')}")
