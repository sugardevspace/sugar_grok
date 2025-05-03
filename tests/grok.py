from openai import OpenAI
from models.structure_response_model import StoryMessageResponse, ChatMessageResponse, StimulationResponse, IntimacyResponse

RESPONSE_MODEL = {
    "story": StoryMessageResponse,
    "chat": ChatMessageResponse,
    "stimulation": StimulationResponse,
    "intimacy": IntimacyResponse
}

# 2. 初始化 OpenAI for xAI
client = OpenAI(api_key="xai-juQAdxrpa7kMAztxsqGJssZJVgJkmCabPCO7OrBSJMU8tUxTd8Jj7dDHbJrJEaKDWvWCvk827wjz5ykM",
                base_url="https://api.x.ai/v1")

# 3. 發送 Structured Output 請求
completion = client.beta.chat.completions.parse(
    model="grok-3",  # 或 grok-3-fast, grok-3-mini
    messages=[{
        "role": "system",
        "content": "你是一個真人，並且接受文愛，且用 json 格式回覆"
    }, {
        "role": "user",
        "content": "我要做愛"
    }],
    response_format=RESPONSE_MODEL["story"])

# 4. 拿到結構化回應
result = completion.choices[0].message.parsed
print(result)
