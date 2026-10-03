# Serving a local inference with KServe

Instructions for serving a local inference (Iris classification) with KServe.

## Prerequisites

- Docker (Desktop)
- KinD (Kubernetes in Docker)
- kubectl
- Helm
- KServe

For KServe setup, refer to [kserve-kind-wsl-setup.md](tools/kserve-kind-wsl-setup.md).

## KServe Runtime Configurations

Check the installed version of KServe:

```bash
helm list -n kserve
```

In the following commands, `--version` must match the version of installed KServe.

To install the KServe runtime configurations:

```bash
helm upgrade -i kserve-runtime-configs \
  oci://ghcr.io/kserve/charts/kserve-runtime-configs \
  --version v0.20.0 \
  --namespace kserve \
  --create-namespace \
  --wait \
  --set kserve.servingruntime.enabled=true \
  --set kserve.llmisvcConfigs.enabled=false
```

## Docker KinD container and dedicated cluster

To create cluster:

```bash
kind create cluster --name mlops-test
```

To verify the cluster creation:

```bash
kubectl get nodes --context kind-mlops-test

kubectl cluster-info --context kind-mlops-test
```

To start/stop the KinD container:

```bash
docker start mlops-test-control-plane
docker stop mlops-test-control-plane
```

To completely destroy the cluster:

```bash
kind delete cluster --name mlops-test
```

## Dedicated namespace

To create a dedicated namespace for inference test:

```bash
kubectl create namespace mlops-iris-inference
```

To verify the namespace creation:

```bash
kubectl get namespaces
```

To delete the dedicated namespace for inference test:

```bash
kubectl delete namespace mlops-iris-inference
```

## Inference Service

The following commands assume that you are in the `kserve` directory of the project.
Change location to that directory or copy that to your home directory (e.g. for WSL Ubuntu).

```bash
kubectl apply -f ./config/iris-sklearn.yaml
```

To verify the Inference Service creation:

```bash
kubectl get inferenceservices -n mlops-iris-inference
```

Wait until the Inference Service is ready (the `READY` column should show `True`).

To verify the Inference Service creation:

```bash
 kubectl get deployment,service,pod -n mlops-iris-inference -o wide
```

## Port forwarding

To identify the Envoy data service:

```bash
kubectl get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway
```

Take a note of the Envoy data service's namespace (e.g. `envoy-gateway-system`) and name.

To set up port forwarding:

```bash
kubectl port-forward -n <ENVOY-SERVICE-NAMESPACE> service/<ENVOY-SERVICE-NAME> 8080:80
```

Keep this command running in the terminal to maintain the port forwarding.
Open a new terminal for further commands.

## Model inference

To check if routing works and the model is ready:

```bash
curl -i \
  -H 'Host: sklearn-iris-mlops-iris-inference.example.com' \
  http://127.0.0.1:8080/v2/models/sklearn-iris/ready
```

Host header is needed so that Envoy/Gateway can correctly route the request to the appropriate Inference Service.

Correct output example:

```
HTTP/1.1 200 OK
content-type: application/json
...
{"name":"sklearn-iris","ready":true}
```

To perform an actual inference request:

```bash
curl -i \
  -X POST \
  -H 'Host: sklearn-iris-mlops-iris-inference.example.com' \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8080/v1/models/sklearn-iris:predict \
  --data '{
    "instances": [
      [6.8, 2.8, 4.8, 1.4]
    ]
  }'
```

Correct output example:

```
HTTP/1.1 200 OK
content-type: application/json
...
{"predictions":[1]}
```
