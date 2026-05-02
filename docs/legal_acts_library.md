# Legal Acts library

Drop the official PDFs of Malaysian acts here. The Q&A digression
(`ai/legal_qa.py`) and Step 6/7 planner consult them to cite specific
sections instead of relying on Claude's training-data knowledge.

## Required files (filenames matter — keep this exact convention)

| Filename | Source |
|---|---|
| `wills_act_1959.pdf` | https://lom.agc.gov.my/act-detail.php?language=BI&act=346 |
| `probate_and_administration_act_1959.pdf` | https://lom.agc.gov.my/act-detail.php?language=BI&act=97 |
| `distribution_act_1958.pdf` | https://lom.agc.gov.my/act-detail.php?language=BI&act=300 |
| `national_land_code_1965.pdf` | https://lom.agc.gov.my/act-detail.php?language=BI&act=828 |
| `strata_titles_act_1985.pdf` | https://lom.agc.gov.my/act-detail.php?language=BI&act=318 |

Add any other relevant acts as `<snake_case_title>.pdf` — the loader
picks them up automatically. The filename slug becomes the citation key
("wills_act_1959" → "Wills Act 1959").

## How retrieval works (until we add embeddings)

1. User asks a question in chat ("what is residuary estate?")
2. `services/legal_library.relevant_excerpts(question)` extracts keywords
   from the question, scans every PDF's paragraphs, and returns the top 3
   matching paragraphs per Act.
3. Those excerpts are injected into the Claude prompt in `ai/legal_qa.py`
   so the answer cites actual provisions instead of guessing.

This is keyword-based for now — fine for a few thousand pages of static
Acts. If/when we need broader retrieval, swap in an embedding index
(SQLite + sqlite-vec is the simplest path).
