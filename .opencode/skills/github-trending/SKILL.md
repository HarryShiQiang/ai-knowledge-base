---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# GitHub Trending 采集

## 使用场景

- 用户要求采集 GitHub Trending 上的热门项目
- 需要获取 AI/LLM/Agent 领域的开源项目动态
- 定期（每日/每周）拉取 GitHub 趋势数据

## 执行步骤

### 第 1 步：搜索热门仓库

使用 WebFetch 工具获取 GitHub Trending 页面，根据用户需求选择时间范围：

| 用户表述 | URL |
|----------|-----|
| 今日/今天/daily | `https://github.com/trending?since=daily` |
| 本周/这周/weekly | `https://github.com/trending?since=weekly` |
| 本月/这个月/monthly | `https://github.com/trending?since=monthly` |

优先使用 markdown 格式抓取以降低噪音。如页面内容不完整，可尝试 GitHub API：

```
https://api.github.com/search/repositories?q=ai+language:python&sort=stars&order=desc&per_page=30
```

### 第 2 步：提取信息

从页面中提取每条仓库的以下字段：

| 字段 | 来源 | 提取方法 |
|------|------|----------|
| `name` | 仓库标题 | `owner/repo` 格式，去除多余空白 |
| `url` | 链接地址 | 补全为 `https://github.com/owner/repo` |
| `stars` | 星数文本 | 解析数字（如 "1,234" → 1234） |
| `language` | 编程语言 | 页面标注的主要语言 |
| `description` | 仓库描述 | 去除特殊字符，保留原文 |

### 第 3 步：过滤

**纳入标准**（包含以下任一关键词即为相关）：

AI、LLM、Agent、大模型、推理、RAG、向量、Embedding、Fine-tune、Prompt、Transformer、多模态、工具调用、函数调用、模型训练、模型部署、模型推理、开源模型、安全对齐、Vision、TTS、STT、Token、Context、MCP、Skill

**排除标准**（以下类型直接丢弃）：

- Awesome 列表类仓库（标题含 "awesome"、"awesome-list"）
- 纯界面工具、UI 框架（非 Agent/LLM 相关）
- 通用 SaaS 工具、DevOps 工具（非 AI 领域）
- 游戏引擎、加密货币/区块链
- 电子书、课程资料合集
- 仅配置文件、dotfiles

### 第 4 步：去重

- 以 `url` 为唯一键，同一条目只保留一次
- 读取 `knowledge/raw/github-trending-*.json` 已有文件
- 若 `url` 已在最近 24 小时内的采集记录中出现，则跳过该条目
- 若 `url` 在更早记录中出现但 stars 数变化显著（>20%），保留为新条目并标注

### 第 5 步：撰写中文摘要

每条条目生成一段 ≤100 字的中文摘要，使用以下公式：

```
项目名 + 是什么（一句话说清功能） + 为什么值得关注（技术亮点/实用性/社区热度）
```

示例：

> "LangChain 是一个用于构建 LLM 驱动应用的开源框架，提供链式调用、Agent 编排和 RAG 支持，是目前最流行的 LLM 应用开发工具之一，GitHub 星数超 90k。"

要求：
- 基于页面描述撰写，不编造技术细节
- 使用中文而非直译英文描述
- 突出 AI/LLM/Agent 相关的技术价值

### 第 6 步：排序取 Top 15

- 按 `stars` 降序排列
- 同分按 `name` 字母序
- 取前 15 条（若符合条件的条目不足 15 条，则全部保留并标注实际数量）
- 检查条目是否来自 GitHub Trending 页面而非 API 搜索结果（避免混入历史热门项目）

### 第 7 步：输出 JSON

将结果写入 `knowledge/raw/github-trending-YYYY-MM-DD.json`：

```
knowledge/raw/github-trending-2026-06-13.json
```

日期使用当天日期，格式 `YYYY-MM-DD`。

## 注意事项

1. **页面动态加载**：GitHub Trending 使用 JavaScript 动态渲染，WebFetch 可能只能获取首屏 ~20 条。如需更多数据，使用 GitHub Search API 作为补充。
2. **频率控制**：同一 URL 24 小时内不重复采集。如检测到 24 小时内有相同 URL 的旧文件，仅在新 stars 变化 >20% 时更新。
3. **API 限流**：GitHub API 未认证请求限流 60 次/小时。达到上限后应优雅降级，仅使用 WebFetch 数据。
4. **输出不覆盖**：写入前检查目标路径是否存在同名文件。若存在，追加时间戳后缀（如 `-2`）而非覆盖。
5. **摘要质量**：优先从仓库 README 前 200 字提取描述，而非仅依赖 Trending 页面的短描述。
6. **Tags 提取**：从仓库的 GitHub Topics 标签中提取，仅保留 AI/LLM 相关标签。

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-06-13T12:00:00Z",
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "中文摘要，≤100 字，说明项目是什么以及为什么值得关注",
      "stars": 12345,
      "language": "Python",
      "topics": ["llm", "agent", "open-source"]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | string | 是 | 固定值 `github_trending` |
| `skill` | string | 是 | 固定值 `github-trending` |
| `collected_at` | string | 是 | 采集时间（ISO 8601） |
| `items` | array | 是 | 项目列表 |
| `items[].name` | string | 是 | `owner/repo` 格式 |
| `items[].url` | string | 是 | 完整 GitHub 仓库链接 |
| `items[].summary` | string | 是 | 中文摘要，≤100 字 |
| `items[].stars` | number | 是 | 总星数 |
| `items[].language` | string | 否 | 主要编程语言 |
| `items[].topics` | string[] | 否 | AI/LLM 相关标签 |
