#!/usr/bin/env bash
# 혼잡도 길찾기 실행 (맥·리눅스). 사용법: ./run.sh   (권한 오류가 나면: bash run.sh)
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" app.py --open "$@"
  fi
done
echo "Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요." >&2
exit 1
