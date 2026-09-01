<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/public/brand/source/hyperfilelens-lockup-on-dark.png">
  <img alt="HyperFileLens" src="website/public/brand/source/hyperfilelens-lockup-on-light.png" width="320">
</picture>

[English](README.md) | 中文

**你的备份，知道的比你想象的更多。**

开源备份与恢复产品，提供基于备份快照的 AI 智能洞察。

[产品主页](https://hyperfilelens.com/zh/) · [文档](https://hyperfilelens.com/zh/docs/) · [免费试用](https://app.hyperfilelens.com/) · [版本发布](https://github.com/oneprolabs/hyperfilelens/releases)

</div>

HyperFileLens 可以保护 Windows、Linux 和 macOS 主机上的文件，将数据保存为隔离的时间点快照，在需要时恢复文件和目录，并基于快照开展智能洞察，无需让 AI 直接访问生产环境。

<p align="center">
  <img src="website/public/product-overview.webp" alt="HyperFileLens 产品概览" width="960">
</p>

## 从备份到智能洞察

HyperFileLens 以备份快照贯通备份、恢复与智能洞察。备份任务将指定文件写入目标存储并生成时间点快照；同一份快照既可以用于浏览和恢复文件，也可以作为智能洞察的数据来源。

<p align="center">
  <img src="website/public/how-it-works.webp" alt="HyperFileLens 从文件备份到智能洞察的工作原理" width="960">
</p>

智能洞察只处理用户从指定快照中选择的数据范围，不直接读取备份主机上的实时文件。

## 核心业务流程

### 创建备份

连接备份主机和目标存储，选择需要保护的文件或目录，并创建可浏览的时间点快照。

- 保护 Windows、Linux 和 macOS 主机上的本地文件。
- 支持对象存储、NAS 或通过 Proxy 连接的本地存储。
- 手动运行备份，或通过策略安排周期性备份与保留规则。

### 恢复文件和目录

浏览指定快照中的文件和目录，将选中的内容恢复到原位置或其他可用位置，并通过恢复测试验证关键数据和恢复流程。

### 基于快照进行智能洞察

从已有备份快照中选择文件或目录，通过智能洞察查找、总结和分析内容。每个会话都限定在选定的快照和数据范围内，并通过 Data Gateway 访问这些数据。

## 为什么选择 HyperFileLens

- **备份、恢复和洞察一体化**：使用同一份备份快照完成数据恢复与内容分析。
- **基于隔离快照进行洞察**：智能洞察处理用户选择的快照数据，而不是生产主机上的实时文件。
- **灵活的使用方式**：可以直接使用官方 SaaS，也可以在自有环境部署社区版。

## 产品界面

备份完成后，HyperFileLens 会生成可浏览和恢复的时间点快照。

<p align="center">
  <img src="website/public/docs/getting-started/backup-succeeded.png" alt="HyperFileLens 中已成功完成的备份" width="960">
</p>

智能洞察基于会话中选择的快照数据回答问题，并展示相关来源。

<p align="center">
  <img src="website/public/docs/getting-started/chat-answer.png" alt="HyperFileLens 智能洞察基于快照数据生成回答" width="960">
</p>

## 快速开始

### 使用官方 SaaS

HyperFileLens 官方 SaaS 由 OnePro Cloud 提供和运营，无需自行部署和维护控制台。

- 访问 [HyperFileLens 产品主页](https://hyperfilelens.com/zh/)，了解产品。
- 打开 [SaaS 控制台](https://app.hyperfilelens.com/)，开始使用。
- 按照[首次使用指南](https://hyperfilelens.com/zh/docs/)，完成首次备份、恢复和智能洞察。

使用 SaaS 时，仍需在需要保护的主机上安装 Agent，并准备一处 SaaS 和备份主机均可连接的对象存储。

### 安装社区版

社区版可部署在自有 Ubuntu 主机上。安装主机需要满足以下基本条件：

- Ubuntu 20.04、22.04 或 24.04，amd64 架构。
- 至少 2 核 CPU、4 GiB 内存，以及 `/opt` 所在磁盘 20 GiB 可用空间。
- Docker Engine 24.0.0 及以上版本、Docker Compose V2 2.20.0 及以上版本，并且 Docker daemon 正常运行。如果主机完全没有安装 Docker，在线安装程序可以通过所选区域的软件源安装 Docker CE 和 Compose V2。
- 已安装 `curl` 和 Python 3，具备 `sudo` 权限，并可访问 Gitee、镜像仓库、所选 Docker CE 软件源和 Ubuntu 软件源。

已有 Docker 符合上述要求时始终优先复用。如果版本过低、缺少 Compose V2 或 daemon 不可用，需要先手动修复或升级；安装程序不会替换或修复已有 Docker。卸载 HyperFileLens 不会卸载 Docker、Compose 或 containerd。

在准备好的主机上执行：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn
```

安装完成后，在安装结果中找到标记为 `Tenant` 的完整地址，并在浏览器中打开 HyperFileLens 控制台。

详细的系统要求、网络条件和安装检查，请参阅[安装社区版](https://hyperfilelens.com/zh/docs/getting-started/install)。

## 完成首次使用

按照首次使用指南，用一组测试文件走通从数据接入到备份、恢复和智能洞察的完整流程：

1. [登录控制台](https://hyperfilelens.com/zh/docs/getting-started/sign-in)。
2. [添加备份源](https://hyperfilelens.com/zh/docs/getting-started/add-source)，并在备份主机上安装 Agent。
3. [配置备份源](https://hyperfilelens.com/zh/docs/getting-started/configure-source)，选择需要保护的文件或目录。
4. [添加目标存储](https://hyperfilelens.com/zh/docs/getting-started/add-target)。
5. [创建并运行首次备份](https://hyperfilelens.com/zh/docs/getting-started/first-backup)。
6. [检查任务与快照](https://hyperfilelens.com/zh/docs/getting-started/verify-backup)。
7. [恢复测试文件](https://hyperfilelens.com/zh/docs/getting-started/first-restore)。
8. [创建智能洞察会话](https://hyperfilelens.com/zh/docs/getting-started/first-insight)。

## 产品工作方式

| 组成部分 | 产品职责 |
| --- | --- |
| HyperFileLens 控制台 | 管理备份源、目标存储、备份配置、任务、快照、恢复和智能洞察 |
| Agent | 运行在备份主机上，访问本机文件并执行备份与恢复任务 |
| Proxy | 连接 NAS 或所在主机的本地存储，为备份和恢复提供存储访问 |
| Data Gateway | 连接备份仓库，为智能洞察会话提供用户选择的快照数据 |

默认情况下，官方 SaaS 提供公共 Data Gateway，社区版在安装时也会部署公共 Data Gateway。当公共网关无法连接备份仓库，或数据处理必须保留在自有网络中时，可以部署 Private Data Gateway。

## 支持范围

### 备份主机

- Linux amd64/arm64
- macOS amd64/arm64
- Windows amd64

### 目标存储

- Amazon S3
- 阿里云 OSS
- 华为云 OBS
- 通用 S3 兼容对象存储
- 通过 Proxy 连接的 NAS 或本地存储

### 社区版控制平面

- Ubuntu 20.04、22.04 或 24.04，amd64
- Docker Engine 24.0.0 及以上版本
- Docker Compose V2 2.20.0 及以上版本

更完整的产品边界请查看[支持范围](https://hyperfilelens.com/zh/docs/reference/support-matrix)与[限制和安全建议](https://hyperfilelens.com/zh/docs/reference/limitations-security)。

## 文档

- [快速开始](https://hyperfilelens.com/zh/docs/)
- [产品使用](https://hyperfilelens.com/zh/docs/product/)
- [备份与恢复](https://hyperfilelens.com/zh/docs/backup-restore/)
- [智能洞察](https://hyperfilelens.com/zh/docs/insights/)
- [部署运维](https://hyperfilelens.com/zh/docs/deployment/)
- [帮助中心](https://hyperfilelens.com/zh/docs/help/)

## 项目状态

HyperFileLens 目前处于公开测试阶段。在首个稳定版本发布前，部分界面、配置和发行方式仍可能调整。

## 开发环境

在仓库根目录运行以下命令，启动完整的热更新开发环境：

```bash
./dev/stack.sh up
```

首次启动会准备依赖、构建 Agent 包并启动后端、前端、数据库、缓存、网关和智能洞察服务，因此可能需要几分钟。

默认访问地址：

| 服务 | 地址 |
| --- | --- |
| 产品网站 | `https://localhost:11442/` |
| 租户控制台 | `https://localhost:11443/` |
| 平台运维控制台 | `https://localhost:11444/` |
| 智能洞察控制台 | `https://localhost:11445/` |
| OpenAPI | `https://localhost:11443/swagger` |

常用命令：

```bash
./dev/stack.sh status
./dev/stack.sh restart
./dev/stack.sh doctor
./dev/stack.sh smoke
./dev/stack.sh down
```

更多开发和构建选项，请使用相应仓库脚本的 `--help` 参数查看。

## 技术架构

| 组成部分 | 主要技术 | 职责 |
| --- | --- | --- |
| 后端 | Python、Django、DRF、Channels、Celery | API、身份认证、任务调度和业务编排 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus | 租户控制台与平台运维控制台 |
| Agent | Go、Kopia | 文件访问、备份、快照和恢复执行 |
| 网关 | Nginx | 网站、控制台、API 和 WebSocket 的 HTTPS 入口 |
| 数据服务 | PostgreSQL、Redis | 业务数据、缓存、消息和异步任务状态 |
| 智能洞察 | SourceLens | 快照数据准备、检索与分析 |

### 仓库结构

```text
hyperfilelens/
├── deploy/              运行环境、Nginx、引导程序和安装资源
├── dev/                 本地开发入口
├── release/             离线发行包构建入口
├── src/
│   ├── agent/           Go Agent 源码与打包模板
│   ├── backend/         Django 后端源码
│   └── frontend/        Vue 前端源码
├── tools/               构建、依赖、质量检查和发布工具
├── website/             产品网站与中英文文档
├── .env.example         环境配置模板
└── docker-compose.yml   本地开发服务编排
```

## 质量检查

根据修改范围运行相应检查：

```bash
# 后端
docker compose exec worker python manage.py test

# 前端
docker compose exec web npm run lint
docker compose exec web npm run test
docker compose exec web npm run build

# Agent
cd src/agent
go test ./...
```

提交代码前还应运行仓库级质量检查：

```bash
python3 tools/quality/check-english-source.py
python3 -m unittest tools/quality/test_check_english_source.py
./tools/quality/check-release-contracts.sh
```

## 安全

- 社区版安装完成后立即修改初始密码。
- 妥善保护 `.env`、访问凭据、TLS 私钥、备份数据和运行日志。
- 仅向必要网络开放产品与组件端口，避免将管理入口直接暴露到互联网。
- 使用访问凭据和最小权限策略连接对象存储，无需将存储桶设为公开。
- 不要将部署环境的密码、令牌、私钥、运行数据或发行包提交到版本库。

## 参与贡献

欢迎参与 HyperFileLens 的开发和改进。提交 Pull Request 前请：

1. 从当前默认分支创建范围明确的开发分支。
2. 使用英文编写源代码、代码注释、提交记录和 Pull Request。
3. 为行为变更增加或更新测试。
4. 运行与修改范围对应的质量检查和构建。
5. 在 Pull Request 中说明问题、解决方案和验证结果。

## 许可证

HyperFileLens 社区版使用 [Apache License 2.0](LICENSE) 开源许可证。
