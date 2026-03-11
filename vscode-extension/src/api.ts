import * as https from 'https';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as crypto from 'crypto';
import { execSync } from 'child_process';

// ── Data shapes (from claude.ai API response) ────────────────────────────────

export interface WindowStats {
    utilization: number | null;
    resets_at: string | null;
}

export interface UsageData {
    five_hour?: WindowStats;
    fiveHour?: WindowStats;
    seven_day?: WindowStats;
    sevenDay?: WindowStats;
    plan?: string;
    subscription_type?: string;
    _cached_at?: number;
}

export interface LocalStats {
    totalTokens: number;
    projects: number;
    sessions: number;
    dailyTokens: Record<string, number>;
    models: Record<string, number>;
    tokens5h: number;
    cacheHitRate: number;
}

export interface FetchResult {
    data?: UsageData;
    error?: string;
    rateLimit?: boolean;
    retryAfter?: number | null;
}

export class RateLimitedError extends Error {
    constructor(public readonly retryAfter: number | null) {
        super(`Rate limited (retry after ${retryAfter}s)`);
    }
}

// ── Normalisation helpers ─────────────────────────────────────────────────────

export function getFiveHour(d: UsageData): WindowStats {
    return d.five_hour ?? d.fiveHour ?? { utilization: null, resets_at: null };
}

export function getSevenDay(d: UsageData): WindowStats {
    return d.seven_day ?? d.sevenDay ?? { utilization: null, resets_at: null };
}

export function getPlan(d: UsageData): string {
    return d.plan ?? d.subscription_type ?? '';
}

// ── HTTP helper ───────────────────────────────────────────────────────────────

interface RawResponse {
    status: number;
    headers: Record<string, string | string[] | undefined>;
    body: string;
}

function httpsGet(host: string, urlPath: string, headers: Record<string, string>): Promise<RawResponse> {
    return new Promise((resolve, reject) => {
        const req = https.request(
            {
                hostname: host,
                path: urlPath,
                method: 'GET',
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    ...headers,
                },
            },
            (res) => {
                let body = '';
                res.on('data', (chunk: Buffer) => (body += chunk.toString()));
                res.on('end', () =>
                    resolve({
                        status: res.statusCode ?? 0,
                        headers: res.headers as Record<string, string | string[] | undefined>,
                        body,
                    })
                );
            }
        );
        req.on('error', reject);
        req.setTimeout(10_000, () => {
            req.destroy(new Error('Request timed out'));
        });
        req.end();
    });
}

// ── Disk cache (~/.claude/.clu_cache.json) ────────────────────────────────────
// Shared with the Python CLI — both tools read/write the same file.

const CACHE_FILE = path.join(os.homedir(), '.claude', '.clu_cache.json');
const CACHE_TTL_SECS = 300;

export function loadCache(): UsageData | null {
    try {
        const data: UsageData = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8'));
        const cachedAt = data._cached_at ?? 0;
        if (Date.now() / 1000 - cachedAt < CACHE_TTL_SECS) {
            return data;
        }
    } catch {
        /* no cache or parse error */
    }
    return null;
}

function saveCache(data: UsageData): void {
    try {
        fs.writeFileSync(CACHE_FILE, JSON.stringify({ ...data, _cached_at: Date.now() / 1000 }));
    } catch {
        /* ignore write errors */
    }
}

// ── Token resolution ──────────────────────────────────────────────────────────
// Mirrors get_token() from clu.py, same priority order.

export function resolveToken(): string | null {
    // 1. Env var
    if (process.env['CLAUDE_TOKEN']) return process.env['CLAUDE_TOKEN'].trim();

    // 2. macOS Keychain
    if (process.platform === 'darwin') {
        const services = [
            'Claude Code-credentials',
            'claude.ai',
            'Claude Code',
            'Anthropic Claude',
            'Claude',
        ];
        for (const svc of services) {
            try {
                const out = execSync(
                    `security find-generic-password -s "${svc}" -w`,
                    { stdio: ['pipe', 'pipe', 'pipe'], timeout: 3000 }
                )
                    .toString()
                    .trim();
                if (out.startsWith('{')) {
                    try {
                        const blob = JSON.parse(out) as Record<string, unknown>;
                        for (const v of Object.values(blob)) {
                            if (v && typeof v === 'object' && (v as Record<string, unknown>)['accessToken']) {
                                return String((v as Record<string, unknown>)['accessToken']).trim();
                            }
                        }
                    } catch { /* not JSON */ }
                }
                if (out.length > 20) return out;
            } catch { /* not in this service */ }
        }
    }

    // 3. Credential files
    const home = os.homedir();
    const credPaths = [
        path.join(home, '.claude', '.credentials.json'),
        path.join(home, '.config', 'claude', 'credentials.json'),
        path.join(home, '.claude', 'auth.json'),
        path.join(home, '.claude', 'session.json'),
        path.join(home, 'Library', 'Application Support', 'Claude', 'credentials.json'),
    ];
    const tokenKeys = [
        'access_token', 'oauth_token', 'token',
        'claudeAiOauthToken', 'session_key', 'sessionKey',
    ];
    for (const p of credPaths) {
        try {
            if (fs.existsSync(p)) {
                const data = JSON.parse(fs.readFileSync(p, 'utf8')) as Record<string, unknown>;
                for (const k of tokenKeys) {
                    if (data[k]) return String(data[k]).trim();
                }
            }
        } catch { /* skip */ }
    }

    return null;
}

// ── Session key resolution ────────────────────────────────────────────────────
// Mirrors _get_session_key() from clu.py.

export function resolveSessionKey(override?: string): string | null {
    // 0. Explicit override (from VSCode settings)
    if (override) return override.trim();

    // 1. Env var
    if (process.env['CLU_SESSION_KEY']) return process.env['CLU_SESSION_KEY'].trim();

    // 2. File cache ~/.claude/.clu_session_key
    const skFile = path.join(os.homedir(), '.claude', '.clu_session_key');
    try {
        if (fs.existsSync(skFile)) {
            const sk = fs.readFileSync(skFile, 'utf8').trim();
            if (sk) return sk;
        }
    } catch { /* skip */ }

    // 3. Try to read + decrypt from Claude Desktop cookie store (macOS)
    if (process.platform === 'darwin') {
        const decrypted = decryptClaudeDesktopSessionKey();
        if (decrypted) {
            // Cache it for next time
            try {
                fs.writeFileSync(skFile, decrypted, { mode: 0o600 });
            } catch { /* skip */ }
            return decrypted;
        }
    }

    return null;
}

function decryptClaudeDesktopSessionKey(): string | null {
    // Mirrors _decrypt_session_key() from clu.py.
    // Uses macOS Keychain for the AES key + sqlite3 CLI to read cookies.
    try {
        const cookieDb = path.join(
            os.homedir(),
            'Library', 'Application Support', 'Claude', 'Cookies'
        );
        if (!fs.existsSync(cookieDb)) return null;

        // Get the AES key password from Keychain
        const keyPassword = execSync(
            'security find-generic-password -s "Claude Safe Storage" -a "Claude Key" -w',
            { stdio: ['pipe', 'pipe', 'pipe'], timeout: 5000 }
        )
            .toString()
            .trim();
        if (!keyPassword) return null;

        // Derive AES key using PBKDF2 (same params as Chromium)
        const derivedKey = crypto.pbkdf2Sync(
            keyPassword, 'saltysalt', 1003, 16, 'sha1'
        );

        // Read the encrypted cookie value via sqlite3 CLI
        const tmpDb = `/tmp/clu_cookies_${Date.now()}.db`;
        try {
            fs.copyFileSync(cookieDb, tmpDb);
            const encHex = execSync(
                `sqlite3 "${tmpDb}" "SELECT hex(encrypted_value) FROM cookies WHERE name='sessionKey' AND host_key LIKE '%claude%' LIMIT 1"`,
                { stdio: ['pipe', 'pipe', 'pipe'], timeout: 5000 }
            )
                .toString()
                .trim();

            if (!encHex) return null;

            const encBuf = Buffer.from(encHex, 'hex');
            // Chromium v10 prefix: first 3 bytes are 'v10', rest is AES-CBC ciphertext
            if (encBuf.slice(0, 3).toString() !== 'v10') return null;

            const iv = Buffer.alloc(16, ' ');
            const decipher = crypto.createDecipheriv('aes-128-cbc', derivedKey, iv);
            decipher.setAutoPadding(false);
            const decrypted = Buffer.concat([decipher.update(encBuf.slice(3)), decipher.final()]);

            // Remove PKCS7 padding
            const padLen = decrypted[decrypted.length - 1];
            return decrypted.slice(0, decrypted.length - padLen).toString('utf8');
        } finally {
            try { fs.unlinkSync(tmpDb); } catch { /* ignore */ }
        }
    } catch {
        return null;
    }
}

// ── Org ID resolution ─────────────────────────────────────────────────────────

export async function resolveOrgId(token: string, override?: string): Promise<string | null> {
    // 0. Explicit override (from VSCode settings)
    if (override) return override.trim();

    // 1. Env var
    if (process.env['CLU_ORG_ID']) return process.env['CLU_ORG_ID'].trim();

    // 2. Fetch from OAuth profile endpoint (same as clu.py's _get_org_id)
    try {
        const resp = await httpsGet(
            'api.anthropic.com',
            '/api/oauth/profile',
            {
                'Authorization': `Bearer ${token}`,
                'anthropic-beta': 'oauth-2025-04-20',
            }
        );
        if (resp.status === 200) {
            const data = JSON.parse(resp.body) as { organization?: { uuid?: string } };
            return data.organization?.uuid ?? null;
        }
    } catch { /* fall through */ }

    return null;
}

// ── Usage fetch ───────────────────────────────────────────────────────────────
// Two-strategy fetch mirroring fetch_usage() in clu.py.

export async function fetchUsage(
    token: string,
    sessionKey: string | null,
    orgId: string | null
): Promise<UsageData> {

    // ── Strategy 1: claude.ai web API (session cookie) ────────────────────
    // Note: Unlike Python's cloudscraper, plain Node https may be blocked by
    // Cloudflare on some requests. We fall through gracefully on 403/503.
    if (sessionKey && orgId) {
        try {
            const resp = await httpsGet(
                'claude.ai',
                `/api/organizations/${orgId}/usage`,
                {
                    'Cookie': `sessionKey=${sessionKey}`,
                    'Accept': 'application/json',
                    'Referer': 'https://claude.ai/',
                }
            );
            if (resp.status === 200) {
                const data = JSON.parse(resp.body) as UsageData;
                saveCache(data);
                return data;
            }
            if (resp.status === 401 || resp.status === 403) {
                throw new Error(`Session expired (${resp.status})`);
            }
            // 429, 503, etc. — fall through to OAuth
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            if (msg.includes('Session expired')) throw e;
            // Network/Cloudflare errors — fall through silently
        }
    }

    // ── Strategy 2: OAuth API (fallback, may be blocked on consumer plans) ─
    if (!token) {
        throw new Error('No auth token available');
    }

    const resp = await httpsGet(
        'api.anthropic.com',
        '/api/oauth/usage',
        {
            'Authorization': `Bearer ${token}`,
            'anthropic-beta': 'oauth-2025-04-20',
        }
    );

    if (resp.status === 429) {
        // CRITICAL: Retry-After: 0 is falsy — must check !== undefined before parsing.
        // This is the same bug fixed in clu.py: was `if (retry_after)` which skipped 0.
        const retryHeader = resp.headers['retry-after'] ?? resp.headers['Retry-After'];
        const retryStr = Array.isArray(retryHeader) ? retryHeader[0] : retryHeader;
        const retryAfter = retryStr !== undefined && /^\d+$/.test(retryStr)
            ? parseInt(retryStr, 10)
            : null;
        throw new RateLimitedError(retryAfter);
    }

    if (resp.status !== 200) {
        throw new Error(`HTTP ${resp.status}`);
    }

    const data = JSON.parse(resp.body) as UsageData;
    saveCache(data);
    return data;
}

// ── Local JSONL data parser ───────────────────────────────────────────────────
// Lightweight port of parse_project_data() from clu.py.
// Reads ~/.claude/projects/**/*.jsonl for local token stats.

interface JsonlEntry {
    timestamp?: string;
    sessionId?: string;
    message?: {
        role?: string;
        usage?: {
            input_tokens?: number;
            output_tokens?: number;
            cache_read_input_tokens?: number;
            cache_creation_input_tokens?: number;
        };
        model?: string;
    };
    type?: string;
}

export function parseLocalData(claudeDir?: string): LocalStats {
    const baseDir = claudeDir ?? path.join(os.homedir(), '.claude');
    const projectsDir = path.join(baseDir, 'projects');

    const result: LocalStats = {
        totalTokens: 0,
        projects: 0,
        sessions: 0,
        dailyTokens: {},
        models: {},
        tokens5h: 0,
        cacheHitRate: 0,
    };

    if (!fs.existsSync(projectsDir)) return result;

    const cutoff5h = Date.now() - 5 * 60 * 60 * 1000;
    const projectNames = new Set<string>();
    const sessionIds = new Set<string>();
    let totalCacheRead = 0;
    let totalCacheCreate = 0;

    let projDirs: fs.Dirent[];
    try {
        projDirs = fs.readdirSync(projectsDir, { withFileTypes: true });
    } catch {
        return result;
    }

    for (const projEnt of projDirs) {
        if (!projEnt.isDirectory()) continue;
        const projPath = path.join(projectsDir, projEnt.name);
        projectNames.add(projEnt.name);

        let jsonlFiles: fs.Dirent[];
        try {
            jsonlFiles = fs.readdirSync(projPath, { withFileTypes: true });
        } catch {
            continue;
        }

        for (const f of jsonlFiles) {
            if (!f.name.endsWith('.jsonl')) continue;
            const filePath = path.join(projPath, f.name);

            let lines: string[];
            try {
                lines = fs.readFileSync(filePath, 'utf8').split('\n');
            } catch {
                continue;
            }

            for (const line of lines) {
                if (!line.trim()) continue;
                let entry: JsonlEntry;
                try {
                    entry = JSON.parse(line) as JsonlEntry;
                } catch {
                    continue;
                }

                const role = entry.message?.role ?? entry.type ?? '';
                if (role !== 'assistant') continue;

                const usage = entry.message?.usage;
                if (!usage) continue;

                const inp = usage.input_tokens ?? 0;
                const out = usage.output_tokens ?? 0;
                const cacheR = usage.cache_read_input_tokens ?? 0;
                const cacheC = usage.cache_creation_input_tokens ?? 0;
                const total = inp + out + cacheR + cacheC;

                result.totalTokens += total;
                totalCacheRead += cacheR;
                totalCacheCreate += cacheC;

                const model = entry.message?.model ?? '';
                if (model && !model.includes('synthetic')) {
                    result.models[model] = (result.models[model] ?? 0) + total;
                }

                if (entry.sessionId) sessionIds.add(entry.sessionId);

                if (entry.timestamp) {
                    try {
                        const ts = new Date(entry.timestamp);
                        const dayKey = ts.toISOString().slice(0, 10);
                        result.dailyTokens[dayKey] = (result.dailyTokens[dayKey] ?? 0) + total;
                        if (ts.getTime() >= cutoff5h) {
                            result.tokens5h += total;
                        }
                    } catch { /* skip */ }
                }
            }
        }
    }

    result.projects = projectNames.size;
    result.sessions = sessionIds.size;

    const cacheTotal = totalCacheRead + totalCacheCreate;
    result.cacheHitRate = cacheTotal > 0 ? (totalCacheRead / cacheTotal) * 100 : 0;

    return result;
}
