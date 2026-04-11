#!/usr/bin/env bash
# Set up τ²-bench (includes τ³ domains) and configure API key. Run once.
set -euo pipefail

# 1. Clone τ²-bench
if [ ! -d "tau2-bench" ]; then
    echo "Cloning τ²-bench (includes τ³ domains)..."
    git clone --depth 1 https://github.com/sierra-research/tau2-bench.git
fi

echo "Installing τ²-bench with knowledge extras..."
pip install -e "tau2-bench/[knowledge]"

# Python 3.13 removed the stdlib `audioop` module. Something in the tau2/litellm
# import chain still references it, so install the community drop-in replacement
# when running on 3.13+.
PY_MINOR=$(python -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
if [ "$PY_MINOR" -ge 13 ] 2>/dev/null; then
    echo "Python 3.13+ detected — installing audioop-lts (drop-in for removed stdlib module)..."
    pip install -q audioop-lts || echo "  ⚠ audioop-lts install failed — you may need to run: pip install audioop-lts"
fi

# 2. Create .env from .env.example if it doesn't exist
if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    echo ""
    echo "Created .env from .env.example."
fi

# 3. Check if OPENAI_API_KEY is still the placeholder — if so, prompt interactively
KEY_NEEDS_INPUT=0
if [ -f .env ]; then
    # Source without exporting to peek at the current value
    CURRENT_KEY=$(grep -E '^OPENAI_API_KEY=' .env | head -n1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [ -z "$CURRENT_KEY" ] || [ "$CURRENT_KEY" = "sk-..." ]; then
        KEY_NEEDS_INPUT=1
    fi
else
    KEY_NEEDS_INPUT=1
fi

if [ "$KEY_NEEDS_INPUT" = "1" ]; then
    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  OpenAI API key required"
    echo "────────────────────────────────────────────────────────────"
    echo "  Paste your OpenAI API key below (it will be written to .env"
    echo "  which is gitignored — your key will not be committed)."
    echo ""
    echo "  Format: sk-..."
    echo "  Press Ctrl-C to skip and edit .env manually later."
    echo ""
    # -s hides input (like a password prompt)
    read -r -s -p "  OPENAI_API_KEY: " USER_KEY
    echo ""

    if [ -z "$USER_KEY" ]; then
        echo "  (no key entered — edit .env manually before running eval)"
    elif [[ ! "$USER_KEY" =~ ^sk- ]]; then
        echo "  ⚠  Warning: key does not start with 'sk-'. Saving anyway."
    fi

    # Write to .env (replace the placeholder line)
    if [ -n "$USER_KEY" ]; then
        if [ -f .env ]; then
            # Use awk to replace the OPENAI_API_KEY line without touching others
            awk -v key="$USER_KEY" '
                /^OPENAI_API_KEY=/ { print "OPENAI_API_KEY=" key; next }
                { print }
            ' .env > .env.tmp && mv .env.tmp .env
        else
            echo "OPENAI_API_KEY=$USER_KEY" > .env
        fi
        echo "  ✓ Key saved to .env"
    fi
fi

echo ""
echo "Done. Next steps:"
if [ "$KEY_NEEDS_INPUT" = "1" ] && [ -z "${USER_KEY:-}" ]; then
    echo "  1. Edit .env and paste your OPENAI_API_KEY"
    echo "  2. Run: bash eval/eval.sh"
else
    echo "  → Run: bash eval/eval.sh"
fi
