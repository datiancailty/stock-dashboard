import {
  allowedOrigin,
  forbiddenResponse,
  INVALID_CREDENTIALS,
  isEndpointPath,
  isInvalidPasswordError,
  isServiceError,
  jsonResponse,
  methodResponse,
  preflightResponse,
  parseLoginInput,
  rateLimitAdmit,
  rateLimitRecordFailure,
  readJson,
  RequestShapeError,
  verifyPassword,
} from "../_shared/username-auth.ts";

const TEMPORARILY_UNAVAILABLE = Object.freeze({ error: "temporarily_unavailable" });
const RATE_LIMITED = Object.freeze({ error: "rate_limited" });

function serviceUnavailable(origin: string): Response {
  return jsonResponse(TEMPORARILY_UNAVAILABLE, 503, origin);
}

async function recordAndReject(request: Request, username: string, origin: string): Promise<Response> {
  try {
    await rateLimitRecordFailure(request, username);
    return jsonResponse(INVALID_CREDENTIALS, 401, origin);
  } catch {
    // Do not let a rate-limit storage failure turn into an unbounded login path.
    return serviceUnavailable(origin);
  }
}

export async function handleUsernameLogin(request: Request): Promise<Response> {
  let origin: string | null;
  try {
    origin = allowedOrigin(request);
  } catch {
    return new Response("unavailable", { status: 503, headers: { "cache-control": "no-store" } });
  }
  if (!origin || !isEndpointPath(request, "username-login")) return forbiddenResponse();
  if (request.method === "OPTIONS") return preflightResponse(origin);
  if (request.method !== "POST") return methodResponse(origin);

  let input: { username: string; password: string } | null = null;
  try {
    input = parseLoginInput(await readJson(request));
  } catch (error) {
    if (!(error instanceof RequestShapeError)) return serviceUnavailable(origin);
  }

  // Invalid/malformed input still consumes the same server-side admission path
  // so the IP limiter is not bypassed by changing the JSON shape.
  const rateLimitUsername = input?.username || "invalid";
  try {
    if (!await rateLimitAdmit(request, rateLimitUsername)) return jsonResponse(RATE_LIMITED, 429, origin);
  } catch {
    return serviceUnavailable(origin);
  }

  if (!input) return recordAndReject(request, rateLimitUsername, origin);

  try {
    return jsonResponse(await verifyPassword(input.username, input.password), 200, origin);
  } catch (error) {
    if (isInvalidPasswordError(error)) return recordAndReject(request, input.username, origin);
    if (isServiceError(error)) return serviceUnavailable(origin);
    return serviceUnavailable(origin);
  }
}

Deno.serve(handleUsernameLogin);
