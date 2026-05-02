// Per-client AI chat — slice 1 (IC photo → testator).
// Talks to /api/chat/<client_id>/{history,message,apply,reject}.

(function () {
  const CLIENT_ID = window.CHAT_CLIENT_ID;
  if (!CLIENT_ID) {
    console.error('CHAT_CLIENT_ID missing');
    return;
  }

  const thread = document.getElementById('chat-thread');
  const empty = document.getElementById('chat-empty');
  const textInput = document.getElementById('chat-text');
  const sendBtn = document.getElementById('chat-send');
  const fileInput = document.getElementById('chat-files');
  const filePills = document.getElementById('file-pills');
  const willPane = document.getElementById('will-snapshot');
  const willStatus = document.getElementById('will-status');
  const dragOverlay = document.getElementById('drag-overlay');

  let pendingFiles = [];

  // ── Helpers ──────────────────────────────────────────────────────────
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Tiny inline markdown: **bold** and [text](url). Run AFTER escapeHtml.
  function inlineMd(s) {
    return escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" class="text-primary-600 underline" target="_blank" rel="noopener">$1</a>')
      .replace(/\n/g, '<br>');
  }

  function fmtKB(bytes) {
    if (!bytes && bytes !== 0) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  function categoryLabel(cat) {
    const map = {
      nric: 'IC / Passport', property_title: 'Property Title',
      property_tax: 'Property Tax', bank_statement: 'Bank Statement',
      insurance: 'Insurance', epf_kwsp: 'EPF / KWSP',
      vehicle: 'Vehicle', will: 'Existing Will',
      chat_inbox: 'Pending', other: 'Unclassified',
    };
    return map[cat] || cat || 'File';
  }

  // ── File pill handling ───────────────────────────────────────────────
  fileInput.addEventListener('change', (e) => {
    addFiles(Array.from(e.target.files));
    fileInput.value = '';
  });

  function addFiles(files) {
    for (const f of files) {
      if (f.size > 20 * 1024 * 1024) {
        alert(`${f.name} is larger than 20MB.`);
        continue;
      }
      pendingFiles.push(f);
    }
    renderFilePills();
  }

  function renderFilePills() {
    if (!pendingFiles.length) {
      filePills.classList.add('hidden');
      filePills.innerHTML = '';
      return;
    }
    filePills.classList.remove('hidden');
    filePills.innerHTML = pendingFiles.map((f, i) => `
      <span class="inline-flex items-center gap-1 px-2 py-1 bg-primary-50 text-primary-700 text-xs rounded-full">
        ${escapeHtml(f.name)} <span class="text-[10px] text-primary-400">(${fmtKB(f.size)})</span>
        <button type="button" onclick="window.__removePendingFile(${i})" class="text-primary-400 hover:text-red-500 ml-0.5">&times;</button>
      </span>
    `).join('');
  }

  window.__removePendingFile = function (i) {
    pendingFiles.splice(i, 1);
    renderFilePills();
  };

  // ── Drag & drop into the thread area ─────────────────────────────────
  let dragDepth = 0;
  document.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
    dragDepth++;
    dragOverlay.classList.remove('hidden');
  });
  document.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dragOverlay.classList.add('hidden');
  });
  document.addEventListener('dragover', (e) => {
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) e.preventDefault();
  });
  document.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) {
      e.preventDefault();
      addFiles(Array.from(e.dataTransfer.files));
    }
    dragDepth = 0;
    dragOverlay.classList.add('hidden');
  });

  // ── Voice recording (MediaRecorder → blob → pendingFiles) ────────────
  let mediaRecorder = null;
  let recordedChunks = [];
  let recordTimer = null;
  let recordSecs = 0;
  const RECORD_MAX_SECS = 180; // 3 min hard cap (Whisper accepts up to 25MB)

  function pickAudioMime() {
    // Prefer webm/opus (Chrome/Edge/Firefox/Android), fallback to mp4 (Safari iOS)
    if (typeof MediaRecorder === 'undefined') return null;
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/mpeg'];
    for (const c of candidates) {
      if (MediaRecorder.isTypeSupported(c)) return c;
    }
    return ''; // empty = let browser pick default
  }

  function updateMicUI(isRecording) {
    const idle = document.getElementById('mic-icon-idle');
    const rec = document.getElementById('mic-icon-recording');
    const timer = document.getElementById('mic-timer');
    if (!idle || !rec || !timer) return;
    if (isRecording) {
      idle.classList.add('hidden');
      rec.classList.remove('hidden');
      timer.classList.remove('hidden');
    } else {
      idle.classList.remove('hidden');
      rec.classList.add('hidden');
      timer.classList.add('hidden');
    }
  }

  function fmtTimer(s) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r < 10 ? '0' : ''}${r}`;
  }

  window.toggleVoiceRecord = async function () {
    // Stop in-progress recording
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      mediaRecorder.stop();
      return;
    }
    // Browser support check
    if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') {
      alert('Voice recording is not supported in this browser. Try Chrome or Safari.');
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      alert('Microphone access denied. Allow microphone for this site and try again.');
      return;
    }

    const mime = pickAudioMime();
    try {
      mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch (e) {
      alert('Could not start recording: ' + e.message);
      stream.getTracks().forEach(t => t.stop());
      return;
    }

    recordedChunks = [];
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recordedChunks.push(e.data);
    };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      clearInterval(recordTimer);
      recordTimer = null;
      updateMicUI(false);

      const usedMime = mediaRecorder.mimeType || mime || 'audio/webm';
      const ext = usedMime.includes('mp4') ? 'm4a'
                : usedMime.includes('mpeg') ? 'mp3'
                : 'webm';
      const blob = new Blob(recordedChunks, { type: usedMime });
      const ts = new Date();
      const stamp = ts.getFullYear()
                  + String(ts.getMonth() + 1).padStart(2, '0')
                  + String(ts.getDate()).padStart(2, '0') + '_'
                  + String(ts.getHours()).padStart(2, '0')
                  + String(ts.getMinutes()).padStart(2, '0')
                  + String(ts.getSeconds()).padStart(2, '0');
      const file = new File([blob], `Voice_${stamp}.${ext}`, { type: usedMime });
      pendingFiles.push(file);
      renderFilePills();
    };

    mediaRecorder.start();
    recordSecs = 0;
    updateMicUI(true);
    const timerEl = document.getElementById('mic-timer');
    timerEl.textContent = '0:00';
    recordTimer = setInterval(() => {
      recordSecs++;
      timerEl.textContent = fmtTimer(recordSecs);
      if (recordSecs >= RECORD_MAX_SECS && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
      }
    }, 1000);
  };

  // ── Auto-resize textarea + Enter to send ─────────────────────────────
  textInput.addEventListener('input', () => {
    textInput.style.height = 'auto';
    textInput.style.height = Math.min(textInput.scrollHeight, 120) + 'px';
  });
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  // ── Send message ─────────────────────────────────────────────────────
  window.sendChatMessage = async function () {
    const text = textInput.value.trim();
    if (!text && !pendingFiles.length) return;

    sendBtn.disabled = true;
    const fd = new FormData();
    fd.append('text', text);
    for (const f of pendingFiles) fd.append('files', f);

    // Optimistic UI: render the user message immediately
    appendMessage({
      id: 'pending-' + Date.now(),
      role: 'user',
      content: text,
      attachments: pendingFiles.map((f) => ({ filename: f.name, size: f.size, category: 'chat_inbox' })),
    });

    const thinkingId = 'thinking-' + Date.now();
    appendThinking(thinkingId);

    textInput.value = '';
    textInput.style.height = 'auto';
    pendingFiles = [];
    renderFilePills();

    try {
      const resp = await fetch(`/api/chat/${CLIENT_ID}/message`, { method: 'POST', body: fd });
      const data = await resp.json();
      removeThinking(thinkingId);
      if (!data.ok) {
        appendMessage({
          role: 'assistant',
          content: '⚠️ ' + (data.error || 'Something went wrong.'),
        });
      } else {
        // Replace the pending user message with the real one (so attachments resolve)
        // For simplicity, just re-render the whole thread from history
        await loadChatHistory({ skipScroll: false });
      }
    } catch (e) {
      removeThinking(thinkingId);
      appendMessage({
        role: 'assistant',
        content: '⚠️ Network error: ' + (e.message || e),
      });
    } finally {
      sendBtn.disabled = false;
    }
  };

  // ── Render a single message ──────────────────────────────────────────
  function appendMessage(m) {
    if (empty) empty.classList.add('hidden');
    const wrap = document.createElement('div');
    wrap.dataset.messageId = m.id || '';
    wrap.className = m.role === 'user' ? 'flex justify-end' : 'flex justify-start';

    const bubble = document.createElement('div');
    bubble.className = m.role === 'user'
      ? 'relative max-w-[85%] bg-primary-600 text-white rounded-2xl rounded-br-sm px-4 py-2.5 shadow-sm group'
      : 'relative max-w-[90%] bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm group';

    let html = '';
    // Per-message delete button (only on persisted messages, not optimistic ones)
    if (m.id && !String(m.id).startsWith('pending-')) {
      const btnCls = m.role === 'user'
        ? 'absolute -top-1.5 -right-1.5 w-5 h-5 bg-white/90 text-red-500 hover:bg-red-50 rounded-full text-xs leading-none shadow border border-red-200'
        : 'absolute -top-1.5 -right-1.5 w-5 h-5 bg-white text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-full text-xs leading-none shadow border border-gray-200';
      html += `<button type="button" onclick="window.__deleteMessage('${m.id}')" class="${btnCls}" title="Delete this message">×</button>`;
    }
    if (m.content) {
      html += `<div class="text-sm ${m.role === 'user' ? '' : 'text-gray-800'}">${inlineMd(m.content)}</div>`;
    }

    if (m.attachments && m.attachments.length) {
      html += '<div class="mt-2 flex flex-wrap gap-1.5">';
      for (const a of m.attachments) {
        const labelColor = m.role === 'user' ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-700';
        html += `<span class="inline-flex items-center gap-1 px-2 py-0.5 ${labelColor} text-[11px] rounded">
          📎 ${escapeHtml(a.filename)} <span class="opacity-60">· ${escapeHtml(categoryLabel(a.category))}</span>
        </span>`;
      }
      html += '</div>';
    }

    if (m.role === 'assistant') {
      // Clarifying questions
      if (m.clarifying_questions && m.clarifying_questions.length) {
        html += '<div class="mt-2 space-y-1">';
        for (const q of m.clarifying_questions) {
          html += `<div class="text-sm text-gray-600 bg-blue-50 border border-blue-100 rounded-lg px-2.5 py-1.5">❓ ${escapeHtml(q)}</div>`;
        }
        html += '</div>';
      }

      // Diff card
      if (m.proposed_patch && Object.keys(m.proposed_patch).length) {
        html += renderDiffCard(m);
      }

      // Advice
      if (m.advice && m.advice.length) {
        html += '<div class="mt-2 space-y-1">';
        for (const a of m.advice) {
          const cls = a.severity === 'error'
            ? 'bg-red-50 border-red-200 text-red-800'
            : a.severity === 'warning'
              ? 'bg-amber-50 border-amber-200 text-amber-800'
              : 'bg-blue-50 border-blue-200 text-blue-800';
          html += `<div class="text-xs border ${cls} rounded-lg px-2.5 py-1.5">
            <span class="font-semibold">${escapeHtml(a.section || 'Note')}:</span> ${escapeHtml(a.text)}
          </div>`;
        }
        html += '</div>';
      }
    }

    bubble.innerHTML = html;
    wrap.appendChild(bubble);
    thread.appendChild(wrap);
    thread.scrollTop = thread.scrollHeight;
  }

  function renderDiffCard(m) {
    const patch = m.proposed_patch || {};
    const rows = [];
    if (patch.step1) {
      const s1 = patch.step1;
      const fields = [
        ['Full name', s1.full_name],
        ['NRIC / passport', s1.nric_passport],
        ['Address', s1.residential_address],
        ['Date of birth', s1.date_of_birth],
        ['Nationality', s1.nationality],
        ['Gender', s1.gender],
      ];
      for (const [k, v] of fields) {
        if (!v) continue;
        rows.push(`<tr><td class="pr-3 py-0.5 text-gray-500 align-top">${escapeHtml(k)}</td><td class="py-0.5 font-medium text-gray-900">${escapeHtml(v)}</td></tr>`);
      }
    }
    const isResolved = m.applied_at || m.rejected_at;
    const buttons = isResolved
      ? `<div class="text-xs ${m.applied_at ? 'text-green-700' : 'text-gray-500'} mt-2 font-medium">
           ${m.applied_at ? '✓ Applied' : '✗ Rejected'}
         </div>`
      : `<div class="flex gap-2 mt-3">
           <button onclick="window.__applyPatch('${m.id}')" class="px-3 py-1.5 bg-green-600 text-white text-xs font-semibold rounded-lg hover:bg-green-700">
             ✓ Apply changes
           </button>
           <button onclick="window.__rejectPatch('${m.id}')" class="px-3 py-1.5 text-gray-600 border border-gray-300 text-xs font-medium rounded-lg hover:bg-gray-50">
             Reject
           </button>
         </div>`;

    return `
      <div class="mt-3 bg-gray-50 border border-gray-200 rounded-lg p-3">
        <div class="text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">Proposed changes</div>
        ${rows.length ? `<table class="text-xs"><tbody>${rows.join('')}</tbody></table>` : '<div class="text-xs text-gray-500 italic">No visible field changes</div>'}
        ${buttons}
      </div>
    `;
  }

  function appendThinking(id) {
    if (empty) empty.classList.add('hidden');
    const div = document.createElement('div');
    div.id = id;
    div.className = 'flex justify-start';
    div.innerHTML = `
      <div class="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-2.5 shadow-sm">
        <div class="flex items-center gap-2 text-sm text-gray-500">
          <svg class="animate-spin w-4 h-4 text-primary-600" fill="none" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" class="opacity-25"/>
            <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" class="opacity-75"/>
          </svg>
          <span>Reading documents…</span>
        </div>
      </div>`;
    thread.appendChild(div);
    thread.scrollTop = thread.scrollHeight;
  }
  function removeThinking(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  // ── Clear / delete ───────────────────────────────────────────────────
  window.clearChatHistory = async function () {
    if (!confirm('Delete every message in this chat? Documents stay; the conversation thread is wiped. This cannot be undone.')) return;
    const r = await fetch(`/api/chat/${CLIENT_ID}/clear`, { method: 'POST' });
    const data = await r.json();
    if (!data.ok) { alert(data.error || 'Clear failed'); return; }
    lastSignature = null;  // force re-render
    await loadChatHistory({ force: true });
  };

  window.__deleteMessage = async function (msgId) {
    if (!confirm('Delete this message (and the assistant reply if any)?')) return;
    const r = await fetch(`/api/chat/${CLIENT_ID}/message/${msgId}`, { method: 'DELETE' });
    const data = await r.json();
    if (!data.ok) { alert(data.error || 'Delete failed'); return; }
    lastSignature = null;
    await loadChatHistory({ force: true });
  };

  // ── Apply / reject ───────────────────────────────────────────────────
  window.__applyPatch = async function (msgId) {
    const resp = await fetch(`/api/chat/${CLIENT_ID}/apply/${msgId}`, { method: 'POST' });
    const data = await resp.json();
    if (!data.ok) {
      alert(data.error || 'Apply failed');
      return;
    }
    await loadChatHistory();
  };
  window.__rejectPatch = async function (msgId) {
    const resp = await fetch(`/api/chat/${CLIENT_ID}/reject/${msgId}`, { method: 'POST' });
    const data = await resp.json();
    if (!data.ok) {
      alert(data.error || 'Reject failed');
      return;
    }
    await loadChatHistory();
  };

  // ── Render right-pane will snapshot ──────────────────────────────────
  function renderWillSnapshot(will) {
    if (!will || !will.will_id) {
      willStatus.textContent = 'No active draft yet';
      willPane.innerHTML = '<div class="text-xs text-gray-400 italic p-2">A draft will be created the first time you apply changes.</div>';
      return;
    }
    willStatus.innerHTML = `<span class="inline-block w-2 h-2 rounded-full ${will.status === 'draft' ? 'bg-amber-400' : 'bg-green-500'} mr-1"></span>${escapeHtml(will.status)} · ${escapeHtml(will.title || 'Untitled')}`;

    const sections = [];
    const s1 = will.step1 || {};
    sections.push(snapshotSection(
      '1', 'Testator', s1.full_name ? '/wizard/step/2' : null,
      s1.full_name ? [
        ['Name', s1.full_name],
        ['NRIC', s1.nric_passport],
        ['DOB', s1.date_of_birth],
        ['Address', s1.residential_address],
      ] : null
    ));

    // Other steps — placeholders for now (slice 2+)
    const placeholderSteps = [
      ['2', 'Executors', will.step2 && will.step2.executors && will.step2.executors.length ? `${will.step2.executors.length} appointed` : null, '/wizard/step/3'],
      ['3', 'Guardians', will.step3 && will.step3.guardians && will.step3.guardians.length ? `${will.step3.guardians.length} appointed` : null, '/wizard/step/4'],
      ['4', 'Beneficiaries', Array.isArray(will.step4) && will.step4.length ? `${will.step4.length} listed` : null, '/wizard/step/5'],
      ['5', 'Specific Gifts', Array.isArray(will.step5) && will.step5.length ? `${will.step5.length} listed` : null, '/wizard/step/6'],
      ['6', 'Residuary Estate', will.step6 && Object.keys(will.step6).length ? 'Configured' : null, '/wizard/step/7'],
      ['7', 'Trust', will.step7 && Object.keys(will.step7).length ? 'Configured' : null, '/wizard/step/8'],
      ['8', 'Other Matters', will.step8 && Object.keys(will.step8).length ? 'Configured' : null, '/wizard/step/9'],
    ];
    for (const [n, name, summary, link] of placeholderSteps) {
      sections.push(snapshotSection(n, name, summary ? link : null, summary ? [[null, summary]] : null));
    }

    willPane.innerHTML = sections.join('');
  }

  function snapshotSection(num, name, link, fields) {
    const isFilled = fields && fields.length;
    const checkmark = isFilled
      ? '<svg class="w-3.5 h-3.5 text-green-600 inline" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>'
      : '<span class="inline-block w-3.5 h-3.5 rounded-full border border-gray-300"></span>';
    const header = link
      ? `<a href="${link}" class="hover:text-primary-600 flex items-center gap-1.5">
           ${checkmark}<span class="font-semibold">Step ${num}: ${escapeHtml(name)}</span>
         </a>`
      : `<div class="flex items-center gap-1.5 ${isFilled ? '' : 'text-gray-400'}">
           ${checkmark}<span class="font-semibold">Step ${num}: ${escapeHtml(name)}</span>
         </div>`;

    let body = '';
    if (fields && fields.length) {
      body = '<table class="text-[11px] mt-1 ml-5"><tbody>';
      for (const [k, v] of fields) {
        if (!v) continue;
        if (k) {
          body += `<tr><td class="pr-2 py-px text-gray-500 align-top">${escapeHtml(k)}</td><td class="py-px text-gray-800">${escapeHtml(v)}</td></tr>`;
        } else {
          body += `<tr><td colspan="2" class="py-px text-gray-700">${escapeHtml(v)}</td></tr>`;
        }
      }
      body += '</tbody></table>';
    }
    return `<div class="border-b border-gray-100 pb-2 last:border-b-0">${header}${body}</div>`;
  }

  // ── Load + render history ────────────────────────────────────────────
  // Tracks last known signature to skip re-render when nothing changed
  // (avoids visual flicker on every poll). Signature combines count +
  // last-message id + last applied/rejected timestamps so a Apply/Reject
  // arriving via another tab also re-renders.
  let lastSignature = null;

  function historySignature(messages) {
    if (!messages.length) return '0';
    return messages.map(m =>
      `${m.id}:${m.applied_at || ''}:${m.rejected_at || ''}`
    ).join('|');
  }

  window.loadChatHistory = async function (opts = {}) {
    try {
      const resp = await fetch(`/api/chat/${CLIENT_ID}/history`);
      const data = await resp.json();
      if (!data.ok) return;

      const sig = historySignature(data.messages);
      if (!opts.force && sig === lastSignature) {
        // Nothing changed — still refresh the will snapshot in case it
        // was edited via the wizard in another tab, but skip the thread.
        renderWillSnapshot(data.will);
        return;
      }
      lastSignature = sig;

      // Preserve scroll behaviour: if the user was at the bottom, stay at
      // the bottom after re-render. If they were scrolled up reading, leave
      // them where they were.
      const wasAtBottom = (thread.scrollHeight - thread.scrollTop - thread.clientHeight) < 80;

      thread.innerHTML = '';
      if (data.messages.length === 0) {
        thread.appendChild(empty);
        empty.classList.remove('hidden');
      } else {
        empty.classList.add('hidden');
        for (const m of data.messages) appendMessage(m);
      }
      renderWillSnapshot(data.will);

      if (wasAtBottom) thread.scrollTop = thread.scrollHeight;
    } catch (e) {
      // Network blip — silent, next poll will retry.
    }
  };

  // ── Auto-poll for new messages (e.g. inbound emails) ─────────────────
  // Polls every 5s while the tab is visible; pauses when hidden or backgrounded
  // so we don't burn the API.
  const POLL_MS = 5000;
  let pollTimer = null;
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        loadChatHistory();
      }
    }, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      loadChatHistory();   // catch up immediately on refocus
      startPolling();
    } else {
      stopPolling();
    }
  });

  loadChatHistory();
  startPolling();
})();
