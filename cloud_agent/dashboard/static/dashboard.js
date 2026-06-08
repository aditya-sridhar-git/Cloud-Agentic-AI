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
    idle_server: 'Idle',
    rightsizer: 'Size',
    disk_cleanup: 'Disk',
    tag_enforcer: 'Tag',
    scheduler: 'Time',
    cost_monitor: 'Cost',
    diagnose_server: 'Diag',
    security_auditor: 'Risk',
    cross_domain: 'Link',
    ec2_manager: 'EC2',
    volume_cleanup: 'Vol',
    iam_auditor: 'IAM',
    log_analyzer: 'Log',
    cost_optimizer: 'Save',
    scanner: 'Scan',
    query_optimizer: 'SQL',
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
    query_optimizer: 'Optimize Slow Queries',
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
    query_optimizations: {},
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
        setTimeout(() => {
            const el = document.getElementById('ws-status');
            if (!el || el.classList.contains('connected')) return;
            const text = el.querySelector('.ws-text');
            const lastSeen = wsConnectedAt ? new Date(wsConnectedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'never';
            if (text) text.textContent = `Reconnecting (attempt ${wsReconnectAttempt}/5) - last connected ${lastSeen}`;
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
    try {
        const confRes = await fetch('/api/confidence-metrics');
        if (confRes.ok) { window._confidenceMetrics = await confRes.json(); }
    } catch (_) {}

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
    btn.innerHTML = `Running...`;
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
    try { renderVolumeGrid(); } catch (e) { console.error(e); }
    try { renderCostBreakdown(); } catch (e) { console.error(e); }
    try { renderQueryOptimizer(); } catch (e) { console.error(e); }
    try { renderActions(); } catch (e) { console.error(e); }
    try { renderSecurity(); } catch (e) { console.error(e); }
    try { renderDiagnosis(); } catch (e) { console.error(e); }
    try { renderThoughts(); } catch (e) { console.error(e); }
}

// ============================================================
// COST BREAKDOWN PANEL
// ============================================================

function renderCostBreakdown() {
    const panel = document.getElementById('cost-breakdown-panel');
    if (!panel) return;
    const costs = state.costs || {};
    const services = costs.services || [];
    const daily = costs.current_daily ?? 0;
    const baseline = costs.baseline_daily ?? 0;
    const deltaPct = costs.delta_pct ?? 0;

    if (!services.length) {
        panel.innerHTML = `
        <div class="panel-empty">
            <svg width="48" height="48" viewBox="0 0 28 28" fill="none" opacity=".4">
                <circle cx="13" cy="13" r="11" stroke="currentColor" stroke-width="1.5" />
                <path d="M13 8v5M16 13h-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
            </svg>
            <span>Cost analysis pending</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">Run a cycle to collect cost data and establish baselines</span>
        </div>`;
        return;
    }

    const spikeClass = deltaPct > 20 ? 'cost-spike' : deltaPct > 0 ? 'cost-elevated' : 'cost-normal';
    const totalFromServices = services.reduce((s, x) => s + x.amount, 0);

    const sortedServices = services.slice().sort((a, b) => Number(b.amount || 0) - Number(a.amount || 0));
    const topService = sortedServices[0];
    let html = `<div class="cost-dashboard ${spikeClass}">
        <div class="cost-hero">
            <div>
                <span class="cost-eyebrow">Daily spend</span>
                <strong>${formatCurrency(daily)}</strong>
                <span class="cost-note">Projected ${formatCurrency(daily * 30)} monthly</span>
            </div>
            <div class="cost-delta-card ${deltaPct > 0 ? 'delta-up' : 'delta-down'}">
                <span>${deltaPct >= 0 ? '+' : ''}${Number(deltaPct).toFixed(1)}%</span>
                <small>vs baseline ${formatCurrency(baseline)}</small>
            </div>
            <div class="cost-top-service">
                <span>Top service</span>
                <strong>${esc(topService?.service || 'No service data')}</strong>
                <small>${topService ? `${formatCurrency(topService.amount)} today` : 'Awaiting Cost Explorer'}</small>
            </div>
        </div>
        <div class="cost-service-list">`;

    sortedServices.slice(0, 10).forEach(svc => {
        const share = totalFromServices > 0 ? ((svc.amount / totalFromServices) * 100).toFixed(1) : '0.0';
        const barW = Math.min((svc.amount / (totalFromServices || 1)) * 100, 100);
        html += `<div class="cost-service-row">
            <div class="cost-service-main">
                <span class="cost-svc">${esc(svc.service)}</span>
                <strong>${formatCurrency(svc.amount)}</strong>
            </div>
            <div class="cost-service-meta">
                <span>${share}% share</span>
                <span>${esc(svc.currency || costs.currency || 'USD')}</span>
            </div>
            <div class="cost-bar-track"><div class="cost-bar-fill" style="width:${barW.toFixed(1)}%"></div></div>
        </div>`;
    });

    html += `</div></div>`;

    const costActions = (state.actions || []).filter(a => a.tool_name === 'cost_monitor' && a.status === 'pending_approval');
    if (costActions.length > 0) {
        costActions.forEach(a => {
            html += `<div class="cost-fix-banner">
                <div class="cost-fix-label">Suggested Fix: ${esc(a.action_type.toUpperCase())} — ${esc(a.reason)}</div>
                <button class="approve-btn cost-approve-btn" onclick="approveAction('cost_monitor','${esc(a.resource_id)}','${esc(a.action_type)}', this)">
                    Approve &amp; Apply
                </button>
            </div>`;
        });
    }

    panel.innerHTML = html;
}

function countQuerySuggestions() {
    const result = state.query_optimizations || {};
    return (result.optimizations || []).reduce((sum, db) => {
        return sum + (db.optimizations || []).length;
    }, 0);
}

function renderQueryOptimizer() {
    const feed = document.getElementById('query-feed');
    if (!feed) return;

    const result = state.query_optimizations || {};
    const groups = result.optimizations || [];
    const total = countQuerySuggestions();
    const badge = document.getElementById('query-badge');
    if (badge) badge.textContent = `${total} quer${total === 1 ? 'y' : 'ies'}`;

    if (!Object.keys(result).length) {
        feed.innerHTML = `
        <div class="panel-empty">
            <span>No query scan has run yet</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">Run a cycle or click Analyze after generating RDS query load</span>
        </div>`;
        return;
    }

    if (!groups.length || total === 0) {
        const errors = result.errors || [];
        const piUnavailable = result.pi_unavailable || [];
        const metrics = result.database_metrics || [];
        feed.innerHTML = `
        <div class="query-summary-row">
            <span class="status-tag ${statusClass(result.status)}">${esc(result.status || 'scanned')}</span>
            <span>${esc(result.summary || 'No slow query samples returned yet')}</span>
        </div>
        ${piUnavailable.length ? `<div class="query-errors">${piUnavailable.map(renderPiUnavailable).join('')}</div>` : ''}
        ${metrics.length ? `<div class="query-db-grid">${metrics.map(renderQueryMetricCard).join('')}</div>` : ''}
        ${errors.length ? `<div class="query-errors">${errors.map(e => `<div>${esc(e.db_instance_id)}: ${esc(e.error)}</div>`).join('')}</div>` : ''}`;
        return;
    }

    feed.innerHTML = `
        <div class="query-summary-row">
            <span class="status-tag st-warn">${total} suggestion${total === 1 ? '' : 's'}</span>
            <span>${esc(result.summary || 'Slow query candidates found')}</span>
        </div>
        ${groups.map(group => `
            <div class="query-db-block">
                <div class="query-db-head">
                    <div>
                        <div class="query-db-title">${esc(group.db_instance_id || 'database')}</div>
                        <div class="query-db-sub">${esc(group.engine || 'engine')} · ${esc(group.database_metrics?.instance_class || '')} · ${esc(group.database_metrics?.region || '')}</div>
                    </div>
                    <span class="status-tag ${statusClass(group.optimizations?.[0]?.severity)}">${esc(group.summary || '')}</span>
                </div>
                <div class="query-list">
                    ${(group.optimizations || []).map(renderQueryOptimization).join('')}
                </div>
            </div>
        `).join('')}`;
}

function renderPiUnavailable(item) {
    return `<div class="query-pi-warning">
        <strong>${esc(item.db_instance_id || 'RDS database')}</strong>
        <span>${esc(item.region || '')}${item.dbi_resource_id ? ` · ${esc(item.dbi_resource_id)}` : ''}${item.aws_error_code ? ` · ${esc(item.aws_error_code)}` : ''}</span>
        <p>${esc(item.diagnostic || 'Performance Insights / Database Insights is unavailable.')}</p>
        <p>${esc(item.recommendation || 'Enable PI/Database Insights and grant pi:DescribeDimensionKeys.')}</p>
    </div>`;
}

function renderQueryMetricCard(db) {
    return `<div class="query-metric-card">
        <div class="query-db-title">${esc(db.db_instance_id)}</div>
        <div class="query-db-sub">${esc(db.engine)} · ${esc(db.instance_class)} · ${esc(db.region)}</div>
        <div class="query-metrics">
            <span><b>Read</b>${Number(db.read_latency_ms || 0).toFixed(2)}ms</span>
            <span><b>Write</b>${Number(db.write_latency_ms || 0).toFixed(2)}ms</span>
            <span><b>CPU</b>${Number(db.cpu_percent || 0).toFixed(1)}%</span>
        </div>
    </div>`;
}

function renderQueryOptimization(opt) {
    const indexes = opt.index_suggestions || [];
    const severity = String(opt.severity || 'warning').toLowerCase();
    return `<article class="query-card query-${esc(severity)}">
        <div class="query-card-top">
            <span class="query-severity">${esc(severity.toUpperCase())}</span>
            <span class="query-source">${esc(opt.source || 'Performance Insights')} · load ${Number(opt.db_load || 0).toFixed(3)}</span>
            <span class="query-improvement">${esc(opt.estimated_improvement || 'Improvement estimate pending')}</span>
        </div>
        <div class="query-problem">${esc(opt.problem || opt.explanation || 'Slow query candidate')}</div>
        <div class="query-code-grid">
            <div class="query-code-block">
                <div class="query-code-label">Slow Query</div>
                <pre><code>${esc(opt.original_query || 'SQL text unavailable')}</code></pre>
            </div>
            <div class="query-code-block optimized">
                <div class="query-code-label">Optimized Version</div>
                <pre><code>${esc(opt.optimized_query || 'Manual rewrite recommended')}</code></pre>
            </div>
        </div>
        ${indexes.length ? `<div class="query-indexes">
            <div class="query-code-label">Suggested Indexes</div>
            ${indexes.map(idx => `<pre><code>${esc(idx)}</code></pre>`).join('')}
        </div>` : ''}
        <div class="query-explanation">${esc(opt.explanation || '')}</div>
    </article>`;
}

async function runQueryOptimizerNow(btn) {
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Analyzing...';
    }
    try {
        const resp = await fetch('/api/query-optimizer/run', { method: 'POST' });
        const data = await resp.json();
        state.query_optimizations = data;
        renderQueryOptimizer();
        if (data.error) alert('Query optimizer error: ' + data.error);
    } catch (err) {
        alert('Query optimizer request failed: ' + err.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Analyze';
        }
    }
}

function renderStatus() {
    const dot = document.getElementById('sb-status-dot');
    const txt = document.getElementById('sb-status-text');
    const cnt = document.getElementById('cycle-count');
    const sbI = document.getElementById('sb-instances');
    const sbA = document.getElementById('sb-actions');
    const sbF = document.getElementById('sb-findings');
    const sbQ = document.getElementById('sb-queries');

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
    if (sbQ) sbQ.textContent = countQuerySuggestions();
}

let lastThoughtCount = 0;
function renderThoughts() {
    const container = document.getElementById('thought-console');
    if (!container) return;

    const thoughts = state.thoughts || [];

    if (thoughts.length === 0 && lastThoughtCount > 0) {
        container.innerHTML = '<div class="thought-line init">▶ System Ready. Awaiting next intelligence cycle...</div>';
        lastThoughtCount = 0;
        return;
    }

    if (thoughts.length > lastThoughtCount) {
        const newBatch = thoughts.slice(lastThoughtCount);
        newBatch.forEach(t => {
            const line = document.createElement('div');
            line.className = 'thought-line';
            line.textContent = t.text;
            container.appendChild(line);
        });
        lastThoughtCount = thoughts.length;
        setTimeout(() => {
            container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
        }, 100);
    }
}

let reasoningQueue = [];
let reasoningRunning = false;

function renderReasoning() {
    const el = document.getElementById('ai-reasoning-text');
    if (!el) return;

    let reason = state.reasoning_summary;
    const isGarbage = !reason || reason.length < 5 || /[^a-zA-Z0-9 .,:%()\-/_!?[\]]/.test(reason);

    if (isGarbage) {
        const insts = state.instances || [];
        const findings = state.security_findings || [];
        const idle = insts.filter(i => (i.cpu ?? 0) < 5 && i.state === 'running');

        if (state.status === 'running') {
            if (findings.length > 0) {
                reason = `Detected ${findings.length} security risk(s). Taking action.`;
            } else if (idle.length > 0) {
                reason = `Detected ${idle.length} idle instance(s). Optimizing usage.`;
            } else {
                reason = "System healthy. Monitoring in progress.";
            }
        } else {
            reason = "Awaiting next cycle...";
        }
    }

    el.textContent = reason;

    const confEl = document.getElementById('ai-confidence');
    const tooltipBody = document.getElementById('confidence-metrics-body');
    if (confEl && window._confidenceMetrics) {
        const conf = window._confidenceMetrics.overall_confidence || state.overall_confidence || 0;
        confEl.textContent = `Confidence: ${conf}%`;
        
        let confClass = 'conf-low';
        if (conf >= 85) confClass = 'conf-high';
        else if (conf >= 60) confClass = 'conf-med';
        confEl.className = `ai-confidence ${confClass}`;

        if (tooltipBody && window._confidenceMetrics.metrics) {
            const m = window._confidenceMetrics.metrics;
            let html = '';
            for (const key in m) {
                const metric = m[key];
                let vClass = 'warn';
                if (metric.value >= 85) vClass = 'good';
                else if (metric.value < 60) vClass = 'crit';
                
                html += `<div class="metric-row">
                    <span class="metric-label">${esc(metric.label)}</span>
                    <span class="metric-value ${vClass}">${esc(metric.value)}</span>
                </div>`;
            }
            tooltipBody.innerHTML = html;
        }
    }
}

function renderKPIs() {
    const insts = state.instances || [];
    const vols = state.volumes || [];
    const costs = state.costs || {};
    const acts = state.actions || [];
    const secs = state.security_findings || [];

    const running = insts.filter(i => i.state === 'running').length;
    const orphaned = vols.filter(v => v.state === 'available').length;
    const critical = secs.filter(f => (f.severity || '').toLowerCase() === 'critical').length;
    const low = secs.length - critical;

    const daily = costs.current_daily ?? costs.daily ?? costs.daily_cost ?? 0;
    const baseline = costs.baseline_daily ?? costs.baseline ?? costs.baseline_cost ?? null;
    const deltaPct = costs.delta_pct ?? 0;

    setText('kpi-instances', insts.length);
    setText('kpi-running', `${running} running`);
    setText('kpi-volumes', vols.length);
    setText('kpi-orphaned', `${orphaned} orphaned`);
    const monthlyProjection = Number(daily || 0) * 30;
    setText('kpi-cost', formatCurrency(daily));
    const baselineEl = document.getElementById('kpi-baseline');
    if (baselineEl) {
        if (!baseline) {
            baselineEl.className = 'kpi-sub kpi-sub-establishing';
            baselineEl.textContent = monthlyProjection > 0
                ? `${formatCurrency(monthlyProjection)} projected monthly`
                : 'Establishing baseline...';
        } else {
            baselineEl.className = `kpi-sub ${deltaPct > 15 ? 'kpi-sub-critical' : deltaPct > 0 ? 'kpi-sub-warning' : 'kpi-sub-good'}`;
            baselineEl.textContent = `${deltaPct >= 0 ? '+' : ''}${Number(deltaPct).toFixed(1)}% vs baseline`;
        }
    }

    drawSparkline();
    setText('kpi-findings', secs.length);
    setText('kpi-actions', acts.length);
    setText('kpi-last', state.last_cycle ? `Last: ${state.last_cycle}` : 'Last: never');

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
            critEl.textContent = secs.length > 0 ? `${secs.length} findings` : 'No findings';
            if (lowEl) lowEl.textContent = '';
            if (card) card.classList.remove('kpi-critical-alert');
            if (pulse) pulse.classList.add('hidden');
        }
    }

    animBar('kpi-bar-instances', running, Math.max(insts.length, 1));
    animBar('kpi-bar-cost', Math.max(daily - (baseline || 0), 0), baseline || daily || 1);
    animBar('kpi-bar-volumes', orphaned, Math.max(vols.length, 1));
    animBar('kpi-bar-findings', critical, Math.max(secs.length, 1));
    animBar('kpi-bar-actions', acts.length, 20);
}

function formatCurrency(value) {
    const amount = Number(value || 0);
    if (amount >= 1000) return `$${(amount / 1000).toFixed(1)}k`;
    if (amount >= 100) return `$${amount.toFixed(0)}`;
    return `$${amount.toFixed(2)}`;
}

function animBar(id, val, max) {
    const el = document.getElementById(id);
    if (!el || val === undefined || !max) return;
    const pct = Math.min((val / max) * 100, 100);
    el.style.width = pct + '%';
}

// ============================================================
// FIX 1: renderInstances — updates inst-badge count
// ============================================================

function renderInstances() {
    const grid = document.getElementById('instance-grid');
    if (!grid) return;
    const insts = state.instances || [];

    // ✅ Update badge counter
    const badge = document.getElementById('inst-badge');
    if (badge) badge.textContent = `${insts.length} instance${insts.length !== 1 ? 's' : ''}`;

    if (!insts.length) {
        grid.innerHTML = `
        <div class="panel-empty">
            <svg width="48" height="48" viewBox="0 0 32 32" fill="none" opacity=".4">
                <rect x="3" y="6" width="26" height="16" rx="3" stroke="currentColor" stroke-width="1.5" />
                <path d="M10 26h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
                <path d="M16 22v4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
            </svg>
            <span>No instances found</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">Run a cycle to discover infrastructure</span>
        </div>`;
        return;
    }
    grid.innerHTML = buildInstanceComparison(insts);
}

function renderActions() {
    const feed = document.getElementById('action-feed');
    if (!feed) return;
    const acts = state.actions || [];
    if (!acts.length) {
        feed.innerHTML = `
        <div class="panel-empty">
            <svg width="48" height="48" viewBox="0 0 28 28" fill="none" opacity=".4">
                <path d="M14 2L4 15h9L10 26l14-16h-9L14 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
            </svg>
            <span>No actions scheduled</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">Agent is monitoring. Awaiting conditions to trigger automated responses</span>
        </div>`;
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
        const inputs = actionInputs(a, findInstance(a.resource_id || a.instance_id));
        
        const { score: actionScore, factors: actionFactors } = confidenceBreakdown(a);
        const finalScore = Math.round(a.confidence || actionScore);
        let actConfClass = 'conf-low';
        if (finalScore >= 85) actConfClass = 'conf-high';
        else if (finalScore >= 60) actConfClass = 'conf-med';
        
        return `<div class="action-entry ${a.status === 'pending_approval' ? 'is-pending' : ''}">
                <span class="ae-icon">${TOOL_ICONS[tool] || 'Act'}</span>
            <div class="ae-body">
                <div class="ae-title">${TOOL_NAMES[tool] || tool}</div>
                <div class="ae-detail">${esc(a.reason || a.action || '')}</div>
                <div class="ae-detail" style="font-family:var(--f-mono); color:var(--cyan); margin-top:4px">${esc(a.resource_id || a.instance_id || '')}</div>
                <div class="decision-inputs">${inputs}</div>
                ${a.status === 'pending_approval' || true ? `
                <div class="action-confidence-wrap">
                    <span class="action-conf-badge ${actConfClass}">${finalScore}% Confident</span>
                    <div class="action-conf-why">
                        ${actionFactors.map(f => `<span>✓ ${esc(f)}</span>`).join('')}
                    </div>
                </div>` : ''}
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
        feed.innerHTML = `
        <div class="panel-empty">
            <svg width="48" height="48" viewBox="0 0 28 28" fill="none" opacity=".4">
                <path d="M14 2L3 8v8c0 6.5 5 11.5 11 12 6-0.5 11-5.5 11-12V8L14 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
                <path d="M10 14l2.5 2.5 5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span>All systems secure</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">No security issues detected</span>
        </div>`;
        return;
    }
    feed.innerHTML = findings.map(f => `
        <div class="finding-entry ${f.severity === 'critical' ? 'finding-crit' : 'finding-warn'}">
            <span class="finding-sev ${f.severity === 'critical' ? 'sev-crit' : 'sev-warn'}">${f.severity.toUpperCase().slice(0, 4)}</span>
            <div class="finding-body">
                <div class="finding-type">${esc(f.type)}</div>
                <div class="finding-detail">${esc(f.detail)}</div>
                ${f.resource ? `<div class="finding-res">${esc(f.resource)}</div>` : ''}
                <div class="decision-inputs">${findingInputs(f)}</div>
            </div>
        </div>`).join('');
}

function renderDiagnosis() {
    const feed = document.getElementById('diag-feed');
    if (!feed) return;
    const diags = (state.actions || []).filter(a => a.diagnosis);
    if (!diags.length) {
        feed.innerHTML = `
        <div class="panel-empty">
            <svg width="48" height="48" viewBox="0 0 28 28" fill="none" opacity=".4">
                <circle cx="13" cy="13" r="9" stroke="currentColor" stroke-width="1.5" />
                <path d="M20 20l5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
            </svg>
            <span>No anomalies detected</span>
            <span style="font-size:0.65rem; color:var(--text-4); margin-top:0.5rem;">System health is optimal. All metrics within normal ranges</span>
        </div>`;
        return;
    }
    feed.innerHTML = diags.map(a => `<div class="diag-entry">
        <div class="diag-top">
            <span class="diag-inst">${esc(a.instance_id || 'Global')}</span>
            <span class="diag-sev ${a.diagnosis.severity === 'critical' ? 'st-error' : 'st-warn'}">${a.diagnosis.severity.toUpperCase()}</span>
        </div>
        <div class="diag-cause">${esc(a.diagnosis.root_cause)}</div>
        <div class="diag-explain">${esc(a.diagnosis.explanation)}</div>
        <div class="decision-inputs">${diagnosisInputs(a)}</div>
        ${a.diagnosis.recommendation ? `<div class="diag-rec">${esc(a.diagnosis.recommendation)}</div>` : ''}
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
            tbody.innerHTML = `<tr><td colspan="5" class="table-empty">
                <svg width="28" height="28" style="opacity:0.4; margin-bottom:0.5rem;" viewBox="0 0 28 28" fill="none">
                    <path d="M4 6h20M4 14h20M4 22h20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
                </svg><br/>
                No history available yet
            </td></tr>`;
            return;
        }
        tbody.innerHTML = history.slice(-30).reverse().map(e => `
            <tr>
                <td class="td-time">${formatTimestamp(e.timestamp)}</td>
                <td class="td-tool">${esc(e.tool || e.tool_name)}</td>
                <td class="td-res">${esc(e.resource || e.resource_id)}</td>
                <td class="td-action">${esc(e.action || e.action_type)}</td>
                <td><span class="status-tag ${statusClass(e.status)}">${esc(e.status || 'unknown')}</span></td>
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
    addChatMessage(message, 'user');
    input.value = '';
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
    html = html.replace(/^### (.*$)/gim, '<h3 style="color:var(--cyan); margin: 0.8rem 0 0.4rem 0; font-size: 0.9rem; border-bottom: 1px solid var(--border); padding-bottom: 2px;">$1</h3>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color:var(--text-1); font-weight: 700;">$1</strong>');
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

function attr(str) {
    return esc(str).replace(/'/g, '&#39;');
}

function compactMetrics(items) {
    return items
        .filter(([, value]) => value !== undefined && value !== null && value !== '')
        .map(([label, value]) => `<span><b>${esc(label)}</b>${esc(value)}</span>`)
        .join('');
}

function tagsToObject(tags) {
    if (!Array.isArray(tags)) return {};
    return tags.reduce((acc, tag) => {
        acc[tag.Key || tag.key] = tag.Value || tag.value;
        return acc;
    }, {});
}

function findInstance(id) {
    if (!id) return null;
    return (state.instances || []).find(i => i.instance_id === id || i.id === id) || null;
}

function actionInputs(action, inst) {
    const tags = inst ? tagsToObject(inst.tags) : {};
    const cpu = inst ? (inst.cpu ?? inst.cpu_percent) : null;
    return compactMetrics([
        ['Tool', action.tool || action.tool_name],
        ['Action', action.action || action.action_type],
        ['Resource', action.resource_id || action.instance_id],
        ['CPU', cpu === null || cpu === undefined ? null : `${Number(cpu).toFixed(1)}%`],
        ['State', inst?.state],
        ['Type', inst?.instance_type],
        ['Missing tags', inst ? ['Owner', 'Project', 'Environment'].filter(k => !tags[k]).join(', ') || 'none' : null],
        ['Cost delta', state.costs?.delta_pct !== undefined ? `${state.costs.delta_pct}%` : null]
    ]);
}

function findingInputs(finding) {
    return compactMetrics([
        ['Severity', finding.severity],
        ['Rule', finding.type],
        ['Resource', finding.resource],
        ['Evidence', finding.detail],
        ['Source', 'security groups / S3 / EBS encryption']
    ]);
}

function diagnosisInputs(action) {
    const inst = findInstance(action.resource_id || action.instance_id);
    const cpu = inst ? (inst.cpu ?? inst.cpu_percent) : null;
    return compactMetrics([
        ['Instance', action.resource_id || action.instance_id],
        ['CPU', cpu === null || cpu === undefined ? null : `${Number(cpu).toFixed(1)}%`],
        ['State', inst?.state],
        ['Type', inst?.instance_type],
        ['Severity', action.diagnosis?.severity],
        ['Signal', action.diagnosis?.root_cause]
    ]);
}

function ageInDays(value) {
    if (!value) return null;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return Math.max(0, Math.round((Date.now() - d.getTime()) / 86400000));
}

function formatShortDate(value) {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString([], { month: 'short', day: '2-digit' });
}

function pct(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(0)}%` : null;
}

function instanceScore(inst, cpu, missingTagCount) {
    let score = inst.state === 'running' ? 20 : 0;
    if (cpu !== null) score += cpu >= 85 ? 80 : cpu <= 5 ? 65 : cpu >= 50 ? 40 : 15;
    score += missingTagCount * 8;
    return score;
}

function instanceSignal(inst, cpu, missingTagCount) {
    if (inst.state !== 'running') return String(inst.state || 'stopped').replace('-', ' ');
    if (cpu === null) return 'No CPU metric';
    if (cpu >= 85) return 'High CPU pressure';
    if (cpu <= 5) return 'Idle candidate';
    if (missingTagCount) return 'Tag cleanup';
    return 'Healthy';
}

function safeDomId(value) {
    return String(value ?? 'item').replace(/[^a-zA-Z0-9_-]/g, '-');
}

function formatTimestamp(value) {
    if (!value) return '-';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? esc(String(value)) : d.toLocaleString([], {
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function buildInstanceComparison(insts) {
    const rows = insts.map(inst => {
        const cpuRaw = inst.cpu ?? inst.cpu_percent;
        const cpu = Number.isFinite(Number(cpuRaw)) ? Number(cpuRaw) : null;
        const tags = tagsToObject(inst.tags);
        const missingTags = ['Owner', 'Project', 'Environment'].filter(k => !tags[k]);
        const ageDays = ageInDays(inst.launch_time);
        const score = instanceScore(inst, cpu, missingTags.length);
        return { inst, cpu, tags, missingTags, ageDays, score };
    }).sort((a, b) => b.score - a.score);

    return `<div class="instance-comparison">
        <div class="comparison-head">
            <div><span class="panel-tag">Sysbench EC2 Benchmark</span><h3>Instance state and live benchmark results</h3></div>
            <span>${rows.length} instances ranked</span>
        </div>
        <div class="comparison-table-wrap">
            <table class="comparison-table">
                <thead>
                    <tr>
                        <th>Rank</th><th>Instance</th><th>Type</th><th>State</th><th>CPU</th>
                        <th>Memory</th><th>CPU eps</th><th>Mem/s</th><th>IOPS</th><th>p95</th><th>Signal</th>
                    </tr>
                </thead>
                <tbody>${rows.map((r, idx) => `${renderInstanceRow(r, idx)}`).join('')}</tbody>
            </table>
        </div>
    </div>`;
}

function renderInstanceRow(r, idx) {
    const inst = r.inst;
    const detailsId = `inst-details-${safeDomId(inst.instance_id || idx)}`;
    return `<tr class="instance-main-row" data-details="${attr(detailsId)}" onclick="toggleInstanceDetails('${attr(detailsId)}')">
                    <td class="rank-cell">#${idx + 1}</td>
                    <td><strong>${esc(inst.name || tagValue(inst, 'Name') || inst.instance_id)}</strong><span>${esc(inst.instance_id)}</span></td>
                    <td>${esc(inst.instance_type || '-')}</td>
                    <td><span class="state-pill state-${safeDomId(inst.state)}">${esc(inst.state || '-')}</span></td>
                    <td>${r.cpu === null ? '-' : `${r.cpu.toFixed(1)}%`}</td>
                    <td>${pct(inst.memory_percent) || '-'}</td>
                    <td>${fmtBench(inst.sysbench?.cpu_events_per_sec)}</td>
                    <td>${fmtBench(inst.sysbench?.memory_mib_per_sec)}</td>
                    <td>${fmtBench(inst.sysbench?.disk_iops)}</td>
                    <td>${fmtBench(inst.sysbench?.p95_latency_ms, 'ms')}</td>
                    <td>${esc(instanceSignal(inst, r.cpu, r.missingTags.length))}</td>
                </tr>
                <tr class="instance-detail-row" id="${detailsId}">
                    <td colspan="11">${renderInstanceDetails(r)}</td>
                </tr>`;
}

function renderInstanceDetails(r) {
    const inst = r.inst;
    const tags = tagsToObject(inst.tags);
    const tagList = Object.entries(tags);
    const volumes = (state.volumes || []).filter(v => {
        const attachments = v.attachments || v.Attachments || [];
        return attachments.some(a => (a.InstanceId || a.instance_id) === inst.instance_id);
    });
    const meta = [
        ['Region', inst.region],
        ['AZ', inst.availability_zone || inst.placement?.AvailabilityZone],
        ['Launch', formatShortDate(inst.launch_time)],
        ['Age', r.ageDays === null ? null : `${r.ageDays} days`],
        ['Private IP', inst.private_ip || inst.private_ip_address],
        ['Public IP', inst.public_ip || inst.public_ip_address],
        ['VPC', inst.vpc_id],
        ['Subnet', inst.subnet_id],
        ['AMI', inst.image_id],
        ['Key', inst.key_name],
        ['Root device', inst.root_device_name],
        ['Monitoring', inst.monitoring || inst.monitoring_state],
        ['Missing tags', r.missingTags.length ? r.missingTags.join(', ') : 'none']
    ];
    const bench = [
        ['CPU events/sec', fmtBench(inst.sysbench?.cpu_events_per_sec)],
        ['Memory MiB/sec', fmtBench(inst.sysbench?.memory_mib_per_sec)],
        ['Disk IOPS', fmtBench(inst.sysbench?.disk_iops)],
        ['p95 latency', fmtBench(inst.sysbench?.p95_latency_ms, 'ms')],
        ['CPU metric', r.cpu === null ? 'unavailable' : `${r.cpu.toFixed(1)}%`],
        ['Memory', pct(inst.memory_percent)]
    ];
    return `<div class="instance-detail-panel">
        <div class="instance-detail-grid">
            <div class="detail-section"><h4>AWS metadata</h4><div class="detail-kv">${compactMetrics(meta)}</div></div>
            <div class="detail-section"><h4>Performance evidence</h4><div class="detail-kv">${compactMetrics(bench)}</div></div>
            <div class="detail-section"><h4>Tags</h4><div class="tag-list">${tagList.length ? tagList.map(([k, v]) => `<span><b>${esc(k)}</b>${esc(v)}</span>`).join('') : '<span>No tags returned</span>'}</div></div>
            <div class="detail-section"><h4>Storage attachments</h4><div class="detail-kv">${volumes.length ? compactMetrics(volumes.map(v => [v.volume_id || v.VolumeId, `${v.size_gb || v.Size || '?'} GB ${v.state || v.State || ''}`])) : '<span>No attached volumes in current snapshot</span>'}</div></div>
        </div>
    </div>`;
}

function toggleInstanceDetails(id) {
    document.getElementById(id)?.classList.toggle('open');
}

function tagValue(inst, key) {
    return tagsToObject(inst.tags)[key];
}

function fmtBench(value, suffix = '') {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    const out = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    return `${out}${suffix}`;
}

function statusClass(status) {
    const s = String(status || '').toLowerCase();
    if (s.includes('error') || s.includes('fail')) return 'st-error';
    if (s.includes('pending')) return 'st-pending';
    if (s.includes('dry')) return 'st-dryrun';
    if (s.includes('warn')) return 'st-warn';
    return 'st-success';
}

// ============================================================
// SPARKLINE HELPERS
// ============================================================

function drawSparkline() {
    const canvas = document.getElementById('kpi-sparkline');
    if (!canvas) return;
    const daily = (state.costs && (state.costs.current_daily ?? state.costs.daily ?? state.costs.daily_cost)) || 0;
    const seed = daily || 120;
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
    ctx.beginPath();
    ctx.moveTo(pts[0].x, h);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, h);
    ctx.closePath();
    ctx.fillStyle = fillColor;
    ctx.fill();
    ctx.beginPath();
    pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.stroke();
}

// ============================================================
// DECISION CARDS
// ============================================================

document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-decision]');
    if (!btn) return;
    const card = btn.closest('.decision-card');
    if (btn.dataset.decision === 'skip') {
        card?.remove();
        return;
    }
    approveAction(btn.dataset.tool || '', btn.dataset.resource || '', btn.dataset.action || '', btn);
});

function confidenceBreakdown(action) {
    const tool = action.tool || action.tool_name || '';
    const inst = findInstance(action.resource_id || action.instance_id);
    const cpu = inst ? Number(inst.cpu ?? inst.cpu_percent) : null;
    const tags = inst ? tagsToObject(inst.tags) : {};
    const missingTags = inst ? ['Owner', 'Project', 'Environment'].filter(k => !tags[k]) : [];
    const factors = [];
    let score = 62;
    if (action.reason) { score += 8; factors.push('Planner reason present'); }
    if (action.resource_id || action.instance_id) { score += 6; factors.push('Resource matched'); }
    if (inst) { score += 8; factors.push(`State ${inst.state || 'known'}`); }
    if (cpu !== null && Number.isFinite(cpu)) {
        const cpuSignal = cpu <= 5 ? 'idle CPU' : cpu >= 85 ? 'high CPU' : 'CPU observed';
        score += cpu <= 5 || cpu >= 85 ? 10 : 4;
        factors.push(`${cpuSignal} ${cpu.toFixed(1)}%`);
    }
    if (missingTags.length) { score += 5; factors.push(`Missing tags: ${missingTags.join(', ')}`); }
    if (tool.includes('security') || tool.includes('audit')) { score += 10; factors.push('Security scan requested'); }
    if (state.costs?.delta_pct !== undefined && Number(state.costs.delta_pct) > 0) {
        score += 4;
        factors.push(`Cost delta ${state.costs.delta_pct}%`);
    }
    return { score: Math.max(50, Math.min(score, 96)), factors: factors.slice(0, 5) };
}

// ============================================================
// NAVIGATION ROUTER
// ============================================================

const NAV_MAP = {
    dashboard: { navId: 'sb-nav-dashboard', path: '/dashboard', title: 'Command Center' },
    instances: { navId: 'sb-nav-instances', path: '/dashboard/instances', title: 'Instances' },
    storage: { navId: 'sb-nav-storage', path: '/dashboard/storage', title: 'Storage' },
    costs: { navId: 'sb-nav-costs', path: '/dashboard/costs', title: 'Costs' },
    actions: { navId: 'sb-nav-actions', path: '/dashboard/actions', title: 'Actions' },
    security: { navId: 'sb-audit-link', path: '/dashboard/audit', title: 'Audit' },
    diag: { navId: 'sb-nav-diag', path: '/dashboard/diagnostics', title: 'Diagnostics' },
    queries: { navId: 'sb-nav-queries', path: '/dashboard/queries', title: 'Slow Queries' },
    strategy: { navId: 'sb-nav-strategy', path: '/dashboard/strategy', title: 'Strategy Engine' },
    assistant: { navId: 'sb-nav-assistant', path: '/dashboard/assistant', title: 'AI Assistant' },
    history: { navId: 'sb-nav-history', path: '/dashboard/history', title: 'Action History' },
    settings: { navId: 'sb-settings-link', path: '/dashboard/settings', title: 'Settings' },
};

function viewFromPath(path = location.pathname) {
    if (path.endsWith('/instances')) return 'instances';
    if (path.endsWith('/storage')) return 'storage';
    if (path.endsWith('/costs')) return 'costs';
    if (path.endsWith('/actions')) return 'actions';
    if (path.endsWith('/audit')) return 'security';
    if (path.endsWith('/diagnostics')) return 'diag';
    if (path.endsWith('/queries')) return 'queries';
    if (path.endsWith('/strategy')) return 'strategy';
    if (path.endsWith('/assistant')) return 'assistant';
    if (path.endsWith('/history')) return 'history';
    if (path.endsWith('/settings')) return 'settings';
    return 'dashboard';
}

function navigateTo(e, view, replace = false) {
    if (e) e.preventDefault();
    if (!NAV_MAP[view]) view = 'dashboard';
    const targetPath = NAV_MAP[view].path;
    if (location.pathname !== targetPath) {
        history[replace ? 'replaceState' : 'pushState']({ view }, '', targetPath);
    }
    applyRoute(view);
}

function applyRoute(view) {
    const isSettings = view === 'settings';
    const content = document.getElementById('main-content');
    const grid = document.getElementById('panel-grid');
    if (content) content.dataset.view = view;
    if (grid) grid.dataset.view = view;

    const ps = document.getElementById('panel-settings');
    if (ps) ps.classList.toggle('active', isSettings);

    ['panel-grid', 'panel-thoughts', 'kpi-strip', 'ai-strategy-strip'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = isSettings ? 'none' : '';
    });

    document.querySelectorAll('.sb-item').forEach(el => el.classList.remove('active'));
    const activeNav = NAV_MAP[view]?.navId;
    if (activeNav) document.getElementById(activeNav)?.classList.add('active');

    const title = document.querySelector('.page-title');
    const eyebrow = document.querySelector('.page-eyebrow');
    if (title) title.textContent = NAV_MAP[view]?.title || 'Command Center';
    if (eyebrow) eyebrow.textContent = view === 'dashboard' ? 'Live Dashboard' : 'Focused View';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function scrollToPanel(id) {
    const map = {
        'panel-instances': 'instances',
        'panel-volumes': 'storage',
        'panel-cost': 'costs',
        'panel-actions': 'actions',
        'panel-security': 'security',
        'panel-diag': 'diag',
        'panel-query': 'queries',
        'panel-chat': 'assistant',
        'panel-log': 'history',
        'panel-thoughts': 'strategy',
    };
    navigateTo(null, map[id] || 'dashboard');
}
function showSettings(e) { navigateTo(e, 'settings'); }
function hideSettings() { navigateTo(null, 'dashboard'); }

window.addEventListener('popstate', () => applyRoute(viewFromPath()));

// ============================================================
// THEME
// ============================================================

function applyTheme(theme) {
    const resolved = theme === 'light' ? 'light' : 'dark';
    document.body.dataset.theme = resolved;
    localStorage.setItem('cloudagent-theme', resolved);
    const toggle = document.getElementById('theme-toggle');
    if (toggle) toggle.setAttribute('aria-label', `Switch to ${resolved === 'dark' ? 'light' : 'dark'} theme`);
}

function toggleTheme() {
    const current = document.body.dataset.theme === 'light' ? 'light' : 'dark';
    applyTheme(current === 'light' ? 'dark' : 'light');
}

// ============================================================
// SETTINGS HELPERS
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
    } catch (_) { alert('Export failed - no history available.'); }
}


// ============================================================
// FIX 2: renderVolumeGrid — normalises field names + updates badge
// ============================================================

function renderVolumeGrid() {
    const container = document.getElementById('volume-grid');
    if (!container) return;

    const volumes = state.volumes || [];

    // Update volume counter badge
    const badge = document.getElementById('vol-badge');
    if (badge) {
        badge.textContent = `${volumes.length} volume${volumes.length !== 1 ? 's' : ''}`;
    }

    // Empty state
    if (!volumes.length) {
        container.innerHTML = `
            <div class="panel-empty">
                <div class="empty-icon">
                    <svg width="48" height="48" viewBox="0 0 28 28" fill="none" opacity=".4">
                        <ellipse 
                            cx="14" 
                            cy="8" 
                            rx="10" 
                            ry="3.5"
                            stroke="currentColor"
                            stroke-width="1.5"
                        />
                        <path 
                            d="M4 8v12c0 1.9 4.5 3.5 10 3.5s10-1.6 10-3.5V8"
                            stroke="currentColor"
                            stroke-width="1.5"
                        />
                    </svg>
                </div>

                <div class="empty-title">No volumes detected</div>

                <div class="empty-subtitle">
                    Run a cycle to discover block storage
                </div>
            </div>
        `;
        return;
    }

    // Render volume cards
    container.innerHTML = volumes.map(volume => {

        // Normalize backend response fields
        const volumeId =
            volume.volume_id ||
            volume.id ||
            '—';

        const volumeSize =
            volume.size_gb ??
            volume.size ??
            '?';

        const volumeType =
            volume.volume_type ||
            volume.type ||
            'gp2';

        const volumeState =
            volume.state ||
            'unknown';

        const volumeRegion =
            volume.region ||
            '—';

        // Detect attached instance
        let attachedInstance = null;

        if (volume.attached_to) {
            attachedInstance = volume.attached_to;
        } else if (
            Array.isArray(volume.attachments) &&
            volume.attachments.length
        ) {
            attachedInstance =
                volume.attachments[0].instance_id ||
                'attached';
        }

        // State color mapping
        let stateColor = 'var(--text-3)';

        if (volumeState === 'available') {
            stateColor = 'var(--green)';
        } else if (volumeState === 'in-use') {
            stateColor = 'var(--cyan)';
        }

        return `
            <div class="vol-card">

                <!-- Volume Icon -->
                <div class="vol-icon">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                        <ellipse 
                            cx="12" 
                            cy="6" 
                            rx="7" 
                            ry="3"
                            stroke="currentColor"
                            stroke-width="1.5"
                        />
                        <path 
                            d="M5 6v8c0 1.7 3.1 3 7 3s7-1.3 7-3V6"
                            stroke="currentColor"
                            stroke-width="1.5"
                        />
                    </svg>
                </div>

                <!-- Volume Information -->
                <div class="vol-info">

                    <!-- Volume ID -->
                    <div 
                        class="vol-id"
                        title="${esc(volumeId)}"
                    >
                        ${esc(
            volumeId.length > 20
                ? volumeId.slice(0, 20) + '…'
                : volumeId
        )}
                    </div>

                    <!-- Main Metadata -->
                    <div class="vol-meta">
                        <span>${esc(String(volumeSize))} GiB</span>

                        <span style="opacity:.5">•</span>

                        <span class="vol-status ${esc(volumeState)}">
                            ${esc(volumeState)}
                        </span>

                        <span style="opacity:.5">•</span>

                        <span>${esc(volumeType)}</span>
                    </div>

                    <!-- Attachment Info -->
                    ${attachedInstance
                ? `
                                <div class="vol-meta attachment-info">
                                    Attached to ${esc(attachedInstance)}
                                </div>
                            `
                : `
                                <div class="vol-meta unattached-info">
                                    Unattached
                                </div>
                            `
            }

                    <!-- Region -->
                    <div class="vol-meta region-info">
                        ${esc(volumeRegion)}
                    </div>

                </div>
            </div>
        `;
    }).join('');
}

// ============================================================
// FIX 3 + 4: Provision modal — guaranteed visible, modal CSS injected
// ============================================================

// Inject modal styles so the modal works even if dashboard.css is missing them
(function injectModalStyles() {
    if (document.getElementById('provision-modal-styles')) return;
    const s = document.createElement('style');
    s.id = 'provision-modal-styles';
    s.textContent = `
    .modal-overlay {
        position: fixed; inset: 0; z-index: 9999;
        background: rgba(0,0,0,0.7); backdrop-filter: blur(6px);
        display: flex; align-items: center; justify-content: center;
    }
    .modal-content {
        background: #161b22; border: 1px solid #30363d; border-radius: 12px;
        width: 420px; max-width: 94vw; box-shadow: 0 24px 64px rgba(0,0,0,.6);
        font-family: 'DM Mono', monospace; color: #e6edf3;
        animation: modalIn .18s ease;
    }
    @keyframes modalIn { from { opacity:0; transform:translateY(-12px) scale(.97); } to { opacity:1; transform:none; } }
    .modal-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 16px 20px; border-bottom: 1px solid #30363d;
    }
    .modal-header h3 { margin: 0; font-size: 14px; font-weight: 600; letter-spacing: .04em; }
    .modal-close {
        background: none; border: none; color: #8b949e; font-size: 20px;
        cursor: pointer; line-height: 1; padding: 0 4px; border-radius: 4px;
        transition: color .12s;
    }
    .modal-close:hover { color: #e6edf3; }
    .modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
    .form-group { display: flex; flex-direction: column; gap: 5px; }
    .form-group label {
        font-size: 10px; font-weight: 600; letter-spacing: .08em;
        text-transform: uppercase; color: #8b949e;
    }
    .modal-select, .modal-input {
        background: #0d1117; border: 1px solid #30363d; border-radius: 7px;
        color: #e6edf3; padding: 8px 12px; font-family: inherit; font-size: 13px;
        outline: none; transition: border-color .15s; width: 100%; box-sizing: border-box;
    }
    .modal-select:focus, .modal-input:focus { border-color: #58a6ff; }
    .modal-select option { background: #161b22; }
    .modal-footer {
        display: flex; justify-content: flex-end; gap: 8px;
        padding: 14px 20px; border-top: 1px solid #30363d;
    }
    .btn-cancel {
        padding: 8px 16px; border-radius: 7px; border: 1px solid #30363d;
        background: transparent; color: #8b949e; cursor: pointer; font-size: 13px;
        transition: border-color .12s, color .12s;
    }
    .btn-cancel:hover { border-color: #58a6ff; color: #e6edf3; }
    .btn-confirm {
        padding: 8px 18px; border-radius: 7px; border: none;
        background: linear-gradient(135deg, #58a6ff, #1f6feb);
        color: #fff; cursor: pointer; font-size: 13px; font-weight: 600;
        transition: opacity .15s, transform .12s;
    }
    .btn-confirm:hover:not(:disabled) { opacity: .88; transform: translateY(-1px); }
    .btn-confirm:disabled { opacity: .45; cursor: not-allowed; }
    .provision-result {
        margin: 0 20px 14px; padding: 9px 12px; border-radius: 7px;
        font-size: 12px; line-height: 1.5; display: none;
    }
    .provision-result.success {
        background: rgba(63,185,80,.12); border: 1px solid rgba(63,185,80,.3);
        color: #3fb950; display: block;
    }
    .provision-result.error {
        background: rgba(248,81,73,.12); border: 1px solid rgba(248,81,73,.3);
        color: #f85149; display: block;
    }
    /* provision button in panel header */
    .provision-btn {
        padding: 5px 12px; border-radius: 20px; border: 1px solid #58a6ff;
        background: rgba(88,166,255,.1); color: #58a6ff; font-size: 11px;
        font-weight: 600; cursor: pointer; letter-spacing: .04em;
        transition: background .15s, transform .12s;
    }
    .provision-btn:hover { background: rgba(88,166,255,.2); transform: translateY(-1px); }
    `;
    document.head.appendChild(s);
})();

let currentProvisionType = 'instance';

function openProvisionModal(type) {
    currentProvisionType = type;
    const modal = document.getElementById('provision-modal');
    const title = document.getElementById('modal-title');
    const instForm = document.getElementById('instance-form');
    const volForm = document.getElementById('volume-form');
    const resultEl = document.getElementById('provision-result');

    if (title) title.textContent = type === 'instance' ? 'Provision Compute Instance' : 'Provision Storage Volume';
    if (instForm) instForm.style.display = type === 'instance' ? 'block' : 'none';
    if (volForm) volForm.style.display = type === 'volume' ? 'block' : 'none';
    if (resultEl) { resultEl.className = 'provision-result'; resultEl.textContent = ''; }

    // ✅ Force display regardless of any conflicting CSS
    if (modal) {
        modal.style.cssText = 'display:flex !important; position:fixed; inset:0; z-index:9999; background:rgba(0,0,0,0.7); backdrop-filter:blur(6px); align-items:center; justify-content:center;';
    }
}

function closeProvisionModal() {
    const modal = document.getElementById('provision-modal');
    if (modal) modal.style.cssText = 'display:none !important;';
}

async function confirmProvision() {
    const btn = document.getElementById('btn-modal-confirm');
    const resultEl = document.getElementById('provision-result');

    if (btn) { btn.disabled = true; btn.textContent = 'Allocating…'; }
    if (resultEl) { resultEl.className = 'provision-result'; resultEl.textContent = ''; }

    try {
        let endpoint, payload;

        if (currentProvisionType === 'instance') {
            endpoint = '/api/allocate-instance';
            payload = {
                instance_type: document.getElementById('provision-inst-type')?.value || 't3.micro',
                region: document.getElementById('provision-inst-region')?.value?.trim() || undefined,
            };
        } else {
            endpoint = '/api/allocate-volume';
            payload = {
                size_gb: parseInt(document.getElementById('provision-vol-size')?.value || '10', 10),
                region: document.getElementById('provision-vol-region')?.value?.trim() || undefined,
            };
        }

        const resp = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const result = await resp.json();

        if (result.error) {
            if (resultEl) {
                resultEl.className = 'provision-result error';
                resultEl.textContent = '✕ ' + result.error;
            }
        } else {
            // Build a detailed success message
            const id = result.instance_id || result.volume_id || '';
            let details = '';
            if (currentProvisionType === 'instance') {
                details = [
                    id ? `ID: ${id}` : '',
                    result.instance_type ? `Type: ${result.instance_type}` : '',
                    result.region ? `Region: ${result.region}` : '',
                    result.state ? `State: ${result.state}` : '',
                ].filter(Boolean).join(' · ');
            } else {
                details = id ? `Volume: ${id}` : '';
            }

            if (resultEl) {
                resultEl.className = 'provision-result success';
                resultEl.textContent = `✓ ${currentProvisionType === 'instance' ? 'Instance' : 'Volume'} provisioned${details ? ' — ' + details : ''}`;
            }
            setTimeout(closeProvisionModal, 2500);

            // Refresh state from the server immediately — the WebSocket will also push updates
            fetch('/api/status').then(r => r.json()).then(d => { state = d; render(); }).catch(() => { });
            // Also refresh the action history to show the provision log entry
            loadHistory();
        }
    } catch (e) {
        if (resultEl) {
            resultEl.className = 'provision-result error';
            resultEl.textContent = '✕ Failed: ' + e.message;
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Confirm Allocation'; }
    }
}

// ============================================================
// FIX 4: All click handlers inside DOMContentLoaded
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    applyTheme(localStorage.getItem('cloudagent-theme') || 'dark');
    tickClock();
    setInterval(tickClock, 1000);
    navigateTo(null, viewFromPath(), true);
    connectWS();
    loadHistory();
    setInterval(loadHistory, 30000);
    fetch('/api/status').then(r => r.json()).then(d => { state = d; render(); }).catch(() => { });

    // Chat suggestion clicks
    document.addEventListener('click', (e) => {
        if (e.target.matches('.chat-suggestions li')) {
            const input = document.getElementById('chat-input');
            if (input) {
                input.value = e.target.textContent.replace(/^"|"$/g, '');
                sendChatMessage();
            }
        }
    });

    // ✅ Provision buttons — now safely wired after DOM exists
    document.addEventListener('click', (e) => {
        const id = e.target.id || e.target.closest('button')?.id;
        if (id === 'btn-provision-instance') { openProvisionModal('instance'); return; }
        if (id === 'btn-provision-volume') { openProvisionModal('volume'); return; }
        if (id === 'btn-run-query-optimizer') { runQueryOptimizerNow(e.target.closest('button')); return; }
        if (id === 'modal-close-btn' || id === 'btn-modal-cancel') { closeProvisionModal(); return; }
        if (id === 'btn-modal-confirm') { confirmProvision(); return; }

        // Close modal when clicking the dark overlay itself
        if (e.target.id === 'provision-modal') { closeProvisionModal(); }
    });
});
