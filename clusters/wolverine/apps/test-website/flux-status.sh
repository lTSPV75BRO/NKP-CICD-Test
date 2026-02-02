#!/bin/sh
while true; do
  # Get real Flux status
  FLUX_STATUS=$(kubectl get kustomizations -n flux-workloads -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.ready}{"\n"}{end}')
  GIT_REVISION=$(kubectl get gitrepository -n flux-workloads kafka-infra -o jsonpath='{.status.artifact.revision}')
  PODS_READY=$(kubectl get pods -n apps --no-headers 2>/dev/null | grep -c Running || echo 0)
  TOTAL_PODS=$(kubectl get pods -n apps --no-headers 2>/dev/null | wc -l)
  
  # Generate HTML
  cat > /usr/share/nginx/html/index.html <<EOF
<!DOCTYPE html>
<html>
<head>
    <title>NKP-CICD Live Status 🎉</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto; margin:0; padding:40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height:100vh; color:white; }
        .container { max-width:900px; margin:0 auto; }
        .header { text-align:center; margin-bottom:40px; }
        .status-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px,1fr)); gap:20px; }
        .card { background:rgba(255,255,255,0.1); backdrop-filter:blur(10px); padding:25px; border-radius:20px; border:1px solid rgba(255,255,255,0.2); }
        .ready { color:#10b981; font-weight:bold; }
        .failed { color:#ef4444; font-weight:bold; }
        .metric { font-size:1.3em; margin:10px 0; }
        .flux-logo { font-size:2em; margin-bottom:10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 NKP-CICD Flux GitOps Dashboard</h1>
            <p>Last updated: $(date)</p>
        </div>
        
        <div class="status-grid">
            <div class="card">
                <div class="flux-logo">🔄 Flux Status</div>
                <div class="metric">Git: <span id="git-rev">${GIT_REVISION}</span></div>
                <pre style="background:rgba(0,0,0,0.2);padding:15px;border-radius:10px;font-family:monospace;font-size:14px;">${FLUX_STATUS}</pre>
            </div>
            
            <div class="card">
                <div>📦 Website Pods</div>
                <div class="metric">${PODS_READY}/${TOTAL_PODS} Running</div>
                <div>Namespace: apps</div>
            </div>
            
            <div class="card">
                <div>🌐 LoadBalancer</div>
                <div class="metric">$(kubectl get svc website -n apps -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo Pending)</div>
                <div>NodePort: $(kubectl get svc website -n apps -o jsonpath='{.spec.ports[0].nodePort}')</div>
            </div>
            
            <div class="card">
                <div>🐳 Kafka Operator</div>
                <div class="metric">$(kubectl get pods -n kafka -l name=strimzi-cluster-operator --no-headers 2>/dev/null | wc -l) pods</div>
                <div>CRDs: $(kubectl get crd | grep kafka.strimzi.io | wc -l)</div>
            </div>
        </div>
    </div>
</body>
</html>
EOF
  
  sleep 30
done

