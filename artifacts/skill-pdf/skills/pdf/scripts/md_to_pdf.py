#!/usr/bin/env python3
"""Authoritative controller for secure Markdown PDF generation."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import (ErrorCode, PdfError, RETRYABLE_RUNTIME_ERRORS,
                               failure_class, renderer_exit_code)
PROTOCOL = "chainabit.pdf.execution/v1"
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("input"); p.add_argument("output"); p.add_argument("--title"); p.add_argument("--lang",default="und"); p.add_argument("--page-size",default="A4"); p.add_argument("--orientation",default="portrait"); p.add_argument("--font"); p.add_argument("--css") ; a=p.parse_args(argv)
 try:
  if a.css: raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY,"custom CSS is disabled; it needs an explicit safe stylesheet adapter")
  r=PdfService(SecurityPolicy.for_paths(a.input,a.output)).generate_markdown(Path(a.input),Path(a.output),a.title,a.lang,a.page_size,a.orientation,deterministic=False,quality_profile="professional",font=a.font)
  print(json.dumps({"schema":PROTOCOL,"success":True,"generator":"skill-pdf.markdown","output":{"path":str(Path(a.output).resolve()),"shape":"file","mime":"application/pdf","sha256":r.sha256,"pages":r.pages,"bytes":r.bytes},"renderer":{"backend":next((w.split("=",1)[1] for w in r.warnings if w.startswith("backend=")),"unknown")},"typography":{"family":a.font or "IBM Plex Sans","source":"user_override" if a.font else "chainabit_default"}},sort_keys=True)); return 0
 except PdfError as e:
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"render","error":{"code":e.code.value,"class":failure_class(e),"message":e.message,"retryable":e.code in RETRYABLE_RUNTIME_ERRORS}},sort_keys=True),file=sys.stderr)
  return renderer_exit_code(e)
if __name__=="__main__":sys.exit(main())
