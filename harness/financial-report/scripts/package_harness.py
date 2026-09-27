"""Build a source-only, portable project folder. Never include credentials or caches."""
from pathlib import Path
import argparse
import zipfile

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'financial-report-harness.zip')
    args = parser.parse_args()
    files = [ROOT / name for name in ['README.md', 'AGENTS.md', 'CLAUDE.md', 'setup.ps1', 'run.ps1', '.gitignore', '.gitattributes', '.env.example', '.claude/agents/financial-report.md']]
    files += sorted(p for p in (ROOT / 'harness' / 'financial-report').rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py', '.md', '.json', '.sql', '.lock', '.in'})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, 'financial-report-harness/' + path.relative_to(ROOT).as_posix())
    print(f'{len(files)} files packaged; {args.output.stat().st_size} bytes; {args.output}')


if __name__ == '__main__':
    main()
