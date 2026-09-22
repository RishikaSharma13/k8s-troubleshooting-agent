# Kubernetes failure testing

These manifests are deliberately isolated to the `troubleshooting-test` namespace. Apply only to a non-production test context:

```bash
kubectl --context <context> apply -f docs/testing/failure-scenarios.yaml
kubectl --context <context> get pods -n troubleshooting-test
kubectl --context <context> delete namespace troubleshooting-test
```

Never apply these scenarios to `kube-system` or a production namespace.
