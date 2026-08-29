// Shared helpers for the username/password Edge Functions.
//
// This module intentionally uses only Web APIs available in Supabase Edge
// Functions. It never returns or logs an Auth email, user id, password,
// service-role key, provider response or rate-limit secret.

export const USERNAME_RE = /^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$/;
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const MAX_BODY_BYTES = 8192;
export const INVALID_CREDENTIALS = Object.freeze({ error: "invalid_credentials" });
export const GENERIC_RECOVERY_MESSAGE = Object.freeze({
  message: "如果该账户允许找回，系统会向已登记的找回邮箱发送邮件。",
});

const DUMMY_EMAIL = "invalid-login@invalid.local";
const AUTH_ENDPOINT_PATHS = (name: string): string[] => [`/${name}`, `/functions/v1/${name}`];

export class RequestShapeError extends Error {
  constructor(message = "request shape is invalid") {
    super(message);
    this.name = "RequestShapeError";
  }
}

export class AuthServiceError extends Error {
  constructor(message = "auth service is unavailable") {
    super(message);
    this.name = "AuthServiceError";
  }
}

function env(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new AuthServiceError("server configuration is unavailable");
  return value;
}

export function supabaseUrl(): string {
  const value = env("SUPABASE_URL").replace(/\/+$/, "");
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new AuthServiceError("server configuration is unavailable");
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new AuthServiceError("server configuration is unavailable");
  }
  return value;
}

function publicAnonKey(): string {
  // SUPABASE_ANON_KEY is present in the standard Supabase Edge environment.
  // The publishable-key fallback supports projects that have migrated naming.
  const value = Deno.env.get("SUPABASE_ANON_KEY")?.trim()
    || Deno.env.get("SUPABASE_PUBLISHABLE_KEY")?.trim();
  if (!value) throw new AuthServiceError("server configuration is unavailable");
  return value;
}

function serviceRoleKey(): string {
  return env("SUPABASE_SERVICE_ROLE_KEY");
}

export function allowedOrigin(request: Request): string | null {
  const configured = env("DASHBOARD_ALLOWED_ORIGIN");
  const origin = request.headers.get("origin");
  if (!origin || origin !== configured) return null;
  try {
    const parsed = new URL(origin);
    if (parsed.protocol !== "https:" && parsed.hostname !== "localhost" && parsed.hostname !== "127.0.0.1") return null;
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) return null;
  } catch {
    return null;
  }
  return origin;
}

export function recoveryRedirectUrl(origin: string): string {
  const value = env("DASHBOARD_RECOVERY_REDIRECT_URL");
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new AuthServiceError("server configuration is unavailable");
  }
  if (parsed.origin !== origin || parsed.username || parsed.password || parsed.hash) {
    throw new AuthServiceError("server configuration is unavailable");
  }
  if (parsed.protocol !== "https:" && parsed.hostname !== "localhost" && parsed.hostname !== "127.0.0.1") {
    throw new AuthServiceError("server configuration is unavailable");
  }
  return value;
}

export function isEndpointPath(request: Request, functionName: string): boolean {
  const url = new URL(request.url);
  return AUTH_ENDPOINT_PATHS(functionName).includes(url.pathname) && !url.search && !url.hash;
}

export function corsHeaders(origin: string): HeadersInit {
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-max-age": "600",
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    vary: "Origin",
  };
}

export function jsonResponse(body: unknown, status: number, origin: string): Response {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders(origin) });
}

export function forbiddenResponse(): Response {
  return new Response("forbidden", { status: 403, headers: { "cache-control": "no-store" } });
}

export function methodResponse(origin: string): Response {
  return jsonResponse({ error: "method_not_allowed" }, 405, origin);
}

export function preflightResponse(origin: string): Response {
  return new Response(null, { status: 204, headers: corsHeaders(origin) });
}

export function normalizeUsername(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 128) return null;
  let normalized: string;
  try {
    normalized = value.normalize("NFKC").trim().toLowerCase();
  } catch {
    return null;
  }
  if (!/^[\x00-\x7f]*$/.test(normalized) || !USERNAME_RE.test(normalized)) return null;
  return normalized;
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export async function readJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") throw new RequestShapeError();
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null && (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_BODY_BYTES)) {
    throw new RequestShapeError();
  }
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new RequestShapeError();
  try {
    return JSON.parse(raw);
  } catch {
    throw new RequestShapeError();
  }
}

export function exactKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const expected = new Set(allowed);
  return Object.keys(value).every((key) => expected.has(key));
}

export function parseLoginInput(value: unknown): { username: string; password: string } | null {
  if (!isPlainObject(value) || !exactKeys(value, ["username", "password"])) return null;
  const username = normalizeUsername(value.username);
  if (!username || typeof value.password !== "string" || value.password.length === 0) return null;
  return { username, password: value.password };
}

export function parseRecoveryInput(value: unknown): { username: string } | null {
  if (!isPlainObject(value) || !exactKeys(value, ["username"])) return null;
  const username = normalizeUsername(value.username);
  return username ? { username } : null;
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
}

function clientAddress(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim();
  const candidate = request.headers.get("cf-connecting-ip")?.trim()
    || forwarded
    || request.headers.get("x-real-ip")?.trim()
    || "unknown";
  return candidate.slice(0, 256) || "unknown";
}

export async function rateLimitHashes(request: Request, username: string): Promise<{ ipHash: string; usernameHash: string }> {
  const secret = env("DASHBOARD_RATE_LIMIT_SECRET");
  const [ipHash, usernameHash] = await Promise.all([
    hmacHex(secret, `ip\u0000${clientAddress(request)}`),
    hmacHex(secret, `username\u0000${username}`),
  ]);
  return { ipHash, usernameHash };
}

function serviceHeaders(): HeadersInit {
  const key = serviceRoleKey();
  return {
    accept: "application/json",
    apikey: key,
    authorization: `Bearer ${key}`,
    "content-type": "application/json",
  };
}

async function rpc(name: string, body: Record<string, unknown>): Promise<unknown> {
  const response = await fetch(`${supabaseUrl()}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: serviceHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new AuthServiceError("auth service is unavailable");
  try {
    return await response.json();
  } catch {
    throw new AuthServiceError("auth service is unavailable");
  }
}

function unwrapJsonb(value: unknown): unknown {
  if (Array.isArray(value) && value.length === 1) return value[0];
  return value;
}

export async function rateLimitAdmit(request: Request, username: string): Promise<boolean> {
  const { ipHash, usernameHash } = await rateLimitHashes(request, username);
  const value = unwrapJsonb(await rpc("app_auth_rate_limit_admit", {
    p_ip_hash: ipHash,
    p_username_hash: usernameHash,
  }));
  if (!isPlainObject(value) || typeof value.allowed !== "boolean") {
    throw new AuthServiceError("auth service is unavailable");
  }
  return value.allowed;
}

export async function rateLimitRecordFailure(request: Request, username: string): Promise<void> {
  const { ipHash, usernameHash } = await rateLimitHashes(request, username);
  const value = unwrapJsonb(await rpc("app_auth_rate_limit_record_failure", {
    p_ip_hash: ipHash,
    p_username_hash: usernameHash,
  }));
  if (!isPlainObject(value) || value.recorded !== true) {
    throw new AuthServiceError("auth service is unavailable");
  }
}

function userIdFromRpc(value: unknown): string | null {
  const unwrapped = unwrapJsonb(value);
  if (typeof unwrapped === "string" && UUID_RE.test(unwrapped)) return unwrapped;
  if (isPlainObject(unwrapped) && typeof unwrapped.user_id === "string" && UUID_RE.test(unwrapped.user_id)) {
    return unwrapped.user_id;
  }
  return null;
}

async function resolveUserId(username: string): Promise<string | null> {
  return userIdFromRpc(await rpc("app_resolve_username", { p_username: username }));
}

async function authAdminUser(userId: string): Promise<{ email: string } | null> {
  const key = serviceRoleKey();
  const response = await fetch(`${supabaseUrl()}/auth/v1/admin/users/${encodeURIComponent(userId)}`, {
    headers: {
      accept: "application/json",
      apikey: key,
      authorization: `Bearer ${key}`,
    },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new AuthServiceError("auth service is unavailable");
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new AuthServiceError("auth service is unavailable");
  }
  if (!isPlainObject(value) || typeof value.email !== "string" || !value.email) return null;
  if (typeof value.deleted_at === "string" && value.deleted_at) return null;
  if (typeof value.banned_until === "string") {
    const bannedUntil = Date.parse(value.banned_until);
    if (Number.isFinite(bannedUntil) && bannedUntil > Date.now()) return null;
  }
  return { email: value.email };
}

export async function lookupAuthEmail(username: string): Promise<string | null> {
  const userId = await resolveUserId(username);
  if (!userId) return null;
  return (await authAdminUser(userId))?.email || null;
}

class InvalidPasswordError extends Error {
  constructor() {
    super("invalid credentials");
    this.name = "InvalidPasswordError";
  }
}

async function passwordGrant(email: string, password: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${supabaseUrl()}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: {
      accept: "application/json",
      apikey: publicAnonKey(),
      "content-type": "application/json",
    },
    body: JSON.stringify({ email, password }),
  });
  if (response.status === 400 || response.status === 401 || response.status === 403) {
    throw new InvalidPasswordError();
  }
  if (!response.ok) throw new AuthServiceError("auth service is unavailable");
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new AuthServiceError("auth service is unavailable");
  }
  if (!isPlainObject(value)
    || typeof value.access_token !== "string"
    || !value.access_token
    || typeof value.refresh_token !== "string"
    || !value.refresh_token) {
    throw new AuthServiceError("auth service is unavailable");
  }
  return value;
}

function safeSession(value: Record<string, unknown>): Record<string, unknown> {
  const expiresIn = typeof value.expires_in === "number" && Number.isFinite(value.expires_in) && value.expires_in > 0
    ? Math.floor(value.expires_in)
    : null;
  if (expiresIn === null) throw new AuthServiceError("auth service is unavailable");
  const rawExpiresAt = typeof value.expires_at === "number" && Number.isFinite(value.expires_at)
    ? Math.floor(value.expires_at)
    : Math.floor(Date.now() / 1000) + expiresIn;
  return {
    access_token: value.access_token,
    refresh_token: value.refresh_token,
    expires_in: expiresIn,
    expires_at: rawExpiresAt,
    token_type: typeof value.token_type === "string" && value.token_type ? value.token_type : "bearer",
  };
}

export async function verifyPassword(username: string, password: string): Promise<Record<string, unknown>> {
  const email = await lookupAuthEmail(username);
  try {
    return safeSession(await passwordGrant(email || DUMMY_EMAIL, email ? password : crypto.randomUUID()));
  } catch (error) {
    if (error instanceof InvalidPasswordError) throw error;
    throw error;
  }
}

export async function sendRecovery(username: string, origin: string): Promise<void> {
  const email = await lookupAuthEmail(username);
  const target = email || "invalid-recovery@invalid.local";
  const response = await fetch(`${supabaseUrl()}/auth/v1/recover`, {
    method: "POST",
    headers: {
      accept: "application/json",
      apikey: publicAnonKey(),
      "content-type": "application/json",
    },
    body: JSON.stringify({ email: target, redirect_to: recoveryRedirectUrl(origin) }),
  });
  if (!response.ok) throw new AuthServiceError("auth service is unavailable");
}

export function isInvalidPasswordError(error: unknown): boolean {
  return error instanceof InvalidPasswordError;
}

export function isServiceError(error: unknown): boolean {
  return error instanceof AuthServiceError;
}
