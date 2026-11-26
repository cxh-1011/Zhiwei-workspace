"""学习沙箱的项目：在磁盘上真的建一个目录，让 agent 在里面写代码、跑验收。

为什么不复用「改代码」那套开关
------------------------------
原来只有一种工作区：代码仓库。`_guard.WORKSPACE_ROOT` 在导入时就定死了，
于是「让 agent 在你的仓库里改文件」和「让 agent 在一个空目录里从零写个程序」
只能用同一个根。前者的红线（.env、chats.db、backups）对一个正在学写代码的人
全是噪音：他只想 `python main.py`，却要先理解为什么 `read_file(".env")` 被拒。

所以这里引入第二个根：每个项目一个目录，`_guard.register_root()` 登记，
进入项目时 `use_root()` 切过去。切换之后**仓库整个不可见**——不是靠红线名单
拦，而是相对路径根本解不到那里。这才是沙箱该有的形状。

项目目录不可删除（只能从列表里移除登记）
----------------------------------------
`delete_project()` 只删数据库行。删目录是不可逆的，而这个功能的用户是刚开始
写代码的人；把「清理列表」和「删掉我写了三小时的项目」合成一个按钮，
早晚会有人手滑。目录还在，列表会如实标成「目录不见了」。
"""

import contextlib
import os
import re
import shutil

import database
import envs
import runtimes
from course import CURRICULUM_ROOT, DEFAULT_COURSE
from tools import _guard

# 项目存放位置：默认在仓库根下的 projects/，可用 AGENT_PROJECTS 指到别处
# （比如放到空间更大的盘）。这个目录名在 _guard.BLOCKED_DIRS 里，
# 意味着从仓库根看过去它整棵不可见——打开 A 项目时读不到 B 项目。
PROJECTS_ROOT = os.path.realpath(
    os.getenv("AGENT_PROJECTS")
    or os.path.join(_guard.base_root(), "projects")
)

MAX_NAME_CHARS = 40
MAX_PROJECTS_PER_CHAPTER = 20

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
_COURSE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_CHAPTER_RE = re.compile(r"^[a-z0-9]{1,12}$")


class ProjectError(Exception):
    """项目操作的预期失败（名字不合法、目录不存在），消息直接给用户看。"""


def _safe_component(value, pattern, label, allow_empty=False):
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ProjectError("%s不能为空" % label)
    if not pattern.match(text):
        raise ProjectError("%s只能用字母、数字和连字符：%s" % (label, text[:40]))
    return text


def slugify(name):
    """把项目名转成目录名。中文名转不出来，返回空串由调用方兜底。"""
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return text[:38].strip("-")


def _unique_dir(parent, slug):
    """找一个还没被占用的目录名：foo、foo-2、foo-3……"""
    candidate = slug
    index = 1
    while os.path.exists(os.path.join(parent, candidate)):
        index += 1
        if index > 99:
            raise ProjectError("同名项目太多了，换一个名字")
        candidate = "%s-%d" % (slug, index)
    return candidate


def _read_brief(course_id, chapter_id):
    """章节的任务说明（可选）。没有就返回空串，不编一段假任务。"""
    if not chapter_id:
        return ""
    path = CURRICULUM_ROOT / course_id / "checks" / ("%s.md" % chapter_id)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _chapter_title(course_id, chapter_id):
    if not chapter_id:
        return ""
    try:
        from course import load_course
        for chapter in load_course(course_id).get("chapters") or []:
            if chapter.get("id") == chapter_id:
                return "第 %d 章《%s》" % (chapter.get("no") or 0, chapter.get("title") or "")
    except Exception:
        pass
    return ""


def _starter_readme(name, course_id, chapter_id):
    """新建项目时放一份 README。

    这不是装饰：agent 一进项目就 `list_dir`，看见一个纯空目录时它常常先问
    「你想做什么项目」。README 里写清「这一章的任务 + 怎么自测」，它就能直接开工，
    也给了用户一个「这个目录是干什么的」的锚点。
    """
    lines = ["# %s" % name, ""]
    title = _chapter_title(course_id, chapter_id)
    brief = _read_brief(course_id, chapter_id)
    if title:
        lines.append("对应课程：%s" % title)
        lines.append("")
    if brief:
        lines.append(brief)
        lines.append("")
    lines.append("## 怎么自测")
    lines.append("")
    lines.append("在这个目录里直接跑 `python -m pytest .`，或者在页面上点「跑验收」。")
    lines.append("验收脚本只读你的代码、不修改它；没过时会指出哪一条没满足。")
    lines.append("")
    lines.append("## 也可以用 JavaScript 写")
    lines.append("")
    lines.append("把代码放 `main.js` 里（不要同时留 `main.py`），验收会用 node 跑它。")
    lines.append("要求一样：`module.exports = { 函数名: ... }`，函数名见下面的验收要求。")
    lines.append("有三章（第 1 章第三节、第 6 章第二节、第 7 章第一节）只能 Python——")
    lines.append("那几章的验收要把一个函数当参数传进你的代码，跨语言传不进去。")
    lines.append("")
    lines.append("## 需要额外依赖时")
    lines.append("")
    lines.append("点页面上的「准备环境」，这个目录会有自己的 `.venv`；之后验收和 `python`")
    lines.append("都跑在它里面。要装包就写 `requirements.txt`（一行一个包名），再点「装依赖」。")
    lines.append("只用标准库的章节不需要这一步。")
    lines.append("")
    return "\n".join(lines)


def _write_starter(directory, name, course_id, chapter_id):
    readme = os.path.join(directory, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8", newline="") as handle:
            handle.write(_starter_readme(name, course_id, chapter_id))
    # __pycache__ 是学生最常误提交的东西；一个 .gitignore 比反复提醒便宜。
    ignore = os.path.join(directory, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8", newline="") as handle:
            handle.write("__pycache__/\n*.pyc\n.venv/\n")


def create(name, chapter_id="", course_id=DEFAULT_COURSE):
    """在磁盘上建一个项目目录并登记，返回项目行。

    只建目录和两份说明文件，不生成任何代码骨架：这一章要写什么由用户和 agent
    在对话里定，预置一个猜出来的 main.py 反而会限制他们。
    """
    name = str(name or "").strip()
    if not name:
        raise ProjectError("项目名不能为空")
    if len(name) > MAX_NAME_CHARS:
        raise ProjectError("项目名太长了（最多 %d 字）" % MAX_NAME_CHARS)

    course_id = _safe_component(course_id, _COURSE_RE, "课程 id")
    chapter_id = _safe_component(chapter_id, _CHAPTER_RE, "章节 id", allow_empty=True)

    existing = database.list_projects(course_id, chapter_id or None)
    if chapter_id and len(existing) >= MAX_PROJECTS_PER_CHAPTER:
        raise ProjectError("这一章已经有 %d 个项目了，先在上面继续写，或换一章练"
                           % MAX_PROJECTS_PER_CHAPTER)

    parent = os.path.join(PROJECTS_ROOT, course_id)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise ProjectError("建不了项目目录：%s" % exc) from exc

    slug = slugify(name) or (chapter_id or "proj")
    slug = _unique_dir(parent, slug)
    path = os.path.realpath(os.path.join(parent, slug))

    # 再确认一次落点：PROJECTS_ROOT 是配置项，被别人改成 "/" 时不能让后面
    # 的写入跑到根目录去。这里用 normcase 比较，Windows 下大小写不敏感。
    root_key = os.path.normcase(PROJECTS_ROOT)
    if not os.path.normcase(path).startswith(root_key + os.sep):
        raise ProjectError("项目路径越界，拒绝创建")

    try:
        os.makedirs(path)
    except OSError as exc:
        raise ProjectError("建不了项目目录：%s" % exc) from exc

    try:
        _write_starter(path, name, course_id, chapter_id)
    except OSError as exc:
        # 说明文件写不进去不算致命，但目录已经建好了，别留下一个「建了一半」的假象
        shutil.rmtree(path, ignore_errors=True)
        raise ProjectError("初始化项目文件失败：%s" % exc) from exc

    try:
        _guard.register_root(path)
        return database.add_project(course_id, chapter_id, slug, name, path)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


def get(project_id):
    """取一条已装饰的项目信息（含 shown_path / check_label），拿不到返回 None。

    提示词和接口都用它：两边要看到同一份措辞，不要在别处再拼一次标签。
    """
    row = database.get_project(project_id)
    return decorate(row) if row else None


def validate(project_id):
    """只读校验：项目存在、目录还在。给「对话前」用，不产生任何副作用。

    和 open_project 分开，是因为流式对话的校验发生在返回响应之前，
    而真正的切根/记 opened_at 发生在流里——两处都要，但只有一处该写库。
    """
    row = database.get_project(project_id)
    if not row:
        raise ProjectError("没有这个项目")
    if not os.path.isdir(row["path"]):
        raise ProjectError("项目目录不在了：%s。可能被手动删除或移动了。" % row["path"])
    return row


def open_project(project_id):
    """取项目并登记成可用根。目录不在就报错，不自动重建。

    自动重建会把「你删掉的项目」变成一个空目录，用户以为数据还在。
    """
    row = database.get_project(project_id)
    if not row:
        raise ProjectError("没有这个项目")
    path = row["path"]
    if not os.path.isdir(path):
        raise ProjectError("项目目录不在了：%s。可能被手动删除或移动了；"
                           "需要的话新建一个，原来的登记可以从列表里移除。" % path)
    _guard.register_root(path)
    database.touch_project(row["id"])
    return database.get_project(project_id)


def register_existing(course_id=None):
    """启动时把库里登记的目录重新登记成根。

    登记表只在内存里，进程重启就空了。少了这一步，「重启后端后第一次进项目」
    会报「未登记的目录」——看起来像权限 bug，其实是没恢复状态。
    """
    count = 0
    for row in database.list_projects(course_id):
        if os.path.isdir(row["path"]):
            try:
                _guard.register_root(row["path"])
                count += 1
            except ValueError:
                continue
    return count


def tree(project_id, max_entries=200):
    """列项目里的文件，给页面显示「这个目录现在有什么」。

    深度限制 3 层、条数限制 200：学习项目通常就几个文件，扫全量没有意义，
    而递归扫到一个误装的 node_modules 会让接口卡住。
    """
    row = database.get_project(project_id)
    if not row:
        raise ProjectError("没有这个项目")
    root = row["path"]
    if not os.path.isdir(root):
        return {"exists": False, "entries": [], "truncated": False}

    entries = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if depth >= 2:
            dirnames[:] = []
        dirnames[:] = sorted(d for d in dirnames if d not in
                             ("__pycache__", ".git", ".venv", "node_modules"))
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            if len(entries) >= max_entries:
                truncated = True
                break
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            entries.append({
                "path": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
            })
        if truncated:
            break
    return {"exists": True, "entries": entries, "truncated": truncated}


def decorate(row):
    """给接口用的项目行：数据库字段 + 磁盘现状 + 章节标题 + 环境状态。"""
    if not row:
        return None
    item = dict(row)
    item["exists"] = os.path.isdir(row["path"])
    # 这一章他选了哪种语言：有 .py 就是 Python，只有 .js 才是 JS。
    # 前端据此把「准备环境 / 装依赖」换掉——JS 项目不需要 venv，摆着一个
    # 「准备环境」按钮只会让他白等十几秒。
    item["language"] = runtimes.student_language(row["path"]) if item["exists"] else "python"
    # 环境状态直接看磁盘，不存库：.venv 被手动删掉之后，库里那条记录就成了谎话。
    # 代价是每次列项目都读一遍 requirements.txt——学习项目的文件只有几个，无所谓。
    item["env"] = envs.info(row["path"]) if item["exists"] else {
        "present": False, "python": "system", "version": "", "pip": False,
        "requirements": {"present": False, "count": 0, "error": "", "state": "none",
                         "dirty": False, "added": [], "removed": [], "last_error": "",
                         "kind": ""},
    }
    if item["check_ok"] is None:
        item["check_label"] = "还没验收"
    elif item["check_ok"]:
        item["check_label"] = "验收通过 %d/%d" % (item["check_passed"], item["check_total"])
    else:
        item["check_label"] = "验收没过 %d/%d" % (item["check_passed"], item["check_total"])
    # 只暴露相对仓库根的路径：绝对路径里带用户名，截图分享出去容易泄露
    try:
        item["shown_path"] = os.path.relpath(row["path"], _guard.base_root()).replace("\\", "/")
    except ValueError:
        item["shown_path"] = row["slug"]
    return item


def list_for(course_id=DEFAULT_COURSE, chapter_id=None):
    rows = database.list_projects(course_id, chapter_id)
    return [decorate(row) for row in rows]


@contextlib.contextmanager
def sandbox(project_id):
    """进入项目沙箱：把活动根切到项目目录，退出时切回来。

    这是「学习模式默认允许写和运行」这条规则的唯一实现处：
    - 有项目 → 允许改文件、跑命令，但**根就是项目目录**，仓库整个不可见；
    - 没项目 → 学习模式一个文件工具都不给（在 agent 里挡）。

    为什么不把这个判断散在接口里：散开之后，任何新增的入口只要忘了看一眼
    `project_id`，就等于在仓库根上开了写权限。所以只有这一个入口，
    切根和授权是同一件事。
    """
    row = open_project(project_id)
    token = _guard.use_root(row["path"])
    try:
        yield row
    finally:
        _guard.reset_root(token)
