/* Agent 开发学习台。
   三栏：目录 / 正文 / 导师。正文是文件里的 Markdown，导师走 /chat/stream 的 tutor 模式。
   这里不做任何内容生成——目录和正文都来自后端读文件，界面只负责渲染。 */

const state = {
  course: null,
  chapterId: '',
  chapter: null,
  chatId: null,
  sending: false,
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
  renderProgress();
  renderToc();
  renderNextCard();
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
      ].filter(Boolean).join(' ');
      return `<button class="${cls}" data-chapter="${c.id}">
        <span class="chap-no">${c.no}</span>
        <span class="chap-title">${escapeHtml(c.title)}</span>
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

function renderNextCard() {
  const next = state.course.next;
  const foot = el('tocFoot');
  if (!next) {
    foot.innerHTML = `<div class="next-card all-done">
      <small>主干已学完</small>
      <b>可以去做项目了</b>
    </div>`;
    return;
  }
  foot.innerHTML = `<div class="next-card">
    <small>按依赖顺序 · 下一步</small>
    <b>第 ${next.no} 章 ${escapeHtml(next.title)}</b>
    <button id="nextGo">去这一章</button>
  </div>`;
  el('nextGo').addEventListener('click', () => openChapter(next.id));
}

/* ---------------- 章节正文 ---------------- */

async function openChapter(chapterId) {
  if (!chapterId) return;
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

function renderChapter(payload) {
  const c = payload.chapter;
  const blocked = payload.blocked_by || [];
  const gateHtml = blocked.length
    ? `<div class="gate">这一章建议先学完：
        ${blocked.map((b) => `第 ${b.no} 章《${escapeHtml(b.title)}》`).join('、')}。
        现在读也能读，但后面几步会用到它们。</div>`
    : '';

  const done = c.status === 'done';

  el('readerInner').innerHTML = `
    <div class="reader-top">
      <span class="stage-tag">${escapeHtml(c.stage_title || '')}</span>
      <span>第 ${payload.index} / ${payload.total} 章</span>
      <span>·</span>
      <span>预计 ${c.minutes || 40} 分钟</span>
    </div>
    ${gateHtml}
    <article class="prose">${renderMarkdown(payload.markdown)}</article>
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
        mastery: target === 'done' ? 2 : 0,
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
    renderNextCard();
    el('doneHint').textContent = target === 'done' ? '已记录' : '已撤销';
  } catch (error) {
    button.disabled = false;
    el('doneHint').textContent = error.message;
  }
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
    if (!status.authenticated) {
      location.href = '/';
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
  const known = (state.course.chapters || []).some((c) => c.id === fromHash);
  const target = known ? fromHash : (state.course.next && state.course.next.id);
  if (target) openChapter(target);
  else el('readerInner').innerHTML = '<div class="empty-state"><p>这门课还没有章节。</p></div>';
}

boot();
