import os
import sqlite3
import json
import logging
import time


logger = logging.getLogger("ai_robot.database")


# 数据库文件路径（基于本文件定位，避免在不同目录启动时又建一个新库）

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats.db")

# 备份目录在项目根的 backups/db 下，和代码回滚快照分开放
BACKUP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups", "db"
)
BACKUP_KEEP = 7



# 初始化数据库

def init_db():

    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        title TEXT

    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_runs(
        run_id TEXT PRIMARY KEY,
        chat_id INTEGER,
        question TEXT,
        reply TEXT,
        status TEXT,
        rounds INTEGER,
        duration_ms INTEGER,
        total_tokens INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_steps(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_index INTEGER,
        round_no INTEGER,
        kind TEXT,
        tool TEXT,
        args TEXT,
        ok INTEGER,
        elapsed_ms INTEGER,
        result_preview TEXT,
        result_data TEXT,
        reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_chat ON agent_runs(chat_id)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        category TEXT DEFAULT '偏好',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learning_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        goal TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learning_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        details TEXT DEFAULT '',
        stage TEXT DEFAULT '',
        status TEXT DEFAULT 'todo',
        sort_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_learning_tasks_plan ON learning_tasks(plan_id, sort_order, id)")

    # 课程进度。课程正文放文件（能 git、能手改），只有「学到哪了」这种
    # 随时会变的状态放这里。mastery 用 0-3 而不是布尔：同样标了「学完」，
    # 有人是看懂了，有人是能自己写出来，这两种不该推荐同一章。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS course_progress(
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        status TEXT DEFAULT 'todo',
        mastery INTEGER DEFAULT 0,
        notes TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (course_id, chapter_id)
    )
    """)

    # 章节里问过什么。用来让导师知道「他在这章卡过什么」，
    # 也让「推荐下一步」有依据，而不是每次都从第一章重新开始猜。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS course_questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_questions_ch ON course_questions(course_id, chapter_id)")

    # 每一次作答。只增不改（除了 flagged），因为「判过的题改了分」比没有记录更糟：
    # 没有记录只是缺点信息，改了分是伪造事实。
    #
    # passed 和 score 分开存：score 是这一题算出来的数，passed 是「按当时的阈值
    # 算出的结论」。阈值以后一定会调（开放题判分调阈值是常态），结论必须留痕，
    # 否则上周判过的题这周变成没过，用户会以为系统在乱改。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quiz_attempts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        answer TEXT NOT NULL,
        score INTEGER NOT NULL,
        passed INTEGER NOT NULL,
        grader TEXT NOT NULL,
        detail TEXT DEFAULT '',
        seconds INTEGER DEFAULT 0,
        flagged INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_ch ON quiz_attempts(course_id, chapter_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_item ON quiz_attempts(course_id, item_id, created_at)")

    # 能力画像：10 行，一个阶段一行。
    #
    # 刻意不做 33 章的粒度。定制学习路线的决策单位是「从哪个阶段插进去」，
    # 不是「跳过哪一章」；章级精度已经由 course_progress.mastery 承担，
    # 再存一份就是两套真相，迟早对不上。
    #
    # evidence 存「诊断卷 4/20、9/20」这种能回溯的依据。没有它，一个不知道
    # 从哪来的 level=3 和编出来的数字没区别。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile_skills(
        course_id TEXT NOT NULL,
        skill TEXT NOT NULL,
        level INTEGER DEFAULT 0,
        evidence TEXT DEFAULT '',
        source TEXT DEFAULT 'diagnostic',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (course_id, skill)
    )
    """)

    # 一次诊断的结论。明细照样进 quiz_attempts（item_id 用 diag- 前缀），
    # 这张表只回答「哪一次测的、多少分、什么时候」，用来算分级和显示「上次测过没」。
    # payload 留着每题作答，为的是事后能拿它去对拍判分规则——规则改过之后
    # 唯一能验证「改了到底对不对」的办法。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS diagnostics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        finished_at TEXT,
        total INTEGER NOT NULL DEFAULT 0,
        correct INTEGER NOT NULL DEFAULT 0,
        payload TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_diagnostics_course ON diagnostics(course_id, id)")

    # 学习排期的设置：每门课一行。开始日/每周哪几天/每天多少分钟都是用户给的
    # 事实，不能由模型猜；改一次就重排一次，所以只存一行，不留历史。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS plan_settings(
        course_id TEXT PRIMARY KEY,
        start_date TEXT NOT NULL,
        weekdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
        minutes INTEGER NOT NULL DEFAULT 60,
        include_skipped INTEGER NOT NULL DEFAULT 0,
        catch_up TEXT NOT NULL DEFAULT 'shift',
        basis_sig TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # 排期是按「当时那张画像」排的。画像变了（重测诊断）而排期没重排，
    # 页面上会出现「这张表说 33 章、画像说只学 23 章」的矛盾。存一个签名，
    # 只用来提示「该重排了」，不自动改任何东西。
    _ensure_column(cursor, "plan_settings", "basis_sig", "TEXT DEFAULT ''")

    # 排期槽位：某天学某章。**只存还没学完的章**，学完由 course_progress 表达。
    # 再存一份「这个槽完成了」就是两套真相，撤销时必然对不上。
    # minutes 是排下去那一刻的快照：课程里改预计时长，不该让已经排好的日子
    # 悄悄变长（那样用户今天一看，任务凭空多了半小时）。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS plan_slots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        day TEXT NOT NULL,
        seq INTEGER NOT NULL DEFAULT 0,
        chapter_id TEXT NOT NULL,
        minutes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_slots_course ON plan_slots(course_id, day, seq)"
    )

    # 学习沙箱的项目：一行对应磁盘上的一个目录。目录才是真相，这张表只记
    # 「谁建的、属于哪一章、上次验收结果」。用户在资源管理器里把目录删了，
    # 表还在——列表里标成「目录不见了」，而不是假装项目还在。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL DEFAULT '',
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        check_ok INTEGER,
        check_passed INTEGER NOT NULL DEFAULT 0,
        check_total INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT DEFAULT '',
        opened_at TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_chapter ON projects(course_id, chapter_id)"
    )
    # path 唯一：同一个目录不能被登记成两个项目，否则「打开哪个」会取决于查询顺序。
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_path ON projects(path)"
    )



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        chat_id INTEGER,

        role TEXT,

        content TEXT

    )
    """)



    # ---- 模型服务商配置表 ----
    # 表结构由 model_store 维护，这里同步建一次，保证 /models 第一次调用就能读写。
    from model_store import PROVIDER_SCHEMA

    cursor.execute(PROVIDER_SCHEMA)

    # ---- 兼容旧库的增量迁移 ----
    # memories 新增 source 列：区分「用户手动添加」和「助手写入」，
    # 让用户能审计、能只删助手加的那些。旧库没有这列，这里补上。
    _ensure_column(cursor, "memories", "source", "TEXT DEFAULT 'user'")

    # agent_runs 记录本次用的服务商与模型，切换 API 后能回溯「这条回答是谁答的」
    _ensure_column(cursor, "agent_runs", "provider", "TEXT")
    _ensure_column(cursor, "agent_runs", "model", "TEXT")

    # agent_steps 记录每一轮的思考摘要，回看历史时能还原「模型当时怎么想的」
    _ensure_column(cursor, "agent_steps", "reasoning", "TEXT")

    # 课程进度补两列：判分成绩与最近作答时间。
    # 目录上的红绿灯直接读 quiz_score，不用每次去 quiz_attempts 里倒查。
    # 不存 passed：过没过要跟着阈值走，存下来只会和阈值打架。
    _ensure_column(cursor, "course_progress", "quiz_score", "INTEGER")
    _ensure_column(cursor, "course_progress", "quiz_at", "TEXT")

    conn.commit()

    conn.close()


def _ensure_column(cursor, table, column, ddl):
    """给已存在的表补列；CREATE TABLE IF NOT EXISTS 不会改动旧表结构。"""
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(%s)" % table).fetchall()}
    if column not in existing:
        cursor.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))





# 创建聊天

def create_chat(title):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "INSERT INTO chats(title) VALUES(?)",

        (title,)

    )


    chat_id=cursor.lastrowid


    conn.commit()

    conn.close()


    return chat_id






# 获取聊天列表

def get_chats():


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "SELECT * FROM chats ORDER BY id DESC"

    )


    data=cursor.fetchall()


    conn.close()


    return data






# 修改聊天标题

def update_chat_title(chat_id,title):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        UPDATE chats

        SET title=?

        WHERE id=?

        """,

        (
            title,
            chat_id
        )

    )


    conn.commit()

    conn.close()






# 删除聊天

def delete_chat(id):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()


    cursor.execute(

        "DELETE FROM chats WHERE id=?",

        (id,)

    )


    cursor.execute(

        "DELETE FROM messages WHERE chat_id=?",

        (id,)

    )


    conn.commit()

    conn.close()







# 保存消息

def save_message(chat_id,role,content):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        INSERT INTO messages

        (chat_id,role,content)

        VALUES(?,?,?)

        """,

        (
            chat_id,
            role,
            content
        )

    )



    conn.commit()

    conn.close()








# 获取历史消息

def get_messages(chat_id):


    conn=sqlite3.connect(DB_PATH)

    cursor=conn.cursor()



    cursor.execute(

        """
        SELECT *

        FROM messages

        WHERE chat_id=?

        ORDER BY id

        """,

        (chat_id,)

    )


    data=cursor.fetchall()


    conn.close()


    return data


def get_recent_messages(chat_id, limit=12):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, max(1, min(int(limit), 30))),
    )
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


def save_agent_run(chat_id, question, result):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO agent_runs(run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,provider,model) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (result["run_id"], chat_id, question, result["reply"], result["status"],
         result["rounds"], result["duration_ms"], result["total_tokens"],
         result.get("provider"), result.get("model")),
    )
    for index, step in enumerate(result.get("steps", [])):
        cursor.execute(
            "INSERT INTO agent_steps(run_id,step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason,reasoning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (result["run_id"], index, step.get("round_no"), step.get("kind"), step.get("tool"),
             json.dumps(step.get("args", {}), ensure_ascii=False),
             None if step.get("ok") is None else int(bool(step.get("ok"))), step.get("elapsed_ms"),
             step.get("result_preview"), json.dumps(step.get("result_data", {}), ensure_ascii=False),
             step.get("reason") or step.get("detail"), step.get("reasoning")),
        )
    conn.commit()
    conn.close()


def get_agent_run(run_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,provider,model,created_at FROM agent_runs WHERE run_id=?", (run_id,))
    run = cursor.fetchone()
    if not run:
        conn.close()
        return None
    cursor.execute("SELECT step_index,round_no,kind,tool,args,ok,elapsed_ms,result_preview,result_data,reason,reasoning FROM agent_steps WHERE run_id=? ORDER BY step_index", (run_id,))
    steps = cursor.fetchall()
    conn.close()
    keys = ("step_index", "round_no", "kind", "tool", "args", "ok", "elapsed_ms", "result_preview", "result_data", "reason", "reasoning")
    parsed_steps = []
    for row in steps:
        item = dict(zip(keys, row))
        for key in ("args", "result_data"):
            try:
                item[key] = json.loads(item[key] or "{}")
            except (ValueError, TypeError):
                item[key] = {}
        parsed_steps.append(item)
    return {"run": dict(zip(("run_id", "chat_id", "question", "reply", "status", "rounds", "duration_ms", "total_tokens", "provider", "model", "created_at"), run)), "steps": parsed_steps}


def get_agent_runs_for_chat(chat_id, limit=100):
    conn = sqlite3.connect(DB_PATH)
    run_ids = conn.execute(
        "SELECT run_id FROM agent_runs WHERE chat_id=? ORDER BY rowid ASC LIMIT ?",
        (chat_id, max(1, min(int(limit), 200))),
    ).fetchall()
    conn.close()
    results = []
    for (run_id,) in run_ids:
        result = get_agent_run(run_id)
        if result:
            results.append({**result["run"], "steps": result["steps"]})
    return results


def add_memory(content, category="偏好", source="user"):
    """source: 'user' 用户手动添加；'agent' 助手按用户要求写入。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO memories(content,category,source) VALUES(?,?,?)",
        (content[:1200], category[:40], source[:16]),
    )
    memory_id = cursor.lastrowid
    cursor.execute("DELETE FROM memories WHERE id NOT IN (SELECT id FROM memories ORDER BY id DESC LIMIT 100)")
    conn.commit()
    conn.close()
    return memory_id


def list_memories(limit=100):
    # 前四个字段的下标保持不变，source 追加在末尾，避免影响已有调用方
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,content,category,created_at,COALESCE(source,'user') FROM memories ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    conn.close()
    return rows


def delete_memory(memory_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def count_memories():
    """记忆总条数。用来在提示词里说清「只在上下文里放了最近 N 条，另有 M 条靠检索」。"""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    return int(total)


# 检索历史对话时扫的是 agent_runs 而不是 messages：
# messages 只有 role+content，一句问一句答各占一行、没有时间戳，检索出来是一堆
# 断句；agent_runs 天然是「问 + 答」成对的，还带 created_at，按时间说「你上次
# 在 11 月 3 日问过」才说得出口。
def list_runs_for_recall(limit=1000):
    """给检索用的问答对索引。reply 截断到 400 字：够判断相关性，不至于把整篇回答读进内存。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT run_id,chat_id,question,substr(COALESCE(reply,''),1,400),created_at "
        "FROM agent_runs ORDER BY rowid DESC LIMIT ?",
        (max(1, min(int(limit), 5000)),),
    ).fetchall()
    conn.close()
    return rows


def count_runs():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    conn.close()
    return int(total)


def save_learning_plan(title, goal, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO learning_plans(title,goal,content) VALUES(?,?,?)", (title[:160], goal[:1000], content[:16000]))
    plan_id = cursor.lastrowid
    cursor.execute("DELETE FROM learning_plans WHERE id NOT IN (SELECT id FROM learning_plans ORDER BY id DESC LIMIT 200)")
    conn.commit()
    conn.close()
    return plan_id


def list_learning_plans(limit=50):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id,title,goal,content,status,created_at FROM learning_plans ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    conn.close()
    return rows


def get_learning_plan(plan_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id,title,goal,content,status,created_at FROM learning_plans WHERE id=?",
        (plan_id,),
    ).fetchone()
    conn.close()
    return row


def set_learning_plan_status(plan_id, status):
    if status not in {"active", "done"}:
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("UPDATE learning_plans SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, plan_id))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_learning_plan(plan_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM learning_tasks WHERE plan_id=?", (plan_id,))
    cursor.execute("DELETE FROM learning_plans WHERE id=?", (plan_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def add_learning_tasks(plan_id, tasks):
    """Store a bounded ordered task list for one plan."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    created = []
    for index, task in enumerate(tasks[:50]):
        title = str(task.get("title", "")).strip()[:180]
        if not title:
            continue
        details = str(task.get("details", "")).strip()[:1200]
        stage = str(task.get("stage", "")).strip()[:80]
        cursor.execute(
            "INSERT INTO learning_tasks(plan_id,title,details,stage,sort_order) VALUES(?,?,?,?,?)",
            (plan_id, title, details, stage, index),
        )
        created.append({"id": cursor.lastrowid, "title": title, "details": details, "stage": stage, "status": "todo"})
    conn.commit()
    conn.close()
    return created


def list_learning_tasks(plan_id):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,title,details,stage,status,sort_order,created_at,completed_at FROM learning_tasks WHERE plan_id=? ORDER BY sort_order,id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return rows


def set_learning_task_status(plan_id, task_id, status):
    if status not in {"todo", "done"}:
        return False
    conn = sqlite3.connect(DB_PATH)
    completed_at = "CURRENT_TIMESTAMP" if status == "done" else "NULL"
    cursor = conn.execute(
        f"UPDATE learning_tasks SET status=?,completed_at={completed_at} WHERE id=? AND plan_id=?",
        (status, task_id, plan_id),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_learning_task(plan_id, task_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM learning_tasks WHERE id=? AND plan_id=?", (task_id, plan_id))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def replace_unfinished_learning_tasks(plan_id, tasks):
    """Keep completed work as a record and replace only unfinished work."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM learning_tasks WHERE plan_id=? AND status='todo'", (plan_id,))
    offset_row = cursor.execute("SELECT COALESCE(MAX(sort_order), -1) FROM learning_tasks WHERE plan_id=?", (plan_id,)).fetchone()
    offset = int(offset_row[0]) + 1
    created = []
    for index, task in enumerate(tasks[:50]):
        title = str(task.get("title", "")).strip()[:180]
        if not title:
            continue
        details = str(task.get("details", "")).strip()[:1200]
        stage = str(task.get("stage", "")).strip()[:80]
        cursor.execute(
            "INSERT INTO learning_tasks(plan_id,title,details,stage,sort_order) VALUES(?,?,?,?,?)",
            (plan_id, title, details, stage, offset + index),
        )
        created.append({"id": cursor.lastrowid, "title": title, "details": details, "stage": stage, "status": "todo"})
    conn.commit()
    conn.close()
    return created


# ---------- 课程进度 ----------

def list_course_progress(course_id):
    """返回 {chapter_id: {...}}。没有记录的章节不在结果里，由上层补 todo。

    这里用 SELECT * + 列名映射，不写死列名。原因不是图省事：这张表会随功能加列
    （quiz_score / quiz_at 就是这么加的），写死列名意味着「旧库还没跑 init_db」
    时整个查询直接抛 no such column。而调用方多半用 try/except 兜着——错误被
    静默吞掉，表现成「导师不知道你在第几章」，比直接报错难查得多。

    语义上也是对的：库里没有这条信息，就该表现为「不知道」，而不是整件事失败。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("SELECT * FROM course_progress WHERE course_id=?", (course_id,))
    names = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    result = {}
    for row in rows:
        item = dict(zip(names, row))
        result[item["chapter_id"]] = {
            "status": item.get("status") or "todo",
            "mastery": int(item.get("mastery") or 0),
            "notes": item.get("notes") or "",
            "updated_at": item.get("updated_at"),
            "quiz_score": item.get("quiz_score"),
            "quiz_at": item.get("quiz_at"),
        }
    return result


def set_course_progress(course_id, chapter_id, status=None, mastery=None, notes=None,
                        quiz_score=None, quiz_at=None):
    """只更新传进来的字段。status 与 mastery 分开，是因为「看过」和「学会」是两件事。

    quiz_score / quiz_at 由判分接口回写，不跟 status 混：答完题不等于学完这一章，
    反过来学完也不等于答得过。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO course_progress(course_id,chapter_id) VALUES(?,?)",
        (course_id, chapter_id),
    )
    sets, params = [], []
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if mastery is not None:
        sets.append("mastery=?")
        params.append(int(mastery))
    if notes is not None:
        sets.append("notes=?")
        params.append(notes)
    if quiz_score is not None:
        sets.append("quiz_score=?")
        params.append(max(0, min(100, int(quiz_score))))
    if quiz_at is not None:
        sets.append("quiz_at=?")
        params.append(quiz_at)
    if sets:
        sets.append("updated_at=CURRENT_TIMESTAMP")
        params.extend([course_id, chapter_id])
        cursor.execute(
            "UPDATE course_progress SET %s WHERE course_id=? AND chapter_id=?" % ", ".join(sets),
            params,
        )
    conn.commit()
    row = cursor.execute(
        "SELECT status,mastery,notes,updated_at,quiz_score,quiz_at FROM course_progress "
        "WHERE course_id=? AND chapter_id=?",
        (course_id, chapter_id),
    ).fetchone()
    conn.close()
    return {"status": row[0], "mastery": int(row[1] or 0), "notes": row[2] or "",
            "updated_at": row[3], "quiz_score": row[4], "quiz_at": row[5]}


def now_text():
    """取 SQLite 自己的时间文本。

    不用 Python 的 datetime.now()：表里其他地方都是 CURRENT_TIMESTAMP（UTC），
    混着写会出现「刚答的题显示成 8 小时前」这种时区错位。
    """
    conn = sqlite3.connect(DB_PATH)
    value = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    conn.close()
    return value


def add_course_question(course_id, chapter_id, question, answer=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO course_questions(course_id,chapter_id,question,answer) VALUES(?,?,?,?)",
        (course_id, chapter_id, (question or "")[:4000], (answer or "")[:20000]),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def list_course_questions(course_id, chapter_id=None, limit=30):
    conn = sqlite3.connect(DB_PATH)
    if chapter_id:
        rows = conn.execute(
            "SELECT id,chapter_id,question,answer,created_at FROM course_questions "
            "WHERE course_id=? AND chapter_id=? ORDER BY id DESC LIMIT ?",
            (course_id, chapter_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,chapter_id,question,answer,created_at FROM course_questions "
            "WHERE course_id=? ORDER BY id DESC LIMIT ?",
            (course_id, limit),
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "chapter_id": r[1], "question": r[2], "answer": r[3], "created_at": r[4]}
        for r in rows
    ]


# ======================
# 答题记录与能力画像
# ======================
#
# 这一层只负责存取事实，不判断对错：判分规则在 quiz.py，分级规则在 profile.py。
# 分开的理由很实际——判分阈值和分级阈值都会反复调，而表结构不该跟着动。
#
# 三张表的分工：
# - quiz_attempts：每一次作答（事实，只增）
# - profile_skills：他现在什么水平（结论，可被下次诊断覆盖）
# - diagnostics：哪一次诊断得出了这个结论（来源，可回溯）


def add_quiz_attempt(course_id, chapter_id, item_id, kind, answer,
                     score=0, passed=0, grader="local", detail="", seconds=0):
    """记一次作答，返回新行 id。

    answer 不截断到几百字：要点判分要看完整作答，截了就等于改了用户的答案。
    score 夹到 0-100 再入库，前端就不用到处防越界。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO quiz_attempts"
        "(course_id,chapter_id,item_id,kind,answer,score,passed,grader,detail,seconds) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (course_id, chapter_id, item_id, kind, (answer or "")[:20000],
         max(0, min(100, int(score or 0))), 1 if passed else 0,
         grader or "local", (detail or "")[:4000], max(0, int(seconds or 0))),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def list_quiz_attempts(course_id, chapter_id=None, item_id=None, limit=50):
    """按时间倒序取作答记录。三个筛选取交集，都不传就是整门课最近的若干条。"""
    sql = ("SELECT id,chapter_id,item_id,kind,answer,score,passed,grader,detail,"
           "seconds,flagged,created_at FROM quiz_attempts WHERE course_id=?")
    params = [course_id]
    if chapter_id:
        sql += " AND chapter_id=?"
        params.append(chapter_id)
    if item_id:
        sql += " AND item_id=?"
        params.append(item_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {"id": r[0], "chapter_id": r[1], "item_id": r[2], "kind": r[3], "answer": r[4],
         "score": int(r[5] or 0), "passed": bool(r[6]), "grader": r[7], "detail": r[8],
         "seconds": int(r[9] or 0), "flagged": bool(r[10]), "created_at": r[11]}
        for r in rows
    ]


def quiz_pass_summary(course_id):
    """{chapter_id: {"answered": 答过几题, "passed": 过了几题}}，一次查全。

    给目录红绿灯用。判定口径必须和 quiz.chapter_state 完全一致——
    「每道题取最近一次」，所以这里先用 MAX(id) 按 (章, 题) 分组，
    再统计。两边口径一旦分叉，就会出现「目录是绿的、点进去发现有题没过」。

    不在这里算「一共几题」：那是题库文件的事实，不是数据库的事实，
    由 quiz.quiz_totals() 去读文件，两边在 course.decorate 里合并。
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chapter_id, item_id, passed FROM quiz_attempts "
        "WHERE course_id=? AND id IN "
        "(SELECT MAX(id) FROM quiz_attempts WHERE course_id=? GROUP BY chapter_id, item_id)",
        (course_id, course_id),
    ).fetchall()
    conn.close()
    summary = {}
    for chapter_id, _item_id, passed in rows:
        row = summary.setdefault(chapter_id, {"answered": 0, "passed": 0})
        row["answered"] += 1
        if passed:
            row["passed"] += 1
    return summary


def latest_quiz_scores(course_id):
    """{chapter_id: {"score": 最近一次的分数, "at": 时间}}。

    一次查全，给 course.decorate 用。刻意不提供「逐章查一次」的函数：
    33 章就是 33 次连接，目录每刷一次都要付这个代价。

    注意这里只回答「最近这一回考得怎么样」。整轮成绩（3 题里过几题）
    由 quiz.chapter_state 现算——那是另一个问题，不该混进这张表。
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chapter_id, score, created_at FROM quiz_attempts "
        "WHERE course_id=? AND id IN "
        "(SELECT MAX(id) FROM quiz_attempts WHERE course_id=? GROUP BY chapter_id)",
        (course_id, course_id),
    ).fetchall()
    conn.close()
    return {r[0]: {"score": int(r[1] or 0), "at": r[2]} for r in rows}


def flag_quiz_attempt(attempt_id, flagged=1):
    """用户标「这题判错了」。只改标记，不改分数——判错是判分器的问题，
    不是这次作答的问题，把分数改掉就再也查不出规则哪里错了。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quiz_attempts SET flagged=? WHERE id=?",
        (1 if flagged else 0, int(attempt_id)),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def upsert_profile_skill(course_id, skill, level, evidence="", source="diagnostic"):
    """写一个阶段的水平。重测会覆盖上一次——画像是「现在」的状态，不是档案。

    level 夹到 0-3：0 未测 / 1 听过 / 2 能写 / 3 能讲。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO profile_skills(course_id,skill,level,evidence,source) VALUES(?,?,?,?,?) "
        "ON CONFLICT(course_id,skill) DO UPDATE SET "
        "level=excluded.level, evidence=excluded.evidence, source=excluded.source, "
        "updated_at=CURRENT_TIMESTAMP",
        (course_id, skill, max(0, min(3, int(level or 0))),
         (evidence or "")[:1000], source or "diagnostic"),
    )
    conn.commit()
    conn.close()


def list_profile_skills(course_id):
    """{skill: {...}}。没有记录的阶段不在结果里，由上层按 0 处理。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT skill,level,evidence,source,updated_at FROM profile_skills WHERE course_id=?",
        (course_id,),
    ).fetchall()
    conn.close()
    return {
        r[0]: {"level": int(r[1] or 0), "evidence": r[2] or "",
               "source": r[3] or "diagnostic", "updated_at": r[4]}
        for r in rows
    }


def save_diagnostic(course_id, total, correct, payload=""):
    """存一次诊断的结论。payload 是每题作答的 JSON，用来事后对拍判分规则。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO diagnostics(course_id,finished_at,total,correct,payload) "
        "VALUES(?,CURRENT_TIMESTAMP,?,?,?)",
        (course_id, max(0, int(total or 0)), max(0, int(correct or 0)),
         (payload or "")[:20000]),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def latest_diagnostic(course_id):
    """最近一次诊断；从没测过返回 None。界面据此决定要不要显示「先测一下」的入口。"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id,finished_at,total,correct,payload,created_at FROM diagnostics "
        "WHERE course_id=? ORDER BY id DESC LIMIT 1",
        (course_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "finished_at": row[1], "total": int(row[2] or 0),
            "correct": int(row[3] or 0), "payload": row[4] or "", "created_at": row[5]}


# ======================
# 学习排期
# ======================

def get_plan_settings(course_id):
    """读排期设置；没排过返回 None（不是返回默认值——「没排过」和「排了但用默认
    参数」在界面上是两件事：前者要显示「开始排期」，后者要显示「重新排」）。"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT start_date,weekdays,minutes,include_skipped,catch_up,updated_at,basis_sig "
        "FROM plan_settings WHERE course_id=?",
        (course_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "start_date": row[0],
        "weekdays": row[1],
        "minutes": int(row[2] or 0),
        "include_skipped": bool(row[3]),
        "catch_up": row[4] or "shift",
        "updated_at": row[5],
        "basis_sig": row[6] or "",
    }


def save_plan_settings(course_id, start_date, weekdays, minutes,
                       include_skipped=False, catch_up="shift", basis_sig=""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO plan_settings(course_id,start_date,weekdays,minutes,"
        "include_skipped,catch_up,basis_sig,updated_at) "
        "VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(course_id) DO UPDATE SET start_date=excluded.start_date,"
        "weekdays=excluded.weekdays,minutes=excluded.minutes,"
        "include_skipped=excluded.include_skipped,catch_up=excluded.catch_up,"
        "basis_sig=excluded.basis_sig,updated_at=CURRENT_TIMESTAMP",
        (course_id, start_date, weekdays, int(minutes),
         1 if include_skipped else 0, catch_up, basis_sig or ""),
    )
    conn.commit()
    conn.close()


def list_plan_slots(course_id):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id,day,seq,chapter_id,minutes FROM plan_slots "
        "WHERE course_id=? ORDER BY day, seq, id",
        (course_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "day": row[1], "seq": int(row[2] or 0),
         "chapter_id": row[3], "minutes": int(row[4] or 0)}
        for row in rows
    ]


def replace_plan_slots(course_id, slots):
    """整份替换。排期是「接下来怎么走」的完整方案，不是可以逐条增删的流水账：
    留一半旧槽一半新槽，会出现同一天两章重复、或某章被排了两次。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM plan_slots WHERE course_id=?", (course_id,))
    cursor.executemany(
        "INSERT INTO plan_slots(course_id,day,seq,chapter_id,minutes) VALUES(?,?,?,?,?)",
        [
            (course_id, slot["day"], int(slot.get("seq") or 0),
             slot["chapter_id"], int(slot.get("minutes") or 0))
            for slot in slots
        ],
    )
    conn.commit()
    rows = cursor.execute(
        "SELECT id,day,seq,chapter_id,minutes FROM plan_slots "
        "WHERE course_id=? ORDER BY day, seq, id",
        (course_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "day": row[1], "seq": int(row[2] or 0),
         "chapter_id": row[3], "minutes": int(row[4] or 0)}
        for row in rows
    ]


def delete_plan(course_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM plan_slots WHERE course_id=?", (course_id,))
    conn.execute("DELETE FROM plan_settings WHERE course_id=?", (course_id,))
    conn.commit()
    conn.close()


# ======================
# 学习沙箱项目
# ======================

_PROJECT_COLUMNS = (
    "id, course_id, chapter_id, slug, name, path, "
    "check_ok, check_passed, check_total, checked_at, opened_at, created_at"
)


def _project_row(row):
    if not row:
        return None
    return {
        "id": row[0], "course_id": row[1], "chapter_id": row[2],
        "slug": row[3], "name": row[4], "path": row[5],
        # 三态：None=还没跑过验收，0=跑过没过，1=跑过通过。
        # 用 None 而不是 0，是为了让界面能区分「没验过」和「验了没过」。
        "check_ok": None if row[6] is None else bool(row[6]),
        "check_passed": int(row[7] or 0), "check_total": int(row[8] or 0),
        "checked_at": row[9] or "", "opened_at": row[10] or "",
        "created_at": row[11],
    }


def add_project(course_id, chapter_id, slug, name, path):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects(course_id,chapter_id,slug,name,path,checked_at,opened_at) "
        "VALUES(?,?,?,?,?,'','')",
        (course_id, chapter_id or "", slug, name, path),
    )
    conn.commit()
    project_id = cursor.lastrowid
    conn.close()
    return get_project(project_id)


def get_project(project_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT %s FROM projects WHERE id=?" % _PROJECT_COLUMNS, (int(project_id),)
    ).fetchone()
    conn.close()
    return _project_row(row)


def get_project_by_path(path):
    """同一个目录重复登记会撞唯一索引，这里用来做「已存在就返回旧行」的判断。"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT %s FROM projects WHERE path=?" % _PROJECT_COLUMNS, (str(path),)
    ).fetchone()
    conn.close()
    return _project_row(row)


def list_projects(course_id=None, chapter_id=None):
    sql = "SELECT %s FROM projects" % _PROJECT_COLUMNS
    where, params = [], []
    if course_id:
        where.append("course_id=?")
        params.append(course_id)
    if chapter_id:
        where.append("chapter_id=?")
        params.append(chapter_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_project_row(row) for row in rows]


def touch_project(project_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE projects SET opened_at=CURRENT_TIMESTAMP WHERE id=?", (int(project_id),)
    )
    conn.commit()
    conn.close()


def set_project_check(project_id, ok, passed, total):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE projects SET check_ok=?, check_passed=?, check_total=?, "
        "checked_at=CURRENT_TIMESTAMP WHERE id=?",
        (None if ok is None else (1 if ok else 0), int(passed or 0), int(total or 0),
         int(project_id)),
    )
    conn.commit()
    conn.close()


def delete_project(project_id):
    """只删登记，不删磁盘目录。

    删目录是不可逆的，而这个功能的用户是刚学写代码的人——把「清理列表」
    和「删掉我写了三小时的项目」合成一个按钮，早晚会有人手滑。
    真要删文件请在资源管理器里自己删，列表会标成「目录不见了」。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM projects WHERE id=?", (int(project_id),))
    conn.commit()
    conn.close()


# ======================
# 备份
# ======================
#
# 整个站的长期价值都在一个 chats.db 里：几个月的对话、课程进度、考过的问题、
# 手动记下的偏好。它没有副本——硬盘坏一次、手滑删一次就全没了，而这些数据
# 不像代码可以从 git 恢复。
#
# 所以每次启动时留一份快照。刻意做得「不精确」：
# - 一天只留一份，当天重启多次不再覆盖。当天第一次启动前的状态往往才是
#   最想要的那个；如果每次重启都覆盖，手滑删完数据再重启就把好备份冲掉了。
# - 只留最近 7 天。备份的价值在于「最近能回退」，不是做归档。


def _db_has_data(db_file: str) -> bool:
    """判断库里有没有真实内容。刚 init_db 出来的空壳不值得占一个备份位。"""
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT count(*) FROM chats").fetchone()[0] > 0
    except sqlite3.Error:
        # 表还没建，说明是全新的库
        return False
    finally:
        conn.close()


def _prune_backups(dest_dir: str, keep: int) -> None:
    """按文件名里的日期排序，删掉最旧的。名字带 ISO 日期，字母序就是时间序。"""
    names = sorted(
        name for name in os.listdir(dest_dir)
        if name.startswith("chats-") and name.endswith(".db")
    )
    if keep <= 0:
        stale = names
    else:
        stale = names[:-keep]
    for name in stale:
        try:
            os.remove(os.path.join(dest_dir, name))
        except OSError as exc:
            logger.warning("旧备份删不掉 %s：%s", name, exc)


def backup_db(dest_dir: str | None = None, keep: int = BACKUP_KEEP,
              day: str | None = None) -> str | None:
    """把当前库整份快照到备份目录，一天一份，只留最近 keep 天。

    返回备份文件路径；跳过（当天已有/空库/库不存在）或失败时返回 None。
    **任何异常都只记日志，绝不抛出**——备份不该成为服务启动的拦路石。
    """
    target_dir = dest_dir or BACKUP_DIR
    try:
        if not os.path.exists(DB_PATH) or not _db_has_data(DB_PATH):
            return None

        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "chats-%s.db" % (day or time.strftime("%Y-%m-%d")))
        if os.path.exists(target):
            return None

        # 用 sqlite 自带的备份接口，而不是直接复制文件：万一有别的进程
        # （命令行、另一个实例）正在写，复制可能拿到写了一半的页。
        src = sqlite3.connect(DB_PATH)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        _prune_backups(target_dir, keep)
        logger.info("数据库已备份到 %s", target)
        return target
    except Exception as exc:  # noqa: BLE001 - 备份失败不能拖垮启动
        logger.warning("数据库备份失败（不影响启动）：%s", exc)
        return None
