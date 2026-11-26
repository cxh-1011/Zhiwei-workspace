/* Agent 开发学习台。
   三栏：目录 / 正文 / 导师。正文是文件里的 Markdown，导师走 /chat/stream 的 tutor 模式。
   这里不做任何内容生成——目录和正文都来自后端读文件，界面只负责渲染。 */

const state = {
  course: null,
  chapterId: '',
  chapter: null,
  chatId: null,
  sending: false,
  // 'chapter' | 'diagnostic'：诊断页没有章节，靠这个区分，别用 chapterId 空着来判断
  view: 'chapter',
  diagInfo: null,
  diagResult: null,
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
  return `<div class="diag-card">
    <small>${escapeHtml(sub)}</small>
    <b>${escapeHtml(text)}</b>
    <button id="diagGo">${last ? '重新测一次' : '开始摸底'}</button>
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
  return `<div class="next-card">
    <small>按依赖顺序 · 下一步</small>
    <b>第 ${next.no} 章 ${escapeHtml(next.title)}</b>
    <button id="nextGo">去这一章</button>
  </div>`;
}

function renderTocFoot() {
  el('tocFoot').innerHTML = diagnosticCardHtml() + nextCardHtml();
  const diagGo = el('diagGo');
  if (diagGo) diagGo.addEventListener('click', openDiagnostic);
  const nextGo = el('nextGo');
  if (nextGo) nextGo.addEventListener('click', () => state.course.next && openChapter(state.course.next.id));
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
    renderTocFoot();
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
  el('diagBack').addEventListener('click', () => {
    const next = state.course.next;
    if (next) openChapter(next.id);
    else openChapter((state.course.chapters[0] || {}).id);
  });
}

/* ---------------- 导师 ---------------- */

function renderTutorHead() {
  const c = state.chapter && state.chapter.chapter;
  el('tutorWhere').textContent = c
    ? `正在看第 ${c.no} 章 · ${c.title}`
    : '未选择章节';

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
    if (want && want !== state.chapterId) openChapter(want);
  });
}

boot();
