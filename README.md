# DemoApp

Application de démonstration **Python / Flask** servie par **uvicorn**, affichant sur sa page
d'accueil trois cartes : l'heure locale avec sa timezone, le hostname et l'IP du pod, et un
compteur de vues persisté dans **Redis** (sans authentification).

## Stack

| Élément | Détail |
|---|---|
| App | Flask 3, wrappée en ASGI via `asgiref.wsgi.WsgiToAsgi` |
| Serveur | uvicorn (`app:asgi_app`) |
| Stockage | Redis (`INCR` sur la clé `demoapp:views`) |
| Conteneur | `python:3.12-slim`, exécution non-root (uid 10001), rootfs read-only |
| CI | GitHub Actions → build + push sur `ghcr.io/ops-nc/demoapp` |
| Déploiement | Kustomize (base + overlays `dev` / `prod`), Gateway API |

> Flask est un framework WSGI : uvicorn étant un serveur ASGI, l'app est exposée via
> `WsgiToAsgi`. C'est le point d'entrée `app:asgi_app`.

## Endpoints

| Route | Description |
|---|---|
| `GET /` | Page HTML — **incrémente** le compteur |
| `GET /api/info` | Mêmes données en JSON — n'incrémente **pas** le compteur |
| `GET /healthz` | Liveness (toujours 200) |
| `GET /readyz` | Readiness — 503 si Redis est injoignable |

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | URL de connexion Redis |
| `COUNTER_KEY` | `demoapp:views` | Clé du compteur |
| `TZ` | `Pacific/Noumea` | Timezone affichée |
| `PORT` | `8000` | Port d'écoute |

## Lancer en local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker run -d --name redis -p 6379:6379 redis:7.4-alpine
REDIS_URL=redis://localhost:6379/0 uvicorn app:asgi_app --host 0.0.0.0 --port 8000
```

## Lancer avec Docker

```bash
docker build -t demoapp:local .
docker network create demoapp && docker run -d --name redis --network demoapp redis:7.4-alpine
docker run --rm --network demoapp -p 8000:8000 \
  -e REDIS_URL=redis://redis:6379/0 demoapp:local
```

## CI/CD

Le workflow `.github/workflows/build.yml` se déclenche sur `dev` et `main` :

1. **Job `test`** — installation des dépendances, `python -m compileall` (syntaxe),
   `ruff check` (lint) et un smoke test d'import du module.
2. **Job `build`** — ne démarre que si `test` réussit. Build et push vers GHCR avec **le nom de
   la branche comme tag** (plus un tag court de SHA) :
   - branche `dev` → `ghcr.io/ops-nc/demoapp:dev`
   - branche `main` → `ghcr.io/ops-nc/demoapp:main`

Sur pull request, seul le job `test` est exécuté (pas de push d'image).

---

# Déploiement ArgoCD

Le répertoire `_k8s/` contient un **base** Kustomize (Deployment, Service, HTTPRoute, Redis) et
deux overlays. Le domaine est défini **une seule fois par overlay** dans un `configMapGenerator`
local (`domain=k8s.lab.ops.nc`), injecté dans le hostname de l'HTTPRoute via un `replacements`
Kustomize. Pour changer de domaine, une seule ligne à modifier par overlay.

```
_k8s/
├── base/                  deployment · service · httproute · redis
├── overlays/dev/          → dev.demoapp.<DOMAIN>, ns demoapp-dev,  image :dev,  1 replica
├── overlays/prod/         → demoapp.<DOMAIN>,     ns demoapp,      image :main, 2 replicas
└── argocd/                Applications ArgoCD prêtes à appliquer
```

L'HTTPRoute est rattachée à la Gateway existante `main-gateway`
(ns `envoy-gateway-system`, listener `https`).

## Vérifier le rendu avant de déployer

```bash
kubectl kustomize _k8s/overlays/dev   | grep -A1 hostnames   # dev.demoapp.k8s.lab.ops.nc
kubectl kustomize _k8s/overlays/prod  | grep -A1 hostnames   # demoapp.k8s.lab.ops.nc
```

## Déclarer les deux Applications

Les manifests fournis pointent chacun vers une branche et un overlay différents :

| Application | Branche suivie | Path | Namespace | URL |
|---|---|---|---|---|
| `demoapp-dev` | `dev` | `_k8s/overlays/dev` | `demoapp-dev` | https://dev.demoapp.k8s.lab.ops.nc |
| `demoapp` | `main` | `_k8s/overlays/prod` | `demoapp` | https://demoapp.k8s.lab.ops.nc |

```bash
kubectl apply -f _k8s/argocd/application-dev.yaml
kubectl apply -f _k8s/argocd/application-prod.yaml
```

Les deux Applications sont en `automated` (prune + selfHeal) avec `CreateNamespace=true`.

## Cycle de déploiement

1. Push sur `dev` → CI publie `ghcr.io/ops-nc/demoapp:dev` → ArgoCD resynchronise `demoapp-dev`.
2. Merge `dev` → `main` → CI publie `:main` → ArgoCD resynchronise `demoapp`.

Le tag d'image étant fixe par branche (`imagePullPolicy: Always`), forcer le redéploiement après
un nouveau build se fait via :

```bash
kubectl rollout restart deploy/demoapp -n demoapp-dev
# ou, côté ArgoCD
argocd app actions run demoapp-dev restart --kind Deployment
```

> Pour un suivi automatique des nouvelles images, brancher **ArgoCD Image Updater** ou passer la
> CI en tag immuable (SHA) avec commit du tag dans l'overlay.

## Notes

- Le Redis déployé utilise un `emptyDir` : le compteur repart à zéro si le pod redémarre.
  Passer sur un PVC ou un Redis managé pour de la persistance réelle.
- Les namespaces `demoapp` et `demoapp-dev` sont créés par les overlays eux-mêmes.
