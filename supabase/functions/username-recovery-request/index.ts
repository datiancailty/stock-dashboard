import {
  allowedOrigin,
  forbiddenResponse,
  GENERIC_RECOVERY_MESSAGE,
  isEndpointPath,
  isServiceError,
  jsonResponse,
  methodResponse,
  parseRecoveryInput,
  preflightResponse,
  readJson,
  sendRecovery,
} from "../_shared/username-auth.ts";

const TEMPORARILY_UNAVAILABLE = Object.freeze({ error: "temporarily_unavailable" });

export async function handleUsernameRecovery(request: Request): Promise<Response> {
  let origin: string | null;
  try {
    origin = allowedOrigin(request);
  } catch {
    return new Response("unavailable", { status: 503, headers: { "cache-control": "no-store" } });
  }
  if (!origin || !isEndpointPath(request, "username-recovery-request")) return forbiddenResponse();
  if (request.method === "OPTIONS") return preflightResponse(origin);
  if (request.method !== "POST") return methodResponse(origin);

  let input: { username: string } | null = null;
  try {
    input = parseRecoveryInput(await readJson(request));
  } catch {
    // The recovery endpoint intentionally keeps malformed and unknown usernames
    // on the same public response path.
    input = null;
  }

  try {
    if (input) await sendRecovery(input.username, origin);
    return jsonResponse(GENERIC_RECOVERY_MESSAGE, 200, origin);
  } catch (error) {
    if (isServiceError(error)) return jsonResponse(TEMPORARILY_UNAVAILABLE, 503, origin);
    return jsonResponse(TEMPORARILY_UNAVAILABLE, 503, origin);
  }
}

Deno.serve(handleUsernameRecovery);
