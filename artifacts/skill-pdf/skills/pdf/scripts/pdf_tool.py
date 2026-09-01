#!/usr/bin/env python3
"""Stable secure PDF CLI. JSON output is suitable for automation."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import (PdfError, RETRYABLE_RUNTIME_ERRORS,
                               failure_class, renderer_exit_code)
from pdf_system.models import Limits
from pdf_system.verification import verify_pdf

def emit(obj, code=0): print(json.dumps(obj, sort_keys=True)); return code
def policy(source, output): return SecurityPolicy.for_paths(source, output)
def main(argv=None):
 p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command", required=True)
 cap=sub.add_parser("capabilities")
 diag=sub.add_parser("diagnose"); diag.add_argument("kind",choices=["markdown","report"]); diag.add_argument("source"); diag.add_argument("--profile",default="quality")
 gen=sub.add_parser("generate-markdown"); gen.add_argument("source"); gen.add_argument("output"); gen.add_argument("--title"); gen.add_argument("--page-size",default="A4"); gen.add_argument("--orientation",default="portrait"); gen.add_argument("--font")
 rep=sub.add_parser("generate-report"); rep.add_argument("source"); rep.add_argument("output")
 val=sub.add_parser("validate"); val.add_argument("pdf")
 man=sub.add_parser("manipulate"); man.add_argument("operation",choices=["merge","extract","remove","reorder","rotate","crop"]); man.add_argument("output"); man.add_argument("sources",nargs="+"); man.add_argument("--options",default="{}")
 a=p.parse_args(argv)
 try:
  if a.command=="capabilities": return emit({"capabilities":PdfService.discover_capabilities()})
  if a.command=="diagnose":
   raw=Path(a.source).read_text(encoding="utf-8"); content=json.loads(raw) if a.kind=="report" else raw
   return emit({"ok":True,**PdfService(policy(a.source,a.source)).diagnose(a.kind,content,a.profile)})
  if a.command=="validate":
   r=verify_pdf(Path(a.pdf),Limits()); return emit({"ok":True,"pages":r.pages,"bytes":r.bytes,"version":r.version,"sha256":r.sha256,"mime":r.mime_type})
  s=PdfService(policy(a.source if a.command!="manipulate" else a.sources[0],a.output))
  if a.command=="generate-markdown": r=s.generate_markdown(Path(a.source),Path(a.output),a.title,page_size=a.page_size,orientation=a.orientation,deterministic=False,font=a.font)
  elif a.command=="generate-report": r=s.generate_report(Path(a.source),Path(a.output))
  else: r=s.manipulate(a.operation,[Path(x) for x in a.sources],Path(a.output),json.loads(a.options))
  return emit({"ok":True,"output":str(a.output),"pages":r.pages,"bytes":r.bytes,"sha256":r.sha256,"mime":r.mime_type,"warnings":list(r.warnings),"backend":s.last_decision.backend if s.last_decision else None})
 except PdfError as e: return emit({"ok":False,"error":{"code":e.code.value,"class":failure_class(e),"message":e.message,"retryable":e.code in RETRYABLE_RUNTIME_ERRORS}},renderer_exit_code(e))
 except Exception: return emit({"ok":False,"error":{"code":"internal_error","class":"internal_skill_defect","message":"unexpected operation failure","retryable":False}},2)
if __name__=="__main__": sys.exit(main())
