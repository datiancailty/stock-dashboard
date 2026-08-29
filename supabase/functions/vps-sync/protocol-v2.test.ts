// Local protocol-v2 contract tests.  This file is bundled with esbuild and run
// under Node; it never talks to Supabase or a VPS.
import assert from "node:assert/strict";
import {
  GatewayValidationError,
  PROTOCOL_VERSION,
  VPS_SOURCE_POLICY_SHA256,
  revisionMembersText,
  revisionPayloadText,
  revisionRawContractText,
  sha256Hex,
  signingMessage,
  validatePullRequest,
  validateRequestTarget,
  validateRuntimeRequestTarget,
  validateRevisionEnvelope,
  validateRevisionEnvelopeIntegrity,
  validateRuntimeReport,
} from "./shared.ts";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const DEVICE_ID = "stock-sim-v31f-15m";

const emptyReport = {
  schema_version: PROTOCOL_VERSION,
  adapter_id: "v31f-15m-miaoxiang-sim-adapter",
  mode: "DRY_RUN",
  health_status: "ok",
  generated_at_cn: "2026-08-25T09:39:00+08:00",
  active_revision_no: null,
  active_generation: null,
  active_pack_sha256: null,
  active_control_payload_sha256: null,
  active_control_raw_contract_sha256: null,
  active_members_sha256: null,
  active_snapshot_id: null,
  active_snapshot_sha256: null,
  last_control_pull_at_cn: null,
  last_strategy_cycle_at_cn: null,
  last_quote_snapshot_at_cn: null,
  last_account_snapshot_at_cn: null,
  last_eod_at_cn: null,
  provider_reads_used: 0,
  provider_reads_cap: 40,
  state_summary: "DRY_RUN 完成。",
  sanitized_error: null,
  symbol_states: [],
  paper_positions: [],
  events: [],
  acks: [],
};

async function validRevision() {
  const symbols = ["600028.SH"];
  const base = {
    protocol_version: PROTOCOL_VERSION,
    revision_no: 7,
    status: "sync_pending" as const,
    target_device_id: DEVICE_ID,
    adapter_id: "v31f-15m-miaoxiang-sim-adapter" as const,
    mode: "DRY_RUN" as const,
    created_at: "2026-08-25T09:00:00Z",
    expires_at: "2026-08-30T09:00:00Z",
    source_policy_sha256: VPS_SOURCE_POLICY_SHA256,
    members_sha256: await sha256Hex(revisionMembersText(symbols)),
    required_snapshot_id: "hithink-daily-increment-fixture",
    required_snapshot_sha256: "c".repeat(64),
    symbols,
  };
  const payload_sha256 = await sha256Hex(revisionPayloadText(base));
  const raw_contract = revisionRawContractText(revisionPayloadText(base), payload_sha256);
  return {
    ...base,
    payload_sha256,
    raw_contract_sha256: await sha256Hex(raw_contract),
    raw_contract,
  };
}

async function main(): Promise<void> {
  assert.equal(
    signingMessage("POST", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
    signingMessage("post", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
  );
  assert.notEqual(
    signingMessage("POST", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
    signingMessage("POST", "/functions/v1/other", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
  );
  assert.notEqual(
    signingMessage("POST", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
    signingMessage("POST", "/functions/v1/vps-sync", 1, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
  );
  assert.notEqual(
    signingMessage("POST", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_A, SHA_B),
    signingMessage("POST", "/functions/v1/vps-sync", 2, "1700000000", "nonce_nonce_nonce_123", SHA_B, SHA_B),
  );
  assert.doesNotThrow(() => validateRequestTarget(new URL("https://example.supabase.co/functions/v1/vps-sync")));
  assert.throws(
    () => validateRequestTarget(new URL("https://example.supabase.co/functions/v1/vps-sync?replay=1")),
    GatewayValidationError,
  );
  assert.doesNotThrow(() => validateRuntimeRequestTarget(new URL("https://edge-runtime/vps-sync")));
  assert.throws(
    () => validateRuntimeRequestTarget(new URL("https://edge-runtime/functions/v1/vps-sync")),
    GatewayValidationError,
  );
  assert.throws(
    () => validateRuntimeRequestTarget(new URL("https://edge-runtime/vps-sync?replay=1")),
    GatewayValidationError,
  );

  assert.deepEqual(validatePullRequest({
    protocol_version: 2,
    request_id: SHA_A,
    operation: "pull",
    device_id: DEVICE_ID,
  }), {
    protocol_version: 2,
    request_id: SHA_A,
    operation: "pull",
    device_id: DEVICE_ID,
  });
  assert.throws(
    () => validatePullRequest({ protocol_version: 2, request_id: SHA_A, operation: "pull", device_id: DEVICE_ID, path: "/wrong" }),
    GatewayValidationError,
  );

  assert.equal(validateRuntimeReport(emptyReport).schema_version, 2);
  assert.throws(
    () => validateRuntimeReport({ ...emptyReport, active_revision_no: 4, active_generation: 5 }),
    GatewayValidationError,
  );
  const privateReport = validateRuntimeReport({
    ...emptyReport,
    private_projection: {
      schema_version: 1,
      scope_key: "primary",
      mode: "DRY_RUN",
      health_status: "ok",
      projection_sequence: 1,
      generated_at: "2026-08-25T09:39:00+08:00",
      account_as_of: "2026-08-25T09:39:00+08:00",
      quote_as_of: "2026-08-25T09:39:00+08:00",
      source_market_snapshot_id: null,
      source_market_snapshot_sha256: null,
      active_revision_no: null,
      active_generation: null,
      active_pack_sha256: null,
      active_control_payload_sha256: null,
      active_control_raw_contract_sha256: null,
      active_members_sha256: null,
      active_snapshot_id: null,
      active_snapshot_sha256: null,
      sanitized_error: null,
      positions: [{
        symbol: "600028.SH",
        display_name: null,
        held_quantity: 300,
        available_quantity: 200,
        average_cost_per_share: 10.123,
        current_unadjusted_price: 11.5,
        price_as_of: "2026-08-25T09:39:00+08:00",
        data_status: "complete",
        quote_source_kind: "hithink_batch_snapshot",
        position_state: "active",
      }],
    },
  });
  assert.equal(privateReport.private_projection?.positions[0].current_unadjusted_price, 11.5);
  assert.throws(
    () => validateRuntimeReport({
      ...emptyReport,
      private_projection: {
        ...privateReport.private_projection,
        positions: [{ ...privateReport.private_projection?.positions[0], market_value: 3450 }],
      },
    }),
    GatewayValidationError,
  );
  assert.throws(
    () => validateRuntimeReport({ ...emptyReport, state_summary: "access_token=forbidden" }),
    GatewayValidationError,
  );

  const rawRevision = await validRevision();
  const revision = validateRevisionEnvelope(rawRevision);
  assert.equal((await validateRevisionEnvelopeIntegrity(revision, DEVICE_ID)).revision_no, 7);
  assert.throws(
    () => validateRevisionEnvelope({ ...rawRevision, symbols: [] }),
    GatewayValidationError,
  );
  await assert.rejects(
    () => validateRevisionEnvelopeIntegrity(validateRevisionEnvelope({ ...rawRevision, members_sha256: SHA_B }), DEVICE_ID),
    GatewayValidationError,
  );
  await assert.rejects(
    () => validateRevisionEnvelopeIntegrity(revision, "another-vps-device"),
    GatewayValidationError,
  );
  assert.throws(
    () => validateRevisionEnvelope({ ...rawRevision, expires_at: rawRevision.created_at }),
    GatewayValidationError,
  );

  console.log("VPS_SYNC_PROTOCOL_V2_SHARED_TESTS_OK");
}

void main().catch((error: unknown) => {
  throw error;
});
