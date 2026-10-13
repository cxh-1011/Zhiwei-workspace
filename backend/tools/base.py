from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_tool_message(self) -> str:
        return self.content if self.ok else (
            f"工具执行失败：{self.error or self.content}。请勿编造结果，可尝试其他工具或说明无法完成。"
        )


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, Any]], ToolResult]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, arguments: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        properties = self.parameters.get("properties", {})
        required = self.parameters.get("required", [])
        if not isinstance(arguments, dict):
            return ToolResult(False, "", error="工具参数必须是 JSON 对象")
        unknown = set(arguments) - set(properties)
        if unknown:
            return ToolResult(False, "", error="包含未允许的参数")
        missing = [key for key in required if key not in arguments]
        if missing:
            return ToolResult(False, "", error="缺少必要参数：" + ", ".join(missing))
        try:
            result = self.handler(arguments, context)
            if not isinstance(result, ToolResult):
                return ToolResult(False, "", error="工具返回格式无效")
            return result
        except Exception as exc:
            return ToolResult(False, "", error=f"{type(exc).__name__}: {str(exc)[:240]}")


_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    if tool.name in _TOOLS:
        raise ValueError(f"重复注册工具：{tool.name}")
    _TOOLS[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    return _TOOLS.get(name)


def all_schemas(names: list[str] | None = None) -> list[dict[str, Any]]:
    selected = [tool for tool in _TOOLS.values() if names is None or tool.name in names]
    return [tool.schema() for tool in selected]


def tool_names() -> list[str]:
    return list(_TOOLS)
