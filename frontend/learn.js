/* Agent 开发学习台。
   三栏：目录 / 正文 / 导师。正文是文件里的 Markdown，导师走 /chat/stream 的 tutor 模式。
   这里不做任何内容生成——目录和正文都来自后端读文件，界面只负责渲染。 */

const state = {
  course: null,
  chapterId: '',
  chapter: null,
  chatId: null,
  sending: false,
  // 'chapter' | 'diagnostic' | 'profile' | 'plan'：非章节视图没有 chapterId，
  // 靠这个区分，别用 chapterId 空着来判断
  view: 'chapter',
  diagInfo: null,
  diagResult: null,
  plan: null,
  // 这一章的项目列表（/projects?chapter_id=）。对话框上的 project_id 来自 activeProject，
  // 切章就清空——不然在第 10 章带着第 11 章的项目说话，验收脚本会对错题。
  projects: [],
  activeProject: null,
  checkResult: null,
};

const el = (id) => document.getElementById(id);

/* ---------------- 基础请求 ---------------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (response.status === 401) {
    location.href = '/';
    throw new Error('需要登录');
  }
  if (!response.ok) {
    let detail = '请求失败';
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (e) { /* 非 JSON 响应，用默认文案 */ }
    throw new Error(detail);
  }
  return response.json();
}

/* ---------------- Markdown ---------------- */

function escapeHtml(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// 章节里的自测答案用的是 <details><summary>，所以白名单必须放它们进去，
// 否则 sanitize 会把标签拆掉，答案直接摊在页面上。
const ALLOWED_TAGS = new Set([
  'P', 'BR', 'STRONG', 'EM', 'DEL', 'CODE', 'PRE', 'BLOCKQUOTE',
  'UL', 'OL', 'LI', 'H1', 'H2', 'H3', 'H4', 'HR', 'A',
  'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD',
  'DETAILS', 'SUMMARY',
]);

function sanitize(html) {
  const doc = new DOMParser().parseFromString(String(html), 'text/html');
  doc.body.querySelectorAll('*').forEach((node) => {
    if (!ALLOWED_TAGS.has(node.tagName)) {
      node.replaceWith(...node.childNodes);
      return;
    }
    [...node.attributes].forEach((attr) => {
      const name = attr.name.toLowerCase();
      const keep = (node.tagName === 'A' && (name === 'href' || name === 'title'))
        || (node.tagName === 'DETAILS' && name === 'open');
      if (!keep) node.removeAttribute(attr.name);
    });
    if (node.tagName === 'A') {
      const href = node.getAttribute('href') || '';
      if (/^(https?:|mailto:)/i.test(href)) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      } else {
        node.removeAttribute('href');
      }
    }
  });
  return doc.body.innerHTML;
}

function renderMarkdown(text) {
  const src = text || '';
  if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
    try {
      return sanitize(marked.parse(src));
    } catch (e) { /* 落到下面的兜底 */ }
  }
  return sanitize(fallbackMarkdown(src));
}

// marked 没加载时的兜底。只保证标题、粗体、代码块、段落能看，
// 不做完整解析——但绝不能把裸的 Markdown 标记直接摊在页面上。
function fallbackMarkdown(src) {
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');

  const lines = String(src).split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i += 1;
      out.push(`<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (!line.trim()) { i += 1; continue; }

    const buf = [];
    while (i < lines.length && lines[i].trim()
      && !/^```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])) {
      buf.push(lines[i++]);
    }
    // 自测答案靠 details 折叠，兜底时也要留住这两个标签
    out.push(`<p>${inline(buf.join(' '))}</p>`
      .replace(/&lt;details&gt;&lt;summary&gt;([\s\S]*?)&lt;\/summary&gt;/g,
        '<details><summary>$1</summary>')
      .replace(/&lt;\/details&gt;/g, '</details>'));
  }
  return out.join('\n');
}

/* ---------------- 课程与目录 ---------------- */

async function loadCourse() {
  state.course = await api('/course');
  // 摸底卷的元信息（题数、上次成绩）用来画目录底部那张卡。
  // 拿不到就不画——诊断卷坏了不该让整页打不开。
  try {
    state.diagInfo = await api('/diagnostic');
  } catch (error) {
    state.diagInfo = null;
  }
  // 排期卡要显示「今天几章、什么时候收尾」。拿不到就不画卡，
  // 排期接口坏了不该让整页打不开。
  try {
    state.plan = await api('/plan');
  } catch (error) {
    state.plan = null;
  }
  renderProgress();
  renderToc();
  renderTocFoot();
}

function renderProgress() {
  const p = state.course.progress || { done: 0, total: 0, percent: 0 };
  el('courseName').textContent = state.course.title || '课程';
  el('progressText').textContent = `${p.done} / ${p.total} 章`;
  el('progressBar').style.width = `${p.percent || 0}%`;
}

function renderToc() {
  const chapters = state.course.chapters || [];
  const stages = state.course.stages || [];
  const byId = {};
  chapters.forEach((c) => { byId[c.id] = c; });

  const parts = stages.map((stage) => {
    const items = (stage.chapters || []).map((id) => byId[id]).filter(Boolean);
    if (!items.length) return '';
    const doneCount = items.filter((c) => c.status === 'done').length;
    const open = items.some((c) => c.id === state.chapterId) || doneCount < items.length;
    const chapterHtml = items.map((c) => {
      const cls = [
        'chap',
        c.id === state.chapterId ? 'active' : '',
        c.written ? '' : 'locked',
        c.skipped ? 'skipped' : '',
      ].filter(Boolean).join(' ');
      // 「跳过」这个标签不能省：目录里少了一个推荐，用户会以为是漏了，
      // 而不是「诊断说这段你会了」。
      const skipTag = c.skipped ? '<span class="skip-tag">跳过</span>' : '';
      return `<button class="${cls}" data-chapter="${c.id}"${c.skipped ? ' title="摸底诊断判定这一段可跳过，想学随时点"' : ''}>
        <span class="chap-no">${c.no}</span>
        <span class="chap-title">${escapeHtml(c.title)}</span>
        ${skipTag}
        ${quizLight(c.quiz)}
        <span class="dot ${c.status}"></span>
      </button>`;
    }).join('');
    return `<div class="stage ${open ? '' : 'collapsed'}" data-stage="${stage.id}">
      <button class="stage-head" data-toggle-stage="${stage.id}">
        <span class="caret">▾</span><span>${escapeHtml(stage.title)}</span>
        <span class="stage-count">${doneCount}/${items.length}</span>
      </button>
      <div class="stage-list">${chapterHtml}</div>
    </div>`;
  });

  el('tocBody').innerHTML = parts.join('') || '<p class="empty-hint">课程目录为空</p>';

  el('tocBody').querySelectorAll('[data-chapter]').forEach((btn) => {
    btn.addEventListener('click', () => openChapter(btn.dataset.chapter));
  });
  el('tocBody').querySelectorAll('[data-toggle-stage]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const node = btn.closest('.stage');
      if (node) node.classList.toggle('collapsed');
    });
  });
}

function diagnosticCardHtml() {
  const info = state.diagInfo;
  // 诊断卷读不到（文件坏了/后端旧版本）就不画这张卡，不显示一个点了会报错的按钮
  if (!info) return '';
  const last = info.last;
  const sub = last
    ? `摸底诊断 · 上次答对 ${last.correct}/${last.total}`
    : `摸底诊断 · ${info.total} 题约 ${info.minutes} 分钟`;
  const text = last
    ? '知道该从哪开始了。换台设备或过一阵可以重测'
    : '先测一下你已经会到哪，不然排进度只能靠猜';
  // 测过之后才给画像入口：10 行全是「未测」的画像没有信息量。
  const actions = last
    ? `<div class="diag-card-actions">
        <button id="diagGo">重新测一次</button>
        <button id="diagProfile">能力画像</button>
      </div>`
    : '<button id="diagGo">开始摸底</button>';
  return `<div class="diag-card">
    <small>${escapeHtml(sub)}</small>
    <b>${escapeHtml(text)}</b>
    ${actions}
  </div>`;
}

function nextCardHtml() {
  const next = state.course.next;
  const p = state.course.progress || {};
  if (!next) {
    const skipped = p.skipped || 0;
    // 「都跳完了」和「都学完了」是两件事。合并成一句「可以去做项目了」，
    // 会在用户一章正文都没读的情况下宣布他学完了。
    if (skipped > 0 && (p.done || 0) < (p.total || 0)) {
      return `<div class="next-card all-done">
        <small>没有必须按顺序学的了</small>
        <b>${skipped} 章按摸底结果跳过，想补哪章点左边就行</b>
      </div>`;
    }
    return `<div class="next-card all-done">
      <small>主干已学完</small>
      <b>可以去做项目了</b>
    </div>`;
  }
  // 兜底推荐：全被诊断判了可跳过，只留最后一章压舱。
  // 不能写「按依赖顺序」——左边目录全打着「跳过」，那句话没法自圆。
  // 也不能说「其它都跳了」：这一章自己也带着跳过标签，留着它是「建议走一遍」，
  // 不是「它不够格跳」。
  if (state.course.next_fallback) {
    return `<div class="next-card fallback">
      <small>全判了可跳过 · 至少走一遍</small>
      <b>第 ${next.no} 章 ${escapeHtml(next.title)}</b>
      <button id="nextGo">去这一章</button>
    </div>`;
  }
  return `<div class="next-card">
    <small>按依赖顺序 · 下一步</small>
    <b>第 ${next.no} 章 ${escapeHtml(next.title)}</b>
    <button id="nextGo">去这一章</button>
  </div>`;
}

function planCardHtml() {
  const plan = state.plan;
  if (!plan) return '';
  const basis = plan.basis || {};
  const stats = plan.stats || {};
  if (!plan.planned) {
    const saved = basis.saved_minutes
      ? `画像已省掉 ${basis.skipped} 章（${hours(basis.saved_minutes)}）`
      : '按每天能投入的时间排到具体日期';
    return `<div class="plan-card">
      <small>学习排期 · ${basis.learnable || 0} 章待学 · ${hours(basis.minutes || 0)}</small>
      <b>${escapeHtml(saved)}</b>
      <button id="planGo">排一份</button>
    </div>`;
  }
  const todayGroup = (plan.days || []).find((g) => g.is_today);
  // 只数还没学完的：今天两章学完一章后，卡片还写「今天 2 章」就是在催一件已经做完的事
  const count = todayGroup ? todayGroup.items.filter((item) => !item.done).length : 0;
  const left = stats.left || 0;
  const end = stats.end ? shortDay(stats.end) : '';
  return `<div class="plan-card ready">
    <small>学习排期 · ${end ? `预计 ${end} 收尾` : `${left} 章待学`}</small>
    <b>${count ? `今天 ${count} 章 · 还剩 ${left} 章` : `今天没有安排 · 还剩 ${left} 章`}</b>
    <button id="planGo">查看排期</button>
  </div>`;
}

function renderTocFoot() {
  el('tocFoot').innerHTML = diagnosticCardHtml() + nextCardHtml() + planCardHtml();
  const diagGo = el('diagGo');
  if (diagGo) diagGo.addEventListener('click', openDiagnostic);
  const diagProfile = el('diagProfile');
  if (diagProfile) diagProfile.addEventListener('click', openProfile);
  const nextGo = el('nextGo');
  if (nextGo) nextGo.addEventListener('click', () => state.course.next && openChapter(state.course.next.id));
  const planGo = el('planGo');
  if (planGo) planGo.addEventListener('click', openPlan);
}

/* ---------------- 章节正文 ---------------- */

async function openChapter(chapterId) {
  if (!chapterId) return;
  state.view = 'chapter';
  state.chapterId = chapterId;
  location.hash = chapterId;
  closeDrawers();

  el('readerInner').innerHTML = '<div class="empty-state"><p>加载中…</p></div>';
  try {
    const payload = await api(`/course/chapter/${encodeURIComponent(chapterId)}`);
    state.chapter = payload;
    renderChapter(payload);
    renderToc();
    renderTutorHead();
    await ensureChat(chapterId, payload.chapter);
    // 项目列表失败不该让整章打不开：它只是章节页上的一块，正文更重要。
    // 失败时把 check_available 定成 false，否则卡片会一直停在「正在确认…」。
    await loadProjects(chapterId).catch(() => {
      if (state.chapter) state.chapter.check_available = false;
      renderTutorHead();
    });
  } catch (error) {
    el('readerInner').innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

/* 「这一段可以跳过」的提示条。抽成函数是为了能在答题后原地更新：
   答错会让这一章退出跳过，提示必须跟着消失——否则页面上会同时写着
   「这段可以跳过」和「下一步就是这一章」，自相矛盾。 */
function skipGateHtml(c) {
  if (!c || !c.skipped) return '';
  const why = c.skip_evidence ? `（${escapeHtml(c.skip_evidence)}）` : '';
  // 兜底那一章不许再说「不推荐」：推荐卡片正指着它。
  const fallback = state.course && state.course.next_fallback
    && state.course.next && state.course.next.id === c.id;
  if (fallback) {
    return `<div class="gate skip">这一段摸底诊断判到 ${c.skip_level} 级${why}，
      别的章都可以跳，这一章留着当压舱石：诊断只能证明你认得这些概念，
      证明不了你真能跑通一遍。答错题会自动把它排回推荐。</div>`;
  }
  return `<div class="gate skip">这一段摸底诊断判到 ${c.skip_level} 级${why}，
    所以不推荐也不拦你。想复习就往下读；这里答错题，会自动把它重新排回推荐。</div>`;
}

function syncSkipGate() {
  const host = el('skipGateHost');
  const fresh = (state.course.chapters || []).find((x) => x.id === state.chapterId);
  if (!host || !fresh) return;
  host.innerHTML = skipGateHtml(fresh);
  if (state.chapter && state.chapter.chapter) {
    state.chapter.chapter.skipped = fresh.skipped;
    state.chapter.chapter.skip_level = fresh.skip_level;
    state.chapter.chapter.skip_evidence = fresh.skip_evidence;
  }
}

function renderChapter(payload) {
  const c = payload.chapter;
  const blocked = payload.blocked_by || [];
  const gateHtml = blocked.length
    ? `<div class="gate">这一章建议先学完：
        ${blocked.map((b) => `第 ${b.no} 章《${escapeHtml(b.title)}》`).join('、')}。
        现在读也能读，但后面几步会用到它们。</div>`
    : '';
  // 被跳过的章也要说清为什么。不说的话，用户看到目录里有个「跳过」标签、
  // 又找不到任何解释，会以为是自己误操作点掉的。
  const skipHtml = skipGateHtml(c);
  const done = c.status === 'done';

  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">${escapeHtml(c.stage_title || '')}</span>
      <span>第 ${payload.index} / ${payload.total} 章</span>
      <span>·</span>
      <span>预计 ${c.minutes || 40} 分钟</span>
    </div>
    ${gateHtml}
    <div id="skipGateHost">${skipHtml}</div>
    <article class="prose">${renderMarkdown(payload.markdown)}</article>
    ${renderQuiz(payload.quiz)}
    ${projectCardHtml()}
    <div class="reader-actions">
      <button class="btn" id="prevBtn" ${payload.prev ? '' : 'disabled'}>← 上一章</button>
      <button class="btn primary ${done ? 'done' : ''}" id="doneBtn">
        ${done ? '✓ 已学完（点击撤销）' : '标记这一章学完'}
      </button>
      <div class="spacer"></div>
      <span class="hint" id="doneHint"></span>
      <button class="btn" id="nextBtn" ${payload.next ? '' : 'disabled'}>下一章 →</button>
    </div>`;

  el('prevBtn').addEventListener('click', () => payload.prev && openChapter(payload.prev));
  el('nextBtn').addEventListener('click', () => payload.next && openChapter(payload.next));
  el('doneBtn').addEventListener('click', () => toggleDone(c));
  bindQuiz();
  bindProjectCard();
  el('reader').scrollTop = 0;
}

async function toggleDone(chapter) {
  const target = chapter.status === 'done' ? 'todo' : 'done';
  const button = el('doneBtn');
  button.disabled = true;
  try {
    const result = await api('/course/progress', {
      method: 'PATCH',
      body: JSON.stringify({
        chapter_id: chapter.id,
        status: target,
        // 掌握度不自己填：手点只该给「看懂了」，三题全过才够「自己写出来」。
        // 服务端按答题记录算，这里填了也会被压回来，索性不填（撤销时清零）。
        mastery: target === 'done' ? undefined : 0,
      }),
    });
    state.course.progress = result.summary;
    state.course.next = result.next;
    // 只就地改状态，不走 openChapter：那条路径会重建导师对话区，
    // 用户刚聊完就点「标记学完」，聊天记录会凭空消失。
    chapter.status = target;
    const current = state.chapter && state.chapter.chapter;
    if (current && current.id === chapter.id) current.status = target;
    button.className = `btn primary ${target === 'done' ? 'done' : ''}`;
    button.textContent = target === 'done' ? '✓ 已学完（点击撤销）' : '标记这一章学完';
    button.disabled = false;
    renderProgress();
    renderToc();
    renderTocFoot();
    el('doneHint').textContent = target === 'done'
      ? `已记录（掌握度 ${result.progress.mastery}/3，做完自测题才够 2）`
      : '已撤销';
    // 排期卡的「今天几章」跟着学习记录走，不然刚点完学完，卡片还说他今天有这章
    await refreshPlanCard();
  } catch (error) {
    button.disabled = false;
    el('doneHint').textContent = error.message;
  }
}

/* ---------------- 自测判分 ---------------- */

// 目录里的小圆点：只看「有题库的章」。没题库不画点，画了等于告诉用户「这章没自测」，
// 而 30 章暂时都没题库，画出来就是一片灰点噪音。
function quizLight(quiz) {
  if (!quiz || !quiz.total) return '';
  const cls = quiz.passed >= quiz.total ? 'ok' : (quiz.answered ? 'half' : 'new');
  const label = quiz.passed >= quiz.total
    ? `自测 ${quiz.total} 题全过`
    : (quiz.answered
      ? `自测过了 ${quiz.passed}/${quiz.total} 题，还有没过的`
      : `有 ${quiz.total} 道自测题，还没答`);
  return `<span class="qlight ${cls}" title="${escapeHtml(label)}"></span>`;
}

function quizRows() {
  const quiz = state.chapter && state.chapter.quiz;
  return (quiz && quiz.state && quiz.state.items) || [];
}

function quizRowOf(itemId) {
  return quizRows().find((row) => row.id === itemId) || null;
}

// 选项文字本来就长这样：「A. 城市」。取前面那个字母当提交值，
// 不然提交整句会让判分器在句子里到处找字母（「B. 城市」里还有一个 B 之外的字母风险）。
function optionLetter(option, index) {
  const matched = String(option || '').match(/^\s*([A-Za-z])\s*[.、．)）:：]/);
  return matched ? matched[1].toUpperCase() : String.fromCharCode(65 + index);
}

function quizItemHtml(item, index, options) {
  const opts = options || {};
  // 诊断页是盲测，不显示「上次几分」——那等于提示这题他做过、对过
  const prev = opts.blind ? null : quizRowOf(item.id);
  const badge = prev && prev.answered
    ? `<span class="q-badge ${prev.passed ? 'ok' : 'bad'}">${prev.passed ? '已过' : '待重做'} ${prev.score} 分</span>`
    : '';

  let field = '';
  if (item.kind === 'choice') {
    const type = item.multi ? 'checkbox' : 'radio';
    field = `<div class="q-options">${(item.options || []).map((option, i) => `
      <label class="q-opt">
        <input type="${type}" name="pick-${item.id}" value="${optionLetter(option, i)}">
        <span>${escapeHtml(option)}</span>
      </label>`).join('')}</div>`;
  } else if (item.kind === 'blank') {
    const count = Math.max(1, item.blank_count || 1);
    field = `<div class="q-blanks">${Array.from({ length: count }, (_, i) =>
      `<input class="q-blank" type="text" placeholder="第 ${i + 1} 空" autocomplete="off" spellcheck="false">`
    ).join('')}</div>`;
  } else {
    field = `<textarea class="q-text" rows="3" placeholder="${opts.blind
      ? '用自己的话写两三句，不用背原文'
      : '用自己的话写两三句，说到要点就算过'}"></textarea>`;
  }

  const foot = opts.blind ? '' : `<div class="q-foot">
      <button class="btn q-submit" type="button" data-submit="${item.id}">提交</button>
      <span class="q-note" data-note="${item.id}"></span>
    </div>
    <div class="q-result" data-result="${item.id}" hidden></div>`;

  return `<div class="q-item" data-item="${item.id}">
    <div class="q-stem"><span class="q-no">${index + 1}</span><span class="q-stem-text">${escapeHtml(item.stem)}</span>${badge}</div>
    ${field}
    ${foot}
  </div>`;
}

function renderQuiz(quiz) {
  if (!quiz) return '';
  if (quiz.error) {
    // 题库写坏了要明说。静默当成「这章没题」，写题的人永远发现不了，
    // 用户则会以为自己答了对的题却没被记录。
    return `<section class="quiz quiz-error">
      <div class="quiz-head"><b>自测</b><span>暂时用不了</span></div>
      <p>这一章的题库有问题：${escapeHtml(quiz.error)}</p>
    </section>`;
  }
  const items = quiz.items || [];
  if (!items.length) return '';

  return `<section class="quiz" id="quizCard">
    <div class="quiz-head">
      <b>自测 · ${items.length} 题</b>
      <span id="quizSummary"></span>
    </div>
    <p class="quiz-tip">判分在本机算，同一份答案永远是同一个分数。写不出完整句子没关系，说到要点就算过。</p>
    <div class="quiz-list">${items.map((item, i) => quizItemHtml(item, i)).join('')}</div>
  </section>`;
}

function bindQuiz() {
  const card = el('quizCard');
  if (!card) return;
  updateQuizSummary();
  card.querySelectorAll('[data-submit]').forEach((button) => {
    button.addEventListener('click', () => {
      const root = button.closest('.q-item');
      const item = (state.chapter.quiz.items || []).find((row) => row.id === button.dataset.submit);
      if (item && root) submitQuizItem(item, root, button);
    });
  });
}

function updateQuizSummary() {
  const node = el('quizSummary');
  if (!node) return;
  const quiz = state.chapter && state.chapter.quiz;
  const quizState = (quiz && quiz.state) || null;
  if (!quizState || !quizState.total) {
    node.textContent = '';
    return;
  }
  node.textContent = quizState.answered
    ? `答过 ${quizState.answered}/${quizState.total} · 过了 ${quizState.passed} 题`
    : '还没答过';
}

function collectAnswer(root, item) {
  if (item.kind === 'choice') {
    // 多选要排序：判分器按集合比，但排一下能让「提交了什么」在日志里可读
    return [...root.querySelectorAll('input:checked')].map((node) => node.value).sort().join('');
  }
  if (item.kind === 'blank') {
    // 多个空按行拼。判分器按行切，前端不要自己加序号或标点
    return [...root.querySelectorAll('.q-blank')].map((node) => node.value.trim()).join('\n');
  }
  const area = root.querySelector('.q-text');
  return area ? area.value.trim() : '';
}

function syncQuizToCourse(quizState, mastery) {
  const chapter = (state.course.chapters || []).find((c) => c.id === state.chapterId);
  if (chapter && quizState) {
    chapter.quiz = {
      total: quizState.total,
      answered: quizState.answered,
      passed: quizState.passed,
    };
  }
  if (chapter && mastery) chapter.mastery = mastery.after;
}

async function submitQuizItem(item, root, button) {
  const answer = collectAnswer(root, item);
  const note = root.querySelector('[data-note]');
  const resultBox = root.querySelector('[data-result]');
  if (!answer) {
    note.textContent = item.kind === 'choice' ? '先选一项再提交' : '还没写东西';
    return;
  }
  button.disabled = true;
  note.textContent = '判分中…';
  try {
    const result = await api(`/quiz/${encodeURIComponent(state.chapterId)}/submit`, {
      method: 'POST',
      body: JSON.stringify({ item_id: item.id, answer }),
    });
    // 整章状态用服务端回的覆盖，不在前端自己累加「过了几题」：
    // 一旦两边口径不同（比如重做把原来的过改成没过），目录灯就会说谎。
    state.chapter.quiz.state = result.quiz;
    syncQuizToCourse(result.quiz, result.mastery);
    // 答错会让这一章退出「诊断跳过」、重新排回推荐，所以必须重拉课程树：
    // 只就地改灯的话，目录标签和下一步卡片会停在旧结论上，
    // 表现成「答错了一题，推荐还是绕开这一章」。
    loadCourse().then(syncSkipGate).catch(() => renderToc());
    updateQuizSummary();

    note.textContent = '';
    resultBox.hidden = false;
    resultBox.className = `q-result ${result.passed ? 'ok' : 'bad'}`;
    const masteryNote = result.mastery && result.mastery.changed
      ? `<p class="q-mastery">掌握度 ${result.mastery.before} → ${result.mastery.after}：${escapeHtml(result.mastery.reason)}</p>`
      : '';
    resultBox.innerHTML = `
      <div class="q-score">${result.score} 分 · ${result.passed ? '通过' : '没过'}${
        result.grader === 'model' ? ' · 这题交给模型复核过' : ''}</div>
      <p>${escapeHtml(result.detail || '')}</p>
      ${result.explain ? `<p class="q-explain">${escapeHtml(result.explain)}</p>` : ''}
      ${masteryNote}`;

    const badgeHost = root.querySelector('.q-stem');
    const old = badgeHost.querySelector('.q-badge');
    if (old) old.remove();
    if (result.quiz && result.quiz.items) {
      const row = result.quiz.items.find((r) => r.id === item.id);
      if (row && row.answered) {
        const badge = document.createElement('span');
        badge.className = `q-badge ${row.passed ? 'ok' : 'bad'}`;
        badge.textContent = `${row.passed ? '已过' : '待重做'} ${row.score} 分`;
        badgeHost.appendChild(badge);
      }
    }
  } catch (error) {
    note.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/* ---------------- 摸底诊断 ---------------- */

function stageNo(stageId) {
  const matched = String(stageId || '').match(/(\d+)/);
  return matched ? Number(matched[1]) : 99;
}

function stageTitle(stageId) {
  const stage = (state.course.stages || []).find((s) => s.id === stageId);
  return stage ? stage.title : stageId;
}

async function openDiagnostic() {
  state.view = 'diagnostic';
  state.chapterId = '';
  state.diagResult = null;
  location.hash = 'diagnostic';
  closeDrawers();
  el('tutorWhere').textContent = '正在做摸底诊断';
  // 导师区不跟着换成诊断的说明，右上角写着「正在做诊断」、下面却还是第 1 章的
  // 验收标准，会让人以为自己点错了。快捷问句也一并收起——诊断期间问章节问题没有落点。
  el('tutorLog').innerHTML = `<div class="tutor-hint">
    <p>诊断进行中。这里只测你已经会到哪，<b>不会的直接留空</b>，
    答错不会扣掉任何进度——只是让起点算得更准一点。</p>
  </div>`;
  el('tutorQuick').innerHTML = '';
  el('readerInner').innerHTML = '<div class="empty-state"><p>正在取卷子…</p></div>';
  try {
    const data = await api('/diagnostic');
    state.diagInfo = data;
    renderDiagnostic(data);
  } catch (error) {
    el('readerInner').innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderDiagnostic(data) {
  const items = data.items || [];
  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">摸底诊断</span>
      <span>${items.length} 题</span><span>·</span><span>约 ${data.minutes || 12} 分钟</span>
    </div>
    <h1 class="diag-title">${escapeHtml(data.title || '摸底诊断')}</h1>
    <p class="diag-desc">${escapeHtml(data.description || '')}</p>
    <div class="gate diag-gate">这次只为了知道你该从哪一章开始，不是考试。不会的直接留空，
      一题别超过一分钟，也不要查资料——查了测出来的就不是你了。</div>
    <div class="quiz-list diag-list">
      ${items.map((item, i) => quizItemHtml(item, i, { blind: true })).join('')}
    </div>
    <div class="diag-actions">
      <button class="btn primary" id="diagSubmit">交卷</button>
      <span class="hint" id="diagHint"></span>
    </div>`;
  el('reader').scrollTop = 0;
  el('diagSubmit').addEventListener('click', submitDiagnostic);
}

function collectDiagnosticAnswers() {
  const data = state.diagInfo || {};
  const answers = {};
  (data.items || []).forEach((item) => {
    const root = document.querySelector(`.q-item[data-item="${item.id}"]`);
    if (!root) return;
    // 诊断允许留空：跳过和不会在这里是同一个意思，不拦
    answers[item.id] = collectAnswer(root, item);
  });
  return answers;
}

async function submitDiagnostic() {
  const button = el('diagSubmit');
  const hint = el('diagHint');
  button.disabled = true;
  hint.textContent = '正在判分…';
  try {
    const result = await api('/diagnostic/submit', {
      method: 'POST',
      body: JSON.stringify({ answers: collectDiagnosticAnswers() }),
    });
    state.diagResult = result;
    // 卡片上的「上次答对 X/Y」跟着立刻更新，不用等刷新
    state.diagInfo = { ...(state.diagInfo || {}), last: { correct: result.correct, total: result.total } };
    // 画像变了：目录的「跳过」标签、排期的依据都跟着变。不重拉课程树的话，
    // 交完卷回到课程，目录还是老样子，用户会以为诊断没生效。
    await loadCourse();
    renderDiagnosticResult(result);
  } catch (error) {
    hint.textContent = error.message;
    button.disabled = false;
  }
}

// 正确答案可能是选项字母，也可能是「每空一组同义写法」的嵌套数组
function answerText(answer) {
  if (Array.isArray(answer)) {
    return answer.map((group) => (Array.isArray(group) ? group.join(' / ') : String(group))).join('｜');
  }
  return String(answer === null || answer === undefined ? '' : answer);
}

function renderDiagnosticResult(result) {
  const levels = result.levels || {};
  const stages = Object.keys(levels).sort((a, b) => stageNo(a) - stageNo(b));
  const levelRow = (stage) => {
    const row = levels[stage];
    const width = Math.round((row.level / Math.max(1, result.max_level || 2)) * 100);
    // 0 级不写「未测」：这一阶段他答了但两道都错，说「未测」是假话
    const label = row.level > 0 ? (row.label || '') : '还没到';
    return `<div class="lv-row">
      <span class="lv-name">${escapeHtml(stageTitle(stage))}</span>
      <span class="lv-bar"><i style="width:${width}%"></i></span>
      <span class="lv-val">${escapeHtml(label)} ${row.correct}/${row.total}</span>
    </div>`;
  };

  const review = result.review || [];
  const reviewHtml = review.map((row) => `
    <div class="rv-item ${row.correct ? 'ok' : 'bad'}">
      <div class="rv-stem"><span class="rv-mark">${row.correct ? '✓' : '✗'}</span>${escapeHtml(row.stem)}</div>
      <div class="rv-line">你的作答：<b>${row.yours ? escapeHtml(answerText(row.yours)) : '（留空）'}</b></div>
      ${row.correct ? '' : `<div class="rv-line">正确答案：<b>${escapeHtml(answerText(row.answer))}</b></div>`}
      ${row.explain ? `<div class="rv-explain">${escapeHtml(row.explain)}</div>` : ''}
    </div>`).join('');

  const suggestions = stages.filter((stage) => (levels[stage].level || 0) < 2).slice(0, 3);

  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">摸底结果</span>
      <span>答对 ${result.correct} / ${result.total}</span>
    </div>
    <h1 class="diag-title">你的起点</h1>
    <p class="diag-desc">${suggestions.length
      ? `这几段还没到位：${suggestions.map((s) => escapeHtml(stageTitle(s))).join('、')}。从最前面那段开始补，后面的会轻松很多。`
      : '十个阶段都答对了。可以直接从第 1 章的练习开始快进，遇到卡的地方再回头。'}</p>

    <div class="lv-list">${stages.map(levelRow).join('')}</div>
    <p class="diag-note">等级：听过 → 能写 → 能讲。诊断只出选择题和填空题，
      最高判到「能写」；「能讲」得靠章节里的开放题，那才真的要求把话说清楚。</p>

    <div class="diag-actions">
      <button class="btn primary" id="diagAgain">再测一次</button>
      <button class="btn" id="diagBack">回到课程</button>
    </div>

    <h2 class="diag-h2">逐题回看</h2>
    <div class="rv-list">${reviewHtml}</div>`;

  el('reader').scrollTop = 0;
  el('diagAgain').addEventListener('click', () => {
    if (state.diagInfo) renderDiagnostic(state.diagInfo);
  });
  el('diagBack').addEventListener('click', backToCourse);
}

/* ---------------- 能力画像 ---------------- */

function ageText(days) {
  if (days === null || days === undefined) return '';
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  return `${days} 天前`;
}

function backToCourse() {
  const next = state.course.next;
  openChapter(next ? next.id : (state.course.chapters[0] || {}).id);
}

/* 画像和诊断结果是两张页：结果页回答「这次考成什么样」，画像页回答「现在会到哪」。
   后者要能随时点开，因为它是推荐、跳章、导师说话的共同依据——看不见依据，
   用户就只能相信或怀疑系统，没法核对。 */
async function openProfile() {
  state.view = 'profile';
  state.chapterId = '';
  location.hash = 'profile';
  closeDrawers();
  el('tutorWhere').textContent = '正在看能力画像';
  el('tutorLog').innerHTML = `<div class="tutor-hint">
    <p>画像决定推荐绕开哪几段。看到不对的地方，最直接的改法是去那一章答题——
    <b>章节实测能推翻诊断</b>，答错了那一章就会重新排回推荐。</p>
  </div>`;
  el('tutorQuick').innerHTML = '';
  el('readerInner').innerHTML = '<div class="empty-state"><p>正在读画像…</p></div>';
  try {
    renderProfile(await api('/profile'));
  } catch (error) {
    el('readerInner').innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderProfile(data) {
  const stages = data.stages || [];
  const last = data.last;
  const maxLevel = Math.max(1, data.max_level || 3);
  const rows = stages.map((row) => {
    const bar = Math.round((row.level / maxLevel) * 100);
    const parts = [];
    if (row.level > 0) {
      parts.push(`${escapeHtml(row.source_label || '来源不明')} · ${escapeHtml(row.evidence || '没写依据')}`);
      const age = ageText(row.age_days);
      if (age) parts.push(age);
    } else {
      parts.push('还没测过');
    }
    const quiz = row.quiz || {};
    if (quiz.with_quiz) {
      // 覆盖度必须写出来：只答过 1 章就说「这阶段会了」，和答完 3 章不是一回事
      parts.push(`实测 ${quiz.tested}/${quiz.with_quiz} 章答过、全过 ${quiz.fully_passed} 章`);
    }
    const tags = [];
    if (row.challenged) tags.push('<span class="pf-tag warn">实测没过</span>');
    else if (row.skip) tags.push('<span class="pf-tag skip">跳过中</span>');
    if (row.stale) tags.push('<span class="pf-tag stale">该复核</span>');
    return `<div class="pf-row${row.level ? '' : ' empty'}">
      <div class="pf-head">
        <span class="pf-name">${row.no}. ${escapeHtml(row.title)}</span>
        <span class="pf-lv">${escapeHtml(row.level ? row.label : '未测')}</span>
      </div>
      <span class="pf-bar"><i style="width:${bar}%"></i></span>
      <div class="pf-meta">${parts.join(' · ')}${tags.join('')}</div>
    </div>`;
  }).join('');

  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">能力画像</span>
      <span>${stages.length} 个阶段</span>
      ${last ? `<span>·</span><span>最近摸底 ${escapeHtml(ageText(last.age_days) || last.at || '')}</span>` : ''}
    </div>
    <h1 class="diag-title">你现在会到哪</h1>
    <p class="diag-desc">${last
      ? `上次摸底答对 ${last.correct}/${last.total}。`
      : '还没做过摸底，下面是空白画像。'}画像不是评分，是推荐和导师共用的依据，
      每一行都写着它凭什么这么判。</p>

    <div class="pf-list">${rows}</div>
    <p class="diag-note">等级：未测 → 听过 → 能写 → 能讲。摸底只有选择题和填空题，最高判到「能写」；
      「能讲」得靠章节里的开放题。标<b>跳过中</b>的段，推荐会绕开它们。标<b>实测没过</b>的，
      是诊断说你会、但章节题没做对——那几章已经重新排回推荐，跳不过去了。
      结论超过 ${data.stale_days} 天仍在使用，但会打上<b>该复核</b>。</p>

    <div class="diag-actions">
      <button class="btn primary" id="pfAgain">重新测一次</button>
      <button class="btn" id="pfBack">回到课程</button>
    </div>`;

  el('reader').scrollTop = 0;
  el('pfAgain').addEventListener('click', openDiagnostic);
  el('pfBack').addEventListener('click', backToCourse);
}

/* ---------------- 学习排期 ---------------- */

function hours(minutes) {
  const value = Number(minutes || 0) / 60;
  if (!value) return '0 小时';
  return value >= 10 ? `${Math.round(value)} 小时` : `${value.toFixed(1)} 小时`;
}

function shortDay(iso) {
  const parts = String(iso || '').split('-');
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : String(iso || '');
}

// 只刷卡片，不重画正文：学完一章后回到目录，不需要把当前页滚回顶部。
async function refreshPlanCard() {
  try {
    state.plan = await api('/plan');
  } catch (error) {
    return;
  }
  if (state.plan) renderTocFoot();
}

async function openPlan() {
  state.view = 'plan';
  state.chapterId = '';
  location.hash = 'plan';
  closeDrawers();
  el('tutorWhere').textContent = '正在看学习排期';
  el('tutorLog').innerHTML = `<div class="tutor-hint">
    <p>排期只排「还没学会、也没被判跳过」的章。学完一章，第二天重排时它就不会再出现；
    <b>诊断说你会的那几段，一开始就不占时间</b>。</p>
  </div>`;
  el('tutorQuick').innerHTML = '';
  el('readerInner').innerHTML = '<div class="empty-state"><p>正在读排期…</p></div>';
  try {
    state.plan = await api('/plan');
    renderPlan();
  } catch (error) {
    el('readerInner').innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function planDayHtml(group) {
  const items = (group.items || []).map((item) => `
    <div class="plan-item ${item.done ? 'done' : ''} ${item.overdue ? 'late' : ''}">
      <span class="plan-no">${item.no}</span>
      <button class="plan-title" data-open="${item.chapter_id}">${escapeHtml(item.title)}</button>
      <span class="plan-min">${item.minutes} 分钟</span>
      ${item.done
    ? '<span class="plan-flag ok">已学完</span>'
    : `<button class="plan-act" data-done="${item.chapter_id}">标记学完</button>`}
    </div>`).join('');
  return `<div class="plan-day ${group.is_today ? 'today' : ''} ${group.is_past ? 'past' : ''}">
    <div class="plan-day-head">
      <b>${group.is_today ? '今天' : escapeHtml(group.weekday)}</b>
      <span class="plan-date">${shortDay(group.day)}</span>
      <span class="plan-day-min">${group.minutes} 分钟</span>
    </div>
    ${items}
  </div>`;
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) {
    el('readerInner').innerHTML = '<div class="empty-state"><p>读不到排期。</p></div>';
    return;
  }
  const settings = plan.settings || {};
  const stats = plan.stats || {};
  const basis = plan.basis || {};
  const checked = new Set(settings.weekdays || []);
  const weekdays = [1, 2, 3, 4, 5, 6, 7].map((day) => `
    <label class="plan-wd ${checked.has(day) ? 'on' : ''}">
      <input type="checkbox" data-plan-day="${day}" ${checked.has(day) ? 'checked' : ''}>
      ${'一二三四五六日'[day - 1]}
    </label>`).join('');

  const saved = basis.saved_minutes
    ? `其中 ${basis.skipped} 章按摸底结果跳过，省下 ${hours(basis.saved_minutes)}。`
    : '';
  // stale 时不能按「当前画像」写说明：那句「共 33 章、省下 6.7 小时」自相矛盾
  // ——33 章是上次排的，6.7 小时是现在才有的。分开说，别让用户自己猜哪句算数。
  const intro = plan.stale
    ? `这份排期是按上次画像排的：共 ${stats.slots} 章、${hours(stats.minutes_total)}，`
      + `排进 ${stats.days} 个学习日${stats.end ? `，预计 ${shortDay(stats.end)} 收尾` : ''}。`
      + '画像变化写在下面那条提示里，重排后才生效。'
    : (plan.planned
      ? `共 ${stats.slots} 章、${hours(stats.minutes_total)}，按每天 ${settings.minutes} 分钟排进 ${stats.days} 个学习日${stats.end ? `，预计 ${shortDay(stats.end)} 收尾` : ''}。${saved}`
      : ((basis.learnable || 0) === 0
        ? '没有要排的章节了：能学的都学完，或者被摸底诊断判过了。'
        : `还没排。${basis.learnable || 0} 章待学、${hours(basis.minutes || 0)}。${saved}定好每天能投入多少，剩下的日期由程序算——不让模型猜你哪天有空。`));

  const overdue = (stats.overdue || []).length;
  const overdueHtml = overdue ? `<div class="gate plan-overdue">
    有 ${overdue} 章（${stats.overdue_minutes} 分钟）没按计划完成。三种处理方式，选一个：
    <div class="plan-strategies">
      <button data-strategy="shift">顺延 · 结束日往后推</button>
      <button data-strategy="compress">压缩 · 每天多塞一点</button>
      <button data-strategy="drop">放弃 · 这几章本轮不排</button>
    </div>
  </div>` : '';
  // 画像变了但排期没重排：不提示的话，页面一边说「共 33 章」，一边说
  // 「10 章可以跳过」，用户不知道该信哪个。
  const staleHtml = plan.stale ? `<div class="gate plan-stale">
    摸底结果变了，这份排期还是按上次画像排的（现在有 ${basis.skipped} 章可以跳过）。
    <button id="planStale">按新画像重排</button>
  </div>` : '';

  const dayHtml = (plan.days || []).map(planDayHtml).join('')
    || '<p class="empty-hint">没有排进去的章节。</p>';

  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">学习排期</span>
      <span>每天 ${settings.minutes} 分钟</span>
      <span>·</span>
      <span>${plan.planned ? `${stats.days} 个学习日` : '还没排'}</span>
    </div>
    <h1 class="diag-title">接下来每天学什么</h1>
    <p class="diag-desc">${escapeHtml(intro)}</p>
    ${overdueHtml}
    ${staleHtml}

    <div class="plan-settings">
      <div class="plan-row">
        <label>开始日 <input type="date" id="planStart" value="${escapeHtml(settings.start_date || plan.today || '')}" min="${escapeHtml(plan.today || '')}"></label>
        <label>每天 <input type="number" id="planMinutes" min="10" max="480" step="10" value="${settings.minutes || 90}"> 分钟</label>
      </div>
      <div class="plan-row">
        <span class="plan-label">每周</span>
        <div class="plan-weekdays">${weekdays}</div>
      </div>
      <div class="plan-row">
        <label class="plan-inline"><input type="checkbox" id="planInclude" ${settings.include_skipped ? 'checked' : ''}> 把诊断判过的章也排进来（复习）</label>
      </div>
      <div class="plan-actions">
        <button class="btn primary" id="planBuild">${plan.planned ? '按这些设置重排' : '开始排期'}</button>
        <button class="btn" id="planModel" title="模型只决定先学哪几章，日期和分钟仍由程序算">让 AI 排先后</button>
        <span class="hint" id="planHint"></span>
      </div>
    </div>

    <div class="plan-list">${dayHtml}</div>

    ${plan.planned ? `<div class="plan-foot">
      <span>已完成 ${stats.done} / ${stats.slots} 章 · 还剩 ${hours(stats.minutes_left)}</span>
      <button class="plan-clear" id="planClear">清空排期</button>
    </div>` : ''}`;

  el('reader').scrollTop = 0;
  bindPlan();
}

function bindPlan() {
  const build = el('planBuild');
  if (build) build.addEventListener('click', () => submitPlan(false));
  const model = el('planModel');
  if (model) model.addEventListener('click', () => submitPlan(true));
  const clear = el('planClear');
  if (clear) clear.addEventListener('click', clearPlan);
  const stale = el('planStale');
  if (stale) stale.addEventListener('click', () => submitPlan(false));

  el('readerInner').querySelectorAll('[data-strategy]').forEach((btn) => {
    btn.addEventListener('click', () => reschedulePlan(btn.dataset.strategy));
  });
  el('readerInner').querySelectorAll('[data-open]').forEach((btn) => {
    btn.addEventListener('click', () => openChapter(btn.dataset.open));
  });
  el('readerInner').querySelectorAll('[data-done]').forEach((btn) => {
    btn.addEventListener('click', () => markPlanDone(btn.dataset.done));
  });
  el('readerInner').querySelectorAll('[data-plan-day]').forEach((box) => {
    box.addEventListener('change', () => {
      box.closest('.plan-wd').classList.toggle('on', box.checked);
    });
  });
}

function planSettingsFromForm() {
  const start = el('planStart') ? el('planStart').value : '';
  const minutes = Number(el('planMinutes') ? el('planMinutes').value : 90);
  const weekdays = [...el('readerInner').querySelectorAll('[data-plan-day]:checked')]
    .map((box) => box.dataset.planDay).join(',');
  const include = !!(el('planInclude') && el('planInclude').checked);
  const hint = el('planHint');
  if (!weekdays) {
    if (hint) hint.textContent = '至少选一天。一周一天都不学，排期没法排。';
    return null;
  }
  if (!minutes || minutes < 10) {
    if (hint) hint.textContent = '每天至少 10 分钟。';
    return null;
  }
  return { start_date: start, weekdays, minutes, include_skipped: include };
}

async function submitPlan(useModel) {
  const config = planSettingsFromForm();
  if (!config) return;
  const button = el(useModel ? 'planModel' : 'planBuild');
  const old = button.textContent;
  button.disabled = true;
  button.textContent = useModel ? 'AI 排列中…' : '正在排…';
  try {
    const result = await api('/plan/build', {
      method: 'POST',
      body: JSON.stringify({ ...config, use_model: useModel }),
    });
    state.plan = result;
    renderPlan();
    const hint = el('planHint');
    if (hint) {
      if (!useModel) hint.textContent = '已按当前设置重排。';
      else if (result.order_source === 'model') hint.textContent = `AI：${result.order_reason || '已按重点重排'}`;
      else hint.textContent = result.order_reason || 'AI 不可用，按课程顺序排了。';
    }
    renderTocFoot();
    renderProgress();
  } catch (error) {
    const hint = el('planHint');
    if (hint) hint.textContent = error.message;
    button.disabled = false;
    button.textContent = old;
  }
}

async function reschedulePlan(strategy) {
  if (strategy === 'drop'
    && !window.confirm('这几章本轮不再排。它们仍留在课程里，之后重排还会回来。继续？')) return;
  const hint = el('planHint');
  el('readerInner').querySelectorAll('[data-strategy]').forEach((btn) => { btn.disabled = true; });
  try {
    const result = await api('/plan/reschedule', {
      method: 'POST',
      body: JSON.stringify({ strategy }),
    });
    state.plan = result;
    renderPlan();
    const next = el('planHint');
    if (next) {
      next.textContent = strategy === 'drop'
        ? `已放弃 ${result.dropped.length} 章，其余从今天重排。`
        : `已重排，每天约 ${result.capacity} 分钟。`;
    }
  } catch (error) {
    if (hint) hint.textContent = error.message;
    el('readerInner').querySelectorAll('[data-strategy]').forEach((btn) => { btn.disabled = false; });
  }
}

async function markPlanDone(chapterId) {
  try {
    // 排期不自己记「完成」：学习记录只有一套，就是 course_progress。
    // 在排期页点完成和在章节页点学完走的是同一个接口。
    await api('/course/progress', {
      method: 'PATCH',
      body: JSON.stringify({ chapter_id: chapterId, status: 'done' }),
    });
    await loadCourse();
    renderPlan();
  } catch (error) {
    const hint = el('planHint');
    if (hint) hint.textContent = error.message;
  }
}

async function clearPlan() {
  if (!window.confirm('清空排期？学习记录不会动，只是不再给你排日子。')) return;
  try {
    await api('/plan', { method: 'DELETE' });
    state.plan = await api('/plan');
    renderPlan();
    renderTocFoot();
  } catch (error) {
    const hint = el('planHint');
    if (hint) hint.textContent = error.message;
  }
}

/* ---------------- 项目沙箱 ---------------- */

/* 章节页底部的项目卡。

   它不是「文件管理器」：只做四件事——看到有哪些项目、建一个、切到某一个、
   跑一次验收。真正的写代码在导师对话里发生（导师带 project_id 进来，
   工具就只能在这个目录里动）。

   为什么验收结果要贴在卡片里而不是弹窗：学生改一轮代码就会跑一次验收，
   弹窗关起来还得再点开；贴在行里，上一轮的失败项和这一轮的能直接对比。 */

function projectCardHtml() {
  const list = state.projects || [];
  const active = state.activeProject;
  // undefined = 还没从 /projects 拿到 checkable，别在这时候断言「没有验收脚本」
  const checkable = state.chapter ? state.chapter.check_available : undefined;
  // 验收要求来自 checks/<章>.md，写进项目 README 的同时也在这里展开一份。
  // 为什么现在才放进来：33 章都有了验收脚本之后，「这一章到底要交什么」
  // 不该只藏在 README 里——学生跑验收前就得看见，哪怕还没建项目。
  const brief = (state.chapter && state.chapter.brief) || '';
  const briefHtml = brief
    ? `<details class="proj-brief"><summary>这一章的验收要求</summary>
         <article class="prose">${renderMarkdown(brief)}</article>
       </details>`
    : '';
  const rows = list.length
    ? list.map((item) => {
      const on = active && active.id === item.id;
      const cls = item.check_ok === true ? 'ok' : (item.check_ok === false ? 'bad' : 'none');
      const detail = state.checkResult && state.checkResult.id === item.id
        ? checkDetailHtml(state.checkResult.result) : '';
      return `<div class="proj-item ${on ? 'on' : ''} ${item.exists ? '' : 'gone'}" data-project="${item.id}">
        <div class="proj-line">
          <button class="proj-name" data-open="${item.id}" title="把导师切到这个项目">${escapeHtml(item.name)}</button>
          <span class="proj-state ${cls}">${escapeHtml(item.check_label)}</span>
          <button class="proj-act" data-check="${item.id}" ${item.exists ? '' : 'disabled'}>跑验收</button>
          <button class="proj-act danger" data-remove="${item.id}" title="只从列表移除，磁盘目录保留">移除</button>
        </div>
        <div class="proj-meta">
          <code>${escapeHtml(item.shown_path)}</code>
          ${on ? '<span class="proj-on">导师已切到这个项目</span>' : ''}
          ${item.exists ? '' : '<span class="proj-gone">目录不在了</span>'}
          ${envHtml(item)}
        </div>
        ${detail}
      </div>`;
    }).join('')
    : `<p class="proj-empty">还没有项目。新建一个空目录，导师就能在里面帮你写、帮你跑——
        它看不到这个目录之外的任何文件。</p>`;

  return `<section class="proj" id="projCard">
    <div class="proj-head">
      <b>这一章的项目</b>
      <small>${checkable === undefined ? '正在确认这一章有没有验收脚本…'
        : (checkable ? '这一章有验收脚本，写完点「跑验收」会真的执行你的代码'
          : '这一章没有自动验收，靠跑起来看输出')}</small>
    </div>
    ${briefHtml}
    <div class="proj-list" id="projList">${rows}</div>
    <div class="proj-new">
      <input id="projName" maxlength="40" placeholder="给项目起个名，例如 react-parse">
      <button id="projCreate">新建项目</button>
      <span class="hint" id="projHint"></span>
    </div>
  </section>`;
}

/* 环境状态放在路径旁边，因为它解释的是「这条路径里发生的事跑在哪个 Python 上」。

   清单那一格有四种说法，对应 envs.state() 的结论：
   - 依赖已装：装的就是这份清单，不用管；
   - 更新依赖 +1：清单改过了，跑验收时会自动装（也可以现在点）；
   - 装依赖 3：有清单没记录（可能是自己在终端装的），装一次就有了记录；
   - 依赖装失败：同一份清单刚失败过，不自动重试，等人点「重试」。

   不做的两件事：不自动建环境（十几秒，且 33 章验收只用标准库），
   不隐藏按钮（需要的时候找得到，不需要的时候也看得见它是可选的）。 */
function envHtml(item) {
  if (!item || !item.exists) return '';
  const env = item.env || {};
  const req = env.requirements || {};
  const parts = [];
  // JS 项目不谈 venv：验收跑在 node 上，pip 装的东西它一个都用不到。
  // 这里换成一个只读的标注，避免他点「准备环境」白等十几秒。
  if (item.language === 'node') {
    return '<span class="proj-env on" title="项目里有 main.js，验收会用 node 跑它">JS 项目</span>';
  }
  if (env.present) {
    parts.push(`<span class="proj-env on" title="验收和 python 命令都跑在这个环境里">项目环境${env.version ? ' ' + escapeHtml(env.version) : ''}</span>`);
  } else {
    parts.push(`<button class="proj-env" data-env="${item.id}"
      title="给这个目录建独立的 .venv，首次约 10–20 秒。只用标准库的章节不需要">准备环境</button>`);
  }
  // 清单有问题时环境状态照常显示：两件事互不替代，藏掉任何一件都会让人
  // 以为「环境没了」或「清单没事」。
  if (req.error) {
    parts.push(`<span class="proj-env bad" title="${escapeHtml(req.error)}">requirements.txt 有问题</span>`);
    return parts.join('');
  }
  if (!env.present || !req.present) return parts.join('');

  if (req.state === 'failed') {
    parts.push(`<span class="proj-env bad" title="${escapeHtml((req.last_error || '').slice(0, 600))}">依赖装失败</span>`);
    parts.push(`<button class="proj-env" data-deps="${item.id}"
      title="重新按 requirements.txt 装一遍（会联网）">重试装依赖</button>`);
  } else if (req.state === 'changed') {
    parts.push(`<button class="proj-env warn" data-deps="${item.id}"
      title="清单和已装的不一样：跑验收时会自动重装；也可以现在点（会联网）">更新依赖${deltaText(req)}</button>`);
  } else if (req.state === 'unrecorded') {
    parts.push(`<button class="proj-env" data-deps="${item.id}"
      title="按 requirements.txt 装进项目环境（会联网）">装依赖${req.count ? ' ' + req.count : ''}</button>`);
  } else if (req.count) {
    parts.push(`<span class="proj-env on" title="requirements.txt 里的包都已经装进项目环境">依赖已装 ${req.count}</span>`);
  }
  return parts.join('');
}

function deltaText(req) {
  const added = (req.added || []).length;
  const removed = (req.removed || []).length;
  if (!added && !removed) return '';
  return ` +${added}${removed ? ' −' + removed : ''}`;
}

/* 验收结果：过了就一行绿的，没过逐条列出。
   失败项的 detail 是验收脚本写的「应该是什么、你写成了什么」，
   直接原样显示——改写它只会丢掉最有用的一半信息。 */
/* 验收跑在哪个解释器上。「明明装了包却说没装」时这是第一个要看的东西，
   所以放在结果标题里，不用再展开一层。

   JS 项目先说 Node：那时 venv 根本没参与验收，说「项目环境」会让人以为
   是 python 在跑。 */
function whereText(result) {
  if (result.language === 'node') {
    return ` · Node${result.runtime_version ? ' ' + result.runtime_version : ''}`;
  }
  if (result.python_source === 'venv') return ' · 项目环境';
  if (result.python_source === 'system') return ' · 默认环境';
  return '';
}

function checkDetailHtml(result) {
  if (!result) return '';
  if (result.error) {
    return `<div class="proj-check bad"><b>跑不起来</b><pre>${escapeHtml(result.error)}</pre></div>`;
  }
  // 同一个根因（比如函数返回类型就不对）会让四条检查报同一句话。
  // 那句话只印一次，后面标「同上」——否则一屏四条一样的报错，看不出其实是同一个问题。
  let lastDetail = '';
  const rows = (result.items || []).map((item) => {
    let detail = item.detail || '';
    if (detail && detail === lastDetail) detail = '同上';
    else if (detail) lastDetail = detail;
    return `<li class="${item.ok ? 'ok' : 'bad'}">
      <span>${item.ok ? '✓' : '✗'}</span>
      <span>${escapeHtml(item.name)}${detail ? `<em>${escapeHtml(detail)}</em>` : ''}</span>
    </li>`;
  }).join('');
  const head = result.ok
    ? `验收通过 ${result.passed}/${result.total}（${Math.round((result.elapsed_ms || 0) / 100) / 10}s）${whereText(result)}`
    : `验收没过 ${result.passed}/${result.total}${whereText(result)}`;
  return `<div class="proj-check ${result.ok ? 'ok' : 'bad'}">
    <b>${escapeHtml(head)}</b>${depsNoteHtml(result.deps)}<ul>${rows}</ul>
    ${result.stdout && !result.ok ? `<details><summary>你的程序输出</summary><pre>${escapeHtml(result.stdout)}</pre></details>` : ''}
  </div>`;
}

/* 验收前有没有因为「清单改过」而重装。学生不关心 pip 的日志，只关心两件事：
   装上了没有、没装上会不会影响这份结果。所以成功只说一句。

   断网要单独说一句：这时「跑验收」本身是好的，只是缺包，不该让他以为代码坏了，
   更不该让他去改包名（pip 在断网和包名写错时给的是同一句 ERROR）。 */
function depsNoteHtml(deps) {
  if (!deps || !deps.changed) return '';
  const secs = Math.round((deps.elapsed_ms || 0) / 100) / 10;
  if (deps.installed) {
    const names = (deps.packages || []).join('、');
    return `<div class="proj-deps ok">清单改过了，验收前已自动重装${names ? '：' + escapeHtml(names) : ''}（${secs}s）</div>`;
  }
  const reason = deps.error || '原因不明';
  const offline = deps.kind === 'offline';
  const head = offline
    ? '清单改过了，但装依赖要联网、现在连不上——下面的结果可能缺包。'
    : (deps.kind === 'missing'
      ? '清单改过了，但 PyPI 上找不到这个包（名字可能拼错了）——下面的结果可能缺包。'
      : '清单改过了，但自动重装失败——下面的结果可能缺包。');
  return `<div class="proj-deps bad" title="${escapeHtml(reason)}">${head}</div>
    <details class="proj-deps-more"><summary>装依赖为什么失败</summary><pre>${escapeHtml(reason)}</pre></details>`;
}

/* 记住「这一章上次在用哪个项目」。

   不记的话，刷新一次导师就静默失去文件能力：用户接着问「改一下 main.py」，
   导师会说它看不到文件，而页面上项目卡片明明还在。按章分开存，
   换章不会串。 */
function rememberProject(chapterId, id) {
  const key = `zh_w_proj_${chapterId || ''}`;
  if (id) sessionStorage.setItem(key, String(id));
  else sessionStorage.removeItem(key);
}

function restoreProject(chapterId) {
  const key = `zh_w_proj_${chapterId || ''}`;
  const saved = Number(sessionStorage.getItem(key) || 0);
  if (!saved) return null;
  const hit = (state.projects || []).find((item) => item.id === saved);
  if (!hit || !hit.exists) {
    // 目录被删了：把记忆清掉，别每次进这一章都试一次
    if (!hit) sessionStorage.removeItem(key);
    return null;
  }
  return hit;
}

function activeProjectId() {
  const project = state.activeProject;
  if (!project) return 0;
  // 切了章但没清 activeProject 时不要带上去：接口会 400，用户看到的是
  // 「这个项目属于第 X 章」，而他只是换了章。
  const chapter = state.chapter && state.chapter.chapter;
  if (chapter && project.chapter_id && project.chapter_id !== chapter.id) return 0;
  return project.id;
}

async function loadProjects(chapterId) {
  const payload = await api(`/projects?chapter_id=${encodeURIComponent(chapterId)}`);
  state.projects = payload.items || [];
  state.chapter.check_available = (payload.checkable || []).includes(chapterId);
  state.chapter.brief = payload.brief || '';
  // 之前切到的项目如果已经不在这一章了，就清掉，免得导师继续对着它说话
  if (state.activeProject && state.activeProject.chapter_id !== chapterId) {
    state.activeProject = null;
    state.checkResult = null;
  }
  if (!state.activeProject) state.activeProject = restoreProject(chapterId);
  const card = el('projCard');
  if (card) card.outerHTML = projectCardHtml();
  bindProjectCard();
  renderTutorHead();
}

function bindProjectCard() {
  const card = el('projCard');
  if (!card) return;
  card.querySelectorAll('[data-open]').forEach((btn) => {
    btn.addEventListener('click', () => selectProject(Number(btn.dataset.open)));
  });
  card.querySelectorAll('[data-check]').forEach((btn) => {
    btn.addEventListener('click', () => runProjectCheck(Number(btn.dataset.check)));
  });
  card.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => removeProject(Number(btn.dataset.remove)));
  });
  card.querySelectorAll('[data-env]').forEach((btn) => {
    btn.addEventListener('click', () => prepareProjectEnv(Number(btn.dataset.env)));
  });
  card.querySelectorAll('[data-deps]').forEach((btn) => {
    btn.addEventListener('click', () => installProjectDeps(Number(btn.dataset.deps)));
  });
  // 重画列表时按钮节点还在，直接再 bind 会叠加监听器：点一次建出两个项目。
  // 用 cloneNode 换一个干净节点，比记「绑过没有」可靠。
  const oldCreate = el('projCreate');
  if (oldCreate) {
    const create = oldCreate.cloneNode(true);
    oldCreate.replaceWith(create);
    create.addEventListener('click', createProject);
  }
  const oldName = el('projName');
  if (oldName) {
    const name = oldName.cloneNode(true);
    oldName.replaceWith(name);
    name.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); createProject(); }
    });
  }
}

function selectProject(id) {
  const project = (state.projects || []).find((item) => item.id === id);
  if (!project) return;
  if (!project.exists) {
    el('projHint').textContent = '这个目录不在了，先新建一个吧。';
    return;
  }
  // 再点一次取消：切回「不挂项目」的纯答疑，不需要另做一个按钮
  state.activeProject = state.activeProject && state.activeProject.id === id ? null : project;
  state.checkResult = null;
  rememberProject(state.chapterId, state.activeProject ? state.activeProject.id : 0);
  renderProjectList();
  renderTutorHead();
  el('projHint').textContent = state.activeProject
    ? '导师现在只在这个目录里工作。问它「下一步该写什么」就能开始。' : '已取消项目，导师回到纯答疑。';
}

function renderProjectList() {
  const host = el('projList');
  if (!host) return;
  // 只重画列表，不动输入框：整块替换会把用户正在输入的项目名清掉
  const scratch = document.createElement('div');
  scratch.innerHTML = projectCardHtml();
  host.innerHTML = scratch.querySelector('#projList').innerHTML;
  bindProjectCard();
}

async function createProject() {
  const input = el('projName');
  const name = (input && input.value || '').trim();
  const hint = el('projHint');
  if (!name) {
    hint.textContent = '先起个名字，目录名会从它生成。';
    return;
  }
  const button = el('projCreate');
  button.disabled = true;
  try {
    const created = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({
        name,
        chapter_id: state.chapterId,
        course_id: (state.course && state.course.id) || 'agent-dev',
      }),
    });
    if (input) input.value = '';
    state.projects = [created, ...(state.projects || [])];
    // 新建就切过去：他要的是「马上开始写」，再点一下「打开」是多余的一步
    state.activeProject = created;
    state.checkResult = null;
    rememberProject(state.chapterId, created.id);
    renderProjectList();
    renderTutorHead();
    hint.textContent = `建好了：${created.shown_path}`;
  } catch (error) {
    hint.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function runProjectCheck(id) {
  const hint = el('projHint');
  const row = (state.projects || []).find((item) => item.id === id);
  const deps = row && row.env && row.env.requirements;
  // 清单改过时先说要等：重装要联网，可能几十秒；不提前讲，
  // 学生会以为页面卡死了，然后在装到一半时刷新。
  hint.textContent = deps && deps.dirty
    ? `清单改过了，先重装依赖再验收（会联网，可能一两分钟）…`
    : (row ? `正在跑「${row.name}」的验收…` : '正在跑验收…');
  try {
    const result = await api(`/projects/${id}/check`, { method: 'POST' });
    state.checkResult = { id, result };
    // 服务端把结果写回了项目行，直接用返回的那份，别自己拼
    if (result.project) {
      state.projects = (state.projects || []).map(
        (item) => (item.id === id ? { ...item, ...result.project } : item));
    }
    renderProjectList();
    hint.textContent = result.ok ? '验收通过。' : '没过，下面写了哪一条不满足。';
  } catch (error) {
    state.checkResult = { id, result: { error: error.message } };
    renderProjectList();
    hint.textContent = '';
  }
}

/* 准备环境与装依赖都要等十几秒到一两分钟，所以在卡片底部那行就地反馈：
   学生会一直盯着这一行，弹窗反而多一步关闭。 */
async function prepareProjectEnv(id) {
  const hint = el('projHint');
  const row = (state.projects || []).find((item) => item.id === id);
  if (!row || !row.exists) {
    hint.textContent = '这个目录不在了，先新建一个项目。';
    return;
  }
  hint.textContent = `正在给「${row.name}」建独立环境（首次约 10–20 秒，别关页面）…`;
  try {
    const payload = await api(`/projects/${id}/env`, { method: 'POST' });
    replaceProject(payload.project);
    hint.textContent = payload.created
      ? `环境好了${payload.version ? '：Python ' + payload.version : ''}，之后验收和 python 命令都跑在它里面。`
      : '这个项目已经有环境了。';
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function installProjectDeps(id) {
  const hint = el('projHint');
  const row = (state.projects || []).find((item) => item.id === id);
  hint.textContent = '正在装依赖（要联网，可能一两分钟）…';
  try {
    const payload = await api(`/projects/${id}/deps`, { method: 'POST' });
    replaceProject(payload.project);
    const names = (payload.packages || []).join('、');
    hint.textContent = names ? `装好了：${names}` : '装好了。';
  } catch (error) {
    hint.textContent = error.message;
  }
}

/* 服务端返回的项目行是唯一事实来源（含 env 与 check 状态），别在本地拼。 */
function replaceProject(project) {
  if (!project) return;
  state.projects = (state.projects || []).map((item) => (item.id === project.id ? project : item));
  if (state.activeProject && state.activeProject.id === project.id) state.activeProject = project;
  renderProjectList();
  renderTutorHead();
}

async function removeProject(id) {
  const row = (state.projects || []).find((item) => item.id === id);
  if (!row) return;
  // 说清「只移除登记」再动手：这个按钮旁边就是项目名，点错了人会以为文件没了
  if (!window.confirm(`从列表移除「${row.name}」？\n\n磁盘上的目录会保留：${row.shown_path}`)) return;
  try {
    await api(`/projects/${id}`, { method: 'DELETE' });
    state.projects = (state.projects || []).filter((item) => item.id !== id);
    if (state.activeProject && state.activeProject.id === id) {
      state.activeProject = null;
      rememberProject(state.chapterId, 0);
    }
    if (state.checkResult && state.checkResult.id === id) state.checkResult = null;
    renderProjectList();
    renderTutorHead();
    el('projHint').textContent = '已从列表移除，目录还在。';
  } catch (error) {
    el('projHint').textContent = error.message;
  }
}

/* ---------------- 导师 ---------------- */

function renderTutorHead() {
  const c = state.chapter && state.chapter.chapter;
  const where = c ? `正在看第 ${c.no} 章 · ${c.title}` : '未选择章节';
  // 带上项目名：用户得知道自己问的这句话，导师是在哪个目录里动手的
  const project = state.activeProject;
  el('tutorWhere').textContent = project ? `${where} · 项目 ${project.name}` : where;
  el('tutor').classList.toggle('with-project', Boolean(project));

  const quick = [
    { label: '这一章到底在讲什么', text: '用三句话说清这一章到底想让我记住什么。不要复述正文。' },
    { label: '练习卡住了', text: '这章的练习我跑不出来，报错是：' },
    { label: '我学完了，检查一下', text: '这章我学完了，你看我算不算真的学会了。' },
  ];
  el('tutorQuick').innerHTML = quick
    .map((q) => `<button class="quick-btn" data-ask="${escapeHtml(q.text)}">${escapeHtml(q.label)}</button>`)
    .join('');
  el('tutorQuick').querySelectorAll('[data-ask]').forEach((btn) => {
    btn.addEventListener('click', () => {
      el('tutorInput').value = btn.dataset.ask;
      el('tutorInput').focus();
    });
  });
}

async function ensureChat(chapterId, chapter) {
  // 同一章不重建：重建会把已经聊过的内容清掉
  if (state.chatChapter === chapterId && state.chatId) return;
  state.chatChapter = chapterId;

  const key = `zh_w_chat_${chapterId}`;
  let chatId = Number(sessionStorage.getItem(key) || 0);
  if (!chatId) {
    try {
      const created = await api(
        `/create_chat?title=${encodeURIComponent(`第 ${chapter.no} 章 ${chapter.title}`)}`,
        { method: 'POST' }
      );
      chatId = created.id;
      sessionStorage.setItem(key, String(chatId));
    } catch (e) {
      chatId = 0;
    }
  }
  state.chatId = chatId;

  // 空白区域放「这一章的验收标准」，比放一句欢迎语有用：
  // 他看到这三个动作，就知道自己该做到什么程度才算学完。
  const goals = (chapter.objectives || [])
    .map((o) => `<li>${escapeHtml(o)}</li>`).join('');
  el('tutorLog').innerHTML = `<div class="tutor-hint">
    <p>这里问的是<b>第 ${chapter.no} 章</b>。练习卡住直接贴报错，它会先问你做到哪一步，而不是把答案丢给你。</p>
    ${goals ? `<p class="hint-goal">学完应该能做到</p><ul class="hint-goals">${goals}</ul>` : ''}
  </div>`;
}

function appendMessage(role, html) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<span class="msg-role">${role === 'user' ? '你' : '导师'}</span>
    <div class="bubble">${html}</div>`;
  el('tutorLog').appendChild(wrap);
  el('tutorLog').scrollTop = el('tutorLog').scrollHeight;
  return wrap.querySelector('.bubble');
}

async function askTutor(question) {
  if (state.sending || !question.trim()) return;
  if (!state.chapterId) {
    appendMessage('ai', '先从左边选一章，我才知道该按哪一章的内容讲。');
    return;
  }
  const text = question.trim();
  el('tutorInput').value = '';
  appendMessage('user', escapeHtml(text));

  state.sending = true;
  el('tutorSend').disabled = true;
  el('tutorQuick').querySelectorAll('button').forEach((b) => { b.disabled = true; });

  const bubble = appendMessage('ai', '<span class="typing"><i></i><i></i><i></i></span>');
  let answer = '';

  try {
    const response = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        chat_id: state.chatId || 0,
        mode: 'tutor',
        course_id: state.course.id || 'agent-dev',
        chapter_id: state.chapterId,
        // 只在「当前章的项目」上带：接口会校验项目归属哪一章，
        // 但前端也别指望它兜底——带错了会被 400，用户只看到一句莫名其妙的错。
        project_id: activeProjectId(),
      }),
    });
    if (response.status === 401) { location.href = '/'; return; }
    if (!response.ok || !response.body) {
      let detail = '导师暂时连不上';
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (e) { /* 保持默认文案 */ }
      bubble.textContent = detail;
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 按空行切帧，最后一帧可能不完整，留在 buffer 里等下一块
      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch (e) {
          continue;
        }
        if (event.type === 'token') {
          answer += event.text || '';
          bubble.innerHTML = renderMarkdown(answer);
          el('tutorLog').scrollTop = el('tutorLog').scrollHeight;
        } else if (event.type === 'error') {
          bubble.textContent = event.message || '导师出错了，重试一次。';
        } else if (event.type === 'done' && event.reply) {
          answer = event.reply;
          bubble.innerHTML = renderMarkdown(answer);
        }
      }
    }
    if (!answer.trim()) bubble.textContent = '没有拿到回答，重试一次。';
  } catch (error) {
    bubble.textContent = `连不上后端：${error.message}`;
  } finally {
    state.sending = false;
    el('tutorSend').disabled = false;
    el('tutorQuick').querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

/* ---------------- 抽屉（窄屏） ---------------- */

function closeDrawers() {
  el('toc').classList.remove('open');
  el('tutor').classList.remove('open');
  el('mask').hidden = true;
}

function bindDrawers() {
  const openToc = () => {
    el('toc').classList.add('open');
    el('mask').hidden = false;
  };
  const openTutor = () => {
    el('tutor').classList.add('open');
    el('mask').hidden = false;
  };
  el('tocToggle').addEventListener('click', openToc);
  el('tutorToggle').addEventListener('click', openTutor);
  el('tocClose').addEventListener('click', closeDrawers);
  el('tutorClose').addEventListener('click', closeDrawers);
  el('mask').addEventListener('click', closeDrawers);
}

/* ---------------- 启动 ---------------- */

async function boot() {
  try {
    const status = await fetch('/auth/status').then((r) => r.json());
    // 带上回来的地址，登录成功后直接跳回学习台，不用他再去首页找入口
    if (!status.authenticated) {
      location.href = '/?next=' + encodeURIComponent('/learn.html');
      return;
    }
  } catch (e) {
    el('boot').textContent = '连不上后端，确认服务已启动。';
    return;
  }

  el('shell').hidden = false;
  el('boot').hidden = true;
  bindDrawers();

  el('tutorForm').addEventListener('submit', (event) => {
    event.preventDefault();
    askTutor(el('tutorInput').value);
  });
  el('tutorInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      askTutor(el('tutorInput').value);
    }
  });
  // 输入框跟着内容长高，最多 120px
  el('tutorInput').addEventListener('input', () => {
    const node = el('tutorInput');
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 120)}px`;
  });

  try {
    await loadCourse();
  } catch (error) {
    el('boot').textContent = `课程加载失败：${error.message}`;
    el('boot').hidden = false;
    return;
  }

  // 地址栏带着章节就打开它；否则从推荐的那一章开始，而不是第 1 章——
  // 用户学到一半回来，应该接着上次的位置
  const fromHash = (location.hash || '').replace('#', '');
  if (fromHash === 'diagnostic') {
    openDiagnostic();
    return;
  }
  if (fromHash === 'profile') {
    openProfile();
    return;
  }
  if (fromHash === 'plan') {
    openPlan();
    return;
  }
  const known = (state.course.chapters || []).some((c) => c.id === fromHash);
  const target = known ? fromHash : (state.course.next && state.course.next.id);
  if (target) openChapter(target);
  else el('readerInner').innerHTML = '<div class="empty-state"><p>这门课还没有章节。</p></div>';

  // 浏览器的前进/后退、以及把 learn.html#s7c2 粘到已打开的页面上，都只会改 hash，
  // 不重新加载页面。不监听的话这两种操作看起来就是「没反应」。
  // 两个 self 判断是防回环：openChapter 自己也会写 hash，写了又触发这里。
  window.addEventListener('hashchange', () => {
    const want = (location.hash || '').replace('#', '');
    if (want === 'diagnostic') {
      if (state.view !== 'diagnostic') openDiagnostic();
      return;
    }
    if (want === 'profile') {
      if (state.view !== 'profile') openProfile();
      return;
    }
    if (want === 'plan') {
      if (state.view !== 'plan') openPlan();
      return;
    }
    if (want && want !== state.chapterId) openChapter(want);
  });
}

boot();
