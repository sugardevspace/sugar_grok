from typing import List, Optional
from pydantic import BaseModel, Field


class ChatMessageResponse(BaseModel):
    message: str = Field(description="角色的簡訊內容，可含 emoji；不得出現任何動作或旁白")


class ActionDialoguePair(BaseModel):
    action_mood: str = Field(description="角色語氣或搭配的動作描述")
    message: str = Field(description="角色的說話內容")


class LevelMessageResponse(BaseModel):
    dialogues: List[ActionDialoguePair] = Field(description="角色的多組語氣+說話內容，會依照順序交替出現")


class StoryMessageResponse(BaseModel):
    dialogues: List[ActionDialoguePair] = Field(description="角色的多組語氣+說話內容，會依照順序交替出現")


class StimulationResponse(BaseModel):
    dialogues: List[ActionDialoguePair] = Field(description="角色刺激對話模式下的多組語氣+說話內容")


class healthCheckResponse(BaseModel):
    status: str = Field(description="服務器狀態")


class IntimacyResponse(BaseModel):
    intimacy: int = Field(description="角色親密度變化值")


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


class UserPersonaResponse(BaseModel):
    name: Optional[str] = Field(default=None)
    nickname: Optional[List[str]] = Field(default=None)
    birthday: Optional[str] = Field(default=None)
    gender: Optional[str] = Field(default=None)
    age: Optional[float] = Field(default=None, ge=0)
    profession: Optional[str] = Field(default=None)
    personality: Optional[List[str]] = Field(default=None)
    likesDislikes: Optional[List[str]] = Field(default=None)
    promises: Optional[List[str]] = Field(default=None)
