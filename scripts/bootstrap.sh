#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Bootstrap Script - Post-Ansible cluster initialization
# ==============================================================================
# Prerequisites:
#   - k3s is installed and running (via Ansible)
#   - kubectl is configured and can reach the cluster
#   - helm is installed
#
# This script:
#   1. Installs Sealed Secrets controller
#   2. Installs ArgoCD
#   3. Applies the app-of-apps root Application
#   4. ArgoCD then takes over and deploys everything from git
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGOCD_NAMESPACE="argocd"
SEALED_SECRETS_NAMESPACE="infrastructure"

echo "============================================"
echo "  Homelab k3s Bootstrap"
echo "============================================"
echo ""

# --- Verify cluster access ---
echo "[1/6] Verifying cluster access..."
if ! kubectl cluster-info &>/dev/null; then
    echo "ERROR: Cannot reach k8s cluster. Is kubeconfig configured?"
    echo "  Try: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
    exit 1
fi
echo "  Cluster is reachable."

# --- Create namespaces ---
echo "[2/6] Creating namespaces..."
for ns in infrastructure media dns home-automation security monitoring dashboard argocd; do
    kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
echo "  Namespaces created."

# --- Install Sealed Secrets ---
echo "[3/6] Installing Sealed Secrets controller..."
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update
helm upgrade --install sealed-secrets sealed-secrets/sealed-secrets \
    --namespace "$SEALED_SECRETS_NAMESPACE" \
    --create-namespace \
    --wait \
    --timeout 5m
echo "  Sealed Secrets installed."

# --- Install ArgoCD ---
echo "[4/6] Installing ArgoCD..."
helm upgrade --install argocd argo/argo-cd \
    --namespace "$ARGOCD_NAMESPACE" \
    --create-namespace \
    --values "$REPO_ROOT/infrastructure/argocd/values.yaml" \
    --wait \
    --timeout 10m
echo "  ArgoCD installed."

# --- Get ArgoCD admin password ---
echo "[5/6] Retrieving ArgoCD admin password..."
ARGOCD_PASSWORD=$(kubectl -n "$ARGOCD_NAMESPACE" get secret argocd-initial-admin-secret \
    -o jsonpath="{.data.password}" 2>/dev/null | base64 -d || echo "")

if [ -n "$ARGOCD_PASSWORD" ]; then
    echo ""
    echo "  ArgoCD Admin Credentials:"
    echo "    URL:      https://argocd.home (or https://<node-ip>:443)"
    echo "    Username: admin"
    echo "    Password: $ARGOCD_PASSWORD"
    echo ""
    echo "  IMPORTANT: Change this password immediately after first login!"
    echo "    argocd account update-password"
    echo ""
else
    echo "  Could not retrieve initial password. It may have been deleted."
    echo "  Reset with: kubectl -n argocd patch secret argocd-secret -p '{\"data\": {\"admin.password\": null}}'"
fi

# --- Apply App of Apps ---
echo "[6/6] Applying App of Apps root Application..."
echo ""
echo "  IMPORTANT: Before applying, update the git repo URL in:"
echo "    argocd-apps/app-of-apps.yaml"
echo "    argocd-apps/infrastructure/*.yaml"
echo "    argocd-apps/apps/*.yaml"
echo ""
read -p "  Have you updated the repo URLs? (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    kubectl apply -f "$REPO_ROOT/argocd-apps/app-of-apps.yaml"
    echo "  App of Apps applied. ArgoCD will now sync all applications."
else
    echo "  Skipped. Apply manually after updating URLs:"
    echo "    kubectl apply -f argocd-apps/app-of-apps.yaml"
fi

echo ""
echo "============================================"
echo "  Bootstrap complete!"
echo ""
echo "  Next steps:"
echo "    1. Access ArgoCD UI and verify all apps are syncing"
echo "    2. Create sealed secrets for apps that need them:"
echo "       ./scripts/seal-secret.sh pihole-admin dns password 'your-password'"
echo "       ./scripts/seal-secret.sh grafana-admin-credentials monitoring admin-password 'your-password'"
echo "       ./scripts/seal-secret.sh vaultwarden-admin security admin-token 'your-token'"
echo "    3. Configure Pi-hole as your network DNS server"
echo "    4. Add *.home DNS entries pointing to your node IP"
echo "============================================"
