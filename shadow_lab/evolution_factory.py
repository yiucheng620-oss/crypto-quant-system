import sys
import os
import json
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional

sys.path.append(os.path.join(os.path.expanduser("~"), "workspace", "scalp_engines"))
from gemini_free import call_pro, call_flash

CANDIDATES_DIR = os.path.join(os.path.expanduser("~"), "workspace", "shadow_lab", "candidates")
LOG_DIR = os.path.join(os.path.expanduser("~"), "workspace", "shadow_lab", "logs")
SHADOW_DIR = os.path.join(os.path.expanduser("~"), "workspace", "shadow_lab")

# ── Load DeepSeek Native API Key ──
DEEPSEEK_API_KEY = ""
env_path = os.path.join(os.path.expanduser("~"), ".hermes", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.startswith("DEEPSEEK_API_KEY="):
                DEEPSEEK_API_KEY = line.split("=", 1)[1].strip()
                break

def call_deepseek_v4_native(prompt: str) -> str:
    """Alpha Researcher: Calls DeepSeek Native API directly"""
    if not DEEPSEEK_API_KEY:
        print("  ⚠️ No native DEEPSEEK_API_KEY found, falling back to Gemini Pro.")
        return call_pro(prompt, temperature=0.7)
        
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat", # deepseek-chat points to V3/V4 latest
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read().decode("utf-8"))
            return res["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  ❌ DeepSeek Native call failed: {e}. Falling back to Gemini Pro.")
        return call_pro(prompt, temperature=0.7)

# ==========================================
# 🧠 AGENT 1: Alpha Researcher (DeepSeek Native)
# ==========================================
def agent_alpha_researcher(market: str, failed_trades_summary: str, attempt: int = 1, previous_error: str = None) -> str:
    print(f"\n🟢 [DeepSeek V4] Alpha Researcher generating strategy (Attempt {attempt})...")
    
    prompt = f"""You are a top-tier Quant Engineer.
Target Market: {market}
Recent Failures to solve:
{failed_trades_summary}

Write a Python class for a trading engine that inherits from `ShadowEngine`.

Requirements:
1. MUST import ShadowEngine like this:
```python
import sys, os
sys.path.append(r"{SHADOW_DIR}")
from shadow_base import ShadowEngine
```
2. The class MUST be named exactly `ShadowEngine_{market}_{int(time.time())}`.
3. The `__init__` method MUST accept EXACTLY `(self, market_name: str, strategy_id: str)` and pass them to `super().__init__(market_name, strategy_id)`.
4. Override ONLY `generate_signal(self, data)`. Return a dict or None.
5. Provide ONLY VALID PYTHON CODE. No markdown formatting outside the code block.

{f"🚨 PREVIOUS ERROR: {previous_error}. FIX IT." if previous_error else ""}
"""
    raw_response = call_deepseek_v4_native(prompt)
    
    code = raw_response
    if "```python" in code:
        code = code.split("```python")[1].split("```")[0].strip()
    elif "```" in code:
        code = code.split("```")[1].split("```")[0].strip()
        
    return code

def sandbox_test(code: str, class_name: str) -> Optional[str]:
    import tempfile
    
    test_script = f"""
import sys
import os
{code}

# Test instantiation
engine = {class_name}("BTC", "shadow_test")
print("Syntax OK.")
"""
    
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, 'w') as f:
        f.write(test_script)
        
    import subprocess
    result = subprocess.run(["python3", path], capture_output=True, text=True)
    os.remove(path)
    
    if result.returncode != 0:
        return result.stderr
    return None

def agent_risk_manager(code: str) -> str:
    print(f"🔴 [Gemini 2.5 Pro] Risk Manager attacking strategy...")
    prompt = f"Critique this trading strategy code for logic flaws or curve fitting:\n\n```python\n{code}\n```\nBe harsh."
    return call_flash(prompt, temperature=0.3)

def agent_judge(code: str, critique: str) -> Dict:
    print(f"⚖️ [Gemini 2.5 Flash] Judge making final decision...")
    prompt = f"""Score this strategy (0-100) based on code and critique. >80 = PASS.
Code:
{code[:500]}...

Critique:
{critique}

Respond STRICTLY in JSON: {{"score": 85, "decision": "PASS", "reason": "..."}}"""
    raw = call_flash(prompt, temperature=0.1)
    try:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        return json.loads(raw)
    except:
        return {"score": 0, "decision": "FAIL", "reason": "JSON parse error"}

def run_evolution_pipeline(market: str = "BTC"):
    print(f"🚀 Starting Evolution Factory for {market}...")
    
    max_attempts = 3
    code = ""
    error = None
    
    for attempt in range(1, max_attempts + 1):
        summary = "Recent LONG trades failed because they bought into high volatility ranges during weekend chop."
        code = agent_alpha_researcher(market, summary, attempt, error)
        
        class_name = None
        for line in code.split("\n"):
            if line.startswith("class ShadowEngine_"):
                class_name = line.split("(")[0].replace("class ", "").strip()
                break
                
        if not class_name:
            error = "Could not find class name."
            continue
            
        error = sandbox_test(code, class_name)
        if not error:
            print(f"  ✅ Sandbox Test Passed.")
            break
        else:
            print(f"  ⚠️ Sandbox Error caught (Attempt {attempt}):\n{error.strip()}")
            
    if error:
        print("🛑 Strategy generation failed after 3 attempts. Dropping.")
        return
        
    critique = agent_risk_manager(code)
    verdict = agent_judge(code, critique)
    print(f"  ⚖️ Verdict: {verdict.get('score')}/100 - {verdict.get('decision')}")
    
    if verdict.get("decision") == "PASS" and verdict.get("score", 0) >= 80:
        filename = os.path.join(CANDIDATES_DIR, f"{class_name.lower()}.py")
        with open(filename, "w") as f:
            f.write(code)
        print(f"🏆 Strategy Deployed to Incubator: {filename}")
    else:
        print("🗑️ Strategy Rejected.")

if __name__ == "__main__":
    run_evolution_pipeline()
