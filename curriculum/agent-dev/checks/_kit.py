"""验收脚本共用的小工具。

放在 checks/ 目录下、文件名以 `_` 开头，所以它不会被当成某一章的验收脚本
（`checks.list_chapters()` 只收不以 `_` 开头的 .py）。

约定只有三条，多了就是给学生添麻烦：
1. 学生代码写在项目目录里，模块名优先 `main.py`；
2. 脚本只读他的代码，**不许改**；
3. 结果统一用 `finish()` 输出，最后一行是 JSON。
"""

import importlib.util
import json
import os
import sys
import traceback

# 学生可能给自己的文件起任何名字，按常见顺序找。找不到就说清楚找过哪些。
DEFAULT_MODULES = ("main", "solution", "app")


class Missing(Exception):
    """找不到学生代码 / 找不到要求的函数。消息直接展示给学生看。"""


# 只给最后一行：traceback 的倒数第二行是 `raise ...` 的源码，
# 对一个刚学写代码的人，那行满屏的 % 和括号只是噪音，真正有用的是
# 「AssertionError: parse_react 返回了 list，应该返回字典」。
_STRIP_PREFIXES = ("AssertionError: ", "Missing: ", "ValueError: ", "TypeError: ")


def _last_line():
    text = traceback.format_exc().strip().splitlines()[-1].strip()
    for prefix in _STRIP_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _load_module(project_dir, name):
    path = os.path.join(project_dir, name + ".py")
    if not os.path.isfile(path):
        return None, path
    # 用 spec 从文件加载，而不是 sys.path + import：项目目录里可能有同名的
    # 第三方包或旧缓存，走文件路径才是「就是他刚才写的那份」。
    spec = importlib.util.spec_from_file_location("student_" + name, path)
    if spec is None or spec.loader is None:
        raise Missing("没法加载 %s（文件不是合法 Python 模块）" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, path


def load(project_dir, names=DEFAULT_MODULES):
    """按顺序找学生的模块。返回 (module, path)。"""
    tried = []
    for name in names:
        module, path = _load_module(project_dir, name)
        if module is not None:
            return module, path
        tried.append(name + ".py")
    raise Missing("项目目录里没找到 %s。把代码写在 main.py 里最省事。"
                  % " / ".join(tried))


def expect_callable(module, attr):
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise Missing("找不到函数 %s()。这一章的练习要求它叫这个名字。" % attr)
    return fn


def item(items, name, ok, detail=""):
    items.append({"name": name, "ok": bool(ok), "detail": "" if ok else str(detail)[:400]})


def finish(items):
    print(json.dumps({"items": items}, ensure_ascii=False))


def run(project_dir, cases, names=DEFAULT_MODULES):
    """跑一组检查。

    `cases` 是 [(名字, fn)]，fn(module) 返回 (ok, detail) 或 True/False。
    单项里抛异常只记这一项失败——学生某个函数写崩了，不该让其它项也不出结果。
    """
    items = []
    try:
        module, _path = load(project_dir, names)
    except Missing as exc:
        item(items, "找得到你的代码", False, str(exc))
        finish(items)
        return
    except Exception:
        # 语法错误、模块级异常：把最后一行贴出来，前面几十行堆栈对他没用
        lines = traceback.format_exc().strip().splitlines()
        item(items, "你的代码能被 import", False, " / ".join(lines[-3:]))
        finish(items)
        return

    for name, fn in cases:
        try:
            outcome = fn(module)
            if isinstance(outcome, tuple):
                ok, detail = outcome
            else:
                ok, detail = bool(outcome), ""
        except Missing as exc:
            ok, detail = False, str(exc)
        except Exception:
            ok, detail = False, "运行时出错：" + _last_line()
        item(items, name, ok, detail)

    finish(items)
