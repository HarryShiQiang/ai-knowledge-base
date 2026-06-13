# AGENTS.md — AI Knowledge Base Assistant

## 项目概述

本项目是一个 AI 驱动的知识库助手，自动从 GitHub Trending 和 Hacker News 采集 AI/LLM/Agent 领域的优秀文章与技术动态，通过 AI 分析后结构化存储为 JSON，支持多渠道（Telegram/飞书）推送分发，帮助团队持续跟踪前沿技术趋势。

## 技术栈

| 层面 | 选型 |
|------|------|
| 运行时 | Python 3.12 |
| AI 编排 | [OpenCode](https://opencode.ai) + 国产大模型 |
| 工作流引擎 | [LangGraph](https://github.com/langchain-ai/langgraph) |
| 多渠道推送 | [OpenClaw](https://github.com/openclaw/openclaw) |

## 编码规范

- **[PEP 8](https://peps.python.org/pep-0008/)** 严格遵循 Python 代码风格
- **snake_case** 命名变量、函数、文件
- **Google 风格 docstring** 写文档注释（`"""Description.\n\nArgs:\nReturns:\n"""`）
- **禁止裸 `print()`** 输出日志，统一使用 `logging` 模块
- **类型注解** 所有函数签名必须包含类型注解

## 项目结构

```
ai-knowledge-base/
├── AGENTS.md                  # 本文件
├── .opencode/
│   ├── agents/                # OpenCode 子代理定义
│   │   ├── collector.md       # 采集代理
│   │   ├── analyzer.md        # 分析代理
│   │   └── organizer.md       # 整理代理
│   └── skills/                # OpenCode 自定义技能
├── knowledge/
│   ├── raw/                   # 原始采集数据（HTML/Markdown）
│   └── articles/              # 结构化分析结果（JSON）
├── scripts/                   # 工具脚本
└── tests/                     # 测试用例
```

## 知识条目 JSON 格式

每篇分析后的文章存储为一个 JSON 文件，字段约定如下：

```json
{
  "id": "20250613-gh-llama3-vision",
  "title": "Llama 3 Vision: Multimodal Breakthrough",
  "source_url": "https://github.com/meta-llama/llama3",
  "source_type": "github_trending",
  "summary": "Meta 发布 Llama 3 多模态版本，支持图像理解与生成。",
  "tags": ["multimodal", "llama", "open-source", "vision"],
  "chinese_summary": "Meta 发布 Llama 3 多模态版本，支持图像理解与生成。",
  "difficulty": "medium",
  "published_at": "2025-06-13T10:30:00Z",
  "fetched_at": "2025-06-13T12:00:00Z",
  "status": "published",
  "distributed_to": ["telegram", "feishu"],
  "score": 8.5
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 唯一标识，格式 `YYYYMMDD-{source}-{slug}` |
| `title` | string | 是 | 原文标题 |
| `source_url` | string | 是 | 原文链接 |
| `source_type` | string | 是 | 来源：`github_trending` / `hackernews` |
| `summary` | string | 是 | 英文摘要（≤200 字） |
| `tags` | string[] | 是 | 标签列表 |
| `chinese_summary` | string | 否 | 中文摘要（≤200 字） |
| `difficulty` | string | 否 | 难度：`beginner` / `medium` / `advanced` |
| `published_at` | string | 否 | 原文发布时间（ISO 8601） |
| `fetched_at` | string | 是 | 采集时间（ISO 8601） |
| `status` | string | 是 | `draft` / `published` / `archived` |
| `distributed_to` | string[] | 否 | 已分发渠道 |
| `score` | float | 否 | 推荐评分 0-10 |

## Agent 角色概览

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| **collector** | 从 GitHub Trending / Hacker News 抓取 AI/LLM/Agent 相关内容，去重后存入 `knowledge/raw/` | 定时触发 / 手动指令 | 原始 HTML/Markdown 文件 |
| **analyzer** | 读取 `knowledge/raw/` 中的原始内容，调用大模型生成摘要、标签、评分，输出结构化 JSON | 原始采集数据 | JSON 文件存入 `knowledge/articles/` |
| **organizer** | 管理知识库生命周期：审核待发布条目、去重合并、状态流转、多渠道分发 | 待处理 JSON 列表 | 分发到 Telegram / 飞书 |

## 红线（绝对禁止的操作）

| 序号 | 禁止行为 |
|------|----------|
| 1 | **禁止修改 `.opencode/` 下的 `package.json`、`package-lock.json`、`node_modules/`** — 这些是 opencode 运行环境，由平台自动管理 |
| 2 | **禁止在 `knowledge/raw/` 和 `knowledge/articles/` 之外写入大文件**（>10MB）— 原始网页应只存标准化精简内容 |
| 3 | **禁止使用 `print()` 输出日志** — 一律使用 `logging.getLogger(__name__)` |
| 4 | **禁止硬编码 API Key / Webhook URL** — 统一使用环境变量或 `.env` 文件 |
| 5 | **禁止直接操作 `/knowledge/articles/` 中的文件而不更新 `status` 字段** — 状态流转必须显式记录 |
| 6 | **禁止在未使用 LangGraph 定义工作流的情况下，将采集和分析混在同一个脚本中** — 每个 Agent 职责独立 |
| 7 | **禁止提交包含实际 API Key、Token 或敏感 URL 的代码到 Git** — 使用 `.gitignore` 排除 `.env` |
| 8 | **禁止对同一 URL 在 `fetched_at` 24 小时内重复采集** — 采集前必须先检查去重 |
