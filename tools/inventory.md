# Tool Inventory

Registry of every tool built for CAT Automation. Claude checks this before building anything new.

| Tool Name | Location | What It Does | When to Use It | Status |
|-----------|----------|--------------|----------------|--------|
| token_tracker | tools/token_tracker/ | Logs Claude usage per session (tokens + cost notes) | After every Claude Code session | ✅ Ready |
| check_processor | tools/check_processor/ | Splits batch scan PDFs, OCR-extracts check details (date, check#, coverage, amount), renames files | Drop scan PDF in watched folder | ✅ Ready |
| cat_web | tools/cat_web/ | Flask web app — claims dashboard, per-claim ledger (transactions, expenses, policy limits), check queue, auth. Phases 1-3 complete. | `python tools/cat_web/main.py` after `python tools/cat_web/seed.py` | ✅ Ready |

---

*Update this table every time a tool is created or significantly changed.*
