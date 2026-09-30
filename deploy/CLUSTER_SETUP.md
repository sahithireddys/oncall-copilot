# Running against a real cluster

Simulated mode needs none of this. Use this when you want the agent to investigate real telemetry.

## 1. Cluster + OpenTelemetry Demo (kind, on a laptop with ≥16 GB RAM)

```bash
brew install kind helm kubectl
kind create cluster --name otel
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm install otel-demo open-telemetry/opentelemetry-demo -n otel-demo --create-namespace
kubectl -n otel-demo get pods -w          # wait until everything is Running (~5 min)
```

## 2. Port-forwards for the copilot running on your laptop

```bash
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080 &   # shop, Jaeger (/jaeger/ui), Grafana (/grafana)
kubectl -n otel-demo port-forward svc/prometheus 9090:9090 &
kubectl -n otel-demo port-forward svc/opensearch 9200:9200 &
```

Service names differ slightly between chart versions - check with `kubectl -n otel-demo get svc`.

## 3. Alerts -> copilot

Add `chaos/alerting/prometheus-rules.yaml` to the demo's Prometheus and point Alertmanager at the copilot with
`chaos/alerting/alertmanager.yaml`. Or skip Alertmanager and let `chaos/inject.py ... --fire 5` post the alert for you.

## 4. Chaos Mesh (only for the network-latency scenarios)

```bash
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace \
  --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/run/containerd/containerd.sock
```

## 5. Run an incident

```bash
TELEMETRY_MODE=live uvicorn copilot.api:app --port 8000        # terminal 1
python chaos/inject.py apply payment-failure-flag --fire 4     # terminal 2
python chaos/inject.py revert payment-failure-flag
```

The investigation is tagged with the scenario id, so live runs are graded against ground truth just like simulated ones.
