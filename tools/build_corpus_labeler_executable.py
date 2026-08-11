#!/usr/bin/env python3
"""Build the standalone corpus_labeler executable (ticket 08).

PyInstaller does not cross-compile: run this *on the client's confirmed OS*
(or a VM/CI runner matching it) -- a build produced on macOS only runs on
macOS, a Windows build only runs on Windows, etc.

Creates a throwaway build venv with the trimmed dependency set
(requirements-corpus-labeler.txt: PyMuPDF + Pillow, not the full
requirements-anonymizer*.txt), then invokes PyInstaller from inside it so the
bundled executable doesn't carry zxing-cpp/reportlab/torch/transformers.

    python3 tools/build_corpus_labeler_executable.py

Output lands in `dist/corpus-labeler` (`dist/corpus-labeler.exe` on Windows).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_VENV = ROOT / ".venv-corpus-labeler-build"
ADD_DATA_SEP = ";" if sys.platform.startswith("win") else ":"

# anonymize_construction_pdfs.py is loaded at runtime via
# importlib.util.spec_from_file_location (as a plain data file, per this
# ticket's trimmed-dependency decision -- see its module docstring), not a
# static `import`, so PyInstaller's analysis never sees its imports. Its
# own top-level imports (all stdlib; confirmed no top-level NER/ML import)
# must be declared here explicitly or they silently go missing at runtime.
ANONYMIZER_MODULE_HIDDEN_IMPORTS = [
    "argparse", "collections", "csv", "datetime", "hashlib", "hmac",
    "importlib.metadata", "io", "json", "os", "platform", "re", "resource",
    "secrets", "shutil", "signal", "subprocess", "sys", "tempfile", "time",
    "uuid", "dataclasses", "pathlib", "typing",
    "PIL.ImageDraw",  # anonymize_construction_pdfs.py's own `from PIL import ... ImageDraw`
]


def venv_python(venv_dir: Path) -> Path:
    subdir = "Scripts" if sys.platform.startswith("win") else "bin"
    exe = "python.exe" if sys.platform.startswith("win") else "python"
    return venv_dir / subdir / exe


def main() -> int:
    if not BUILD_VENV.exists():
        print(f"Creating build venv at {BUILD_VENV}")
        venv.EnvBuilder(with_pip=True).create(BUILD_VENV)

    python = venv_python(BUILD_VENV)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True,
    )
    subprocess.run(
        [
            str(python), "-m", "pip", "install", "--quiet",
            "-r", str(ROOT / "requirements-corpus-labeler.txt"),
            "pyinstaller>=6,<7",
        ],
        check=True,
    )

    if (ROOT / "build").exists():
        shutil.rmtree(ROOT / "build")
    (ROOT / "build").mkdir(parents=True)

    # Precompile to bytecode with the *build venv's* interpreter, so the
    # magic number matches what PyInstaller embeds -- see MODULE_PATH in
    # corpus_labeler.py for why (avoids a ~30s from-source recompile on
    # every single onefile launch).
    compiled_module = ROOT / "build" / "anonymize_construction_pdfs.pyc"
    subprocess.run(
        [
            str(python), "-c",
            "import py_compile, sys; "
            "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
            str(ROOT / "tools" / "anonymize_construction_pdfs.py"),
            str(compiled_module),
        ],
        check=True,
    )

    hidden_import_args = []
    for name in ANONYMIZER_MODULE_HIDDEN_IMPORTS:
        hidden_import_args += ["--hidden-import", name]

    subprocess.run(
        [
            str(python), "-m", "PyInstaller",
            "--onefile",
            "--name", "corpus-labeler",
            "--distpath", str(ROOT / "dist"),
            "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            "--add-data", f"{ROOT / 'tools' / 'corpus_labeler.html'}{ADD_DATA_SEP}.",
            "--add-data", f"{compiled_module}{ADD_DATA_SEP}.",
            *hidden_import_args,
            str(ROOT / "tools" / "corpus_labeler.py"),
        ],
        cwd=ROOT,
        check=True,
    )

    suffix = ".exe" if sys.platform.startswith("win") else ""
    built = ROOT / "dist" / f"corpus-labeler{suffix}"
    print(f"\nBuilt: {built}")
    print("Smoke-test it before sending to the client: double-click it (no terminal),")
    print("pick a real corpus PDF, label at least one item, export, and confirm a")
    print("correctly-schemed JSON lands in labeled-output/ next to the executable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
