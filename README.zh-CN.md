<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/public/brand/source/hyperfilelens-lockup-on-dark.png">
  <img alt="HyperFileLens" src="website/public/brand/images/hyperfilelens-lockup-transparent-on-light.png" width="320">
</picture>

[English](README.md) | 中文

**你的备份，藏着意想不到的答案。**

直接对你的文档备份进行问答——PDF、Word、Excel、PPT、图片、Markdown 等任意文本格式，且不影响生产环境。

[产品主页](https://hyperfilelens.com/zh/) · [文档](https://hyperfilelens.com/zh/docs/) · [免费试用](https://app.hyperfilelens.com/) · [版本发布](https://github.com/oneprolabs/hyperfilelens/releases) · [微信交流群](#社区交流)

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-public%20beta-orange.svg)](#项目状态)

</div>

<p align="center">
  <img src="website/public/product-overview.webp" alt="HyperFileLens 产品概览" width="960">
</p>

## 使用场景

能回答你大部分问题的那些文档，其实早就躺在备份里了——技术方案、管理制度、客服资料、源代码。但备份仓库只能按文件名检索，这些知识就那么放着，用不起来。最直接的办法是让 AI 去读这些文件，而这恰恰是最不能做的：生产主机不该为了建索引被打开，把文件再复制一份到别的系统，等于多出一个需要防护的地方。

HyperFileLens 补的就是这一环。你本来就为了容灾保留的那份隔离副本，直接成为 AI Agent 推理的数据来源。

**不止是检索，更是答案。**

- **智能客服问答**——直接基于产品文档、PDF、PPT 回答客户问题。多模态识别跨格式读取，不用预先转换格式。
- **企业知识库**——把分散的技术文档、管理制度、决策记录，不管是研发的还是全公司的，整合成一个团队真正能查询的知识库，而不只是搜索和摘要。
- **基于源码的根因分析**——不是关键词匹配，也不是扫日志。Agent 会像资深工程师一样真正阅读、推理你的源代码，直到确认真正的根因。

**同时，它还是一个靠谱的备份工具。**

- **任意主机与 NAS**——Windows、Linux、macOS，服务器还是工作站都一样，再加上 NAS 共享目录，统一策略保护，不用来回切换三套工具。
- **存储不绑定**——对象存储还是本地存储，任意 S3 兼容服务商都可以，你的备份不会被绑死在某一家存储厂商身上。
- **单文件秒级恢复**——只想找回一个文件？浏览任意快照，几秒钟恢复这一个文件就够了，不需要整卷恢复。

## 工作原理

**备份掘金，治理无扰。**

HyperFileLens 把你的文档和代码备份成一份安全、隔离的副本——然后由 [SourceLens](https://github.com/oneprolabs/sourcelens)（我们的开源 Agentic RAG 引擎）直接对这份副本进行推理，全程不碰生产环境。它不需要预先建立索引：Agent 会像人一样去阅读、检索和浏览快照。

<p align="center">
  <img src="website/public/how-it-works.webp" alt="HyperFileLens 从文件备份到智能洞察的工作原理" width="960">
</p>

备份任务将指定文件写入目标存储并生成时间点快照；同一份快照既可以用于浏览和恢复文件，也可以作为智能洞察的数据来源。智能洞察只处理用户从指定快照中选择的数据范围，不会读取备份主机上的实时文件。

备份完成后，快照可浏览、可恢复：

<p align="center">
  <img src="website/public/docs/getting-started/backup-succeeded.png" alt="HyperFileLens 中已成功完成的备份" width="960">
</p>

智能洞察基于会话中选择的快照数据回答问题，并展示相关来源：

<p align="center">
  <img src="website/public/docs/getting-started/chat-answer.png" alt="HyperFileLens 智能洞察基于快照数据生成回答" width="960">
</p>

## 技术架构

| 组成部分 | 主要技术 | 职责 |
| --- | --- | --- |
| 控制台 | Vue 3、TypeScript、Vite、Element Plus | 管理备份源、目标存储、备份配置、任务、快照、恢复和智能洞察 |
| 后端 | Python、Django、DRF、Channels、Celery | API、身份认证、任务调度和业务编排 |
| Agent | Go、Kopia | 运行在备份主机上，访问本机文件并执行备份与恢复任务 |
| Proxy | Go（Agent 角色） | 连接 NAS 或所在主机的本地存储，为备份和恢复提供存储访问 |
| Data Gateway | Go（Agent 角色） | 连接备份仓库，为智能洞察会话提供用户选择的快照数据 |
| 智能洞察引擎 | [SourceLens](https://github.com/oneprolabs/sourcelens) | 快照数据准备、检索与 Agentic 分析 |
| 网关 | Nginx | 网站、控制台、API 和 WebSocket 的 HTTPS 入口 |
| 数据服务 | PostgreSQL、Redis | 业务数据、缓存、消息和异步任务状态 |

默认情况下，官方 SaaS 提供公共 Data Gateway，社区版在安装时也会部署公共 Data Gateway。当公共网关无法连接备份仓库，或数据处理必须保留在自有网络中时，可以部署 Private Data Gateway。

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

## 快速开始

### 安装社区版

社区版部署在自有 Ubuntu 主机上：

- Ubuntu 20.04、22.04 或 24.04，amd64 架构。
- 至少 4 核 CPU、8 GiB 内存，以及 `/opt` 所在磁盘 20 GiB 可用空间；正式使用建议 8 核 CPU 和 16 GiB 内存。
- Docker Engine 24.0.0+ 和 Docker Compose V2 2.20.0+，且 Docker daemon 正常运行。主机没有 Docker 时安装程序可以自动安装 Docker CE 和 Compose V2，已有的健康 Docker CE 运行时始终优先复用。
- 已安装 `curl` 和 Python 3，具备 `sudo` 权限，并可访问 Gitee、镜像仓库和 Ubuntu 软件源。

在准备好的主机上执行：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn
```

安装完成后，在安装结果中找到标记为 `Tenant` 的完整地址，在浏览器中打开即可进入控制台。详细的系统要求、网络条件、Docker 运行时规则和安装检查，请参阅[安装社区版](https://hyperfilelens.com/zh/docs/getting-started/install)。

### 使用官方 SaaS

HyperFileLens 官方 SaaS 由 OnePro Cloud 提供和运营，无需自行部署和维护控制台，打开 [SaaS 控制台](https://app.hyperfilelens.com/)即可开始使用。仍需在需要保护的主机上安装 Agent，并准备一处 SaaS 和备份主机均可连接的对象存储。

### 完成首次使用

不管用哪种方式，都可以按[首次使用指南](https://hyperfilelens.com/zh/docs/)用一组测试文件走通完整流程：[登录控制台](https://hyperfilelens.com/zh/docs/getting-started/sign-in) →
[添加备份源](https://hyperfilelens.com/zh/docs/getting-started/add-source) →
[配置备份源](https://hyperfilelens.com/zh/docs/getting-started/configure-source) →
[添加目标存储](https://hyperfilelens.com/zh/docs/getting-started/add-target) →
[运行首次备份](https://hyperfilelens.com/zh/docs/getting-started/first-backup) →
[检查快照](https://hyperfilelens.com/zh/docs/getting-started/verify-backup) →
[恢复测试文件](https://hyperfilelens.com/zh/docs/getting-started/first-restore) →
[创建智能洞察会话](https://hyperfilelens.com/zh/docs/getting-started/first-insight)。

### 支持范围

| | 支持情况 |
| --- | --- |
| 备份主机 | Linux amd64/arm64、macOS amd64/arm64、Windows amd64 |
| 目标存储 | Amazon S3、阿里云 OSS、华为云 OBS、通用 S3 兼容对象存储、通过 Proxy 连接的 NAS 或本地存储 |
| 社区版控制平面 | Ubuntu 20.04/22.04/24.04 amd64、Docker Engine 24.0.0+、Docker Compose V2 2.20.0+ |

更完整的产品边界请查看[支持范围](https://hyperfilelens.com/zh/docs/reference/support-matrix)与[限制和安全建议](https://hyperfilelens.com/zh/docs/reference/limitations-security)。

### 安全运行

- 社区版安装完成后立即修改初始密码。
- 妥善保护 `.env`、访问凭据、TLS 私钥、备份数据和运行日志。
- 仅向必要网络开放产品与组件端口，避免将管理入口直接暴露到互联网。
- 使用访问凭据和最小权限策略连接对象存储，无需将存储桶设为公开。

## 开发

开发环境是一套基于 Docker Compose 的热更新栈。在仓库根目录执行：

```bash
./dev/stack.sh up
```

首次启动会准备依赖、构建 Agent 包并启动后端、前端、数据库、缓存、网关和智能洞察服务，可能需要几分钟。

| 服务 | 地址 |
| --- | --- |
| 产品网站 | `https://localhost:11442/` |
| 租户控制台 | `https://localhost:11443/` |
| 平台运维控制台 | `https://localhost:11444/` |
| 智能洞察控制台 | `https://localhost:11445/` |
| OpenAPI | `https://localhost:11443/swagger` |

```bash
./dev/stack.sh status
./dev/stack.sh restart
./dev/stack.sh doctor
./dev/stack.sh smoke
./dev/stack.sh down
```

根据修改范围运行相应检查：

```bash
# 后端
docker compose exec worker python manage.py test

# 前端
docker compose exec web npm run lint
docker compose exec web npm run test
docker compose exec web npm run build

# Agent
cd src/agent && go test ./...

# 仓库级检查，提交 Pull Request 前运行
python3 tools/quality/check-english-source.py
python3 -m unittest tools/quality/test_check_english_source.py
./tools/quality/check-release-contracts.sh
```

更多开发和构建选项，请使用相应仓库脚本的 `--help` 参数查看。

## 参与贡献

欢迎提 Pull Request，也欢迎提 Issue。发现 bug、用着别扭、或者想要某个还没有的能力，都可以[提交 Issue](https://github.com/oneprolabs/hyperfilelens/issues)，这是找到我们最快的方式。

提交 Pull Request 前请：

1. 从当前默认分支创建范围明确的开发分支。
2. 使用英文编写源代码、代码注释、提交记录和 Pull Request。
3. 为行为变更增加或更新测试。
4. 运行与修改范围对应的质量检查和构建。
5. 在 Pull Request 中说明问题、解决方案和验证结果。

## 社区交流

扫码加入 oneprolabs 开源交流群，和我们聊聊使用问题、需求和进展：

<p align="center">
  <img src="website/public/community/wechat-group.png" alt="oneprolabs 开源交流群微信二维码" width="240">
</p>

> 群二维码有效期有限，过期后可以关注 [X（Twitter）@oneprolabs](https://x.com/oneprolabs) 或[提交 Issue](https://github.com/oneprolabs/hyperfilelens/issues) 联系我们更新。

英文用户可以关注 [X（Twitter）@oneprolabs](https://x.com/oneprolabs)，获取版本发布和开发进展。

## 文档

- [快速开始](https://hyperfilelens.com/zh/docs/)
- [产品使用](https://hyperfilelens.com/zh/docs/product/)
- [备份与恢复](https://hyperfilelens.com/zh/docs/backup-restore/)
- [智能洞察](https://hyperfilelens.com/zh/docs/insights/)
- [部署运维](https://hyperfilelens.com/zh/docs/deployment/)
- [帮助中心](https://hyperfilelens.com/zh/docs/help/)

## 项目状态

HyperFileLens 目前处于公开测试阶段。在首个稳定版本发布前，部分界面、配置和发行方式仍可能调整。

## 许可证

HyperFileLens 社区版使用 [Apache License 2.0](LICENSE) 开源许可证。
