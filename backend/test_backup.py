"""数据库备份的离线测试：真的备了、不覆盖已有的、会剪掉旧的、失败不拦启动。

这组测试守的是一条底线：**备份逻辑本身不能成为新的数据风险**。
所以除了「能备份」，还要验证「不会把好备份冲掉」和「出问题时静悄悄跳过」——
一个会吞掉旧备份的备份功能，比没有备份更危险。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def fresh_env():
    """每轮一套全新的 库 + 备份目录。绝不碰真实的 chats.db。"""
    root = tempfile.mkdtemp(prefix="backup-test-")
    database.DB_PATH = os.path.join(root, "chats.db")
    return root, os.path.join(root, "backups")


def seed_chats(count=1):
    """建表并塞几条对话，让库变成「有内容」的。"""
    database.init_db()
    for index in range(count):
        database.create_chat("对话 %d" % index)


def count_in(db_file, table="chats"):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
    finally:
        conn.close()


def test_empty_db():
    print("\n[空库不该占备份位]")
    root, dest = fresh_env()
    database.init_db()  # 只有表结构，没有任何对话

    check("库不存在时返回 None", database.backup_db(dest) is None)
    check("空库时返回 None", database.backup_db(dest) is None)
    check("没建出备份目录", not os.path.exists(dest))


def test_backup_created():
    print("\n[有数据就备份，且内容是完整的]")
    root, dest = fresh_env()
    seed_chats(3)

    path = database.backup_db(dest, day="2026-11-06")
    check("返回了备份路径", bool(path), path)
    check("文件名带日期", path and path.endswith("chats-2026-11-06.db"), path)
    check("文件真的存在", path and os.path.exists(path))

    # 备份要能直接用：打开它查数据，而不是只看文件大小
    check("备份里有全部对话", count_in(path) == 3, count_in(path))
    check("备份能当库打开（表结构完整）", count_in(path, "messages") == 0)

    # 备份完原库没被动过
    check("原库还在且没少数据", count_in(database.DB_PATH) == 3)


def test_no_overwrite_same_day():
    print("\n[同一天不覆盖：好备份不能被坏数据冲掉]")
    root, dest = fresh_env()
    seed_chats(2)

    first = database.backup_db(dest, day="2026-11-06")
    check("第一份备份成功", first and os.path.exists(first))

    # 模拟「备份之后又造了更多数据」——真实场景里更常见的是删了数据
    database.create_chat("后来新增的对话")
    second = database.backup_db(dest, day="2026-11-06")

    check("同一天第二次返回 None", second is None, second)
    check("旧备份仍是 2 条，没被新状态覆盖", count_in(first) == 2, count_in(first))
    check("目录里只有一份", len(os.listdir(dest)) == 1, os.listdir(dest))

    # 换一天就应该生成新的一份
    third = database.backup_db(dest, day="2026-11-07")
    check("隔天会生成新备份", third and os.path.exists(third))
    check("新备份有 3 条", count_in(third) == 3, count_in(third))


def test_prune():
    print("\n[只留最近 N 天]")
    root, dest = fresh_env()
    seed_chats(1)
    os.makedirs(dest, exist_ok=True)

    # 造 10 个「历史备份」占位文件，日期递增
    for day in range(1, 11):
        with open(os.path.join(dest, "chats-2026-01-%02d.db" % day), "w") as handle:
            handle.write("old")
    check("先有 10 个占位", len(os.listdir(dest)) == 10, len(os.listdir(dest)))

    database.backup_db(dest, keep=7, day="2026-02-01")
    names = sorted(os.listdir(dest))
    check("剪到 7 份", len(names) == 7, names)
    check("留下的是最新的", names[-1] == "chats-2026-02-01.db", names)
    check("最旧的被删了", "chats-2026-01-01.db" not in names, names)
    check("次旧的也被删了", "chats-2026-01-04.db" not in names, names)
    check("最近的占位留下来了", "chats-2026-01-10.db" in names, names)

    # 别的文件不该被误删
    other = os.path.join(dest, "notes.txt")
    with open(other, "w") as handle:
        handle.write("x")
    database.backup_db(dest, keep=3, day="2026-02-02")
    check("非备份文件保留", os.path.exists(other))
    check("keep=3 时只剩 3 份 db", len([n for n in os.listdir(dest) if n.endswith(".db")]) == 3,
          os.listdir(dest))


def test_failure_is_quiet():
    print("\n[备份失败不能拦住启动]")
    root, dest = fresh_env()
    seed_chats(1)

    # 把「备份目录」指到一个已存在的普通文件上，makedirs 必然失败
    blocker = os.path.join(root, "blocked")
    with open(blocker, "w") as handle:
        handle.write("not a dir")

    result = None
    raised = None
    try:
        result = database.backup_db(blocker)
    except Exception as exc:  # noqa: BLE001 - 这里就是要证明它不会抛
        raised = exc
    check("没有抛异常", raised is None, raised)
    check("失败时返回 None", result is None, result)

    # 库本身没被搞坏
    check("原库仍可读", count_in(database.DB_PATH) == 1)


def test_default_dir():
    print("\n[默认落到项目根的 backups/db]")
    check("BACKUP_DIR 在项目根下", database.BACKUP_DIR.endswith(os.path.join("backups", "db")),
          database.BACKUP_DIR)
    check("默认保留 7 天", database.BACKUP_KEEP == 7, database.BACKUP_KEEP)
    # 默认路径必须是绝对路径，否则从不同目录启动会各存一份
    check("是绝对路径", os.path.isabs(database.BACKUP_DIR), database.BACKUP_DIR)


def main_():
    test_empty_db()
    test_backup_created()
    test_no_overwrite_same_day()
    test_prune()
    test_failure_is_quiet()
    test_default_dir()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
