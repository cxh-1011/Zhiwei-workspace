"""验收脚本共用的小工具。

放在 checks/ 目录下、文件名以 `_` 开头，所以它不会被当成某一章的验收脚本
（`checks.list_chapters()` 只收不以 `_` 开头的 .py）。

约定只有三条，多了就是给学生添麻烦：
1. 学生代码写在项目目录里，模块名优先 `main.py`；
2. 脚本只读他的代码，**不许改**；
3. 结果统一用 `finish()` 输出，最后一行是 JSON。

两种语言
--------
学生可以用 Python（`main.py`），也可以用 JavaScript（`main.js`）。后者由
`JsModule` 包成「长得像 Python 模块」的对象：每次取属性得到的是一个
可调用对象，调用时起一个 node 进程，把参数 JSON 过去、把返回值 JSON 回来。
这样 33 章检查代码一行都不用改——它们本来就只做「拿到函数、喂数据、看结果」。

不支持的章只有三处：检查要把一个 Python 函数（或生成器）当参数传进学生代码，
跨语言传不了。那三章在 `run(..., languages=("python",))` 里声明，学生用 JS
写时会得到一句人话，而不是一句 `Object is not JSON serializable`。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import traceback

# 学生可能给自己的文件起任何名字，按常见顺序找。找不到就说清楚找过哪些。
DEFAULT_MODULES = ("main", "solution", "app")
JS_EXTENSIONS = (".js", ".mjs", ".cjs")

# 单个产物文件最多读这么多：学生的输出里塞进一整份日志时，
# 直接读进内存再正则，比先 stat 再截断省事。
MAX_TEXT = 40000

# 只是调一个函数，超过这个时间基本就是死循环。比整章验收的 90 秒短得多：
# 一条检查卡住不该让整章等满。
JS_CALL_TIMEOUT = 15

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_node_harness.js")


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


def _find_js(project_dir, names):
    """按同样的名字顺序找 .js / .mjs / .cjs。"""
    for name in names:
        for extension in JS_EXTENSIONS:
            path = os.path.join(project_dir, name + extension)
            if os.path.isfile(path):
                return path
    return ""


def _node_path():
    """node 在哪。先看执行器给的（它已经查过一次），再自己找。"""
    given = os.environ.get("ZHIWEI_NODE", "").strip()
    if given:
        return given
    for name in ("node", "node.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def why(exc):
    """异常的一句话说明。

    为什么不是 `type(exc).__name__`：类名只说「抛了」，不说「为什么」。
    `KeyError: 'city'` 比 `KeyError` 多出来的那半句，才是学生能改的东西；
    JS 那边抛的错更是全靠这句话——不然他只会看到「抛了 Missing」。

    检查脚本里凡是 `except Exception as exc` 的地方都用它。
    """
    text = str(exc).strip().replace("\n", " ")
    if not text:
        return type(exc).__name__
    return "%s: %s" % (type(exc).__name__, text[:200])


def _camel(name):
    """parse_react → parseReact。JS 的习惯写法，也认。"""
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


class _JsFunction:
    """`module.foo` 拿到的东西。调用时把活交给 node。"""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name

    def __repr__(self):
        return "<JS function %s>" % self._name

    def __call__(self, *args, **kwargs):
        if kwargs:
            raise Missing("%s() 这一章用不上关键字参数，按位置传就行。" % self._name)
        return self._owner.call(self._name, args)


class JsModule:
    """把学生的 JS 模块包成「像 Python 模块」的样子。

    代价是每次调用起一个 node 进程（几十毫秒）。一章十几条检查多几百毫秒，
    换来「同一份检查对两种语言都成立」，比给 33 章各写一份 JS 版检查划算得多。

    进来就先加载一次（见 `_load`）：语法错误、没装 node 这两类最常见的失败，
    只有在这里拦下来，才不会被摊成「每条检查都抛了同一个错」。
    """

    def __init__(self, project_dir, path):
        self.dir = project_dir
        self.path = path
        self.exports = []
        self._load()

    def __repr__(self):
        return "<JS module %s>" % os.path.basename(self.path)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _JsFunction(self, name)

    def _load(self):
        data = self._ask({"file": self.path})
        if not data.get("ok"):
            raise Missing("你的 JS 文件加载失败：%s" % (data.get("error") or "原因不明"))
        self.exports = [str(name) for name in (data.get("exports") or [])]

    def _ask(self, request):
        """跑一次桥，拿回一行 JSON。node 不在、启动失败、输出不合法都在这里说清。"""
        node = _node_path()
        if not node:
            raise Missing("这个项目里的代码是 JavaScript，但没找到 node。"
                          "装一个 Node.js，或者用 main.py 写 Python 版，两条路都行。")
        payload = json.dumps(request, ensure_ascii=False)
        try:
            proc = subprocess.run([node, HARNESS], input=payload, cwd=self.dir,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=JS_CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise Missing("node 超过 %d 秒没返回，检查一下有没有死循环。"
                          % JS_CALL_TIMEOUT)
        except OSError as exc:
            raise Missing("node 启动失败：%s" % exc)

        data = _last_json(proc.stdout)
        if data is None:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise Missing("node 没有按约定回话%s"
                          % ("：" + detail[-1][:200] if detail else "。"))
        return data

    def call(self, name, args):
        wanted = {name, _camel(name)}
        if not (wanted & set(self.exports)):
            have = "、".join(self.exports[:6]) if self.exports else "（一个都没有）"
            raise Missing("你的 JS 里没有导出 %s。现在导出的是：%s。"
                          "写成 module.exports = { %s: ... } 就行（%s 这样的写法也认）。"
                          % (name, have, name, _camel(name)))
        request = {"file": self.path, "func": name, "args": list(args)}
        try:
            # 先序列化一次：参数里混进函数/对象时，那句 TypeError 比 node 端
            # 收到的半个东西更容易解释
            json.dumps(request, ensure_ascii=False)
        except TypeError as exc:
            raise Missing("这一章要把一个 Python 对象传给 %s()，它没法变成 JSON（%s）。"
                          "这多半是这一章的验收只支持 Python。" % (name, exc))
        data = self._ask(request)
        if not data.get("ok"):
            raise Missing(str(data.get("error") or "你的代码抛了一个空错误"))
        return data.get("result")


def _last_json(text):
    """最后一行是 JSON 就返回它，否则 None。学生自己的 console.log 在前面，不影响。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def load(project_dir, names=DEFAULT_MODULES, languages=("python", "js")):
    """按顺序找学生的模块。返回 (module, path)。

    `languages` 是这一章允许的语言。默认两种都行；检查需要把 Python 函数
    传进学生代码的章（s1c3/s6c2/s7c1）传 `("python",)`。
    """
    tried = []
    if "python" in languages:
        for name in names:
            module, path = _load_module(project_dir, name)
            if module is not None:
                return module, path
            tried.append(name + ".py")

    js_path = _find_js(project_dir, names)
    if js_path and "js" in languages:
        return JsModule(project_dir, js_path), js_path
    if js_path:
        raise Missing("这一章的验收只支持 Python：它要把一个 Python 函数当参数"
                      "传进你的代码，换成 JS 就传不进去了。这一章请用 main.py 写。")
    raise Missing("项目目录里没找到 %s%s。把代码写在 main.py（或 main.js）里最省事。"
                  % (" / ".join(tried) or "代码文件",
                     "" if "js" in languages else "（这一章只支持 Python）"))


def expect_callable(module, attr):
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise Missing("找不到函数 %s()。这一章的练习要求它叫这个名字。" % attr)
    return fn


def item(items, name, ok, detail=""):
    items.append({"name": name, "ok": bool(ok), "detail": "" if ok else str(detail)[:400]})


def read_text(project_dir, filename, hint="", limit=MAX_TEXT):
    """读项目目录里的一个产物文件。返回 (文本, 错误)。

    找不到时把 `hint`（"这个文件应该放什么"）接在后面——学生最常见的
    失败是文件名差一个字母，而不是内容写错。
    """
    path = os.path.join(project_dir, filename)
    if not os.path.isfile(path):
        message = "没找到 %s。" % filename
        return None, (message + hint) if hint else message
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit), ""
    except OSError as exc:
        return None, "读不了 %s：%s" % (filename, exc)


def read_json(project_dir, filename, hint="", limit=MAX_TEXT):
    """读一个 JSON 产物。返回 (对象, 错误)。

    错误消息带上行列号：JSON 的报错本来就精确到列，扔掉它等于让学生
    自己数第几个逗号。
    """
    text, error = read_text(project_dir, filename, hint, limit)
    if text is None:
        return None, error
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return None, "%s 不是合法 JSON（%s）。注意不能有结尾逗号、不能用单引号。" % (
            filename, exc)


def finish(items):
    print(json.dumps({"items": items}, ensure_ascii=False))


def _collect(items, name, call):
    """跑一项，把结果（或异常）记成一条。单项失败不影响其它项。"""
    try:
        outcome = call()
        if isinstance(outcome, tuple):
            ok, detail = outcome
        else:
            ok, detail = bool(outcome), ""
    except Missing as exc:
        ok, detail = False, str(exc)
    except Exception:
        ok, detail = False, "运行时出错：" + _last_line()
    item(items, name, ok, detail)


def run(project_dir, cases, names=DEFAULT_MODULES, extra=(), need_module=True,
        languages=("python", "js")):
    """跑一组检查。

    `cases` 是 [(名字, fn)]，fn(module) 返回 (ok, detail) 或 True/False。
    单项里抛异常只记这一项失败——学生某个函数写崩了，不该让其它项也不出结果。

    `extra` 是 [(名字, fn)]，fn(module, project_dir) 返回同样形状。给「这一章
    还要交一个文件」的检查用：那类检查既要读文件、又常常要调学生刚写的函数，
    也必须和函数检查出现在同一份 JSON 里（执行器只认最后一行）。

    `need_module=False` 给「这一章不写代码、只交文档」的章用——不然会在
    第一条就报「没找到 main.py」，把真正的失败盖住。

    `languages=("python",)` 给「必须把 Python 函数传进学生代码」的章用，
    学生交 JS 时会得到一句解释，而不是一句 JSON 序列化错误。
    """
    items = []
    module = None
    if need_module:
        try:
            module, _path = load(project_dir, names, languages)
        except Missing as exc:
            item(items, "找得到你的代码", False, str(exc))
        except Exception:
            # 语法错误、模块级异常：把最后一行贴出来，前面几十行堆栈对他没用
            lines = traceback.format_exc().strip().splitlines()
            item(items, "你的代码能被 import", False, " / ".join(lines[-3:]))

    if module is not None:
        for name, fn in cases:
            _collect(items, name, lambda fn=fn, m=module: fn(m))
    # 代码读不到时也跑文件检查：两样都缺，一次说清比修一样再跑一次省事
    for name, fn in extra:
        _collect(items, name, lambda fn=fn, m=module, p=project_dir: fn(m, p))

    finish(items)
