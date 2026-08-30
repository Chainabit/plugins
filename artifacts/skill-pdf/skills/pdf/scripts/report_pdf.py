#!/usr/bin/env python3
"""Compatibility controller for JSON report PDF generation."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pdf_system import PdfService,SecurityPolicy
from pdf_system.errors import ErrorCode,PdfError
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("spec");p.add_argument("output");p.add_argument("--validate-only",action="store_true");a=p.parse_args(argv)
 try:
  s=PdfService(SecurityPolicy.for_paths(a.spec,a.output))
  if a.validate_only:
   issues=s.validate_report(json.loads(Path(a.spec).read_text(encoding="utf-8")))
   if issues: raise PdfError(ErrorCode.INVALID_INPUT,"; ".join(issues))
   print(json.dumps({"ok":True,"valid":True},sort_keys=True));return 0
  r=s.generate_report(Path(a.spec),Path(a.output));print(json.dumps({"ok":True,"output":a.output,"pages":r.pages,"bytes":r.bytes},sort_keys=True));return 0
 except (PdfError,OSError,json.JSONDecodeError,UnicodeDecodeError) as e:
  code=e.code.value if isinstance(e,PdfError) else "invalid_input"; print(json.dumps({"ok":False,"error":{"code":code,"message":str(e) if isinstance(e,PdfError) else "report specification is unreadable or invalid JSON"}},sort_keys=True));return 2
if __name__=="__main__":sys.exit(main())
