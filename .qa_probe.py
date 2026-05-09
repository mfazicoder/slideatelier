"""SCRATCH QA probe — runs route smoke tests and prints results."""
from fastapi.testclient import TestClient
from slideatelier.web.app import app

c = TestClient(app)


def probe(method, path, **kw):
    if method == "GET":
        r = c.get(path, **kw)
    elif method == "POST":
        r = c.post(path, **kw)
    body_preview = (r.text[:80].replace("\n", " ")) if r.text else ""
    print(f"  {method:4} {path:55} -> {r.status_code}  {body_preview!r}")
    return r


print("\n=== QA smoke routes ===")
probe("GET", "/")
probe("GET", "/workflow")
probe("GET", "/library")
probe("GET", "/design-system")

print("\n=== Storyboard form ===")
r = probe("GET", "/workflow/storyboard")  # Only POST registered

print("\n=== DIFC deck (real data) ===")
r = probe("GET", "/workflow/wireframe/f0e1fdf77d19")
if r.status_code == 200:
    for marker in ["data-slide-preview", "Continue to Hi-fi", "Hi-fi", "Publish"]:
        print(f"     contains '{marker}': {marker in r.text}")
probe("GET", "/workflow/storyboard/f0e1fdf77d19")
probe("GET", "/workflow/hi-fi/f0e1fdf77d19")

print("\n=== Bogus IDs ===")
probe("GET", "/workflow/wireframe/badjobid")
probe("GET", "/workflow/storyboard/notreal")
probe("GET", "/workflow/hi-fi/notreal")

print("\n=== Web/published ===")
probe("GET", "/web/R2rmnAXd")
probe("GET", "/web/totallybogusslug")

print("\n=== APIs ===")
probe("GET", "/api/health")
probe("GET", "/api/ready")
probe("GET", "/api/templates")
probe("GET", "/api/library/categories")
probe("GET", "/api/library/assets?limit=3")
probe("GET", "/api/jobs/nope")
probe("POST", "/api/generate", data={"content": ""})

print("\n=== Sample brief ===")
probe("GET", "/workflow/sample/uae-setup")
probe("GET", "/workflow/sample/nonexistent")

print("\n=== Critique routes ===")
probe("GET", "/workflow/wireframe/f0e1fdf77d19/critique")

print("\n=== Web deck routes ===")
probe("POST", "/workflow/wireframe/f0e1fdf77d19/publish")
