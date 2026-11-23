#!/usr/bin/env python3
"""공유용 zip을 만든다.  python tools/make_zip.py  →  dist/subway-congestion-route.zip

받는 사람은 압축을 풀고 run.bat(Windows) 또는 bash run.sh 를 실행하면 된다. 파이썬 3.10+만 있으면 된다.
들어가는 것: 프로그램, 데이터(data/), 실험 결과(results/), 테스트, 문서.
빠지는 것: .venv, subway-etl(팀원 저장소 복사본), 알고리즘/(수업 자료), 캐시, dist.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "subway-congestion-route.zip"
TOP = "subway-congestion-route"

FILES = ["README.md", "START_HERE.txt", "CLAUDE.md", "requirements.txt", "run.bat", "run.sh",
         "app.py", "route.py", "routelib.py", "graph.py"]
DIRS = ["algorithms", "experiments", "tools", "tests", "ui", "data", "results"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", ".git"}
SKIP_SUFFIX = {".pyc", ".pyo"}
EXECUTABLE = {"run.sh"}


def wanted(p: Path) -> bool:
    return not (SKIP_PARTS & set(p.parts)) and p.suffix not in SKIP_SUFFIX and p.is_file()


def main() -> int:
    missing = [f for f in FILES if not (ROOT / f).is_file()] + [d for d in DIRS if not (ROOT / d).is_dir()]
    if missing:
        print("없는 항목:", missing, file=sys.stderr)
        return 1
    OUT.parent.mkdir(exist_ok=True)
    paths = [ROOT / f for f in FILES]
    for d in DIRS:
        paths += sorted(p for p in (ROOT / d).rglob("*") if wanted(p.relative_to(ROOT)))
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            rel = p.relative_to(ROOT)
            info = zipfile.ZipInfo(f"{TOP}/{rel.as_posix()}", date_time=(2026, 11, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if rel.as_posix() in EXECUTABLE else 0o644) << 16)
            data = p.read_bytes()
            if rel.suffix == ".bat":
                # cmd.exe는 .bat을 UTF-8이 아닌 현지 인코딩(한국어 Windows는 CP949)으로 읽어서 한글이 있으면 깨진다.
                # 영문 전용이어야 하고, 줄바꿈은 CRLF여야 goto/label이 안전하다.
                if not data.isascii():
                    raise SystemExit(f"{rel}: .bat 파일에는 ASCII 문자만 쓸 수 있습니다")
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(info, data)
    print(f"{OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, 파일 {len(paths)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
