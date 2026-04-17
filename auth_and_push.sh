#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "🔐 Starting Schwab authentication..."
echo "This will open your browser for login. Complete the OAuth flow."
echo ""

PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
TOKEN_DB=".schwab_tokens.db"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# Run Streamlit through Python directly so copied virtualenv entrypoints
# are not required for the auth-refresh flow to work.
timeout 120 "$PYTHON_BIN" -m streamlit run app.py 2>&1 | grep -i "after authorizing" || true

echo ""
echo "⏳ Waiting for token database to be created..."
sleep 2

if [ -f "$TOKEN_DB" ]; then
    echo "✅ Token database created successfully!"
    echo ""
    echo "📤 Pushing to GitHub..."
    
    git add "$TOKEN_DB" requirements.txt pyproject.toml
    git commit -m "Update Schwab API tokens (auto-synced from local auth)" || echo "⚠️  No changes to commit"
    git push origin main
    
    echo "✅ Tokens pushed to GitHub!"
    echo ""
    echo "💡 Your Streamlit Cloud app will automatically use these tokens on the next deploy."
else
    echo "❌ Token database not found. Make sure you completed the Schwab OAuth flow."
    exit 1
fi
