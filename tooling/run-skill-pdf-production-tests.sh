#!/usr/bin/env bash
# Run skill-pdf's tests on the production renderer stack, and fail if any is skipped.
#
# `run-skill-tests.mjs` runs every skill's tests on a bare interpreter, where the
# tests that render a real PDF skip because WeasyPrint and the IBM Plex fonts are
# absent. A skipped test verifies nothing, so those runs read as coverage while the
# renderer's behaviour -- what a page prints, how many pages there are, whether a
# refused source leaves a file behind -- went unchecked in CI. This script installs the
# same pins the sandbox image ships and treats a skip as a failure.
#
# It mutates the machine it runs on (apt packages, /opt, /usr/local/share/fonts), so
# it is meant for a CI runner or a throwaway container, never a workstation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$ROOT/artifacts/skill-pdf/skills/pdf"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

# Mirrors the sandbox image: the Python pins, the IBM Plex release and its digests.
PIN_WEASYPRINT=70.0
PIN_MARKDOWN=3.8.2
PIN_PYPDF=6.16.2
PIN_PILLOW=12.3.0
PIN_REPORTLAB=4.4.3
IBM_PLEX_VERSION=1.1.0
IBM_PLEX_SANS_SHA512=6b15393b8aa70920b39c610b03214bae397e9021af039c0f1c2fc8bc20832bf31085673e96b3dd9be7ef5ca1ecb3880fae0c9b274c287f61d8158f6bf95636ed
IBM_PLEX_ARABIC_SHA512=48420d1baa372999f9a52ccf24f4d3a69ad2de03d6fed5ded89f62dbc7f414f7304f4361c07400441b659c9395ed33fcd058ba95b8504f38e5ade6c2d0338dba
FONT_DIR=/opt/chainabit/artifact-fonts/ibm-plex-sans

export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends \
  ca-certificates curl unzip fontconfig fonts-dejavu-core \
  libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
  python3 python3-venv

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch_font_zip() { # <release tag> <archive name> <sha512> <destination-dir>
  curl -fsSL "https://github.com/IBM/plex/releases/download/%40ibm/$1%40${IBM_PLEX_VERSION}/$2.zip" -o "$WORK/$2.zip"
  echo "$3  $WORK/$2.zip" | sha512sum -c -
  unzip -q "$WORK/$2.zip" -d "$4"
}
fetch_font_zip plex-sans ibm-plex-sans "$IBM_PLEX_SANS_SHA512" "$WORK/sans"
fetch_font_zip plex-sans-arabic ibm-plex-sans-arabic "$IBM_PLEX_ARABIC_SHA512" "$WORK/arabic"
$SUDO mkdir -p "$FONT_DIR" /usr/local/share/fonts/chainabit
$SUDO cp "$WORK"/sans/ibm-plex-sans/fonts/complete/ttf/IBMPlexSans-{Regular,SemiBold}.ttf "$FONT_DIR"/
$SUDO cp "$WORK"/arabic/ibm-plex-sans-arabic/fonts/complete/ttf/IBMPlexSansArabic-{Regular,SemiBold}.ttf "$FONT_DIR"/
$SUDO cp "$FONT_DIR"/*.ttf /usr/local/share/fonts/chainabit/
$SUDO fc-cache -f
fc-match "IBM Plex Sans" | grep -q "IBMPlexSans"

python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet --disable-pip-version-check \
  "weasyprint==$PIN_WEASYPRINT" "markdown==$PIN_MARKDOWN" "pypdf==$PIN_PYPDF" \
  "pillow==$PIN_PILLOW" "reportlab==$PIN_REPORTLAB"

export CHAINABIT_ARTIFACT_FONT_DIR="$FONT_DIR"
export CHAINABIT_ARTIFACT_FONT_FAMILY="IBM Plex Sans"
export PYTHONDONTWRITEBYTECODE=1
cd "$SKILL"
"$WORK/venv/bin/python" - <<'PY'
import sys
import unittest

result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover("tests"))
if result.skipped:
    for test, reason in result.skipped:
        print(f"SKIPPED on the production stack: {test.id()}: {reason}", file=sys.stderr)
    sys.exit(1)
sys.exit(0 if result.wasSuccessful() else 1)
PY
