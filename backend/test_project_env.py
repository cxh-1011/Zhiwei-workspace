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
import time

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

# ---------------------------------------------------------------- 清单改了要重装

section("清单指纹：只认「装什么」")
check("顺序不影响指纹",
      envs.fingerprint(["requests>=2.31", "flask"]) == envs.fingerprint(["flask", "requests>=2.31"]))
check("大小写不影响指纹",
      envs.fingerprint(["Flask"]) == envs.fingerprint(["flask"]),
      (envs.fingerprint(["Flask"]), envs.fingerprint(["flask"])))
check("行内注释不影响指纹",
      envs.fingerprint(["flask  # 网页框架"]) == envs.fingerprint(["flask"]))
check("空行不影响指纹",
      envs.fingerprint(["flask", "", "   "]) == envs.fingerprint(["flask"]))
check("加一个包就变",
      envs.fingerprint(["flask"]) != envs.fingerprint(["flask", "requests"]))
check("改版本号就变",
      envs.fingerprint(["requests>=2.31"]) != envs.fingerprint(["requests>=2.32"]))

section("state：清单和已装的是否一致")
sync_demo = new_project("sync-demo")
sync_req = os.path.join(sync_demo, "requirements.txt")


def write_req(text):
    with open(sync_req, "w", encoding="utf-8") as handle:
        handle.write(text)


check("没有清单时 state=none", envs.state(sync_demo)["state"] == "none", envs.state(sync_demo))
envs.ensure(sync_demo, with_pip=False, timeout=120)
write_req("flask\n")
st = envs.state(sync_demo)
check("有清单没记录 → unrecorded 且要装", st["state"] == "unrecorded" and st["dirty"] is True, st)
check("unrecorded 时 info 也这么报",
      envs.info(sync_demo)["requirements"]["state"] == "unrecorded", envs.info(sync_demo))

envs._record_success(sync_demo, ["flask"])
st = envs.state(sync_demo)
check("记下之后 state=ok", st["state"] == "ok" and st["dirty"] is False, st)
check("ok 时不报新增", st["added"] == [] and st["removed"] == [], st)

write_req("flask\nrequests>=2.31\n")
st = envs.state(sync_demo)
check("加包 → changed", st["state"] == "changed" and st["dirty"] is True, st)
check("说得出加了哪个", st["added"] == ["requests>=2.31"], st)
check("没说删了什么", st["removed"] == [], st)

write_req("flask\n")
st = envs.state(sync_demo)
check("改回去 → ok（指纹按清单算，不按时间）", st["state"] == "ok", st)

write_req("requests>=2.31\n")
st = envs.state(sync_demo)
check("删包也能看出来", st["removed"] == ["flask"] and st["state"] == "changed", st)

write_req("-e .\n")
check("清单不合法 → invalid", envs.state(sync_demo)["state"] == "invalid", envs.state(sync_demo))
check("invalid 时 info 的 error 有话说",
      bool(envs.info(sync_demo)["requirements"]["error"]), envs.info(sync_demo))

write_req("flask\n")
st = envs.state(sync_demo)
check("恢复成装过的那份 → ok", st["state"] == "ok", st)

# 失败记录：同一份清单别再自动撞一次
write_req("flask\nrequests>=2.31\n")
envs._record_failure(sync_demo, ["flask", "requests>=2.31"], "网络不可达：connection timed out")
st = envs.state(sync_demo)
check("失败后 state=failed", st["state"] == "failed", st)
check("失败不触发自动重试", st["dirty"] is False, st)
check("失败原因带出来", "网络不可达" in st["error"], st)
info = envs.info(sync_demo)
check("info 把失败原因放 last_error（不是 error）",
      "网络不可达" in info["requirements"]["last_error"]
      and info["requirements"]["error"] == "", info["requirements"])
check("成功那份记录没被失败覆盖",
      envs.read_stamp(sync_demo)["packages"] == ["flask"], envs.read_stamp(sync_demo))

write_req("flask\n")
st = envs.state(sync_demo)
check("改回装过的那份 → ok，不再提上次失败", st["state"] == "ok" and st["error"] == "", st)

write_req("flask\nnumpy\n")
st = envs.state(sync_demo)
check("换成另一份清单 → 允许再试一次", st["state"] == "changed" and st["dirty"] is True, st)

# 记录跟着环境走：.venv 被删掉，记录一起没了
envs._record_success(sync_demo, ["flask"])
envs._rm_venv(sync_demo)
check("删掉 .venv 后记录也没了",
      envs.read_stamp(sync_demo) is None, envs.read_stamp(sync_demo))
check("删掉 .venv 后 state=no_env", envs.state(sync_demo)["state"] == "no_env",
      envs.state(sync_demo))

section("sync：只在清单变了的时候动手")
sync_project = new_project("sync-run")
envs.ensure(sync_project, with_pip=False, timeout=120)
with open(os.path.join(sync_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("flask\n")
envs._record_success(sync_project, ["flask"])
result = envs.sync(sync_project)
check("没改就什么都不做", result["changed"] is False and result["installed"] is False, result)
check("没改时耗时是 0（说明真没起进程）", result["elapsed_ms"] == 0, result)

# 改了：这个 venv 是 --without-pip 建的，pip 必然失败。用它来验「失败也要记下来」。
with open(os.path.join(sync_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("flask\nrequests>=2.31\n")
first = envs.sync(sync_project, timeout=60)
check("失败不抛异常，写进返回值", first["changed"] is True and first["installed"] is False, first)
check("失败时带上原因", bool(first["error"]), first)
check("失败后 state=failed", envs.state(sync_project)["state"] == "failed",
      envs.state(sync_project))

second = envs.sync(sync_project, timeout=60)
check("第二次不再自动重试", second["changed"] is False and second["elapsed_ms"] == 0, second)
check("但仍然告诉调用方上次失败了", bool(second["error"]), second)

section("断网和包名写错要分开说")
# 这两段输出来自真机（PIP_INDEX_URL 指向一个没人监听的端口）。
# 注意它们的结尾一模一样：都是 No matching distribution found。
OFFLINE_OUTPUT = (
    "WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, "
    "status=None)) after connection broken by 'NewConnectionError('<pip._vendor."
    "urllib3.connection.HTTPConnection object at 0x1>: Failed to establish a new "
    "connection: [WinError 10061] 由于目标计算机积极拒绝，无法连接。')': /simple/requests/\n"
    "ERROR: Could not find a version that satisfies the requirement requests>=2.31 "
    "(from versions: none)\n"
    "ERROR: No matching distribution found for requests>=2.31\n"
)
MISSING_OUTPUT = (
    "ERROR: Could not find a version that satisfies the requirement zhiwei-nope "
    "(from versions: none)\n"
    "ERROR: No matching distribution found for zhiwei-nope\n"
)

check("断网判成 offline", envs.failure_kind(OFFLINE_OUTPUT) == "offline",
      envs.failure_kind(OFFLINE_OUTPUT))
check("包名写错判成 missing", envs.failure_kind(MISSING_OUTPUT) == "missing",
      envs.failure_kind(MISSING_OUTPUT))
check("两种输出结尾是同一句 ERROR，结论仍然不同",
      "No matching distribution found" in OFFLINE_OUTPUT
      and "No matching distribution found" in MISSING_OUTPUT
      and envs.failure_kind(OFFLINE_OUTPUT) != envs.failure_kind(MISSING_OUTPUT))

offline_msg = envs._fail_message("offline", 1, OFFLINE_OUTPUT)
check("断网的话先说「要联网」", "联网" in offline_msg, offline_msg[:80])
check("断网的话给出下一步", "重试装依赖" in offline_msg, offline_msg[:120])
check("断网的话不安罪给包名", "拼错" not in offline_msg, offline_msg[:120])
missing_msg = envs._fail_message("missing", 1, MISSING_OUTPUT)
check("包名写错的话先说拼错", "拼错" in missing_msg, missing_msg[:80])
check("包名写错的话不说断网", "连不上" not in missing_msg, missing_msg[:80])
check("原话仍然附在后面", "No matching distribution" in offline_msg, offline_msg[-120:])

section("pip 被杀后留下的临时目录要清掉")
litter = new_project("litter")
for name in ("pip-build-tracker-abc", "pip-install-x1", "pip-ephem-wheel-cache-z"):
    os.makedirs(os.path.join(litter, name))
keep = os.path.join(litter, "mypackage")
os.makedirs(keep)
with open(os.path.join(litter, "main.py"), "w", encoding="utf-8") as handle:
    handle.write("print(1)\n")
envs._clean_pip_leftovers(litter)
check("清掉了 pip 的三类临时目录",
      not glob.glob(os.path.join(litter, "pip-*")), os.listdir(litter))
check("没动学生自己的东西",
      os.path.isdir(keep) and os.path.isfile(os.path.join(litter, "main.py")),
      os.listdir(litter))

section("真断网装一次：几十秒内失败，并且说清是网络")
offline_project = new_project("offline-install")
with open(os.path.join(offline_project, "requirements.txt"), "w", encoding="utf-8") as handle:
    handle.write("requests>=2.31\n")
# 带 pip 的 venv 只有这一处需要（其余用例都用 --without-pip 省时间）
envs.ensure(offline_project, timeout=180)
previous_index = os.environ.get("PIP_INDEX_URL")
os.environ["PIP_INDEX_URL"] = "http://127.0.0.1:9/simple"
started = time.monotonic()
offline_error = ""
try:
    try:
        envs.install(offline_project, timeout=120)
    except envs.EnvError as exc:
        offline_error = str(exc)
    elapsed = time.monotonic() - started
finally:
    if previous_index is None:
        os.environ.pop("PIP_INDEX_URL", None)
    else:
        os.environ["PIP_INDEX_URL"] = previous_index

check("连不上时不该装成功", bool(offline_error), offline_error[:120])
check("没有等满 120 秒", elapsed < 60, round(elapsed, 1))
check("说的话点明是网络", "连不上 PyPI" in offline_error, offline_error[:200])
check("顺手告诉了下一步", "重试装依赖" in offline_error, offline_error[:200])
check("失败类型记成 offline", envs.state(offline_project)["kind"] == "offline",
      envs.state(offline_project))
check("没把断网说成包名拼错", "拼错" not in offline_error, offline_error[:200])
check("这次失败不会自动重试", envs.sync(offline_project)["changed"] is False,
      envs.sync(offline_project))

# ---------------------------------------------------------------- 收尾

print("\n通过 %d，失败 %d" % (len(PASSED), len(FAILED)))
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - %s" % name)
    sys.exit(1)

shutil.rmtree(TEMP, ignore_errors=True)
