#!/usr/bin/env bash
# ============================================================================
# 清理脚本：删除 k8s 资源（不删除云基础设施；如需清理基础设施请 terraform destroy）
# ============================================================================

set -euo pipefail

NAMESPACE="${NAMESPACE:-spf-app}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RENDER_DIR="${ROOT_DIR}/src/manifests/rendered"

kubectl delete -f "${RENDER_DIR}/data-collector.yaml" --ignore-not-found || true
kubectl delete -f "${RENDER_DIR}/anomaly-detector.yaml" --ignore-not-found || true
kubectl delete -f "${RENDER_DIR}/postgres.yaml" --ignore-not-found || true
kubectl delete namespace "${NAMESPACE}" --ignore-not-found || true

echo "k8s 资源已清理。基础设施（VPC/CCE/OBS/SWR）如需销毁请执行 terraform destroy。"
