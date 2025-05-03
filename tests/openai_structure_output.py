import json
from typing import List, Optional
from pydantic import BaseModel, Field
from openai import OpenAI


class ImportantEvent(BaseModel):
    date: str  # YYYY-MM-DD
    title: str
    description: str
    reason: Optional[str] = None


class Promise(BaseModel):
    date: str  # YYYY-MM-DD
    content: str


class UserPersona(BaseModel):
    name: Optional[str] = None
    nickname: Optional[List[str]] = None
    birthday: Optional[str] = None  # YYYY-MM-DD
    age: Optional[int] = None
    profession: Optional[str] = None
    gender: Optional[str] = None
    personality: Optional[List[str]] = None
    likesDislikes: Optional[List[str]] = None
    promises: List[Promise] = Field(default_factory=list)
    importantEvent: List[ImportantEvent] = Field(default_factory=list)


client = OpenAI(api_key="sk-proj--DmTnx3zWdvlVTI9AnqLfQCTRBnyCvlbdYMyGIIS72-AqsYj3EOt88Rx_L18nOl5C"
                "35EpAuiC-T3BlbkFJiofYslbDDNUO694PUsbWcrW0RE1-HCnbATzr-yNHGFH0sdkT-7GlOT2hnY5ns9ACIQXlGoUoEA")

completion = client.beta.chat.completions.parse(
    model="gpt-4.1-2025-04-14",
    messages=[{
        "role":
        "system",
        "content":
        """
        你是一個「使用者記憶助手」，會先讀取並理解以下現有的使用者記憶（`UserPersona` 模型）：
        使用者會傳送：
        目前日期與目前記憶

        然後根據最新的對話內容，只擷取與目前記憶不同的新資訊，輸出符合 `UserPersona` 模型的純 JSON，且須遵守：

        1. 僅包含與現有記憶有差異或新的欄位和值。
        2. 不重複輸出已有記憶中的資料，也不推測未提及之內容。
        """
    }, {
        "role":
        "user",
        "content": ("""現有記憶{
  "name": "小明",
  "nickname": ["明明", "大頭"],
  "birthday": "2000-01-01",
  "age": 25,
  "profession": "工程師",
  "gender": "男",
  "personality": ["開朗", "好奇"],
  "likesDislikes": ["喜歡游泳", "討厭下雨"],
  "promises": ["2025-05-01: 完成報告", "2025-06-10: 參加聚會"],
  "importantEvent": [
    {
      "date": "2025-04-28",
      "title": "校園口角",
      "description": "與同學因誤會發生爭執，對方推倒導致擦傷",
      "reason": "誤聽對方言論"
    },
    {
      "date": "2025-03-15",
      "title": "第一次游泳比賽",
      "description": "參加校際游泳比賽並獲得第三名",
      "reason": "想挑戰自己"
    }
  ]
}"""
                    "今日日期：2025/04/30"
                    "昨天你還叫我什麼來著？啊對，「菜鳥獸醫」。"
                    "我都說我才剛滿負24，是女生好嗎。"
                    "雖然是獸醫，但我超怕香菜。"
                    "我們不是說好週日要一起值班的嗎？")
    }],
    response_format=UserPersona,
)

profile = completion.choices[0].message.parsed
print(json.dumps(profile.model_dump(), indent=2, ensure_ascii=False))
