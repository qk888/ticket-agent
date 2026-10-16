"""工具层：按角色物理裁剪的注册表 + 知识库检索。"""

from .knowledge import search_policies
from .registry import (
    ROLE_TOOL_MATRIX,
    WRITE_TOOLS,
    ToolRegistry,
    get_tool_context,
    registry,
    set_tool_context,
)

__all__ = [
    "ROLE_TOOL_MATRIX",
    "WRITE_TOOLS",
    "ToolRegistry",
    "get_tool_context",
    "registry",
    "search_policies",
    "set_tool_context",
]
