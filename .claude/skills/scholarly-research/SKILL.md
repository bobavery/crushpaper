---
name: scholarly-research
description: Rigorous literature research and synthesis for academic writing in theology, biblical studies, philosophy, and adjacent humanities. Finds an author's journal articles and essays, locates book reviews in theological journals, traces reception and citations, compares a thinker with peers, and answers "what is distinctive about X's treatment of Y." Use this whenever the user asks for research, a literature review, a bibliography, reviews of a book, "who has responded to," "what's unique about," reception history, sources for a paper, sermon series, lecture, or dissertation chapter, or uploads a book (EPUB or PDF) and asks what is new in it, even if the word "research" never appears. Always starts with a network preflight, because cloud sessions can silently block page fetches and waste the whole search budget.
---

# Scholarly research

## Why this skill exists

Two failures recur when Claude does academic research in a cloud session. First, the environment's network policy may deny every page fetch while web search still works, so agents burn a 200-search budget on snippets and only discover afterward that nothing could be verified. Second, research agents that write their report only at the end lose everything when a run is interrupted. This skill front-loads the network check, makes every agent save as it goes, labels every claim by how it was verified, and gives the synthesis a shape that answers "what is distinctive" rather than "what is the doctrine."

## Step 0: preflight the network (first two minutes)

Run the bundled check before anything else:

```bash
bash <skill-path>/scripts/preflight_network.sh
```

Exit 0 means fetches work. Exit 2 means every host is blocked at the proxy (curl reports code 000, and the proxy status shows "403 to CONNECT"). Exit 1 means partial.

If blocked, tell the user once, in a few lines: name the hosts, say that web search still works but no page, PDF, or API can be opened, and that the permanent fix is theirs to make: open the cloud environment menu in the session title bar, choose Edit, set Network access to Full access (or add the needed domains), then start a new session. Do not ask whether to continue; continue in degraded mode (below) and say so.

If browser tools are present in the session (tool names beginning `mcp__Claude_Browser__`, `mcp__remote-devices__Claude_Browser__`, or `mcp__claude-in-chrome__`), pages can be read through the user's own browser on their machine, which is outside the container's policy. Load the matching skill (`built-in-browser` or `chrome-browser`) and use it for the fetch list.

## Step 1: frame the question before searching

Decide three things, and ask only if the request genuinely leaves them open:

1. **The question type.** "What is distinctive about X" is a different job from "give me a bibliography" or "how was this book received." The first needs primary reading plus comparators; the second needs breadth; the third needs reviews and citations.
2. **The primary texts in hand.** If the user uploaded books, convert them now and read them yourself. Do not delegate the core reading; the synthesis depends on it.
   - EPUB: `python3 <skill-path>/scripts/epub_to_text.py book.epub outdir/` (one text file per chapter plus `_FULL.txt`; the book's own subject index, if present, supplies print page numbers).
   - PDF: `python3 <skill-path>/scripts/pdf_to_text.py book.pdf out.txt --offset N` (inserts `=== PAGE n ===` markers; `--offset` maps PDF pages to printed pages).
3. **The deliverable.** Usually a memo (template in `references/memo-template.md`) plus the raw agent reports. Say what it will be.

Then split the search into lanes. The standard four for an author-and-book question:

- **A. The author's own shorter statements**: journal articles, essays in edited volumes, lecture series the book grew from, interviews. Concise treatments expose the distinctive claims better than the monograph does, because the author has to choose.
- **B. Reviews in academic journals**, then substantial web reviews. Reviewers say what is new and what is wrong.
- **C. Reception**: who cites the book, dissertations, conference sessions, comparisons by other scholars, awards, endorsements.
- **D. Comparators**: the two or three nearest treatments the author is measured against, so the residue can be isolated.

## Step 2: launch the lanes in parallel, in the background

Use the Agent tool (general-purpose, `run_in_background: true`), one agent per lane, all in one message. Build each prompt from `references/agent-brief.md`, filling in the question, the lane, the output file path, and what you already know (so the agent recognizes the distinctive moves when it sees them).

Three requirements in every brief, and the reasons for them:

- **Create the output file immediately and append after every three or four searches.** Interruptions kill background agents without warning; a report written only at the end is lost.
- **Label every claim** with the verification marks in `references/verification.md` ([F] fetched, [S] snippet, [M] background knowledge, UNVERIFIED). A paper cannot cite a snippet as if it were a page.
- **Budget searches.** The session's web-search allowance (about 200 calls) is shared by all agents; a relaunched agent inherits a smaller budget. Tell each agent its rough allowance and to prefer opening index pages (journal tables of contents, publisher pages, Google Scholar "cited by") over many small searches when fetch works.

If an agent dies, relaunch it with the same brief; the partial file survives and the new run should read it first.

## Step 3: read the primary texts while the agents run

For a "what is distinctive" question, follow `references/distinctiveness-method.md`. The short version:

1. **Compression test.** Read the author's shortest statements first (the systematic-theology chapter, the journal article, the lecture titles). Uniqueness is easiest to see where the author had the least room.
2. **Interlocutor map.** Count who the author cites and who they argue with: `python3 <skill-path>/scripts/term_counts.py text.txt "Calvin,Owen,Barth,Moltmann"`. The foils reveal the polemical shape of the project.
3. **Chronology.** Trace each thesis to its first appearance in the author's earlier work. A move built for prolegomena and later applied is "imported"; one that first appears in the treatment at hand is "native." Native moves are the candidates for "their own."
4. **Nearest neighbors.** Compare on the exact points of departure, not in general. Build a table.
5. **Vulnerabilities and absences.** What a reviewer will press, and what the author does not treat. Absences are distinctives too.

Keep quotations verbatim with locators as you read. When only an EPUB exists, take page numbers from the book's own subject index and say so.

## Step 4: synthesize

Write the memo from `references/memo-template.md`. Lead with the short answer. Keep what you verified from primary texts separate from what agents reported from the web, and keep the verification labels on the web material. End with a fetch list: every URL or PDF that could not be opened, so the user (or a later session with network access) can confirm quotations in minutes.

## Step 5: deliver

Save the memo and the raw agent reports in the scratchpad (or a folder the user names) and send them with SendUserFile. Send a draft of the memo as soon as the primary-text sections are done; the user can read while the agents finish. Offer a shareable page or a Word file in one line; do not produce them unasked.

## Degraded mode (fetch blocked)

What still works: web search snippets (indexed titles, abstracts, first sentences), package installs from PyPI (so `pip install pymupdf` works for PDF conversion), connectors the session has (Google Drive for PDFs the user drops in), and the user's own browser if browser tools are present.

What to do differently: cap each agent at about 50 searches and tell it why; instruct agents to quote only what appears verbatim in a snippet; mark everything [S] or UNVERIFIED; treat "not found by search" as "not surfaced," never as "not published"; put the fetch list at the top of the deliverable rather than the bottom; and tell the user once, plainly, that the web quotations need confirmation.

## Files

- `references/agent-brief.md`: the research-lane prompt template with placeholders.
- `references/verification.md`: labels, citation hygiene, interruption recovery.
- `references/distinctiveness-method.md`: the method for "what is unique about X."
- `references/venues-theology.md`: journals, review outlets, repositories, and lecture series for theology and adjacent fields, with which ones post free full text.
- `references/memo-template.md`: deliverable structure.
- `scripts/preflight_network.sh`, `scripts/epub_to_text.py`, `scripts/pdf_to_text.py`, `scripts/term_counts.py`.
