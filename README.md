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
| Déploiement | Kustomize (base + overlays `dev` / `prod`), Gateway API (Envoy Gateway) |

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
| `COMMIT` | `unknown` | SHA court affiché sous l'IP ; réécrit par la CI à chaque déploiement |

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
3. **Job `deploy`** — déclenche le redéploiement ArgoCD (voir ci-dessous).

Sur pull request, seuls les jobs `test` s'exécutent (ni push d'image, ni commit).

---

# Déploiement ArgoCD

Le répertoire `_k8s/` contient un **base** Kustomize (Deployment, Service, HTTPRoute, Redis) et
deux overlays. Chaque overlay surcharge le hostname de l'HTTPRoute via un patch
(`httproute-patch.yaml`) : une ligne à modifier pour changer de domaine.

```
_k8s/
├── base/                  deployment · service · httproute · redis
├── overlays/dev/          → demoapp-dev.k8s.lab.ops.nc, ns demoapp-dev, image :dev,  1 replica
├── overlays/prod/         → demoapp.k8s.lab.ops.nc,     ns demoapp,     image :main, 2 replicas
└── argocd/                Applications ArgoCD prêtes à appliquer
```

L'HTTPRoute est rattachée à la Gateway existante `main-gateway`
(ns `envoy-gateway-system`, listener `https`).

## Vérifier le rendu avant de déployer

```bash
kubectl kustomize _k8s/overlays/dev   | grep -A1 hostnames   # demoapp-dev.k8s.lab.ops.nc
kubectl kustomize _k8s/overlays/prod  | grep -A1 hostnames   # demoapp.k8s.lab.ops.nc
```

## Déclarer les deux Applications

Les manifests fournis pointent chacun vers une branche et un overlay différents :

| Application | Branche suivie | Path | Namespace | URL |
|---|---|---|---|---|
| `demoapp-dev` | `dev` | `_k8s/overlays/dev` | `demoapp-dev` | https://demoapp-dev.k8s.lab.ops.nc |
| `demoapp` | `main` | `_k8s/overlays/prod` | `demoapp` | https://demoapp.k8s.lab.ops.nc |

```bash
kubectl apply -f _k8s/argocd/application-dev.yaml
kubectl apply -f _k8s/argocd/application-prod.yaml
```

Les deux Applications sont en `automated` (prune + selfHeal) avec `CreateNamespace=true`.

## Redéploiement automatique (job `deploy`)

Le tag d'image est fixe par branche : un nouveau push ne modifie donc aucun manifest, et ArgoCD
n'a rien à resynchroniser. Le job `deploy` résout ce problème **sans changer de tag** — il réécrit
l'ENV var `COMMIT` du Deployment avec le SHA court dans
`_k8s/overlays/<env>/commit-patch.yaml`, puis commite sur la branche courante.

Modifier une ENV var change le pod template : ArgoCD voit la dérive, sync, et Kubernetes fait un
rolling update. Comme `imagePullPolicy: Always`, les nouveaux pods tirent l'image fraîche du
même tag. Le SHA déployé est affiché en petit sous l'IP sur la page.

```
push sur dev → test → build & push :dev → commit "COMMIT=<sha>" → ArgoCD sync → rollout
```

**Le commit de la CI ne redéclenche aucun workflow**, via trois protections :

1. Un push authentifié avec le `GITHUB_TOKEN` ne déclenche pas de workflow (garantie GitHub).
2. Le message de commit porte `[skip ci]`.
3. `on.push.paths-ignore` exclut `_k8s/overlays/*/commit-patch.yaml`.

Mapping branche → overlay patché : `dev` → `overlays/dev`, `main` → `overlays/prod`.

Redéploiement manuel si besoin :

```bash
kubectl rollout restart deploy/demoapp -n demoapp-dev
```

## Notes

- Le Redis déployé utilise un `emptyDir` : le compteur repart à zéro si le pod redémarre.
  Passer sur un PVC ou un Redis managé pour de la persistance réelle.
- Les namespaces `demoapp` et `demoapp-dev` sont créés par les overlays eux-mêmes.
