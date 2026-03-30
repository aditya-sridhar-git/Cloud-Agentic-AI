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

    window.addEventListener('resize', () => { resize(); });
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
};

// ============================================================
// WEBSOCKET
// ============================================================

let ws;
let wsRetryTimer;

function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
        setWSStatus(true);
        clearTimeout(wsRetryTimer);
    };

    ws.onmessage = (e) => {
        try {
            state = JSON.parse(e.data);
            render();
        } catch (_) { }
    };

    ws.onclose = () => {
        setWSStatus(false);
        wsRetryTimer = setTimeout(connectWS, 3000);
    };

    ws.onerror = () => ws.close();
}

function setWSStatus(connected) {
    const el = document.getElementById('ws-status');
    if (!el) return;
    const dot = el.querySelector('.ws-dot');
    const text = el.querySelector('.ws-text');
    if (connected) {
        el.classList.add('connected');
        if (text) text.textContent = 'Connected';
    } else {
        el.classList.remove('connected');
        if (text) text.textContent = 'Disconnected';
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
// TRIGGER CYCLE
// ============================================================

async function triggerCycle() {
    const btn = document.getElementById('btn-run-cycle');
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = `<span class="run-btn-icon">
    <svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="3" stroke="white" stroke-width="1.5" fill="none" stroke-dasharray="12" stroke-dashoffset="0"><animate attributeName="stroke-dashoffset" values="0;-18" dur="0.8s" repeatCount="indefinite"/></circle></svg>
  </span> Running…`;
    try {
        await fetch('/api/run-cycle', { method: 'POST' });
        setTimeout(() => {
            btn.disabled = false;
            btn.innerHTML = `<span class="run-btn-icon"><svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 1.5l6 3.5-6 3.5V1.5z" fill="currentColor"/></svg></span> Run Cycle`;
        }, 3000);
    } catch (_) {
        btn.disabled = false;
        btn.innerHTML = `<span class="run-btn-icon"><svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 1.5l6 3.5-6 3.5V1.5z" fill="currentColor"/></svg></span> Run Cycle`;
    }
}

// ============================================================
// APPROVE ACTION
// ============================================================

async function approveAction(toolName, resourceId, actionType, btn) {
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span style="display:inline-block; animation:spin 1s linear infinite">⏳</span>`;
        btn.style.cursor = 'wait';
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
            if (btn) { btn.disabled = false; btn.innerHTML = 'Approve'; btn.style.cursor = 'pointer'; }
        } else {
            const sr = await fetch('/api/status');
            if (sr.ok) { state = await sr.json(); render(); }
        }
    } catch (e) {
        console.error('Approve failed', e);
        if (btn) { btn.disabled = false; btn.innerHTML = 'Error'; btn.style.cursor = 'pointer'; }
    }
}

// ============================================================
// FOOTER CLOCK
// ============================================================

function tickClock() {
    const el = document.getElementById('footer-time');
    if (el) el.textContent = new Date().toLocaleTimeString();
}

// ============================================================
// MAIN RENDER
// ============================================================

function render() {
    renderStatus();
    renderKPIs();
    renderInstances();
    renderActions();
    renderSecurity();
    renderDiagnosis();
}

// ── Status ────────────────────────────────────────────────────

function renderStatus() {
    const dot = document.getElementById('sb-status-dot');
    const txt = document.getElementById('sb-status-text');
    const cnt = document.getElementById('cycle-count');
    const sbI = document.getElementById('sb-instances');
    const sbA = document.getElementById('sb-actions');
    const sbF = document.getElementById('sb-findings');

    const s = state.status || 'idle';
    if (dot) {
        dot.className = 'sb-agent-dot ' + s;
    }
    if (txt) {
        txt.textContent = {
            running: 'Agent running…',
            error: 'Error detected',
            idle: 'Idle — awaiting cycle',
        }[s] || s;
    }
    if (cnt) cnt.textContent = state.cycle_count || 0;
    if (sbI) sbI.textContent = (state.instances || []).length;
    if (sbA) sbA.textContent = (state.actions || []).length;
    if (sbF) sbF.textContent = (state.security_findings || []).length;
}

// ── KPIs ──────────────────────────────────────────────────────

function renderKPIs() {
    const insts = state.instances || [];
    const vols = state.volumes || [];
    const costs = state.costs || {};
    const acts = state.actions || [];
    const secs = state.security_findings || [];

    const running = insts.filter(i => i.state === 'running').length;
    const orphaned = vols.filter(v => v.state === 'available').length;
    const critical = secs.filter(f => f.severity === 'critical').length;

    const daily = typeof costs.daily === 'number' ? costs.daily
        : typeof costs.daily_cost === 'number' ? costs.daily_cost : null;
    const baseline = typeof costs.baseline === 'number' ? costs.baseline
        : typeof costs.baseline_cost === 'number' ? costs.baseline_cost : null;

    setText('kpi-instances', insts.length || '—');
    setText('kpi-running', `${running} running`);
    setText('kpi-volumes', vols.length || '—');
    setText('kpi-orphaned', `${orphaned} orphaned`);
    setText('kpi-cost', daily !== null ? `$${Number(daily).toFixed(0)}` : '$—');
    setText('kpi-baseline', baseline !== null ? `Baseline $${Number(baseline).toFixed(0)}` : 'Baseline —');
    setText('kpi-findings', secs.length || '—');
    setText('kpi-critical', `${critical} critical`);
    setText('kpi-actions', acts.length || '—');
    setText('kpi-last', state.last_cycle ? `Last: ${state.last_cycle}` : 'Last: never');

    // KPI bar animations (proportional fill)
    animBar('kpi-bar-instances', running, Math.max(insts.length, 1), 100);
    animBar('kpi-bar-cost', daily, baseline || daily, 100);
    animBar('kpi-bar-volumes', orphaned, Math.max(vols.length, 1), 100);
    animBar('kpi-bar-findings', critical, Math.max(secs.length, 1), 100);
    animBar('kpi-bar-actions', acts.length, 60, 100);
}

function animBar(id, val, max, cap) {
    const el = document.getElementById(id);
    if (!el || !val || !max) return;
    const pct = Math.min((val / max) * cap, cap);
    el.style.width = pct + '%';
}

// ── Instances ─────────────────────────────────────────────────

function renderInstances() {
    const insts = state.instances || [];
    const grid = document.getElementById('instance-grid');
    const badge = document.getElementById('inst-badge');
    if (!grid) return;

    if (badge) badge.textContent = `${insts.length} instance${insts.length !== 1 ? 's' : ''}`;

    if (!insts.length) {
        grid.innerHTML = `<div class="panel-empty">
      <svg width="32" height="32" viewBox="0 0 32 32" fill="none" opacity=".3"><rect x="3" y="6" width="26" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M10 26h12M16 22v4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      <span>No instances found</span>
    </div>`;
        return;
    }

    grid.innerHTML = insts.map((inst, idx) => {
        const cpu = typeof inst.cpu === 'number' ? inst.cpu
            : typeof inst.cpu_percent === 'number' ? inst.cpu_percent : 0;
        const cpuClass = cpu >= 80 ? 'cpu-high' : cpu >= 40 ? 'cpu-med' : 'cpu-low';
        const name = inst.name
            || (inst.tags || []).find(t => t.Key === 'Name')?.Value
            || inst.instance_id;
        const isRunning = inst.state === 'running';
        const delay = (idx * 0.05 + 0.3).toFixed(2);

        return `<div class="inst-tile ${isRunning ? 'state-running' : 'state-stopped'}"
                 style="animation: fadeUp 0.4s var(--ease-out-expo) ${delay}s both">
      <div class="inst-row-1">
        <span class="inst-name">${esc(name)}</span>
        <span class="inst-badge">
          <span class="inst-badge-dot"></span>
          ${esc(inst.state)}
        </span>
      </div>
      <div class="inst-row-2">
        <span class="inst-id">${esc(inst.instance_id)}</span>
        <span class="inst-type">${esc(inst.instance_type || '—')}</span>
      </div>
      ${isRunning ? `<div class="cpu-section ${cpuClass}">
        <div class="cpu-row">
          <span class="cpu-lbl">CPU</span>
          <span class="cpu-pct">${cpu.toFixed(1)}%</span>
        </div>
        <div class="cpu-track">
          <div class="cpu-fill" style="width:${Math.min(cpu, 100)}%"></div>
        </div>
      </div>` : ''}
    </div>`;
    }).join('');
}

// ── Actions ───────────────────────────────────────────────────

function renderActions() {
    const acts = state.actions || [];
    const feed = document.getElementById('action-feed');
    const badge = document.getElementById('action-badge');
    if (!feed) return;

    if (badge) badge.textContent = `${acts.length} action${acts.length !== 1 ? 's' : ''}`;

    if (!acts.length) {
        feed.innerHTML = `<div class="panel-empty">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" opacity=".3"><path d="M14 2L4 15h9L10 26l14-16h-9L14 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
      <span>No actions yet this cycle</span>
    </div>`;
        return;
    }

    const STATUS_MAP = {
        'dry_run': ['st-dryrun', 'Dry Run'],
        'stopping': ['st-success', 'Stopping'],
        'stopped': ['st-success', 'Stopped'],
        'tagged': ['st-success', 'Tagged'],
        'cleaned': ['st-success', 'Cleaned'],
        'alert_sent': ['st-warn', 'Alert'],
        'freeze_initiated': ['st-warn', 'Frozen'],
        'error': ['st-error', 'Error'],
        'pending_approval': ['st-pending', 'Pending'],
    };

    feed.innerHTML = acts.map((a, idx) => {
        const tool = a.tool || a.tool_name || 'unknown';
        const icon = TOOL_ICONS[tool] || '⚡';
        const friendlyName = TOOL_NAMES[tool] || tool;
        const resource = a.resource || a.resource_id || a.instance_id || a.volume_id || '';
        const action = a.action || a.action_type || '';
        const reason = a.reason || '';
        const status = a.status || 'unknown';
        const isPending = status === 'pending_approval';
        const [stClass, stLabel] = STATUS_MAP[status] || ['st-dryrun', status];
        const delay = (idx * 0.06).toFixed(2);

        return `<div class="action-entry ${isPending ? 'is-pending' : ''}" style="animation-delay:${delay}s">
      <span class="ae-icon">${icon}</span>
      <div class="ae-body">
        <div class="ae-title">${esc(friendlyName)}</div>
        <div class="ae-detail">
            ${resource && resource !== 'account' ? `<span style="display:inline-block; font-family:var(--f-mono); background:var(--bg-base); padding:0.15rem 0.35rem; border-radius:3px; margin-bottom:0.3rem; border:1px solid var(--border); color:var(--cyan); font-size:0.55rem;">${esc(resource)}</span><br/>` : ''}
            ${reason ? esc(reason) : esc(action)}
        </div>
      </div>
      <div class="ae-right">
        <span class="status-tag ${stClass}">${stLabel}</span>
        ${isPending ? `<button class="approve-btn"
          onclick="approveAction('${esc(tool)}','${esc(resource)}','${esc(action)}', this)">
          Approve
        </button>` : ''}
      </div>
    </div>`;
    }).join('');
}

// ── Security ──────────────────────────────────────────────────

function renderSecurity() {
    const findings = state.security_findings || [];
    const feed = document.getElementById('sec-feed');
    const badge = document.getElementById('sec-badge');
    if (!feed) return;

    const critCount = findings.filter(f => f.severity === 'critical').length;
    if (badge) {
        badge.textContent = `${findings.length} finding${findings.length !== 1 ? 's' : ''}`;
        badge.className = `count-chip${critCount ? ' danger-chip' : ''}`;
    }

    if (!findings.length) {
        feed.innerHTML = `<div class="panel-empty">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" opacity=".3"><path d="M14 2L3 8v8c0 6.5 5 11.5 11 12 6-0.5 11-5.5 11-12V8L14 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
      <span>No security findings — all clear</span>
    </div>`;
        return;
    }

    feed.innerHTML = findings.map((f, idx) => {
        const isCrit = f.severity === 'critical';
        const delay = (idx * 0.06).toFixed(2);
        return `<div class="finding-entry ${isCrit ? 'finding-crit' : 'finding-warn'}" style="animation-delay:${delay}s">
      <span class="finding-sev ${isCrit ? 'sev-crit' : 'sev-warn'}">${isCrit ? 'CRIT' : 'WARN'}</span>
      <div class="finding-body">
        <div class="finding-type">${esc(f.type || '—')}</div>
        <div class="finding-detail">${esc(f.detail || f.description || '')}</div>
        ${f.resource ? `<div class="finding-res">${esc(f.resource)}</div>` : ''}
      </div>
    </div>`;
    }).join('');
}

// ── Diagnosis ─────────────────────────────────────────────────

function renderDiagnosis() {
    const acts = state.actions || [];
    const diags = acts.filter(a => (a.tool || a.tool_name) === 'diagnose_server' && a.diagnosis);
    const feed = document.getElementById('diag-feed');
    if (!feed) return;

    if (!diags.length) {
        feed.innerHTML = `<div class="panel-empty">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" opacity=".3"><circle cx="13" cy="13" r="9" stroke="currentColor" stroke-width="1.5"/><path d="M20 20l5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      <span>No diagnoses this cycle</span>
    </div>`;
        return;
    }

    const SEV_CLASS = {
        critical: 'st-error',
        warning: 'st-warn',
        info: 'st-pending',
    };

    feed.innerHTML = diags.map((a, idx) => {
        const d = a.diagnosis;
        const sev = d.severity || 'info';
        const sc = SEV_CLASS[sev] || 'st-pending';
        const delay = (idx * 0.06).toFixed(2);

        return `<div class="diag-entry" style="animation-delay:${delay}s">
      <div class="diag-top">
        <span class="diag-inst">${esc(a.instance_id || a.resource || '—')}</span>
        <span class="diag-sev ${sc}">${sev.toUpperCase()}</span>
      </div>
      <div class="diag-cause">${esc(d.root_cause || '—')}</div>
      <div class="diag-explain">${esc(d.explanation || '')}</div>
      ${d.recommended_action
                ? `<div class="diag-rec">→ ${esc(d.recommended_action)}</div>`
                : ''}
    </div>`;
    }).join('');
}

// ── History ───────────────────────────────────────────────────

async function loadHistory() {
    try {
        const r = await fetch('/api/history');
        if (!r.ok) return;
        const { history } = await r.json();
        const tbody = document.getElementById('log-body');
        const badge = document.getElementById('log-badge');
        if (!tbody || !history?.length) return;

        if (badge) badge.textContent = `${history.length} entr${history.length !== 1 ? 'ies' : 'y'}`;

        const STATUS_CHIP = {
            'dry_run': ['st-dryrun', 'Dry Run'],
            'stopping': ['st-success', 'Stopping'],
            'stopped': ['st-success', 'Stopped'],
            'tagged': ['st-success', 'Tagged'],
            'cleaned': ['st-success', 'Cleaned'],
            'alert_sent': ['st-warn', 'Alert'],
            'freeze_initiated': ['st-warn', 'Frozen'],
            'error': ['st-error', 'Error'],
            'pending_approval': ['st-pending', 'Pending'],
        };

        tbody.innerHTML = history.slice(-50).reverse().map(entry => {
            const ts = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '—';
            const st = entry.status || 'unknown';
            const [cls, label] = STATUS_CHIP[st] || ['st-dryrun', st];
            return `<tr>
        <td class="td-time">${ts}</td>
        <td class="td-tool">${esc(entry.tool || '—')}</td>
        <td class="td-res">${esc(entry.resource || entry.resource_id || '—')}</td>
        <td class="td-action">${esc(entry.action || entry.action_type || '—')}</td>
        <td><span class="status-tag ${cls}">${label}</span></td>
      </tr>`;
        }).join('');
    } catch (_) { }
}

async function clearHistory() {
    if (!confirm("Are you sure you want to completely clear the Action History?")) return;
    try {
        await fetch('/api/history', { method: 'DELETE' });
        loadHistory();
    } catch (e) {
        console.error("Failed to clear history", e);
    }
}

// ============================================================
// UTILITIES
// ============================================================

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function esc(str) {
    if (typeof str !== 'string') str = String(str ?? '');
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ============================================================
// INIT
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    // Footer clock
    tickClock();
    setInterval(tickClock, 1000);

    // Connect WebSocket
    connectWS();

    // Load audit history
    loadHistory();
    setInterval(loadHistory, 30_000);

    // Initial REST state fetch
    fetch('/api/status')
        .then(r => r.json())
        .then(d => { state = d; render(); })
        .catch(() => { });
});