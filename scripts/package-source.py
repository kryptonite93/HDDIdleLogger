"""Build a portable source archive without virtual environments or observations."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
output = root/"artifacts"/"HDDIdleLogger-source.zip"
output.parent.mkdir(exist_ok=True)
files = [path for path in root.iterdir() if path.is_file() and
         (path.suffix in (".md", ".txt", ".yml") or path.name in ("Dockerfile", ".dockerignore", ".gitignore"))]
for directory in ("app", "tests", "scripts", "unraid", ".github", ".impeccable"):
    files.extend(path for path in (root/directory).rglob("*") if path.is_file() and
                 "__pycache__" not in path.parts and path.suffix != ".pyc")
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, Path("HDDIdleLogger")/path.relative_to(root))
print(f"Packaged {len(set(files))} files: {output}")
