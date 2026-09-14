from pydantic import BaseModel, Field
from typing import List

class AgentSettings(BaseModel):
    languages: List[str] = Field(default_factory=lambda: ["Urdu", "English"], description="List of target languages")
    sources: List[str] = Field(default_factory=list, description="List of news source URLs")
    keywords: List[str] = Field(default_factory=list, description="List of keywords to track")
