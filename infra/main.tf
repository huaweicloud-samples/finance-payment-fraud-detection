# ============================================================================
# 支付欺诈检测 · 基础设施即代码（Terraform + huaweicloud Provider）
# ============================================================================
# 架构：CCE 集群 + 节点池 + OBS 对象存储 + SWR 镜像仓库 + PostgreSQL(pgvector)
# 组件：
#   - VPC / 子网 / 安全组
#   - CCE 集群（VirtualMachine）+ 节点池（c7.large.2）
#   - OBS 运行时数据桶
#   - SWR 镜像仓库（postgres / anomaly-detector / data-collector）
#   - IAM 委托（CCE → OBS / SWR）
#   - 可选 NAT 网关（仅当工作负载需访问公网第三方 API 时启用）
# ============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = "~> 1.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "local" {
    # 生产环境可改为 OBS 后端（endpoint/bucket/key 按需填写）
  }
}

provider "huaweicloud" {
  region = var.region
  # AK/SK 从环境变量读取：HW_ACCESS_KEY / HW_SECRET_KEY
}

provider "random" {}

# ============================================================================
# 变量
# ============================================================================

variable "region" {
  description = "华为云区域"
  type        = string
  default     = "cn-north-4"
}

variable "project_name" {
  description = "项目名称（资源命名前缀）"
  type        = string
  default     = "spf"
}

variable "cluster_name" {
  description = "CCE 集群名称"
  type        = string
  default     = "spf-cce-cluster"
}

variable "node_flavor" {
  description = "CCE 节点规格"
  type        = string
  default     = "c7.large.2"
}

variable "node_count" {
  description = "CCE 节点数量"
  type        = number
  default     = 2
}

variable "obs_bucket_name" {
  description = "OBS 运行时数据桶名（全局唯一）"
  type        = string
}

variable "docker_registry" {
  description = "SWR 镜像仓库域名"
  type        = string
  default     = "swr.cn-north-4.myhuaweicloud.com"
}

variable "swr_organization" {
  description = "SWR 组织名（镜像仓库归属组织）"
  type        = string
  default     = "spf"
}

variable "public_key" {
  description = "CCE 节点 SSH 公钥（key_pair），与 node_password 二选一"
  type        = string
  default     = ""
}

variable "node_password" {
  description = "CCE 节点 root 密码（与 public_key 二选一）"
  type        = string
  sensitive   = true
  default     = ""
}

variable "nat_enabled" {
  description = "是否创建 NAT 网关（仅当 Pod 需访问公网第三方 API 时启用）"
  type        = bool
  default     = false
}

# ============================================================================
# 网络
# ============================================================================

resource "huaweicloud_vpc" "main" {
  name = "${var.project_name}-vpc"
  cidr = "10.0.0.0/16"
}

resource "huaweicloud_vpc_subnet" "main" {
  name       = "${var.project_name}-subnet"
  vpc_id     = huaweicloud_vpc.main.id
  cidr       = "10.0.1.0/24"
  gateway_ip = "10.0.1.1"
}

# ============================================================================
# 安全组
# ============================================================================

resource "huaweicloud_networking_secgroup" "main" {
  name        = "${var.project_name}-sg"
  description = "Security group for ${var.project_name}"
}

# ============================================================================
# CCE 集群 + 节点池
# ============================================================================

# 导入节点 SSH 公钥为密钥对（key_pair 需为密钥对名称，非公钥字符串）
resource "huaweicloud_compute_keypair" "main" {
  name       = "${var.project_name}-cce-keypair"
  public_key = var.public_key
}

resource "huaweicloud_cce_cluster" "main" {
  name                   = var.cluster_name
  cluster_type           = "VirtualMachine"
  flavor_id              = "cce.s1.small"
  vpc_id                 = huaweicloud_vpc.main.id
  subnet_id              = huaweicloud_vpc_subnet.main.id
  container_network_type = "vpc-router"
  description            = "支付欺诈检测集群"
  security_group_id      = huaweicloud_networking_secgroup.main.id

  tags = {
    project = var.project_name
  }
}

resource "huaweicloud_cce_node_pool" "main" {
  cluster_id         = huaweicloud_cce_cluster.main.id
  name               = "${var.project_name}-nodepool"
  flavor_id          = var.node_flavor
  initial_node_count = var.node_count
  availability_zone  = "${var.region}a"
  os                 = "Huawei Cloud EulerOS 2.11"
  password           = var.node_password != "" ? var.node_password : null
  key_pair           = huaweicloud_compute_keypair.main.name

  root_volume {
    size       = 40
    volumetype = "GPSSD"
  }

  data_volumes {
    size       = 100
    volumetype = "GPSSD"
  }
}

# ============================================================================
# 集群公网访问 EIP（按流量计费；用于本机 kubectl 连接集群 API Server）
# ============================================================================

resource "huaweicloud_vpc_eip" "cluster_api" {
  publicip {
    type = "5_bgp"
  }
  bandwidth {
    name        = "${var.project_name}-cce-api-bw"
    size        = 5
    share_type  = "PER"
    charge_mode = "traffic"
  }
}

output "cluster_api_eip" {
  description = "CCE 集群 API 公网 EIP"
  value       = huaweicloud_vpc_eip.cluster_api.address
}

# ============================================================================
# 可选 NAT 网关（公网出网；启用前需确认）
# ============================================================================

resource "huaweicloud_vpc_eip" "nat" {
  count = var.nat_enabled ? 1 : 0

  publicip {
    type = "5_bgp"
  }
  bandwidth {
    name        = "${var.project_name}-nat-bw"
    size        = 5
    share_type  = "PER"
    charge_mode = "traffic"
  }
}

resource "huaweicloud_nat_gateway" "main" {
  count       = var.nat_enabled ? 1 : 0
  name        = "${var.project_name}-nat"
  spec        = "1"
  vpc_id      = huaweicloud_vpc.main.id
  subnet_id   = huaweicloud_vpc_subnet.main.id
  description = "NAT for public egress"
}

resource "huaweicloud_nat_snat_rule" "main" {
  count          = var.nat_enabled ? 1 : 0
  nat_gateway_id = huaweicloud_nat_gateway.main[0].id
  subnet_id      = huaweicloud_vpc_subnet.main.id
  floating_ip_id = huaweicloud_vpc_eip.nat[0].id
}

# ============================================================================
# IAM 委托（CCE 节点访问 OBS/SWR）
# ============================================================================

resource "huaweicloud_iam_agency" "cce" {
  name                  = "${var.project_name}-cce-agency"
  description           = "CCE agency for OBS/SWR access"
  delegated_domain_name = "op_svc_cce"

  project_role {
    project = var.region
    roles   = ["OBS OperateAccess", "SWR Admin"]
  }
}

# ============================================================================
# OBS 对象存储（运行时数据桶）
# ============================================================================

resource "huaweicloud_obs_bucket" "runtime" {
  bucket        = var.obs_bucket_name
  acl           = "private"
  force_destroy = true
  versioning    = false

  tags = {
    project = var.project_name
  }
}

# ============================================================================
# SWR 镜像仓库
# ============================================================================

resource "huaweicloud_swr_organization" "main" {
  name = var.swr_organization
}

resource "huaweicloud_swr_repository" "postgres" {
  organization = huaweicloud_swr_organization.main.name
  name         = "postgres-pgvector"
  description  = "PostgreSQL 16 with pgvector extension"
  is_public    = false
}

resource "huaweicloud_swr_repository" "anomaly_detector" {
  organization = huaweicloud_swr_organization.main.name
  name         = "anomaly-detector"
  description  = "Anomaly detector microservice"
  is_public    = false
}

resource "huaweicloud_swr_repository" "data_collector" {
  organization = huaweicloud_swr_organization.main.name
  name         = "data-collector"
  description  = "Data collector microservice"
  is_public    = false
}

# ============================================================================
# 数据库密码（生成后写入 k8s Secret，由部署脚本注入）
# ============================================================================

resource "random_password" "db" {
  length      = 24
  special     = false
  min_upper   = 1
  min_lower   = 1
  min_numeric = 1
}

# ============================================================================
# 输出
# ============================================================================

output "cluster_name" {
  description = "CCE 集群名称"
  value       = huaweicloud_cce_cluster.main.name
}

output "cluster_id" {
  description = "CCE 集群 ID"
  value       = huaweicloud_cce_cluster.main.id
}

output "cluster_endpoint" {
  description = "CCE 集群 API 端点"
  value       = huaweicloud_cce_cluster.main.certificate_clusters[0].server
}

output "obs_bucket" {
  description = "OBS 运行时数据桶"
  value       = huaweicloud_obs_bucket.runtime.bucket
}

output "db_password" {
  description = "数据库密码（部署时写入 k8s Secret）"
  value       = random_password.db.result
  sensitive   = true
}

output "node_pool_id" {
  description = "CCE 节点池 ID"
  value       = huaweicloud_cce_node_pool.main.id
}
