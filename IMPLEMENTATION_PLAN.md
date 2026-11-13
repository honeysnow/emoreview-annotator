# Emotion Annotation & Review Tool — Implementation Plan

A local, single-user web app (Python) for annotating product-review sentences against the *Emotion Triplet Codebook* and reviewing two annotators' work. It reads and writes the same `.xlsx` layouts the team already uses.

The UI design lives in `design/` (4 screens: Home, Annotate, Review, Export). Treat those files as the visual and behavioural reference; this document is the engineering spec.

---

## 1. Goals and non-goals

**Goals**
1. Import raw `TRAIN_*.xlsx` files and let an annotator label every sentence per the codebook, with far fewer typos and misaligned fields than free-text spreadsheets.
2. Import two annotators' files and let a reviewer resolve disagreements, producing the *Comparison Sheet* layout.
3. Export both formats byte-compatible in structure with the examples (see §6).
4. Run entirely locally, no accounts, no cloud, no build step.

**Non-goals (v1):** multi-user sync, auth, model-assisted pre-annotation, inter-annotator statistics beyond exact-match agreement, more than 2 annotators per comparison.

---

## 2. Tech stack (recommended)

| Concern | Choice | Why |
|---|---|---|
| Server | **FastAPI** + Uvicorn | Small, typed, easy to test |
| Templates | **Jinja2** | Server-rendered pages, no SPA build |
| Interactivity | **Alpine.js** (vendored, no CDN) + a little vanilla JS | Click-to-tag tokens, tabs, toggles |
| Storage | **SQLite** via SQLAlchemy 2.x (or `sqlite3`) | Autosave, resume, no data loss on crash |
| Excel I/O | **openpyxl** | Needs formulas, sheet names, data validation |
| Config | `codebook.yaml` | Tags, definitions, arousal hints, separators |
| Tests | **pytest** | Round-trip tests against the real example files |
| Run | `uv run python -m annotool` (or `python -m annotool`) | One command, opens `http://127.0.0.1:8000` |

Streamlit/Gradio are not recommended: the core interaction (click words to fill a field) is awkward there.

---

## 3. Folder layout

Keep raw files in a folder. They are inputs and are never modified. The app copies what it needs into SQLite.

```
annotool/
├── pyproject.toml
├── README.md
├── codebook.yaml                 # 30 tags, 8 Section-2 tags, definitions, arousal levels, source options
├── design/                       # UI reference (the 4 wireframe screens)
├── data/                         # git-ignored
│   ├── raw/                      # DROP raw files here, read-only
│   │   └── TRAIN_1_clothing_shoes_and_jewelry_annotation_2.xlsx
│   ├── imports/                  # existing annotated / comparison files to load
│   │   ├── HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx
│   │   └── TRAIN_2_EmoReview40K_Comparison_Sheet_20250922_1.xlsx
│   ├── annotool.db               # SQLite (source of truth while working)
│   └── exports/                  # every export is written here, timestamped
├── src/annotool/
│   ├── __main__.py               # starts uvicorn, opens browser
│   ├── app.py                    # FastAPI app + routes
│   ├── db.py                     # engine, session, migrations
│   ├── models.py                 # tables (§4)
│   ├── codebook.py               # loads codebook.yaml
│   ├── serialize.py              # structured <-> ";;" "&&" "//" strings (§5)
│   ├── validate.py               # validation rules (§7)
│   ├── importers.py              # raw / annotated / comparison readers
│   ├── exporters.py              # annotation + comparison writers (§6)
│   ├── review.py                 # match / agreement / final logic (§8)
│   ├── templates/                # home, annotate, review, export (+ partials)
│   └── static/                   # alpine.min.js, app.css, app.js
└── tests/
    ├── fixtures/                 # small copies of the real example files
    └── test_*.py
```

A "Choose files" button on Home should also allow selecting a file anywhere; the app copies it into `data/raw/` or `data/imports/`. There is **no folder watcher**: new files in `data/raw/` and `data/imports/` are picked up only when the user clicks **Rescan** on Home (decision 8, §13).

---

## 4. Data model (SQLite)

Store **structured** data. Strings with `;;` / `&&` / `//` are generated only at export time.

```
batch            id, name (e.g. "TRAIN_1_clothing_shoes_and_jewelry"), kind ("annotate"|"review"),
                 sheet_name, source_file, created_at
sentence         id, batch_id, document_id ("DCS0002"), sent_id ("CS000026"), position, text
annotator        id, code ("HZY", "OYC", "TJH"), display_name
annotation       id, sentence_id, annotator_id, status ("todo"|"none"|"done"), arousal (int|null),
                 source ("self"|"other person"|"no one"|null), polarity_json (["negative","positive"]),
                 updated_at                      -- UNIQUE(sentence_id, annotator_id)
emotion          id, annotation_id, ord (0 = primary), tag
span             id, emotion_id, slot ("cue"|"obj"|"act"|"exp"), ord, group_no, start_tok, end_tok
review_decision  id, sentence_id, field, final_value, resolved (bool), note, reviewer, updated_at
raw_cell         annotation_id, field, text         -- verbatim legacy strings kept from imports (§5.3)
```

Notes:
- **Spans are stored as token ranges** (`start_tok`, `end_tok`) over `sentence.text.split(" ")`. The text is already tokenised with single spaces, so a token index is exact. Spans are reconstructed to text with `" ".join(tokens[start:end+1])`, which satisfies the codebook rule "each word separated by a space".
- `group_no` links an action to its object when one emotion has several (§5.2).
- `status` is derived: no polarity → `todo`; polarity `none` → `none`; otherwise `done`.

---

## 5. Serialization (the part that must be exactly right)

### 5.1 Export strings (from codebook Q1–Q7 and the example files)

Per sentence, with emotions `e1 … en` (primary first):

| Column | Rule | Example |
|---|---|---|
| Polarity | polarities joined by `;; ` in selection order; `none` alone if no emotion | `negative;; positive` |
| Arousal | integer 1–5, blank if polarity `none` | `3` |
| Emotion Tag | tags joined by `;; ` | `disappointment;; happiness` |
| Emotion Cues | `tag: cue1, cue2` per emotion, joined by `;; ` | `disappointment: does not keep me warm;; happiness: looks nice` |
| Emotion Source | `self` / `other person` / `no one` | `self` |
| Emotion Cause Object | per emotion: objects joined by ` && `, or `none`; emotions joined by `;; ` | `it;; it` |
| Emotion Cause Action | per emotion: see §5.2, or `none` | `does not keep me warm;; looks nice` |
| Emotion Experiencer | per emotion: experiencers joined by ` && `, or `none` | `me;; me` |

Join separators: the examples use `;; ` (semicolons then a space). Use that exact form on export; on import, split on `;;` and trim.

### 5.2 Several objects and actions (codebook table, p. 13)

- `&&` separates multiple objects, or the action groups of different objects.
- `//` is used **only when one object has two or more actions** (decision 2). A single action never gets `//`.
- Combined: `action_1.1 // action_1.2 && action_2.1 // action_2.2`.

Data model support: each object span has a `group_no`; action spans carry the `group_no` of the object they belong to. Serializer: group actions by `group_no`, join within a group with ` // `, join groups with ` && `.
UI support: when an emotion has ≥2 object spans, show a small "belongs to object ▾" selector on each action chip (default: the object nearest to the left). With one object, no selector is shown.

### 5.3 Importing legacy annotation files

Parsing free text is lossy. The example file contains typos (`suprise`, `posiitve`, `sellf`), trailing spaces, cue/tag counts that don't match, and cue text that isn't in the sentence (e.g. `look nice` vs `looks nice`).

Import algorithm per cell: `split(";;")` → trim → map to structure. For each span string, search it as a whole-token sequence in the sentence.
- Found → store as a token range.
- Not found → store verbatim in `raw_cell` and mark the field **needs attention** (amber dot in the UI, shown in the Annotate checklist).
- Unknown tag / polarity / source → map by a typo table + `difflib.get_close_matches` (cutoff 0.8) and record the correction in an import report; otherwise flag.
- Segment count mismatch across columns (≈152 rows in the example) → flag; do not guess.
- Cause object / action / experiencer values attached to an emotion **outside the 8 Section-2 tags** (e.g. `relaxed`, `love`, `doubt`) → kept verbatim in `raw_cell`, flagged "outside Section 2", and **not exported** (the export writes `none`). The import report lists how many rows this affects so nothing disappears unnoticed.

Produce an **import report** (CSV + on-screen summary): rows imported, auto-corrected values, flagged rows. Never silently change data.

---

## 6. Export formats

All exports go to `data/exports/` and are also offered as a browser download. Include a `.csv` option (same columns).

### 6.1 Annotation export: `{ANNOTATOR}_{batch}.xlsx`

Example: `HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx`
- One sheet. Name = first 31 chars of the batch (Excel limit), e.g. `clothing_shoes_and_jewelry_anno`.
- Row 1 headers, exactly: `DocumentID, SentID, Sentence, Polarity, Arousal, Emotion Tag, Emotion Cues, Emotion Source, Emotion Cause Object, Emotion Cause Action, Emotion Experiencer`.
- One row per sentence in original order. Rows with status `todo` keep blank annotation cells (option "Include sentences not yet annotated"). Status `none` writes `none` in Polarity and leaves the rest blank, as in the example.
- Arousal as an integer (the example has `3.0`; use `3`).

### 6.2 Comparison export: `TRAIN_2_EmoReview40K_Comparison_Sheet_{YYYYMMDD}.xlsx`

Layout of the example (verified):
- Sheet name like `TRAIN_2_cell_phones_accessories`. A second sheet `Sheet1` holds the 30-tag list (used for dropdowns): `happiness, sadness, anger, surprise, fear, disgust, disappointment, hate, admiration, amusement, boredom, confidence, curiosity, desperation, doubt, excitement, exhaustion, fascination, gratitude, hope, indifference, inspiration, jealousy, longing, love, pride, regret, relaxed, shame, sympathy`.
- **Row 1**: per field group, the agreement cell: `=COUNTIF(G3:G601,"-")/COUNTA(G3:G601)` (match column of that group). Optional (toggle "Include agreement % row").
- **Row 2**: headers. `DocumentID, SentID, Sentence`, then 8 groups × 4 columns: `[Field] ×(A, B, Final, "A×B")`. Group order: Polarity, Arousal, Emotion Tag, Emotion Cues, Emotion Source, Emotion Cause Object, Emotion Cause Action, Emotion Experiencer. Row-1 labels above each group are the annotator codes (`OYC`, `TJH`, `Final`).
- **Row 3+**: data.
  - Annotator A and B columns: values (blank when not annotated).
  - **Match** column: `=IF(EXACT(D3,E3),"-","X")` (formula) or the literal `-`/`X` when formulas are off.
  - **Final** column: if the reviewer set a value, write the **value**. Otherwise write the formula `=IF(D3=E3,IF(E3="","-",E3),"")`, which auto-fills when both agree and stays blank when they disagree. This matches the example, where resolved disagreements are overwritten with hand-typed values.
- Data validation: Emotion Source columns (`T3:U601` in the example) allow `self, other person, no one`. Keep it.
- Freeze panes beside the Sentence column; set column widths similar to the example (Sentence ≈ 47).
- **Cues carry the tag prefix in both formats** (decision 3): `happiness: durable`. This deliberately differs from the example comparison sheet, which has plain phrases. Keep it behind a setting `cue_prefix_with_tag` (default **on**) so the old layout can be reproduced. When importing the old comparison sheet, treat plain cues as belonging to the emotion tag(s) in the same row.

Both exporters must pass the round-trip tests in §10.

---

## 7. Validation rules (Annotate screen "Checks" panel + export warnings)

Hard (block "Save & next" only with a confirm, never block autosave):
1. Polarity present (`positive|negative|neutral|none`; mixed allowed; `none` is exclusive).
2. If polarity ≠ none: arousal 1–5, source set, ≥1 emotion.
3. Every emotion tag ∈ the 30 codebook tags (or an explicitly suggested new tag, flagged).
4. Every emotion has ≥1 cue.

Soft (warnings):
5. Tag duplicated within a sentence.
6. **Section 2 applicability** (decision 4): cause object, cause action and experiencer apply only to `happiness, sadness, anger, surprise, fear, disgust, disappointment, hate`. For any other tag these slots are locked in the UI and exported as `none`. For the 8 tags, if object and action are both empty, prompt "none?" (the codebook says to write `none` explicitly).
7. Cause action text not starting with a do/is/has/can/should/could/would/will form is **not** checked (codebook asks to include them; too fuzzy).
8. Column alignment is guaranteed by construction; no check needed.

Review-screen flags (per annotator, per sentence): tag present but cue empty/`none`; cue text not found in sentence; two values differ only by case/whitespace; one annotator left the sentence blank.

---

## 8. Review logic (`review.py`)

- Fields (order fixed): Polarity, Arousal, Emotion Tag, Emotion Cues, Emotion Source, Cause Object, Cause Action, Experiencer.
- Compare the **exported strings** of A and B (so results equal the spreadsheet): `match = (a == b)` exact, case and whitespace sensitive, blank equals blank. Cues are compared **with** the `tag:` prefix, so a different tag also makes the cue field differ.
- `diff_count`, `open_count = disagreements without a decision`.
- Row status: `agreed` (no diffs), `resolved`, `open`.
- Final value: agreed → equals both; disagreed → reviewer picks A, B, or types a value; the typed value is stored as-is.
- Agreement % per field = matches / total rows (same as the sheet formula `COUNTIF("-")/COUNTA`); overall = mean of the 8. Reference values from the example file (exact match, all 599 rows): Polarity 79.3, Arousal 76.5, Tag 76.1, Cues 46.7, Source 82.8, Object 71.1, Action 57.9, Experiencer 79.3 (mean ≈ 71%). Use these as a regression check for the importer + calculator.
- Word-level diff for display: tokens of A not present in B (and vice versa) are highlighted.
- **No free text in Final** (decision 7). A reviewer sets Final by **Use A**, **Use B**, or **Edit…**, which opens the same structured widgets as Annotate (tag chips, click-to-select spans, arousal and source chips) and serializes the result. Typed strings are never accepted.

---

## 9. Screens and behaviour

Reference visuals: `design/`.

### Home
- Two cards: **Annotate** (batch name, "N of M sentences", progress bar, *Continue* → resumes at last-edited sentence) and **Review** (A × B, rows annotated by both, mean agreement, lowest field, *Open review*).
- Import area (file picker + a **Rescan** button for the data folders; no automatic watching). Documents table with progress and status; each row opens that document.
- No user name or greeting needed; heading is just "Your tasks".

### Annotate
- Left: sentence list for the current document (status dot: todo / none / done; polarity pill).
- Centre: sentence as clickable **tokens**. One of four slots is *active* (Cues, Cause object, Cause action, Experiencer). **Spans are always contiguous** (decision 6): a click adds a one-word span (click again to remove), **Shift-click** extends the nearest span to cover every word in between, and dragging selects a range. A cue such as "not … impressed" is therefore annotated as the whole span from first to last word. Each span shows as a chip with × to remove.
- **Slot availability:** Cues are always available. Cause object, cause action and experiencer are enabled only when the active emotion's tag is one of the 8 Section-2 tags; otherwise the rows are dimmed with "Not applicable for <tag>" and clicks are ignored. Before a tag is chosen they show "Choose a tag first".
- Slot colours: cue yellow, object blue, action purple, experiencer green (highlight + underline so colour is not the only cue).
- Sentence-level controls: Polarity chips (multi-select, `none` exclusive and greys the rest), Arousal 1–5 with tooltips from the codebook, Source chips.
- Emotions: tabs (1 · tag, 2 · tag, + Add emotion). Tag picker = 30 chips; hovering shows the codebook definition in the right panel. "Suggest new tag…" opens a small text field and is flagged for review.
- Right panel: **Export preview** (the 8 output strings live), **Checks**, **Codebook** definition.
- Footer: Previous, "No emotion in this sentence", Save & next. Autosave on every change (debounced 300 ms) → `PUT /api/annotations/{id}`.
- Keyboard: `1–5` arousal, `←/→` sentence, `Enter` save & next, `Tab` through slots. (Note: the wireframe shows these as hints; implement them in app.js, scoped to the page.)

### Review
- Left: sentence queue with filters All / Disagreements / Unresolved and status pills (agreed / resolved / N open).
- Centre: sentence with "Highlight cues from OYC / TJH / Off". Comparison grid: Field | A | B | Final | Match. Agreeing rows are muted; disagreeing rows show word-level diff and **Use A / Use B / Edit…** buttons. Final is read-only text, never a free-text box. Match shows ✓ (exports `-`) or X.
- Footer: Previous, Next disagreement, "Use A for all open fields", Resolve & next.
- Right: flags for this sentence, reviewer note, agreement panel (per-field bars; fields under 60% in amber).

### Export
- Choose type (Annotations / Comparison sheet), rows (all / one document / completed only; or all / disagreements / unresolved), options (include un-annotated rows, warn on incomplete; or include formulas, agreement row, reviewer-note column). Live preview of the first rows with the real column headers. Buttons: Download .xlsx, Download .csv. The file is also saved under `data/exports/`.

---

## 10. API sketch

```
GET  /                                 Home
GET  /annotate/{batch}/{sent_id}       Annotate page
PUT  /api/annotations/{sentence}/{annotator}      body: structured annotation JSON
GET  /review/{batch}/{sent_id}         Review page
PUT  /api/review/{sentence}/{field}    body: {final_value, resolved, note}
POST /api/import/raw | /annotated | /comparison   multipart file (+ import report)
POST /api/export/annotations | /comparison         body: options → file
GET  /api/stats/agreement/{batch}
POST /api/rescan                       re-read data/raw and data/imports (button only)
GET/POST /api/annotators               list / add annotator codes (HZY, OYC, ...)
```

---

## 11. Testing

Use the real example files as fixtures (copy small slices into `tests/fixtures/`).
1. **Serializer round-trip**: parse each well-formed row from the HZY file → structure → serialize → equals the original string (normalising whitespace and `3.0`→`3`). Rows that fail go into an explicit expected-flagged list.
2. **Agreement regression**: importing the comparison workbook reproduces the 8 percentages in §8 (±0.1).
3. **Exporter structure**: re-open exported `.xlsx` with openpyxl; check headers, sheet names, formulas in row 1/Match/Final, data validation range, column count (11 and 35).
4. **Validation** unit tests per rule in §7.
5. **UI smoke** (Playwright): open Annotate, click tokens into each slot, assert the preview string; open Review, resolve a field, assert Final.

---

## 12. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M1 | Project skeleton, `codebook.yaml`, DB models, raw importer, Home page | Raw file imports; Home lists documents |
| M2 | `serialize.py` + legacy importer + import report | Round-trip tests pass on the HZY file |
| M3 | Annotate screen (tokens, slots, emotions, autosave, checks, preview) | Can annotate a document end to end |
| M4 | Annotation exporter | Exported file matches the HZY layout |
| M5 | Comparison importer + review logic + Review screen (incl. structured "Edit…" for Final) | Agreement numbers match §8 |
| M6 | Comparison exporter (formulas, validation, freeze panes) | Opens in Excel with formulas working |
| M7 | Polish: keyboard shortcuts, `//` object-action grouping, empty/error states, README | Walkthrough on a fresh machine |

---

## 13. Decisions and remaining items

### Decisions (resolved with the team)

| # | Topic | Decision |
|---|---|---|
| 1 | Number of tags | **30** tags. The codebook's "28" is a typo; update the codebook text. |
| 2 | `//` | Used only for **two or more actions tied to one object**. |
| 3 | Cue format | Cues always carry the tag: `tag: cue`, in **both** the annotation and the comparison export. |
| 4 | Cause / experiencer | Only for the **8 Section-2 emotions** (happiness, sadness, anger, surprise, fear, disgust, disappointment, hate). Other emotions: locked, exported as `none`. |
| 5 | Annotators | `HZY` and `OYC` to start; more can be added on a Settings page. Code is used in export filenames. |
| 6 | Cue spans | The **whole span** (first to last word) is the cue; spans are contiguous. |
| 7 | Reviewer Final | **No free text.** Use A, Use B, or the structured editor. |
| 8 | New files | **Manual Rescan** button only. |

### Consequences to keep in mind

- **Cue agreement will drop slightly vs the example sheet**, because cues now include the tag: if two annotators choose different tags, their cues differ too. The regression test (§11) should compute cue agreement with the prefix stripped when checking against the example workbook's 46.7%.
- **Legacy files lose some data on re-export**: values on non-Section-2 emotions are preserved in the database and the import report, but not written back out.
- **The structured Final editor adds work to M5** (it reuses the Annotate components, so build those first as reusable partials).
- **Wireframe**: the Annotate and Review screens in `design/` already reflect decisions 4, 6 and 7 (dimmed slots, Shift-click spans, read-only Final with Edit…).

### Still to confirm

- The 8 Section-2 tags match the first eight entries of the workbook's `Sheet1` list. Verified; no action needed.
- When two annotator files are paired for review, sentences are matched by `SentID`. If the files contain different sentences, the importer will list the unmatched rows rather than guess.

---

## 14. Handing this to Claude Code

1. Create the repo with the §3 layout; put this file at the root as `IMPLEMENTATION_PLAN.md`.
2. Put the wireframe screenshots/HTML in `design/` and the three example files in `tests/fixtures/` (and `data/imports/`).
3. Start Claude Code in the repo and say: *"Read IMPLEMENTATION_PLAN.md and the files in design/. Implement milestone M1 and M2, run the tests, then stop for review."* Continue milestone by milestone.
