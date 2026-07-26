#!/usr/bin/env bash
# founder-os installer. One command, no configuration, works in any repository.
#
#   curl -fsSL https://raw.githubusercontent.com/avanturer/claude-bestpractice/HEAD/install.sh | bash
#
# or, from a clone:
#
#   ./install.sh
#
# Everything is idempotent. Running it twice changes nothing the second time.

set -euo pipefail

REPO_URL="${FOUNDER_OS_REPO:-https://github.com/avanturer/claude-bestpractice.git}"
INSTALL_DIR="${FOUNDER_OS_DIR:-$HOME/.founder-os}"
MARKETPLACE="founder-os"
PLUGIN="founder-os"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"
command -v claude >/dev/null 2>&1 || die "the claude CLI is required — see https://code.claude.com"

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
[ -n "$PY" ] || die "python 3.9 or newer is required (no other dependency is)"

bold "founder-os"
dim  "python: $($PY --version 2>&1) · claude: $(claude --version 2>&1 | head -1)"

# ---------------------------------------------------------------- fetch or update
if [ -d "$INSTALL_DIR/.git" ]; then
  dim "updating $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" reset --quiet --hard origin/HEAD 2>/dev/null \
    || git -C "$INSTALL_DIR" pull --quiet --ff-only
elif [ -f "$(dirname "$0")/plugin/.claude-plugin/plugin.json" ]; then
  # Running from a clone: install in place rather than fetching a second copy.
  INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
  dim "installing from $INSTALL_DIR"
else
  dim "cloning into $INSTALL_DIR"
  git clone --quiet --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

chmod +x "$INSTALL_DIR"/plugin/bin/* 2>/dev/null || true

# ------------------------------------------------------------------ prove it works
# The gates are proven by attempting known-bad actions BEFORE anything is registered.
# Installing a plugin whose gates silently do nothing is worse than not installing it.
bold "verifying"
# mktemp, not a fixed /tmp path: a predictable name that an attacker (or a stale
# symlink) already created is followed by the redirect, truncating whatever it points at.
DOCTOR_LOG="$(mktemp -t founder-os-doctor.XXXXXX)"
trap 'rm -f "$DOCTOR_LOG" "${LOG:-}"' EXIT
if ! "$PY" "$INSTALL_DIR/plugin/bin/founder-os-doctor" >"$DOCTOR_LOG" 2>&1; then
  cat "$DOCTOR_LOG"
  die "the doctor failed — refusing to install gates that do not fire"
fi
dim "$(tail -1 "$DOCTOR_LOG")"

# ---------------------------------------------------------------------- register
# The CLI installs a COPY into its own plugin cache, pinned to a commit. So a second run
# over new source used to leave the old gates running while the doctor certified the new
# ones and the script printed "installed" — an update mechanism that updated nothing.
# `update` is what refreshes the cache; `install` on an existing plugin is a no-op.
bold "registering"
if ! claude plugin marketplace add "$INSTALL_DIR" >/dev/null 2>&1; then
  claude plugin marketplace update "$MARKETPLACE" >/dev/null 2>&1 \
    || dim "marketplace already registered"
fi

# `claude plugin list` indents and bullets its entries, so a "^name@" grep never matched
# and the upgrade branch was dead. Match anywhere on the line.
# `update` is also a no-op when the version string has not changed, which is exactly the
# case during development — so uninstall and reinstall, which does refresh the cache.
LOG="$(mktemp -t founder-os-register.XXXXXX)"
if claude plugin list 2>/dev/null | grep -q "${PLUGIN}@${MARKETPLACE}"; then
  dim "refreshing the installed copy"
  claude plugin uninstall "${PLUGIN}@${MARKETPLACE}" >"$LOG" 2>&1 || true
fi
claude plugin install "${PLUGIN}@${MARKETPLACE}" >"$LOG" 2>&1 \
  || { cat "$LOG"; die "could not install the plugin"; }

# Prove the registered COPY is the code the doctor just verified. The doctor ran against
# the source tree; the CLI executes a pinned copy in its own cache, and a run that
# certified one while shipping the other is worse than no check at all.
# Content only, never paths: the CLI stores its copy under a version directory, so
# hashing relative paths compared `plugin/x` against `1.0.0/plugin/x` and every install
# aborted on byte-identical trees. What matters is that the same code is there.
FINGERPRINT="$("$PY" - "$INSTALL_DIR/plugin" <<'EOF'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digests = sorted(
    hashlib.sha256(p.read_bytes()).hexdigest()
    for p in root.rglob("*")
    if p.is_file() and "__pycache__" not in p.parts
)
print(hashlib.sha256("\n".join(digests).encode()).hexdigest())
EOF
)"
CACHED="$(find "$HOME/.claude/plugins" -type d -name "$PLUGIN" -print -quit 2>/dev/null || true)"
if [ -n "$CACHED" ]; then
  CACHED_FINGERPRINT="$("$PY" - "$CACHED" <<'EOF'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digests = sorted(
    hashlib.sha256(p.read_bytes()).hexdigest()
    for p in root.rglob("*")
    if p.is_file() and "__pycache__" not in p.parts
)
print(hashlib.sha256("\n".join(digests).encode()).hexdigest())
EOF
)"
  if [ "$FINGERPRINT" != "$CACHED_FINGERPRINT" ]; then
    die "the registered copy at $CACHED is NOT the code just verified. Run: claude plugin uninstall ${PLUGIN}@${MARKETPLACE} && $0"
  fi
  dim "registered copy matches the verified source"
fi

# The plugin's bin/ is on the Bash tool's PATH inside a session automatically. This
# symlink is only so the commands also work in the founder's own terminal.
LINK_DIR="$HOME/.local/bin"
mkdir -p "$LINK_DIR"
for command in founder-os founder-os-doctor founder-os-knowledge founder-os-plan \
               founder-os-decide founder-os-ingest founder-os-reindex founder-os-ci \
               founder-os-attempt founder-os-options founder-os-ship; do
  ln -sf "$INSTALL_DIR/plugin/bin/$command" "$LINK_DIR/$command"
done

echo
bold "installed"
cat <<TEXT

  $INSTALL_DIR

  Plugin changes take effect in your NEXT session, not this one.

  In any repository:
    founder-os init      derive the knowledge layer from the code that is there
    founder-os status    what is known, in flight, planned and enforced
    founder-os doctor    prove every gate still fires

  Inside a Claude session:
    /founder-os:status   the same view, read by the agent
    /founder-os:plan     the work ledger
    /founder-os:review   fresh-context review of the current diff

TEXT

case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *) dim "add $LINK_DIR to PATH to use these outside a Claude session" ;;
esac
