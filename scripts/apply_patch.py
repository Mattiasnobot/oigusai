#!/usr/bin/env python3
"""Safely apply an ÕigusAI patch and run its declared regression tests.

Usage:
    python scripts/apply_patch.py patches/v10_step1a_retrieval_policy.patch
    python scripts/apply_patch.py patches/v10_step1a_retrieval_policy.patch --reverse

The script never stages, commits, pushes, or opens a pull request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        if capture:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, args)
    return result


def git_output(repo: Path, *args: str) -> str:
    return run(["git", *args], cwd=repo, capture=True).stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(patch: Path) -> dict:
    manifest = patch.with_suffix(".json")
    if not manifest.is_file():
        raise SystemExit(
            f"Manifest puudub: {manifest}\n"
            "Patch peab olema koos sama nimega .json manifestiga."
        )
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Manifesti lugemine ebaõnnestus: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Manifest peab olema JSON objekt.")
    return data


def ensure_repo_root(start: Path) -> Path:
    try:
        root = run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise SystemExit("Käivita skript ÕigusAI Git repositooriumi seest.") from exc
    return Path(root).resolve()


def ensure_clean_tracked_worktree(repo: Path) -> None:
    unstaged = run(["git", "diff", "--quiet", "--"], cwd=repo, check=False)
    staged = run(["git", "diff", "--cached", "--quiet", "--"], cwd=repo, check=False)
    if unstaged.returncode != 0 or staged.returncode != 0:
        print("\nTracked failides on juba muudatusi:\n")
        run(["git", "status", "--short"], cwd=repo, check=False)
        raise SystemExit(
            "\nKatkestan, et mitte kirjutada olemasolevatele muudatustele otsa."
        )


def ensure_base(repo: Path, manifest: dict) -> None:
    base_commit = str(manifest.get("base_commit") or "").strip()
    if base_commit:
        exists = run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            cwd=repo,
            check=False,
        )
        if exists.returncode != 0:
            raise SystemExit(
                f"Patchi baascommit {base_commit} ei ole selles repos saadaval."
            )
        ancestor = run(
            ["git", "merge-base", "--is-ancestor", base_commit, "HEAD"],
            cwd=repo,
            check=False,
        )
        if ancestor.returncode != 0:
            raise SystemExit(
                f"HEAD ei põhine patchi baascommitil {base_commit}. "
                "Uuenda patch või kasuta õiget branchi."
            )

    expected_files = manifest.get("base_files") or []
    for item in expected_files:
        rel = Path(str(item.get("path") or ""))
        expected_blob = str(item.get("git_blob_sha") or "").strip()
        if not rel or not expected_blob:
            continue
        target = repo / rel
        if not target.is_file():
            raise SystemExit(f"Patchi baasfail puudub: {rel}")
        actual = git_output(repo, "hash-object", "--", str(rel))
        if actual != expected_blob:
            raise SystemExit(
                f"{rel} ei vasta patchi baasversioonile.\n"
                f"  oodatud blob: {expected_blob}\n"
                f"  tegelik blob: {actual}\n"
                "Katkestan enne patchimist."
            )


def prepare_existing_new_files(
    repo: Path, manifest: dict, backup_root: Path
) -> list[tuple[Path, Path]]:
    """Temporarily move exact untracked copies of files the patch creates.

    This makes the patch safe to use after a user has already copied one of the
    new files manually. A differing file is never overwritten.
    """
    moved: list[tuple[Path, Path]] = []
    for item in manifest.get("new_files") or []:
        rel = Path(str(item.get("path") or ""))
        expected_sha = str(item.get("sha256") or "").strip().lower()
        if not rel or not expected_sha:
            continue
        target = repo / rel
        if not target.exists():
            continue

        tracked = run(
            ["git", "ls-files", "--error-unmatch", "--", str(rel)],
            cwd=repo,
            check=False,
            capture=True,
        ).returncode == 0
        if tracked:
            raise SystemExit(
                f"Patch tahab luua faili {rel}, kuid see on juba Git-is tracked. "
                "Kontrolli, kas Step 1A on osaliselt commititud."
            )
        if not target.is_file():
            raise SystemExit(f"Patchi siht {rel} on olemas, kuid pole tavaline fail.")
        actual_sha = sha256_file(target)
        if actual_sha != expected_sha:
            raise SystemExit(
                f"Olemasolev untracked fail {rel} erineb patchi versioonist.\n"
                "Ma ei kirjuta sellele automaatselt otsa."
            )

        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
        moved.append((target, backup))
        print(f"= {rel}: sama sisu oli juba olemas; ühendan selle patchi rakendusse")
    return moved


def restore_moved(moved: Iterable[tuple[Path, Path]]) -> None:
    for target, backup in moved:
        if backup.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))


def apply_patch(repo: Path, patch: Path, *, reverse: bool) -> None:
    args = ["git", "apply"]
    if reverse:
        args.append("--reverse")
    args.extend(["--check", "--whitespace=error", str(patch)])
    checked = run(args, cwd=repo, check=False, capture=True)
    if checked.returncode != 0:
        if checked.stdout:
            print(checked.stdout, end="")
        if checked.stderr:
            print(checked.stderr, end="", file=sys.stderr)
        raise SystemExit("git apply --check ebaõnnestus; ühtegi patchi muudatust ei rakendatud.")

    args.remove("--check")
    run(args, cwd=repo)


def run_tests(repo: Path, manifest: dict) -> None:
    tests = manifest.get("tests") or []
    if not tests:
        print("= Manifestis pole teste määratud.")
        return
    for index, command in enumerate(tests, start=1):
        if not isinstance(command, list) or not command:
            raise SystemExit(f"Vigane testikäsk manifestis #{index}.")
        rendered = [
            sys.executable if str(part) == "{python}" else str(part)
            for part in command
        ]
        print("+", " ".join(rendered))
        result = run(rendered, cwd=repo, check=False)
        if result.returncode != 0:
            print("\nTESTID EBAÕNNESTUSID. Patch on jäetud worktree'sse ülevaatamiseks.")
            print("Kontrolli: git diff --check && git diff")
            raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rakenda ÕigusAI patch turvaliselt.")
    parser.add_argument("patch", help="Patch fail, nt patches/v10_step1a_retrieval_policy.patch")
    parser.add_argument("--reverse", action="store_true", help="Pööra patch tagasi.")
    parser.add_argument("--skip-tests", action="store_true", help="Ära käivita manifesti teste.")
    args = parser.parse_args()

    repo = ensure_repo_root(Path.cwd())
    patch_arg = Path(args.patch)
    patch = patch_arg if patch_arg.is_absolute() else (repo / patch_arg)
    patch = patch.resolve()
    if not patch.is_file():
        raise SystemExit(f"Patchi ei leitud: {patch}")

    manifest = load_manifest(patch)
    print(f"ÕigusAI patch: {manifest.get('name') or patch.name}")
    print(f"Repo: {repo}")
    print(f"HEAD: {git_output(repo, 'rev-parse', '--short', 'HEAD')}")

    if args.reverse:
        # Reverse is intentionally allowed on the dirty state created by this
        # patch. git apply --reverse --check is the safety gate here.
        apply_patch(repo, patch, reverse=True)
        print("✓ Patch pöörati tagasi.")
        run(["git", "diff", "--check"], cwd=repo)
        return 0

    ensure_clean_tracked_worktree(repo)
    ensure_base(repo, manifest)

    with tempfile.TemporaryDirectory(prefix="oigusai-patch-") as temp_dir:
        moved: list[tuple[Path, Path]] = []
        try:
            moved = prepare_existing_new_files(repo, manifest, Path(temp_dir))
            apply_patch(repo, patch, reverse=False)
        except BaseException:
            restore_moved(moved)
            raise

    print("✓ Patch rakendatud.")
    run(["git", "diff", "--check"], cwd=repo)
    print("✓ git diff --check korras.")

    if not args.skip_tests:
        run_tests(repo, manifest)
        print("✓ Manifesti regressioonitestid korras.")

    print("\nMuudatused:")
    run(["git", "status", "--short"], cwd=repo, check=False)
    print("\nMidagi ei stage'itud, commit'itud ega push'itud.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
