import { UsageData, LocalStats, getFiveHour, getSevenDay, getPlan } from './api';

// ── Color palette — references local CSS vars, which map to VS Code theme tokens ─
const C = {
    amber:    'var(--amber)',
    amberL:   'var(--amber-l)',
    violet:   'var(--violet)',
    violetD:  'var(--violet-d)',
    cyan:     'var(--cyan)',
    muted:    'var(--muted)',
    dim:      'var(--dim)',
    dimD:     'var(--dim-d)',
    white:    'var(--white)',
    green:    'var(--green)',
    orange:   'var(--orange)',
    red:      'var(--red)',
    skin:     '#c8866b',   // creature colour — intentionally fixed
    blue:     'var(--blue)',
    bg:       'var(--bg)',
    bgPanel:  'var(--bg-panel)',
    bgCard:   'var(--bg-card)',
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtTimeUntil(iso: string | null | undefined): string {
    if (!iso) return '—';
    try {
        let secs = Math.max(0, Math.floor((new Date(iso).getTime() - Date.now()) / 1000));
        const d = Math.floor(secs / 86400); secs -= d * 86400;
        const h = Math.floor(secs / 3600); secs -= h * 3600;
        const m = Math.floor(secs / 60); secs -= m * 60;
        if (d > 0) return `${d}d ${h}h`;
        if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
        if (m > 0) return `${m}m ${String(secs).padStart(2, '0')}s`;
        return `${secs}s`;
    } catch { return '—'; }
}

function fmtTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function fmtModel(name: string): string {
    if (!name || name === '—') return '—';
    const n = name.replace('claude-', '');
    const parts = n.split('-');
    if (parts.length >= 2) return `${parts[0][0].toUpperCase() + parts[0].slice(1)} ${parts[1]}`;
    return n[0].toUpperCase() + n.slice(1);
}

// ── HTML generation ────────────────────────────────────────────────────────────

export function getDashboardHtml(
    usageData: UsageData | null | undefined,
    localData: LocalStats | null | undefined,
    warning?: string
): string {
    const fh = usageData ? getFiveHour(usageData) : { utilization: null, resets_at: null };
    const sd = usageData ? getSevenDay(usageData) : { utilization: null, resets_at: null };
    const plan = usageData ? getPlan(usageData) : '';

    const fhPct  = fh.utilization ?? 0;
    const sdPct  = sd.utilization ?? 0;
    const fhReset = fmtTimeUntil(fh.resets_at);
    const sdReset = fmtTimeUntil(sd.resets_at);
    const fhTimePct = timeElapsedPct(fh.resets_at, 5 * 3600);
    const sdTimePct = timeElapsedPct(sd.resets_at, 7 * 24 * 3600);

    const totals = localData?.totalTokens ?? 0;
    const projects = localData?.projects ?? 0;
    const sessions = localData?.sessions ?? 0;
    const cacheRate = localData?.cacheHitRate ?? 0;
    const daily = localData?.dailyTokens ?? {};
    const models = localData?.models ?? {};
    const tokens5h = localData?.tokens5h ?? 0;

    // Daily sparkline (last 14 days)
    const sortedDays = Object.keys(daily).sort();
    const dailyVals = sortedDays.slice(-14).map(k => daily[k]);
    const sparkMax = Math.max(...dailyVals, 1);

    // Top 3 models
    const topModels = Object.entries(models)
        .filter(([name]) => !name.includes('synthetic'))
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3);

    // Burn rate (today's tokens / hours elapsed)
    const today = new Date().toISOString().slice(0, 10);
    const todayTok = daily[today] ?? 0;
    const hoursElapsed = Math.max(0.1, new Date().getHours() + new Date().getMinutes() / 60);
    const burnRate = Math.round(todayTok / hoursElapsed);

    const now = new Date().toLocaleTimeString();
    const hasData = usageData != null;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<title>clu — Claude Usage</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    /* Map local vars → VS Code theme tokens (with dark-theme hex fallbacks) */
    --amber:    var(--vscode-charts-yellow,  #d97706);
    --amber-l:  var(--vscode-charts-yellow,  #fbbf24);
    --violet:   var(--vscode-charts-purple,  #a78bfa);
    --violet-d: var(--vscode-charts-purple,  #7c3aed);
    --cyan:     var(--vscode-charts-blue,    #67e8f9);
    --muted:    var(--vscode-descriptionForeground, #6b7280);
    --dim:      var(--vscode-panel-border,   var(--vscode-widget-border, #374151));
    --dim-d:    var(--vscode-input-background, #1f2937);
    --white:    var(--vscode-editor-foreground, #f3f4f6);
    --green:    var(--vscode-charts-green,   #34d399);
    --orange:   var(--vscode-charts-orange,  #fb923c);
    --red:      var(--vscode-charts-red,     #f87171);
    --skin:     #c8866b;
    --blue:     var(--vscode-charts-blue,    #60a5fa);
    --bg:       var(--vscode-editor-background, #111827);
    --bg-panel: var(--vscode-sideBar-background, var(--vscode-editor-background, #1f2937));
    --bg-card:  var(--vscode-editorWidget-background, var(--vscode-editor-background, #161e2d));
  }

  body {
    background: var(--bg);
    color: var(--white);
    font-family: var(--vscode-editor-font-family), 'Cascadia Code', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: var(--vscode-editor-font-size, 13px);
    line-height: 1.5;
    padding: 16px;
    min-height: 100vh;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--dim-d);
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo {
    font-size: 18px;
    font-weight: bold;
  }

  .logo .diamond { color: var(--amber); }
  .logo .name    { color: var(--white); }
  .logo .version { color: var(--violet); font-size: 12px; }

  .plan-badge {
    background: color-mix(in srgb, var(--violet) 15%, transparent);
    border: 1px solid var(--violet-d);
    color: var(--violet);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--muted);
    font-size: 11px;
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green);
    display: inline-block;
    animation: pulse 2s infinite;
  }
  .status-dot.error { background: var(--red); animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }

  .card {
    background: var(--bg-card);
    border: 1px solid var(--dim);
    border-radius: 8px;
    padding: 14px 16px;
  }

  .card-title {
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .card-title.amber { color: var(--amber); }
  .card-title.cyan  { color: var(--cyan); }
  .card-title.violet{ color: var(--violet); }

  /* ── Creature ───────────────── */
  .creature-row {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 14px;
  }

  .creature {
    font-family: inherit;
    line-height: 1.4;
    font-size: 13px;
    flex-shrink: 0;
  }

  .creature .eye { display: inline-block; transition: all 0.1s; }

  .speech-bubble {
    position: relative;
    background: var(--dim-d);
    border: 1px solid var(--dim);
    border-radius: 8px;
    padding: 6px 10px;
    font-style: italic;
    color: var(--muted);
    font-size: 12px;
    align-self: center;
    max-width: 180px;
  }
  .speech-bubble::before {
    content: '';
    position: absolute;
    left: -8px;
    top: 50%;
    transform: translateY(-50%);
    border: 4px solid transparent;
    border-right-color: var(--dim);
  }
  .speech-bubble::after {
    content: '';
    position: absolute;
    left: -6px;
    top: 50%;
    transform: translateY(-50%);
    border: 4px solid transparent;
    border-right-color: var(--dim-d);
  }

  /* ── Progress bars ──────────────────── */
  .gauge-section { margin-bottom: 10px; }

  .gauge-label {
    display: flex;
    justify-content: space-between;
    margin-bottom: 4px;
    font-size: 11px;
    color: var(--muted);
  }

  .gauge-label .window { font-weight: bold; color: var(--white); }
  .gauge-label .pct    { font-weight: bold; }

  .bar-track {
    height: 8px;
    background: var(--dim-d);
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 3px;
  }

  .bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
  }

  .time-bar-track {
    height: 3px !important;
    margin-top: 3px;
    opacity: 0.6;
  }

  .reset-row {
    font-size: 11px;
    color: var(--cyan);
    display: flex;
    align-items: center;
    gap: 4px;
  }

  /* ── Stats ──────────────────── */
  .stat-row {
    display: flex;
    align-items: baseline;
    gap: 6px;
    margin-bottom: 6px;
  }

  .stat-value { font-weight: bold; font-size: 14px; }
  .stat-label { color: var(--muted); font-size: 11px; }

  .stat-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px 16px;
    margin-bottom: 8px;
  }

  /* ── Sparkline ──────────────────── */
  .sparkline {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 24px;
    margin: 6px 0 2px;
  }

  .spark-bar {
    flex: 1;
    border-radius: 1px 1px 0 0;
    min-height: 2px;
    max-width: 20px;
    transition: height 0.3s;
  }

  /* ── Cache bar ──────────────────── */
  .mini-bar-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  .mini-bar-track {
    flex: 1;
    height: 6px;
    background: var(--dim-d);
    border-radius: 3px;
    overflow: hidden;
  }

  .mini-bar-fill {
    height: 100%;
    border-radius: 3px;
  }

  .mini-bar-label {
    font-size: 11px;
    font-weight: bold;
    min-width: 36px;
    text-align: right;
  }

  /* ── Models ──────────────────── */
  .model-row {
    display: flex;
    justify-content: space-between;
    margin-bottom: 3px;
    font-size: 11px;
  }

  .model-name { color: var(--blue); }
  .model-tok  { color: var(--white); font-weight: bold; }

  /* ── Warning ──────────────────── */
  .warning-bar {
    background: color-mix(in srgb, var(--orange) 12%, transparent);
    border: 1px solid var(--orange);
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 12px;
    font-size: 11px;
    color: var(--orange);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  /* ── Footer ──────────────────── */
  .footer {
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--dim-d);
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: var(--muted);
    font-size: 11px;
  }

  .refresh-btn {
    background: var(--dim-d);
    border: 1px solid var(--dim);
    color: var(--violet);
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    font-family: inherit;
    font-size: 11px;
    transition: background 0.15s;
  }
  .refresh-btn:hover { background: var(--dim); }

  /* ── Bounce animation for creature ─── */
  @keyframes bounce {
    0%, 100% { transform: translateY(0); }
    50%       { transform: translateY(-3px); }
  }

  .bouncing { animation: bounce 0.4s ease-in-out; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo">
      <span class="diamond">◆</span>
      <span class="name"> clu </span>
    </div>
    ${plan ? `<div class="plan-badge">${escHtml(plan)}</div>` : ''}
  </div>
  <div class="header-right">
    <span class="status-dot${warning ? ' error' : ''}"></span>
    <span>${escHtml(now)}</span>
  </div>
</div>

${warning ? `<div class="warning-bar">⚠ ${escHtml(warning)}</div>` : ''}

<div class="grid">

  <!-- ── Hero card: creature + gauges ── -->
  <div class="card">
    <div class="card-title amber">◆ usage</div>

    <div class="creature-row">
      <pre class="creature" id="creature"><span style="color:${C.violet}">   *</span>
<span style="color:${C.violet}">   |</span>
<span style="color:${C.skin}"> ┌────┐</span>
<span style="color:${C.skin}"> │</span><span class="eye" id="eye-l" style="color:${C.violet}">◕</span><span style="color:${C.violet}"> </span><span class="eye" id="eye-r" style="color:${C.violet}">◕</span><span style="color:${C.skin}">│</span>
<span style="color:${C.skin}"> └┬──┬┘</span>
<span style="color:${C.skin}">  │  │</span></pre>
      <div class="speech-bubble" id="speech">${getPhrase(false)}</div>
    </div>

    ${hasData ? `
    <!-- 5h gauge -->
    <div class="gauge-section">
      <div class="gauge-label">
        <span class="window">5h window</span>
        <span class="pct" style="color:${barColor(fhPct)}">${Math.round(fhPct)}%</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:${Math.min(fhPct, 100)}%; background:${barColor(fhPct)}"></div>
      </div>
      <div class="bar-track time-bar-track">
        <div id="fh-time-bar" class="bar-fill" style="width:${fhTimePct.toFixed(1)}%; background:${C.blue}; transition:none"></div>
      </div>
      <div class="reset-row">◷ resets in <strong>${escHtml(fhReset)}</strong></div>
    </div>

    <!-- 7d gauge -->
    <div class="gauge-section">
      <div class="gauge-label">
        <span class="window">7d window</span>
        <span class="pct" style="color:${barColor(sdPct)}">${Math.round(sdPct)}%</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:${Math.min(sdPct, 100)}%; background:${barColor(sdPct)}"></div>
      </div>
      <div class="bar-track time-bar-track">
        <div id="sd-time-bar" class="bar-fill" style="width:${sdTimePct.toFixed(1)}%; background:${C.blue}; transition:none"></div>
      </div>
      <div class="reset-row">◷ resets in <strong>${escHtml(sdReset)}</strong></div>
    </div>
    ` : '<div style="color: var(--muted); font-size:12px; padding: 8px 0">Waiting for API data…</div>'}
  </div>

  <!-- ── Stats card ── -->
  <div class="card">
    <div class="card-title cyan">◈ stats</div>

    <div class="stat-row">
      <span class="stat-value" style="color:${C.orange}">🔥</span>
      <span class="stat-value" style="color:${C.white}">${escHtml(fmtTokens(burnRate))}/h</span>
      <span class="stat-label">today's burn rate</span>
    </div>

    <div class="stat-grid">
      <div>
        <div class="stat-value" style="color:${C.white}">${escHtml(fmtTokens(totals))}</div>
        <div class="stat-label">total tokens</div>
      </div>
      <div>
        <div class="stat-value" style="color:${C.violet}">${projects}</div>
        <div class="stat-label">projects</div>
      </div>
      <div>
        <div class="stat-value" style="color:${C.cyan}">${sessions}</div>
        <div class="stat-label">sessions</div>
      </div>
      <div>
        <div class="stat-value" style="color:${C.amberL}">${escHtml(fmtTokens(tokens5h))}</div>
        <div class="stat-label">last 5h (local)</div>
      </div>
    </div>

    <!-- Cache hit rate -->
    <div style="margin-bottom: 10px;">
      <div class="stat-label" style="margin-bottom:4px">cache hit rate</div>
      <div class="mini-bar-row">
        <div class="mini-bar-track">
          <div class="mini-bar-fill" style="width:${cacheRate.toFixed(0)}%; background:${cacheRate > 80 ? C.green : cacheRate > 50 ? C.amberL : C.red}"></div>
        </div>
        <span class="mini-bar-label" style="color:${cacheRate > 80 ? C.green : cacheRate > 50 ? C.amberL : C.red}">${cacheRate.toFixed(0)}%</span>
      </div>
      <div class="stat-label">${cacheRate > 80 ? 'reusing prior context well' : cacheRate > 50 ? 'some context reuse' : 'mostly fresh context'}</div>
    </div>

    <!-- Daily sparkline -->
    ${dailyVals.length > 0 ? `
    <div>
      <div class="stat-label">daily tokens (14d)</div>
      <div class="sparkline">
        ${dailyVals.map((v, i) => {
            const h = Math.max(2, Math.round((v / sparkMax) * 24));
            const ratio = v / sparkMax;
            const col = ratio > 0.7 ? C.amberL : ratio > 0.4 ? C.violet : C.cyan;
            const isToday = i === dailyVals.length - 1;
            return `<div class="spark-bar" style="height:${h}px; background:${isToday ? C.amber : col}; opacity:${isToday ? 1 : 0.7}" title="${sortedDays[sortedDays.length - dailyVals.length + i] ?? ''}: ${fmtTokens(v)}"></div>`;
        }).join('')}
      </div>
      <div class="stat-label">← oldest · today →</div>
    </div>
    ` : ''}

    <!-- Top models -->
    ${topModels.length > 0 ? `
    <div style="margin-top:10px">
      <div class="stat-label" style="margin-bottom:4px">top models</div>
      ${topModels.map(([name, tok]) => `
      <div class="model-row">
        <span class="model-name">${escHtml(fmtModel(name))}</span>
        <span class="model-tok">${escHtml(fmtTokens(tok))}</span>
      </div>`).join('')}
    </div>
    ` : ''}
  </div>

</div>

<div class="footer">
  <span>last updated ${escHtml(now)}</span>
  <button class="refresh-btn" onclick="refresh()">↻ refresh</button>
</div>

<script>
  const vscode = acquireVsCodeApi();

  // ── Creature animation ────────────────────────────────────────────────
  const EYE_STYLES = [
    ['⌒', '⌒'], ['◕', '◕'], ['●', '●'], ['◠', '◠'],
    ['◉', '◉'], ['◦', '◦'], ['•', '•'], ['○', '○']
  ];
  const PHRASES = [
    "let's go!", "vibing~", "all good!", "smooth sailing~",
    "doing great!", "feeling good!", "cruising along~", "no worries!",
    "looking good!", "keep going!", "nice work!", "on a roll!"
  ];
  const SAD_PHRASES = [
    "ugh, hold on...", "not again~", "waiting...", "brb~",
    "oops!", "one sec...", "hmm...", "hang tight~"
  ];

  let tick = 0;
  const eyeL = document.getElementById('eye-l');
  const eyeR = document.getElementById('eye-r');
  const speech = document.getElementById('speech');
  const creature = document.getElementById('creature');
  const hasError = ${warning ? 'true' : 'false'};

  function animateTick() {
    tick++;
    // Eyes: rotate style every 40 ticks (~4s), blink every 20 ticks
    if (tick % 20 === 0 || tick % 20 === 1) {
      if (eyeL) eyeL.textContent = '^';
      if (eyeR) eyeR.textContent = '^';
    } else {
      const styleIdx = Math.floor(tick / 40) % EYE_STYLES.length;
      const [l, r] = EYE_STYLES[styleIdx];
      if (eyeL) eyeL.textContent = l;
      if (eyeR) eyeR.textContent = r;
    }

    // Speech bubble: rotate phrase every 20 ticks
    if (tick % 20 === 0 && speech) {
      const phrases = hasError ? SAD_PHRASES : PHRASES;
      speech.textContent = phrases[Math.floor(tick / 20) % phrases.length];
    }

    // Bounce: every 120 ticks, add bounce class for 0.4s
    if (tick % 120 === 0 && creature) {
      creature.classList.add('bouncing');
      setTimeout(() => creature && creature.classList.remove('bouncing'), 400);
    }
  }

  setInterval(animateTick, 100);

  // ── Countdown timers ──────────────────────────────────────────────────
  const fhResetIso = ${fh.resets_at ? JSON.stringify(fh.resets_at) : 'null'};
  const sdResetIso = ${sd.resets_at ? JSON.stringify(sd.resets_at) : 'null'};

  function fmtUntil(iso) {
    if (!iso) return '—';
    try {
      let secs = Math.max(0, Math.floor((new Date(iso) - Date.now()) / 1000));
      const d = Math.floor(secs / 86400); secs -= d * 86400;
      const h = Math.floor(secs / 3600); secs -= h * 3600;
      const m = Math.floor(secs / 60); secs -= m * 60;
      if (d > 0) return d + 'd ' + h + 'h';
      if (h > 0) return h + 'h ' + String(m).padStart(2,'0') + 'm';
      if (m > 0) return m + 'm ' + String(secs).padStart(2,'0') + 's';
      return secs + 's';
    } catch { return '—'; }
  }

  // Update reset countdowns + time bars every second
  const fhResets = document.querySelectorAll('.fh-reset');
  const sdResets = document.querySelectorAll('.sd-reset');
  const fhTimeBar = document.getElementById('fh-time-bar');
  const sdTimeBar = document.getElementById('sd-time-bar');
  setInterval(() => {
    const ft = fmtUntil(fhResetIso);
    const st = fmtUntil(sdResetIso);
    fhResets.forEach(el => el.textContent = ft);
    sdResets.forEach(el => el.textContent = st);

    if (fhResetIso && fhTimeBar) {
      const secsLeft = Math.max(0, (new Date(fhResetIso) - Date.now()) / 1000);
      const pct = Math.min(100, Math.max(0, (18000 - secsLeft) / 18000 * 100));
      fhTimeBar.style.width = pct.toFixed(2) + '%';
    }
    if (sdResetIso && sdTimeBar) {
      const secsLeft = Math.max(0, (new Date(sdResetIso) - Date.now()) / 1000);
      const pct = Math.min(100, Math.max(0, (604800 - secsLeft) / 604800 * 100));
      sdTimeBar.style.width = pct.toFixed(2) + '%';
    }
  }, 1000);

  // ── Message from extension ────────────────────────────────────────────
  window.addEventListener('message', event => {
    if (event.data.type === 'refresh') {
      // Full reload handled by extension replacing the webview HTML
    }
  });

  function refresh() {
    vscode.postMessage({ type: 'refresh' });
  }
</script>
</body>
</html>`;
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function getPhrase(isError: boolean): string {
    const phrases = isError
        ? ["ugh, hold on...", "not again~", "waiting...", "brb~"]
        : ["let's go!", "vibing~", "all good!", "smooth sailing~"];
    return phrases[Math.floor(Math.random() * phrases.length)];
}

function barColor(pct: number): string {
    if (pct >= 90) return C.red;
    if (pct >= 70) return C.orange;
    if (pct >= 40) return C.amberL;
    return C.green;
}

function timeElapsedPct(resetsAt: string | null | undefined, windowSecs: number): number {
    if (!resetsAt) return 0;
    try {
        const secsLeft = Math.max(0, (new Date(resetsAt).getTime() - Date.now()) / 1000);
        const elapsed = Math.max(0, windowSecs - secsLeft);
        return Math.min(100, (elapsed / windowSecs) * 100);
    } catch { return 0; }
}
