"""Build hook: ship the guides inside the package.

The Markdown guides live in the repository's top-level docs/ folder. The in-app
Help (`/help`) serves them with no internet, so a normal (non-editable) install
needs its own copy: this copies docs/*.md and the images they use into
src/openmuscle/web/help_docs/ before the package files are collected. A clone
or an editable install reads docs/ directly and ignores that copy.

Everything else is configured in pyproject.toml.
"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
TARGET = HERE / "src" / "openmuscle" / "web" / "help_docs"
KEEP = {".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def sync_docs():
    """Copy docs/ into the package. No-op when docs/ is not beside pc/ (building
    from an sdist, which already carries the copy)."""
    if not DOCS.is_dir():
        return
    if TARGET.exists():
        shutil.rmtree(TARGET)
    for src in DOCS.rglob("*"):
        if src.is_file() and src.suffix.lower() in KEEP:
            dest = TARGET / src.relative_to(DOCS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


class BuildPy(build_py):
    def run(self):
        sync_docs()
        super().run()


class Sdist(sdist):
    def run(self):
        sync_docs()
        super().run()


setup(cmdclass={"build_py": BuildPy, "sdist": Sdist})
