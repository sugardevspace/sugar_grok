from core.setting import settings


def calculate_cost(prompt_tokens: int, completion_tokens: int, provider: str = "grok") -> float:
    """
    計算 API 請求的費用
    
    Args:
        prompt_tokens: 提示詞 Token 數量
        completion_tokens: 完成詞 Token 數量
        provider: 提供者名稱（預設為 grok）
        
    Returns:
        float: 美元計價的費用
    """
    # 根據不同提供者計算費用
    if provider == "grok":
        prompt_cost = (prompt_tokens / 1_000_000) * settings.PROMPT_TOKEN_COST_PER_MILLION
        completion_cost = (completion_tokens / 1_000_000) * settings.COMPLETION_TOKEN_COST_PER_MILLION
        return prompt_cost + completion_cost

    elif provider == "openai":
        # OpenAI 費用計算 (千字成本)
        RATE_PROMPT = 1.00  # $1.00 / 1M tokens
        RATE_COMPLETION = 4.00  # $4.00 / 1M tokens

        input_cost = prompt_tokens / 1_000_000 * RATE_PROMPT
        output_cost = completion_tokens / 1_000_000 * RATE_COMPLETION

        return input_cost + output_cost

    elif provider == "anthropic":
        # Anthropic 費用計算
        # Claude 3 Opus 模型的範例費率
        prompt_cost = (prompt_tokens / 1_000) * 0.015
        completion_cost = (completion_tokens / 1_000) * 0.075
        return prompt_cost + completion_cost

    elif provider == "local":
        # 本地 LLM 通常不計費
        return 0.0

    else:
        # 如果未知提供者，使用預設的 Grok 費率
        prompt_cost = (prompt_tokens / 1_000_000) * settings.PROMPT_TOKEN_COST_PER_MILLION
        completion_cost = (completion_tokens / 1_000_000) * settings.COMPLETION_TOKEN_COST_PER_MILLION
        return prompt_cost + completion_cost
