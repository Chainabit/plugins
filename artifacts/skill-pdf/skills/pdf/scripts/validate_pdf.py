#!/usr/bin/env python3
"""Authoritative content-present validator with exact-file identity."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pdf_system.models import Limits
from pdf_system.errors import (ErrorCode, PdfError, RETRYABLE_RUNTIME_ERRORS,
                               failure_class)
from pdf_system.verification import verify_pdf
PROTOCOL="chainabit.pdf.validation/v1"
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("pdf");p.add_argument("--strict",action="store_true");a=p.parse_args(argv)
 try:
  r=verify_pdf(Path(a.pdf),Limits(),strict=a.strict);print(json.dumps({"schema":PROTOCOL,"valid":True,"validator":"skill-pdf.validate_pdf","classification":"authoritative","subject":{"path":str(Path(a.pdf).resolve()),"shape":"file","mime":r.mime_type,"sha256":r.sha256,"pages":r.pages,"bytes":r.bytes,"pdfVersion":r.version},"checks":["pdf_signature","pdf_parse","page_count","painted_content","embedded_fonts","exact_sha256"],"fonts":list(r.fonts),"warnings":list(r.warnings)},sort_keys=True));return 0
 except PdfError as e:
  unavailable=e.code in {ErrorCode.DEPENDENCY_UNAVAILABLE,ErrorCode.DEPENDENCY_FAILURE,ErrorCode.TIMEOUT,ErrorCode.FILESYSTEM_FAILURE}
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"validate","error":{"code":e.code.value,"class":failure_class(e),"message":e.message,"retryable":e.code in RETRYABLE_RUNTIME_ERRORS}},sort_keys=True),file=sys.stderr)
  return 2 if unavailable else 1
 except OSError:
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"validate","error":{"code":"filesystem_failure","class":"filesystem_io_failure","message":"PDF input could not be read","retryable":True}},sort_keys=True),file=sys.stderr);return 2
if __name__=="__main__":sys.exit(main())
