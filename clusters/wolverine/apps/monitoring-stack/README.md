# Monitoring Stack

Backend polls the Kubernetes API (nodes, pods, deployments, Flux Kustomizations/HelmReleases/GitRepositories). Frontend displays the data via the backend API.

## CI/CD – Auto build and push to Docker Hub

On every **git push to `main`** (when `monitoring-backend/`, `monitoring-frontend/`, or the workflow file change), GitHub Actions builds both images and pushes them to Docker Hub.

**One-time setup in your GitHub repo:**

1. **Settings → Secrets and variables → Actions**
2. Add **Repository secrets**:
   - `DOCKERHUB_USERNAME` – your Docker Hub username (e.g. `prajwalnutant`)
   - `DOCKERHUB_TOKEN` – a Docker Hub [Access Token](https://hub.docker.com/settings/security) (create with “Read, Write, Delete” for repos)

Workflow file: [`.github/workflows/docker-build-push.yml`](../../../../.github/workflows/docker-build-push.yml).  
Images are tagged as `DOCKERHUB_USERNAME/monitoring-backend:latest` and `…/monitoring-frontend:latest`, plus a short git SHA tag (e.g. `…:abc1234`). After a successful run, point your cluster at `docker.io/DOCKERHUB_USERNAME/monitoring-backend:latest` (and frontend) and redeploy.

## Fix ImagePullBackOff / ErrImagePull

The cluster tries to pull `monitoring-backend:latest` and `monitoring-frontend:latest` from the default registry; those images only exist after you build (and optionally push) them.

**Option A – Remote cluster (e.g. wolverine)**  
Push images to a container registry, then point Kustomize at them:

```bash
# From repo root: build and tag for your registry
export REGISTRY=ghcr.io/YOUR_USER   # or docker.io/youruser, etc.
docker build -t $REGISTRY/monitoring-backend:latest ./monitoring-backend
docker build -t $REGISTRY/monitoring-frontend:latest ./monitoring-frontend
docker push $REGISTRY/monitoring-backend:latest
docker push $REGISTRY/monitoring-frontend:latest

# Point the stack at your registry
cd clusters/wolverine/apps/monitoring-stack
kustomize edit set image monitoring-backend:latest=$REGISTRY/monitoring-backend:latest
kustomize edit set image monitoring-frontend:latest=$REGISTRY/monitoring-frontend:latest
```

Then apply (or let Flux reconcile).

**Option B – Local cluster (kind / minikube)**  
Build and load images into the cluster so it doesn’t pull:

```bash
# kind
kind load docker-image monitoring-backend:latest --name YOUR_CLUSTER
kind load docker-image monitoring-frontend:latest --name YOUR_CLUSTER

# minikube
eval $(minikube docker-env)
docker build -t monitoring-backend:latest ./monitoring-backend
docker build -t monitoring-frontend:latest ./monitoring-frontend
```

Then apply. With `imagePullPolicy: IfNotPresent`, the cluster will use the loaded image.

## Build images (for local/minikube)

From the repo root:

```bash
docker build -t monitoring-backend:latest ./monitoring-backend
docker build -t monitoring-frontend:latest ./monitoring-frontend
```

For minikube (use the daemon’s images):

```bash
eval $(minikube docker-env)
docker build -t monitoring-backend:latest ./monitoring-backend
docker build -t monitoring-frontend:latest ./monitoring-frontend
```

## Deploy

Apply the Kustomization (from repo root or this directory):

```bash
kubectl apply -k clusters/wolverine/apps/monitoring-stack
```

## Access

Open the **frontend** URL (e.g. `http://<frontend-LoadBalancer-IP>/`). The frontend nginx proxies `/api` to the backend inside the cluster, so one URL is enough. With Ingress, use your configured host (e.g. `http://monitoring.local`). The frontend image uses an entrypoint that injects the cluster DNS resolver from `/etc/resolv.conf` into nginx so `/api` requests resolve the backend correctly (avoids 502).

## Troubleshooting (“Still same” / no data)

1. **Check pods** – Backend must be Running for the UI to show data:
   ```bash
   kubectl get pods -n apps
   ```
   If `monitoring-backend` is `ImagePullBackOff` or `CrashLoopBackOff`, fix the image (push to registry and `kustomize edit set image`), then redeploy.

2. **Check API through the frontend** – From your machine, using the frontend LoadBalancer IP:
   ```bash
   curl -s http://<frontend-IP>/api/health
   ```
   - **JSON** (`{"status":"healthy",...}`) → proxy and backend work; if the browser still shows “Failed to reach API”, try a hard refresh or another browser.
   - **502 Bad Gateway** → backend pod not running or not ready. Run `kubectl get pods -n apps` and `kubectl logs deployment/monitoring-backend -n apps`. Ensure the backend image is built/pushed and the backend pod is Running. If the backend is Running but 502 persists, the frontend nginx may be using a wrong cluster DNS IP; see the comment in `monitoring-frontend/nginx.conf` (resolver) and set your cluster’s kube-dns IP if needed.
   - **404** → frontend image may be old (no proxy); rebuild and push the frontend image, then `kubectl rollout restart deployment/monitoring-frontend -n apps`.

3. **Restart frontend after image change**:
   ```bash
   kubectl rollout restart deployment/monitoring-frontend -n apps
   ```

## Backend API (same host under `/api`)

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Cluster API health |
| `GET /api/namespaces` | Cluster namespaces (for UI filter) |
| `GET /api/nodes` | Nodes and status |
| `GET /api/pods` | Pods (optional `?namespace=...`) |
| `GET /api/services` | Services (optional `?namespace=...`) |
| `GET /api/deployments` | Deployments (optional `?namespace=...`) |
| `GET /api/flux/kustomizations` | Flux Kustomizations |
| `GET /api/flux/helmreleases` | Flux HelmReleases |
| `GET /api/flux/gitrepositories` | Flux GitRepositories |
| `GET /api/workloads` | Aggregated summary |
| `GET /api/docs` | OpenAPI (Swagger) UI |

## RBAC

The `monitoring-sa` ServiceAccount has read-only access to nodes, pods, services, deployments, and (if present) Flux CRDs. Backend runs with this SA.
