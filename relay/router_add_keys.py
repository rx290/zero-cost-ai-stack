"""Add provider keys to the FreeLLMAPI router, one provider at a time.

    python3 ~/relay/router_add_keys.py            # all providers below
    python3 ~/relay/router_add_keys.py xkiro google   # only these

For each provider it opens the key page in your browser, you create a key and
paste it (hidden), the key is TESTED against the provider directly, and only
a working key is added to the router. Enter skips a provider. Keys are never
printed or written anywhere except the router (encrypted).
"""
import getpass, json, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

ROUTER = "http://127.0.0.1:3001/api"
TOKEN_FILE = Path.home() / ".config/freellmapi-dashboard-token"

# platform: (key page, test url or None, auth style)
PROVIDERS = {
    "xkiro":       ("https://xkiro.com", "https://api.xkiro.com/v1/models", "bearer"),
    "google":      ("https://aistudio.google.com/apikey",
                    "https://generativelanguage.googleapis.com/v1beta/models?key={k}", "query"),
    "huggingface": ("https://huggingface.co/settings/tokens", "https://router.huggingface.co/v1/models", "bearer"),
    "cohere":      ("https://dashboard.cohere.com/api-keys", "https://api.cohere.com/v1/models", "bearer"),
    "cloudflare":  ("https://dash.cloudflare.com/profile/api-tokens",
                    "https://api.cloudflare.com/client/v4/user/tokens/verify", "cloudflare"),
    "zhipu":       ("https://z.ai/manage-apikey/apikey-list", "https://api.z.ai/api/paas/v4/models", "bearer"),
    "modelscope":  ("https://modelscope.cn/my/myaccesstoken", "https://api-inference.modelscope.cn/v1/models", "bearer"),
    "siliconflow": ("https://cloud.siliconflow.com/account/ak", "https://api.siliconflow.com/v1/models", "bearer"),
    "llm7":        ("https://token.llm7.io", "https://api.llm7.io/v1/models", "bearer"),
}


def router(method, path, body=None):
    req = urllib.request.Request(ROUTER + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json",
                                  "Authorization": "Bearer " + TOKEN_FILE.read_text().strip()}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")


def test_key(url, style, key):
    """True = works, False = rejected (401/403/400 invalid), None = could not tell."""
    headers = {"User-Agent": "Mozilla/5.0"}
    if style == "query":
        url = url.format(k=key)
    elif style == "cloudflare":
        headers["Authorization"] = "Bearer " + key.split(":", 1)[1]
    else:
        headers["Authorization"] = "Bearer " + key
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20)
        return True
    except urllib.error.HTTPError as e:
        return False if e.code in (400, 401, 403) else None
    except Exception:
        return None


def login():
    if TOKEN_FILE.exists():
        try:
            router("GET", "/keys")
            return
        except Exception:
            pass
    email = input("Router dashboard email: ").strip()
    pw = getpass.getpass("Router dashboard password (hidden): ")
    req = urllib.request.Request(ROUTER + "/auth/login", json.dumps({"email": email, "password": pw}).encode(),
                                 {"Content-Type": "application/json"})
    TOKEN_FILE.write_text(json.loads(urllib.request.urlopen(req, timeout=15).read())["token"])
    TOKEN_FILE.chmod(0o600)


login()
wanted = sys.argv[1:] or list(PROVIDERS)
added = []
for p in wanted:
    page, url, style = PROVIDERS[p]
    print(f"\n== {p}: opening {page}")
    subprocess.Popen(["xdg-open", page], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        if p == "cloudflare":
            acct = input("  Cloudflare Account ID (right sidebar of the dashboard, Enter to skip): ").strip()
            if not acct:
                break
            tok = getpass.getpass("  Cloudflare API token with 'Workers AI' permission (hidden): ").strip()
            key = f"{acct}:{tok}"
        else:
            key = getpass.getpass(f"  paste {p} key (hidden, Enter to skip): ").strip()
            if not key:
                break
        ok = test_key(url, style, key)
        if ok is False:
            print("  provider REJECTED this key. Paste another, or Enter to skip.")
            continue
        try:
            router("POST", "/keys", {"platform": p, "key": key, "label": "added-1015"})
            print("  added" + ("" if ok else " (provider check inconclusive, added anyway)"))
            added.append(p)
        except urllib.error.HTTPError as e:
            print(f"  router refused: {e.read().decode(errors='replace')[:120]}")
        break

print(f"\nAdded: {', '.join(added) or 'nothing'}")
print("Test: fllm -t fast 'say hi'    Then save everything: bash ~/relay/export_bundle.sh")
