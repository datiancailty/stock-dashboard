#!/usr/bin/env python3
"""Offline regression checks for the Part 6 research-match confidence contract.

This script deliberately uses only synthetic values and public repository source.
It does not load the local Worker config, Keychain, Supabase session, or private
strategy analysis.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "scripts" / "plus_strategy_worker.py"
SCHEMA_PATH = ROOT / "scripts" / "plus_strategy_output.schema.json"
APP_PATH = ROOT / "assets" / "app.js"
MIGRATION_PATH = ROOT / "supabase" / "migrations" / "20260830130000_personal_strategy_confidence_contract_v3.sql"
POSTFLIGHT_PATH = ROOT / "supabase" / "verification" / "personal_strategy_confidence_contract_v3_postflight.sql"
PREFLIGHT_PATH = ROOT / "supabase" / "verification" / "personal_strategy_confidence_contract_v3_preflight.sql"
SCALE = "research_match_percent_0_to_100"


def load_worker():
    spec = importlib.util.spec_from_file_location("strategy_confidence_contract", WORKER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("worker_import_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_output(confidence: object = 72, *, scale: object = SCALE) -> dict:
    return {
        "confidenceScale": scale,
        "profileSummary": "合成研究摘要",
        "learnedRules": ["合成规则"],
        "recordInsights": [{"id": "synthetic-1", "insight": "合成洞察"}],
        "briefCommand": {
            "code": "600036",
            "action": "分批买入",
            "reason": "合成理由",
            "condition": "合成人工确认条件",
            "confidence": confidence,
        },
        "advice": [
            {
                "code": "000001",
                "action": "继续观察",
                "reason": "合成建议",
                "confidence": 66,
            }
        ],
    }


def expect_invalid(worker, payload: dict) -> None:
    try:
        worker.validate_model_output(payload, {"600036", "000001"})
    except worker.WorkerError as error:
        assert error.category == "codex_output_invalid"
    else:
        raise AssertionError("invalid_output_accepted")


def main() -> None:
    worker = load_worker()
    assert worker.ANALYSIS_SCHEMA_VERSION == 3
    assert worker.CONFIDENCE_SCALE == SCALE
    valid = worker.validate_model_output(synthetic_output(), {"600036", "000001"})
    assert valid["confidenceScale"] == SCALE
    assert valid["briefCommand"]["confidence"] == 72
    assert isinstance(valid["briefCommand"]["confidence"], int)
    expect_invalid(worker, synthetic_output(0.72))
    expect_invalid(worker, synthetic_output(101))
    expect_invalid(worker, synthetic_output(72, scale="probability_0_to_1"))
    missing_scale = synthetic_output()
    del missing_scale["confidenceScale"]
    expect_invalid(worker, missing_scale)
    analysis = worker.analysis_payload(
        synthetic_output(),
        {"currentStocks": [{"code": "600036"}, {"code": "000001"}], "feedback": {"stats": {}}, "recommendations": {"performance": {}}},
        "codex-synthetic",
        "a" * 64,
    )
    assert analysis["schemaVersion"] == 3
    assert analysis["confidenceScale"] == SCALE
    assert analysis["confidenceMeaning"] == worker.CONFIDENCE_MEANING
    prompt = worker.prompt_for({"synthetic": True})
    assert SCALE in prompt and "0.72 不合法" in prompt and "1 只表示 1%" in prompt

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["confidenceScale"]["enum"] == [SCALE]
    assert "confidenceScale" in schema["required"]
    for confidence in (
        schema["properties"]["briefCommand"]["properties"]["confidence"],
        schema["properties"]["advice"]["items"]["properties"]["confidence"],
    ):
        assert confidence == {"type": "integer", "minimum": 0, "maximum": 100}

    app = APP_PATH.read_text(encoding="utf-8")
    assert "RESEARCH_MATCH_SCHEMA_VERSION=3" in app
    assert "研究匹配度待刷新" in app
    assert "页面不会猜测 0.72、1" in app
    assert "legacyProbability" not in app

    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    assert migration.count("begin;") == 1 and migration.count("commit;") == 1 and migration.count("$$") == 2
    assert "p_analysis->>'schemaVersion' is distinct from '3'" in migration
    assert "worker_confidence_contract_invalid" in migration
    assert "trunc(confidence_value) <> confidence_value" in migration
    assert "and (confidence_item.value #>> '{}')::numeric < 1" in migration
    assert "personal_strategy_worker_runs" in migration
    assert "drop table" not in migration.lower()

    postflight = POSTFLIGHT_PATH.read_text(encoding="utf-8")
    preflight = PREFLIGHT_PATH.read_text(encoding="utf-8")
    assert "current_strategy_analysis_uses_v3_integer_research_match" in postflight
    assert "worker_rpc_requires_research_match_scale" in postflight
    assert "worker_rpc_requires_integer_confidence" in postflight
    assert "worker_table as" in postflight
    assert "target.proc_oid::oid" in postflight
    assert "safe_legacy_probability_batch" in preflight
    assert "requires_fresh_worker" in preflight
    print("STRATEGY_CONFIDENCE_CONTRACT_V3_OFFLINE_PROBE_OK")
    print("valid_integer_percent=72")
    print("fractional_0_72_rejected=true")
    print("missing_or_wrong_scale_rejected=true")
    print("no_private_config_or_network_used=true")


if __name__ == "__main__":
    main()
