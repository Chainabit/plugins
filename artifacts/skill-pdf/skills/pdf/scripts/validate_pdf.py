#!/usr/bin/env python3
"""Compatibility validator backed by the bounded PDF verifier."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pdf_system.models import Limits
from pdf_system.errors import PdfError
from pdf_system.verification import verify_pdf
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("pdf");p.add_argument("--strict",action="store_true");a=p.parse_args(argv)
 try:
  r=verify_pdf(Path(a.pdf),Limits());print(json.dumps({"ok":True,"pages":r.pages,"bytes":r.bytes,"version":r.version},sort_keys=True));return 0
 except (PdfError,OSError):print(json.dumps({"ok":False,"error":{"code":"validation_failure","message":"PDF validation failed"}},sort_keys=True));return 1
if __name__=="__main__":sys.exit(main())
