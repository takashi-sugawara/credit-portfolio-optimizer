---
name: devlog
description: Summarize the current session's work and record it as a dated dev log entry under docs/devlog/. Use when the user asks to save, record, or write up today's work (e.g. "今日の作業を記録して", "devlogに残して"), or wraps up a weekend session and wants a durable summary of what happened.
---

# Devlog

Record a summary of the current session's work into
`docs/devlog/<YYYY-MM-DD>.md`, in this repository's established style
(see any existing file under `docs/devlog/` for a concrete example).

This project is a hobbyist weekend PoC (see `docs/ROADMAP.md`). The devlog
exists so that, weeks or months later, the user (or a future Claude Code
session) can reconstruct *why* a decision was made without re-reading the
whole chat — not just *what* changed (git log already covers that).

## Steps

1. **Determine today's date.** Use the date from the session context
   (`YYYY-MM-DD`). If a devlog for today already exists, read it first and
   plan to extend it with new numbered sections rather than overwriting or
   duplicating existing ones.

2. **Gather source material for this session:**
   - `git log --oneline` for commits made today, to anchor each section to
     a commit hash.
   - The conversation itself — especially decisions, investigations, and
     dead ends that *aren't* visible from the diff alone (e.g. "we
     considered X but rejected it because Y", root-cause analysis of an
     external incident, a judgment framework agreed on for a design
     choice). These are the most valuable parts to capture; a devlog that
     only restates commit messages isn't worth writing.

3. **Draft the entry** matching the existing structure:
   - Title: `# <YYYY-MM-DD> 開発ログ`
   - One `## N. <short title>` section per distinct piece of work, in
     chronological order, written in Japanese, using concise bullet points
     (not prose paragraphs).
   - Reference commit hashes inline where a section corresponds to a
     commit (e.g. `- コミット: \`239ee98\``).
   - When a section covers a decision or investigation (not just a code
     change), state the conclusion *and* the reasoning that led to it —
     future readers need the "why", not just the "what".
   - End with a `## 未完了・持ち越し` section listing anything left
     incomplete or deferred to a future session, so the next session can
     pick up context immediately.

4. **Show the draft to the user before writing**, or write it and show the
   diff — this is new content the user should confirm reflects what they
   consider worth keeping, not just what Claude thinks is notable.

5. **Do not commit or push automatically.** Creating/editing the file is
   fine to do directly; ask the user before running `git add` / `git
   commit` / `git push`, per this repo's normal workflow (they've always
   confirmed each of these explicitly in past sessions).
