#!/usr/bin/env bash
# ============================================================================
# 支付欺诈检测 · 一键部署脚本
# 步骤：
#   1. 上传 embeddings 数据资产到 OBS
#   2. 构建并推送镜像到 SWR（postgres-pgvector / anomaly-detector / data-collector）
#   3. 渲染 k8s manifest 并 apply 到 CCE 集群
# 前置：已执行 terraform apply 创建基础设施；已配置 kubectl 连接 CCE。
# ============================================================================

set -euo pipefail

# ---------- 配置（可通过环境变量覆盖） ----------
REGION="${REGION:-cn-north-4}"
OBS_BUCKET="${OBS_BUCKET:-}"
SWR_ORG="${SWR_ORG:-spf}"
SWR_ENDPOINT="${SWR_ENDPOINT:-swr.${REGION}.myhuaweicloud.com}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-${SWR_ENDPOINT}/${SWR_ORG}/postgres-pgvector:latest}"
ANOMALY_IMAGE="${ANOMALY_IMAGE:-${SWR_ENDPOINT}/${SWR_ORG}/anomaly-detector:latest}"
COLLECTOR_IMAGE="${COLLECTOR_IMAGE:-${SWR_ENDPOINT}/${SWR_ORG}/data-collector:latest}"
DB_PASSWORD="${DB_PASSWORD:-}"
NAMESPACE="spf-app"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------- 校验 ----------
if [ -z "${OBS_BUCKET}" ]; then
  echo "[ERROR] OBS_BUCKET 未设置（terraform output obs_bucket 获取）"
  exit 1
fi
if [ -z "${DB_PASSWORD}" ]; then
  echo "[ERROR] DB_PASSWORD 未设置（terraform output db_password 获取）"
  exit 1
fi
if [ -z "${HW_ACCESS_KEY:-}" ] || [ -z "${HW_SECRET_KEY:-}" ]; then
  echo "[ERROR] 需要 HW_ACCESS_KEY / HW_SECRET_KEY 环境变量（SWR 登录与 OBS 上传）"
  exit 1
fi

# ---------- 1. 上传 embeddings 数据资产到 OBS ----------
echo "[1/4] 上传 embeddings 数据到 OBS://${OBS_BUCKET}/embeddings/"
if ! command -v obsutil >/dev/null 2>&1; then
  echo "[WARN] 未检测到 obsutil，跳过自动上传。请手动执行："
  echo "  obsutil cp ${ROOT_DIR}/data/embeddings/ obs://${OBS_BUCKET}/embeddings/ -f -r"
else
  obsutil cp "${ROOT_DIR}/data/embeddings/" "obs://${OBS_BUCKET}/embeddings/" -f -r
fi

# ---------- 2. 登录 SWR 并推送镜像 ----------
echo "[2/4] 登录 SWR 并推送镜像"
SWR_LOGIN_CMD=$(huaweicloud_swr_login 2>/dev/null || echo "")
if [ -z "${SWR_LOGIN_CMD}" ]; then
  # 通过 huaweicloud CLI 获取登录指令
  if command -v huaweicloud >/dev/null 2>&1; then
    SWR_LOGIN_CMD=$(huaweicloud swr login 2>/dev/null | tail -1 || true)
  fi
fi
if [ -n "${SWR_LOGIN_CMD}" ]; then
  eval "${SWR_LOGIN_CMD}"
else
  echo "[WARN] 未获取到 SWR 登录指令，跳过 docker push（请手动执行 docker login ${SWR_ENDPOINT}）"
fi

# Docker 29+ BuildKit 默认生成 OCI manifest list（含 provenance），SWR 会拒收（Invalid image, fail to parse 'manifest.json'）。
# 必须加 --provenance=false --sbom=false 生成 Docker schema2 单 manifest。
docker build --provenance=false --sbom=false -t "${POSTGRES_IMAGE}" "${ROOT_DIR}/src/app/postgres"
docker build --provenance=false --sbom=false -t "${ANOMALY_IMAGE}" "${ROOT_DIR}/src/app/anomaly-detector"
docker build --provenance=false --sbom=false -t "${COLLECTOR_IMAGE}" "${ROOT_DIR}/src/app/data-collector"

docker push "${POSTGRES_IMAGE}" || echo "[WARN] push 失败（未登录 SWR？）"
docker push "${ANOMALY_IMAGE}" || echo "[WARN] push 失败（未登录 SWR？）"
docker push "${COLLECTOR_IMAGE}" || echo "[WARN] push 失败（未登录 SWR？）"

# ---------- 3. 渲染 k8s manifest ----------
echo "[3/4] 渲染 k8s manifest"
RENDER_DIR="${ROOT_DIR}/src/manifests/rendered"
mkdir -p "${RENDER_DIR}"

render() {
  local src="$1" dst="$2"
  sed -e "s#REPLACE_POSTGRES_IMAGE#${POSTGRES_IMAGE}#g" \
      -e "s#REPLACE_ANOMALY_IMAGE#${ANOMALY_IMAGE}#g" \
      -e "s#REPLACE_COLLECTOR_IMAGE#${COLLECTOR_IMAGE}#g" \
      -e "s#REPLACE_OBS_BUCKET#${OBS_BUCKET}#g" \
      -e "s#REPLACE_DB_PASSWORD#${DB_PASSWORD}#g" \
      "${src}" > "${dst}"
  echo "  rendered ${dst}"
}

render "${ROOT_DIR}/src/manifests/postgres.yaml"        "${RENDER_DIR}/postgres.yaml"
render "${ROOT_DIR}/src/manifests/data-collector.yaml"  "${RENDER_DIR}/data-collector.yaml"
render "${ROOT_DIR}/src/manifests/anomaly-detector.yaml" "${RENDER_DIR}/anomaly-detector.yaml"

# obs-creds Secret（AK/SK 由部署者在本地配置，明文不落盘）
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "${NAMESPACE}" delete secret obs-creds --ignore-not-found >/dev/null
kubectl -n "${NAMESPACE}" create secret generic obs-creds \
  --from-literal=access_key="${HW_ACCESS_KEY}" \
  --from-literal=secret_key="${HW_SECRET_KEY}"

# ---------- 4. 应用到集群 ----------
echo "[4/4] 部署到 CCE 集群"
kubectl apply -f "${RENDER_DIR}/postgres.yaml"
kubectl apply -f "${RENDER_DIR}/anomaly-detector.yaml"
kubectl apply -f "${RENDER_DIR}/data-collector.yaml"

echo ""
echo "部署完成。查看状态："
echo "  kubectl -n ${NAMESPACE} get pods"
echo "  kubectl -n ${NAMESPACE} get cronjob"
echo "  kubectl -n ${NAMESPACE} logs deploy/anomaly-detector"
