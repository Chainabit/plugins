#!/usr/bin/env python3
"""Authoritative controller for JSON report PDF generation."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pdf_system import PdfService,SecurityPolicy
from pdf_system.errors import (ErrorCode,PdfError,RETRYABLE_RUNTIME_ERRORS,
                               failure_class,renderer_exit_code)
PROTOCOL="chainabit.pdf.execution/v1"
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("spec");p.add_argument("output");p.add_argument("--validate-only",action="store_true");a=p.parse_args(argv)
 try:
  s=PdfService(SecurityPolicy.for_paths(a.spec,a.output))
  if a.validate_only:
   issues=s.validate_report(json.loads(Path(a.spec).read_text(encoding="utf-8")))
   if issues: raise PdfError(ErrorCode.INVALID_INPUT,"; ".join(issues))
   print(json.dumps({"schema":PROTOCOL,"ok":True,"operation":"preflight","valid":True},sort_keys=True));return 0
  r=s.generate_report(Path(a.spec),Path(a.output));spec=json.loads(Path(a.spec).read_text(encoding="utf-8"));font=spec.get("font");print(json.dumps({"schema":PROTOCOL,"success":True,"generator":"skill-pdf.report","output":{"path":str(Path(a.output).resolve()),"shape":"file","mime":"application/pdf","sha256":r.sha256,"pages":r.pages,"bytes":r.bytes},"renderer":{"backend":next((w.split("=",1)[1] for w in r.warnings if w.startswith("backend=")),"unknown")},"typography":{"family":font or "IBM Plex Sans","source":"user_override" if font else "chainabit_default"}},sort_keys=True));return 0
 except (PdfError,OSError,json.JSONDecodeError,UnicodeDecodeError) as e:
  error=e if isinstance(e,PdfError) else PdfError(ErrorCode.INVALID_INPUT,"report specification is unreadable or invalid JSON")
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"render","error":{"code":error.code.value,"class":failure_class(error),"message":error.message,"retryable":error.code in RETRYABLE_RUNTIME_ERRORS}},sort_keys=True),file=sys.stderr);return renderer_exit_code(error)
if __name__=="__main__":sys.exit(main())
