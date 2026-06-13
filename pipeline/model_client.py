#!/usr/bin/env python3
"""统一 LLM 调用客户端 — 支持 DeepSeek / Qwen / OpenAI 三种模型提供商。

通过 httpx 直接调用 OpenAI 兼容 API，提供带重试、Token 估算和成本计算的能力。

环境变量:
    LLM_PROVIDER  : 模型提供商 (deepseek / qwen / openai), 默认 deepseek
    LLM_MODEL     : 模型名称, 默认使用各提供商的推荐模型
    DEEPSEEK_API_KEY
    QWEN_API_KEY
    OPENAI_API_KEY

Usage:
    from pipeline.model_client import quick_chat

    response = quick_chat("你好，请介绍一下你自己")
    print(response.content)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
REQUEST_TIMEOUT = 60.0
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # 指数退避基数 (秒)

# 各提供商的默认配置
_PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
}

# 定价表 (元/百万 tokens, 输入/输出)
_PRICING_CNY: dict[str, dict[str, float]] = {
    "deepseek": {"input": 1.0, "output": 2.0},
    "qwen": {"input": 4.0, "output": 12.0},
    "openai": {"input": 150.0, "output": 600.0},
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token 用量统计。

    Attributes:
        prompt_tokens: 提示词消耗的 Token 数。
        completion_tokens: 生成内容消耗的 Token 数。
        total_tokens: 总 Token 数。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """LLM 统一返回结构。

    Attributes:
        content: 模型返回的文本内容。
        usage: Token 用量统计。
        model: 实际使用的模型名称。
        provider: 提供商名称。
        finish_reason: 结束原因 (stop / length / content_filter 等)。
    """

    content: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    finish_reason: str = "stop"


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """LLM 调用成本追踪器。

    记录每次 API 调用的 Token 消耗，按提供商统计并计算成本（人民币）。

    Attributes:
        _records: 原始调用记录列表。
    """

    _PRICING: dict[str, dict[str, float]] = _PRICING_CNY

    def __init__(self) -> None:
        """初始化空的成本追踪器。"""
        self._records: list[dict[str, Any]] = []

    def record(self, usage: Usage, provider: str) -> None:
        """记录一次 API 调用的 Token 用量。

        Args:
            usage: Token 用量统计对象。
            provider: 提供商标识 (deepseek / qwen / openai)。
        """
        self._records.append({
            "provider": provider.strip().lower(),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        })

    def _cost_for_record(self, record: dict[str, Any]) -> float:
        """计算单条记录的成本。

        Args:
            record: 单次调用记录。

        Returns:
            人民币成本（元）。
        """
        provider = record["provider"]
        if provider not in self._PRICING:
            return 0.0
        pricing = self._PRICING[provider]
        input_cost = (record["prompt_tokens"] / 1_000_000) * pricing["input"]
        output_cost = (record["completion_tokens"] / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def estimated_cost(self, provider: str | None = None) -> float:
        """返回估算的总成本（元）。

        Args:
            provider: 提供商标识，为 None 时返回所有提供商合计。

        Returns:
            人民币成本（元），保留 6 位小数。
        """
        filtered = (
            self._records
            if provider is None
            else [r for r in self._records if r["provider"] == provider.strip().lower()]
        )
        total = sum(self._cost_for_record(r) for r in filtered)
        return round(total, 6)

    def report(self, provider: str | None = None) -> str:
        """生成成本报告。

        Args:
            provider: 提供商标识，为 None 时报告所有提供商。

        Returns:
            格式化的成本报告字符串。
        """
        providers = (
            [provider.strip().lower()]
            if provider
            else sorted({r["provider"] for r in self._records})
        )

        lines: list[str] = [
            "=" * 56,
            "  LLM Cost Report (CNY / 元)",
            "=" * 56,
            f"  {'Provider':<12} {'Calls':>6} {'Prompt':>10} {'Comp':>10} {'Cost':>10}",
            "  " + "-" * 54,
        ]

        grand_calls = 0
        grand_prompt = 0
        grand_comp = 0
        grand_cost = 0.0

        for p in providers:
            recs = [r for r in self._records if r["provider"] == p]
            if not recs:
                continue
            calls = len(recs)
            prompt_tokens = sum(r["prompt_tokens"] for r in recs)
            comp_tokens = sum(r["completion_tokens"] for r in recs)
            cost = self.estimated_cost(p)

            lines.append(
                f"  {p:<12} {calls:>6} {prompt_tokens:>10,} {comp_tokens:>10,} "
                f"{cost:>9.4f} ¥"
            )

            grand_calls += calls
            grand_prompt += prompt_tokens
            grand_comp += comp_tokens
            grand_cost += cost

        lines.append("  " + "-" * 54)
        lines.append(
            f"  {'TOTAL':<12} {grand_calls:>6} {grand_prompt:>10,} "
            f"{grand_comp:>10,} {grand_cost:>9.4f} ¥"
        )
        lines.append("=" * 56)

        return "\n".join(lines)

    @property
    def total_calls(self) -> int:
        """返回记录的总调用次数。"""
        return len(self._records)


# 全局成本追踪器实例
cost_tracker = CostTracker()


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """LLM 提供商的抽象接口。

    所有具体实现必须完成 chat() 方法，返回统一的 LLMResponse。
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话请求并返回统一响应。

        Args:
            messages: 消息列表，格式 [{"role": "system/user/assistant", "content": "..."}]
            max_tokens: 最大生成 Token 数。
            temperature: 采样温度 (0-2)。
            **kwargs: 传递给 API 的额外参数。

        Returns:
            LLMResponse 统一响应对象。
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI 兼容接口实现
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    """通过 OpenAI 兼容 API 调用任意模型提供商。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        provider_name: str,
    ) -> None:
        """初始化提供商客户端。

        Args:
            base_url: API 基础地址 (如 https://api.deepseek.com/v1)。
            api_key: API 密钥。
            model: 模型名称。
            provider_name: 提供商标识 (deepseek / qwen / openai)。
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._provider_name = provider_name

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话请求。

        Args:
            messages: 消息列表。
            max_tokens: 最大生成 Token 数。
            temperature: 采样温度。
            **kwargs: 额外参数，直接透传至 API。

        Returns:
            LLMResponse 统一响应对象。

        Raises:
            httpx.HTTPStatusError: HTTP 状态异常。
            httpx.TimeoutException: 请求超时。
        """
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        payload.update(kwargs)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        usage_raw = data.get("usage", {})

        response = LLMResponse(
            content=choice["message"]["content"],
            usage=Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            ),
            model=data.get("model", self._model),
            provider=self._provider_name,
            finish_reason=choice.get("finish_reason", "stop"),
        )

        cost_tracker.record(response.usage, self._provider_name)
        return response


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def create_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> OpenAICompatibleProvider:
    """创建 LLM 提供商实例。

    未传入的参数将从环境变量读取。支持传入自定义配置以覆盖默认值。

    Args:
        provider_name: 提供商名称 (deepseek / qwen / openai)。
            默认从 LLM_PROVIDER 环境变量读取。
        api_key: API 密钥。默认从 {PROVIDER}_API_KEY 环境变量读取。
        model: 模型名称。默认从 LLM_MODEL 环境变量读取，或使用提供商默认模型。
        base_url: API 基础地址。默认使用提供商的默认地址。

    Returns:
        配置好的 OpenAICompatibleProvider 实例。

    Raises:
        ValueError: 未找到 API Key 或提供商不支持。
    """
    if provider_name is None:
        provider_name = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()

    if provider_name not in _PROVIDER_CONFIGS:
        valid = ", ".join(_PROVIDER_CONFIGS)
        raise ValueError(
            f"不支持的提供商 '{provider_name}'，可选值: {valid}"
        )

    config = _PROVIDER_CONFIGS[provider_name]

    if api_key is None:
        env_key = f"{provider_name.upper()}_API_KEY"
        api_key = os.getenv(env_key, "")
    if not api_key:
        raise ValueError(
            f"API Key 未设置，请设置环境变量 {provider_name.upper()}_API_KEY"
        )

    if model is None:
        model = os.getenv("LLM_MODEL", config["default_model"])

    if base_url is None:
        base_url = config["base_url"]

    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        provider_name=provider_name,
    )


def _get_provider() -> OpenAICompatibleProvider:
    """根据环境变量构造对应的提供商实例 (内部使用)。

    Returns:
        配置好的 OpenAICompatibleProvider 实例。
    """
    return create_provider()


async def chat_with_retry(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    provider: LLMProvider | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """带重试机制的对话请求 (3 次，指数退避)。

    遇到网络超时或 5xx 错误时自动重试，最多 3 次。重试间隔依次为 1s、2s、4s。

    Args:
        messages: 消息列表。
        max_tokens: 最大生成 Token 数。
        temperature: 采样温度。
        provider: 提供商实例，为 None 时根据环境变量自动创建。
        **kwargs: 传递给 chat() 的额外参数。

    Returns:
        LLMResponse 统一响应对象。

    Raises:
        RuntimeError: 所有重试均失败。
    """
    if provider is None:
        provider = _get_provider()

    last_error: Exception | None = None

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return await provider.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning(
                "请求超时 (attempt %d/%d): %s",
                attempt,
                RETRY_MAX_ATTEMPTS,
                exc,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_error = exc
            logger.warning(
                "服务端错误 %d (attempt %d/%d): %s",
                exc.response.status_code,
                attempt,
                RETRY_MAX_ATTEMPTS,
                exc,
            )
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "网络错误 (attempt %d/%d): %s",
                attempt,
                RETRY_MAX_ATTEMPTS,
                exc,
            )

        if attempt < RETRY_MAX_ATTEMPTS:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("等待 %.1fs 后重试...", delay)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"LLM 请求失败，已重试 {RETRY_MAX_ATTEMPTS} 次"
    ) from last_error


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 Token 数量 (适用于中英文混合场景)。

    使用启发式规则：
      - 英文: ~4 字符 = 1 token (GPT tokenizer 近似)
      - 中文: ~1.5 字符 = 1 token

    Args:
        text: 待估算文本。

    Returns:
        估算的 Token 数，最小返回 0。
    """
    if not text:
        return 0

    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars

    tokens = (other_chars / 4.0) + (chinese_chars / 1.5)
    return max(0, round(tokens))


def calculate_cost(usage: Usage, provider_name: str) -> float:
    """根据 Token 用量计算调用成本 (元/CNY)。

    Args:
        usage: 用量统计对象。
        provider_name: 提供商标识 (deepseek / qwen / openai)。

    Returns:
        CNY 成本，保留 6 位小数用于精确统计。

    Raises:
        ValueError: 提供商不支持。
    """
    provider_name = provider_name.strip().lower()

    if provider_name not in _PRICING_CNY:
        valid = ", ".join(_PRICING_CNY)
        raise ValueError(
            f"不支持的提供商 '{provider_name}'，可选值: {valid}"
        )

    pricing = _PRICING_CNY[provider_name]
    input_cost = (usage.prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (usage.completion_tokens / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 6)


def quick_chat(
    prompt: str,
    *,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    provider: LLMProvider | None = None,
) -> LLMResponse:
    """便捷函数 — 一句话调用 LLM (同步包装)。

    Args:
        prompt: 用户提示词。
        system_prompt: 系统提示词 (可选)。
        max_tokens: 最大生成 Token 数。
        temperature: 采样温度。
        provider: 提供商实例，为 None 时自动根据环境变量创建。

    Returns:
        LLMResponse 统一响应对象。
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    coro = chat_with_retry(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        provider=provider,
    )
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Token 估算测试 ===")
    test_texts = [
        "Hello, this is a test sentence for token estimation.",
        "这是一段中文测试文本，用于验证 Token 估算的准确性。",
        "混合文本 Mixed text with both Chinese 中文 and English 英文 content.",
        "",
    ]
    for text in test_texts:
        tokens = estimate_tokens(text)
        preview = text[:40] + ("..." if len(text) > 40 else "")
        print(f"  text={preview!r}  ->  tokens={tokens}")

    print("\n=== 成本计算测试 ===")
    for provider_name in ("deepseek", "qwen", "openai"):
        demo_usage = Usage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
        )
        cost = calculate_cost(demo_usage, provider_name)
        print(f"  {provider_name}: {cost:.6f} 元 (1k in / 500 out)")

    print("\n=== CostTracker 测试 ===")
    tracker = CostTracker()
    tracker.record(demo_usage, "deepseek")
    tracker.record(demo_usage, "qwen")
    tracker.record(demo_usage, "openai")
    print(tracker.report())

    print("\n=== 快捷调用测试 ===")
    print("  quick_chat() 函数可用，需要设置环境变量后运行。")
    print(f"  当前提供商: {os.getenv('LLM_PROVIDER', DEFAULT_PROVIDER)}")

    start = time.perf_counter()
    print(f"\n  测试完成，耗时 {time.perf_counter() - start:.3f}s")
