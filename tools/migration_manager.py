#!/usr/bin/env python3
import subprocess
import sys
import shutil
import time
import os
import argparse


# Colors for output
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def log(message, level="info"):
    if level == "info":
        print(f"{Colors.OKBLUE}[INFO]{Colors.ENDC} {message}")
    elif level == "success":
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} {message}")
    elif level == "warn":
        print(f"{Colors.WARNING}[WARN]{Colors.ENDC} {message}")
    elif level == "error":
        print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} {message}")


def check_command(command):
    if not shutil.which(command):
        log(f"Command '{command}' not found. Please install it first.", "error")
        sys.exit(1)


def run_command(command, shell=False):
    try:
        if shell:
            subprocess.run(command, check=True, shell=True)
        else:
            subprocess.run(command.split(), check=True)
    except subprocess.CalledProcessError as e:
        log(f"Command failed: {command}", "error")
        sys.exit(1)


def setup_legacy():
    """Start the legacy Docker Compose stack"""
    log("Setting up Legacy Environment (Docker Compose)...")
    check_command("docker-compose")
    legacy_path = os.path.join(os.getcwd(), "legacy")

    if not os.path.exists(legacy_path):
        log("Legacy directory not found!", "error")
        return

    os.chdir(legacy_path)
    run_command("docker-compose up -d", shell=True)
    os.chdir("..")
    log("Legacy stack is running at http://localhost (various ports)", "success")


def teardown_legacy():
    """Stop the legacy Docker Compose stack"""
    log("Tearing down Legacy Environment...")
    legacy_path = os.path.join(os.getcwd(), "legacy")
    os.chdir(legacy_path)
    run_command("docker-compose down", shell=True)
    os.chdir("..")
    log("Legacy stack stopped.", "success")


def setup_k3d():
    """Create a local k3d cluster to simulate the target environment"""
    log("Setting up Target Environment (k3d Kubernetes Cluster)...")
    check_command("k3d")
    check_command("kubectl")

    # Check if cluster exists
    result = subprocess.run(
        "k3d cluster list homelab",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        log("Cluster 'homelab' already exists. Skipping creation.", "warn")
    else:
        # Create cluster with specific config to match production (disable traefik/servicelb)
        # We map ports 80/443 to localhost for Ingress testing
        cmd = """
        k3d cluster create homelab \
            --api-port 6443 \
            -p "80:80@loadbalancer" \
            -p "443:443@loadbalancer" \
            --k3s-arg "--disable=traefik@server:0" \
            --k3s-arg "--disable=servicelb@server:0" \
            --agents 2
        """
        run_command(cmd, shell=True)
        log("k3d cluster 'homelab' created.", "success")

    # Wait for cluster to be ready
    log("Waiting for cluster to be ready...")
    run_command(
        "kubectl wait --for=condition=Ready nodes --all --timeout=60s", shell=True
    )


def bootstrap_gitops():
    """Install ArgoCD and apply the Root Application"""
    log("Bootstrapping GitOps (ArgoCD)...")
    check_command("helm")
    check_command("kubectl")

    # 1. Create argocd namespace
    run_command(
        "kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -",
        shell=True,
    )

    # 2. Install ArgoCD via Helm
    log("Installing ArgoCD...")
    run_command("helm repo add argo https://argoproj.github.io/argo-helm", shell=True)
    run_command("helm repo update", shell=True)
    run_command(
        "helm upgrade --install argocd argo/argo-cd --namespace argocd --version 5.51.6 --set server.service.type=LoadBalancer",
        shell=True,
    )

    # 3. Apply Root App
    log("Applying Root Application (app-of-apps)...")
    # We need to ensure the repo URL is correct. For local testing, we might need to point to the local git repo or a public one.
    # For now, we assume the user has pushed to GitHub and the URL in app-of-apps.yaml is correct.
    # OR we can apply the local files directly for testing.

    # Let's apply the app-of-apps directly from the local file
    app_file = "argocd-apps/app-of-apps.yaml"
    if os.path.exists(app_file):
        run_command(f"kubectl apply -f {app_file}", shell=True)
        log("Root Application applied. ArgoCD will now sync the cluster.", "success")
    else:
        log(f"File {app_file} not found!", "error")


def main():
    parser = argparse.ArgumentParser(description="Migration Manager for Homelab")
    parser.add_argument(
        "action",
        choices=["legacy-up", "legacy-down", "k3d-up", "bootstrap", "full-demo"],
        help="Action to perform",
    )

    args = parser.parse_args()

    if args.action == "legacy-up":
        setup_legacy()
    elif args.action == "legacy-down":
        teardown_legacy()
    elif args.action == "k3d-up":
        setup_k3d()
    elif args.action == "bootstrap":
        bootstrap_gitops()
    elif args.action == "full-demo":
        setup_legacy()
        time.sleep(5)
        setup_k3d()
        bootstrap_gitops()


if __name__ == "__main__":
    main()
