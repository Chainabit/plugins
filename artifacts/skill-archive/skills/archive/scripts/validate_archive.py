#!/usr/bin/env python3
"""Authoritative content-present validator with exact-file identity."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_system import Limits, verify_archive
from archive_system.errors import (ArchiveError, ErrorCode, RETRYABLE_RUNTIME_ERRORS,
                                   failure_class)
from archive_system.verification import MIME_TYPE
PROTOCOL = "chainabit.archive.validation/v1"
# Verification-only bounds: a validator inspects an artifact that already
# exists, so it must not impose a build-time budget the builder never applied.
UNBOUNDED = Limits(max_files=2**31 - 1, max_total_bytes=2**63 - 1, max_file_bytes=2**63 - 1)
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("archive"); a=p.parse_args(argv)
 try:
  r=verify_archive(a.archive,UNBOUNDED); print(json.dumps({"schema":PROTOCOL,"valid":True,"validator":"skill-archive.validate_archive","classification":"authoritative","subject":{"path":r.path,"shape":"file","mime":MIME_TYPE,"sha256":r.sha256,"bytes":r.bytes,"entryCount":r.entry_count},"checks":["container_parse","member_integrity","member_path_safety","exact_sha256"],"entries":list(r.entries),"warnings":[]},sort_keys=True)); return 0
 except ArchiveError as e:
  unavailable=e.code in {ErrorCode.FILESYSTEM_FAILURE}
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"validate","error":{"code":e.code.value,"class":failure_class(e),"message":e.message,"retryable":e.code in RETRYABLE_RUNTIME_ERRORS}},sort_keys=True),file=sys.stderr)
  return 2 if unavailable else 1
 except OSError:
  print(json.dumps({"schema":PROTOCOL,"ok":False,"operation":"validate","error":{"code":"filesystem_failure","class":"filesystem_io_failure","message":"archive input could not be read","retryable":True}},sort_keys=True),file=sys.stderr); return 2
if __name__=="__main__":sys.exit(main())
