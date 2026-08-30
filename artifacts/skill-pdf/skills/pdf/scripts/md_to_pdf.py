#!/usr/bin/env python3
"""Compatibility controller for secure Markdown PDF generation."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import ErrorCode, PdfError
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("input"); p.add_argument("output"); p.add_argument("--title"); p.add_argument("--lang",default="und"); p.add_argument("--page-size",default="A4"); p.add_argument("--orientation",default="portrait"); p.add_argument("--css") ; a=p.parse_args(argv)
 try:
  if a.css: raise PdfError(ErrorCode.UNSUPPORTED_CAPABILITY,"custom CSS is disabled; it needs an explicit safe stylesheet adapter")
  r=PdfService(SecurityPolicy.for_paths(a.input,a.output)).generate_markdown(Path(a.input),Path(a.output),a.title,a.lang,a.page_size,a.orientation)
  print(json.dumps({"ok":True,"output":a.output,"pages":r.pages,"bytes":r.bytes},sort_keys=True)); return 0
 except PdfError as e: print(json.dumps({"ok":False,"error":{"code":e.code.value,"message":e.message}},sort_keys=True)); return 2
if __name__=="__main__":sys.exit(main())
