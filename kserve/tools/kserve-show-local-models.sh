#!/bin/bash
echo '=== Inference Services ==='
kubectl get inferenceservice -A

echo
echo '=== HTTP Routes ==='
kubectl get httproute -A

echo
echo '=== Deployments, Pods, and Services ==='
kubectl get deployment,pod,svc -A

echo
echo '=== Describe Inference Service ==='
echo 'Set model name and namespace to describe the inference service'
###kubectl describe inferenceservice <nome-modello> -n <namespace>
