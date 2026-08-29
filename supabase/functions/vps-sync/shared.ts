// Shared, secret-free validation and HMAC helpers for the private VPS gateway.
//
// This module is usable by Deno Edge Functions and Node-focused static tests.
// It contains no service-role key, OAuth key, database URL, VPS address,
// account data or provider credential.  Protocol v2 binds exact endpoint path,
// protocol version and durable request identity into every HMAC signature.

export const PROTOCOL_VERSION = 2;
export const VPS_ADAPTER_ID = "v31f-15m-miaoxiang-sim-adapter";
export const VPS_SOURCE_POLICY_SHA256 = "7330a793c79b3c2f2bf0c52b2d085b98cdc9b851fbc88f7c3d399030d8d54c75";
export const VPS_DEVICE_ID = "stock-sim-v31f-15m";
// The public gateway signs the full Supabase URL path.  Supabase Edge Runtime
// presents the function-local pathname without the /functions/v1 prefix.
export const VPS_SYNC_PATH = "/functions/v1/vps-sync";
export const VPS_SYNC_RUNTIME_PATH = "/vps-sync";
export const SIGNING_SCOPE = "vps-sync";
export const MAX_REQUEST_BYTES = 256 * 1024;
export const MAX_EVENTS = 50;
export const MAX_SYMBOL_STATES = 50;
export const MAX_POSITIONS = 50;
export const MAX_ACKS = 8;

const SYMBOL_RE = /^\d{6}\.(SH|SZ)$/;
const DEVICE_RE = /^[a-z0-9][a-z0-9._-]{2,79}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const REQUEST_ID_RE = /^[0-9a-f]{64}$/;
const SNAPSHOT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/;
const EVENT_CODE_RE = /^[a-z0-9_.-]{1,80}$/;
const STATUS_KEY_RE = /^[a-z0-9_.-]{1,64}$/;
const POSITION_STATE_RE = /^[a-z0-9_.-]{1,64}$/;
const SENSITIVE_TERM_RE = /(authorization|bearer\s+[a-z0-9._-]+|api[_ -]?key|service[_ -]?role|password|secret|access[_ -]?token|refresh[_ -]?token|private[_ -]?key|ssh-rsa|account[_ -]?id|cookie)/i;
const HEALTH = new Set(["ok", "degraded", "failed", "unknown"]);
const ACK_STAGE = new Set(["received", "preparing", "activated", "rejected"]);
const SEVERITY = new Set(["info", "warning", "error"]);

export class GatewayValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GatewayValidationError";
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function rejectUnknownKeys(raw: Record<string, unknown>, allowed: readonly string[], field: string): void {
  const allowedKeys = new Set(allowed);
  for (const key of Object.keys(raw)) {
    if (!allowedKeys.has(key)) throw new GatewayValidationError(`${field} contains an unknown field`);
  }
}

function text(value: unknown, field: string, maxLength: number, { optional = false } = {}): string | null {
  if (value === null || value === undefined || value === "") {
    if (optional) return null;
    throw new GatewayValidationError(`${field} is required`);
  }
  if (typeof value !== "string") throw new GatewayValidationError(`${field} must be text`);
  const trimmed = value.trim();
  if (!trimmed && !optional) throw new GatewayValidationError(`${field} is required`);
  if (trimmed.length > maxLength) throw new GatewayValidationError(`${field} is too long`);
  if (SENSITIVE_TERM_RE.test(trimmed)) throw new GatewayValidationError(`${field} contains a protected term`);
  return trimmed || null;
}

function exactText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value) throw new GatewayValidationError(`${field} is required`);
  if (value.length > maxLength) throw new GatewayValidationError(`${field} is too long`);
  if (SENSITIVE_TERM_RE.test(value)) throw new GatewayValidationError(`${field} contains a protected term`);
  return value;
}

function integer(value: unknown, field: string, { nullable = false, min = 0, max = Number.MAX_SAFE_INTEGER } = {}): number | null {
  if (value === null || value === undefined || value === "") {
    if (nullable) return null;
    throw new GatewayValidationError(`${field} is required`);
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new GatewayValidationError(`${field} is invalid`);
  }
  return value;
}

function realNumber(value: unknown, field: string, { nullable = false, min = -1_000_000_000, max = 1_000_000_000 } = {}): number | null {
  if (value === null || value === undefined || value === "") {
    if (nullable) return null;
    throw new GatewayValidationError(`${field} is required`);
  }
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new GatewayValidationError(`${field} is invalid`);
  }
  return value;
}

function isoTimestamp(value: unknown, field: string, { optional = false } = {}): string | null {
  const candidate = text(value, field, 64, { optional });
  if (candidate === null) return null;
  const parsed = Date.parse(candidate);
  if (!Number.isFinite(parsed) || !/[zZ]|[+-]\d\d:\d\d$/.test(candidate)) {
    throw new GatewayValidationError(`${field} must be an offset ISO timestamp`);
  }
  return candidate;
}

function sha256(value: unknown, field: string, { optional = false } = {}): string | null {
  const candidate = text(value, field, 64, { optional });
  if (candidate === null) return null;
  const normalized = candidate.toLowerCase();
  if (!SHA256_RE.test(normalized)) throw new GatewayValidationError(`${field} must be a lowercase SHA-256`);
  return normalized;
}

function canonicalSymbol(value: unknown, field: string): string {
  const candidate = text(value, field, 16)!;
  if (!SYMBOL_RE.test(candidate)) throw new GatewayValidationError(`${field} is not a canonical A-share symbol`);
  return candidate;
}

function snapshotId(value: unknown, field: string, { optional = false } = {}): string | null {
  const candidate = text(value, field, 160, { optional });
  if (candidate === null) return null;
  if (!SNAPSHOT_ID_RE.test(candidate)) throw new GatewayValidationError(`${field} is invalid`);
  return candidate;
}

function denySensitiveStructure(value: unknown, field = "payload"): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => denySensitiveStructure(item, `${field}[${index}]`));
    return;
  }
  if (isPlainObject(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (SENSITIVE_TERM_RE.test(key)) throw new GatewayValidationError(`${field} contains a protected field`);
      denySensitiveStructure(child, `${field}.${key}`);
    }
    return;
  }
  if (typeof value === "string" && SENSITIVE_TERM_RE.test(value)) {
    throw new GatewayValidationError(`${field} contains a protected term`);
  }
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new GatewayValidationError("non-finite number is forbidden");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isPlainObject(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  throw new GatewayValidationError("unsupported canonical JSON value");
}

export function utf8(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function cryptoBytes(value: string): BufferSource {
  // Deno accepts Uint8Array directly.  The cast keeps TypeScript's newer
  // generic typed-array declaration compatible with the standard WebCrypto
  // BufferSource overload without changing any runtime bytes.
  return utf8(value) as unknown as BufferSource;
}

export function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(value: string): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", cryptoBytes(value)));
}

export async function hmacSha256Hex(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", cryptoBytes(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, cryptoBytes(value)));
}

export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let different = 0;
  for (let index = 0; index < a.length; index += 1) different |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return different === 0;
}

export function validateRequestTarget(url: URL): void {
  if (url.pathname !== VPS_SYNC_PATH || url.search || url.hash) {
    throw new GatewayValidationError("request target is invalid");
  }
}

export function validateRuntimeRequestTarget(url: URL): void {
  if (url.pathname !== VPS_SYNC_RUNTIME_PATH || url.search || url.hash) {
    throw new GatewayValidationError("runtime request target is invalid");
  }
}

export function signingMessage(
  method: string,
  path: string,
  protocolVersion: number,
  timestamp: string,
  nonce: string,
  requestId: string,
  bodySha256: string,
): string {
  return `${method.toUpperCase()}\n${path}\n${protocolVersion}\n${SIGNING_SCOPE}\n${timestamp}\n${nonce}\n${requestId}\n${bodySha256}`;
}

export function validateDeviceId(value: unknown): string {
  const deviceId = text(value, "device_id", 80)!;
  if (!DEVICE_RE.test(deviceId)) throw new GatewayValidationError("device_id is invalid");
  return deviceId;
}

export function validateRequestId(value: unknown): string {
  const requestId = text(value, "request_id", 64)!?.toLowerCase();
  if (!REQUEST_ID_RE.test(requestId)) throw new GatewayValidationError("request_id is invalid");
  return requestId;
}

export type SignedHeaders = {
  deviceId: string;
  protocolVersion: number;
  timestamp: string;
  nonce: string;
  requestId: string;
  signature: string;
};

export function validateSignedHeaders(headers: Headers): SignedHeaders {
  const deviceId = validateDeviceId(headers.get("x-vps-device"));
  const protocol = text(headers.get("x-vps-protocol"), "x-vps-protocol", 8)!;
  if (protocol !== String(PROTOCOL_VERSION)) throw new GatewayValidationError("x-vps-protocol is invalid");
  const timestamp = text(headers.get("x-vps-timestamp"), "x-vps-timestamp", 16)!;
  if (!/^\d{10}$/.test(timestamp)) throw new GatewayValidationError("x-vps-timestamp is invalid");
  const nonce = text(headers.get("x-vps-nonce"), "x-vps-nonce", 128)!;
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(nonce)) throw new GatewayValidationError("x-vps-nonce is invalid");
  const requestId = validateRequestId(headers.get("x-vps-request-id"));
  const signature = (text(headers.get("x-vps-signature"), "x-vps-signature", 64) || "").toLowerCase();
  if (!SHA256_RE.test(signature)) throw new GatewayValidationError("x-vps-signature is invalid");
  return { deviceId, protocolVersion: PROTOCOL_VERSION, timestamp, nonce, requestId, signature };
}

type Envelope = {
  protocol_version: typeof PROTOCOL_VERSION;
  request_id: string;
  device_id: string;
};

function validateEnvelope(raw: unknown, operation: "pull" | "publish"): Envelope {
  if (!isPlainObject(raw) || raw.operation !== operation) throw new GatewayValidationError(`${operation} payload is invalid`);
  const allowed = operation === "pull"
    ? ["protocol_version", "request_id", "operation", "device_id"]
    : ["protocol_version", "request_id", "operation", "device_id", "report"];
  rejectUnknownKeys(raw, allowed, operation);
  denySensitiveStructure(raw);
  if (raw.protocol_version !== PROTOCOL_VERSION) throw new GatewayValidationError("protocol_version is invalid");
  return {
    protocol_version: PROTOCOL_VERSION,
    request_id: validateRequestId(raw.request_id),
    device_id: validateDeviceId(raw.device_id),
  };
}

export function validatePullRequest(raw: unknown): { operation: "pull" } & Envelope {
  const envelope = validateEnvelope(raw, "pull");
  return { operation: "pull", ...envelope };
}

export function validatePublishRequest(raw: unknown): { operation: "publish"; report: RuntimeReport } & Envelope {
  const envelope = validateEnvelope(raw, "publish");
  return {
    operation: "publish",
    ...envelope,
    report: validateRuntimeReport((raw as Record<string, unknown>).report),
  };
}

export type SyncAck = {
  ack_id: string;
  revision_no: number;
  sync_stage: "received" | "preparing" | "activated" | "rejected";
  generation: number | null;
  pack_sha256: string | null;
  control_payload_sha256: string;
  control_raw_contract_sha256: string;
  members_sha256: string;
  required_snapshot_id: string;
  required_snapshot_sha256: string;
  adapter_id: typeof VPS_ADAPTER_ID;
  mode: "DRY_RUN";
  reported_at_cn: string;
  message: string | null;
  rejection_code: string | null;
};

export type SymbolStateReport = {
  symbol: string;
  status_key: string;
  status_reason: string | null;
  source_generated_at_cn: string;
  data_fresh_at_cn: string | null;
  active_revision_no: number | null;
  is_in_active_whitelist: boolean;
};

export type PositionReport = {
  symbol: string;
  held_quantity: number;
  available_quantity: number;
  position_state: string;
  source_generated_at_cn: string;
  active_revision_no: number | null;
};

export type PrivateProjectionPosition = {
  symbol: string;
  display_name: string | null;
  held_quantity: number;
  available_quantity: number;
  average_cost_per_share: number | null;
  current_unadjusted_price: number | null;
  price_as_of: string | null;
  data_status: "complete" | "stale_price" | "missing_price" | "missing_cost" | "unavailable";
  quote_source_kind: "hithink_batch_snapshot" | "not_available";
  position_state: string;
};

export type PrivateProjectionReport = {
  schema_version: 1;
  scope_key: "primary";
  mode: "DRY_RUN";
  health_status: "ok" | "degraded" | "failed" | "unknown";
  projection_sequence: number;
  generated_at: string;
  account_as_of: string | null;
  quote_as_of: string | null;
  source_market_snapshot_id: string | null;
  source_market_snapshot_sha256: string | null;
  active_revision_no: number | null;
  active_generation: number | null;
  active_pack_sha256: string | null;
  active_control_payload_sha256: string | null;
  active_control_raw_contract_sha256: string | null;
  active_members_sha256: string | null;
  active_snapshot_id: string | null;
  active_snapshot_sha256: string | null;
  sanitized_error: string | null;
  positions: PrivateProjectionPosition[];
};

export type EventReport = {
  occurred_at_cn: string;
  severity: "info" | "warning" | "error";
  event_code: string;
  message: string;
  symbol: string | null;
  action: string | null;
  revision_no: number | null;
  generation: number | null;
};

export type RuntimeReport = {
  schema_version: typeof PROTOCOL_VERSION;
  adapter_id: typeof VPS_ADAPTER_ID;
  mode: "DRY_RUN";
  health_status: "ok" | "degraded" | "failed" | "unknown";
  generated_at_cn: string;
  active_revision_no: number | null;
  active_generation: number | null;
  active_pack_sha256: string | null;
  active_control_payload_sha256: string | null;
  active_control_raw_contract_sha256: string | null;
  active_members_sha256: string | null;
  active_snapshot_id: string | null;
  active_snapshot_sha256: string | null;
  last_control_pull_at_cn: string | null;
  last_strategy_cycle_at_cn: string | null;
  last_quote_snapshot_at_cn: string | null;
  last_account_snapshot_at_cn: string | null;
  last_eod_at_cn: string | null;
  provider_reads_used: number | null;
  provider_reads_cap: number | null;
  state_summary: string | null;
  sanitized_error: string | null;
  symbol_states: SymbolStateReport[];
  paper_positions: PositionReport[];
  private_projection?: PrivateProjectionReport;
  events: EventReport[];
  acks: SyncAck[];
};

export type RevisionEnvelope = {
  protocol_version: typeof PROTOCOL_VERSION;
  revision_no: number;
  status: "submitted" | "sync_pending" | "preparing";
  target_device_id: string;
  adapter_id: typeof VPS_ADAPTER_ID;
  mode: "DRY_RUN";
  created_at: string;
  expires_at: string;
  source_policy_sha256: string;
  members_sha256: string;
  required_snapshot_id: string;
  required_snapshot_sha256: string;
  payload_sha256: string;
  raw_contract_sha256: string;
  raw_contract: string;
  symbols: string[];
};

export function revisionMembersText(symbols: readonly string[]): string {
  return `vps-members-v2\n${[...symbols].sort().join("\n")}\n`;
}

export function revisionPayloadText(revision: Pick<RevisionEnvelope,
  "revision_no" | "target_device_id" | "adapter_id" | "mode" | "created_at" | "expires_at"
  | "source_policy_sha256" | "members_sha256" | "required_snapshot_id" | "required_snapshot_sha256" | "symbols"
>): string {
  const values: Array<[string, string]> = [
    ["protocol_version", String(PROTOCOL_VERSION)],
    ["revision_no", String(revision.revision_no)],
    ["target_device_id", revision.target_device_id],
    ["adapter_id", revision.adapter_id],
    ["mode", revision.mode],
    ["created_at", revision.created_at],
    ["expires_at", revision.expires_at],
    ["source_policy_sha256", revision.source_policy_sha256],
    ["members_sha256", revision.members_sha256],
    ["required_snapshot_id", revision.required_snapshot_id],
    ["required_snapshot_sha256", revision.required_snapshot_sha256],
  ];
  return "vps-whitelist-payload-v2\n"
    + values.map(([key, value]) => `${key}=${value}\n`).join("")
    + [...revision.symbols].sort().map((symbol) => `symbol=${symbol}\n`).join("");
}

export function revisionRawContractText(payloadText: string, payloadSha256: string): string {
  return `vps-whitelist-raw-v2\n${payloadText}payload_sha256=${payloadSha256}\n`;
}

export async function validateRevisionEnvelopeIntegrity(
  revision: RevisionEnvelope,
  expectedDeviceId?: string,
): Promise<RevisionEnvelope> {
  if (expectedDeviceId !== undefined && revision.target_device_id !== expectedDeviceId) {
    throw new GatewayValidationError("revision target device does not match request device");
  }
  if (revision.source_policy_sha256 !== VPS_SOURCE_POLICY_SHA256) {
    throw new GatewayValidationError("revision source policy is unsupported");
  }
  if (Date.parse(revision.expires_at) <= Date.parse(revision.created_at)) {
    throw new GatewayValidationError("revision expiry is invalid");
  }
  const membersSha = await sha256Hex(revisionMembersText(revision.symbols));
  if (membersSha !== revision.members_sha256) {
    throw new GatewayValidationError("revision membership hash is invalid");
  }
  const payloadText = revisionPayloadText(revision);
  const payloadSha = await sha256Hex(payloadText);
  if (payloadSha !== revision.payload_sha256) {
    throw new GatewayValidationError("revision payload hash is invalid");
  }
  const rawContract = revisionRawContractText(payloadText, payloadSha);
  const rawContractSha = await sha256Hex(rawContract);
  if (revision.raw_contract !== rawContract || rawContractSha !== revision.raw_contract_sha256) {
    throw new GatewayValidationError("revision raw contract is invalid");
  }
  return revision;
}

function statusRow(raw: unknown): SymbolStateReport {
  if (!isPlainObject(raw)) throw new GatewayValidationError("symbol state must be an object");
  rejectUnknownKeys(raw, ["symbol", "status_key", "status_reason", "source_generated_at_cn", "data_fresh_at_cn", "active_revision_no", "is_in_active_whitelist"], "symbol_states");
  const symbol = canonicalSymbol(raw.symbol, "symbol_states.symbol");
  const statusKey = text(raw.status_key, "symbol_states.status_key", 64)!;
  if (!STATUS_KEY_RE.test(statusKey)) throw new GatewayValidationError("symbol_states.status_key is invalid");
  if (typeof raw.is_in_active_whitelist !== "boolean") throw new GatewayValidationError("symbol_states.is_in_active_whitelist is invalid");
  return {
    symbol,
    status_key: statusKey,
    status_reason: text(raw.status_reason, "symbol_states.status_reason", 500, { optional: true }),
    source_generated_at_cn: isoTimestamp(raw.source_generated_at_cn, "symbol_states.source_generated_at_cn")!,
    data_fresh_at_cn: isoTimestamp(raw.data_fresh_at_cn, "symbol_states.data_fresh_at_cn", { optional: true }),
    active_revision_no: integer(raw.active_revision_no, "symbol_states.active_revision_no", { nullable: true, min: 1 }),
    is_in_active_whitelist: raw.is_in_active_whitelist,
  };
}

function positionRow(raw: unknown): PositionReport {
  if (!isPlainObject(raw)) throw new GatewayValidationError("paper position must be an object");
  rejectUnknownKeys(raw, ["symbol", "held_quantity", "available_quantity", "position_state", "source_generated_at_cn", "active_revision_no"], "paper_positions");
  const held = integer(raw.held_quantity, "paper_positions.held_quantity", { min: 0 })!;
  const available = integer(raw.available_quantity, "paper_positions.available_quantity", { min: 0 })!;
  if (available > held) throw new GatewayValidationError("paper_positions.available_quantity exceeds held_quantity");
  const state = text(raw.position_state, "paper_positions.position_state", 64)!;
  if (!POSITION_STATE_RE.test(state)) throw new GatewayValidationError("paper_positions.position_state is invalid");
  return {
    symbol: canonicalSymbol(raw.symbol, "paper_positions.symbol"),
    held_quantity: held,
    available_quantity: available,
    position_state: state,
    source_generated_at_cn: isoTimestamp(raw.source_generated_at_cn, "paper_positions.source_generated_at_cn")!,
    active_revision_no: integer(raw.active_revision_no, "paper_positions.active_revision_no", { nullable: true, min: 1 }),
  };
}

function privateProjectionPosition(raw: unknown): PrivateProjectionPosition {
  if (!isPlainObject(raw)) throw new GatewayValidationError("private projection position must be an object");
  rejectUnknownKeys(
    raw,
    [
      "symbol", "display_name", "held_quantity", "available_quantity",
      "average_cost_per_share", "current_unadjusted_price", "price_as_of",
      "data_status", "quote_source_kind", "position_state",
    ],
    "private_projection.positions",
  );
  const symbol = canonicalSymbol(raw.symbol, "private_projection.positions.symbol");
  const held = integer(raw.held_quantity, "private_projection.positions.held_quantity", { min: 0, max: 2_147_483_647 })!;
  const available = integer(raw.available_quantity, "private_projection.positions.available_quantity", { min: 0, max: 2_147_483_647 })!;
  if (available > held) throw new GatewayValidationError("private projection available quantity exceeds held quantity");
  const cost = realNumber(raw.average_cost_per_share, "private_projection.positions.average_cost_per_share", { nullable: true });
  const price = realNumber(raw.current_unadjusted_price, "private_projection.positions.current_unadjusted_price", { nullable: true, min: 0.000001 });
  const priceAsOf = isoTimestamp(raw.price_as_of, "private_projection.positions.price_as_of", { optional: true });
  const dataStatus = text(raw.data_status, "private_projection.positions.data_status", 20)!;
  if (!["complete", "stale_price", "missing_price", "missing_cost", "unavailable"].includes(dataStatus)) {
    throw new GatewayValidationError("private projection data status is invalid");
  }
  const quoteSource = text(raw.quote_source_kind, "private_projection.positions.quote_source_kind", 40)!;
  if (quoteSource !== "hithink_batch_snapshot" && quoteSource !== "not_available") {
    throw new GatewayValidationError("private projection quote source is invalid");
  }
  const positionState = text(raw.position_state, "private_projection.positions.position_state", 64, { optional: true }) || "held";
  if (!POSITION_STATE_RE.test(positionState)) throw new GatewayValidationError("private projection position state is invalid");
  if (price === null) {
    if (priceAsOf !== null || quoteSource !== "not_available" || !["missing_price", "unavailable"].includes(dataStatus)) {
      throw new GatewayValidationError("private projection missing-price state is inconsistent");
    }
  } else if (priceAsOf === null || quoteSource !== "hithink_batch_snapshot" || ["missing_price", "unavailable"].includes(dataStatus)) {
    throw new GatewayValidationError("private projection price state is inconsistent");
  }
  if (held > 0 && cost === null && !["missing_cost", "unavailable"].includes(dataStatus)) {
    throw new GatewayValidationError("private projection missing-cost state is inconsistent");
  }
  if (cost !== null && price !== null && !["complete", "stale_price"].includes(dataStatus)) {
    throw new GatewayValidationError("private projection complete state is inconsistent");
  }
  return {
    symbol,
    display_name: text(raw.display_name, "private_projection.positions.display_name", 80, { optional: true }),
    held_quantity: held,
    available_quantity: available,
    average_cost_per_share: cost,
    current_unadjusted_price: price,
    price_as_of: priceAsOf,
    data_status: dataStatus as PrivateProjectionPosition["data_status"],
    quote_source_kind: quoteSource as PrivateProjectionPosition["quote_source_kind"],
    position_state: positionState,
  };
}

type PrivateActiveEvidence = {
  active_revision_no: number | null;
  active_generation: number | null;
  active_pack_sha256: string | null;
  active_control_payload_sha256: string | null;
  active_control_raw_contract_sha256: string | null;
  active_members_sha256: string | null;
  active_snapshot_id: string | null;
  active_snapshot_sha256: string | null;
};

function validatePrivateProjection(raw: unknown, activeFields: PrivateActiveEvidence): PrivateProjectionReport {
  if (!isPlainObject(raw)) throw new GatewayValidationError("private projection is invalid");
  rejectUnknownKeys(
    raw,
    [
      "schema_version", "scope_key", "mode", "health_status", "projection_sequence",
      "generated_at", "account_as_of", "quote_as_of", "source_market_snapshot_id",
      "source_market_snapshot_sha256", "active_revision_no", "active_generation",
      "active_pack_sha256", "active_control_payload_sha256", "active_control_raw_contract_sha256",
      "active_members_sha256", "active_snapshot_id", "active_snapshot_sha256",
      "sanitized_error", "positions",
    ],
    "private_projection",
  );
  denySensitiveStructure(raw, "private_projection");
  if (raw.schema_version !== 1 || raw.scope_key !== "primary" || raw.mode !== "DRY_RUN") {
    throw new GatewayValidationError("private projection schema, scope or mode is invalid");
  }
  const health = text(raw.health_status, "private_projection.health_status", 16)!;
  if (!HEALTH.has(health)) throw new GatewayValidationError("private projection health status is invalid");
  const sequence = integer(raw.projection_sequence, "private_projection.projection_sequence", { min: 1, max: 2_147_483_647 })!;
  const generated = isoTimestamp(raw.generated_at, "private_projection.generated_at")!;
  const accountAsOf = isoTimestamp(raw.account_as_of, "private_projection.account_as_of", { optional: true });
  const quoteAsOf = isoTimestamp(raw.quote_as_of, "private_projection.quote_as_of", { optional: true });
  const sourceId = snapshotId(raw.source_market_snapshot_id, "private_projection.source_market_snapshot_id", { optional: true });
  const sourceSha = sha256(raw.source_market_snapshot_sha256, "private_projection.source_market_snapshot_sha256", { optional: true });
  if ((sourceId === null) !== (sourceSha === null)) throw new GatewayValidationError("private projection market snapshot evidence is incomplete");

  const nestedActive: PrivateActiveEvidence = {
    active_revision_no: integer(raw.active_revision_no, "private_projection.active_revision_no", { nullable: true, min: 1, max: 2_147_483_647 }),
    active_generation: integer(raw.active_generation, "private_projection.active_generation", { nullable: true, min: 1, max: 2_147_483_647 }),
    active_pack_sha256: sha256(raw.active_pack_sha256, "private_projection.active_pack_sha256", { optional: true }),
    active_control_payload_sha256: sha256(raw.active_control_payload_sha256, "private_projection.active_control_payload_sha256", { optional: true }),
    active_control_raw_contract_sha256: sha256(raw.active_control_raw_contract_sha256, "private_projection.active_control_raw_contract_sha256", { optional: true }),
    active_members_sha256: sha256(raw.active_members_sha256, "private_projection.active_members_sha256", { optional: true }),
    active_snapshot_id: snapshotId(raw.active_snapshot_id, "private_projection.active_snapshot_id", { optional: true }),
    active_snapshot_sha256: sha256(raw.active_snapshot_sha256, "private_projection.active_snapshot_sha256", { optional: true }),
  };
  for (const key of Object.keys(nestedActive) as (keyof PrivateActiveEvidence)[]) {
    if (nestedActive[key] !== activeFields[key]) throw new GatewayValidationError("private projection active evidence does not match runtime evidence");
  }

  const positionsRaw = raw.positions;
  if (!Array.isArray(positionsRaw) || positionsRaw.length > MAX_POSITIONS) {
    throw new GatewayValidationError("private projection positions are invalid");
  }
  const positions = uniqueSymbols(positionsRaw.map(privateProjectionPosition), "private_projection.positions");
  if (positions.length > 0 && accountAsOf === null) throw new GatewayValidationError("private projection positions require account_as_of");
  return {
    schema_version: 1,
    scope_key: "primary",
    mode: "DRY_RUN",
    health_status: health as PrivateProjectionReport["health_status"],
    projection_sequence: sequence,
    generated_at: generated,
    account_as_of: accountAsOf,
    quote_as_of: quoteAsOf,
    source_market_snapshot_id: sourceId,
    source_market_snapshot_sha256: sourceSha,
    ...nestedActive,
    sanitized_error: text(raw.sanitized_error, "private_projection.sanitized_error", 500, { optional: true }),
    positions,
  };
}

function eventRow(raw: unknown): EventReport {
  if (!isPlainObject(raw)) throw new GatewayValidationError("event must be an object");
  rejectUnknownKeys(raw, ["occurred_at_cn", "severity", "event_code", "message", "symbol", "action", "revision_no", "generation"], "events");
  const severity = text(raw.severity, "events.severity", 16)!;
  if (!SEVERITY.has(severity)) throw new GatewayValidationError("events.severity is invalid");
  const code = text(raw.event_code, "events.event_code", 80)!;
  if (!EVENT_CODE_RE.test(code)) throw new GatewayValidationError("events.event_code is invalid");
  const action = text(raw.action, "events.action", 64, { optional: true });
  if (action !== null && !STATUS_KEY_RE.test(action)) throw new GatewayValidationError("events.action is invalid");
  return {
    occurred_at_cn: isoTimestamp(raw.occurred_at_cn, "events.occurred_at_cn")!,
    severity: severity as EventReport["severity"],
    event_code: code,
    message: text(raw.message, "events.message", 500)!,
    symbol: raw.symbol === null || raw.symbol === undefined || raw.symbol === "" ? null : canonicalSymbol(raw.symbol, "events.symbol"),
    action,
    revision_no: integer(raw.revision_no, "events.revision_no", { nullable: true, min: 1 }),
    generation: integer(raw.generation, "events.generation", { nullable: true, min: 1 }),
  };
}

function ackRow(raw: unknown): SyncAck {
  if (!isPlainObject(raw)) throw new GatewayValidationError("ack must be an object");
  rejectUnknownKeys(raw, [
    "ack_id", "revision_no", "sync_stage", "generation", "pack_sha256",
    "control_payload_sha256", "control_raw_contract_sha256", "members_sha256",
    "required_snapshot_id", "required_snapshot_sha256", "adapter_id", "mode",
    "reported_at_cn", "message", "rejection_code",
  ], "acks");
  const ackId = sha256(raw.ack_id, "acks.ack_id")!;
  const stage = text(raw.sync_stage, "acks.sync_stage", 16)!;
  if (!ACK_STAGE.has(stage)) throw new GatewayValidationError("acks.sync_stage is invalid");
  const generation = integer(raw.generation, "acks.generation", { nullable: true, min: 1 });
  const packSha = sha256(raw.pack_sha256, "acks.pack_sha256", { optional: true });
  if (stage === "activated" && (generation === null || packSha === null)) {
    throw new GatewayValidationError("activated ACK requires generation and market-pack hash");
  }
  const adapterId = text(raw.adapter_id, "acks.adapter_id", 120)!;
  const mode = text(raw.mode, "acks.mode", 16)!;
  if (adapterId !== VPS_ADAPTER_ID || mode !== "DRY_RUN") {
    throw new GatewayValidationError("ACK adapter or mode is invalid");
  }
  const rejectionCode = text(raw.rejection_code, "acks.rejection_code", 80, { optional: true });
  if (rejectionCode !== null && !EVENT_CODE_RE.test(rejectionCode)) throw new GatewayValidationError("acks.rejection_code is invalid");
  return {
    ack_id: ackId,
    revision_no: integer(raw.revision_no, "acks.revision_no", { min: 1 })!,
    sync_stage: stage as SyncAck["sync_stage"],
    generation,
    pack_sha256: packSha,
    control_payload_sha256: sha256(raw.control_payload_sha256, "acks.control_payload_sha256")!,
    control_raw_contract_sha256: sha256(raw.control_raw_contract_sha256, "acks.control_raw_contract_sha256")!,
    members_sha256: sha256(raw.members_sha256, "acks.members_sha256")!,
    required_snapshot_id: snapshotId(raw.required_snapshot_id, "acks.required_snapshot_id")!,
    required_snapshot_sha256: sha256(raw.required_snapshot_sha256, "acks.required_snapshot_sha256")!,
    adapter_id: adapterId as typeof VPS_ADAPTER_ID,
    mode: "DRY_RUN",
    reported_at_cn: isoTimestamp(raw.reported_at_cn, "acks.reported_at_cn")!,
    message: text(raw.message, "acks.message", 500, { optional: true }),
    rejection_code: rejectionCode,
  };
}

function uniqueSymbols<T extends { symbol: string }>(rows: T[], field: string): T[] {
  const seen = new Set<string>();
  for (const row of rows) {
    if (seen.has(row.symbol)) throw new GatewayValidationError(`${field} contains a duplicate symbol`);
    seen.add(row.symbol);
  }
  return rows;
}

function uniqueAckIds(rows: SyncAck[]): SyncAck[] {
  const seen = new Set<string>();
  for (const row of rows) {
    if (seen.has(row.ack_id)) throw new GatewayValidationError("acks contains a duplicate ack_id");
    seen.add(row.ack_id);
  }
  return rows;
}

export function validateRuntimeReport(raw: unknown): RuntimeReport {
  if (!isPlainObject(raw)) throw new GatewayValidationError("publish report is invalid");
  rejectUnknownKeys(raw, [
    "schema_version", "adapter_id", "mode", "health_status", "generated_at_cn",
    "active_revision_no", "active_generation", "active_pack_sha256",
    "active_control_payload_sha256", "active_control_raw_contract_sha256", "active_members_sha256",
    "active_snapshot_id", "active_snapshot_sha256",
    "last_control_pull_at_cn", "last_strategy_cycle_at_cn", "last_quote_snapshot_at_cn",
    "last_account_snapshot_at_cn", "last_eod_at_cn", "provider_reads_used", "provider_reads_cap",
    "state_summary", "sanitized_error", "symbol_states", "paper_positions", "private_projection", "events", "acks",
  ], "report");
  denySensitiveStructure(raw);
  if (raw.schema_version !== PROTOCOL_VERSION) throw new GatewayValidationError("publish schema_version is unsupported");
  if (raw.adapter_id !== VPS_ADAPTER_ID) throw new GatewayValidationError("adapter_id is unsupported");
  if (raw.mode !== "DRY_RUN") throw new GatewayValidationError("only DRY_RUN reports are accepted");
  const health = text(raw.health_status, "health_status", 16)!;
  if (!HEALTH.has(health)) throw new GatewayValidationError("health_status is invalid");
  const statesRaw = raw.symbol_states;
  const positionsRaw = raw.paper_positions;
  const eventsRaw = raw.events;
  const acksRaw = raw.acks;
  if (!Array.isArray(statesRaw) || !Array.isArray(positionsRaw) || !Array.isArray(eventsRaw) || !Array.isArray(acksRaw)) {
    throw new GatewayValidationError("report collections are invalid");
  }
  if (statesRaw.length > MAX_SYMBOL_STATES || positionsRaw.length > MAX_POSITIONS || eventsRaw.length > MAX_EVENTS || acksRaw.length > MAX_ACKS) {
    throw new GatewayValidationError("publish collection limit exceeded");
  }
  const activeRevision = integer(raw.active_revision_no, "active_revision_no", { nullable: true, min: 1 });
  const activeGeneration = integer(raw.active_generation, "active_generation", { nullable: true, min: 1 });
  const activePackSha = sha256(raw.active_pack_sha256, "active_pack_sha256", { optional: true });
  const activeControlPayload = sha256(raw.active_control_payload_sha256, "active_control_payload_sha256", { optional: true });
  const activeControlRaw = sha256(raw.active_control_raw_contract_sha256, "active_control_raw_contract_sha256", { optional: true });
  const activeMembers = sha256(raw.active_members_sha256, "active_members_sha256", { optional: true });
  const activeSnapshotId = snapshotId(raw.active_snapshot_id, "active_snapshot_id", { optional: true });
  const activeSnapshotSha = sha256(raw.active_snapshot_sha256, "active_snapshot_sha256", { optional: true });
  const activeFields = [activeGeneration, activePackSha, activeControlPayload, activeControlRaw, activeMembers, activeSnapshotId, activeSnapshotSha];
  if (activeRevision === null && activeFields.some((value) => value !== null)) {
    throw new GatewayValidationError("report has orphan active revision evidence");
  }
  if (activeRevision !== null && activeFields.some((value) => value === null)) {
    throw new GatewayValidationError("active revision evidence is incomplete");
  }

  const privateProjection = raw.private_projection === undefined
    ? undefined
    : validatePrivateProjection(raw.private_projection, {
      active_revision_no: activeRevision,
      active_generation: activeGeneration,
      active_pack_sha256: activePackSha,
      active_control_payload_sha256: activeControlPayload,
      active_control_raw_contract_sha256: activeControlRaw,
      active_members_sha256: activeMembers,
      active_snapshot_id: activeSnapshotId,
      active_snapshot_sha256: activeSnapshotSha,
    });

  const states = uniqueSymbols(statesRaw.map(statusRow), "symbol_states");
  for (const state of states) {
    if (state.is_in_active_whitelist && state.active_revision_no !== activeRevision) {
      throw new GatewayValidationError("active symbol state is not linked to active revision");
    }
  }
  const positions = uniqueSymbols(positionsRaw.map(positionRow), "paper_positions");
  for (const position of positions) {
    if (position.active_revision_no !== null && position.active_revision_no !== activeRevision) {
      throw new GatewayValidationError("position is not linked to active revision");
    }
  }
  const acks = uniqueAckIds(acksRaw.map(ackRow));
  for (const ack of acks) {
    if (ack.sync_stage === "activated" && (
      ack.revision_no !== activeRevision || ack.generation !== activeGeneration || ack.pack_sha256 !== activePackSha
      || ack.control_payload_sha256 !== activeControlPayload || ack.control_raw_contract_sha256 !== activeControlRaw
      || ack.members_sha256 !== activeMembers || ack.required_snapshot_id !== activeSnapshotId
      || ack.required_snapshot_sha256 !== activeSnapshotSha
    )) {
      throw new GatewayValidationError("activated ACK does not match active runtime evidence");
    }
  }
  return {
    schema_version: PROTOCOL_VERSION,
    adapter_id: VPS_ADAPTER_ID,
    mode: "DRY_RUN",
    health_status: health as RuntimeReport["health_status"],
    generated_at_cn: isoTimestamp(raw.generated_at_cn, "generated_at_cn")!,
    active_revision_no: activeRevision,
    active_generation: activeGeneration,
    active_pack_sha256: activePackSha,
    active_control_payload_sha256: activeControlPayload,
    active_control_raw_contract_sha256: activeControlRaw,
    active_members_sha256: activeMembers,
    active_snapshot_id: activeSnapshotId,
    active_snapshot_sha256: activeSnapshotSha,
    last_control_pull_at_cn: isoTimestamp(raw.last_control_pull_at_cn, "last_control_pull_at_cn", { optional: true }),
    last_strategy_cycle_at_cn: isoTimestamp(raw.last_strategy_cycle_at_cn, "last_strategy_cycle_at_cn", { optional: true }),
    last_quote_snapshot_at_cn: isoTimestamp(raw.last_quote_snapshot_at_cn, "last_quote_snapshot_at_cn", { optional: true }),
    last_account_snapshot_at_cn: isoTimestamp(raw.last_account_snapshot_at_cn, "last_account_snapshot_at_cn", { optional: true }),
    last_eod_at_cn: isoTimestamp(raw.last_eod_at_cn, "last_eod_at_cn", { optional: true }),
    provider_reads_used: integer(raw.provider_reads_used, "provider_reads_used", { nullable: true, min: 0, max: 50 }),
    provider_reads_cap: integer(raw.provider_reads_cap, "provider_reads_cap", { nullable: true, min: 1, max: 50 }),
    state_summary: text(raw.state_summary, "state_summary", 500, { optional: true }),
    sanitized_error: text(raw.sanitized_error, "sanitized_error", 500, { optional: true }),
    symbol_states: states,
    paper_positions: positions,
    ...(privateProjection === undefined ? {} : { private_projection: privateProjection }),
    events: eventsRaw.map(eventRow),
    acks,
  };
}

export function validateRevisionEnvelope(raw: unknown): RevisionEnvelope {
  if (!isPlainObject(raw)) throw new GatewayValidationError("revision response is invalid");
  rejectUnknownKeys(raw, [
    "protocol_version", "revision_no", "status", "target_device_id", "adapter_id", "mode",
    "created_at", "expires_at", "source_policy_sha256", "members_sha256",
    "required_snapshot_id", "required_snapshot_sha256", "payload_sha256",
    "raw_contract_sha256", "raw_contract", "symbols",
  ], "revision");
  denySensitiveStructure(raw);
  if (raw.protocol_version !== PROTOCOL_VERSION) throw new GatewayValidationError("revision protocol is invalid");
  const status = text(raw.status, "revision.status", 32)!;
  if (!(status === "submitted" || status === "sync_pending" || status === "preparing")) {
    throw new GatewayValidationError("revision status is invalid");
  }
  const symbolsRaw = raw.symbols;
  if (!Array.isArray(symbolsRaw) || symbolsRaw.length < 1 || symbolsRaw.length > MAX_SYMBOL_STATES) {
    throw new GatewayValidationError("revision symbols are invalid");
  }
  const symbols = symbolsRaw.map((value) => canonicalSymbol(value, "revision.symbols"));
  if (new Set(symbols).size !== symbols.length) throw new GatewayValidationError("revision symbols are duplicated");
  const adapterId = text(raw.adapter_id, "revision.adapter_id", 120)!;
  const mode = text(raw.mode, "revision.mode", 16)!;
  if (adapterId !== VPS_ADAPTER_ID || mode !== "DRY_RUN") throw new GatewayValidationError("revision adapter/mode is invalid");
  const rawContract = exactText(raw.raw_contract, "revision.raw_contract", 16384);
  const createdAt = isoTimestamp(raw.created_at, "revision.created_at")!;
  const expiresAt = isoTimestamp(raw.expires_at, "revision.expires_at")!;
  if (Date.parse(expiresAt) <= Date.parse(createdAt)) {
    throw new GatewayValidationError("revision expiry is invalid");
  }
  const sourcePolicySha = sha256(raw.source_policy_sha256, "revision.source_policy_sha256")!;
  if (sourcePolicySha !== VPS_SOURCE_POLICY_SHA256) {
    throw new GatewayValidationError("revision source policy is unsupported");
  }
  return {
    protocol_version: PROTOCOL_VERSION,
    revision_no: integer(raw.revision_no, "revision.revision_no", { min: 1 })!,
    status: status as RevisionEnvelope["status"],
    target_device_id: validateDeviceId(raw.target_device_id),
    adapter_id: VPS_ADAPTER_ID,
    mode: "DRY_RUN",
    created_at: createdAt,
    expires_at: expiresAt,
    source_policy_sha256: sourcePolicySha,
    members_sha256: sha256(raw.members_sha256, "revision.members_sha256")!,
    required_snapshot_id: snapshotId(raw.required_snapshot_id, "revision.required_snapshot_id")!,
    required_snapshot_sha256: sha256(raw.required_snapshot_sha256, "revision.required_snapshot_sha256")!,
    payload_sha256: sha256(raw.payload_sha256, "revision.payload_sha256")!,
    raw_contract_sha256: sha256(raw.raw_contract_sha256, "revision.raw_contract_sha256")!,
    raw_contract: rawContract,
    symbols,
  };
}
