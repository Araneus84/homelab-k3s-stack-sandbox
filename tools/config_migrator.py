#!/usr/bin/env python3
import argparse
import subprocess
import time
import sys
import os

# ==============================================================================
# Kubernetes PVC Migrator
# ==============================================================================
# Migrates local configuration files (e.g. from Docker Compose) into a Kubernetes PVC.
#
# Workflow:
# 1. Scales down the target application (Deployment/StatefulSet) to 0 to ensure data consistency.
# 2. Spins up a temporary "transfer pod" mounting the target PVC.
# 3. Uses 'kubectl cp' to upload local files to the PVC.
# 4. Cleans up the transfer pod.
# 5. Scales the application back up.
# ==============================================================================


def log(msg, level="INFO"):
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARN": "\033[93m",
        "ERROR": "\033[91m",
        "RESET": "\033[0m",
    }
    print(f"{colors.get(level, '')}[{level}] {msg}{colors['RESET']}")


def run_cmd(cmd, check=True):
    try:
        log(f"Running: {cmd}", "INFO")
        result = subprocess.run(
            cmd,
            shell=True,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log(f"Command failed: {e.stderr}", "ERROR")
        if check:
            sys.exit(1)
        return None


def wait_for_pod(pod_name, namespace):
    log(f"Waiting for pod {pod_name} to be ready...", "INFO")
    for _ in range(30):
        status = run_cmd(
            f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}'",
            check=False,
        )
        if status == "Running":
            return True
        time.sleep(2)
    return False


def migrate(app_name, namespace, pvc_name, local_path, workload_type="deployment"):
    if not os.path.exists(local_path):
        log(f"Local path '{local_path}' does not exist!", "ERROR")
        sys.exit(1)

    log(f"Starting migration for {app_name} in {namespace}...", "INFO")

    # 1. Scale down
    log(f"Scaling down {workload_type}/{app_name}...", "INFO")
    run_cmd(f"kubectl scale {workload_type} {app_name} -n {namespace} --replicas=0")

    # Wait for pods to terminate (basic check)
    time.sleep(5)

    # 2. Create Transfer Pod
    pod_name = f"migrator-{app_name}-{int(time.time())}"
    yaml_overrides = f"""
apiVersion: v1
spec:
  containers:
  - name: transfer
    image: alpine:latest
    command: ["/bin/sh", "-c", "sleep 3600"]
    volumeMounts:
    - mountPath: /data
      name: vol
  volumes:
  - name: vol
    persistentVolumeClaim:
      claimName: {pvc_name}
  restartPolicy: Never
"""
    # We use kubectl run with overrides because it's cleaner than a temp file
    # Note: formatting JSON for overrides is annoying in python, so we create a temp file
    with open("temp_pod.yaml", "w") as f:
        f.write(
            f"apiVersion: v1\nkind: Pod\nmetadata:\n  name: {pod_name}\n  namespace: {namespace}\n"
        )
        f.write(
            f"spec:\n  containers:\n  - name: transfer\n    image: alpine:latest\n    command: ['/bin/sh', '-c', 'sleep 3600']\n    volumeMounts:\n    - mountPath: /data\n      name: vol\n  volumes:\n  - name: vol\n    persistentVolumeClaim:\n      claimName: {pvc_name}\n  restartPolicy: Never\n"
        )

    log(f"Creating transfer pod {pod_name}...", "INFO")
    run_cmd(f"kubectl apply -f temp_pod.yaml")

    if not wait_for_pod(pod_name, namespace):
        log("Transfer pod failed to start.", "ERROR")
        sys.exit(1)

    # 3. Copy Data
    log(f"Copying data from {local_path} to PVC...", "INFO")
    # Clean destination first? Optional. Let's assume overwrite.
    # We copy the CONTENTS of local_path into /data/

    # tar strategy is faster and preserves permissions better than kubectl cp for many files
    # Local tar -> kubectl exec -> tar extract
    try:
        # Check if local path is directory
        if os.path.isdir(local_path):
            cmd = f"tar cf - -C {local_path} . | kubectl exec -i -n {namespace} {pod_name} -- tar xf - -C /data"
            run_cmd(cmd)
            log("Data transfer complete.", "SUCCESS")
        else:
            log("Local path must be a directory.", "ERROR")
    except Exception as e:
        log(f"Copy failed: {e}", "ERROR")

    # 4. Cleanup
    log(f"Deleting transfer pod {pod_name}...", "INFO")
    run_cmd(f"kubectl delete pod {pod_name} -n {namespace} --force --grace-period=0")
    os.remove("temp_pod.yaml")

    # 5. Scale Up
    log(f"Scaling up {workload_type}/{app_name}...", "INFO")
    run_cmd(f"kubectl scale {workload_type} {app_name} -n {namespace} --replicas=1")

    log(f"Migration for {app_name} completed successfully!", "SUCCESS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate local configs to K8s PVCs")
    parser.add_argument(
        "--app",
        required=True,
        help="Name of the application (deployment/statefulset name)",
    )
    parser.add_argument("--namespace", required=True, help="K8s Namespace")
    parser.add_argument("--pvc", required=True, help="Name of the PVC to migrate into")
    parser.add_argument(
        "--local-path", required=True, help="Local path to config directory"
    )
    parser.add_argument(
        "--type",
        default="deployment",
        choices=["deployment", "statefulset"],
        help="Workload type",
    )

    args = parser.parse_args()

    migrate(args.app, args.namespace, args.pvc, args.local_path, args.type)
