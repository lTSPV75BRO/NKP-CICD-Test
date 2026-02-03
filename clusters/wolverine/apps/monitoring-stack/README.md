# Monitoring Stack

Backend polls the Kubernetes API (nodes, pods, deployments, Flux Kustomizations/HelmReleases/GitRepositories). Frontend displays the data via the backend API.

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
