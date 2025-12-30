# DataScope

Data Debugging Agent for Databricks - investigates data quality issues and finds root causes.

## Quick Start

```bash
# 1. Clone and install
cd datascope-project
pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env with your Databricks credentials

# 3. Test connection
datascope test

# 4. Run an investigation
datascope "Why do some customers have NULL churn_risk?"
```

## Development with Claude Code

This project is set up for development with Claude Code + Cursor.

1. Open in Cursor: `cursor datascope-project/`
2. Claude Code reads `CLAUDE.md` for context
3. Check `TODO.md` for current tasks

## Project Structure

```
src/datascope/
├── tools/           # Databricks tools (SQL, Schema, Lineage)
├── agent/           # LangGraph agent (State, Prompts, Graph)
└── evaluation/      # Test runner and judge
```

## Next Steps

See `TODO.md` for the build plan. Start with Phase 1: Tools.
