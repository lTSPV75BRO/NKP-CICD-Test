# NKP-CICD-Test

Kubernetes cluster monitoring stack and infrastructure for the **wolverine** cluster. A FastAPI backend polls the Kubernetes API; a static frontend displays nodes, pods, deployments, services, PV/PVC/StorageClasses, StatefulSets, DaemonSets, ConfigMaps, Secrets, Jobs, CronJobs, and (optionally) Flux CD workloads. Optional log shipping to Kafka via Fluent Bit.

## Repository structure

| Path | Description |
|------|-------------|
| **`monitoring-backend/`** | FastAPI app – polls K8s API, exposes REST endpoints, optional Kafka logging |
| **`monitoring-frontend/`** | Static HTML/JS UI + Nginx – proxies `/api` to backend, shows cluster data |
| **`clusters/wolverine/apps/`** | Apps Kustomization – monitoring stack (Deployment, Service, RBAC, Ingress, Fluent Bit config) |
| **`clusters/wolverine/apps/monitoring-stack/`** | Monitoring manifests + [detailed README](clusters/wolverine/apps/monitoring-stack/README.md) |
| **`clusters/wolverine/infrastructure/`** | Infra Kustomization – namespace, Strimzi operator, Kafka cluster + topic |
| **`.github/workflows/`** | GitHub Actions – build and push Docker images to Docker Hub on push to `main` |

## Quick start

1. **Deploy infrastructure** (Kafka namespace, Strimzi operator, Kafka cluster + topic):
   ```bash
   kubectl apply -k clusters/wolverine/infrastructure/
   ```

2. **Deploy the monitoring stack** (backend, frontend, RBAC, optional Ingress):
   ```bash
   kubectl apply -k clusters/wolverine/apps/monitoring-stack
   ```

3. **Open the frontend** at the LoadBalancer IP or your Ingress host (e.g. `http://monitoring.local`). Use the namespace filter and refresh controls as needed.

Images are expected from your registry. To point the stack at your images (e.g. after CI/CD build):

```bash
cd clusters/wolverine/apps/monitoring-stack
kustomize edit set image monitoring-backend:latest=docker.io/YOUR_USER/monitoring-backend:latest
kustomize edit set image monitoring-frontend:latest=docker.io/YOUR_USER/monitoring-frontend:latest
```

## CI/CD (Docker Hub)

On push to `main`, GitHub Actions builds and pushes `monitoring-backend` and `monitoring-frontend` to Docker Hub. Configure repository secrets:

- **`DOCKERHUB_USERNAME`** – your Docker Hub username  
- **`DOCKERHUB_TOKEN`** – a Docker Hub **Access Token** (not your password)

See [monitoring-stack README – CI/CD](clusters/wolverine/apps/monitoring-stack/README.md) for details.

## What the UI shows

- **Nodes** – status, roles  
- **Pods** – phase, ready, node (namespace filter)  
- **Deployments / StatefulSets / DaemonSets** – ready vs desired  
- **Services / Ingresses** – types, IPs, hosts  
- **PV / PVC / StorageClasses** – status, capacity, provisioner  
- **ConfigMaps / Secrets** – names and namespaces only (no data)  
- **Jobs / CronJobs** – status, schedule, last run  
- **Flux** – Kustomizations, HelmReleases, GitRepositories (if installed)

API docs (Swagger) are at `/api/docs` when using the frontend URL.

## Optional: logs to Kafka

The backend and frontend (Fluent Bit sidecar) can send logs to a Kafka topic. Deploy Kafka first (`kubectl apply -k clusters/wolverine/infrastructure/`), then switch the frontend Fluent Bit config to the Kafka variant and restart. See [Logs to Kafka](clusters/wolverine/apps/monitoring-stack/README.md#logs-to-kafka) in the monitoring-stack README.

## License

MIT – see [LICENSE](LICENSE).
