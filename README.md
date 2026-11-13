# emoreview-annotator

A local, single-user web app for annotating product-review sentences against the
*Emotion Triplet Codebook* and reviewing two annotators' work. It reads and writes the
same `.xlsx` layouts the team already uses. No accounts, no cloud, no build step.

`IMPLEMENTATION_PLAN.md` is the engineering spec; `design/` holds the UI wireframes.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies
uv run python -m annotool     # starts http://127.0.0.1:8000 and opens your browser
uv run pytest                 # run the tests
```

Options: `--port 8001`, `--no-browser`. To keep your data somewhere else, set
`ANNOTOOL_DATA_DIR=/path/to/folder` before starting.

## The data folder

Everything the app reads or writes lives under `data/` (git-ignored; created on first start).
Your input files are **never modified**.

| Folder | Purpose | Who writes it |
|---|---|---|
| `data/raw/` | Raw review files, e.g. `TRAIN_1_…xlsx` with columns `DocumentID, SentID, Sentence`. Each file becomes a batch to annotate. | you |
| `data/imports/` | Work that already exists: annotated files named `CODE_TRAIN_….xlsx` (e.g. `HZY_TRAIN_1_…`) and comparison sheets (file name contains `Comparison`). | you |
| `data/reports/` | One **import report** (CSV) per annotated or comparison import: what was auto-corrected (typos such as `suprise`), and which rows need a check. | the app |
| `data/exports/` | A timestamped copy of every file you download. | the app |
| `data/annotool.db` | SQLite database: the working copy of all sentences, annotations and review decisions. Autosaved on every change. | the app |

New files in `raw/` and `imports/` are picked up only when you click **Rescan** on the Tasks page
(there is no folder watcher). You can also use **Choose files**, which copies the file into the
right folder and imports it. Importing the same file twice is safe: it is recognised and skipped.

## Workflows

**Annotate.** Tasks → *Start/Continue*. Click words to fill the active slot (cues, cause object,
cause action, experiencer); shift-click extends a span, dragging selects a range. Everything saves
automatically. The right panel shows the exact export strings, the checks, and the codebook definition.
Imported rows that could not be mapped exactly show an amber dot and keep the annotator's original text
in *Imported data to check*.

**Switch user.** The avatar menu switches who you are working as. Each annotator's work stays separate.
*Rename…* is separate and explicit: it moves that annotator's data to a new code (the code is used in
export file names).

**Review two annotators.** Import both annotators' files, then use **Start a review** on the Tasks page
(or import a finished comparison sheet). In Review, set each disagreeing field with **Use A**, **Use B**
or **Edit…** (the structured editor); Final values are never typed. Reviews are snapshots: if an annotator
changes their work afterwards, remove the review and start it again.

**Export.** *Annotations* gives the 11-column `{CODE}_{batch}_annotation.xlsx`. *Comparison sheet* gives
`TRAIN_N_EmoReview40K_Comparison_Sheet_YYYYMMDD.xlsx` with live Match/Final formulas, agreement row,
the Emotion Source dropdown, and optional reviewer notes. Both can also be downloaded as `.csv`.

## Rules the app enforces (from the plan)

- 30 emotion tags; cause object, cause action and experiencer only for the 8 Section-2 tags
  (happiness, sadness, anger, surprise, fear, disgust, disappointment, hate). Other tags: locked, exported as `none`.
- Cues always carry the tag (`happiness: durable`); spans are contiguous.
- `;;` separates emotions, `&&` separates objects (or the action groups of different objects),
  `//` only separates several actions of **one** object. With two or more objects each action chip has a
  “→ object” selector (default: the nearest object to its left).
- Importing never guesses: values it cannot map exactly are kept verbatim and flagged.

## Keyboard shortcuts

Annotate: `1`–`5` arousal · `←` `→` previous/next · `Enter` save & next · `Tab` next slot · `?` help.
Review: `←` `→` previous/next · `n` next disagreement · `?` help. Shortcuts are off while typing in a text box.

## Troubleshooting

- **A batch shows up twice or you imported the wrong file** — use *Remove…* on its card (this deletes only the
  app's stored copy; your `.xlsx` files stay). Note that Rescan re-imports anything still in `raw/`/`imports/`.
- **“Not saved” in the header** — the page keeps your edits and retries; if the server was stopped, restart it
  and make any edit. The browser warns before you close a page with unsaved changes.
- **Port already in use** — start with `--port 8001`.
- **Where is my data?** — `data/annotool.db` plus the exports; back up the `data/` folder.

## Code layout

```
src/annotool/   app.py (routes) · serialize.py (strings ⇄ structure) · importers.py · exporters.py
                review.py · validate.py · store.py · models.py · db.py · codebook.py
                templates/ · static/ (plain JS, no build step)
codebook.yaml   30 tags, definitions, Section-2 tags, arousal hints
tests/          pytest against the real example files (tests/fixtures) + headless JS checks (needs Node)
```
