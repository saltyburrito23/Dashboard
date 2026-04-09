# Dashboard V3 Web

Hosted/web research dashboard for Schwab/thinkorswim options data using [`schwabdev`](https://github.com/tylerebowers/Schwabdev). This version is a polling-based Mac-friendly dashboard that:

- loads a live option chain from Schwab
- computes an explainable `bullish`, `bearish`, or `mixed` bias
- surfaces logistics like expected move, liquidity, call/put pressure, OI walls, and gamma structure

## What This App Is

This is a decision-support dashboard, not an order-entry bot. The bias engine is intentionally explainable and rules-based so you can see why it leans bullish, bearish, or mixed.

## What’s New in Dashboard V3 Web

- Archived snapshot storage and snapshot review were removed from the web build.
- The hosted app now focuses on live analysis, trade planning, charts, and targets only.
- Clearer explanation for `Wait / No New Premium` cases, including why the model is pausing.
- More robust dynamic target handling when intraday high/low data is unavailable.
- `.DS_Store` is ignored so Mac file-system artifacts stay out of the repository.

## Stack

- Python 3.11+
- `schwabdev` for Schwab auth + market data
- `streamlit` for the UI
- `plotly` for charts

## Setup

1. Create and activate a virtual environment.
2. Install the project:

```bash
pip install -e .
```

3. Copy the environment template and fill in your Schwab app credentials:

```bash
cp .env.example .env
```

4. Make sure your Schwab app is configured with:

- callback URL: `https://127.0.0.1`
- `Accounts and Trading Production`
- `Market Data Production`
- status: `Ready for use`

These details come from the current `schwabdev` setup guidance and are especially relevant on macOS.

## Run

```bash
streamlit run app.py
```

On the first authenticated run, `schwabdev` will open a browser window for OAuth. After approval, paste the full callback URL back into the terminal when prompted.

## Project Layout

```text
app.py
src/options_bias_dashboard/
  analytics.py
  config.py
  models.py
  normalization.py
  schwab_service.py
tests/
```

## Current Behavior

- Uses REST polling instead of websocket streaming for the first version
- Pulls option chain, quote context, and short-term price history
- Does not persist local snapshot archives in the web build
- Scores directional bias from:
  - call vs put volume
  - call vs put open interest
  - IV skew
  - gamma concentration estimate
  - short-term price trend
- Reduces confidence when spreads are wide or chain quality is weak

## Next Good Step

Once this baseline is working with your Schwab credentials, the next upgrade is a selective websocket stream for the underlying and the most relevant contracts near spot.

## Reminder

Options involve risk. This tool is for research and workflow support only and should not be treated as individualized investment advice.
