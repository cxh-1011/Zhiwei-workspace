"""判分规则的离线测试：三种题型、边界、题库校验、模型兜底。

不联网、不调模型。模型那一组用假的 chat_completion 顶替，专门验「模型不可用时
不能把用户卡住」这条退路。
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import quiz

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


def use_temp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = database.DB_PATH
    database.DB_PATH = tmp.name
    database.init_db()
    return tmp.name, old


def restore(old, path):
    database.DB_PATH = old
    try:
        os.unlink(path)
    except OSError:
        pass


class TempCurriculum:
    """临时课程目录，用来测题库文件的各种坏法。

    quiz.CURRICULUM_ROOT 是模块常量，换掉它就能让 load_quiz 读到临时文件，
    不必往真课程目录里塞垃圾。
    """

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.old = quiz.CURRICULUM_ROOT
        quiz.CURRICULUM_ROOT = Path(self.dir)

    def write(self, chapter_id, payload, course_id="agent-dev"):
        root = Path(self.dir) / course_id / "quiz"
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("%s.json" % chapter_id)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path

    def close(self):
        quiz.CURRICULUM_ROOT = self.old
        for root, dirs, files in os.walk(self.dir, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.dir)


# ---------- 样例题 ----------

CHOICE = {
    "id": "t-q1", "kind": "choice", "stem": "下面哪个是参数名？",
    "options": ["A. 甲", "B. 乙", "C. 丙"], "answer": "B",
}
MULTI = {
    "id": "t-q2", "kind": "choice", "stem": "哪些是参数？", "multi": True,
    "options": ["A. 甲", "B. 乙", "C. 丙"], "answer": "AC",
}
BLANK_ONE = {"id": "t-q3", "kind": "blank", "stem": "控制长度的参数是 ____。",
             "blanks": ["max_tokens"]}
BLANK_TWO = {"id": "t-q4", "kind": "blank", "stem": "两个参数 ____ 和 ____。",
             "blanks": [["max_tokens", "max tokens"], "temperature"]}
RUBRIC = {
    "id": "t-q5", "kind": "rubric", "stem": "为什么同一句话中文比英文贵？",
    "must": [
        {"point": "token 数量", "any": ["token", "标记"]},
        {"point": "按 token 计费", "any": ["计费", "收费", "价格"]},
        {"point": "中英差异", "any": ["中文", "英文", "语言"]},
    ],
    "forbid": ["因为中文更复杂"],
}


def at(item, chapter_id, number=1):
    """把样例题挂到指定章节上：题库校验要求 id 以 <chapter>- 开头。"""
    return dict(item, id="%s-q%d" % (chapter_id, number))


# ---------- 归一化 ----------

def test_norm():
    print("\n[归一化]")
    check("全角转半角", quiz._norm("ＭＡＸ") == "max")
    check("大小写归一", quiz._norm("Max") == "max")
    check("去空格与下划线", quiz._norm("max tokens") == quiz._norm("max_tokens"))
    check("去中文标点", quiz._norm("max_tokens。") == "maxtokens")
    check("去中括号引号", quiz._norm("「max」") == "max")
    check("空值不炸", quiz._norm(None) == "")
    check("连字符也算噪声", quiz._norm("gpt-4") == "gpt4")


# ---------- 选择题 ----------

def test_choice():
    print("\n[选择题]")
    for answer in ("B", "b", "B.", "选B", "答案是 B", "B. 乙", "  B  "):
        result = quiz.grade(CHOICE, answer)
        check("认得这种写法：%r" % answer, result["score"] == 100, result)

    check("选错给 0", quiz.grade(CHOICE, "A")["score"] == 0)
    check("选错说清正确答案", "正确答案是 B" in quiz.grade(CHOICE, "A")["detail"])
    check("空作答给 0", quiz.grade(CHOICE, "")["score"] == 0)
    check("空作答提示没看出", "没看出" in quiz.grade(CHOICE, "")["detail"])
    check("只写汉字不猜", quiz.grade(CHOICE, "乙")["score"] == 0)
    check("没配答案时不判错人", quiz.grade({"kind": "choice", "options": ["A. 甲"]}, "A")["score"] == 0)
    check("没配答案时说明原因", "没配好" in quiz.grade({"kind": "choice"}, "A")["detail"])

    check("多选全对", quiz.grade(MULTI, "AC")["score"] == 100)
    check("多选顺序无关", quiz.grade(MULTI, "CA")["score"] == 100)
    check("多选带逗号", quiz.grade(MULTI, "A,C")["score"] == 100)
    check("多选少选算错", quiz.grade(MULTI, "A")["score"] == 0)
    check("多选少选不给半分", quiz.grade(MULTI, "AC")["score"] == 100
          and quiz.grade(MULTI, "AB")["score"] == 0)
    check("多选说清正确答案", "正确答案是 AC" in quiz.grade(MULTI, "AB")["detail"])


# ---------- 填空题 ----------

def test_blank():
    print("\n[填空题]")
    check("原样正确", quiz.grade(BLANK_ONE, "max_tokens")["score"] == 100)
    check("大写也对", quiz.grade(BLANK_ONE, "MAX TOKENS")["score"] == 100)
    check("带句号也对", quiz.grade(BLANK_ONE, "max_tokens。")["score"] == 100)
    check("带说明也对", quiz.grade(BLANK_ONE, "参数是 max_tokens")["score"] == 100)
    check("全角空格也对", quiz.grade(BLANK_ONE, "ｍａｘ＿ｔｏｋｅｎｓ")["score"] == 100)

    wrong = quiz.grade(BLANK_ONE, "token")
    check("答错给 0", wrong["score"] == 0, wrong)
    check("答错说清正确答案", "max_tokens" in wrong["detail"], wrong)
    check("空作答给 0", quiz.grade(BLANK_ONE, "")["score"] == 0)

    # 短答案不做包含匹配：「a」出现在任何句子里都成立
    short = {"kind": "blank", "blanks": ["a"]}
    check("短答案不做包含匹配", quiz.grade(short, "这是一个 a 参数")["score"] == 0)
    check("短答案精确匹配仍算对", quiz.grade(short, "A")["score"] == 100)

    check("两空全对", quiz.grade(BLANK_TWO, "max_tokens\ntemperature")["score"] == 100)
    check("两空同义词", quiz.grade(BLANK_TWO, "max tokens\ntemperature")["score"] == 100)
    check("两空接受 JSON 数组", quiz.grade(BLANK_TWO, '["max_tokens","temperature"]')["score"] == 100)
    check("两空只填一个给 50", quiz.grade(BLANK_TWO, "max_tokens")["score"] == 50)
    check("两空错一个给 50", quiz.grade(BLANK_TWO, "max_tokens\ntemp")["score"] == 50)
    check("两空错一个不过线", quiz.grade(BLANK_TWO, "max_tokens\ntemp")["passed"] is False)
    check("多填不扣分", quiz.grade(BLANK_TWO, "max_tokens\ntemperature\n补充说明")["score"] == 100)
    check("两空说清第几空错", "第 2 空" in quiz.grade(BLANK_TWO, "max_tokens\ntemp")["detail"])


# ---------- 要点题 ----------

def test_rubric():
    print("\n[要点题]")
    full = quiz.grade(RUBRIC, "因为 token 数量不同，计费按 token 算，中英文语言差异会让数量翻倍")
    check("要点全中给 100", full["score"] == 100, full)
    check("全中不给模型兜底", full["needs_model"] is False)
    check("全中判过", full["passed"] is True)

    two_of_three = quiz.grade(RUBRIC, "token 数量不同，计费按 token 算")
    check("差一个要点给 67", two_of_three["score"] == 67, two_of_three)
    check("差一个要点不过线", two_of_three["passed"] is False)
    check("差一个要点交模型复核", two_of_three["needs_model"] is True)
    check("说清还差哪一点", "中英差异" in two_of_three["detail"], two_of_three["detail"])

    one_of_three = quiz.grade(RUBRIC, "token 的数量不一样多而已")
    check("只中一个给 33", one_of_three["score"] == 33, one_of_three)
    check("只中一个也交模型", one_of_three["needs_model"] is True)

    nothing = quiz.grade(RUBRIC, "这个问题我完全没有想法先跳过")
    check("一个没中给 0", nothing["score"] == 0)
    check("一个没中不浪费模型调用", nothing["needs_model"] is False)

    reversed_ = quiz.grade(RUBRIC, "因为中文更复杂，token 数量自然多，计费也高")
    check("反例命中直接 0", reversed_["score"] == 0, reversed_)
    check("反例命中说清结论反了", "结论反了" in reversed_["detail"], reversed_["detail"])
    check("反例命中不交模型", reversed_["needs_model"] is False)

    too_short = quiz.grade(RUBRIC, "不知道")
    check("太短给 0", too_short["score"] == 0)
    check("太短说明原因", "太短" in too_short["detail"])

    check("没配要点时不判错人", quiz.grade({"kind": "rubric"}, "一段足够长的作答内容")["score"] == 0)

    # 反例被否定过就不再算命中。开放题里正确回答常常是「不是A，而是B」，
    # 照字面判会把对的判成 0——这类误判用户没法申诉，一出现就没人再信判分。
    deny = {"kind": "rubric", "stem": "谁发请求",
            "must": [{"any": ["模型输出"]}, {"any": ["你的代码"]}, {"any": ["塞回"]}],
            "forbid": ["模型自己发请求"]}
    good = quiz.grade(deny, "不是模型自己发请求，模型输出 json 之后由你的代码执行，再把结果塞回对话")
    check("反例被否定时不算命中", good["score"] == 100, good)
    bad = quiz.grade(deny, "模型自己发请求，模型输出 json，然后塞回对话")
    check("反例被肯定时仍然命中", bad["score"] == 0, bad)
    tail_neg = quiz.grade(deny, "模型自己发请求是不对的，模型输出 json 再由你的代码塞回对话")
    check("否定词在后面也认", tail_neg["score"] == 100, tail_neg)
    # 前后各说一半时不能整体当否定，必须逐次出现判断
    mixed = quiz.grade(deny, "模型自己发请求是对的，模型输出 json，你的代码只负责塞回")
    check("先肯定后否定仍算命中", mixed["score"] == 0, mixed)

    # 要点写成整句几乎不会命中，这是写题库的人最容易犯的错，用测试把它钉住
    sentence = {"kind": "rubric", "must": ["token 数量不同"]}
    result = quiz.grade(sentence, "token 数量不一样")
    check("整句要点会漏判（提醒写词组）", result["score"] == 0, result)

    only_all = {"kind": "rubric", "must": [{"all": ["max_tokens", "长度"]}]}
    check("all 要点全中才算中", quiz.grade(only_all, "用 max_tokens 控制长度就行")["score"] == 100)
    check("all 要点缺一不算", quiz.grade(only_all, "用 max_tokens 控制")["score"] == 0)


# ---------- 未知题型 ----------

def test_bad_kind():
    print("\n[异常输入]")
    check("未知题型给 0", quiz.grade({"kind": "essay"}, "随便答")["score"] == 0)
    check("未知题型说清原因", "未知题型" in quiz.grade({"kind": "essay"}, "")["detail"])
    check("空题目不炸", quiz.grade(None, "x")["score"] == 0)
    check("None 作答不炸", quiz.grade(BLANK_ONE, None)["score"] == 0)


# ---------- 公开题目 ----------

def test_public():
    print("\n[给前端的题目]")
    items = quiz.public_items({"items": [CHOICE, BLANK_TWO, RUBRIC]})
    blob = json.dumps(items, ensure_ascii=False)
    check("剥掉正确答案", "正确答案" not in blob and '"answer"' not in blob, blob)
    check("剥掉要点", '"must"' not in blob and "forbid" not in blob)
    check("剥掉解析", "explain" not in blob)
    check("保留题干", items[0]["stem"] == CHOICE["stem"])
    check("保留选项", items[0]["options"] == CHOICE["options"])
    check("填空给空数不给答案", items[1]["blank_count"] == 2 and "blanks" not in items[1], items[1])
    check("空题库返回空列表", quiz.public_items({"items": []}) == [])
    check("题目里搜不到答案字眼", "temperature" not in json.dumps(items[1], ensure_ascii=False))


# ---------- 题库文件 ----------

def test_load():
    print("\n[题库文件]")
    curriculum = TempCurriculum()
    try:
        missing = quiz.load_quiz("s1c1")
        check("没有题库时返回空列表", missing["items"] == [])
        check("没有题库时不抛异常", missing["chapter_id"] == "s1c1")

        curriculum.write("s1c1", {"chapter_id": "s1c1", "items": [at(CHOICE, "s1c1")]})
        loaded = quiz.load_quiz("s1c1")
        check("能读出来", len(loaded["items"]) == 1)

        curriculum.write("s1c2", "{ 这不是 json")
        try:
            quiz.load_quiz("s1c2")
            check("坏 JSON 抛错", False)
        except quiz.QuizError as exc:
            check("坏 JSON 抛错", "读不了" in str(exc), exc)

        curriculum.write("s1c3", {"chapter_id": "s1c3", "items": [dict(CHOICE, id="xx-q1")]})
        try:
            quiz.load_quiz("s1c3")
            check("id 前缀不对抛错", False)
        except quiz.QuizError as exc:
            check("id 前缀不对抛错", "id 应该是" in str(exc), exc)

        curriculum.write("s1c4", {"chapter_id": "s1c4", "items": [dict(CHOICE, id="s1c4-q1", answer="D")]})
        try:
            quiz.load_quiz("s1c4")
            check("答案不在选项里抛错", False)
        except quiz.QuizError as exc:
            check("答案不在选项里抛错", "不在选项里" in str(exc), exc)

        try:
            quiz.load_quiz("../etc", "agent-dev")
            check("路径穿越被挡", False)
        except quiz.QuizError:
            check("路径穿越被挡", True)
        try:
            quiz.load_quiz("s1c1", "../agent-dev")
            check("课程 id 穿越被挡", False)
        except quiz.QuizError:
            check("课程 id 穿越被挡", True)
    finally:
        curriculum.close()


def test_validate():
    print("\n[题库校验]")
    base = {"id": "s1c1-q1", "kind": "choice", "stem": "题干", "options": ["A. 甲", "B. 乙"], "answer": "A"}
    check("合格题库没问题", quiz.validate_quiz({"chapter_id": "s1c1", "items": [base]}) == [])
    check("没有题目要报", quiz.validate_quiz({"chapter_id": "s1c1", "items": []}) != [])
    check("缺题干要报", bool(quiz.validate_quiz({"chapter_id": "s1c1", "items": [dict(base, stem="")]})))
    check("id 重复要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [base, dict(base)]})))
    check("选项少于两个要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, options=["A. 甲"])]})))
    check("选项没字母要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, options=["甲", "乙"])]})))
    check("kind 不认识要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, kind="essay")]})))

    chapter = {"objectives": ["目标一", "目标二"]}
    check("objective 越界要报", any("超出" in p for p in quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, objective=5)]}, chapter)))
    check("objective 合法不报", quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, objective=1)]}, chapter) == [])
    check("objective 负数要报", any("非负" in p for p in quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, objective=-1)]}, chapter)))

    check("要点为空要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [{"id": "s1c1-q1", "kind": "rubric", "stem": "x", "must": []}]})))
    check("要点写成空对象要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [{"id": "s1c1-q1", "kind": "rubric", "stem": "x",
                                          "must": [{"point": "没有关键词"}]}]})))
    check("填空答案为空要报", bool(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [{"id": "s1c1-q1", "kind": "blank", "stem": "x",
                                          "blanks": ["  "]}]})))
    check("一次报多个问题", len(quiz.validate_quiz(
        {"chapter_id": "s1c1", "items": [dict(base, stem=""), dict(base, kind="essay")]})) >= 2)


# ---------- 与作答记录合并 ----------

def test_state():
    print("\n[章节判分状态]")
    path, old = use_temp_db()
    try:
        empty = quiz.chapter_state("s1c1", quiz={"items": [CHOICE, BLANK_ONE]})
        check("没答过时 total 正确", empty["total"] == 2, empty)
        check("没答过时 passed 是 0", empty["passed"] == 0)
        check("没答过时最新分是空", empty["latest_score"] is None)
        check("每题都标未作答", all(not row["answered"] for row in empty["items"]))

        database.add_quiz_attempt("agent-dev", "s1c1", "t-q1", "choice", "A", 0, 0, "local")
        database.add_quiz_attempt("agent-dev", "s1c1", "t-q1", "choice", "B", 100, 1, "local")
        state = quiz.chapter_state("s1c1", quiz={"items": [CHOICE, BLANK_ONE]})
        check("取最近一次而不是最高分", state["items"][0]["passed"] is True)
        check("通过数正确", state["passed"] == 1, state)
        check("已答数正确", state["answered"] == 1, state)

        database.add_quiz_attempt("agent-dev", "s1c1", "t-q1", "choice", "C", 0, 0, "local")
        state = quiz.chapter_state("s1c1", quiz={"items": [CHOICE, BLANK_ONE]})
        check("答错会覆盖之前的通过", state["items"][0]["passed"] is False, state["items"][0])
        check("最新分跟着变", state["latest_score"] == 0, state)

        database.add_quiz_attempt("agent-dev", "s4c1", "x-q1", "choice", "A", 100, 1, "local")
        state = quiz.chapter_state("s1c1", quiz={"items": [CHOICE]})
        check("跨章不串", state["passed"] == 0, state)
    finally:
        restore(old, path)


def test_record():
    print("\n[判分并落库]")
    curriculum = TempCurriculum()
    path, old = use_temp_db()
    try:
        one, two = at(CHOICE, "s1c1", 1), at(BLANK_ONE, "s1c1", 2)
        curriculum.write("s1c1", {"chapter_id": "s1c1", "items": [one, two]})

        result, state = quiz.record("s1c1", one, "B", seconds=12)
        check("返回判分结果", result["score"] == 100 and result["passed"] is True, result)
        check("落了一条记录", len(database.list_quiz_attempts("agent-dev", "s1c1")) == 1)
        check("返回章节状态", state["passed"] == 1 and state["total"] == 2, state)

        rows = database.list_quiz_attempts("agent-dev", "s1c1")
        check("答案原样存下来", rows[0]["answer"] == "B")
        check("用时钟住", rows[0]["seconds"] == 12)
        check("题型一起存", rows[0]["kind"] == "choice")

        progress = database.list_course_progress("agent-dev")
        check("进度表回写了成绩", progress["s1c1"]["quiz_score"] == 100, progress)
        check("进度表回写了作答时间", bool(progress["s1c1"]["quiz_at"]), progress)
        check("回写成绩不动 status", progress["s1c1"]["status"] == "todo", progress)

        _, state = quiz.record("s1c1", two, "token")
        check("答错也记", state["answered"] == 2, state)
        check("答错不通过", state["passed"] == 1, state)
        progress = database.list_course_progress("agent-dev")
        check("成绩被最新一次覆盖", progress["s1c1"]["quiz_score"] == 0, progress)
    finally:
        restore(old, path)
        curriculum.close()


# ---------- 模型兜底 ----------

def test_model_fallback():
    print("\n[模型兜底]")
    import llm

    original = llm.chat_completion
    calls = {"count": 0}

    def boom(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("网络断了")

    def reply(score, hit):
        def fake(*args, **kwargs):
            calls["count"] += 1
            payload = json.dumps({"hit": hit, "miss": [], "score": score, "reason": "说到了要点"})
            return SimpleNamespace(content=payload), None
        return fake

    try:
        llm.chat_completion = boom
        full = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算，中英文语言差异会让数量翻倍")
        check("有把握时不调模型", calls["count"] == 0, calls)
        check("有把握时直接返回本地分", full["score"] == 100 and full["grader"] == "local")

        calls["count"] = 0
        broken = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算")
        check("模型不可用时退回本地结论", broken["grader"] == "local", broken)
        check("模型不可用时保留本地分", broken["score"] == 67, broken)
        check("模型不可用时注明原因", "模型判定不可用" in broken["detail"], broken["detail"])
        check("确实尝试调过一次", calls["count"] == 1, calls)

        calls["count"] = 0
        llm.chat_completion = reply(85, [0, 1, 2])
        judged = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算")
        check("模糊时采信模型", judged["score"] == 85, judged)
        check("模型判的要标记来源", judged["grader"] == "model")
        check("模型判的也能通过", judged["passed"] is True)
        check("带上模型的依据", "说到了要点" in judged["detail"], judged["detail"])

        llm.chat_completion = reply(120, [0])
        clamped = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算")
        check("模型给的越界分被夹住", clamped["score"] == 100, clamped)

        llm.chat_completion = reply("八十五", [])
        garbled = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算")
        check("模型给非数字时退回本地", garbled["score"] == 67 and garbled["grader"] == "local", garbled)

        llm.chat_completion = lambda *a, **k: (SimpleNamespace(content="我不会判"), None)
        no_json = quiz.grade_with_model(RUBRIC, "token 数量不同，计费按 token 算")
        check("模型没给 JSON 时退回本地", no_json["score"] == 67 and no_json["grader"] == "local", no_json)
    finally:
        llm.chat_completion = original


def test_first_json():
    print("\n[从模型输出里取 JSON]")
    check("裸 JSON", quiz._first_json('{"score": 80}') == {"score": 80})
    check("代码块包着", quiz._first_json('```json\n{"score": 80}\n```') == {"score": 80})
    check("前面有废话", quiz._first_json('好的，结果是 {"score": 80}，就这样') == {"score": 80})
    check("两段 JSON 取第一段", quiz._first_json('{"score": 80}{"score": 20}') == {"score": 80})
    check("字符串里的花括号不干扰", quiz._first_json('{"reason": "写了 { 和 } 两个符号", "score": 70}')
          == {"reason": "写了 { 和 } 两个符号", "score": 70})
    check("没有 JSON 返回 None", quiz._first_json("我没有判出来") is None)
    check("空值返回 None", quiz._first_json("") is None)
    check("残缺 JSON 返回 None", quiz._first_json('{"score": 80') is None)


def main_():
    test_norm()
    test_choice()
    test_blank()
    test_rubric()
    test_bad_kind()
    test_public()
    test_load()
    test_validate()
    test_state()
    test_record()
    test_model_fallback()
    test_first_json()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
