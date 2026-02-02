package main

import (
    "encoding/json"
    "fmt"
    "log"
    "net/http"
    "os/exec"
    "strings"
    "time"
)

type Status struct {
    Pods      []PodInfo  `json:"pods"`
    Services  []SvcInfo  `json:"services"`
    ExternalIPs map[string]string `json:"external_ips"`
    Timestamp string    `json:"timestamp"`
}

type PodInfo struct {
    Name   string `json:"name"`
    Status string `json:"status"`
    Node   string `json:"node"`
}

type SvcInfo struct {
    Name      string `json:"name"`
    Type      string `json:"type"`
    ClusterIP string `json:"cluster_ip"`
}

func main() {
    http.HandleFunc("/api/status", statusHandler)
    http.HandleFunc("/health", healthHandler)
    log.Println("🚀 Monitoring Backend starting on :8080")
    log.Fatal(http.ListenAndServe(":8080", nil))
}

func statusHandler(w http.ResponseWriter, r *http.Request) {
    status := getClusterStatus()
    
    // Log to Kafka
    logToKafka(status)
    
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(status)
}

func getClusterStatus() Status {
    pods := getPods()
    svcs := getServices()
    ips := getExternalIPs()
    
    return Status{
        Pods:      pods,
        Services:  svcs,
        ExternalIPs: ips,
        Timestamp: time.Now().Format(time.RFC3339),
    }
}

func getPods() []PodInfo {
    out, _ := exec.Command("kubectl", "get", "pods", "-n", "apps", "-o", "jsonpath={range .items[*]}{.metadata.name}:{.status.phase}:{.spec.nodeName}{'\\n'}{end}").Output()
    lines := strings.Split(strings.TrimSpace(string(out)), "\n")
    var pods []PodInfo
    for _, line := range lines {
        parts := strings.Split(line, ":")
        if len(parts) == 3 {
            pods = append(pods, PodInfo{Name: parts[0], Status: parts[1], Node: parts[2]})
        }
    }
    return pods
}

func getServices() []SvcInfo {
    out, _ := exec.Command("kubectl", "get", "svc", "-n", "apps", "-o", "jsonpath={range .items[*]}{.metadata.name}:{.spec.type}:{.spec.clusterIP}{'\\n'}{end}").Output()
    lines := strings.Split(strings.TrimSpace(string(out)), "\n")
    var svcs []SvcInfo
    for _, line := range lines {
        parts := strings.Split(line, ":")
        if len(parts) == 3 {
            svcs = append(svcs, SvcInfo{Name: parts[0], Type: parts[1], ClusterIP: parts[2]})
        }
    }
    return svcs
}

func getExternalIPs() map[string]string {
    out, _ := exec.Command("kubectl", "get", "svc", "-n", "apps", "-o", "jsonpath={range .items[*]}{.metadata.name}:{.status.loadBalancer.ingress[0].ip}{'\\n'}{end}").Output()
    ips := make(map[string]string)
    lines := strings.Split(strings.TrimSpace(string(out)), "\n")
    for _, line := range lines {
        parts := strings.Split(line, ":")
        if len(parts) == 2 && parts[1] != "<none>" {
            ips[parts[0]] = parts[1]
        }
    }
    return ips
}

func logToKafka(status Status) {
    data, _ := json.Marshal(status)
    cmd := exec.Command("kafkacat", "-b", "my-kafka-kafka-bootstrap.kafka.svc:9092", "-t", "cluster-metrics", "-P")
    cmd.Stdin = strings.NewReader(string(data))
    cmd.Run()
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, "OK")
}

