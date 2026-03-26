#!/usr/bin/env bash
set -e

echo "Running ruff format..."
ruff format .

echo "Running ruff check..."
ruff check .

echo "Running mypy..."
mypy --config-file mypy.ini nira_app tests

echo "Running pyright..."
pyright

echo "Running jscpd (duplication check)..."
npx jscpd nira_app --threshold 5

echo "Running pytest..."
pytest

echo "All checks passed successfully! ✨"
