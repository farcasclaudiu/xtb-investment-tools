# Install Skills For Agents

This file is written for LLM agents and coding assistants. Follow it when a user asks you to install, use, or copy the XTB portfolio skills from this repository.

The repository ships two standalone, harness-neutral skill folders:

- `skills/xtb-portfolio-review`
- `skills/xtb-wealthfolio-export`

Each skill folder is self-contained: it includes `SKILL.md`, references, runnable scripts, Python source files, requirements, and offline Chart.js assets where needed. Users may copy a single skill folder without cloning the full repository.

## Choose The Installation Path

Use one of these paths based on the user's agent harness.

| Harness | Recommended action |
| ------- | ------------------ |
| Codex | Copy the desired skill folder into `~/.codex/skills/`. |
| Claude or Claude Code | Copy the desired skill folder into the user's configured skills/instructions directory, or keep it in the project and read `SKILL.md` before use. |
| Cursor, Aider, OpenHands, generic LLM | Keep/copy the skill folder anywhere accessible and explicitly read `SKILL.md` before running scripts. |
| Unknown harness | Do not assume a special install location. Use the skill folder directly. |

## Install From This Repository

Run these commands from the repository root.

For Codex:

```bash
mkdir -p "$HOME/.codex/skills"
cp -R skills/xtb-portfolio-review "$HOME/.codex/skills/"
cp -R skills/xtb-wealthfolio-export "$HOME/.codex/skills/"
```

For a generic agent workspace, copy the skill folders to a user-chosen directory:

```bash
mkdir -p ./agent-skills
cp -R skills/xtb-portfolio-review ./agent-skills/
cp -R skills/xtb-wealthfolio-export ./agent-skills/
```

If only one workflow is needed, copy only that folder.

## Install From A Copied Skill Folder

If the user already has only one copied skill folder, no repository files are required. Work from the directory where the user's XTB workbook and future `results/` folder should live.

For portfolio review:

```bash
/path/to/xtb-portfolio-review/scripts/setup-env.sh
/path/to/xtb-portfolio-review/scripts/validate-review.sh
/path/to/xtb-portfolio-review/scripts/run-review.sh /path/to/report.xlsx
```

For Wealthfolio export:

```bash
/path/to/xtb-wealthfolio-export/scripts/setup-env.sh
/path/to/xtb-wealthfolio-export/scripts/validate-export.sh
/path/to/xtb-wealthfolio-export/scripts/export-wealthfolio.sh /path/to/report.xlsx
```

The setup scripts create or reuse `.venv` in the current working directory. If network access or package installation requires approval, ask before running `setup-env.sh`.

## Use Without Installing

If you cannot copy files, use the skill in place:

1. Read the relevant `SKILL.md` completely.
2. Read referenced files only when the skill tells you to.
3. Run the bundled validation script.
4. Run the bundled workflow script.
5. Report generated output paths and data-quality caveats to the user.

Example prompts a user can give an agent:

```text
Read skills/xtb-portfolio-review/SKILL.md and use that skill to generate a portfolio report for my XTB export.
```

```text
Read skills/xtb-wealthfolio-export/SKILL.md and use that skill to create a Wealthfolio CSV from my XTB export.
```

## Skill Contents

Expected portable structure:

```text
skills/
  xtb-portfolio-review/
    SKILL.md
    references/
    scripts/
      setup-env.sh
      validate-review.sh
      run-review.sh
      main.py
      html_charts.py
      requirements.txt
      assets/

  xtb-wealthfolio-export/
    SKILL.md
    references/
    scripts/
      setup-env.sh
      validate-export.sh
      export-wealthfolio.sh
      exporter.py
      main.py
      html_charts.py
      requirements.txt
      assets/
```

Do not require the root-level `main.py`, `exporter.py`, or `html_charts.py` for copied skill usage. Those root files are repository compatibility shims only.

## Verification Commands

From the repository root:

```bash
skills/xtb-portfolio-review/scripts/validate-review.sh
skills/xtb-wealthfolio-export/scripts/validate-export.sh
```

If the full repository test suite is available:

```bash
.venv/bin/python -m pytest -q
```

Successful validation means the Python dependencies are importable and the bundled skill tools can be loaded. A successful portfolio or export run is still the final check for a specific workbook.

## Operational Rules For Agents

- Prefer the bundled scripts inside the skill folder over re-implementing behavior.
- Keep generated files in the user's current working directory, usually under `results/`.
- Do not upload or expose XTB workbooks; they can contain personal financial data.
- Do not present portfolio output as investment advice. Report computed values, assumptions, and caveats.
- If dependencies are missing, propose running `scripts/setup-env.sh`.
- If package installation needs network access or elevated permissions, ask the user first.
- If a workbook path is ambiguous, ask the user which `.xlsx` file to use.
- If validation fails, report the failing command and the actionable error.

## Copy-Paste Installation Request

A user can paste this to another agent:

```text
Install the XTB agent skills from this repository. Read INSTALL_FOR_AGENTS.md, copy the needed folder from skills/ into your skill or instruction directory if your harness supports that, run the skill's setup and validation scripts, then use the relevant SKILL.md workflow for my XTB workbook.
```
