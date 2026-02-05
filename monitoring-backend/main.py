"""
Monitoring Backend - Polls Kubernetes API for nodes, pods, Flux workloads, and health.
Runs in-cluster using the monitoring ServiceAccount.
Optionally sends logs to Kafka when KAFKA_BOOTSTRAP_SERVERS is set.
"""
import json
import logging
import os
import queue
import threading
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
batch_v1 = client.BatchV1Api()
networking_v1 = client.NetworkingV1Api()
storage_v1 = client.StorageV1Api()
custom_api = client.CustomObjectsApi()


def _setup_kafka_logging():
    """If KAFKA_BOOTSTRAP_SERVERS is set, add a handler that sends log records to Kafka."""
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    topic = os.environ.get("KAFKA_LOG_TOPIC", "monitoring-logs").strip() or "monitoring-logs"
    if not bootstrap:
        return
    try:
        from kafka import KafkaProducer
        from kafka.errors import KafkaError

        log_queue = queue.Queue()
        producer = KafkaProducer(
            bootstrap_servers=bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=5000,
            retries=0,
        )

        def send_worker():
            while True:
                try:
                    msg = log_queue.get()
                    if msg is None:
                        break
                    producer.send(topic, value=msg)
                except Exception:
                    pass
                finally:
                    try:
                        log_queue.task_done()
                    except Exception:
                        pass

        thread = threading.Thread(target=send_worker, daemon=True)
        thread.start()

        class KafkaHandler(logging.Handler):
            def emit(self, record):
                try:
                    log_queue.put_nowait({
                        "source": "monitoring-backend",
                        "level": record.levelname,
                        "message": self.format(record),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "logger": record.name,
                    })
                except Exception:
                    pass

        handler = KafkaHandler()
        handler.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
        logging.getLogger().addHandler(handler)
    except Exception as e:
        logging.warning("Kafka logging not enabled: %s", e)


_setup_kafka_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify we can talk to the API
    try:
        v1_core.list_namespace(limit=1)
        logging.info("Monitoring backend started; Kubernetes API reachable")
    except ApiException as e:
        logging.warning("Kubernetes API check failed: %s", e)
    yield
    # Shutdown
    pass


app = FastAPI(
    title="NKP Monitoring API",
    description="Cluster nodes, pods, Flux workloads, and health",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
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


# Build-time version (set in Dockerfile: ENV APP_VERSION=...)
APP_VERSION = os.environ.get("APP_VERSION", "dev")


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
        "version": APP_VERSION,
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


def _list_namespaces():
    try:
        ret = v1_core.list_namespace()
        names = [i.metadata.name for i in ret.items]
        return {"namespaces": sorted(names), "count": len(names), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "namespaces": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/namespaces")
@app.get("/api/namespaces/")
@app.get("/api/ns")  # alias (some proxies block "namespaces")
@app.get("/api/ns/")
def list_namespaces():
    """List cluster namespaces (for frontend filter)."""
    return _list_namespaces()


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


def _list_services(namespace: str | None = None):
    try:
        if namespace:
            ret = v1_core.list_namespaced_service(namespace=namespace)
        else:
            ret = v1_core.list_service_for_all_namespaces()
        services = []
        for i in ret.items:
            svc_type = i.spec.type if i.spec else "ClusterIP"
            ports = ",".join(str(p.port) for p in (i.spec.ports or [])) if i.spec and i.spec.ports else "—"
            external_ip = None
            if i.status and i.status.load_balancer and i.status.load_balancer.ingress:
                ing = i.status.load_balancer.ingress[0]
                external_ip = ing.ip if ing.ip else (ing.hostname if ing.hostname else None)
            services.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "type": svc_type,
                "clusterIP": i.spec.cluster_ip if i.spec else None,
                "externalIP": external_ip,
                "ports": ports,
            })
        return {"services": services, "count": len(services), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "services": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/services")
@app.get("/api/services/")
@app.get("/api/svc")  # alias (some proxies block "services")
@app.get("/api/svc/")
def list_services(namespace: str | None = None):
    """List services (optional ?namespace=...)."""
    return _list_services(namespace)


@app.get("/api/ingresses")
@app.get("/api/ingresses/")
def list_ingresses(namespace: str | None = None):
    """List Ingresses (optional ?namespace=...)."""
    try:
        if namespace:
            ret = networking_v1.list_namespaced_ingress(namespace=namespace)
        else:
            ret = networking_v1.list_ingress_for_all_namespaces()
        ingresses = []
        for i in ret.items:
            hosts = []
            if i.spec and i.spec.rules:
                for r in i.spec.rules:
                    if r.host:
                        hosts.append(r.host)
            ingress_class = (i.spec.ingress_class_name if i.spec else None) or "—"
            ingresses.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "hosts": ",".join(hosts) if hosts else "—",
                "ingressClassName": ingress_class,
            })
        return {"ingresses": ingresses, "count": len(ingresses), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "ingresses": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/pv")
def list_pv():
    """List PersistentVolumes (cluster-scoped)."""
    try:
        ret = v1_core.list_persistent_volume()
        pvs = []
        for i in ret.items:
            status = i.status.phase if i.status else "Unknown"
            sc = i.spec.storage_class_name if i.spec else None
            cap = None
            if i.spec and i.spec.capacity and "storage" in i.spec.capacity:
                cap = i.spec.capacity["storage"]
            access = ",".join(i.spec.access_modes) if i.spec and i.spec.access_modes else "—"
            pvs.append({
                "name": i.metadata.name,
                "status": status,
                "storageClass": sc or "—",
                "capacity": cap or "—",
                "accessModes": access,
            })
        return {"persistentvolumes": pvs, "count": len(pvs), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "persistentvolumes": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/pvc")
@app.get("/api/pvc/")
def list_pvc(namespace: str | None = None):
    """List PersistentVolumeClaims (optional ?namespace=...)."""
    try:
        if namespace:
            ret = v1_core.list_namespaced_persistent_volume_claim(namespace=namespace)
        else:
            ret = v1_core.list_persistent_volume_claim_for_all_namespaces()
        pvcs = []
        for i in ret.items:
            phase = i.status.phase if i.status else "Unknown"
            sc = i.spec.storage_class_name if i.spec else None
            cap = None
            if i.spec and i.spec.resources and i.spec.resources.requests and "storage" in i.spec.resources.requests:
                cap = i.spec.resources.requests["storage"]
            access = ",".join(i.spec.access_modes) if i.spec and i.spec.access_modes else "—"
            volume = i.spec.volume_name if i.spec else None
            pvcs.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "status": phase,
                "storageClass": sc or "—",
                "capacity": cap or "—",
                "accessModes": access,
                "volume": volume or "—",
            })
        return {"persistentvolumeclaims": pvcs, "count": len(pvcs), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "persistentvolumeclaims": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/storageclasses")
@app.get("/api/storageclasses/")
@app.get("/api/sc")  # alias
@app.get("/api/sc/")
def list_storageclasses():
    """List StorageClasses (cluster-scoped)."""
    try:
        ret = storage_v1.list_storage_class()
        scs = []
        for i in ret.items:
            provisioner = i.provisioner if i.provisioner else "—"
            reclaim = i.reclaim_policy or "—"
            default = "false"
            if i.metadata and i.metadata.annotations:
                default = i.metadata.annotations.get("storageclass.kubernetes.io/is-default-class", "false")
            scs.append({
                "name": i.metadata.name,
                "provisioner": provisioner,
                "reclaimPolicy": reclaim,
                "isDefault": default == "true",
            })
        return {"storageclasses": scs, "count": len(scs), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "storageclasses": [], "count": 0, "timestamp": _now_iso()}


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


@app.get("/api/statefulsets")
@app.get("/api/statefulsets/")
def list_statefulsets(namespace: str | None = None):
    """List StatefulSets and their ready/desired replicas."""
    try:
        if namespace:
            ret = v1_apps.list_namespaced_stateful_set(namespace=namespace)
        else:
            ret = v1_apps.list_stateful_set_for_all_namespaces()
        items = []
        for i in ret.items:
            ready = i.status.ready_replicas or 0
            desired = i.spec.replicas or 0
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "ready": ready,
                "desired": desired,
            })
        return {"statefulsets": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "statefulsets": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/daemonsets")
@app.get("/api/daemonsets/")
def list_daemonsets(namespace: str | None = None):
    """List DaemonSets and their desired/current/ready number."""
    try:
        if namespace:
            ret = v1_apps.list_namespaced_daemon_set(namespace=namespace)
        else:
            ret = v1_apps.list_daemon_set_for_all_namespaces()
        items = []
        for i in ret.items:
            desired = i.status.desired_number_scheduled or 0
            current = i.status.current_number_scheduled or 0
            ready = i.status.number_ready or 0
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "desired": desired,
                "current": current,
                "ready": ready,
            })
        return {"daemonsets": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "daemonsets": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/configmaps")
@app.get("/api/configmaps/")
def list_configmaps(namespace: str | None = None):
    """List ConfigMaps (name/namespace only; no data)."""
    try:
        if namespace:
            ret = v1_core.list_namespaced_config_map(namespace=namespace)
        else:
            ret = v1_core.list_config_map_for_all_namespaces()
        items = []
        for i in ret.items:
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
            })
        return {"configmaps": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "configmaps": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/secrets")
@app.get("/api/secrets/")
def list_secrets(namespace: str | None = None):
    """List Secrets (name/namespace/type only; no data)."""
    try:
        if namespace:
            ret = v1_core.list_namespaced_secret(namespace=namespace)
        else:
            ret = v1_core.list_secret_for_all_namespaces()
        items = []
        for i in ret.items:
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "type": i.type or "Opaque",
            })
        return {"secrets": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "secrets": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/jobs")
@app.get("/api/jobs/")
def list_jobs(namespace: str | None = None):
    """List Jobs with succeeded/failed/active status."""
    try:
        if namespace:
            ret = batch_v1.list_namespaced_job(namespace=namespace)
        else:
            ret = batch_v1.list_job_for_all_namespaces()
        items = []
        for i in ret.items:
            status = i.status
            succeeded = status.succeeded or 0 if status else 0
            failed = status.failed or 0 if status else 0
            active = status.active or 0 if status else 0
            complete = status.completion_time is not None if status else False
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "succeeded": succeeded,
                "failed": failed,
                "active": active,
                "complete": complete,
            })
        return {"jobs": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "jobs": [], "count": 0, "timestamp": _now_iso()}


@app.get("/api/cronjobs")
@app.get("/api/cronjobs/")
def list_cronjobs(namespace: str | None = None):
    """List CronJobs with schedule and last run."""
    try:
        if namespace:
            ret = batch_v1.list_namespaced_cron_job(namespace=namespace)
        else:
            ret = batch_v1.list_cron_job_for_all_namespaces()
        items = []
        for i in ret.items:
            schedule = i.spec.schedule if i.spec else "—"
            last_schedule = None
            if i.status and i.status.last_successful_time:
                last_schedule = i.status.last_successful_time.isoformat()
            elif i.status and i.status.last_schedule_time:
                last_schedule = i.status.last_schedule_time.isoformat()
            suspended = i.spec.suspend if i.spec is not None else False
            items.append({
                "name": i.metadata.name,
                "namespace": i.metadata.namespace,
                "schedule": schedule,
                "lastSchedule": last_schedule,
                "suspended": suspended,
            })
        return {"cronjobs": items, "count": len(items), "timestamp": _now_iso()}
    except ApiException as e:
        return {"error": e.reason or str(e), "cronjobs": [], "count": 0, "timestamp": _now_iso()}


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
    """Aggregated view: health, nodes, pods, deployments, PV, PVC, StorageClasses, Flux (if present)."""
    health_resp = health()
    nodes_resp = list_nodes()
    pods_resp = list_pods()
    deployments_resp = list_deployments()
    statefulsets_resp = list_statefulsets()
    daemonsets_resp = list_daemonsets()
    configmaps_resp = list_configmaps()
    secrets_resp = list_secrets()
    jobs_resp = list_jobs()
    cronjobs_resp = list_cronjobs()
    pv_resp = list_pv()
    pvc_resp = list_pvc()
    sc_resp = list_storageclasses()
    kustomizations_resp = list_flux_kustomizations()
    gitrepos_resp = list_flux_gitrepositories()

    return {
        "health": health_resp,
        "nodes": nodes_resp,
        "pods": pods_resp,
        "deployments": deployments_resp,
        "statefulsets": statefulsets_resp,
        "daemonsets": daemonsets_resp,
        "configmaps": configmaps_resp,
        "secrets": secrets_resp,
        "jobs": jobs_resp,
        "cronjobs": cronjobs_resp,
        "persistentvolumes": pv_resp,
        "persistentvolumeclaims": pvc_resp,
        "storageclasses": sc_resp,
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
