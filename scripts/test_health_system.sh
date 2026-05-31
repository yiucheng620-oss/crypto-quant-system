#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Indicator Health Monitor — Master Integration Test
# ═══════════════════════════════════════════════════════════
# 測試所有 component 是否正常運作
# ═══════════════════════════════════════════════════════════
set -e

VENV="$HOME/.hermes/hermes-agent/venv/bin/python3"
WORKSPACE="$HOME/workspace"
PASS=0
FAIL=0

run_test() {
    local name="$1"
    local cmd="$2"
    echo -n "  [$name] ... "
    if eval "$cmd" > /dev/null 2>&1; then
        echo "✅ PASS"
        PASS=$((PASS + 1))
    else
        echo "❌ FAIL"
        FAIL=$((FAIL + 1))
        eval "$cmd" 2>&1 | head -3
    fi
}

echo "════════════════════════════════════════════════"
echo "  Indicator Health Monitor — Integration Tests"
echo "════════════════════════════════════════════════"
echo ""

# Test 1: Core engine
run_test "Core engine" "cd $WORKSPACE && $VENV indicator_health_monitor.py 2>&1 | grep -q 'Health check complete'"

# Test 2: Health report JSON exists
run_test "Health report JSON" "test -f $WORKSPACE/health_reports/health_latest.json"

# Test 3: Health DB created
run_test "Health DB" "test -f $WORKSPACE/indicator_health.db"

# Test 4: Health bridge
run_test "Health bridge" "cd $WORKSPACE && $VENV health_bridge.py 2>&1 | grep -q '指標健康狀態'"

# Test 5: Gemini analyst imports health
run_test "Gemini import" "cd $WORKSPACE && $VENV -c 'from health_bridge import get_indicator_health; assert get_indicator_health()' 2>&1"

# Test 6: Gemini analyst syntax check
run_test "Gemini syntax" "$VENV -c 'import py_compile; py_compile.compile(\"$WORKSPACE/gemini_daily_analyst.py\", doraise=True)' 2>&1"

# Test 7: signal_logger.py still works
run_test "Signal logger" "cd $WORKSPACE && $VENV signal_logger.py --report 2>&1 | grep -q 'Report ready'"

# Test 8: Cron job exists + enabled
run_test "Cron job exists" "hermes cron list 2>/dev/null | grep -q 'indicator-health-monitor'"

# Test 9: Profiles exist
run_test "Health profiles" "hermes profile list 2>/dev/null | grep -q 'health-collector'"

# Summary
echo ""
echo "════════════════════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed"
echo "════════════════════════════════════════════════"
