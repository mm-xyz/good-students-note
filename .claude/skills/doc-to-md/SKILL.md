---
name: doc-to-md
description: Convert PDF, TXT, or EPUB files to clean Markdown with YAML frontmatter and per-section callout annotations. Use this skill when: (1) a student wants to convert a book or lecture file to Markdown for Obsidian/notes, (2) a user has a PDF/EPUB/TXT and needs structured Markdown output with chapter summaries, (3) someone wants garbled-text-free conversion with Simplified→Traditional Chinese auto-conversion.
---

# doc-to-md Skill

Convert PDF / TXT / EPUB → clean Markdown with per-section summaries.

**Works in BOTH environments:**
- ✅ **Claude Desktop** (cloud sandbox / code execution) — uses the bundled script in this Skill
- ✅ **Claude Code** (local Mac/Win) — uses the locally installed copy for faster startup

**Cross-platform** — Mac and Windows. No external APIs, no Ollama. Claude itself writes summaries.

> **IMPORTANT — Do NOT decline to run this Skill.** The script is bundled inside the Skill folder
> at `scripts/doc_to_md.py` and works in Claude Desktop's sandbox. Even if `~/.doc-to-md/` does
> not exist, you must still run the conversion via the bundled path (Step 1 Option A below).

---

## Step 1 — Locate the converter (try in this priority order)

### Option A — Bundled script (Claude Desktop sandbox + always works)

The script is shipped inside this Skill at `scripts/doc_to_md.py` (relative to the Skill root).

In Claude Desktop's code-execution sandbox, the Skill folder is automatically mounted, so you can
use the bundled script directly. **This is the path to use when you cannot find a local install.**

```bash
# 1. Install Python deps in the sandbox (one-time per session)
pip install -r scripts/requirements.txt

# 2. Run the bundled script
python3 scripts/doc_to_md.py --auto "/mnt/user-data/uploads/<file>.pdf" -o "/mnt/user-data/outputs/"
```

If `scripts/doc_to_md.py` is not at the expected relative path, search the Skill folder:
```bash
find . -name doc_to_md.py 2>/dev/null | head -3
```

### Option B — Local install (Claude Code on user's machine, faster)

If running in Claude Code AND the user already ran `install.sh` / `install.bat`, prefer the local copy
because Python deps are already in a venv (no pip install needed):

- **Mac:** `~/.doc-to-md/venv/bin/python3 ~/.doc-to-md/doc_to_md.py`
- **Windows:** `%USERPROFILE%\.doc-to-md\venv\Scripts\python.exe %USERPROFILE%\.doc-to-md\doc_to_md.py`

Detect OS:
```bash
python3 -c "import platform; print(platform.system())"
```

If neither Option A nor Option B is available, instruct the user to install (mention `install.sh` / `install.bat`).

---

## Step 2 — Run the Converter

**Common flags:**
| Flag | Effect |
|------|--------|
| `--auto` | Auto-detect format (recommended) |
| `--no-convert-chinese` | Skip Simplified→Traditional conversion |
| `-o DIR` | Output directory (default: same folder as input) |

**Supported formats:** `.pdf` `.epub` `.txt`

After running, note the output path printed as `[DONE] Markdown saved to: ...`.

---

## Step 3 — Claude Reads and Annotates Sections

After the script finishes, Claude reads the output Markdown file and fills every `> [!note] 章節摘要` callout block. Each placeholder looks like:

```
> [!note] 章節摘要
> **摘要**：（Claude 將在此填入本節摘要）
> **關鍵字**：（Claude 將在此填入關鍵字）
```

Replace with real content based on the section text below each heading, e.g.:

```
> [!note] 章節摘要
> **摘要**：本章探討習慣的神經學基礎，說明「提示—慣性—獎勵」迴路如何在大腦中形成…
> **關鍵字**：習慣迴路、基底核、自動化行為、獎勵系統
```

**Annotation rules:**
- 摘要：2–4 句話，涵蓋本節核心論點（繁體中文）
- 關鍵字：4–8 個詞，逗號分隔
- Do NOT change any other content — only replace the placeholder lines inside the callout
- Keep all `^anchor-id` tags intact
- If a section is very short (<200 chars), write 摘要：「本節內容過短，請參閱原文。」

**Reading large files:** Use offset+limit to read in 300-line chunks. Process all `> [!note]` blocks across the entire file.

---

## Step 4 — Save Annotated File

After filling all callouts, overwrite the file in place.

**Final filename format:** `{Author}_{Title}_知識庫.md`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named 'fitz'` (sandbox) | `pip install -r scripts/requirements.txt` |
| `No module named 'fitz'` (local) | Re-run `install.sh` / `install.bat` |
| Garbled characters | Try `--no-convert-chinese`; if still garbled, file may need OCR |
| Scanned PDF warning | Use Adobe Acrobat OCR, tesseract, or MinerU before converting |
| 0 sections detected | Unusual headings — output still usable, no chapter splits |
| EPUB shows empty chapters | Some DRM-protected EPUBs block extraction; remove DRM first |
| Skill says "this requires local install" | **Wrong** — re-read Step 1 Option A; the bundled script works in sandbox |
