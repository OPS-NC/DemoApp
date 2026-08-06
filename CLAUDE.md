# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A demo web app: Flask served by uvicorn, showing local time + timezone, hostname + IP, and a
Redis-backed view counter. Deployed to Kubernetes behind Envoy Gateway, delivered by ArgoCD.

## Architecture

```
app.py                    Flask app + WsgiToAsgi wrapper (entrypoint: app:asgi_app)
templates/index.html      Single page, inline CSS/JS, no build step
Dockerfile                python:3.12-slim, non-root uid 10001
.github/workflows/        test (compileall + ruff) -> build/push to GHCR -> trivy scan -> deploy
_k8s/base/                deployment · service · httproute · redis
_k8s/overlays/{dev,prod}  namespace, image tag, replicas, hostname patch
_k8s/argocd/              ArgoCD Application per branch
```

**Flask is WSGI, uvicorn is ASGI.** The app is exposed through
`asgiref.wsgi.WsgiToAsgi` as `asgi_app`. Never change the entrypoint to `app:app` — uvicorn
cannot serve it.

## Conventions

- Commits: `[Claude] <type>: <description>` (`fix`, `feat`, `refactor`, `docs`, `chore`, `perf`, `test`).
- Lint config is `ruff.toml` (`E,F,W,I,B,UP,C4`, line length 100). Run `ruff check .` before pushing —
  CI blocks the build on it.
- No new dependencies unless required. Pin exact versions in `requirements.txt`.
- Keep the page a single self-contained HTML file. No framework, no bundler.

## Invariants — do not break these

- `GET /` increments the counter; `GET /api/info` must stay read-only (the front polls it).
- Redis failures must degrade gracefully: `/` renders with `redis KO`, never 500. `/readyz`
  returns 503 so Kubernetes pulls the pod out of rotation.
- Container runs as uid 10001 with `readOnlyRootFilesystem: true`. Any code writing to disk
  breaks the deployment — keep `PYTHONDONTWRITEBYTECODE=1`.
- All config comes from env vars: `REDIS_URL`, `COUNTER_KEY`, `TZ`, `PORT`, `COMMIT`. No
  hardcoded values.
- `_k8s/overlays/*/commit-patch.yaml` is **CI-generated** — do not hand-edit it, the `deploy` job
  overwrites the file on every push.
- The `scan` job (Trivy) gates `deploy`: any fixable `CRITICAL` in the pushed image fails the job,
  so the `COMMIT` bump never lands and ArgoCD keeps the previous revision. The image is still
  pushed to GHCR — the gate blocks the rollout, not the build. Fix the CVE (base image or
  `requirements.txt`) rather than loosening the severity filter.

## Branches and deployment

| Branch | Image tag | Overlay | Namespace | Hostname |
|---|---|---|---|---|
| `dev` | `:dev` | `_k8s/overlays/dev` | `demoapp-dev` | `demoapp-dev.k8s.lab.ops.nc` |
| `main` | `:main` | `_k8s/overlays/prod` | `demoapp` | `demoapp.k8s.lab.ops.nc` |

Work on `dev`. Reach `main` through a PR. Hostnames are written literally in each overlay's
`httproute-patch.yaml` — one line to change per environment.

The HTTPRoute attaches to the pre-existing Gateway `main-gateway` in namespace
`envoy-gateway-system`, listener `https`. Do not recreate that Gateway here.

## Verifying changes

```bash
ruff check .                                   # lint (same as CI)
python -m compileall -q .                      # syntax (same as CI)
kubectl kustomize _k8s/overlays/dev            # render manifests, expect no warnings
docker build -t demoapp:local . && docker run --rm --read-only \
  --network demoapp -e REDIS_URL=redis://redis:6379/0 -p 8000:8000 demoapp:local
```

Never mark k8s or Docker work done without rendering the manifests or running the container.

## Known limitations

- Redis uses an `emptyDir`: the counter resets when the pod restarts.
- Image tags are mutable per branch. Redeployment is triggered by the `deploy` job bumping the
  `COMMIT` env var, which changes the pod template and makes ArgoCD roll the pods. That commit
  must never retrigger CI: it relies on `GITHUB_TOKEN` pushes not firing workflows, plus
  `[skip ci]` and `paths-ignore`. Keep all three if you touch the workflow.

## Gotcha: commit messages

GitHub scans the whole commit message — subject *and* body — for the skip marker (`skip ci`
in square brackets). Never write that literal sequence in a commit message when describing
this mechanism, or your push silently produces no CI run. Referring to it inside files
(README, workflow YAML) is safe; only the commit message matters.
- `/api/info` returns the server date in the server locale; the browser reformats it in French.
