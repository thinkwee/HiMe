<p align="center">
  <img src="assets/logo.png" alt="HiMe Logo" width="500"/>
</p>

<p align="center">
  <em>HiMe — Say Hi to Healthy Me</em>
</p>

<p align="center">
  一站式个人健康 AI Agent
</p>

<p align="center">
  <a href="README.md">English</a> | <b>简体中文</b>
</p>

<p align="center">
  <a href="https://apps.apple.com/app/id6762160735"><img alt="Download on the App Store" src="https://img.shields.io/badge/App_Store-Download-0a84ff?logo=apple"></a>
  <a href="docs/DEVELOPMENT.md"><img alt="Developer docs" src="https://img.shields.io/badge/docs-development-green"></a>
  <a href="docs/INSTALL.md#im-gateway-setup"><img alt="Telegram Support" src="https://img.shields.io/badge/Telegram-supported-26A5E4?logo=telegram&logoColor=white"></a>
  <a href="docs/INSTALL.md#im-gateway-setup"><img alt="Feishu Support" src="https://img.shields.io/badge/Feishu-supported-00D6B9?logo=lark&logoColor=white"></a>
  <br/>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-blue.svg"></a>
  <a href="https://github.com/thinkwee/HiMe/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/thinkwee/HiMe/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/thinkwee/HiMe/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/thinkwee/HiMe?display_name=tag&sort=semver"></a>
  <a href="https://github.com/thinkwee/HiMe/commits/main"><img alt="Last commit" src="https://img.shields.io/github/last-commit/thinkwee/HiMe"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white">
  <img alt="Platform iOS" src="https://img.shields.io/badge/platform-iOS%20%7C%20watchOS-lightgrey?logo=apple">
  <img alt="Docker" src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white">
</p>

---

HiMe（Health Intelligence Management Engine）是一款自托管、完全本地化、安全开源的一站式个人健康 AI Agent 平台。它实时理解你的可穿戴设备健康数据，7×24 小时主动为你提供健康洞察——当然，还附带一只可爱的像素风小猫，作为你的个人健康数字分身。

## 功能特性

<p align="center">
  <a href="https://apps.apple.com/app/id6762160735"><img src="assets/hime_app.png" alt="HiMe App — 在 App Store 下载" width="900"/></a>
</p>

---

<p align="center">
  <img src="assets/hime_chat.png" alt="HiMe Chat" width="600"/>
</p>

---

<p align="center">
  <img src="assets/hime_panel.png" alt="HiMe Panel" width="600"/>
</p>

- 实时接入 Apple Watch + iPhone 的可穿戴数据，包括心率、HRV、血氧、睡眠阶段、运动、活动能力等 50+ 项指标。
- iOS 与 watchOS 配套 App，轻松同步健康数据并控制 Agent。
- 自主式 AI 分析，支持定时检查与事件触发。
- OpenClaw 风格的聊天体验，支持 Telegram 或飞书，回复均附带证据来源。
- Agent 按需生成个性化页面，用于重复工作流与个性化交互。让 Agent 为你生成应用，而不是让你学习使用应用。
- Skills 系统，复用分析 playbook。
- 强隐私的自托管部署。

## 快速开始

三步搞定，总耗时约 10 分钟。

### 1. 获取 IM 凭证

HiMe 通过 **Telegram** 或 **飞书** 与你对话。任选其一，并在启动服务前准备好凭证（setup 向导会询问）。

- **Telegram**：通过 [@BotFather](https://t.me/BotFather) 创建 bot → 保存 token。向 [@userinfobot](https://t.me/userinfobot) 发送 `/start` → 保存你的 chat_id。
- **飞书**：在 [open.feishu.cn](https://open.feishu.cn) 创建自建应用 → 获取 APP_ID + APP_SECRET。将 bot 邀请进群 → 获取 open_chat_id。

详细步骤：[`docs/INSTALL.md#im-gateway-setup`](docs/INSTALL.md#im-gateway-setup)。

### 2. 启动服务

```bash
git clone https://github.com/thinkwee/HiMe.git HiMe
cd HiMe
./setup.sh
```

<p align="center">
  <img src="assets/hime_wizard.png" alt="HiMe Wizard" width="600"/>
</p>

别担心，跟随 `setup.sh` 中的快速向导即可完成全部步骤。首次构建约 2–5 分钟。

完成后，Dashboard 在 http://localhost:5173 —— 但日常使用主要通过 iOS App。

### 3. 安装 iOS App

- **简单方式**：从 App Store 安装 [HiMe](https://apps.apple.com/app/id6762160735)。打开 设置 → Server URL → 填入你的主机地址（如 `localhost`、Mac 的局域网 IP 或 `homelab.local`）。
- **源码构建**：参见 [`docs/INSTALL.md#ios-app`](docs/INSTALL.md#ios-app)。

完成。向 bot 发一条消息，Agent 就会回复。

## 更新 HiMe

拉取最新版本并重启 —— 你的 `.env`、`data/`、`memory/` 都会被保留：

```bash
git pull
./hime.sh restart --rebuild   # Docker 模式：重新构建镜像
./hime.sh restart --clean     # 原生模式：清理 Python/node 缓存
```

如果新版本新增了环境变量，请对比 `.env.example` 与你的 `.env` 并补齐缺失项。备份建议参见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#5-upgrades-and-backups)。

---

## 文档

- [`docs/INSTALL.md`](docs/INSTALL.md) — 手动安装、原生开发环境、公网部署、自定义配置。
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — 局域网、公网与 Compose 生产环境部署方案。
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — 架构、添加工具/Provider、代码风格。
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 贡献流程。
- [`SECURITY.md`](SECURITY.md) — 安全漏洞披露。
- [`PRIVACY.md`](PRIVACY.md) — 隐私政策。

## 状态

HiMe 是面向个人使用的研究级软件。它不是医疗设备，也不提供诊断。

## 许可证

HiMe 基于 [PolyForm Noncommercial License 1.0.0](LICENSE) 发布。

## 商标

"HiMe" 及 HiMe logo 是 HiMe Organisation 的商标。
