#!/usr/bin/env bash
# Per-machine setup. Run once after cloning. Re-runnable safely.
#
# This script keeps machine identity (git config, secrets) parameterized per-repo
# so you can switch machines without leaking personal credentials between them.

set -e

echo "slideAtelier setup"
echo "=================="
echo ""

# 1. .env from template
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ Created .env from template (fill in ANTHROPIC_API_KEY before running)"
else
  echo "• .env already exists — leaving alone"
fi

# 2. Repo-local git identity (overrides global ~/.gitconfig for this repo only)
echo ""
echo "Setting up repo-local git identity (does not affect global git config)…"
current_name=$(git config --local user.name 2>/dev/null || echo "")
current_email=$(git config --local user.email 2>/dev/null || echo "")
if [ -n "$current_name" ] && [ -n "$current_email" ]; then
  echo "• Already set: $current_name <$current_email>"
  read -p "  Overwrite? [y/N]: " overwrite
  if [ "$overwrite" != "y" ] && [ "$overwrite" != "Y" ]; then
    echo "  Skipping git identity setup"
    set_identity=0
  else
    set_identity=1
  fi
else
  set_identity=1
fi

if [ "$set_identity" = "1" ]; then
  read -p "  Git author name for this repo: " name
  read -p "  Git author email for this repo: " email
  if [ -n "$name" ] && [ -n "$email" ]; then
    git config user.name "$name"
    git config user.email "$email"
    echo "✓ Repo-local git identity set"
  fi
fi

# 3. Install Python deps via uv
echo ""
if ! command -v uv >/dev/null 2>&1; then
  echo "✗ uv not found. Install with: brew install uv"
  exit 1
fi
echo "Installing dependencies with uv…"
uv sync
echo "✓ Dependencies installed"

echo ""
echo "Next steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY"
echo "  2. uv run atelier --help"
echo ""
