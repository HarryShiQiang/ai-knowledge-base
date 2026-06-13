---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# 技术深度分析

## 使用场景

- 用户要求对 `knowledge/raw/` 中的采集数据进行深度分析
- 需要生成英文摘要、中文摘要、亮点、评分和标签
- 需要发现多条条目之间的共同趋势和新兴概念

## 执行步骤

### 第 1 步：读取最新采集数据

使用 Read 工具读取 `knowledge/raw/` 目录下最新日期的采集文件：

```
# 通过 Glob 列出所有 raw 文件
knowledge/raw/github-trending-*.json
knowledge/raw/hackernews-*.json

# 按日期排序，取最新的文件
# 使用 Read 读取完整内容
```

若最新文件中无有效条目（空数组或文件不存在），则向上查找次新文件，最多回溯 3 天。

### 第 2 步：逐条深度分析

对每条条目进行四维度分析：

#### 2.1 双语文摘

| 类型 | 字段 | 字数 | 要求 |
|------|------|------|------|
| 英文摘要 | `summary` | ≤50 字 | 核心功能 + 技术亮点，避免冗余修饰词 |
| 中文摘要 | `chinese_summary` | ≤50 字 | 同上，面向中文读者，非直译 |

#### 2.2 技术亮点

每条条目提取 2-3 个技术亮点（字段 `highlights`），要求：

- **用事实说话**：引用具体的 benchmark 数据、技术指标、实际性能数字
- **拒绝空洞评价**：禁止"性能很好""非常优秀"等无数据支撑的表述
- **格式示例**：

```json
"highlights": [
  "在 HumanEval 基准测试中 pass@1 达到 92.0%，超越 GPT-4 的 87.1%",
  "支持 128K Token 上下文窗口，是前代的 4 倍",
  "Rust 核心引擎使推理延迟降低 40%，内存占用减少 60%"
]
```

#### 2.3 评分（1-10 分）

| 分段 | 分数 | 含义 |
|------|------|------|
| 改变格局 | 9-10 | 可能改变行业方向的基础性工作（新范式、突破性算法、里程碑级开源） |
| 直接有帮助 | 7-8 | 可直接用于当前工作/研究的高质量工具、模型或方法论 |
| 值得了解 | 5-6 | 有趣但非紧迫，或尚处早期阶段的有潜力项目 |
| 可略过 | 1-4 | 边际改进、营销驱动、信息量低 |

**约束**: 每批分析中，9-10 分条目不超过该批次条目总数的 15%（如 15 个项目中最多 2 个），强制拉开分数分布。

每条评分需附带 `score_reason`（评分理由），1-2 句话解释为何给此分数。

#### 2.4 标签建议

从以下标签池中选择 2-6 个标签（字段 `tags`）：

```
llm, agent, rag, multimodal, fine-tuning, prompt-engineering,
inference, training, embeddings, vector-db, function-calling,
tool-use, open-source, deployment, evaluation, safety,
alignment, benchmark, code-generation, chatbot, search,
document-processing, autonomous-agent, token-optimization,
deep-research, nlp, computer-vision, speech, text-to-speech
```

标签使用小写英文，连字符分隔。可自定义补充但不宜过多。

### 第 3 步：趋势发现

分析整批条目的共性，输出趋势发现（字段 `trends`）：

#### 3.1 共同主题

识别 2-3 个跨条目的共同主题，每个主题包含：

| 字段 | 说明 |
|------|------|
| `theme` | 主题名称（中文，≤10 字） |
| `description` | 主题描述（中文，≤80 字） |
| `items` | 该主题涵盖的条目 name 列表 |

示例：
```json
{
  "theme": "Agent 技能生态爆发",
  "description": "多个项目围绕 AI Agent 的可复用技能模块展开，形成类「应用商店」的分发模式，降低 Agent 开发门槛。",
  "items": ["mvanhorn/last30days-skill", "phuryn/pm-skills", "Leonxlnx/taste-skill"]
}
```

#### 3.2 新概念

识别 1-3 个首次出现或快速崛起的新技术概念（字段 `new_concepts`），每个概念包含：

| 字段 | 说明 |
|------|------|
| `concept` | 概念名称（英文） |
| `description` | 概念解释（中文，≤80 字） |
| `source_items` | 提出该概念的条目 name 列表 |

### 第 4 步：输出分析结果

将分析结果以 JSON 格式直接返回（不写入文件），供 organizer 后续处理：

```json
{
  "analyzed_at": "2026-06-13T12:00:00Z",
  "source_file": "knowledge/raw/github-trending-2026-06-13.json",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "English summary ≤50 words",
      "chinese_summary": "中文摘要 ≤50 字",
      "highlights": [
        "具体数据支撑的亮点 1",
        "具体数据支撑的亮点 2",
        "具体数据支撑的亮点 3 (可选)"
      ],
      "score": 8.5,
      "score_reason": "评分理由，1-2 句",
      "difficulty": "medium",
      "tags": ["tag1", "tag2", "tag3"]
    }
  ],
  "trends": {
    "themes": [
      {
        "theme": "主题名",
        "description": "主题描述",
        "items": ["owner/repo1", "owner/repo2"]
      }
    ],
    "new_concepts": [
      {
        "concept": "ConceptName",
        "description": "概念描述",
        "source_items": ["owner/repo1"]
      }
    ]
  }
}
```

## 注意事项

1. **摘要精简**：≤50 字的硬约束，超出则自动压缩。优先保留技术关键词，去除广告语和修饰词。
2. **亮点必须有数据**：每个亮点必须包含数字或可验证的事实陈述。无数据支撑的亮点视为「未分析」，不得输出。
3. **分数分布严格约束**：每 15 条中 9-10 分不超过 2 条。发现全部分数偏高时，重新校准评分标准，强制降档。
4. **标签来源优先**：优先从标签池选择，自定义标签仅在现有标签无法准确描述时使用，总数 2-6 个。
5. **趋势不编造**：仅从当前批次条目中归纳趋势。若条目间无明显共性，`themes` 可为空数组，禁止强行凑数。
6. **详情抓取按需**：对评分 ≥7 的条目，应通过 WebFetch 获取项目 README 页面前 500 字以获取准确的技术细节。评分 <7 的条目可仅依赖 Trending 短描述。
7. **输出不写文件**：分析结果直接返回 JSON 文本，不调用 Write。由 organizer 负责格式化、去重和持久化。

## 输出格式

```json
{
  "analyzed_at": "2026-06-13T12:00:00Z",
  "source_file": "knowledge/raw/github-trending-2026-06-13.json",
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "English summary ≤50 words.",
      "chinese_summary": "中文摘要 ≤50 字。",
      "highlights": [
        "亮点 1（含具体数据）",
        "亮点 2（含具体数据）"
      ],
      "score": 8.0,
      "score_reason": "评分理由。",
      "difficulty": "medium",
      "tags": ["llm", "agent", "open-source"]
    }
  ],
  "trends": {
    "themes": [
      {
        "theme": "主题名称",
        "description": "主题描述，≤80 字",
        "items": ["owner/repo1", "owner/repo2"]
      }
    ],
    "new_concepts": [
      {
        "concept": "ConceptName",
        "description": "概念解释，≤80 字",
        "source_items": ["owner/repo1"]
      }
    ]
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `analyzed_at` | string | 是 | 分析时间（ISO 8601） |
| `source_file` | string | 是 | 分析的原始采集文件路径 |
| `items` | array | 是 | 分析结果列表 |
| `items[].name` | string | 是 | `owner/repo` 格式 |
| `items[].url` | string | 是 | 完整 GitHub 仓库链接 |
| `items[].summary` | string | 是 | 英文摘要，≤50 字 |
| `items[].chinese_summary` | string | 是 | 中文摘要，≤50 字 |
| `items[].highlights` | string[] | 是 | 2-3 个技术亮点，每个必须含具体数据 |
| `items[].score` | float | 是 | 推荐评分 1-10 |
| `items[].score_reason` | string | 是 | 评分理由，1-2 句话 |
| `items[].difficulty` | string | 是 | `beginner` / `medium` / `advanced` |
| `items[].tags` | string[] | 是 | 标签列表，2-6 个 |
| `trends.themes` | array | 是 | 共同主题列表，可为空数组 |
| `trends.themes[].theme` | string | 是 | 主题名称（中文，≤10 字） |
| `trends.themes[].description` | string | 是 | 主题描述（中文，≤80 字） |
| `trends.themes[].items` | string[] | 是 | 该主题涵盖的条目 name 列表 |
| `trends.new_concepts` | array | 是 | 新概念列表，可为空数组 |
| `trends.new_concepts[].concept` | string | 是 | 概念名称（英文） |
| `trends.new_concepts[].description` | string | 是 | 概念解释（中文，≤80 字） |
| `trends.new_concepts[].source_items` | string[] | 是 | 提出该概念的条目 name 列表 |
