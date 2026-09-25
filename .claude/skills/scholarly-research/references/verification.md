# Verification labels, citation hygiene, and interruption recovery

## Labels

Every web-sourced claim in an agent report or a memo carries one of these:

- **[F]** Fetched: the full page or PDF was opened and the quotation was read there. Page numbers may be cited.
- **[S]** Snippet: existence and wording confirmed only by a search-engine snippet of the page. Quote only what the snippet showed, mark it "(snippet)", and give no page number.
- **[M]** Memory: from the model's background knowledge, not confirmed this session. A lead, not a citation.
- **UNVERIFIED**: neither fetched nor snippet-confirmed (for example a volume number inferred from a date).

Primary texts the user supplied and you read yourself need no label; cite them with locators (print page from the PDF, or from the book's subject index when only an EPUB exists, and say which).

## Why the labels matter

A paper that cites a snippet as a page will be caught by an examiner or a reviewer, and one such error discredits the rest. The labels let the user see at a glance what they can cite now and what needs a library visit. They also keep agents honest under a blocked network, when the temptation to round a snippet up to a quotation is strongest.

## Citation hygiene

- Quote verbatim; mark omissions with an ellipsis; never smooth a quotation.
- Record the URL and the date accessed for every web item.
- When two searches attribute the same sentence to different reviewers, record both attributions and flag the conflict rather than picking one.
- "Not found by web search" is reported as "not surfaced." Many theological journals (WTJ, CTJ, MAJT, PRJ, Presbyterion, SBET, Churchman, Pneuma, JPT) are only partly indexed online; ATLA or EBSCO is the authority.
- Distinguish works by the author from works about the author, and reviews of the target book from reviews of the author's other books.
- Endorsements and publisher copy are evidence of how the book presents itself, not of reception; label them as such.

## Interruption recovery

Background agents die silently when a session is interrupted. Defenses, in order of value:

1. Every agent creates its output file in its first minute and appends every three or four searches.
2. On relaunch, the new agent's brief says: read the partial file first, do not repeat its searches, continue from its gaps.
3. The parent checks the transcripts' modification times when in doubt: three files that stopped growing at the same second were killed together.
4. The parent writes the memo's primary-text sections before the agents finish, so an interruption never costs the core analysis.
