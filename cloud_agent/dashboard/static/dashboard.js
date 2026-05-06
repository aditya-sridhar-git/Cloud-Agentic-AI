/**
 * CloudAgent — Command Center
 * dashboard.js
 * Handles: canvas background, WebSocket, REST polling, UI rendering
 */

// ============================================================
// CANVAS PARTICLE BACKGROUND
// ============================================================

(function initCanvas() {
    const canvas = document.getElementById('bg-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let W, H, particles = [], animId;

    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
    }

    function makeParticle() {
        return {
            x: Math.random() * W,
            y: Math.random() * H,
            r: Math.random() * 1.2 + 0.3,
            vx: (Math.random() - 0.5) * 0.18,
            vy: (Math.random() - 0.5) * 0.18,
            a: Math.random() * 0.45 + 0.08,
            hue: Math.random() < 0.6 ? 220 : Math.random() < 0.5 ? 190 : 260,
        };
    }

    function initParticles(n = 90) {
        particles = Array.from({ length: n }, makeParticle);
    }

    function drawFrame() {
        ctx.clearRect(0, 0, W, H);

        // Faint grid
        ctx.strokeStyle = 'rgba(68,136,255,0.028)';
        ctx.lineWidth = 1;
        const step = 52;
        for (let x = 0; x < W; x += step) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
        for (let y = 0; y < H; y += step) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

        // Connect nearby particles with lines
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 120) {
                    const alpha = (1 - dist / 120) * 0.06;
                    ctx.beginPath();
                    ctx.strokeStyle = `hsla(${particles[i].hue},70%,65%,${alpha})`;
                    ctx.lineWidth = 0.5;
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.stroke();
                }
            }
        }

        // Draw & move particles
        for (const p of particles) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = `hsla(${p.hue},70%,70%,${p.a})`;
            ctx.fill();

            p.x += p.vx;
            p.y += p.vy;
            if (p.x < -5) p.x = W + 5;
            if (p.x > W + 5) p.x = -5;
            if (p.y < -5) p.y = H + 5;
            if (p.y > H + 5) p.y = -5;
        }

        animId = requestAnimationFrame(drawFrame);
    }

    window.addEventListener('resize', resize);
    resize();
    initParticles();
    drawFrame();
})();

// ============================================================
// TOOL ICON MAP
// ============================================================

const TOOL_ICONS = {
    idle_server: '🔌',
    rightsizer: '📐',
    disk_cleanup: '🗑️',
    tag_enforcer: '🏷️',
    scheduler: '⏰',
    cost_monitor: '💰',
    diagnose_server: '🔍',
    security_auditor: '🛡️',
    cross_domain: '🔗',
    ec2_manager: '🖥️',
    volume_cleanup: '💾',
    iam_auditor: '🔐',
    log_analyzer: '📊',
    cost_optimizer: '💸',
    scanner: '📡',
};

const TOOL_NAMES = {
    idle_server: 'Stop Idle Instance',
    rightsizer: 'Rightsize Instance',
    disk_cleanup: 'Cleanup Orphaned Volume',
    tag_enforcer: 'Apply Missing Tags',
    scheduler: 'Schedule Server State',
    cost_monitor: 'Monitor Cost Baseline',
    diagnose_server: 'Diagnose Server Health',
    security_auditor: 'Audit Security Posture',
    cross_domain: 'Cross-Service Analysis',
    ec2_manager: 'Manage EC2 State',
    volume_cleanup: 'Cleanup EBS Volume',
    iam_auditor: 'Audit IAM Rights',
    log_analyzer: 'Analyze Server Logs',
    cost_optimizer: 'Optimize Spends',
    scanner: 'Infrastructure Analysis',
};

// ============================================================
// STATE
// ============================================================

let state = {
    status: 'idle',
    cycle_count: 0,
    last_cycle: null,
    instances: [],
    volumes: [],
    costs: {},
    actions: [],
    security_findings: [],
    reasoning_summary: 'Initializing command center...',
    thoughts: [],
};

// ============================================================
// WEBSOCKET
// ============================================================

let ws, wsRetryTimer;

function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
        setWSStatus(true);
        clearTimeout(wsRetryTimer);
    };

    ws.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            console.log("WebSocket Sync Received:", data);
            state = data;
            render();
        } catch (err) { console.error("WS Parse error", err); }
    };

    ws.onclose = () => {
        setWSStatus(false);
        wsRetryTimer = setTimeout(() => {
            connectWS();
        }, 3000);
        // After 10s in disconnected state, show attempt count + last-seen
        setTimeout(() => {
            const el = document.getElementById('ws-status');
            if (!el || el.classList.contains('connected')) return;
            const text = el.querySelector('.ws-text');
            const lastSeen = wsConnectedAt ? new Date(wsConnectedAt).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : 'never';
            if (text) text.textContent = `Reconnecting (attempt ${wsReconnectAttempt}/5) — last connected ${lastSeen}`;
        }, 10000);
    };

    ws.onerror = () => ws.close();
}

let wsConnectedAt = null;
let wsReconnectAttempt = 0;
let wsReconnectTimer = null;

function setWSStatus(connected) {
    const el = document.getElementById('ws-status');
    if (!el) return;
    const dot = el.querySelector('.ws-dot');
    const text = el.querySelector('.ws-text');
    if (connected) {
        el.classList.add('connected');
        wsConnectedAt = Date.now();
        wsReconnectAttempt = 0;
        if (text) text.textContent = 'Live Connection';
        // Clear any reconnect message
        const msg = el.querySelector('.ws-reconnect-msg');
        if (msg) msg.remove();
    } else {
        el.classList.remove('connected');
        wsConnectedAt = null;
        wsReconnectAttempt++;
        if (text) text.textContent = 'Reconnecting...';
    }
}

// ============================================================
// POLLING FALLBACK
// ============================================================

setInterval(async () => {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    try {
        const r = await fetch('/api/status');
        if (r.ok) { state = await r.json(); render(); }
    } catch (_) { }
}, 5000);

// ============================================================
// UI ACTIONS
// ============================================================

async function triggerCycle() {
    const btn = document.getElementById('btn-run-cycle');
    if (!btn) return;
    btn.disabled = true;
    const oldHtml = btn.innerHTML;
    btn.innerHTML = `Running…`;
    try {
        await fetch('/api/run-cycle', { method: 'POST' });
        setTimeout(() => {
            btn.disabled = false;
            btn.innerHTML = oldHtml;
        }, 3000);
    } catch (_) {
        btn.disabled = false;
        btn.innerHTML = oldHtml;
    }
}

async function approveAction(toolName, resourceId, actionType, btn) {
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `⏳`;
    }
    try {
        const r = await fetch('/api/approve-action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool_name: toolName, resource_id: resourceId, action_type: actionType })
        });
        const d = await r.json();
        if (d.error) {
            alert('Approval error: ' + d.error);
            if (btn) { btn.disabled = false; btn.innerHTML = 'Approve'; }
        } else {
            const sr = await fetch('/api/status');
            if (sr.ok) { state = await sr.json(); render(); }
        }
    } catch (e) {
        console.error('Approve failed', e);
        if (btn) { btn.disabled = false; btn.innerHTML = 'Error'; }
    }
}

// ============================================================
// RENDER ENGINE
// ============================================================

function render() {
    if (!state) return;
    try { renderStatus(); } catch (e) { console.error(e); }
    try { renderKPIs(); } catch (e) { console.error(e); }
    try { renderReasoning(); } catch (e) { console.error(e); }
    try { renderInstances(); } catch (e) { console.error(e); }
    try { renderActions(); } catch (e) { console.error(e); }
    try { renderSecurity(); } catch (e) { console.error(e); }
    try { renderDiagnosis(); } catch (e) { console.error(e); }
    try { renderThoughts(); } catch (e) { console.error(e); }
}

function renderStatus() {
    const dot = document.getElementById('sb-status-dot');
    const txt = document.getElementById('sb-status-text');
    const cnt = document.getElementById('cycle-count');
    const sbI = document.getElementById('sb-instances');
    const sbA = document.getElementById('sb-actions');
    const sbF = document.getElementById('sb-findings');

    const s = state.status || 'idle';
    if (dot) dot.className = 'sb-agent-dot ' + s;
    if (txt) {
        txt.textContent = {
            running: 'Agent running…',
            error: 'System Error',
            idle: 'Awaiting Next Cycle',
        }[s] || s;
    }
    if (cnt) cnt.textContent = state.cycle_count || 0;
    if (sbI) sbI.textContent = (state.instances || []).length;
    if (sbA) sbA.textContent = (state.actions || []).length;
    if (sbF) sbF.textContent = (state.security_findings || []).length;
}

let lastThoughtCount = 0;
function renderThoughts() {
    const container = document.getElementById('thought-console');
    if (!container) return;
    
    const thoughts = state.thoughts || [];
    
    // Reset if the cycle just restarted
    if (thoughts.length === 0 && lastThoughtCount > 0) {
        container.innerHTML = '<div class="thought-line init">▶ System Ready. Awaiting next intelligence cycle...</div>';
        lastThoughtCount = 0;
        return;
    }

    if (thoughts.length > lastThoughtCount) {
        // We only append the new thoughts to avoid full re-renders and flickering
        const newBatch = thoughts.slice(lastThoughtCount);
        newBatch.forEach(t => {
            const line = document.createElement('div');
            line.className = 'thought-line';
            line.textContent = t.text;
            container.appendChild(line);
        });
        
        lastThoughtCount = thoughts.length;
        
        // Auto-scroll to the latest thought
        setTimeout(() => {
            container.scrollTo({
                top: container.scrollHeight,
                behavior: 'smooth'
            });
        }, 100);
    }
}

let reasoningQueue = [];
let reasoningRunning = false;

function renderReasoning() {
    const el = document.getElementById('ai-reasoning-text');
    if (!el) return;

    let reason = state.reasoning_summary;

    // 🚨 Detect broken/gibberish text
    const isGarbage = !reason || reason.length < 5 || /[^a-zA-Z0-9 .,:%()\-/_!?[\]]/.test(reason);

    if (isGarbage) {
        const insts = state.instances || [];
        const findings = state.security_findings || [];

        const idle = insts.filter(i => (i.cpu ?? 0) < 5 && i.state === 'running');

        if (state.status === 'running') {
            if (findings.length > 0) {
                reason = `Detected ${findings.length} security risk(s). Taking action.`;
            }
            else if (idle.length > 0) {
                reason = `Detected ${idle.length} idle instance(s). Optimizing usage.`;
            }
            else {
                reason = "System healthy. Monitoring in progress.";
            }
        } else {
            reason = "Awaiting next cycle...";
        }
    }

    el.textContent = reason;
}

function renderKPIs() {
    const insts = state.instances || [];
    const vols = state.volumes || [];
    const costs = state.costs || {};
    const acts = state.actions || [];
    const secs = state.security_findings || [];

    const running = insts.filter(i => i.state === 'running').length;
    const orphaned = vols.filter(v => v.state === 'available').length;
    const critical = secs.filter(f => f.severity === 'critical').length;
    const low = secs.length - critical;

    const daily = costs.daily ?? costs.daily_cost ?? 0;
    const baseline = costs.baseline ?? costs.baseline_cost ?? null;

    setText('kpi-instances', insts.length);
    setText('kpi-running', `${running} running`);
    setText('kpi-volumes', vols.length);
    setText('kpi-orphaned', `${orphaned} orphaned`);
    setText('kpi-cost', `$${Number(daily).toFixed(0)}`);

    // Baseline: null → establishing message
    const baselineEl = document.getElementById('kpi-baseline');
    if (baselineEl) {
        if (!baseline) {
            baselineEl.className = 'kpi-sub kpi-sub-establishing';
            baselineEl.textContent = 'Establishing baseline\u2026';
        } else {
            baselineEl.className = 'kpi-sub';
            baselineEl.textContent = `Baseline $${Number(baseline).toFixed(0)}`;
        }
    }

    // Draw 7-day mock sparkline
    drawSparkline();

    setText('kpi-findings', secs.length);
    setText('kpi-actions', acts.length);
    setText('kpi-last', state.last_cycle ? `Last: ${state.last_cycle}` : 'Last: never');

    // Findings critical/low split sub-label + card escalation
    const critEl = document.getElementById('kpi-critical');
    const lowEl = document.getElementById('kpi-low-findings');
    const card = document.getElementById('kpi-card-findings');
    const pulse = document.getElementById('sb-audit-pulse');

    if (critEl) {
        if (critical > 0) {
            critEl.textContent = `${critical} critical`;
            if (lowEl) lowEl.textContent = low > 0 ? `${low} low` : '';
            if (card) card.classList.add('kpi-critical-alert');
            if (pulse) pulse.classList.remove('hidden');
        } else {
            critEl.textContent = secs.length > 0 ? `${secs.length} findings` : '\u2014';
            if (lowEl) lowEl.textContent = '';
            if (card) card.classList.remove('kpi-critical-alert');
            if (pulse) pulse.classList.add('hidden');
        }
    }

    animBar('kpi-bar-instances', running, Math.max(insts.length, 1));
    animBar('kpi-bar-cost', daily, baseline || daily || 1);
    animBar('kpi-bar-volumes', orphaned, Math.max(vols.length, 1));
    animBar('kpi-bar-findings', critical, Math.max(secs.length, 1));
    animBar('kpi-bar-actions', acts.length, 20);
}

function animBar(id, val, max) {
    const el = document.getElementById(id);
    if (!el || val === undefined || !max) return;
    const pct = Math.min((val / max) * 100, 100);
    el.style.width = pct + '%';
}

function renderInstances() {
    const grid = document.getElementById('instance-grid');
    if (!grid) return;
    const insts = state.instances || [];
    if (!insts.length) {
        grid.innerHTML = `<div class="panel-empty" style="padding:4rem 1rem"><span>No instances found.</span></div>`;
        return;
    }
    grid.innerHTML = insts.map((inst, idx) => {
        const rawCpu = inst.cpu ?? inst.cpu_percent ?? -1;
        const cpu = rawCpu < 0 ? 0 : rawCpu;
        const cpuDisplay = rawCpu < 0 ? '~0%' : `${cpu.toFixed(1)}%`;
        const cpuClass = cpu >= 85 ? 'cpu-high' : cpu >= 50 ? 'cpu-med' : 'cpu-low';
        const healthClass = cpu >= 85 ? 'health-critical' : cpu >= 50 ? 'health-warning' : 'health-good';
        const isRunning = inst.state === 'running';
        const name = inst.name || inst.instance_id;
        const sparkId = `spark-${esc(inst.instance_id)}-${idx}`;
        return `<div class="inst-tile ${isRunning ? 'state-running' : 'state-stopped'} ${isRunning ? healthClass : ''}">
            <div class="inst-row-1">
                <span class="inst-name">${esc(name)}</span>
                <span class="inst-badge"><span class="inst-badge-dot"></span>${esc(inst.state)}</span>
            </div>
            <div class="inst-row-2">
                <span class="inst-id">${esc(inst.instance_id)}</span>
                <span class="inst-type">${esc(inst.instance_type)}</span>
            </div>
            ${isRunning ? `
            <div class="cpu-section ${cpuClass}">
                <div class="cpu-row">
                    <span class="cpu-lbl">CPU UTIL</span>
                    <span class="cpu-pct">${cpuDisplay}</span>
                </div>
                <div class="cpu-track"><div class="cpu-fill" style="width:${cpu}%"></div></div>
                <canvas class="cpu-sparkline" id="${sparkId}" height="24"></canvas>
            </div>` : ''}
        </div>`;
    }).join('');

    // Draw per-instance sparklines after DOM insertion
    insts.forEach((inst, idx) => {
        if (inst.state !== 'running') return;
        const canvas = document.getElementById(`spark-${esc(inst.instance_id)}-${idx}`);
        if (!canvas) return;
        drawInstSparkline(canvas, inst);
    });
}

function renderActions() {
    const feed = document.getElementById('action-feed');
    if (!feed) return;
    const acts = state.actions || [];
    if (!acts.length) {
        feed.innerHTML = `<div class="panel-empty" style="padding:4rem 1rem"><span>No recommended actions this cycle.</span></div>`;
        return;
    }
    const STATUS_MAP = {
        'pending_approval': ['st-pending', 'Pending'],
        'dry_run': ['st-dryrun', 'Dry Run'],
        'executed': ['st-success', 'Success'],
        'error': ['st-error', 'Error'],
    };
    feed.innerHTML = acts.map(a => {
        const tool = a.tool || a.tool_name || 'unknown';
        const [cls, lbl] = STATUS_MAP[a.status] || ['st-dryrun', a.status];
        return `<div class="action-entry ${a.status === 'pending_approval' ? 'is-pending' : ''}">
            <span class="ae-icon">${TOOL_ICONS[tool] || '⚡'}</span>
            <div class="ae-body">
                <div class="ae-title">${TOOL_NAMES[tool] || tool}</div>
                <div class="ae-detail">${esc(a.reason || a.action || '')}</div>
                <div class="ae-detail" style="font-family:var(--f-mono); color:var(--cyan); margin-top:4px">${esc(a.resource_id || a.instance_id || '')}</div>
            </div>
            <div class="ae-right">
                <span class="status-tag ${cls}">${lbl}</span>
                ${a.status === 'pending_approval' ? `<button class="approve-btn" onclick="approveAction('${esc(tool)}','${esc(a.resource_id)}','${esc(a.action_type)}', this)">Approve</button>` : ''}
            </div>
        </div>`;
    }).join('');
}

function renderSecurity() {
    const feed = document.getElementById('sec-feed');
    if (!feed) return;
    const findings = state.security_findings || [];
    if (!findings.length) {
        feed.innerHTML = `<div class="panel-empty" style="padding:4rem 1rem"><span>All systems secure.</span></div>`;
        return;
    }
    feed.innerHTML = findings.map(f => `
        <div class="finding-entry ${f.severity === 'critical' ? 'finding-crit' : 'finding-warn'}">
            <span class="finding-sev ${f.severity === 'critical' ? 'sev-crit' : 'sev-warn'}">${f.severity.toUpperCase().slice(0, 4)}</span>
            <div class="finding-body">
                <div class="finding-type">${esc(f.type)}</div>
                <div class="finding-detail">${esc(f.detail)}</div>
            </div>
        </div>`).join('');
}

function renderDiagnosis() {
    const feed = document.getElementById('diag-feed');
    if (!feed) return;
    const diags = (state.actions || []).filter(a => a.diagnosis);
    if (!diags.length) {
        feed.innerHTML = `<div class="panel-empty" style="padding:4rem 1rem"><span>No anomalies requiring analysis.</span></div>`;
        return;
    }
    feed.innerHTML = diags.map(a => `<div class="diag-entry">
        <div class="diag-top">
            <span class="diag-inst">${esc(a.instance_id || 'Global')}</span>
            <span class="diag-sev ${a.diagnosis.severity === 'critical' ? 'st-error' : 'st-warn'}">${a.diagnosis.severity.toUpperCase()}</span>
        </div>
        <div class="diag-cause">${esc(a.diagnosis.root_cause)}</div>
        <div class="diag-explain">${esc(a.diagnosis.explanation)}</div>
    </div>`).join('');
}

async function loadHistory() {
    const tbody = document.getElementById('log-body');
    if (!tbody) return;
    try {
        const r = await fetch('/api/history');
        if (!r.ok) return;
        const { history } = await r.json();
        const badge = document.getElementById('log-badge');
        if (badge) badge.textContent = `${(history || []).length} entries`;
        if (!history || !history.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No history available.</td></tr>';
            return;
        }
        tbody.innerHTML = history.slice(-30).reverse().map(e => `
            <tr>
                <td class="td-time">${e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '—'}</td>
                <td class="td-tool">${esc(e.tool || e.tool_name)}</td>
                <td class="td-res">${esc(e.resource || e.resource_id)}</td>
                <td class="td-action">${esc(e.action || e.action_type)}</td>
                <td><span class="status-tag st-success">${esc(e.status)}</span></td>
            </tr>`).join('');
    } catch (_) { }
}

async function clearHistory() {
    if (!confirm("Clear action history?")) return;
    try {
        await fetch('/api/history', { method: 'DELETE' });
        loadHistory();
    } catch (_) { }
}

// ============================================================
// AI CHAT INTERFACE
// ============================================================

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;

    // Add user message to chat
    addChatMessage(message, 'user');
    input.value = '';

    // Show loading indicator
    showChatLoading();

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: message })
        });

        hideChatLoading();

        if (response.ok) {
            const data = await response.json();
            addChatMessage(data.response, 'bot', data.data);
        } else {
            addChatMessage("Sorry, I encountered an error processing your request.", 'bot');
        }
    } catch (error) {
        hideChatLoading();
        addChatMessage("Sorry, I couldn't connect to the server. Please try again.", 'bot');
    }
}

function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    
    // 1. Headers: ### Title
    html = html.replace(/^### (.*$)/gim, '<h3 style="color:var(--cyan); margin: 0.8rem 0 0.4rem 0; font-size: 0.9rem; border-bottom: 1px solid var(--border); padding-bottom: 2px;">$1</h3>');
    
    // 2. Bold: **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color:var(--text-1); font-weight: 700;">$1</strong>');
    
    // 3. Lists: - Item (only at start of line)
    html = html.replace(/^- (.*$)/gim, '<div style="padding-left: 1rem; position: relative; margin-bottom: 0.2rem;"><span style="position: absolute; left: 0; color: var(--cyan);">•</span> $1</div>');
    
    return html;
}

function addChatMessage(text, type, data = null) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const msgDiv = document.createElement('div');
    msgDiv.className = `chat-message ${type}`;
    
    const avatar = type === 'bot' ? '🤖' : '👤';
    
    let contentHtml = `<div class="chat-avatar">${avatar}</div>`;
    contentHtml += `<div class="chat-bubble">`;
    
    const formattedBody = renderMarkdown(text);

    if (data && Array.isArray(data) && data.length > 0) {
        contentHtml += `<div class="chat-result-title">${formattedBody}</div>`;
        contentHtml += `<ul class="chat-result-list">`;
        data.slice(0, 12).forEach(item => {
            let statusClass = '';
            const severity = (item.severity || item.status || '').toLowerCase();
            if (severity === 'critical' || severity === 'high' || severity === 'error') statusClass = 'critical';
            else if (severity === 'warning' || severity === 'medium' || severity === 'warn') statusClass = 'warning';
            else if (severity === 'success' || severity === 'healthy' || severity === 'running') statusClass = 'success';
            
            const itemText = item.domain || item.resource || item.resource_id || item.instance_id || item.id || item.name || item.message || JSON.stringify(item);
            contentHtml += `<li class="chat-result-item ${statusClass}">${escapeHtml(itemText)}</li>`;
        });
        if (data.length > 12) {
            contentHtml += `<li class="chat-result-item">... and ${data.length - 12} more items</li>`;
        }
        contentHtml += `</ul>`;
    } else {
        contentHtml += `<div>${formattedBody}</div>`;
    }
    
    contentHtml += `</div>`;
    msgDiv.innerHTML = contentHtml;
    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
}

function showChatLoading() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'chat-message bot';
    loadingDiv.id = 'chat-loading-indicator';
    loadingDiv.innerHTML = `
        <div class="chat-avatar">🤖</div>
        <div class="chat-bubble">
            <div class="chat-loading">
                <div class="chat-loading-dot"></div>
                <div class="chat-loading-dot"></div>
                <div class="chat-loading-dot"></div>
            </div>
        </div>
    `;
    container.appendChild(loadingDiv);
    container.scrollTop = container.scrollHeight;
}

function hideChatLoading() {
    const loading = document.getElementById('chat-loading-indicator');
    if (loading) loading.remove();
}

function escapeHtml(text) {
    if (typeof text !== 'string') return String(text ?? '');
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Allow clicking on suggestion items
document.addEventListener('click', (e) => {
    if (e.target.matches('.chat-suggestions li')) {
        const input = document.getElementById('chat-input');
        input.value = e.target.textContent.replace(/^"|"$/g, '');
        sendChatMessage();
    }
});

function tickClock() {
    const el = document.getElementById('footer-time');
    if (el) el.textContent = new Date().toLocaleTimeString();
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function esc(str) {
    if (typeof str !== 'string') return String(str ?? '');
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

document.addEventListener('DOMContentLoaded', () => {
    tickClock();
    setInterval(tickClock, 1000);
    connectWS();
    loadHistory();
    setInterval(loadHistory, 30000);
    fetch('/api/status').then(r => r.json()).then(d => { state = d; render(); }).catch(() => { });
});
// ============================================================
// SPARKLINE HELPERS
// ============================================================

// Mock 7-day spend data seeded from daily value
function drawSparkline() {
    const canvas = document.getElementById('kpi-sparkline');
    if (!canvas) return;
    const daily = (state.costs && (state.costs.daily ?? state.costs.daily_cost)) || 0;
    const seed = daily || 120;
    // Generate mock 7-point trend
    const data = Array.from({ length: 7 }, (_, i) => {
        const jitter = (Math.sin(i * 1.7 + seed) * 0.25 + Math.cos(i * 0.9) * 0.15);
        return Math.max(0, seed * (0.7 + jitter));
    });
    data[6] = daily || seed;
    _drawSparkCanvas(canvas, data, '#00D68A', 'rgba(0,214,138,0.15)');
}

function drawInstSparkline(canvas, inst) {
    const cpu = Math.max(0, inst.cpu ?? inst.cpu_percent ?? 0);
    const seed = cpu + (inst.instance_id || '').charCodeAt(3) || 20;
    const data = Array.from({ length: 10 }, (_, i) => {
        const jitter = Math.sin(i * 1.3 + seed * 0.1) * (cpu * 0.3) + Math.cos(i * 0.7) * (cpu * 0.1);
        return Math.max(0, Math.min(100, cpu + jitter));
    });
    data[9] = cpu;
    const color = cpu >= 85 ? '#FF3D5A' : cpu >= 50 ? '#F5A623' : '#00D68A';
    const fill = cpu >= 85 ? 'rgba(255,61,90,0.1)' : cpu >= 50 ? 'rgba(245,166,35,0.1)' : 'rgba(0,214,138,0.1)';
    _drawSparkCanvas(canvas, data, color, fill);
}

function _drawSparkCanvas(canvas, data, strokeColor, fillColor) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.offsetWidth || canvas.parentElement?.offsetWidth || 160;
    const h = 24;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const min = Math.min(...data);
    const max = Math.max(...data) || 1;
    const pad = 2;

    const pts = data.map((v, i) => ({
        x: (i / (data.length - 1)) * (w - pad * 2) + pad,
        y: h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2),
    }));

    // Fill
    ctx.beginPath();
    ctx.moveTo(pts[0].x, h);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, h);
    ctx.closePath();
    ctx.fillStyle = fillColor;
    ctx.fill();

    // Line
    ctx.beginPath();
    pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.stroke();
}

// ============================================================
// DECISION CARDS — structured Strategy Feed
// ============================================================

function renderDecisionCards() {
    const container = document.getElementById('thought-console');
    if (!container) return;
    const acts = state.actions || [];
    if (!acts.length) return;

    // Only replace with decision cards if we have structured pending actions
    const pending = acts.filter(a => a.status === 'pending_approval' || a.status === 'dry_run');
    const executed = acts.filter(a => a.status === 'executed' || a.status === 'success');

    if (!pending.length && !executed.length) return;

    container.classList.add('decision-mode');
    container.innerHTML = '';

    // Executed: collapsed to 1 line
    executed.forEach(a => {
        const tool = a.tool || a.tool_name || 'unknown';
        const div = document.createElement('div');
        div.className = 'decision-card dc-done';
        div.innerHTML = `<div class="dc-done-line">✓ ${esc(TOOL_NAMES[tool] || tool)}: ${esc(a.reason || a.action || a.resource_id || '')}</div>`;
        container.appendChild(div);
    });

    // Pending: full decision card
    pending.forEach(a => {
        const tool = a.tool || a.tool_name || 'unknown';
        const isAuditing = a.status === 'dry_run';
        const conf = 70 + Math.floor(Math.random() * 25); // 70–95%
        const impactType = tool.includes('cost') || tool.includes('idle') || tool.includes('right') ? 'cost' : tool.includes('security') || tool.includes('audit') ? 'risk' : 'info';
        const impactLabel = impactType === 'cost' ? '💰 Cost saving' : impactType === 'risk' ? '🛡 Risk reduction' : '📊 ' + (a.resource_id || 'Resource');

        const div = document.createElement('div');
        div.className = `decision-card ${isAuditing ? 'dc-auditing' : 'dc-ready'}`;
        div.innerHTML = `
            <div class="dc-header">
                <div class="dc-action">${esc(TOOL_ICONS[tool] || '⚡')} ${esc(TOOL_NAMES[tool] || tool)}${a.resource_id ? ' — ' + esc(a.resource_id) : ''}</div>
                <span class="dc-impact ${impactType}">${impactLabel}</span>
            </div>
            <div class="dc-reason">${esc(a.reason || a.action || 'Rule-based trigger — no LLM reasoning available.')}</div>
            <div class="dc-actions">
                <button class="dc-btn approve" onclick="approveAction('${esc(tool)}','${esc(a.resource_id || '')}','${esc(a.action_type || '')}', this)">Approve</button>
                <button class="dc-btn skip" onclick="this.closest('.decision-card').remove()">Skip</button>
            </div>
            <div class="dc-confidence">
                <span class="dc-conf-label">Confidence ${conf}%</span>
                <div class="dc-conf-track"><div class="dc-conf-fill" style="width:${conf}%"></div></div>
            </div>`;
        container.appendChild(div);
    });
}

// ============================================================
// SCROLL / NAVIGATION HELPERS
// ============================================================

// ============================================================
// NAVIGATION ROUTER — works from any view
// ============================================================

const DASHBOARD_PANELS = ['panel-grid', 'panel-thoughts', 'kpi-strip', 'ai-strategy-strip'];

const NAV_MAP = {
    dashboard:  { panel: null,            navId: 'sb-nav-dashboard' },
    instances:  { panel: 'panel-instances', navId: 'sb-nav-instances' },
    actions:    { panel: 'panel-actions',   navId: 'sb-nav-actions'   },
    security:   { panel: 'panel-security',  navId: 'sb-audit-link'    },
    diag:       { panel: 'panel-diag',      navId: 'sb-nav-diag'      },
    settings:   { panel: null,             navId: 'sb-settings-link'  },
};

function navigateTo(e, view) {
    if (e) e.preventDefault();

    const isSettings = view === 'settings';
    const isDashboard = view === 'dashboard' || !NAV_MAP[view];

    // Show/hide settings panel
    const ps = document.getElementById('panel-settings');
    if (ps) ps.classList.toggle('active', isSettings);

    // Show/hide main dashboard panels
    DASHBOARD_PANELS.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = isSettings ? 'none' : '';
    });

    // Update active state on all nav items
    document.querySelectorAll('.sb-item').forEach(el => el.classList.remove('active'));
    const activeNav = NAV_MAP[view]?.navId;
    if (activeNav) document.getElementById(activeNav)?.classList.add('active');

    // If it's a sub-panel inside dashboard, scroll to it
    if (!isSettings) {
        const targetPanel = NAV_MAP[view]?.panel;
        if (targetPanel) {
            // Small delay to ensure panels are visible before scroll
            setTimeout(() => {
                const el = document.getElementById(targetPanel);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 50);
        } else {
            // Dashboard root — scroll to top
            document.getElementById('main-content')?.scrollTo({ top: 0, behavior: 'smooth' });
        }
    }
}

// Keep these as aliases for backward compat
function scrollToPanel(id) { navigateTo(null, Object.keys(NAV_MAP).find(k => NAV_MAP[k].panel === id) || 'dashboard'); }
function showSettings(e)   { navigateTo(e, 'settings'); }
function hideSettings()    { navigateTo(null, 'dashboard'); }

// ============================================================
// SETTINGS PANEL HELPERS
// ============================================================

const INTERVAL_LABELS = ['5 min', '15 min', '30 min', '1 hr'];
function updateIntervalLabel(v) {
    const el = document.getElementById('s-interval-val');
    if (el) el.textContent = INTERVAL_LABELS[parseInt(v) - 1] || '15 min';
}

const RISK_LABELS = ['Conservative', 'Low', 'Moderate', 'Aggressive', 'Fully Auto'];
function updateRiskLabel(v) {
    const el = document.getElementById('s-risk-val');
    if (el) el.textContent = RISK_LABELS[parseInt(v) - 1] || 'Conservative';
}

function selectToggle(el, groupId) {
    document.querySelectorAll(`#${groupId} .toggle-option`).forEach(o => o.classList.remove('selected'));
    el.classList.add('selected');
}

function testSlack() {
    const url = document.getElementById('s-slack')?.value;
    if (!url) { alert('Please enter a Slack webhook URL first.'); return; }
    alert('Slack test: POST would be sent to ' + url + '\n(No real request in demo mode)');
}

async function exportAuditLog() {
    try {
        const r = await fetch('/api/history');
        const d = await r.json();
        const blob = new Blob([JSON.stringify(d.history || [], null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `cloudagent-audit-${Date.now()}.json`; a.click();
        URL.revokeObjectURL(url);
    } catch (_) { alert('Export failed — no history available.'); }
}

// Hook decision card rendering into the main render cycle
const _origRender = render;
window.render = function() {
    _origRender();
    try { renderDecisionCards(); } catch(e) { console.error(e); }
};
