#!/usr/bin/env python3
"""
clean_crontab.py — 系統定時任務 (Linux Crontab) 精簡與優化器
===========================================================
1. 移除與 Hermes 衝突/重複的美股選股及每週評估任務。
2. 將 System 1 的每日優化 (daily_review_v2.py) 降頻至每週六，避免每日過度擬合與 Token 浪費。
3. 保留核心 5-分鐘掃描、System Guardian 與數據採集任務。
"""
import subprocess
import sys

def get_current_crontab():
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError:
        return ""

def write_crontab(content):
    try:
        subprocess.run(["crontab", "-"], input=content, text=True, check=True)
        print("🎉 Crontab 更新成功！")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Crontab 更新失敗: {e}", file=sys.stderr)
        return False

def main():
    current = get_current_crontab()
    if not current:
        print("⚠️ 無法獲取當前 Crontab 內容。")
        return
        
    lines = current.splitlines()
    new_lines = []
    
    deleted_jobs = []
    modified_jobs = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append("")
            continue
            
        # 註解行直接保留
        if stripped.startswith("#"):
            new_lines.append(line)
            continue
            
        # ── 1. 移除重疊的美股 Pipeline 與 Executor 任務 (交由 Hermes 管理發送至 TG) ──
        if "kanban_pipeline_v3.py" in line:
            deleted_jobs.append(f"美股預判: {line}")
            # 同步將上一行註釋也去掉
            if new_lines and new_lines[-1].strip().startswith("#"):
                new_lines.pop()
            continue
            
        if "kanban_closed_loop.py execute" in line:
            deleted_jobs.append(f"美股執行: {line}")
            if new_lines and new_lines[-1].strip().startswith("#"):
                new_lines.pop()
            continue
            
        if "kanban_closed_loop.py review" in line:
            deleted_jobs.append(f"美股每週檢討: {line}")
            if new_lines and new_lines[-1].strip().startswith("#"):
                new_lines.pop()
            continue
            
        # ── 2. 將每日大腦優化 (daily_review_v2.py) 修改為每週六運行，避免每日過度擬合 ──
        if "daily_review_v2.py" in line:
            # 0 0 * * * -> 30 0 * * 6 (每週六 08:30 HKT)
            new_line = "30 0 * * 6 cd /home/yiucheng620/workspace && /home/yiucheng620/.hermes/hermes-agent/venv/bin/python3 scalp_engines/daily_review_v2.py >> /home/yiucheng620/workspace/reviews/daily_review.log 2>&1"
            modified_jobs.append(f"Daily Review V2 降頻為週六運行:\n  舊: {line}\n  新: {new_line}")
            new_lines.append(new_line)
            continue
            
        # 其他核心任務一律保留
        new_lines.append(line)
        
    print("╔══════════════════════════════════════════════════════╗")
    print("║          Linux Crontab 簡化與重構審計                 ║")
    print("╚══════════════════════════════════════════════════════╝")
    
    if deleted_jobs:
        print("\n🗑️  已刪除與 Hermes 重複/無效的串行任務：")
        for j in deleted_jobs:
            print(f"  ❌ {j}")
            
    if modified_jobs:
        print("\n🔧 已修改/優化的長線任務：")
        for j in modified_jobs:
            print(f"  {j}")
            
    print("\n📝 正在寫入優化後的 Crontab...")
    write_crontab("\n".join(new_lines) + "\n")

if __name__ == "__main__":
    main()
