from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuthError:
    code: str
    message: str
    details: Any = None


@dataclass
class AuthResponse:
    success: bool
    status: int | None = None
    data: Any = None
    errors: list[AuthError] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    raw_body: str = ""
    raw_headers: dict = field(default_factory=dict)
    url: str = ""
    method: str = ""
