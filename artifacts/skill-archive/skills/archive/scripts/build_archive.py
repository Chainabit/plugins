#!/usr/bin/env python3
"""Authoritative controller for deterministic, bounded ZIP packaging."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_system import ArchiveService, ExclusionRules, Limits
from archive_system.errors import (ArchiveError, ErrorCode, RETRYABLE_RUNTIME_ERRORS,
                                   builder_exit_code, failure_class)
from archive_system.verification import MIME_TYPE
PROTOCOL = "chainabit.archive.execution/v1"
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("source"); p.add_argument("output"); p.add_argument("exclusions"); p.add_argument("max_files",type=int); p.add_argument("max_total_bytes",type=int); p.add_argument("max_file_bytes",type=int); a=p.parse_args(argv)
 try:
  try: rules=ExclusionRules.from_mapping(json.loads(a.exclusions))
  except json.JSONDecodeError as error: raise ArchiveError(ErrorCode.INVALID_INPUT,"exclusion rules are not valid JSON") from error
  r=ArchiveService(rules,Limits(a.max_files,a.max_total_bytes,a.max_file_bytes)).build(a.source,a.output)
  print(json.dumps({"schema":PROTOCOL,"success":True,"generator":"skill-archive.zip","output":{"path":r.path,"shape":"file","mime":MIME_TYPE,"sha256":r.sha256,"bytes":r.bytes,"entryCount":r.entry_count,"entries":list(r.entries)}},sort_keys=True)); return 0
 except ArchiveError as e:
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"package","error":{"code":e.code.value,"class":failure_class(e),"message":e.message,"retryable":e.code in RETRYABLE_RUNTIME_ERRORS,**({"context":e.context} if e.context else {})}},sort_keys=True),file=sys.stderr)
  return builder_exit_code(e)
 except OSError:
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"package","error":{"code":"filesystem_failure","class":"filesystem_io_failure","message":"source or destination could not be read","retryable":True}},sort_keys=True),file=sys.stderr); return 2
if __name__=="__main__":sys.exit(main())
