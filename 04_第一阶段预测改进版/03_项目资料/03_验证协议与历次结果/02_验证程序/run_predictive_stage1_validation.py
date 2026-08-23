#!/usr/bin/env python3
"""Run implementation-level gates for the isolated stage-one copy."""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve(); STAGE1=HERE.parents[3]; SIM=STAGE1/"02_模拟程序"
def run(cmd):
    p=subprocess.run(cmd,cwd=SIM,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
    return {"command":cmd,"returncode":p.returncode,"passed":p.returncode==0,"output":p.stdout}
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--include-baseline-tests",action="store_true"); parser.add_argument("--output",type=Path); args=parser.parse_args(); stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); out=args.output or STAGE1/"03_项目资料"/"03_验证协议与历次结果"/"04_历次结果"/f"{stamp}-predictive-stage1"; out.mkdir(parents=True,exist_ok=False)
    gates={"compile":run([sys.executable,"-m","compileall","-q","src/airvortex/stage1","run_stage1_gui.py","run_stage1_cli.py"]),"stage1_tests":run([sys.executable,"-m","unittest","discover","-s","tests","-p","test_stage1_*.py","-v"])}
    if args.include_baseline_tests: gates["all_tests"]=run([sys.executable,"-m","unittest","discover","-s","tests","-p","test_*.py","-v"])
    implementation=all(v["passed"] for v in gates.values()); status="PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION" if implementation else "FAIL_STAGE1_IMPLEMENTATION"
    result={"schema":"airvortex-predictive-stage1-result-v1","overall_status":status,"implementation_pass":implementation,"physical_prediction_go":False,"implementation_gates":{k:{"passed":v["passed"],"returncode":v["returncode"]} for k,v in gates.items()},"physical_go_gates":{"static_drop_laplace_pressure":False,"spurious_current_grid_convergence":False,"capillary_wave_frequency_and_damping":False,"dynamic_grid_and_experiment_validation":False}}
    (out/"acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for k,v in gates.items(): (out/f"{k}.log").write_text(v["output"],encoding="utf-8")
    print(status); return 0 if implementation else 1
if __name__=="__main__": raise SystemExit(main())
