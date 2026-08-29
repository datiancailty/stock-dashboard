import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import {
  GatewayValidationError,
  MAX_REQUEST_BYTES,
  PROTOCOL_VERSION,
  VPS_ADAPTER_ID,
  VPS_SYNC_PATH,
  VPS_SYNC_RUNTIME_PATH,
  hmacSha256Hex,
  safeEqual,
  sha256Hex,
  signingMessage,
  validateDeviceId,
  validatePullRequest,
  validatePublishRequest,
  validateRuntimeRequestTarget,
  validateRevisionEnvelope,
  validateRevisionEnvelopeIntegrity,
  validateSignedHeaders,
} from "./shared.ts";

const jsonHeaders = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_CLOCK_SKEW_SECONDS = 300;

type ConsumedRequest = {
  deviceId: string;
  requestId: string;
  bodySha256: string;
  payload: Record<string, unknown>;
};

function response(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
}

function requiredEnv(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`missing required gateway configuration: ${name}`);
  return value;
}

function genericGatewayError(error: unknown): Response {
  if (error instanceof GatewayValidationError) return response(400, { ok: false, error: "invalid_request" });
  return response(502, { ok: false, error: "gateway_unavailable" });
}

async function consumeSignedRequest(request: Request, rawBody: string): Promise<ConsumedRequest> {
  const { deviceId, protocolVersion, timestamp, nonce, requestId, signature } = validateSignedHeaders(request.headers);
  const url = new URL(request.url);
  validateRuntimeRequestTarget(url);
  const bodySha256 = await sha256Hex(rawBody);
  const now = Math.floor(Date.now() / 1000);
  const sentAt = Number(timestamp);
  if (!Number.isSafeInteger(sentAt) || Math.abs(now - sentAt) > MAX_CLOCK_SKEW_SECONDS) {
    throw new GatewayValidationError("signature timestamp is stale");
  }
  const expected = await hmacSha256Hex(
    requiredEnv("VPS_SYNC_SHARED_SECRET"),
    // Sign the stable public pathname.  Supabase Edge Runtime exposes the
    // function-local pathname separately, so it is validated above but is not
    // the wire-contract path used by the VPS client.
    signingMessage(request.method, VPS_SYNC_PATH, protocolVersion, timestamp, nonce, requestId, bodySha256),
  );
  if (!safeEqual(expected, signature)) throw new GatewayValidationError("signature is invalid");
  let payload: unknown;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    throw new GatewayValidationError("request body is not JSON");
  }
  if (!(payload && typeof payload === "object") || Array.isArray(payload)) {
    throw new GatewayValidationError("request body is invalid");
  }
  const objectPayload = payload as Record<string, unknown>;
  if (validateDeviceId(objectPayload.device_id) !== deviceId || objectPayload.request_id !== requestId) {
    throw new GatewayValidationError("request identity does not match signed headers");
  }
  if (objectPayload.protocol_version !== PROTOCOL_VERSION) {
    throw new GatewayValidationError("request protocol does not match signed headers");
  }
  return { deviceId, requestId, bodySha256, payload: objectPayload };
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") return response(405, { ok: false, error: "method_not_allowed" });
  const requestUrl = new URL(request.url);
  if (requestUrl.pathname !== VPS_SYNC_RUNTIME_PATH || requestUrl.search || requestUrl.hash) {
    return response(404, { ok: false, error: "not_found" });
  }

  try {
    const rawBody = await request.text();
    if (!rawBody || new TextEncoder().encode(rawBody).byteLength > MAX_REQUEST_BYTES) {
      return response(413, { ok: false, error: "request_too_large" });
    }

    const consumed = await consumeSignedRequest(request, rawBody);
    const client = createClient(
      requiredEnv("SUPABASE_URL"),
      requiredEnv("SUPABASE_SERVICE_ROLE_KEY"),
      { auth: { persistSession: false, autoRefreshToken: false } },
    );

    // Nonce is deliberately consumed only after HMAC/path/protocol/request-id
    // verification.  A repeated HMAC-authenticated body may use a *new* nonce
    // after a lost response; durable idempotency is handled by request_id below.
    const { error: nonceError, data: nonceAccepted } = await client.rpc("vps_sync_consume_nonce", {
      p_device_id: consumed.deviceId,
      p_nonce_sha256: await sha256Hex(request.headers.get("x-vps-nonce") || ""),
      p_seen_at: new Date().toISOString(),
    });
    if (nonceError || nonceAccepted !== true) return response(401, { ok: false, error: "unauthorized" });

    const operation = consumed.payload.operation;
    if (operation === "pull") {
      const checked = validatePullRequest(consumed.payload);
      if (checked.device_id !== consumed.deviceId || checked.request_id !== consumed.requestId) {
        throw new GatewayValidationError("pull identity mismatch");
      }
      const { data, error } = await client.rpc("vps_sync_pull_request", {
        p_device_id: consumed.deviceId,
        p_request_id: consumed.requestId,
        p_request_body_sha256: consumed.bodySha256,
      });
      if (error || !(data && typeof data === "object") || Array.isArray(data)) {
        return response(502, { ok: false, error: "gateway_unavailable" });
      }
      const result = data as Record<string, unknown>;
      const rawRevision = result.revision ?? null;
      const revision = rawRevision === null
        ? null
        : await validateRevisionEnvelopeIntegrity(validateRevisionEnvelope(rawRevision), consumed.deviceId);
      return response(200, {
        ok: true,
        protocol_version: PROTOCOL_VERSION,
        adapter_id: VPS_ADAPTER_ID,
        request_id: consumed.requestId,
        request_body_sha256: consumed.bodySha256,
        revision,
      });
    }

    if (operation === "publish") {
      const checked = validatePublishRequest(consumed.payload);
      if (checked.device_id !== consumed.deviceId || checked.request_id !== consumed.requestId) {
        throw new GatewayValidationError("publish identity mismatch");
      }
      const { data, error } = await client.rpc("vps_sync_publish_request", {
        p_device_id: consumed.deviceId,
        p_request_id: consumed.requestId,
        p_request_body_sha256: consumed.bodySha256,
        p_report: checked.report,
      });
      if (error || !(data && typeof data === "object") || Array.isArray(data)) {
        return response(502, { ok: false, error: "gateway_unavailable" });
      }
      const accepted = (data as Record<string, unknown>).accepted_ack_ids;
      if (!Array.isArray(accepted) || accepted.some((value) => typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value))) {
        return response(502, { ok: false, error: "gateway_unavailable" });
      }
      return response(200, {
        ok: true,
        protocol_version: PROTOCOL_VERSION,
        request_id: consumed.requestId,
        request_body_sha256: consumed.bodySha256,
        accepted_ack_ids: accepted,
      });
    }

    return response(400, { ok: false, error: "unsupported_operation" });
  } catch (error) {
    return genericGatewayError(error);
  }
});
