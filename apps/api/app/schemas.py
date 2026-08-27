"""API 请求模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500, description="用户问题")
    mode: Literal["auto", "direct", "research"] = Field(
        default="auto", description="auto=路由器决定；direct/research=强制指定"
    )
    session_id: str = Field(default="default", max_length=64, description="会话 ID（办理流程跨轮状态）")
    role: Literal["student", "counselor"] = Field(default="student", description="演示身份")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    k: int = Field(default=5, ge=1, le=20)
