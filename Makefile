# Makefile for Homelab Migration Project

.PHONY: help lint legacy-start legacy-stop demo-start demo-stop bootstrap migrate-config clean secrets

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

lint: ## Lint Ansible playbooks and Helm charts
	ansible-lint ansible/
	helm lint apps/* infrastructure/*

secrets: ## Generate SealedSecrets interactively
	./scripts/setup_secrets.sh

legacy-start: ## Start the legacy Docker Compose stack (Simulation)
	python3 tools/migration_manager.py legacy-up

legacy-stop: ## Stop the legacy Docker Compose stack
	python3 tools/migration_manager.py legacy-down

demo-start: ## Start the local k3d Kubernetes cluster (Target Environment)
	python3 tools/migration_manager.py k3d-up

bootstrap: ## Install ArgoCD and apply the GitOps root application
	kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
	helm repo add argo https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argo/argo-cd --namespace argocd --version 5.51.6 \
		--set server.service.type=LoadBalancer \
		--set server.insecure=true
	# Ensure sealed secrets controller is installed (usually via app-of-apps/infrastructure)
	# But we might need it earlier to decrypt secrets if we apply them now.
	# The bootstrap.sh script usually handles this sequence.
	./scripts/bootstrap.sh

migrate-config: ## Migrate local config to K8s PVC (Interactive)
	@echo "Migrating config..."
	@read -p "Enter App Name (e.g., plex): " APP; \
	read -p "Enter Namespace (e.g., media): " NS; \
	read -p "Enter PVC Name (e.g., plex-config): " PVC; \
	read -p "Enter Local Path (e.g., ./legacy/config/plex): " PATH; \
	python3 tools/config_migrator.py --app $$APP --namespace $$NS --pvc $$PVC --local-path $$PATH

demo-stop: ## Destroy the local k3d cluster
	k3d cluster delete homelab

clean: legacy-stop demo-stop ## Stop and remove all environments (Legacy + Target)
