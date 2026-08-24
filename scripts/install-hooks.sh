#!/bin/sh
# Symlink the tracked hooks in scripts/ into .git/hooks/.
# Run once per clone:  ./scripts/install-hooks.sh
#
# Symlink, not copy, so edits to scripts/pre-commit take effect immediately.
set -e

repo_root=$(cd "$(dirname "$0")/.." && pwd)
hooks_dir="$repo_root/.git/hooks"

if [ ! -d "$hooks_dir" ]; then
  echo "install-hooks: $hooks_dir not found — run this inside a git clone." >&2
  exit 1
fi

for hook in pre-commit pre-push; do
  src="$repo_root/scripts/$hook"
  dst="$hooks_dir/$hook"
  [ -f "$src" ] || continue
  chmod +x "$src"
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "install-hooks: $dst exists and is not a symlink — backing up to $dst.bak"
    mv "$dst" "$dst.bak"
  fi
  ln -sf "$src" "$dst"
  echo "install-hooks: linked $hook"
done

printf 'gitleaks: '
command -v gitleaks >/dev/null 2>&1 && gitleaks version || echo "MISSING — brew install gitleaks"
