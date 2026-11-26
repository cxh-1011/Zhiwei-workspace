"""多语言验收的测试：同一份检查脚本，学生用 JS 写也要能验。

为什么值得单独测
----------------
`_kit` 把 JS 模块包成了「长得像 Python 模块」的对象，这条路只有真的用 JS
写过一遍才知道通不通：能不能拿到函数、参数过去对不对、返回值回来是什么类型、
报错能不能说人话。所以这里直接跑**真的验收脚本**（s2c3 / s10c2），
配一份真 JS 实现，而不是只断言「检测到 node」。

不联网、不装包：node 是本机已有的；没有 node 的机器上这一组会跳过并说明。
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
TEMP = tempfile.mkdtemp(prefix="test_multilang_")
sys.path.insert(0, BACKEND)

import checks  # noqa: E402
import runtimes  # noqa: E402

COURSE = os.path.join(BACKEND, "..", "curriculum")
FAILED = []
PASSED = []
SKIPPED = []

NODE = runtimes.get("node")


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        return
    FAILED.append(name)
    print("  FAIL %s  → %s" % (name, detail))


def section(title):
    print("\n[%s]" % title)


def skip(name, why):
    SKIPPED.append(name)
    print("  skip %s  → %s" % (name, why))


def project(name, files):
    path = os.path.join(TEMP, name)
    os.makedirs(path, exist_ok=True)
    for filename, content in files.items():
        full = os.path.join(path, filename)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) != path else None
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
    return path


# ---------------------------------------------------------------- 语言判断

section("项目语言：看文件，不看配置")
py_only = project("lang-py", {"main.py": "x = 1\n"})
js_only = project("lang-js", {"main.js": "module.exports = {}\n"})
both = project("lang-both", {"main.py": "x = 1\n", "main.js": "module.exports = {}\n"})
empty = project("lang-empty", {})
venv_py = project("lang-venv", {
    "main.js": "module.exports = {}\n",
    ".venv/Lib/site-packages/requests/__init__.py": "x = 1\n",
})
dotted = project("lang-dotted", {"main.mjs": "export const a = 1;\n"})

check("只有 .py → python", runtimes.student_language(py_only) == "python")
check("只有 .js → node", runtimes.student_language(js_only) == "node")
check("两种都有 → python（正文示例是 Python，交的那份也是）",
      runtimes.student_language(both) == "python", runtimes.student_language(both))
check("空目录 → python（报错文案仍然说 main.py）",
      runtimes.student_language(empty) == "python")
check(".venv 里的 .py 不算数", runtimes.student_language(venv_py) == "node",
      runtimes.student_language(venv_py))
check(".mjs 也算 JS", runtimes.student_language(dotted) == "node")
check("目录不存在不抛异常", runtimes.student_language(os.path.join(TEMP, "nope")) == "python")

section("运行时发现")
if NODE:
    check("找到 node", bool(NODE.get("path")), NODE)
    check("读到版本号", bool(NODE.get("version")) and NODE["version"][0].isdigit(), NODE)
    check("路径真的存在", os.path.isfile(NODE["path"]), NODE)
else:
    skip("找到 node", "这台机器没有 node，JS 相关用例全部跳过")

# ---------------------------------------------------------------- 真跑 JS

JS_S2C3 = r"""
function extractJson(text) {
  const match = String(text).match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    return JSON.parse(match[0]);
  } catch (err) {
    return null;
  }
}

function validateWeather(data) {
  if (!data || typeof data !== 'object') return false;
  if (typeof data.city !== 'string' || data.city.length === 0) return false;
  if (typeof data.temperature !== 'number') return false;
  if (!['celsius', 'fahrenheit'].includes(data.unit)) return false;
  return true;
}

// 注意这里用的是验收要求里的名字（snake_case），不是 JS 习惯的 camelCase
module.exports = { extract_json: extractJson, validate_weather: validateWeather };
"""

JS_S10C2 = r"""
// 这一份刻意用 camelCase 导出：验收要求写的是 classify_feedback，
// JS 习惯写 classifyFeedback，两个都该认（否则「写对了却判不过」）
export function classifyFeedback(text) {
  const value = String(text || '');
  if (/能不能|希望|支持|加个|要是能/.test(value)) return '功能缺失';
  if (/错|不对|胡说|答错|算错/.test(value)) return '回答错误';
  if (/等|秒|卡|慢|延迟|转圈/.test(value)) return '太慢';
  return '其他';
}

export function priority(repeat, blocksMain) {
  if (repeat && blocksMain) return '高';
  if (repeat || blocksMain) return '中';
  return '低';
}
"""

BAD_TYPES = r"""
function extractJson(text) {
  return String(text);            // 该返回对象，返回了字符串
}
function validateWeather(data) {
  return 'true';                  // 该返回布尔，返回了字符串
}
module.exports = { extractJson, validateWeather };
"""

NO_EXPORT = r"""
module.exports = { somethingElse: function () { return 1; } };
"""

SYNTAX_ERROR = "function broken( {\n"

THROWS = r"""
module.exports = {
  extract_json: function () { throw new Error('我故意抛的'); },
  validate_weather: function () { return true; },
};
"""

if NODE:
    section("真跑一章 JS：s2c3（提取 JSON + 校验字段）")
    good = project("js-s2c3", {"main.js": JS_S2C3})
    result = checks.run(good, "s2c3", "agent-dev", timeout=90)
    check("JS 实现能通过", result["ok"] is True, result.get("items"))
    check("结果标明语言是 node", result.get("language") == "node", result.get("language"))
    check("结果带上 node 版本", bool(result.get("runtime_version")), result.get("runtime_version"))
    check("三条检查都跑了", result.get("total") == 3, result.get("items"))

    section("同一份检查，错的地方要说得具体")
    bad = project("js-bad-types", {"main.js": BAD_TYPES})
    result = checks.run(bad, "s2c3", "agent-dev", timeout=90)
    check("类型不对判不过", result["ok"] is False, result.get("items"))
    details = " ".join(item["detail"] for item in result["items"])
    check("说了「返回了 str」", "str" in details, details[:200])
    check("说了要布尔值", "布尔" in details or "bool" in details, details[:200])

    section("常见写错的样子都要有人话")
    no_export = project("js-no-export", {"main.js": NO_EXPORT})
    result = checks.run(no_export, "s2c3", "agent-dev", timeout=90)
    detail = result["items"][0]["detail"] if result["items"] else ""
    check("没导出函数时说清怎么导出", "没有导出" in detail, detail[:200])
    check("顺带给了 JS 写法", "module.exports" in detail or "export function" in detail,
          detail[:200])

    broken = project("js-syntax", {"main.js": SYNTAX_ERROR})
    result = checks.run(broken, "s2c3", "agent-dev", timeout=90)
    detail = result["items"][0]["detail"] if result["items"] else ""
    check("语法错时说「加载失败」", "加载失败" in detail, detail[:200])
    check("语法错时不会甩一屏堆栈", len(detail) < 300, len(detail))

    throwing = project("js-throws", {"main.js": THROWS})
    result = checks.run(throwing, "s2c3", "agent-dev", timeout=90)
    details = " ".join(item["detail"] for item in result["items"])
    check("学生代码抛的错原样带回来", "我故意抛的" in details, details[:200])

    section("ESM 写法也要能用（s10c2，camelCase 导出）")
    esm = project("js-esm", {"main.js": JS_S10C2})
    result = checks.run(esm, "s10c2", "agent-dev", timeout=90)
    check("export function + camelCase 能通过", result["ok"] is True, result.get("items"))
    check("这章三条检查都跑了", result.get("total") == 3, result.get("total"))

    section("只支持 Python 的章要明说")
    for chapter, label in (("s6c2", "要传函数"), ("s7c1", "要传回调"), ("s1c3", "要传 encode")):
        js_project = project("js-only-%s" % chapter, {"main.js": "module.exports = {};\n"})
        result = checks.run(js_project, chapter, "agent-dev", timeout=90)
        detail = result["items"][0]["detail"] if result["items"] else ""
        check("%s（%s）用 JS 时解释清楚" % (chapter, label),
              "只支持 Python" in detail, detail[:160])

    section("有 main.py 时仍然走 Python（JS 文件不影响）")
    mixed = project("js-mixed", {"main.py": "def extract_json(text):\n    return None\n",
                                 "main.js": JS_S2C3})
    result = checks.run(mixed, "s2c3", "agent-dev", timeout=90)
    check("语言判成 python", result.get("language") == "python", result.get("language"))
    check("用的是 py 那份（所以判不过）", result["ok"] is False, result.get("items"))
else:
    skip("JS 验收", "没有 node")

# ---------------------------------------------------------------- 没有 node

section("没有 node 时的说法")
# 直接测 _kit：把 ZHIWEI_NODE 清掉、which 打死，模拟一台没装 node 的机器
sys.path.insert(0, os.path.join(COURSE, "agent-dev", "checks"))
spec = importlib.util.spec_from_file_location(
    "kit_under_test", os.path.join(COURSE, "agent-dev", "checks", "_kit.py"))
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

saved_node = os.environ.pop("ZHIWEI_NODE", None)
saved_which = kit.shutil.which
kit.shutil.which = lambda name: None
try:
    js_project = project("js-no-node", {"main.js": "module.exports = {};\n"})
    try:
        kit.load(js_project)
        check("没有 node 时加载 JS 应该失败", False)
    except kit.Missing as exc:
        message = str(exc)
    check("说明是缺 node", "没找到 node" in message, message[:160])
    check("给了两条出路", "Node.js" in message and "main.py" in message, message[:200])

    # 有 .py 的项目在没 node 的机器上照常能验
    py_project = project("py-no-node", {"main.py": "def f():\n    return 1\n"})
    module, path = kit.load(py_project)
    check("没有 node 也不影响 Python 项目", callable(getattr(module, "f")), path)
finally:
    kit.shutil.which = saved_which
    if saved_node is not None:
        os.environ["ZHIWEI_NODE"] = saved_node

# ---------------------------------------------------------------- 收尾

print("\n通过 %d，失败 %d，跳过 %d" % (len(PASSED), len(FAILED), len(SKIPPED)))
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - %s" % name)
    sys.exit(1)

shutil.rmtree(TEMP, ignore_errors=True)
