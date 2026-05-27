import os
import sys
import json
from urllib import request

API_BASE = "https://api.render.com/v1"


def get_api_key():
    key = os.environ.get("RENDER_API_KEY")
    if not key:
        print("ERROR: RENDER_API_KEY environment variable not set.")
        sys.exit(1)
    return key


def api_get(path, api_key):
    url = API_BASE + path
    req = request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with request.urlopen(req) as r:
            return json.load(r)
    except Exception as e:
        # Try to show HTTP error body when available
        try:
            import urllib.error
            if isinstance(e, urllib.error.HTTPError):
                body = e.read().decode(errors="replace")
                print(f"HTTPError {e.code}: {e.reason}")
                print("Response body:", body)
        except Exception:
            pass
        raise


def api_post(path, data, api_key):
    url = API_BASE + path
    payload = json.dumps(data).encode("utf-8")
    req = request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    print("Request payload:", json.dumps(data, ensure_ascii=False))
    try:
        with request.urlopen(req) as r:
            return json.load(r)
    except Exception as e:
        try:
            import urllib.error
            if isinstance(e, urllib.error.HTTPError):
                body = e.read().decode(errors="replace")
                print(f"HTTPError {e.code}: {e.reason}")
                print("Response body:", body)
        except Exception:
            pass
        raise


def main():
    api_key = get_api_key()

    owners = api_get("/owners", api_key)
    if not owners or len(owners) == 0:
        print("ERROR: no owners/workspaces found for this API key")
        sys.exit(1)
    owner_id = owners[0].get("id")

    repo = os.environ.get("RENDER_REPO")
    if not repo:
        if len(sys.argv) > 1:
            repo = sys.argv[1]
        else:
            try:
                import subprocess
                out = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
                repo = out
            except Exception:
                print("ERROR: repo not provided. Set RENDER_REPO env or pass repo URL as first arg.")
                sys.exit(1)

    payload = {
        "type": "web_service",
        "name": os.environ.get("RENDER_SERVICE_NAME", "wendangzhushou"),
        "ownerId": owner_id,
        "repo": repo,
        "branch": os.environ.get("RENDER_BRANCH", "main"),
        "buildCommand": os.environ.get("RENDER_BUILD_CMD", "pip install -r requirements.txt"),
        "startCommand": os.environ.get("RENDER_START_CMD", "python -m app.server --host 0.0.0.0"),
        "env": os.environ.get("RENDER_ENV", "python"),
        "plan": os.environ.get("RENDER_PLAN", "free"),
        "healthCheckPath": os.environ.get("RENDER_HEALTH_PATH", "/health"),
    }

    print("Creating service on Render with name:", payload["name"])
    resp = api_post("/services", payload, api_key)

    print(json.dumps(resp, indent=2, ensure_ascii=False))
    svc = resp.get("service") or resp
    url = svc.get("dashboardUrl") or svc.get("url")
    if url:
        print("Service created. Dashboard / URL:", url)
    else:
        print("Service created, response shown above.")


if __name__ == "__main__":
    main()
