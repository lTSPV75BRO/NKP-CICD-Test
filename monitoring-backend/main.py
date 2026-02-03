"""
Monitoring Backend - Polls Kubernetes API for nodes, pods, Flux workloads, and health.
Runs in-cluster using the monitoring ServiceAccount.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Try in-cluster config first (when running in K8s), else kubeconfig
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

v1_core = client.CoreV1Api()
v1_apps = client.AppsV1Api()
custom_api = client.CustomObjectsApi()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify we can talk to the API
    try:
        v1_core.list_namespace(limit=1)
    except ApiException as e:
        print(f"Kubernetes API check failed: {e}")
    yield
    # Shutdown
    pass


app = FastAPI(
    title="NKP Monitoring API",
    description="Cluster nodes, pods, Flux workloads, and health",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.get("/api/health")
def health():
    """Overall health and API connectivity."""
    try:
        v1_core.list_namespace(limit=1)
        k8s_ok = True
        message = "OK"
    except ApiException as e:
        k8s_ok = False
        message = str(e.reason) if e.reason else str(e)
    return {
        "status": "healthy" if k8s_ok else "degraded",
        "kubernetes_api": "reachable" if k8s_ok else "unreachable",
        "message": message,
        "timestamp": _now_iso(),
    }


@app.get("/api/nodes")
def list_nodes():
    """List cluster nodes with status and capacity."""
    try:
        ret = v1_core.list_node()
        nodes = []
        for i in ret.items:
            status = "Unknown"
            for c in i.status.conditions or []:
                if c.type == "Ready":
                    status = "Ready" if c.status == "True" else "NotReady"
                    break
            nodes.append({
                "name": i.metadata.name,
                "status": status,
                "roles": i.metadata.labels.get("node-role.kubernetes.io/control-plane", i.metadata.labels.get("node-role.kubernetes.io/master") or "worker"),
                "allocatable": dict(i.status.allocatable) if i.status.allocatable else {},
                "capacity": dict(i.status.capacity) if i.status.capacity else {},
            })
        return {"nodes": nodes, "count": len(nodes), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "nodes": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/namespaces")
@app.get("/api/namespaces/")
def list_namespaces():
    """List cluster namespaces (for frontend filter)."""
    try:
        ret = v1_core.list_namespace()
        names = [i.metadata.name for i in ret.items]
        return {"namespaces": sorted(names), "count": len(names), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "namespaces": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/pods")
def list_pods(namespace: str | None = None):
    """List pods across the cluster or in a namespace."""
    try:
        if namespace:
            ret = v1_core.list_namespaced_pod(namespace=namespace)
        else:
            ret = v1_core.list_pod_for_all_namespaces()
        pods = []
        for i in ret.items:
            phase = i.status.phase if i.status else "Unknown"
            ready = "0/0"
            if i.status and i.status.container_statuses:
                ready_count = sum(1 for c in i.status.container_statuses if c.ready)
                total = len(i.status.container_statuses)
                ready = f"{ready_count}/{total}"
            pods.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "phase": phase,
                "ready": ready,
                "node": i.spec.node_name if i.spec else None,
            })
        return {"pods": pods, "count": len(pods), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "pods": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/services")
@app.get("/api/services/")
def list_services(namespace: str | None = None):
    """List services (optional ?namespace=...)."""
    try:
        if namespace:
            ret = v1_core.list_namespaced_service(namespace=namespace)
        else:
            ret = v1_core.list_service_for_all_namespaces()
        services = []
        for i in ret.items:
            svc_type = i.spec.type if i.spec else "ClusterIP"
            ports = ",".join(str(p.port) for p in (i.spec.ports or [])) if i.spec and i.spec.ports else "—"
            services.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "type": svc_type,
                "clusterIP": i.spec.cluster_ip if i.spec else None,
                "ports": ports,
            })
        return {"services": services, "count": len(services), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "services": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/deployments")
def list_deployments(namespace: str | None = None):
    """List deployments and their ready/desired replicas."""
    try:
        if namespace:
            ret = v1_apps.list_namespaced_deployment(namespace=namespace)
        else:
            ret = v1_apps.list_deployment_for_all_namespaces()
        deployments = []
        for i in ret.items:
            ready = i.status.ready_replicas or 0
            desired = i.spec.replicas or 0
            deployments.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "ready": ready,
                "desired": desired,
                "available": i.status.available_replicas or 0,
            })
        return {"deployments": deployments, "count": len(deployments), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "deployments": [], "count": 0, "timestamp": _now_iso()}


def _flux_list(group: str, version: str, plural: str):
    try:
        ret = custom_api.list_cluster_custom_object(group=group, version=version, plural=plural)
        return ret.get("items", []), None
    except ApiException as e:
        if e.status == 404:
            return [], "CRD not installed"
        return [], (e.reason or str(e))


@app.get("/api/flux/kustomizations")
def list_flux_kustomizations():
    """List Flux Kustomizations and their reconciliation status."""
    items, err = _flux_list("kustomize.toolkit.fluxcd.io", "v1", "kustomizations")
    if err:
        return {"kustomizations": [], "error": err, "timestamp": _now_iso()}
    out = []
    for i in items:
        status = i.get("status", {})
        cond = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
        out.append({
            "name": i.get("metadata", {}).get("name"),
            "namespace": i.get("metadata", {}).get("namespace"),
            "ready": cond.get("status") == "True",
            "message": cond.get("message", ""),
            "lastAppliedRevision": status.get("lastAppliedRevision"),
        })
    return {"kustomizations": out, "count": len(out), "timestamp": _now_iso()}


@app.get("/api/flux/helmreleases")
def list_flux_helmreleases():
    """List Flux HelmReleases and their status."""
    items, err = _flux_list("helm.toolkit.fluxcd.io", "v2", "helmreleases")
    if err:
        return {"helmreleases": [], "error": err, "timestamp": _now_iso()}
    out = []
    for i in items:
        status = i.get("status", {})
        cond = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
        out.append({
            "name": i.get("metadata", {}).get("name"),
            "namespace": i.get("metadata", {}).get("namespace"),
            "ready": cond.get("status") == "True",
            "message": cond.get("message", ""),
        })
    return {"helmreleases": out, "count": len(out), "timestamp": _now_iso()}


@app.get("/api/flux/gitrepositories")
def list_flux_gitrepositories():
    """List Flux GitRepositories and their artifact status."""
    items, err = _flux_list("source.toolkit.fluxcd.io", "v1", "gitrepositories")
    if err:
        return {"gitrepositories": [], "error": err, "timestamp": _now_iso()}
    out = []
    for i in items:
        status = i.get("status", {})
        cond = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
        out.append({
            "name": i.get("metadata", {}).get("name"),
            "namespace": i.get("metadata", {}).get("namespace"),
            "ready": cond.get("status") == "True",
            "url": i.get("spec", {}).get("url"),
            "revision": status.get("artifact", {}).get("revision"),
        })
    return {"gitrepositories": out, "count": len(out), "timestamp": _now_iso()}


@app.get("/api/workloads")
def workloads_summary():
    """Aggregated view: health, nodes, pod count, deployments, Flux (if present)."""
    health_resp = health()
    nodes_resp = list_nodes()
    pods_resp = list_pods()
    deployments_resp = list_deployments()
    kustomizations_resp = list_flux_kustomizations()
    gitrepos_resp = list_flux_gitrepositories()

    return {
        "health": health_resp,
        "nodes": nodes_resp,
        "pods": pods_resp,
        "deployments": deployments_resp,
        "flux": {
            "kustomizations": kustomizations_resp,
            "gitrepositories": gitrepos_resp,
        },
        "timestamp": _now_iso(),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
