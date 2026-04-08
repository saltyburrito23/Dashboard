#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "🔐 Starting Schwab authentication..."
echo "This will open your browser for login. Complete the OAuth flow."
echo ""

# Activate virtual environment
source .venv/bin/activate

# Run streamlit to trigger auth (timeout after 2 minutes if user doesn't complete)
timeout 120 streamlit run app.py 2>&1 | grep -i "after authorizing" || true

echo ""
echo "⏳ Waiting for token file to be created..."
sleep 2

if [ -f ".token.json" ]; then
    echo "✅ Token file created successfully!"
    echo ""
    echo "📤 Pushing to GitHub..."
    
    git add .token.json requirements.txt pyproject.toml
    git commit -m "Update Schwab API tokens (auto-synced from local auth)" || echo "⚠️  No changes to commit"
    git push origin main
    
    echo "✅ Tokens pushed to GitHub!"
    echo ""
    echo "💡 Your Streamlit Cloud app will automatically use these tokens on the next deploy."
else
    echo "❌ Token file not found. Make sure you completed the Schwab OAuth flow."
    exit 1
fi
