<div align="center">

# Homelab · Kubernetes · GitOps

**Personal cluster, production-style habits:** everything lives in Git, Argo CD reconciles it, and the stack is observable end-to-end.  
Ansible for the metal, Helm for workloads, Sealed Secrets for anything sensitive.

</div>

---

### How it fits together

```mermaid
flowchart LR
    git["Git<br/>main branch"] --> argo["Argo CD<br/>app-of-apps"]

    subgraph k8s["k3s cluster"]
        subgraph dns["DNS"]
            pihole["Pi-hole"]
            adguard["AdGuard Home"]
        end

        subgraph sec["Security & TLS"]
            certmgr["cert-manager"]
            sealed["Sealed Secrets"]
            vaultwarden["Vaultwarden"]
        end

        subgraph edge["Edge"]
            nginx["nginx-ingress"]
        end

        subgraph obs["Observability"]
            prom["Prometheus + Grafana + Alertmanager"]
            loki["Loki + Promtail"]
        end

        subgraph storage["Storage"]
            nfs["NFS provisioner"]
        end

        subgraph media["Apps"]
            homepage["Homepage"]
            homeassistant["Home Assistant"]
            overseerr["Overseerr"]
            prowlarr["Prowlarr"]
            radarr["Radarr"]
            sonarr["Sonarr"]
            qbit["qBittorrent"]
            plex["Plex"]
            autobrr["Autobrr"]
            flaresolverr["FlareSolverr"]
        end
    end

    argo --> certmgr
    argo --> sealed
    argo --> nginx
    argo --> prom
    argo --> loki
    argo --> nfs
    argo --> pihole
    argo --> adguard
    argo --> homepage
    argo --> homeassistant
    argo --> overseerr
    argo --> prowlarr
    argo --> radarr
    argo --> sonarr
    argo --> qbit
    argo --> plex
    argo --> autobrr
    argo --> flaresolverr

    certmgr --> nginx
    nginx --> homepage

    nfs --> qbit
    nfs --> plex

    overseerr --> radarr
    overseerr --> sonarr
    prowlarr --> radarr
    prowlarr --> sonarr
    radarr --> qbit
    sonarr --> qbit
    radarr --> plex
    sonarr --> plex
```

---

### Why open this repo (10-second scan)

| You’re looking for… | It’s here |
|---------------------|-----------|
| GitOps | Argo CD app-of-apps, sync waves, Image Updater on selected charts |
| IaC | Ansible roles for k3s node + Helm charts for every workload |
| Observability | kube-prometheus-stack, Loki/Promtail, custom alert rules |
| Security posture | Sealed Secrets, ingress TLS, Ansible ssh/UFW/fail2ban baseline |
| Real homelab problems | NFS, hostNetwork where discovery requires it, `*.home` DNS patterns |

---

### Repo layout

- **Root app:** `argocd-apps/app-of-apps.yaml`
- **Applications:** `argocd-apps/apps/`, `argocd-apps/infrastructure/`
- **Charts:** `apps/` (workloads), `infrastructure/` (platform add-ons)
- **Optional / not in Argo yet:** `infrastructure/velero/` (backups when you wire object storage)
- **Legacy:** `legacy/docker-compose.yml` — compose stack **before** k8s; reference only

### Docs

- [docs/setup.md](docs/setup.md) — Ansible, bootstrap, secrets, DNS  
- [docs/architecture.md](docs/architecture.md) — topology, namespaces, ingress table, sync waves, security detail  
