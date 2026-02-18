# CAT Automation — Master Reference

## Project Vision

<!-- FILL THIS IN: Describe the side hustle process being automated here. -->
<!-- What is CAT Automation? What manual steps are you replacing? What does the end-to-end workflow look like? -->

CAT Automation is a personal automation system designed to replace a manual side-hustle workflow with a series of small, composable Python tools. Each tool handles one discrete step in the process. Tools are triggered automatically when files appear in a designated folder (file-watching pattern) and can be layered together over time into a larger pipeline.

[Project Vision placeholder — fill in your side hustle description here.]

---

## How Claude Should Behave

1. **Check inventory first.** Before building any new tool, read `tools/inventory.md`. If a tool that does the same thing already exists, use or extend it instead of building a duplicate.

2. **Write tools in Python** unless there is a specific, stated reason to use something else.

3. **Update the inventory after every tool.** After creating or significantly modifying a tool, update `tools/inventory.md` to reflect the change.

4. **One tool, one folder.** Each tool lives in its own subfolder inside `tools/`. Main entry point is always `main.py`.

5. **Keep tools self-contained.** Each tool should work on its own without depending on other tools, unless explicitly designed as a pipeline.

6. **No Claude at runtime.** Tools run autonomously — they do not call the Claude API at runtime unless that is explicitly part of the tool's stated purpose. Claude is used to BUILD tools, not to run them.

7. **Keep it simple.** Don't over-engineer. Build the minimum needed for the current task. Avoid premature abstractions.

8. **Log token usage.** At the end of each Claude Code session, run `python tools/token_tracker/tracker.py log` to record the session.

---

## Tool Conventions

- **Naming:** lowercase, underscores (e.g. `file_sorter`, `pdf_renamer`, `token_tracker`)
- **Location:** `tools/<tool_name>/`
- **Entry point:** always `main.py`
- **File-watching:** each tool either has its own watcher built in, or is called by a central watcher — decide per tool at build time
- **Triggers:** tools activate when a specific file or file type appears in a watched folder
- **Optional docs:** a brief `README.md` inside the tool folder is welcome but not required

### Folder pattern for each tool:
```
tools/
    my_tool_name/
        main.py
        README.md   (optional)
```

### Running a tool manually:
```bash
python tools/<tool_name>/main.py
```

---

## Current Status

### Tools built
| Tool | Status | Notes |
|------|--------|-------|
| token_tracker | Ready | Logs Claude session usage; run manually after each session |

### In progress
- Nothing currently in progress

### Up next
- First domain-specific tool (TBD based on project vision)

---

*This file is read by Claude at the start of every session. Keep it up to date.*
