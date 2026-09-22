from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any

ALLOWED_LANGUAGES = {"urdu", "english", "arabic"}
CANONICAL_LANGUAGES = {"urdu": "Urdu", "english": "English", "arabic": "Arabic"}

class AgentSettings(BaseModel):
    source_languages: List[str] = Field(default_factory=lambda: ["English", "Urdu", "Arabic"], description="List of source languages")
    target_languages: List[str] = Field(default_factory=lambda: ["Urdu", "English"], description="List of target languages")
    sources: List[str] = Field(default_factory=list, description="List of news source URLs")
    keywords: List[str] = Field(default_factory=list, description="List of keywords to track")

    # Backwards-compatible alias property for 'languages'
    @property
    def languages(self) -> List[str]:
        return self.target_languages

    @field_validator("source_languages", "target_languages", mode="before")
    @classmethod
    def validate_languages(cls, v):
        if not isinstance(v, list):
            return v
        validated = []
        for item in v:
            item_str = str(item).strip()
            if not item_str:
                continue
            lower_val = item_str.lower()
            if lower_val not in ALLOWED_LANGUAGES:
                raise ValueError(f"'{item_str}' is not a valid language. Allowed languages are: Urdu, English, Arabic.")
            validated.append(CANONICAL_LANGUAGES[lower_val])
        return validated

    def __init__(self, **data):
        if "languages" in data and "target_languages" not in data:
            data["target_languages"] = data.pop("languages")
        super().__init__(**data)


class StoryItem(BaseModel):
    heading: str = ""
    url: str = ""
    subheading: str = ""
    summary: str = ""
    image: str = ""
    content: str = ""
    author: str = ""
    date: str = ""
    sitename: str = ""
    source_language: str = ""
    tags: List[str] = Field(default_factory=list)
    blocks: List[Dict[str, Any]] = Field(default_factory=list)


class StoryUpdateRequest(BaseModel):
    index: int
    story: StoryItem


class StoryFetchRequest(BaseModel):
    url: str

