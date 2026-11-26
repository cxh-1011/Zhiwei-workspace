"""项目级 Python 环境的离线测试：建 venv、找解释器、装依赖的前置校验。

最关键的一条是「验收真的跑在项目 venv 里」：造一个只存在于 venv site-packages
里的模块，让验收脚本 import 它。如果 checks.run 还用 sys.executable，这条会失败。
只断言 `python_source == "venv"` 是不够的——那只是自己报的数。

不联网：装依赖只测「不该装」的路径（文件不存在、清单不合法、选项被拒）。
真去 pip install 会让这个测试在断网机器上红，而那不代表代码坏了。
"""

import glob
import json
import os
import pathlib
import shutil
import sys
import tempfile

TEMP = tempfile.mkdtemp(prefix="test_envs_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks  # noqa: E402
import envs  # noqa: E402
from tools import _guard, command  # noqa: E402

FAILED = []
PASSED = []


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        return
    FAILED.append(name)
    print("  FAIL %s  → %s" % (name, detail))


def section(title):
    print("\n[%s]" % title)


def new_project(name):
    path = os.path.join(TEMP, name)
    os.makedirs(path, exist_ok=True)
    return path


def site_packages(project_dir):
    """项目 venv 的 site-packages 目录（跨平台）。"""
    venv_root = os.path.join(project_dir, envs.VENV_DIR)
    if os.name == "nt":
        return os.path.join(venv_root, "Lib", "site-packages")
    hits = glob.glob(os.path.join(venv_root, "lib", "python*", "site-packages"))
    return hits[0] if hits else os.path.join(venv_root, "lib", "site-packages")


# ---------------------------------------------------------------- requirements

section("requirements.txt 的校验")
good_lines = [
    "requests",
    "requests>=2.31",
    "flask[async]>=2.0,<3",
    "numpy==1.26.4",
    "  pandas  ",
    "python-dotenv~=1.0",
    "# 这是注释",
    "",
]
for line in good_lines:
    problem = envs._check_requirement(line, 1)
    check("接受 %r" % line.strip()[:24], not problem, problem)

bad_cases = [
    ("-e .", "pip 选项"),
    ("--index-url https://evil.example/simple", "pip 选项"),
    ("git+https://github.com/a/b.git", "URL"),
    ("requests @ https://example.com/x.whl", "URL"),
    ("./local-pkg", "本地路径"),
    ("../outside", "本地路径"),
    ("requests; python_version<'3.9'", "环境标记"),
    ("requests --hash=sha256:abc", "空格"),
    ("a && b", "shell 符号"),
    ("requests#", ""),  # 行内注释吃掉后是合法包名，应通过
]
for text, keyword in bad_cases:
    problem = envs._check_requirement(text, 3)
    if keyword:
        check("拒绝 %r" % text[:26], bool(problem) and keyword.split()[0][:4] in problem,
              problem or "居然通过了")
    else:
        check("接受带行内注释的 %r" % text, not problem, problem)

check("拒绝超长行", bool(envs._check_requirement("x" * 200, 2)))
check("拒绝非法包名", bool(envs._check_requirement("..", 2)), envs._check_requirement("..", 2))
check("拒绝非法约束", bool(envs._check_requirement("requests=>2", 2)))

# read_requirements：文件不存在不是错误
empty_project = new_project("no-req")
packages, problem = envs.read_requirements(empty_project)
check("没有 requirements.txt 返回空列表", packages == [] and not problem, (packages, problem))

with open(os.path.join(empty_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("requests>=2.31\n# 注释\nflask\n")
packages, problem = envs.read_requirements(empty_project)
check("读出两个包", packages == ["requests>=2.31", "flask"], (packages, problem))

with open(os.path.join(empty_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("requests\n-e .\n")
packages, problem = envs.read_requirements(empty_project)
check("一行不合法就整份拒绝", packages == [] and "第 2 行" in problem, problem)

with open(os.path.join(empty_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("\n".join("pkg%d" % i for i in range(40)))
packages, problem = envs.read_requirements(empty_project)
check("超过 30 行拒绝", packages == [] and "上限" in problem, problem)

# ---------------------------------------------------------------- 找解释器

section("解释器发现")
candidates = envs._candidate_pythons(empty_project)
first_folder = os.path.basename(os.path.dirname(candidates[0]))
check("Windows 先找 Scripts", (first_folder == "Scripts") == (os.name == "nt"), first_folder)
check("没有 .venv 时返回空串", envs.venv_python(empty_project) == "")
check("没有 .venv 时用系统解释器",
      envs.python_for(empty_project) == (sys.executable, "system"),
      envs.python_for(empty_project))
check("没有 .venv 时 info 说没有", envs.info(empty_project)["present"] is False)

# 残缺目录：目录在、python 不在
broken = new_project("broken")
os.makedirs(os.path.join(broken, envs.VENV_DIR, "Scripts"), exist_ok=True)
check("残缺 .venv 不算有环境", envs.venv_python(broken) == "")

# ---------------------------------------------------------------- 真建一个

section("真建一个 venv（--without-pip，只要几秒）")
project = new_project("real")
result = envs.ensure(project, with_pip=False, timeout=120)
check("ensure 说建好了", result.get("created") is True, result)
python = result.get("python") or ""
check("解释器文件真的在", bool(python) and os.path.isfile(python), python)

again = envs.ensure(project, with_pip=False, timeout=120)
check("第二次不重建", again.get("created") is False, again)
check("两次是同一个解释器", again.get("python") == python, (again.get("python"), python))

check("python_for 认这个 venv", envs.python_for(project) == (python, "venv"),
      envs.python_for(project))
info = envs.info(project)
check("info 说 present", info["present"] is True, info)
check("info 读出 pyvenv.cfg 的版本", bool(info.get("version")), info)
check("没 pip 的 venv 如实说 pip=False", info.get("pip") is False, info)

# 真跑一句：sys.prefix 必须落在项目目录里
code, out, err, timed_out = envs._run(
    [python, "-c", "import sys;print(sys.prefix)"], project, 30)
check("venv 解释器能启动", not timed_out and code == 0, err[-300:])
check("sys.prefix 指向项目",
      os.path.normcase(os.path.realpath(out.strip())).startswith(
          os.path.normcase(os.path.realpath(project))),
      out.strip())

# 隔离证据：只装进 venv site-packages 的模块，venv 能 import，系统 python 不能
site = site_packages(project)
os.makedirs(site, exist_ok=True)
with open(os.path.join(site, "venv_only_probe_zzz.py"), "w", encoding="utf-8") as handle:
    handle.write("VALUE = 42\n")

code, out, err, timed_out = envs._run(
    [python, "-c", "import venv_only_probe_zzz as m;print(m.VALUE)"], project, 30)
check("venv 里能 import 只装在 venv 的模块", code == 0 and out.strip() == "42", out + err)

code, out, err, timed_out = envs._run(
    [sys.executable, "-c", "import venv_only_probe_zzz"], project, 30)
check("系统解释器 import 不到它", code != 0, out)

# ---------------------------------------------------------------- 验收真的用 venv

section("验收跑在项目 venv 里")
course_root = os.path.join(TEMP, "curriculum")
checks_dir = os.path.join(course_root, "demo", "checks")
os.makedirs(checks_dir, exist_ok=True)
with open(os.path.join(checks_dir, "t1.py"), "w", encoding="utf-8") as handle:
    handle.write("""import json
ok = False
detail = ""
try:
    import venv_only_probe_zzz as probe
    ok = probe.VALUE == 42
except Exception as exc:
    detail = "import 失败：%s" % exc
print(json.dumps({"items": [{"name": "venv 里的模块能 import", "ok": ok, "detail": detail}]}))
""")

original_root = checks.CURRICULUM_ROOT
try:
    checks.CURRICULUM_ROOT = pathlib.Path(course_root)
    result = checks.run(project, "t1", "demo", timeout=60)
    check("验收结论: ok", result.get("ok") is True, result.get("items"))
    check("结果标明用了项目环境", result.get("python_source") == "venv",
          result.get("python_source"))

    # 反证：同一份脚本、同一个目录，但没有 venv → 系统解释器 → 失败
    plain = new_project("plain")
    result2 = checks.run(plain, "t1", "demo", timeout=60)
    check("没有 venv 时用系统解释器", result2.get("python_source") == "system",
          result2.get("python_source"))
    check("没有 venv 时这条检查确实不过", result2.get("ok") is False, result2.get("items"))
finally:
    checks.CURRICULUM_ROOT = original_root

# ---------------------------------------------------------------- 命令工具

section("run_command 的 python / pip / pytest 跟着项目走")
_guard.register_root(project)
token = _guard.use_root(project)
try:
    argv = command._resolve_argv(["python", "main.py"])
    check("python 换成 venv 解释器", argv[0] == python, argv)
    argv = command._resolve_argv(["pip", "install", "requests"])
    check("pip 换成 venv 的 pip", argv[:3] == [python, "-m", "pip"], argv)
    argv = command._resolve_argv(["pytest", "-q"])
    check("pytest 换成 venv 的 pytest", argv[:3] == [python, "-m", "pytest"], argv)
finally:
    _guard.reset_root(token)

# 不在沙箱里时不换，保持原命令
argv = command._resolve_argv(["python", "main.py"])
check("仓库根下不改写 python", argv[0] == sys.executable, argv)
argv = command._resolve_argv(["pip", "install", "requests"])
check("仓库根下不改写 pip", argv[0] == "pip", argv)

# ---------------------------------------------------------------- 装依赖的拒绝路径

section("装依赖：不该装的时候要说清原因")
# 空目录：没有 requirements.txt
empty = new_project("empty-req")
try:
    envs.install(empty)
    check("没有清单时要报错", False)
except envs.EnvError as exc:
    check("没有清单时报的是清单问题", "没有可安装的包" in str(exc), str(exc))

# 有清单但只有注释
comment_only = new_project("comment-req")
with open(os.path.join(comment_only, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("# 先不装东西\n\n")
try:
    envs.install(comment_only)
    check("只有注释也要报错", False)
except envs.EnvError as exc:
    check("只有注释时说没有可装的包", "没有可安装的包" in str(exc), str(exc))

try:
    envs.install(new_project("ghost"))
except envs.EnvError as exc:
    check("目录不存在时报目录问题", "不存在" in str(exc), str(exc))

try:
    envs.install(os.path.join(TEMP, "no-such-dir"))
except envs.EnvError as exc:
    check("假目录也报目录问题", "不存在" in str(exc), str(exc))

try:
    envs.ensure(os.path.join(TEMP, "no-such-dir"))
except envs.EnvError as exc:
    check("ensure 对不存在的目录报错", "不存在" in str(exc), str(exc))

# ---------------------------------------------------------------- 收尾

print("\n通过 %d，失败 %d" % (len(PASSED), len(FAILED)))
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - %s" % name)
    sys.exit(1)

shutil.rmtree(TEMP, ignore_errors=True)
