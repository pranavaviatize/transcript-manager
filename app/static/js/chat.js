(function () {
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const thread = document.getElementById('chatThread');
  const chat = document.getElementById('chat');
  const sendBtn = form ? form.querySelector('.chat-send') : null;
  const suggestions = document.getElementById('chatSuggestions');
  const historyPanel = document.getElementById('chatHistory');
  const historyList = historyPanel ? (historyPanel.querySelector('.chat-history-list') || historyPanel) : null;
  const historyToggle = document.getElementById('chatHistoryToggle');
  const newChatBtn = document.getElementById('chatNew');
  if (!form || !input || !thread) return;

  let history = [];            // [{role, content}] for the active conversation
  let currentSessionId = null; // persisted conversation id (null = unsaved/new)
  let busy = false;

  // ----- small DOM helpers -------------------------------------------------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
  function setEmpty(on) { if (chat) chat.classList.toggle('is-empty', on); }
  function updateSend() { if (sendBtn) sendBtn.disabled = busy || !input.value.trim(); }

  // ----- markdown (escape-first, XSS-safe) ---------------------------------
  function renderInline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^_]+)__/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\(([^)\s"']+)\)/g, function (m, t, url) {
        return /^(https?:\/\/|\/)/.test(url)
          ? '<a href="' + url + '" target="_blank" rel="noopener">' + t + '</a>'
          : t;
      })
      .replace(/N°\s*0*(\d+)/g, '<a class="cite" href="/transcripts/$1">N°$1</a>');
  }
  function renderMarkdown(src) {
    let text = escapeHtml(src);
    const blocks = [];
    text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, function (m, code) {
      blocks.push(code.replace(/\n+$/, ''));
      return ' ' + (blocks.length - 1) + ' ';
    });
    const lines = text.split('\n');
    let out = '';
    let list = null;
    let para = [];
    const flushPara = function () { if (para.length) { out += '<p>' + para.join('<br>') + '</p>'; para = []; } };
    const closeList = function () { if (list) { out += '</' + list + '>'; list = null; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/\s+$/, '');
      let m = line.match(/^ (\d+) $/);
      if (m) { flushPara(); closeList(); out += '<pre><code>' + blocks[+m[1]] + '</code></pre>'; continue; }
      if (!line.trim()) { flushPara(); closeList(); continue; }
      if ((m = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/))) {
        flushPara(); closeList();
        const lvl = Math.min(6, m[1].length + 2);
        out += '<h' + lvl + '>' + renderInline(m[2]) + '</h' + lvl + '>';
      } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
        flushPara();
        if (list !== 'ul') { closeList(); out += '<ul>'; list = 'ul'; }
        out += '<li>' + renderInline(m[1]) + '</li>';
      } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
        flushPara();
        if (list !== 'ol') { closeList(); out += '<ol>'; list = 'ol'; }
        out += '<li>' + renderInline(m[1]) + '</li>';
      } else {
        closeList();
        para.push(renderInline(line));
      }
    }
    flushPara(); closeList();
    return out;
  }
  function renderAnswer(bubble, text) {
    bubble.innerHTML = renderMarkdown(text);
  }

  // ----- thread rendering --------------------------------------------------
  function clearEmpty() {
    const e = thread.querySelector('.chat-empty');
    if (e) e.remove();
  }
  function showEmpty() {
    thread.innerHTML =
      '<div class="chat-empty">' +
      '<div class="empty-mark" aria-hidden="true">✻</div>' +
      '<h2 class="chat-empty-title">Ask anything about your meetings</h2>' +
      '<p class="chat-empty-sub">Decisions, action items, who-said-what — answered from your transcripts, ' +
      'with a link back to every source.</p>' +
      '</div>';
    setEmpty(true);
  }
  function addMessage(role, text) {
    clearEmpty();
    setEmpty(false);
    const wrap = el('div', 'chat-msg chat-msg-' + role);
    if (role === 'assistant') {
      const av = el('div', 'chat-avatar', '✻');
      av.setAttribute('aria-hidden', 'true');
      wrap.appendChild(av);
    }
    const bubble = el('div', 'chat-bubble');
    bubble.textContent = text || '';
    wrap.appendChild(bubble);
    thread.appendChild(wrap);
    thread.scrollTop = thread.scrollHeight;
    return bubble;
  }
  function addSources(sources) {
    if (!sources || !sources.length) return;
    const box = el('div', 'chat-sources');
    box.appendChild(el('span', 'chat-sources-label', 'Sources'));
    sources.forEach(function (s) {
      const id3 = String(s.id).padStart(3, '0');
      const a = el('a', 'chip chat-source', 'N°' + id3 + ' · ' + s.title);
      a.href = '/transcripts/' + s.id;
      box.appendChild(a);
    });
    thread.appendChild(box);
    thread.scrollTop = thread.scrollHeight;
  }

  // ----- SSE parsing -------------------------------------------------------
  function drainEvents(buffer) {
    const parts = buffer.split('\n\n');
    const remaining = parts.pop();
    const events = parts.map(function (raw) {
      let name = 'message';
      const dataLines = [];
      raw.split('\n').forEach(function (line) {
        if (line.indexOf('event:') === 0) name = line.slice(6).trim();
        else if (line.indexOf('data: ') === 0) dataLines.push(line.slice(6));
      });
      return { name: name, data: dataLines.join('\n') };
    });
    return [remaining, events];
  }

  // ----- ask ---------------------------------------------------------------
  async function ask(question) {
    busy = true;
    updateSend();
    addMessage('user', question);
    const bubble = addMessage('assistant', '');
    bubble.classList.add('is-streaming');

    let answer = '';
    let sources = null;
    try {
      const resp = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question, history: history, session_id: currentSessionId }),
      });
      if (!resp.ok || !resp.body) throw new Error('bad response');

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let events;
        [buffer, events] = drainEvents(buffer);
        for (const evt of events) {
          if (!evt.data || evt.data === '[DONE]') continue;
          if (evt.name === 'meta') {
            try {
              const meta = JSON.parse(evt.data);
              if (meta.session_id) { currentSessionId = meta.session_id; loadHistory(); }
            } catch (e) {}
            continue;
          }
          if (evt.name === 'sources') {
            try { sources = JSON.parse(evt.data).sources; } catch (e) {}
            continue;
          }
          try {
            const obj = JSON.parse(evt.data);
            if (obj.t) {
              answer += obj.t;
              renderAnswer(bubble, answer);
              thread.scrollTop = thread.scrollHeight;
            }
          } catch (e) {}
        }
      }

      bubble.classList.remove('is-streaming');
      if (!answer) renderAnswer(bubble, 'No answer.');
      history.push({ role: 'user', content: question });
      history.push({ role: 'assistant', content: answer });
      addSources(sources);
    } catch (e) {
      bubble.classList.remove('is-streaming');
      bubble.textContent = 'Something went wrong. Please try again.';
      if (window.showToast) showToast('Chat request failed', 'error');
    } finally {
      busy = false;
      updateSend();
      input.focus();
    }
  }

  // ----- history -----------------------------------------------------------
  function relTime(iso) {
    if (!iso) return '';
    const then = new Date(iso + (iso.indexOf('Z') < 0 && iso.indexOf('+') < 0 ? 'Z' : ''));
    const secs = Math.max(0, (Date.now() - then.getTime()) / 1000);
    if (secs < 60) return 'just now';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
    return Math.floor(secs / 86400) + 'd ago';
  }

  function renderHistory(sessions) {
    if (!historyList) return;
    historyList.innerHTML = '';
    if (!sessions || !sessions.length) {
      historyList.appendChild(el('div', 'chat-history-empty muted', 'No saved conversations yet.'));
      return;
    }
    sessions.forEach(function (s) {
      const row = el('div', 'chat-history-item' + (s.id === currentSessionId ? ' is-active' : ''));
      const main = el('button', 'chat-history-open');
      main.type = 'button';
      main.appendChild(el('span', 'chat-history-title', s.title || 'Untitled'));
      main.appendChild(el('span', 'chat-history-time', relTime(s.updated_at)));
      main.addEventListener('click', function () { openSession(s.id); });
      const del = el('button', 'chat-history-del', '×');
      del.type = 'button';
      del.title = 'Delete conversation';
      del.addEventListener('click', function (e) { e.stopPropagation(); deleteSession(s.id); });
      row.appendChild(main);
      row.appendChild(del);
      historyList.appendChild(row);
    });
  }

  async function loadHistory() {
    try {
      const resp = await fetch('/chat/sessions');
      if (!resp.ok) return;
      renderHistory(await resp.json());
    } catch (e) {}
  }

  function closeHistory() {
    if (!historyPanel) return;
    historyPanel.hidden = true;
    if (historyToggle) historyToggle.setAttribute('aria-expanded', 'false');
  }

  async function openSession(id) {
    try {
      const resp = await fetch('/chat/sessions/' + id);
      if (!resp.ok) throw new Error('not found');
      const data = await resp.json();
      currentSessionId = data.id;
      history = [];
      thread.innerHTML = '';
      setEmpty(false);
      (data.messages || []).forEach(function (m) {
        if (m.role === 'assistant') {
          const bubble = addMessage('assistant', '');
          renderAnswer(bubble, m.content);
          addSources(m.sources);
        } else {
          addMessage('user', m.content);
        }
        history.push({ role: m.role, content: m.content });
      });
      closeHistory();
      loadHistory(); // refresh active highlight
      input.focus();
    } catch (e) {
      if (window.showToast) showToast('Could not load that conversation', 'error');
    }
  }

  async function deleteSession(id) {
    try {
      await fetch('/chat/sessions/' + id, { method: 'DELETE' });
    } catch (e) {}
    if (id === currentSessionId) newChat();
    loadHistory();
  }

  function newChat() {
    currentSessionId = null;
    history = [];
    showEmpty();
    input.value = '';
    input.style.height = 'auto';
    updateSend();
    if (historyPanel) historyPanel.querySelectorAll('.is-active').forEach(function (n) { n.classList.remove('is-active'); });
    closeHistory();
    input.focus();
  }

  // ----- wiring ------------------------------------------------------------
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) return;
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    input.style.height = 'auto';
    updateSend();
    ask(q);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  input.addEventListener('input', function () {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    updateSend();
  });

  // Clickable example prompts → fill and send.
  if (suggestions) {
    suggestions.addEventListener('click', function (e) {
      const btn = e.target.closest('.chat-suggest');
      if (!btn || busy) return;
      const q = btn.textContent.trim();
      input.value = '';
      input.style.height = 'auto';
      updateSend();
      ask(q);
    });
  }

  if (newChatBtn) newChatBtn.addEventListener('click', newChat);
  if (historyToggle) {
    historyToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      const show = historyPanel.hidden;
      historyPanel.hidden = !show;
      historyToggle.setAttribute('aria-expanded', String(show));
      if (show) loadHistory();
    });
  }
  // Dismiss the history popover on outside click / Escape.
  document.addEventListener('click', function (e) {
    if (!historyPanel || historyPanel.hidden) return;
    const wrap = historyPanel.closest('.chat-history-wrap');
    if (wrap && !wrap.contains(e.target)) closeHistory();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && historyPanel && !historyPanel.hidden) closeHistory();
  });

  updateSend();
  loadHistory();
  // Land in the composer so you can start typing immediately — no click needed.
  input.focus();
})();
