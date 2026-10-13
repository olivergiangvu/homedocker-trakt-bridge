#!/usr/bin/env python3
"""Stage/preview a guarded AIOStreams NextUp canary patch in an upstream checkout.

DRY-RUN by default. Requires the exact reviewed upstream commit/blob.
Never targets running containers, persistent databases or Compose files.
"""
import argparse
import difflib
from pathlib import Path
import subprocess

UPSTREAM_SHA = "bd1de5bc9720a9fbadfa0f990de7113db3decf7f"
LIBRARY_BLOB_SHA = "4d8acdbd5f48013a9fab2ce712156df1447121d2"
LIBRARY = Path("packages/server/src/routes/jellyfin/library.ts")
NEW_HELPER = Path("packages/server/src/routes/jellyfin/nextup-identity.ts")
HELPER_SOURCE = Path(__file__).resolve().parent / "src" / "nextup-identity.ts"

REPLACEMENTS = [
    (
        "import { getMeta } from './resolve.js';",
        "import { getMeta } from './resolve.js';\n"
        "import { reconcileNextUpCandidates } from './nextup-identity.js';",
    ),
    (
        "    const provider = getWatchStateProvider();\n"
        "    const items: JellyfinItem[] = [];\n"
        "    if (seriesId) {",
        "    const provider = getWatchStateProvider();\n"
        "    const items: JellyfinItem[] = [];\n"
        "    // Runtime-only provenance for NextUp response normalization.\n"
        "    const nextUpSourceBaseByItemId = new Map<string, string>();\n"
        "    if (seriesId) {",
    ),
    (
        "        return nextUpForSeries(ctx, d, row, { includeResumable }).catch(\n"
        "          () => null\n"
        "        );",
        "        const candidate = await nextUpForSeries(\n"
        "          ctx, d, row, { includeResumable }\n"
        "        ).catch(() => null);\n"
        "        if (candidate) nextUpSourceBaseByItemId.set(candidate.Id, d.i);\n"
        "        return candidate;",
    ),
    (
        "        if (shown.has(series)) continue;\n"
        "        shown.add(series);\n"
        "        items.push(next);\n"
        "      }\n"
        "    }\n"
        "    send(req, res, items, items.length, 0);",
        "        if (shown.has(series)) continue;\n"
        "        shown.add(series);\n"
        "        items.push(next);\n"
        "      }\n"
        "    }\n"
        "    // Explicit, verified IMDb crosswalk only. Unset = complete no-op.\n"
        "    // Retains one unique active resume; ambiguous rewatches fail open.\n"
        "    const normalized = reconcileNextUpCandidates(\n"
        "      items, nextUpSourceBaseByItemId,\n"
        "      process.env.AIO_NEXTUP_IDENTITY_ALIASES ?? ''\n"
        "    );\n"
        "    send(req, res, normalized, normalized.length, 0);",
    ),
]


def patch_source(source):
    result = source
    for old, new in REPLACEMENTS:
        count = result.count(old)
        if count != 1:
            raise ValueError("Unsafe upstream anchor (expected exactly 1, got %d): %s"
                             % (count, old[:76]))
        result = result.replace(old, new)
    return result


def git(checkout, *args):
    return subprocess.check_output(
        ["git", "-C", str(checkout), *args],
        text=True, stderr=subprocess.DEVNULL
    ).strip()


def stage(checkout, apply=False):
    checkout = checkout.resolve()
    if git(checkout, "rev-parse", "HEAD") != UPSTREAM_SHA:
        raise ValueError("Upstream checkout HEAD differs from pinned reviewed commit")
    library = checkout / LIBRARY
    new_helper = checkout / NEW_HELPER
    if not library.is_file() or new_helper.exists():
        raise ValueError("Required upstream file missing or helper already exists")
    if git(checkout, "hash-object", str(library)) != LIBRARY_BLOB_SHA:
        raise ValueError("Upstream library.ts content differs from pinned reviewed blob")
    if apply and git(checkout, "status", "--porcelain"):
        raise ValueError("Refusing to apply patch to a dirty checkout")

    source = library.read_text(encoding="utf-8")
    modified = patch_source(source)
    helper = HELPER_SOURCE.read_text(encoding="utf-8")
    print("UPSTREAM_PIN=" + UPSTREAM_SHA)
    print("UPSTREAM_BLOB_VERIFIED=true")
    print("AIO_RUNTIME_DEFAULT=UNCHANGED")
    print("PATCH_SOURCE=" + str(LIBRARY))
    print("PATCH_HELPER=" + str(NEW_HELPER))
    if apply:
        # Only a separate development checkout; no production path touched.
        new_helper.write_text(helper, encoding="utf-8")
        library.write_text(modified, encoding="utf-8")
        print("RESULT=PATCH_APPLIED_TO_DEVELOPMENT_CHECKOUT")
    else:
        diff = difflib.unified_diff(
            source.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile="a/" + str(LIBRARY), tofile="b/" + str(LIBRARY)
        )
        print("".join(diff), end="")
        print("RESULT=DRY_RUN_ONLY")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("checkout", type=Path, help="clean AIOStreams Git checkout")
    ap.add_argument("--apply", action="store_true",
                    help="explicitly patch a clean, pinned *development* checkout")
    args = ap.parse_args()
    try:
        stage(args.checkout, apply=args.apply)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("STOP: " + str(error)) from error


if __name__ == "__main__":
    main()
