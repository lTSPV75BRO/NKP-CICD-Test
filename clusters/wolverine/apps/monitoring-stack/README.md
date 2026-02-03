# Monitoring Stack

Backend polls the Kubernetes API (nodes, pods, deployments, Flux Kustomizations/HelmReleases/GitRepositories). Frontend displays the data via the backend API.

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

- **With Ingress**: Use one host (e.g. `monitoring.local`) so `/` is the frontend and `/api` is the backend. Add `monitoring.local` to `/etc/hosts` pointing at the Ingress controller’s IP. Open `http://monitoring.local`.
- **Without Ingress**: Frontend and backend get separate LoadBalancer IPs. Open the frontend IP in the browser; for API calls to work, either use Ingress or set `window.API_BASE = 'http://<backend-ip>:8080'` (e.g. via a small config page or build-time env).

## Backend API (same host under `/api`)

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Cluster API health |
| `GET /api/nodes` | Nodes and status |
| `GET /api/pods` | Pods (optional `?namespace=...`) |
| `GET /api/deployments` | Deployments (optional `?namespace=...`) |
| `GET /api/flux/kustomizations` | Flux Kustomizations |
| `GET /api/flux/helmreleases` | Flux HelmReleases |
| `GET /api/flux/gitrepositories` | Flux GitRepositories |
| `GET /api/workloads` | Aggregated summary |

## RBAC

The `monitoring-sa` ServiceAccount has read-only access to nodes, pods, services, deployments, and (if present) Flux CRDs. Backend runs with this SA.
