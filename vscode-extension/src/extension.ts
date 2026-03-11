import * as vscode from 'vscode';
import {
    resolveToken,
    resolveSessionKey,
    resolveOrgId,
    fetchUsage,
    parseLocalData,
    loadCache,
    UsageData,
    LocalStats,
    RateLimitedError,
    getFiveHour,
    getSevenDay,
    getPlan,
} from './api';
import { getDashboardHtml } from './panel';

// ── State ─────────────────────────────────────────────────────────────────────

let statusBar: vscode.StatusBarItem;
let mainTimer: ReturnType<typeof setInterval> | undefined;
let localTimer: ReturnType<typeof setInterval> | undefined;
let dashPanel: vscode.WebviewPanel | undefined;

let usageData: UsageData | undefined;
let localData: LocalStats | undefined;
let orgIdCache: string | null = null;

let errorMsg: string | undefined;
let nextFetchAt: number = 0;       // epoch ms — when to next hit the API
let backoffMs: number = 30_000;    // starts at 30s, mirrors clu.py's _INITIAL_BACKOFF
let tick: number = 0;

const INITIAL_BACKOFF_MS = 30_000;
const SPINNERS = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'];

// ── Activate / Deactivate ─────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext): void {
    // Status bar — right side, click opens dashboard
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.command = 'clu.openDashboard';
    statusBar.text = '$(sync~spin) clu';
    statusBar.tooltip = 'clu — Claude Usage Monitor';
    statusBar.show();
    context.subscriptions.push(statusBar);

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('clu.refresh', () => {
            nextFetchAt = 0; // force immediate fetch on next tick
        }),
        vscode.commands.registerCommand('clu.openDashboard', () => {
            openDashboard(context);
        })
    );

    // Pre-load from shared disk cache (avoids cold-start API call if Python CLI ran recently)
    const cached = loadCache();
    if (cached) {
        usageData = cached;
        renderStatusBar();
    }

    // Parse local JSONL data immediately (non-blocking, but synchronous for now)
    refreshLocalData();

    // Main tick loop — 500ms like clu.py's time.sleep(0.5)
    mainTimer = setInterval(onTick, 500);

    // Local data refresh every 5 minutes (same as clu.py's next_local_refresh = now + 300)
    localTimer = setInterval(refreshLocalData, 5 * 60 * 1000);

    // Config change handler — reset org ID cache + restart timer
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration(e => {
            if (e.affectsConfiguration('clu')) {
                orgIdCache = null;
                nextFetchAt = 0;
            }
        })
    );
}

export function deactivate(): void {
    if (mainTimer !== undefined) clearInterval(mainTimer);
    if (localTimer !== undefined) clearInterval(localTimer);
}

// ── Tick loop ─────────────────────────────────────────────────────────────────

function onTick(): void {
    tick++;

    // Update spinner while loading
    if (usageData === undefined && !errorMsg) {
        const spinner = SPINNERS[tick % SPINNERS.length];
        statusBar.text = `◆ clu  ${spinner}`;
    }

    // Countdown display while rate-limited or backing off
    if (errorMsg && nextFetchAt > Date.now()) {
        const secsLeft = Math.ceil((nextFetchAt - Date.now()) / 1000);
        statusBar.text = `◆ clu  ✕ ${errorMsg} (${secsLeft}s)`;
        return;
    }

    // Time to fetch?
    if (Date.now() >= nextFetchAt) {
        doFetch(); // fire-and-forget
    }
}

// ── API fetch ─────────────────────────────────────────────────────────────────

async function doFetch(): Promise<void> {
    // Prevent concurrent fetches
    nextFetchAt = Date.now() + 999_999_999;

    const config = vscode.workspace.getConfiguration('clu');
    const configSessionKey = config.get<string>('sessionKey') || undefined;
    const configOrgId      = config.get<string>('orgId')      || undefined;
    const refreshSecs      = Math.max(30, config.get<number>('refreshInterval') ?? 90);

    const token = resolveToken();
    if (!token) {
        errorMsg = 'no auth token';
        statusBar.text = '$(warning) clu';
        statusBar.tooltip = 'clu: No auth token found.\nSet CLAUDE_TOKEN env var or sign in to Claude Code.';
        statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        nextFetchAt = Date.now() + refreshSecs * 1000;
        return;
    }

    if (!orgIdCache) {
        orgIdCache = await resolveOrgId(token, configOrgId);
    }

    const sessionKey = resolveSessionKey(configSessionKey);

    try {
        const data = await fetchUsage(token, sessionKey, orgIdCache);
        usageData = data;
        errorMsg = undefined;
        backoffMs = INITIAL_BACKOFF_MS;
        nextFetchAt = Date.now() + refreshSecs * 1000;

        renderStatusBar();
        if (dashPanel) {
            dashPanel.webview.html = getDashboardHtml(usageData, localData, errorMsg);
        }
    } catch (e: unknown) {
        if (e instanceof RateLimitedError) {
            // CRITICAL: retryAfter can be 0 (falsy) — check !== null, not truthiness.
            // Mirrors the fix in clu.py for Retry-After: 0.
            if (e.retryAfter !== null) {
                const wait = Math.max(e.retryAfter * 1000, 2000);
                nextFetchAt = Date.now() + wait;
                backoffMs = INITIAL_BACKOFF_MS;
            } else {
                const wait = Math.min(backoffMs * 2, refreshSecs * 1000);
                backoffMs = wait;
                nextFetchAt = Date.now() + wait + Math.random() * 3000;
            }
            errorMsg = 'rate limited';
        } else {
            const msg = e instanceof Error ? e.message : String(e);
            errorMsg = msg.length > 32 ? msg.slice(0, 32) + '…' : msg;
            // Session expired — wait for full refresh interval before retrying
            const delay = msg.includes('Session expired') ? refreshSecs * 1000 : 15_000;
            nextFetchAt = Date.now() + delay;
        }

        statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        // Countdown will show on next tick
    }
}

// ── Local data refresh ────────────────────────────────────────────────────────

function refreshLocalData(): void {
    try {
        localData = parseLocalData();
        // Update dashboard if open
        if (dashPanel && usageData) {
            dashPanel.webview.html = getDashboardHtml(usageData, localData, errorMsg);
        }
    } catch { /* ignore JSONL parse errors */ }
}

// ── Status bar rendering ──────────────────────────────────────────────────────

function renderStatusBar(): void {
    if (!usageData) return;

    const fh = getFiveHour(usageData);
    const sd = getSevenDay(usageData);
    const pct5h = fh.utilization;

    if (pct5h == null) {
        statusBar.text = '◆ clu  —';
        statusBar.backgroundColor = undefined;
        statusBar.tooltip = 'clu: No utilization data yet';
        return;
    }

    const pct5hRounded = Math.round(pct5h);
    const sdPct = sd.utilization;

    // Status bar text: "◆ clu  ▓▓▓▓▓░░░░░  45%"
    const barStr = makeBar(pct5h, 10);
    const pctPart = sdPct != null
        ? `${pct5hRounded}% · 7d ${Math.round(sdPct)}%`
        : `${pct5hRounded}%`;
    statusBar.text = `◆ clu  ${barStr}  ${pctPart}`;

    // Background color based on severity
    if (pct5h >= 90) {
        statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    } else if (pct5h >= 70) {
        statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
        statusBar.backgroundColor = undefined;
    }

    // Markdown tooltip with details
    const fhReset = fmtUntil(fh.resets_at);
    const sdReset = fmtUntil(sd.resets_at);
    const plan = getPlan(usageData);

    const md = new vscode.MarkdownString('', true);
    md.isTrusted = true;
    md.appendMarkdown(`**◆ clu** — Claude Usage Monitor\n\n`);
    if (plan) md.appendMarkdown(`Plan: \`${plan}\`\n\n`);
    md.appendMarkdown(`**5h window:** ${pct5hRounded}% — resets in ${fhReset}\n\n`);
    if (sdPct != null) {
        md.appendMarkdown(`**7d window:** ${Math.round(sdPct)}% — resets in ${sdReset}\n\n`);
    }
    if (localData) {
        md.appendMarkdown(`Local: ${fmtTokens(localData.totalTokens)} total · ${localData.projects} projects\n\n`);
    }
    if (errorMsg) {
        md.appendMarkdown(`⚠️ ${errorMsg}\n\n`);
    }
    md.appendMarkdown(`_Click to open dashboard_`);
    statusBar.tooltip = md;
}

// ── Dashboard panel ────────────────────────────────────────────────────────────

function openDashboard(context: vscode.ExtensionContext): void {
    if (dashPanel) {
        dashPanel.reveal(vscode.ViewColumn.Beside);
        return;
    }

    dashPanel = vscode.window.createWebviewPanel(
        'cluDashboard',
        'clu — Claude Usage',
        vscode.ViewColumn.Beside,
        {
            enableScripts: true,
            retainContextWhenHidden: true,
        }
    );

    dashPanel.onDidDispose(() => {
        dashPanel = undefined;
    }, null, context.subscriptions);

    // Handle messages from the webview (e.g. "refresh" button click)
    dashPanel.webview.onDidReceiveMessage(
        (msg: { type: string }) => {
            if (msg.type === 'refresh') {
                nextFetchAt = 0;
            }
        },
        null,
        context.subscriptions
    );

    dashPanel.webview.html = getDashboardHtml(usageData, localData, errorMsg);
}

// ── Formatting helpers ────────────────────────────────────────────────────────

function makeBar(pct: number, width: number): string {
    const ratio = Math.min(Math.max(pct / 100, 0), 1);
    const filled = Math.round(ratio * width);
    const empty = width - filled;
    return '▓'.repeat(filled) + '░'.repeat(empty);
}

function fmtUntil(iso: string | null | undefined): string {
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
