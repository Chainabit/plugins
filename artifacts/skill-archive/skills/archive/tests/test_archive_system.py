"""Executable coverage for the archive builder and validator.

The scripts are EXECUTED here, not asserted about. Every rule this package
claims -- exclusion at any depth, exact-directory versus namespace matching,
symlink refusal, self-exclusion of the destination, the three size bounds, the
expansion bound, deterministic ordering, partial-output cleanup and read-back
verification -- is a rule about what a subprocess does to a real directory. A
test that imported the functions and stubbed the filesystem would assert the
opposite of what is worth knowing.

Run: python3 -m unittest discover -s <this directory>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
BUILD = SKILL_ROOT / "scripts" / "build_archive.py"
VALIDATE = SKILL_ROOT / "scripts" / "validate_archive.py"

# Generic rule names. The host supplies its own at run time; this package must
# never encode which names those are.
EXCLUSIONS = {"directories": ["vendor"], "namespaces": ["buildmeta"]}

MAX_FILES = 64
MAX_TOTAL_BYTES = 4_000_000
MAX_FILE_BYTES = 512_000


class Result:
    def __init__(self, completed: subprocess.CompletedProcess) -> None:
        self.code = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr

    @property
    def report(self) -> dict:
        return json.loads(self.stdout.strip() or "{}")

    @property
    def error(self) -> dict:
        return json.loads(self.stderr.strip() or "{}")

    @property
    def error_code(self) -> str:
        return self.error.get("error", {}).get("code", "")


def run_build(
    source: Path,
    output: Path,
    exclusions: dict | None = None,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> Result:
    """Mirrors the host's argv exactly, so this runs what production runs."""
    return Result(
        subprocess.run(
            [
                sys.executable,
                str(BUILD),
                str(source),
                str(output),
                json.dumps(EXCLUSIONS if exclusions is None else exclusions),
                str(max_files),
                str(max_total_bytes),
                str(max_file_bytes),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    )


def run_validate(archive: Path) -> Result:
    return Result(
        subprocess.run(
            [sys.executable, str(VALIDATE), str(archive)],
            capture_output=True,
            text=True,
            check=False,
        )
    )


class ArchiveProgramTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def file(self, relative: str, content: str = "x") -> Path:
        absolute = self.root / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(content, encoding="utf-8")
        return absolute

    # ---- exclusion rules -------------------------------------------------

    def test_excludes_namespace_at_every_depth_and_keeps_lookalikes(self) -> None:
        # The exclusion once read only the FIRST segment, so everything here
        # except the root-level entry was packaged into a download.
        self.file("src/index.html", "<html/>")
        self.file("src/buildmeta/state", "host-owned")
        self.file("src/site/buildmeta/state", "host-owned")
        self.file("src/site/buildmeta-site.json", "{}")
        self.file("src/buildmetaish/keep.md", "the caller's own directory")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            sorted(result.report["output"]["entries"]),
            ["buildmetaish/keep.md", "index.html"],
        )

    def test_directory_matches_exactly_and_namespace_matches_at_boundary(self) -> None:
        # Two rules, because the host sends two different things. One namespace
        # rule would drop `vendor-legacy`, which is the caller's; one exact rule
        # would carry `buildmeta-site.json`, which is not.
        self.file("src/index.html", "<html/>")
        self.file("src/vendor-legacy/notes.md", "the caller's own directory")
        self.file("src/vendor/bundle.json", "host-owned")
        self.file("src/buildmeta-site.json", "{}")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            sorted(result.report["output"]["entries"]),
            ["index.html", "vendor-legacy/notes.md"],
        )

    # ---- symlink and entry-type refusal ----------------------------------

    def test_refuses_a_symlink_rather_than_following_or_packaging_it(self) -> None:
        # A link pointing outside the tree is how an archive comes to carry
        # bytes the walk never visited.
        self.file("src/index.html", "<html/>")
        outside = self.file("outside.txt", "not part of the tree")
        os.symlink(outside, self.root / "src" / "link.txt")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.error_code, "unsafe_input")
        self.assertEqual(result.code, 1)

    def test_refuses_a_symlinked_directory_before_descending_into_it(self) -> None:
        self.file("src/index.html", "<html/>")
        self.file("elsewhere/secret.txt", "secret")
        os.symlink(self.root / "elsewhere", self.root / "src" / "linked")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.error_code, "unsafe_input")
        self.assertEqual(result.code, 1)

    def test_refuses_when_the_source_itself_is_a_symlink(self) -> None:
        self.file("real/a.txt", "a")
        os.symlink(self.root / "real", self.root / "aliased")

        result = run_build(self.root / "aliased", self.root / "out.zip")

        self.assertEqual(result.error_code, "unsafe_input")
        self.assertEqual(result.code, 1)

    def test_refuses_an_entry_that_is_not_a_regular_file(self) -> None:
        self.file("src/index.html", "<html/>")
        os.mkfifo(self.root / "src" / "pipe")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.error_code, "unsupported_entry")
        self.assertEqual(result.code, 1)

    # ---- emptiness -------------------------------------------------------

    def test_reports_an_empty_source_rather_than_producing_an_empty_archive(
        self,
    ) -> None:
        # A tree whose every member is excluded looks identical to an empty one
        # from outside, and a zero-entry archive offered as a deliverable is a
        # lying success.
        self.file("src/buildmeta/state", "host-owned")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(result.error_code, "source_empty")
        self.assertEqual(result.code, 1)
        self.assertFalse((self.root / "out.zip").exists())

    def test_reports_a_missing_source(self) -> None:
        result = run_build(self.root / "absent", self.root / "out.zip")

        self.assertEqual(result.error_code, "source_not_found")
        self.assertEqual(result.code, 1)

    # ---- destination handling -------------------------------------------

    def test_never_packages_the_destination_into_its_own_archive(self) -> None:
        self.file("src/index.html", "<html/>")
        self.file("src/style.css", "body{}")

        result = run_build(self.root / "src", self.root / "src" / "out.zip")

        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            sorted(result.report["output"]["entries"]), ["index.html", "style.css"]
        )

    def test_packages_a_single_file_source_under_its_base_name(self) -> None:
        self.file("src/report.md", "# report")

        result = run_build(self.root / "src" / "report.md", self.root / "out.zip")

        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(result.report["output"]["entries"], ["report.md"])

    # ---- bounds ----------------------------------------------------------

    def test_refuses_a_member_over_the_per_file_limit(self) -> None:
        self.file("src/big.bin", "x" * 2048)

        result = run_build(
            self.root / "src", self.root / "out.zip", max_file_bytes=1024
        )

        self.assertEqual(result.error_code, "resource_limit")
        self.assertEqual(result.code, 1)

    def test_refuses_more_members_than_the_count_limit(self) -> None:
        for index in range(5):
            self.file(f"src/file-{index}.txt", "x")

        result = run_build(self.root / "src", self.root / "out.zip", max_files=2)

        self.assertEqual(result.error_code, "resource_limit")
        self.assertEqual(result.code, 1)

    def test_refuses_a_selection_over_the_total_byte_limit(self) -> None:
        self.file("src/a.txt", "x" * 900)
        self.file("src/b.txt", "y" * 900)

        result = run_build(
            self.root / "src", self.root / "out.zip", max_total_bytes=1000
        )

        self.assertEqual(result.error_code, "resource_limit")
        self.assertEqual(result.code, 1)

    def test_refuses_an_archive_whose_expansion_ratio_is_too_high(self) -> None:
        # Refused AFTER writing, because the ratio is a property of the written
        # container. The partial output must not survive the refusal.
        self.file("src/zeros.bin", "\0" * 500_000)
        output = self.root / "out.zip"

        result = run_build(
            self.root / "src", output, max_total_bytes=4_000_000, max_file_bytes=4_000_000
        )

        self.assertEqual(result.error_code, "resource_limit")
        self.assertEqual(result.code, 1)
        self.assertFalse(
            output.exists(), "a refused archive must not be left on disk"
        )

    # ---- determinism and verification ------------------------------------

    def test_the_same_tree_produces_the_same_archive(self) -> None:
        self.file("src/b.txt", "b")
        self.file("src/a.txt", "a")
        self.file("src/nested/c.txt", "c")

        first = run_build(self.root / "src", self.root / "one.zip")
        second = run_build(self.root / "src", self.root / "two.zip")

        self.assertEqual(first.code, 0, first.stderr)
        self.assertEqual(second.code, 0, second.stderr)
        self.assertEqual(
            first.report["output"]["sha256"], second.report["output"]["sha256"]
        )
        self.assertEqual(
            first.report["output"]["entries"], second.report["output"]["entries"]
        )

    def test_the_reported_identity_describes_the_file_on_disk(self) -> None:
        self.file("src/index.html", "<html/>")
        output = self.root / "out.zip"

        result = run_build(self.root / "src", output)

        self.assertEqual(result.code, 0, result.stderr)
        reported = result.report["output"]
        self.assertEqual(reported["bytes"], output.stat().st_size)
        # Absolute and denoting the same file -- deliberately not the fully
        # resolved path. The host matches this against the output argument it
        # passed, after stripping the root that argument was relative to;
        # resolving intermediate symlinks could move the reported path out from
        # under that root and break the match for a file that is entirely fine.
        self.assertTrue(os.path.isabs(reported["path"]))
        self.assertTrue(os.path.samefile(reported["path"], output))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), reported["entries"])

    # ---- protocol envelopes ----------------------------------------------

    def test_success_emits_one_line_of_generator_protocol_on_stdout(self) -> None:
        self.file("src/index.html", "<html/>")

        result = run_build(self.root / "src", self.root / "out.zip")

        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        report = result.report
        self.assertEqual(report["schema"], "chainabit.archive.execution/v1")
        self.assertIs(report["success"], True)
        self.assertEqual(report["generator"], "skill-archive.zip")
        output = report["output"]
        self.assertEqual(output["shape"], "file")
        self.assertEqual(output["mime"], "application/zip")
        self.assertRegex(output["sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(output["bytes"], int)
        self.assertEqual(result.stderr, "")

    def test_failure_emits_the_error_envelope_on_stderr_and_nothing_on_stdout(
        self,
    ) -> None:
        result = run_build(self.root / "absent", self.root / "out.zip")

        self.assertEqual(result.stdout.strip(), "")
        envelope = result.error
        self.assertEqual(envelope["schema"], "chainabit.archive.execution/v1")
        self.assertIs(envelope["ok"], False)
        self.assertEqual(envelope["operation"], "package")
        self.assertEqual(envelope["error"]["class"], "invalid_user_input")
        self.assertIs(envelope["error"]["retryable"], False)

    def test_invalid_exclusion_json_is_an_input_rejection(self) -> None:
        self.file("src/index.html", "<html/>")

        result = Result(
            subprocess.run(
                [
                    sys.executable,
                    str(BUILD),
                    str(self.root / "src"),
                    str(self.root / "out.zip"),
                    "{not json",
                    str(MAX_FILES),
                    str(MAX_TOTAL_BYTES),
                    str(MAX_FILE_BYTES),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        )

        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(result.code, 1)


class ArchiveValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_accepts_an_archive_the_builder_produced(self) -> None:
        source = self.root / "src"
        source.mkdir()
        (source / "index.html").write_text("<html/>", encoding="utf-8")
        output = self.root / "out.zip"
        built = run_build(source, output)
        self.assertEqual(built.code, 0, built.stderr)

        result = run_validate(output)

        self.assertEqual(result.code, 0, result.stderr)
        report = result.report
        self.assertEqual(report["schema"], "chainabit.archive.validation/v1")
        self.assertIs(report["valid"], True)
        self.assertEqual(report["classification"], "authoritative")
        self.assertEqual(report["validator"], "skill-archive.validate_archive")
        # The two scripts must agree about one file, byte for byte.
        self.assertEqual(
            report["subject"]["sha256"], built.report["output"]["sha256"]
        )

    def test_rejects_a_file_that_is_not_a_container(self) -> None:
        fake = self.root / "fake.zip"
        fake.write_text("not a zip", encoding="utf-8")

        result = run_validate(fake)

        self.assertEqual(result.error_code, "malformed_artifact")
        self.assertEqual(result.code, 1)
        self.assertEqual(
            result.error["error"]["class"], "produced_artifact_rejected"
        )

    def test_rejects_a_missing_archive(self) -> None:
        result = run_validate(self.root / "absent.zip")

        self.assertEqual(result.error_code, "corrupted_output")
        self.assertEqual(result.code, 1)

    def test_rejects_a_container_whose_member_would_escape_extraction(self) -> None:
        # Hand-built, because the builder refuses to create one. A validator
        # that only ever sees its own builder's output is not a validator.
        hostile = self.root / "hostile.zip"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr("../escaped.txt", "x")

        result = run_validate(hostile)

        self.assertEqual(result.error_code, "unsafe_input")
        self.assertEqual(result.code, 1)


if __name__ == "__main__":
    unittest.main()
