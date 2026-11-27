# 部署指导书（Deployment Guide）

本指导书面向具备华为云账号与基础 CLI 技能的实施人员，按步骤完成「支付欺诈检测」方案的完整部署。全程约需 30~60 分钟（含 CCE 集群创建等待）。

## 1. 前置准备

### 1.1 账号与权限

| 项 | 要求 |
|---|---|
| 账号 | 华为云账号，建议使用子账号并具备以下服务权限 |
| 权限 | VPC / CCE / OBS / SWR / IAM 委托 的创建与操作权限 |
| 区域 | `cn-north-4`（北京四） |

### 1.2 本地工具

| 工具 | 版本要求 | 用途 |
|---|---|---|
| Terraform | ≥ 1.5 | 创建云基础设施 |
| kubectl | ≥ 1.26 | 操作 CCE 集群 |
| docker | ≥ 20.10 | 构建镜像 |
| obsutil | 最新 | 上传数据资产到 OBS |
| jq | ≥ 1.6 | 解析输出 |

### 1.3 凭据

```bash
# AK/SK（仅注入环境变量，不写入任何文件）
export HW_ACCESS_KEY="<AK>"
export HW_SECRET_KEY="<SK>"
```

> 凭据建议使用最小权限：CCE FullAccess、OBS OperateAccess、SWR Admin、VPC FullAccess、IAM Agency。

## 2. 创建云基础设施

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars

# 编辑 terraform.tfvars：修改 obs_bucket_name 为全局唯一桶名；
# 节点认证二选一（建议 public_key）
vi terraform.tfvars

terraform init
terraform plan
terraform apply
```

**预期输出**（摘要）：

```
Apply complete! Resources: 15 added, 0 changed, 0 destroyed.

Outputs:
cluster_name     = "spf-cce-cluster"
cluster_endpoint = "https://..."
obs_bucket       = "spf-runtime-cn-north-4-xxxx"
db_password      = <sensitive>
node_pool_id     = "xxxx"
```

记录以下值（后续步骤使用）：

```bash
export OBS_BUCKET=$(terraform output -raw obs_bucket)
export DB_PASSWORD=$(terraform output -raw db_password)
export CLUSTER_NAME=$(terraform output -raw cluster_name)
```

> 注意：CCE 集群创建与节点池初始化约需 5~10 分钟，`terraform apply` 会等待节点就绪。

## 3. 连接 CCE 集群

方式一（控制台）：CCE 控制台 → 集群 → 连接信息 → 下载 kubeconfig → `export KUBECONFIG=<文件路径>`。

方式二（命令行）：

```bash
# 使用华为云 CLI 获取 kubeconfig（按 CLI 文档操作）
kubectl get nodes
# 预期：2 个节点 Ready（c7.large.2）
```

## 4. 上传数据资产

embeddings CSV（约 1.7GB）存放于本仓库 `data/embeddings/`，需上传到 OBS 桶：

```bash
obsutil cp data/embeddings/ obs://${OBS_BUCKET}/embeddings/ -f -r
# 预期输出：4 个文件全部 upload success
```

> 若未安装 obsutil，也可通过 OBS 控制台将 `data/embeddings/` 下 4 个 CSV 上传至桶内 `embeddings/` 目录。

## 5. 构建镜像并部署应用

```bash
# 登录 SWR（获取登录指令后执行）
# 方式一：华为云 CLI
#   huaweicloud swr login
# 方式二：控制台 → SWR → 右上角「登录指令」复制执行

# 一键部署（自动完成：构建 3 个镜像 → 推送 SWR → 渲染 manifest → kubectl apply）
bash src/scripts/deploy.sh
```

> `deploy.sh` 需要环境变量：`OBS_BUCKET`、`DB_PASSWORD`、`HW_ACCESS_KEY`、`HW_SECRET_KEY`（见第 2、1.3 节）。

## 6. 验证部署

详见仓库根目录 `README.md`「使用方法/验证」章节。核心检查：

```bash
kubectl -n spf-app get pods
# 预期：postgres-0 1/1 Running；anomaly-detector-xxx 1/1 Running；无 CrashLoopBackOff

kubectl -n spf-app logs -l app=anomaly-detector-app -c anomaly-detector-init --tail=30
# 预期：Database created / Creating tables and inserting data... / Success!
```

## 7. 故障排查速查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 节点无法拉取镜像 | SWR 未登录或私仓无凭据 | 确认 docker login SWR 成功；检查节点 IAM 委托含 SWR Admin |
| init 容器报 OBS 下载失败 | 桶名错误 / 数据未上传 / 无 OBS 权限 | `obsutil ls obs://<桶>/embeddings/` 确认文件存在；检查节点委托含 OBS OperateAccess |
| 检测器连不上数据库 | 数据库未就绪 / 密码不一致 | `kubectl -n spf-app get pods` 确认 postgres-0 Running；确认 DB_PASSWORD 与 manifest 一致 |
| 相似度查询报维度错误 | embeddings 维度 ≠ 847 | 确认 `vector(847)` 与特征拼接维度一致（正常应为 847） |
| 镜像构建网络超时 | 基础镜像拉取失败 | 确认 Docker 可访问镜像仓库；可改用已同步到本区域 SWR 的基础镜像 |
