import os

file_path = r"c:\Users\nadig\Downloads\Cloud-Agentic-AI-main\cloud_agent\dashboard\static\dashboard.js"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Find the start of the mess
mess_start = "function renderReasoning() {"
mess_end = "    setText('kpi-instances', insts.length);"

start_idx = content.find(mess_start)
end_idx = content.find(mess_end)

if start_idx != -1 and end_idx != -1:
    fixed_middle = """function renderReasoning() {
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

    // Update Overall Confidence
    const confEl = document.getElementById('ai-confidence');
    if (confEl) {
        const conf = state.overall_confidence || 0;
        confEl.textContent = `Confidence: ${conf}%`;
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

    // FIX: collector stores keys as current_daily / baseline_daily
    const daily    = costs.current_daily  ?? costs.daily    ?? costs.daily_cost    ?? 0;
    const baseline = costs.baseline_daily ?? costs.baseline ?? costs.baseline_cost ?? null;
    const deltaPct = costs.delta_pct ?? 0;

    """
    new_content = content[:start_idx] + fixed_middle + content[end_idx:]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("Fixed dashboard.js structure using block replacement.")
else:
    print(f"Could not find markers: {start_idx}, {end_idx}")
