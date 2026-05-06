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
      nric: 'IC / Passport',
      property_title: 'Property Title',
      property_spa: 'SPA',
      property_tax: 'Property Tax',
      property_transfer: 'MOT (Borang 14A)',
      utility_bill: 'Utility Bill',
      bank_letter: 'Bank Letter',
      loan_agreement: 'Loan Agreement',
      bank_statement: 'Bank Statement',
      insurance: 'Insurance',
      epf_kwsp: 'EPF / KWSP',
      vehicle: 'Vehicle',
      will: 'Existing Will',
      death_certificate: '⚠️ Death Cert',
      unrelated: '⚠️ Unrelated',
      chat_inbox: 'Pending',
      other: 'Unclassified',
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
    // Extract any <!--quickreplies:[...]--> marker from content. The marker
    // is stripped before rendering text and the buttons are emitted below.
    let inlineQuickReplies = null;
    let renderContent = m.content || '';
    if (renderContent) {
      const qrMatch = renderContent.match(/<!--quickreplies:(\[[\s\S]*?\])-->/);
      if (qrMatch) {
        try { inlineQuickReplies = JSON.parse(qrMatch[1]); } catch (e) {}
        renderContent = renderContent.replace(qrMatch[0], '').trim();
      }
    }
    if (renderContent) {
      html += `<div class="text-sm ${m.role === 'user' ? '' : 'text-gray-800'}">${inlineMd(renderContent)}</div>`;
    }

    if (m.attachments && m.attachments.length) {
      // Collect image URLs + meta for carousel (in order) — only images participate;
      // PDFs and audio open in a new tab as before.
      const imgCarouselUrls = m.attachments
        .filter(a => /\.(jpe?g|png|gif|webp|heic|heif|bmp)$/i.test(a.filename || ''))
        .map(a => `/api/documents/${a.id}`);
      // Meta: label (purpose/address or category) + docId for remove button
      const imgCarouselMeta = m.attachments
        .filter(a => /\.(jpe?g|png|gif|webp|heic|heif|bmp)$/i.test(a.filename || ''))
        .map(a => ({
          docId: a.id,
          label: (a.purpose || a.address || categoryLabel(a.category) || '').trim(),
          suspectedWrong: !!a.suspected_wrong,
          wrongReason: a.wrong_reason || '',
        }));

      const isMany = m.attachments.length > 6;
      html += `<div class="mt-2 grid ${isMany ? 'grid-cols-4 sm:grid-cols-6' : 'grid-cols-3 sm:grid-cols-4'} gap-1.5">`;
      let carouselIdx = 0; // index into imgCarouselUrls for each image thumb
      for (const a of m.attachments) {
        const url = `/api/documents/${a.id}`;
        const isImg = /\.(jpe?g|png|gif|webp|heic|heif|bmp)$/i.test(a.filename || '');
        const isPdf = /\.pdf$/i.test(a.filename || '');
        const isAudio = /\.(mp3|m4a|wav|webm|ogg|oga)$/i.test(a.filename || '');
        const catLabel = escapeHtml(categoryLabel(a.category));
        const catColor = a.category === 'chat_inbox'
          ? 'bg-amber-100 text-amber-700'
          : a.category === 'nric'
            ? 'bg-purple-100 text-purple-700'
            : a.category === 'property_title'
              ? 'bg-blue-100 text-blue-700'
              : a.category === 'voice'
                ? 'bg-pink-100 text-pink-700'
                : 'bg-gray-100 text-gray-600';

        let inner = '';
        if (isImg) {
          inner = `<img src="${url}" loading="lazy" alt="${escapeHtml(a.filename)}" class="w-full h-16 object-cover">`;
        } else if (isAudio) {
          inner = `<div class="w-full h-16 flex items-center justify-center bg-pink-50 text-pink-600 text-2xl">🎙</div>`;
        } else if (isPdf) {
          inner = `<div class="w-full h-16 flex items-center justify-center bg-red-50 text-red-600 text-2xl">📄</div>`;
        } else {
          inner = `<div class="w-full h-16 flex items-center justify-center bg-gray-50 text-gray-500 text-2xl">📎</div>`;
        }

        if (isImg) {
          const thisIdx = carouselIdx;
          const thumbNum = carouselIdx + 1; // 1-based position shown as overlay
          carouselIdx++;
          // Flag documents suspected as wrong/irrelevant
          const isSuspect = !!a.suspected_wrong;
          const warnBadge = isSuspect
            ? `<div class="absolute top-0.5 right-0.5 bg-amber-500 text-white text-[8px] font-bold px-1 rounded leading-tight">⚠️ check</div>`
            : '';
          // Amber dashed border for suspect docs, normal border otherwise
          const borderCls = isSuspect
            ? 'ring-2 ring-amber-400 border-amber-300'
            : 'border-gray-200 hover:border-primary-400';
          // Show purpose/address as label if available, else category
          const thumbLabel = escapeHtml((a.purpose || a.address || catLabel || '').trim().slice(0, 40));
          html += `<button type="button"
                      onclick="window.__openCarousel(${JSON.stringify(imgCarouselUrls).replace(/"/g, '&quot;')}, ${thisIdx}, ${JSON.stringify(imgCarouselMeta).replace(/"/g, '&quot;')})"
                      title="${escapeHtml(a.filename || '')} — ${thumbLabel}${isSuspect ? ' ⚠️ may be incorrect' : ''}"
                      class="relative block bg-white border rounded-md overflow-hidden hover:shadow transition-all text-left w-full ${borderCls}">
            ${inner}
            <div class="absolute top-0.5 left-0.5 bg-black/50 text-white text-[9px] font-bold px-1 rounded leading-tight">${thumbNum}</div>
            ${warnBadge}
            <div class="px-1 py-0.5 text-[9px] truncate ${isSuspect ? 'bg-amber-100 text-amber-800' : catColor} font-medium">${thumbLabel || catLabel}</div>
          </button>`;
        } else {
          html += `<a href="${url}" target="_blank" rel="noopener" title="${escapeHtml(a.filename)} (${catLabel})"
                      class="block bg-white border border-gray-200 rounded-md overflow-hidden hover:border-primary-400 hover:shadow transition-all">
            ${inner}
            <div class="px-1 py-0.5 text-[9px] truncate ${catColor} font-medium">${catLabel}</div>
          </a>`;
        }
      }
      html += '</div>';
    }

    if (m.role === 'assistant') {
      // Walk-through quick action buttons (Yes / Skip / Delete) — only render
      // on the LATEST assistant bubble so old questions don't show stale buttons.
      const isWalkthrough = (m.content || '').includes('Step 1: Identity walk-through');
      const isLatest = (m.id === window.__latestAssistantId);
      if (isWalkthrough && isLatest) {
        html += `
          <div class="mt-3 flex flex-wrap gap-2">
            <button onclick="window.__quickReply('yes')" class="px-3 py-1.5 bg-green-600 text-white text-sm font-semibold rounded-lg hover:bg-green-700 flex items-center gap-1">
              ✓ Yes, confirm
            </button>
            <button onclick="window.__quickReply('skip')" class="px-3 py-1.5 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200 flex items-center gap-1 border border-gray-300">
              ⏭ Skip
            </button>
            <button onclick="window.__quickReply('delete')" class="px-3 py-1.5 bg-red-50 text-red-600 text-sm font-semibold rounded-lg hover:bg-red-100 border border-red-200 flex items-center gap-1">
              🗑 Delete (uploaded by mistake)
            </button>
          </div>
          <div class="mt-2 text-xs text-gray-500">…or type a different role: spouse, son, daughter, executor, etc.</div>
        `;
      }

      // Inline quick-reply buttons from <!--quickreplies--> marker (only on
      // the latest assistant bubble so stale buttons disappear).
      const isLatestForQR = (m.id === window.__latestAssistantId);
      if (inlineQuickReplies && inlineQuickReplies.length && isLatestForQR) {
        html += '<div class="mt-3 flex flex-wrap gap-2">';
        for (const qr of inlineQuickReplies) {
          const lbl = escapeHtml(qr.label || qr.value || '');
          const val = (qr.value || qr.label || '').replace(/'/g, "\\'");
          // Heuristic styling: skip/delete get muted/red, others primary
          const v = (qr.value || '').toLowerCase();
          const cls = v === 'skip' || v === 'not yet'
            ? 'px-3 py-1.5 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200 border border-gray-300'
            : v === 'delete'
              ? 'px-3 py-1.5 bg-red-50 text-red-600 text-sm font-semibold rounded-lg hover:bg-red-100 border border-red-200'
              : 'px-3 py-1.5 bg-primary-600 text-white text-sm font-semibold rounded-lg hover:bg-primary-700';
          html += `<button onclick="window.__quickReply('${val}')" class="${cls}">${lbl}</button>`;
        }
        html += '</div>';
      }

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

  // ── Quick reply (walk-through buttons send a one-word message) ──────
  window.__quickReply = function (word) {
    // Special value 'other' → just focus the input so user can type their own.
    if (word === 'other' || word === 'type') {
      textInput.focus();
      textInput.placeholder = 'Type your answer here…';
      return;
    }
    // Values ending in ': ' (e.g. 'address: ') prefill but don't send — the
    // user fills in the rest, then hits Send.
    if (typeof word === 'string' && word.endsWith(': ')) {
      textInput.value = word;
      textInput.focus();
      return;
    }
    textInput.value = word;
    sendChatMessage();
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

    // Live identities from Person registry (added via chat walk-through)
    const ids = will.identities || [];
    if (ids.length) {
      let body = '<table class="text-[11px] mt-1 ml-5"><tbody>';
      for (const p of ids) {
        const rel = p.relationship || '—';
        const relColor = rel === 'Testator' ? 'text-purple-700 font-semibold'
                       : rel === 'Executor' ? 'text-green-700'
                       : '—';
        body += `<tr><td class="pr-2 py-px text-gray-500 align-top truncate" style="max-width:9rem">${escapeHtml(p.full_name||'?')}</td>
                 <td class="py-px ${relColor}">${escapeHtml(rel)}</td></tr>`;
      }
      body += '</tbody></table>';
      sections.push(
        `<div class="border-b border-gray-100 pb-2">
           <a href="/wizard/step/1" class="hover:text-primary-600 flex items-center gap-1.5">
             <svg class="w-3.5 h-3.5 text-green-600 inline" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
             <span class="font-semibold">Identities (${ids.length})</span>
           </a>
           ${body}
         </div>`
      );
    }

    // Build per-step data — figure out which step is "current" so we can
    // auto-expand it and collapse the rest.
    const s1 = will.step1 || {};
    const execs = (will.step2 && will.step2.executors) || [];
    const guardians = (will.step3 && will.step3.guardians) || [];
    const benefs = Array.isArray(will.step4) ? will.step4 : [];
    const gifts = Array.isArray(will.step5) ? will.step5 : [];
    // Step numbers MATCH the wizard sidebar (Identities=1 above, Testator=2,
    // Executors=3, Guardians=4, Beneficiaries=5, Gifts=6, Residuary=7,
    // Trust=8, Other=9). DB columns are step1_data…step8_data offset −1.
    // `optional` flag stops the fallback "first empty" cursor from
    // glowing skipped steps forever (Guardians/Trust/Other are commonly
    // skipped — without this the chat snapshot would always say current
    // = Guardians even when the chat is actually on Specific Gifts).
    const stepData = [
      { n: '2', name: 'Testator', link: '/wizard/step/2', optional: false,
        fields: s1.full_name ? [
          ['Name', s1.full_name], ['NRIC', s1.nric_passport],
          ['DOB', s1.date_of_birth], ['Address', s1.residential_address],
        ] : null },
      { n: '3', name: 'Executors', link: '/wizard/step/3', optional: false,
        fields: execs.length ? execs.map(e => [
          e.is_substitute ? 'Substitute' : 'Main',
          e.full_name + (e.relationship ? ` (${e.relationship})` : '')
        ]) : null },
      { n: '4', name: 'Guardians', link: '/wizard/step/4', optional: true,
        fields: guardians.length ? guardians.map(g => [null, g.full_name || g.name || '?']) : null },
      { n: '5', name: 'Beneficiaries', link: '/wizard/step/5', optional: false,
        fields: benefs.length ? benefs.map(b => [null,
          (b.full_name || b.name || '?') + (b.share ? ` — ${b.share}` : '')]) : null },
      { n: '6', name: 'Specific Gifts', link: '/wizard/step/6', optional: true,
        fields: gifts.length ? gifts.filter(g => !g.skipped).map(g => {
          // Gifts arrive in two formats:
          //   Chat format:   {kind, property_address, title_number, beneficiaries:[{name,share}]}
          //   Wizard format: {gift_type, description, property_details:{property_address,title_number,...},
          //                   financial_details:{institution,account_number,...}, allocations:[{beneficiary_name}]}
          // Normalise both into a single view.
          const kind    = g.kind       || g.gift_type || '';
          const pd      = g.property_details   || {};
          const fd      = g.financial_details  || {};
          const propAddr  = g.property_address  || pd.property_address  || '';
          const titleNo   = g.title_number      || pd.title_number      || '';
          const lotNo     = g.lot_number        || pd.lot_number        || '';
          const bankName  = g.bank_name         || fd.institution       || '';
          const acctRaw   = g.account_number    || fd.account_number    || '';
          const regNum    = g.reg_number        || '';
          // Prefer allocations (wizard format) when present; fall back to
          // legacy beneficiaries array. Never concat both — the gift entry
          // has both arrays with the same names which causes duplicates.
          const benList = ((g.allocations && g.allocations.length)
            ? g.allocations.map(a => a.beneficiary_name || a.name || '')
            : (g.beneficiaries || []).map(b => b.name || b)
          ).filter(Boolean);

          let prefix = '', ident = '';
          if (kind === 'property' || propAddr || titleNo || lotNo) {
            prefix = '🏠 Property';
            // Prefer address → title no → lot no → mukim.
            // Show first 2 comma segments of the address (e.g. unit + building)
            // so e.g. "#30-08, Menara C, Pangsapuri Tepian Bayu, …" renders as
            // "#30-08, Menara C" instead of just "#30-08". The outer 120-char
            // slice still caps total length.
            if (propAddr) {
              const segs = propAddr.split(',').map(s => s.trim()).filter(Boolean);
              ident = segs.slice(0, 3).join(', ');
            }
            else if (titleNo) ident = titleNo;
            else if (lotNo) ident = `Lot ${lotNo}`;
            else ident = (pd.mukim || pd.negeri || '').trim();
          } else if (kind === 'bank' || kind === 'financial' || bankName || acctRaw) {
            prefix = '🏦';
            const acct = acctRaw ? `…${String(acctRaw).slice(-4)}` : '';
            ident = [bankName, g.account_type || fd.asset_type, acct].filter(Boolean).join(' ') || 'Bank account';
          } else if (kind === 'vehicle' || regNum) {
            prefix = '🚗';
            ident = regNum || g.vehicle_description || g.description || 'Vehicle';
          } else if (kind === 'insurance' || g.insurer_name) {
            prefix = '🛡';
            ident = g.insurer_name || g.policy_number || 'Insurance';
          } else if (kind === 'epf_kwsp') {
            prefix = '💼 EPF/KWSP';
          } else if (g.document_id) {
            // Legacy chat-format gift: has document_id but no recognised kind.
            // Only _try_save_property_gift saves with document_id, so this is
            // always a property gift saved before `kind` was introduced.
            prefix = '🏠 Property';
            ident = g.title_number || g.lot_number || g.property_address || '';
          } else {
            // Other/disclaimer — use description if any, else a readable gift type
            const typeLabel = { other: 'Other Gift', financial: 'Financial Asset', property: '🏠 Property' }[kind] || '';
            const rawDesc = g.description || '';
            // Don't use generic placeholders like 'Gift' as the label — they add no value
            prefix = (rawDesc && rawDesc.toLowerCase() !== 'gift') ? rawDesc : (typeLabel || '📦 Asset');
          }
          let label = ident ? `${prefix} — ${ident}` : prefix;
          if (benList.length) label += ' → ' + benList.slice(0, 2).join(', ') + (benList.length > 2 ? '…' : '');
          else if (g._pending_beneficiary) label += ' · ⏳ awaiting beneficiary';
          return [null, label.slice(0, 120)];
        }) : null },
      { n: '7', name: 'Residuary Estate', link: '/wizard/step/7', optional: false,
        fields: will.step6 && Object.keys(will.step6).length ? [[null, 'Configured']] : null },
      { n: '8', name: 'Trust', link: '/wizard/step/8', optional: true,
        fields: will.step7 && Object.keys(will.step7).length ? [[null, 'Configured']] : null },
      { n: '9', name: 'Other Matters', link: '/wizard/step/9', optional: true,
        fields: will.step8 && Object.keys(will.step8).length ? [[null, 'Configured']] : null },
    ];

    // Pick "current" step in priority order:
    //   1. The chat planner's actual stage (server-side current_stage_num) —
    //      this is the source of truth, e.g. 6 when reviewing assets.
    //   2. Else: first REQUIRED empty step (skips optional gaps).
    //   3. Else: first empty step (any kind).
    //   4. Else: last step (everything filled).
    let currentIdx = -1;
    const stageNum = will && will.current_stage_num;
    if (stageNum) {
      currentIdx = stepData.findIndex(s => String(s.n) === String(stageNum));
    }
    if (currentIdx === -1) {
      currentIdx = stepData.findIndex(s => !s.fields && !s.optional);
    }
    if (currentIdx === -1) {
      currentIdx = stepData.findIndex(s => !s.fields);
    }
    if (currentIdx === -1) currentIdx = stepData.length - 1;

    for (let i = 0; i < stepData.length; i++) {
      sections.push(snapshotSection(stepData[i], i === currentIdx));
    }

    willPane.innerHTML = sections.join('');

    // Cost badge — async, best-effort, never blocks the snapshot render.
    if (will.will_id) {
      fetchAndRenderCostBadge(will.will_id);
    }
  }

  async function fetchAndRenderCostBadge(willId) {
    try {
      const resp = await fetch(`/api/will/${willId}/cost`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.ok) return;
      const pct = data.budget_pct || 0;
      const barW = Math.min(100, pct).toFixed(1);
      const barColor = pct < 10 ? 'bg-green-400' : pct < 40 ? 'bg-amber-400' : 'bg-red-500';
      // Build per-site tooltip text
      const rows = (data.calls || []).map(c =>
        `${c.call_site.replace('ai.', '')}: ${c.calls}× · $${parseFloat(c.cost_usd).toFixed(4)}`
      ).join('\n');
      const badge = document.createElement('div');
      badge.id = 'cost-badge';
      badge.className = 'border-t border-gray-100 pt-2 mt-2 px-1';
      badge.title = rows || 'No API calls logged yet';
      badge.innerHTML = `
        <div class="flex items-center justify-between text-[11px] text-gray-500 mb-0.5">
          <span>💰 API cost this will</span>
          <span class="font-mono font-semibold text-gray-700">${data.total_usd_fmt}</span>
        </div>
        <div class="w-full bg-gray-100 rounded-full h-1.5">
          <div class="${barColor} h-1.5 rounded-full" style="width:${barW}%"></div>
        </div>
        <div class="text-[10px] text-gray-400 mt-0.5">
          ${pct.toFixed(2)}% of $300/yr budget
        </div>`;
      // Replace existing badge or append
      const existing = willPane.querySelector('#cost-badge');
      if (existing) existing.replaceWith(badge);
      else willPane.appendChild(badge);
    } catch (e) {
      // silent — cost badge is informational only
    }
  }

  function snapshotSection(step, isCurrent) {
    const { n, name, link, fields } = step;
    const isFilled = fields && fields.length;
    const checkmark = isFilled
      ? '<svg class="w-3.5 h-3.5 text-green-600 inline" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>'
      : (isCurrent
          ? '<span class="inline-block w-3.5 h-3.5 rounded-full bg-primary-500 animate-pulse"></span>'
          : '<span class="inline-block w-3.5 h-3.5 rounded-full border border-gray-300"></span>');

    // Inline summary in the <summary> tag — N items or first-line preview
    let inlineSummary = '';
    if (isFilled) {
      const visibleFields = fields.filter(([k, v]) => v);
      if (visibleFields.length === 1 && !visibleFields[0][0]) {
        inlineSummary = visibleFields[0][1];
      } else {
        inlineSummary = `${visibleFields.length} item${visibleFields.length === 1 ? '' : 's'}`;
      }
    }

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

    const headerInner = `${checkmark}<span class="font-semibold ${isCurrent ? 'text-primary-700' : (isFilled ? 'text-gray-800' : 'text-gray-400')}">Step ${n}: ${escapeHtml(name)}</span>`;
    const summaryRight = inlineSummary
      ? `<span class="ml-auto text-[11px] text-gray-500 truncate" style="max-width:9rem">${escapeHtml(inlineSummary)}</span>`
      : '';
    const linkBtn = (isFilled && link)
      ? `<a href="${link}" onclick="event.stopPropagation()" class="ml-2 text-[11px] text-primary-600 hover:underline" title="Open in wizard">edit</a>`
      : '';
    const isOpen = isCurrent || !isFilled;

    return `<details ${isOpen ? 'open' : ''} class="border-b border-gray-100 pb-1.5 last:border-b-0 group">
      <summary class="cursor-pointer flex items-center gap-1.5 select-none py-1">
        <svg class="w-3 h-3 text-gray-400 transition-transform group-open:rotate-90" fill="currentColor" viewBox="0 0 20 20"><path d="M6 5l8 5-8 5V5z"/></svg>
        ${headerInner}
        ${summaryRight}
        ${linkBtn}
      </summary>
      ${body}
    </details>`;
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
        renderWillSnapshot(data.will);
        return;
      }
      lastSignature = sig;

      // Remember the latest assistant id so quick-action buttons only render
      // on the most recent walk-through question, not stale older ones.
      const latestAsst = [...data.messages].reverse().find(m => m.role === 'assistant');
      window.__latestAssistantId = latestAsst ? latestAsst.id : null;

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

  // ── Image carousel / lightbox ─────────────────────────────────────────
  // Renders a full-screen overlay with prev/next arrows, just like WhatsApp.
  // Used whenever a thumbnail inside the chat is tapped.
  //
  // Usage: window.__openCarousel(urlArray, startIndex)

  let _carouselUrls = [];
  let _carouselMeta = []; // parallel array: [{docId, label}]
  let _carouselIdx = 0;

  function _ensureCarouselDOM() {
    if (document.getElementById('img-carousel')) return;
    const overlay = document.createElement('div');
    overlay.id = 'img-carousel';
    overlay.className = [
      'fixed inset-0 z-50 flex items-center justify-center',
      'bg-black/90',
      'select-none',
    ].join(' ');
    overlay.style.cssText = 'display:none';
    overlay.innerHTML = `
      <!-- Close button -->
      <button id="car-close" aria-label="Close"
        class="absolute top-3 right-4 text-white text-3xl leading-none hover:text-gray-300 z-10">&#215;</button>

      <!-- Open in new tab button -->
      <a id="car-newtab" href="#" target="_blank" rel="noopener"
        aria-label="Open in new tab"
        class="absolute top-3 right-14 text-white/80 hover:text-white z-10
               bg-black/30 hover:bg-black/60 rounded px-2 py-1 text-xs font-medium
               flex items-center gap-1 no-underline transition-colors"
        onclick="event.stopPropagation()">
        &#128196; new tab
      </a>

      <!-- Counter -->
      <div id="car-counter"
        class="absolute top-3 left-1/2 -translate-x-1/2 text-white text-sm font-medium bg-black/40 px-3 py-1 rounded-full z-10">
      </div>

      <!-- Prev arrow -->
      <button id="car-prev" aria-label="Previous"
        class="absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 text-white text-4xl leading-none
               hover:text-gray-300 bg-black/30 hover:bg-black/50 rounded-full w-10 h-10 flex items-center justify-center z-10
               disabled:opacity-20 disabled:cursor-not-allowed transition-opacity">
        &#8249;
      </button>

      <!-- Image -->
      <img id="car-img" src="" alt=""
        class="max-h-[90vh] max-w-[90vw] object-contain rounded shadow-2xl transition-opacity duration-150"
        style="opacity:1">

      <!-- Loading spinner shown while image loads -->
      <div id="car-spinner" class="absolute inset-0 flex items-center justify-center pointer-events-none" style="display:none!important">
        <svg class="animate-spin w-8 h-8 text-white opacity-60" fill="none" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" class="opacity-25"/>
          <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" class="opacity-75"/>
        </svg>
      </div>

      <!-- Next arrow -->
      <button id="car-next" aria-label="Next"
        class="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 text-white text-4xl leading-none
               hover:text-gray-300 bg-black/30 hover:bg-black/50 rounded-full w-10 h-10 flex items-center justify-center z-10
               disabled:opacity-20 disabled:cursor-not-allowed transition-opacity">
        &#8250;
      </button>

      <!-- Warning banner for suspect exhibits — shown above the image -->
      <div id="car-warn-banner" style="display:none"
        class="absolute top-14 left-1/2 -translate-x-1/2 z-20 w-[90vw] max-w-md
               bg-amber-50 border border-amber-300 rounded-xl shadow-lg px-4 py-3 text-center">
        <div class="text-amber-800 font-semibold text-sm mb-1">⚠️ This exhibit may not be needed</div>
        <div id="car-warn-reason" class="text-amber-700 text-xs mb-3"></div>
        <div class="flex gap-2 justify-center">
          <button id="car-warn-keep"
            class="px-4 py-1.5 bg-white border border-amber-300 text-amber-800 text-sm font-medium rounded-lg hover:bg-amber-50 transition-colors">
            ✅ Keep it
          </button>
          <button id="car-warn-remove"
            class="px-4 py-1.5 bg-red-600 text-white text-sm font-semibold rounded-lg hover:bg-red-700 transition-colors">
            🗑 Remove
          </button>
        </div>
      </div>

      <!-- Bottom bar: exhibit label + manual remove button -->
      <div class="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-2 max-w-[90vw]">
        <div id="car-label"
          class="text-white/80 text-xs font-medium bg-black/40 px-3 py-1.5 rounded-full truncate max-w-[60vw]">
        </div>
        <button id="car-remove" style="display:none"
          class="text-white text-xs font-semibold bg-red-600/80 hover:bg-red-600 px-3 py-1.5 rounded-full flex items-center gap-1 transition-colors whitespace-nowrap">
          🗑 Remove
        </button>
      </div>
    `;
    document.body.appendChild(overlay);

    // Wire up controls
    document.getElementById('car-close').addEventListener('click', _closeCarousel);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) _closeCarousel();
    });
    document.getElementById('car-prev').addEventListener('click', (e) => {
      e.stopPropagation();
      _carouselNav(-1);
    });
    document.getElementById('car-next').addEventListener('click', (e) => {
      e.stopPropagation();
      _carouselNav(1);
    });
    // Warning banner: Keep dismisses it, Remove sends inbox remove command
    document.getElementById('car-warn-keep').addEventListener('click', (e) => {
      e.stopPropagation();
      document.getElementById('car-warn-banner').style.display = 'none';
    });
    document.getElementById('car-warn-remove').addEventListener('click', (e) => {
      e.stopPropagation();
      const meta = _carouselMeta[_carouselIdx] || {};
      if (meta.docId) {
        _closeCarousel();
        window.__quickReply(`inbox remove ${meta.docId}`);
      }
    });

    // Keyboard nav
    document.addEventListener('keydown', (e) => {
      const overlay = document.getElementById('img-carousel');
      if (!overlay || overlay.style.display === 'none') return;
      if (e.key === 'ArrowLeft')  { e.preventDefault(); _carouselNav(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); _carouselNav(1); }
      if (e.key === 'Escape')     { e.preventDefault(); _closeCarousel(); }
    });

    // Touch swipe support (left/right)
    let _touchStartX = null;
    overlay.addEventListener('touchstart', (e) => {
      _touchStartX = e.touches[0].clientX;
    }, { passive: true });
    overlay.addEventListener('touchend', (e) => {
      if (_touchStartX === null) return;
      const dx = e.changedTouches[0].clientX - _touchStartX;
      _touchStartX = null;
      if (Math.abs(dx) < 40) return; // too small — ignore
      _carouselNav(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  function _carouselShow() {
    const overlay    = document.getElementById('img-carousel');
    const img        = document.getElementById('car-img');
    const counter    = document.getElementById('car-counter');
    const prevBtn    = document.getElementById('car-prev');
    const nextBtn    = document.getElementById('car-next');
    const label      = document.getElementById('car-label');
    const newTab     = document.getElementById('car-newtab');
    const removeBtn  = document.getElementById('car-remove');
    const warnBanner = document.getElementById('car-warn-banner');
    const warnReason = document.getElementById('car-warn-reason');

    const url  = _carouselUrls[_carouselIdx];
    const meta = _carouselMeta[_carouselIdx] || {};
    const n    = _carouselUrls.length;

    // Fade-swap the image
    img.style.opacity = '0.3';
    img.onload = () => { img.style.opacity = '1'; };
    img.src = url;
    img.alt = `Image ${_carouselIdx + 1} of ${n}`;

    // Keep the "open in new tab" link pointing at the current image
    if (newTab) newTab.href = url;

    counter.textContent = `${_carouselIdx + 1} / ${n}`;

    // Show exhibit label (purpose/address) or fallback to doc id segment
    label.textContent = meta.label || url.split('/').pop() || '';

    // Warning banner — auto-shown for suspect/irrelevant docs
    if (warnBanner) {
      if (meta.suspectedWrong) {
        const reason = meta.wrongReason
          || 'Please check whether this image is correct or if it was uploaded by mistake.';
        if (warnReason) warnReason.textContent = reason;
        warnBanner.style.display = '';
      } else {
        warnBanner.style.display = 'none';
      }
    }

    // Manual remove button — shown whenever we have a docId
    if (removeBtn) {
      if (meta.docId) {
        removeBtn.style.display = '';
        removeBtn.onclick = (e) => {
          e.stopPropagation();
          _closeCarousel();
          window.__quickReply(`inbox remove ${meta.docId}`);
        };
      } else {
        removeBtn.style.display = 'none';
      }
    }

    prevBtn.disabled = (_carouselIdx === 0);
    nextBtn.disabled = (_carouselIdx === n - 1);

    // Hide arrows when only 1 image
    prevBtn.style.display = n <= 1 ? 'none' : '';
    nextBtn.style.display = n <= 1 ? 'none' : '';
    counter.style.display = n <= 1 ? 'none' : '';

    overlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
  }

  function _carouselNav(delta) {
    const next = _carouselIdx + delta;
    if (next < 0 || next >= _carouselUrls.length) return;
    _carouselIdx = next;
    _carouselShow();
  }

  function _closeCarousel() {
    const overlay = document.getElementById('img-carousel');
    if (overlay) overlay.style.display = 'none';
    document.body.style.overflow = '';
  }

  window.__openCarousel = function (urls, startIdx, meta) {
    if (!urls || !urls.length) return;
    _ensureCarouselDOM();
    _carouselUrls = urls;
    _carouselMeta = meta || [];
    _carouselIdx  = Math.max(0, Math.min(startIdx || 0, urls.length - 1));
    _carouselShow();
  };
})();
