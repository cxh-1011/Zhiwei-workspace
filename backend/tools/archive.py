"""学习档案的写入工具。

这三个工具只在用户显式开启「允许助手记录」后才注册（见 agent.run_agent 的
allow_writes 参数），默认关闭——保持「对话不会自动写入长期记忆」这一安全约定。

工具内部统一走 database 层已有的函数，不直接写 SQL。
"""

from .base import Tool, ToolResult, register


# ---------------------------------------------------------------- 长期记忆

def _remember(args: dict, _context: dict) -> ToolResult:
    from database import add_memory, list_memories

    content = str(args.get("content", "")).strip()[:1200]
    if not content:
        return ToolResult(False, "", error="记忆内容不能为空")

    category = str(args.get("category", "偏好")).strip()[:40] or "偏好"

    # 完全相同的内容不重复写入，避免助手把同一句话反复记住
    for row in list_memories(100):
        if str(row[1]).strip() == content:
            return ToolResult(
                True,
                "这条内容已经在长期记忆里（编号 %d），本次没有重复写入。" % row[0],
                {"memory_id": row[0], "duplicated": True},
            )

    memory_id = add_memory(content, category, source="agent")
    return ToolResult(
        True,
        "已写入长期记忆：编号 %d，分类「%s」。这条记忆会在之后的对话里作为背景生效。" % (memory_id, category),
        {"memory_id": memory_id, "category": category, "content": content, "source": "agent"},
    )


# ---------------------------------------------------------------- 学习任务

def _list_tasks(_args: dict, _context: dict) -> ToolResult:
    from database import list_learning_plans, list_learning_tasks

    lines: list[str] = []
    plans_data: list[dict] = []

    for plan in list_learning_plans(20):
        plan_id, title, goal, _content, status, _created = plan
        tasks = list_learning_tasks(plan_id)
        done = sum(1 for task in tasks if task[4] == "done")

        lines.append(
            "【计划 %d】%s%s —— 目标：%s（%d/%d 已完成）"
            % (plan_id, title, "（整体已完成）" if status == "done" else "",
               str(goal)[:120], done, len(tasks))
        )
        if not tasks:
            lines.append("  （这个计划还没有可勾选任务）")

        task_items = []
        for task in tasks:
            task_id, task_title, details, stage, task_status = task[0], task[1], task[2], task[3], task[4]
            lines.append(
                "  - task_id=%d  [%s] %s%s"
                % (task_id, "已完成" if task_status == "done" else "未完成",
                   (str(stage) + " · ") if stage else "", str(task_title)[:100])
            )
            task_items.append({
                "task_id": task_id, "title": task_title, "stage": stage,
                "details": details, "status": task_status,
            })
        plans_data.append({
            "plan_id": plan_id, "title": title, "goal": goal,
            "status": status, "tasks": task_items,
        })

    if not lines:
        return ToolResult(
            True,
            "当前没有任何学习计划。用户可以先在学习档案里写下目标，再生成任务。",
            {"plans": []},
        )

    return ToolResult(True, "\n".join(lines)[:5000], {"plans": plans_data})


def _set_task_status(args: dict, _context: dict) -> ToolResult:
    from database import get_learning_plan, set_learning_task_status

    plan_id = int(args["plan_id"])
    task_id = int(args["task_id"])
    status = str(args.get("status", "")).strip()

    if status not in {"todo", "done"}:
        return ToolResult(False, "", error="status 只能是 todo 或 done")
    if not get_learning_plan(plan_id):
        return ToolResult(False, "", error="计划 %d 不存在，请先调用 list_learning_tasks 查看" % plan_id)
    if not set_learning_task_status(plan_id, task_id, status):
        return ToolResult(
            False, "",
            error="任务 %d 不存在，或者不属于计划 %d。请先调用 list_learning_tasks 确认编号" % (task_id, plan_id),
        )

    label = "已完成" if status == "done" else "未完成"
    return ToolResult(
        True,
        "已把计划 %d 下的任务 %d 标记为「%s」。" % (plan_id, task_id, label),
        {"plan_id": plan_id, "task_id": task_id, "status": status},
    )


# ---------------------------------------------------------------- 注册

register(Tool(
    name="remember",
    description=(
        "把用户明确要求记住的事实、偏好或结论写入他的长期记忆。"
        "只在用户说出「记住 / 记一下 / 帮我记着」这类明确要求时调用；"
        "不要因为你自己觉得某条信息有用就写入。"
        "内容要写成一条独立、可复用的陈述句，不要写成对话语气。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容，一句完整的话"},
            "category": {"type": "string", "description": "分类，例如 研究方向 / 偏好 / 结论，默认「偏好」"},
        },
        "required": ["content"],
        "additionalProperties": False,
    },
    handler=_remember,
))

register(Tool(
    name="list_learning_tasks",
    description=(
        "读取用户当前的学习计划与任务清单，返回计划编号、任务编号和完成状态。"
        "用于回答「我的计划进展到哪了」，或在勾选任务之前先取得任务编号。"
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    handler=_list_tasks,
))

register(Tool(
    name="set_task_status",
    description=(
        "把某个学习任务标记为已完成或未完成。"
        "必须先调用 list_learning_tasks 取得 plan_id 和 task_id，不要凭空猜编号。"
        "只在用户明确要求勾选、完成或取消某项任务时调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "plan_id": {"type": "integer", "description": "计划编号，来自 list_learning_tasks"},
            "task_id": {"type": "integer", "description": "任务编号，来自 list_learning_tasks"},
            "status": {"type": "string", "enum": ["todo", "done"], "description": "done 表示完成，todo 表示未完成"},
        },
        "required": ["plan_id", "task_id", "status"],
        "additionalProperties": False,
    },
    handler=_set_task_status,
))
