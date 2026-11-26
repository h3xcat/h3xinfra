#!/usr/bin/env python3
"""ste_check.py - flag likely ASD-STE100 problems in Markdown prose.

Usage:
    python3 ste_check.py [options] FILE_OR_DIR [FILE_OR_DIR ...]

Options:
    --summary          Print only the counts for each file.
    --rules R1,R2      Report only these rules (names below).
    --glossary PATH    Glossary JSON (default: glossary.json next to this file).
    --glossary-md[=N]  Print the glossary as Markdown tables (one for each
                       group, under level-N headings, default 3), then stop.

The checker is a review aid, not a gate. It always exits with status 0.
A finding tells you where to look. It is not proof of an error. Fix the
text or ignore the finding. Python 3 standard library only.

What the checker reads
----------------------
The checker removes these parts before it reads the prose:
  * YAML front matter, fenced code blocks (``` and ~~~), HTML comments.
  * Heading lines (ATX "# ..." and setext). Headings are frozen text.
  * Reference link definitions ("[id]: https://...") and table separator
    rows.
  * Lines between a "<!-- ste-check off -->" line and a
    "<!-- ste-check on -->" line. Each marker must be alone on its line.
It replaces these parts with one placeholder word:
  * an inline code span            -> CODE   (counts as 1 word)
  * a bare URL or an <autolink>    -> URL    (counts as 1 word)
  * a double-quoted string "..."   -> QUOTE  (counts as 1 word; quoted
    output and quoted names are frozen text)
It keeps only the text of a link: [text](target) -> text.
It removes emphasis markers (** __ * _) and HTML tags.

Units and sentences
-------------------
A unit is one paragraph, one list item (with its continuation lines), one
table cell, or one block-quote paragraph. The checker splits a unit into
sentences after ".", "!" or "?" when a space follows. It does not split
after the abbreviations in ABBREVIATIONS or after a single capital letter
("J. Smith"). A colon or a semicolon does not end a sentence.

Word count
----------
A word is a whitespace-separated token that has a letter or a digit.
CODE, URL and QUOTE count as one word each. A hyphenated or slash-joined
token ("read-only", "IPv4/IPv6") counts as one word.

Rules
-----
long-desc   A description with more than 25 words.
long-instr  An instruction with more than 20 words. A sentence is an
            instruction when, after an optional label of up to three
            words that ends with a colon ("Note:", "Step 2:"), an
            optional adverb (Then, Also, First ...) and an optional lead-in
            clause that ends with a comma ("If X, " / "Before you X, " /
            "To X, "), it starts with a verb from IMPERATIVE_VERBS, with
            "Do not" or "Don't", or with "Never" or "Always" and a verb.
            A verb that "is/are/can/must/..." follows is a noun ("Update
            failures are ..."), so the sentence is a description.
ing         A word that ends in "-ing" and is not in ING_ALLOW. These are
            not flagged: a capitalized -ing word that is not the first
            word of the sentence (a name: "Running", "Pending"), a CamelCase
            or all-caps word, a word with "_" (an identifier), a phrase in
            ING_PHRASES ("rolling update", "sealing key"), and text in CODE,
            URL or QUOTE.
passive     A form of "be" or "get", with up to two adverbs, and then a
            past participle (a regular "-ed" word or a word in
            IRREGULAR_PARTICIPLES). "is/are/am" + a word in
            STATE_ADJECTIVES ("is enabled", "are mounted") is a state, not a
            passive, unless "by" follows. "was/were/be/been/being" + a
            participle is always flagged. "gone" and "un...ed" words
            ("unchanged", "unused") are adjectives, not participles. A word
            in ATTRIBUTIVE before a noun is an adjective ("are dated
            records"). Known false positives: "are fixed strings", "are
            escaped text" (an adjective that is not in ATTRIBUTIVE).
tense       A progressive form ("is running", "was failing") or a perfect
            form ("has been", "have removed the ..."). The checker flags a
            perfect form when the participle is "been", "gone", "done",
            "seen", "made" or "had", or when a determiner, a pronoun, a
            preposition, an adverb or punctuation follows the participle.
            Thus "has limited memory" (an adjective) is not flagged.
            "is" + a word in ING_ADJECTIVES ("is missing") is not a
            progressive form; the "ing" rule flags the word.
glossary    A word or a phrase from the "avoid" list of a glossary entry, or
            an "avoid_regex" match. A lowercase term also matches its
            capitalized form, but only at the start of a sentence. A term
            with a capital letter matches only that exact form. The
            "avoid_in_context" words are for a human review only.
multi       More than one instruction in one sentence: an instruction that
            contains (after its first verb) ", then", "; then", "then
            <verb>", "; <verb>", "and/but do not", or "and/but <verb>
            <determiner>" where <verb> is in IMPERATIVE_VERBS. "or" between
            two verbs gives a choice, not a second instruction. Two verbs
            side by side ("Stop and start the node") are one action.

Tuning notes
------------
Keep false positives low. The word lists below are the place to tune.
Add a word to ING_ALLOW only when it is never a verb form in these
documents, or when it is a technical name (STE allows a technical name
that ends in -ing, for example "networking" or "logging").
"""

import json
import os
import re
import sys

# ---------------------------------------------------------------- word lists

IMPERATIVE_VERBS = set("""
abort accept add adjust allow apply approve archive ask assign attach avoid
back block bump build call cancel change check choose clean clear click clone
close collect commit compare configure confirm connect contact copy cordon
correct create decide decrypt define delete deploy describe detach disable do
document download drain drop edit enable encrypt ensure enter examine exclude
expand export extract fetch fill find fix follow force generate get give go
grant have ignore import include increase inspect install keep kill label let
limit link list load look make mark merge migrate monitor mount move name note
obey open paste patch pause pick pin plan point prefer prepare press print
protect prune pull push put read reboot rebuild record recover reduce refer
register reinstall reject reload remove rename render repair repeat replace
report request rerun reset resolve restart restore resume retry return review
revoke rotate run save scale scan seal see select send set show skip sign start
stop store submit switch sync tag take tell test track treat trigger turn type
uncordon uninstall unseal update upgrade upload use validate verify wait watch
write
""".split())

LEAD_ADVERBS = set("""
then also first next finally now only optionally again instead otherwise
second third last afterwards
""".split())

LEAD_CLAUSE_WORDS = set("""
if when before after to for in on where unless once until while from with
without outside inside at during whenever as
""".split())

NON_IMPERATIVE_NEXT = set("""
is are was were be been can could will would must may might should has have
had does did am
""".split())

# Words that end in "ing" but are not an -ing verb form, and technical names
# (STE allows a technical name that ends in -ing).
ING_ALLOW = set("""
thing things nothing something anything everything during string strings
spring springs ring rings king kings wing wings bring brings sing sting swing
cling fling sling wring ping pings ceiling ceilings morning mornings evening
evenings sibling siblings offspring lightning herring awning
setting settings warning warnings heading headings meaning meanings
mapping mappings binding bindings padding spacing wiring
networking logging monitoring routing scheduling balancing forwarding tracing
provisioning fencing throttling hardening masquerading substring substrings
billing accounting engineering spoofing versioning timing docstring docstrings
finding findings
""".split())

# Technical phrases with an -ing word. The checker ignores these phrases.
ING_PHRASES = re.compile(
    r"\b(rolling (updates?|reboots?|restarts?)|recurring jobs?|"
    r"sealing[- ](keys?|certificates?)|streaming (replication|standbys?|"
    r"replicas?)|load[- ]balancing|port[- ]forwarding|operating systems?|"
    r"listening ports?)\b", re.I)

BE_FORMS = {"am", "is", "are", "was", "were", "be", "been", "being"}
BE_ALWAYS_PASSIVE = {"was", "were", "be", "been", "being"}
GET_FORMS = {"get", "gets", "got", "gotten"}
HAVE_FORMS = {"has", "have", "had"}

ADVERBS = set("""
not also only then still now already automatically explicitly never always
usually often all both just currently intentionally deliberately correctly
fully partly partially normally typically again first later once ever thus
therefore successfully manually directly properly silently actually so
""".split())

IRREGULAR_PARTICIPLES = set("""
arisen awoken been beaten become begun bent bound bitten blown broken brought
built burnt bought caught chosen cut dealt done drawn driven eaten fallen
fed felt fought found forbidden forgotten forgiven frozen given gone grown hung
heard hidden held hurt kept known laid led left lent lost made meant met
mistaken overridden overwritten paid proven put rebuilt rerun reset
rewritten ridden risen run said seen sought sold sent set shaken shown shut
sunk slid sped spent split spun stolen stuck struck sworn swept taken taught
torn told thought thrown understood undone upheld withdrawn withheld won worn
woken written
""".split())

# Words that end in "-ed" but are not past participles.
NOT_PARTICIPLE_ED = set("""
need needs bed red shed speed feed seed embed hundred sacred naked wicked rugged
ragged kindred wretched crooked shred bred breed weed reed steed creed greed
freed indeed exceed proceed succeed
""".split())

# "is/are/am" + one of these is a state, not a passive (STE allows a past
# participle as an adjective). "by" after the word still flags it.
STATE_ADJECTIVES = set("""
enabled disabled closed locked unlocked attached detached degraded connected
disconnected mounted unmounted encrypted cordoned uncordoned tainted pinned
signed expired deprecated archived installed uninstalled loaded unloaded
stopped done blocked broken paused suspended drained merged retired
unchanged unset empty frozen
""".split())

# A perfect participle that one of these follows is a verb form ("has
# removed the ..."), not an adjective ("has limited memory").
AFTER_VERB_WORDS = set("""
the a an this that these those its their his her our your my it them him us
me some any each every all no to in on at for with by from into onto out up
down off over back again also already not only then yet since code url quote
""".split())

# Participles that often stand before a noun as an adjective. "are dated
# records" is not a passive. These words are skipped when a word that is
# not a preposition, a conjunction or an adverb follows them.
ATTRIBUTIVE = set("""
dated structured desired planned named detailed nested combined mixed
advanced dedicated reserved shared maintained
""".split())

PASSIVE_FOLLOW = AFTER_VERB_WORDS | set("""
and or but as if when because so here there again too during until after
before unless while through under against across between per via without
within like
""".split())

DETERMINERS = set("""
the a an this that these those its their your each every all it them CODE
no one both some any code
""".split())

ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "approx.", "No.", "no.",
                 "Fig.", "cf.", "Inc.", "Ltd.", "Mr.", "Dr.", "St.", "a.m.",
                 "p.m.", "U.S.", "Corp.", "Jr.", "Sr.", "min.", "max.", "...")

PLACEHOLDERS = ("CODE", "URL", "QUOTE")

# -ing adjectives. The "ing" rule flags them; the "tense" rule ignores them
# after "is/are" ("is missing" is not a progressive tense).
ING_ADJECTIVES = set("""
missing pending existing outstanding ongoing remaining matching conflicting
corresponding misleading following leading underlying surrounding
""".split())

# ---------------------------------------------------------------- extraction

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
ATX_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(\[[ xX]\]\s+)?")
REFDEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
OFF_RE = re.compile(r"^[ \t]*<!--\s*ste-check\s+off\s*-->[ \t]*$.*?"
                    r"(^[ \t]*<!--\s*ste-check\s+on\s*-->[ \t]*$|\Z)", re.S | re.M)
COMMENT_RE = re.compile(r"(?<!`)<!--.*?-->", re.S)


def blank_keep_lines(m):
    return "\n" * m.group(0).count("\n")


def split_table_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code = [], "", False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "`":
            in_code = not in_code
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if ch == "|" and not in_code:
            cells.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur)
    return cells


def extract_units(text):
    """Return a list of (start_line, kind, [(line_no, text), ...])."""
    text = OFF_RE.sub(blank_keep_lines, text)
    text = COMMENT_RE.sub(blank_keep_lines, text)
    lines = text.split("\n")

    units = []
    cur = None  # [start_line, kind, parts]

    def flush():
        nonlocal cur
        if cur and any(p[1].strip() for p in cur[2]):
            units.append(tuple(cur))
        cur = None

    i = 0
    n = len(lines)
    if n and lines[0].strip() == "---":  # YAML front matter
        for j in range(1, n):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break
    fence = None
    while i < n:
        raw = lines[i]
        ln = i + 1
        m = FENCE_RE.match(raw)
        if fence:
            s = raw.strip()
            if m and s and set(s) == {fence[0]} and len(s) >= len(fence):
                fence = None
            i += 1
            continue
        if m:
            flush()
            fence = m.group(1)
            i += 1
            continue
        line = re.sub(r"^\s{0,3}(>\s?)+", "", raw) if re.match(r"^\s{0,3}>", raw) else raw
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        if ATX_RE.match(line):
            flush()
            i += 1
            continue
        if cur is None and i + 1 < n and SETEXT_RE.match(lines[i + 1]) \
                and not LIST_RE.match(line) and not stripped.startswith("|"):
            i += 2  # setext heading
            continue
        if HR_RE.match(line) or REFDEF_RE.match(line):
            flush()
            i += 1
            continue
        if stripped.startswith("|"):
            flush()
            if not TABLE_SEP_RE.match(stripped):
                for cell in split_table_row(stripped):
                    if cell.strip():
                        units.append((ln, "cell", [(ln, cell.strip())]))
            i += 1
            continue
        lm = LIST_RE.match(line)
        if lm:
            flush()
            cur = [ln, "item", [(ln, line[lm.end():])]]
            i += 1
            continue
        if re.match(r"^</?[a-zA-Z][^>]*>$", stripped):
            flush()  # an HTML block line such as <details>
            i += 1
            continue
        if cur is None:
            cur = [ln, "para", []]
        cur[2].append((ln, stripped))
        i += 1
    flush()
    return units


INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1", re.S)
IMG_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
REFLINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
AUTOLINK_RE = re.compile(r"<(https?://|mailto:)[^>\s]+>")
URL_RE = re.compile(r"\bhttps?://[^\s)>\]]+")
QUOTE_RE = re.compile(r"\"[^\"]{1,300}\"|\u201c[^\u201d]{1,300}\u201d")
TAG_RE = re.compile(r"</?[a-zA-Z][^>\n]*>")


def sub_keep(rx, repl, s):
    """Like rx.sub(repl, s), but keep the number of newlines."""
    def f(m):
        out = m.expand(repl)
        return out + "\n" * (m.group(0).count("\n") - out.count("\n"))
    return rx.sub(f, s)


def clean_inline(s):
    s = sub_keep(INLINE_CODE_RE, " CODE ", s)
    s = sub_keep(IMG_RE, r"\1", s)
    s = sub_keep(LINK_RE, r"\1", s)
    s = sub_keep(REFLINK_RE, r"\1", s)
    s = sub_keep(AUTOLINK_RE, " URL ", s)
    s = sub_keep(URL_RE, " URL ", s)
    s = sub_keep(QUOTE_RE, " QUOTE ", s)
    s = TAG_RE.sub(" ", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", r"\1", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ([,.;:!?])", r"\1", s)
    return s


def unit_text(parts):
    """Join the lines of a unit; return the text and (offset, line) marks.

    The cleaner works on the whole unit, so a code span or a link can wrap
    across lines. It keeps the number of lines, so each line keeps its
    source line number.
    """
    cleaned = clean_inline("\n".join(p.strip() for _ln, p in parts)).split("\n")
    text, marks = "", []
    for (ln, _p), line in zip(parts, cleaned):
        line = line.strip()
        if not line:
            continue
        if text:
            text += " "
        marks.append((len(text), ln))
        text += line
    return text, marks


def line_at(marks, off):
    ln = marks[0][1] if marks else 0
    for o, l in marks:
        if o > off:
            break
        ln = l
    return ln


SENT_END_RE = re.compile(r"([.!?])([\"')\]]*)\s+(?=\S)")


def split_sentences(text):
    out, start = [], 0
    for m in SENT_END_RE.finditer(text):
        end = m.end(2)
        before = text[start:end]
        if before.endswith(ABBREVIATIONS) or re.search(r"(^|\s)[A-Z]\.$", before):
            continue
        out.append((start, before.strip()))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append((start, tail))
    return out


def words_of(sentence):
    return [w for w in sentence.split() if re.search(r"[A-Za-z0-9]", w)]


def norm(w):
    return re.sub(r"^[^\w']+|[^\w']+$", "", w)


# ---------------------------------------------------------------- analysis

def instruction_start(tokens):
    """Return the index of the imperative verb, or -1."""
    low = [norm(t).lower() for t in tokens]
    i = 0
    for k in range(min(3, len(tokens))):  # a label: "Note:", "Step 2:"
        if tokens[k].endswith(":"):
            i = k + 1
            break
    while i < len(low) and low[i] in LEAD_ADVERBS:
        i += 1
    if i < len(low) and low[i] in LEAD_CLAUSE_WORDS:
        for k in range(i, min(len(tokens), i + 20)):
            if tokens[k].endswith(","):
                i = k + 1
                break
        else:
            return -1
        while i < len(low) and low[i] in LEAD_ADVERBS:
            i += 1
    if i >= len(low):
        return -1
    w = low[i]
    nxt = low[i + 1] if i + 1 < len(low) else ""
    if w == "don't" or (w == "do" and nxt == "not"):
        return i
    if w in ("never", "always") and nxt in IMPERATIVE_VERBS:
        return i
    if w in IMPERATIVE_VERBS and tokens[i][:1].isalpha():
        if "-" in tokens[i] or "/" in tokens[i]:
            return -1
        if nxt in NON_IMPERATIVE_NEXT or nxt.endswith("'s"):
            return -1
        if w == "do" and nxt not in DETERMINERS:
            return -1
        if i + 2 < len(low) and low[i + 2] in ("is", "are", "was", "were") \
                and nxt not in DETERMINERS:
            return -1  # "Update failures are ..."
        return i
    return -1


def is_participle(w):
    if w in IRREGULAR_PARTICIPLES:
        return True
    return len(w) > 3 and w.endswith("ed") and w not in NOT_PARTICIPLE_ED


def find_ing(sentence):
    tokens = words_of(ING_PHRASES.sub("X", sentence))
    hits = []
    for idx, raw in enumerate(tokens):
        w = norm(raw)
        if not w or w in PLACEHOLDERS or "_" in w:
            continue  # a placeholder or an identifier such as host_key_checking
        for part in re.split(r"[-/\u2013\u2014]", w):
            pl = part.lower()
            if len(pl) < 5 or not pl.endswith("ing") or pl in ING_ALLOW:
                continue
            if part.isupper() or any(c.isupper() for c in part[1:]):
                continue  # ALLCAPS or CamelCase name
            if part[:1].isupper() and idx > 0:
                continue  # a name in the middle of a sentence
            hits.append(part)
    return hits


def phrase(tokens, i, j):
    return " ".join(norm(t) for t in tokens[i:j + 1])


def find_passive_and_tense(tokens):
    low = [norm(t).lower() for t in tokens]
    passive, tense = [], []
    for i, w in enumerate(low):
        if w not in BE_FORMS and w not in GET_FORMS and w not in HAVE_FORMS:
            continue
        j, skipped = i + 1, 0
        while j < len(low) and skipped < 2 and (
                low[j] in ADVERBS or (low[j].endswith("ly") and len(low[j]) > 4)):
            j += 1
            skipped += 1
        if j >= len(low):
            continue
        nxt = low[j]
        if not nxt or "-" in tokens[j] or "/" in tokens[j] or nxt in ("code", "url", "quote"):
            continue
        after = low[j + 1] if j + 1 < len(low) else ""
        ends_clause = tokens[j].endswith((",", ".", ";", ":", ")"))
        if w in HAVE_FORMS:
            if nxt == "been" or (is_participle(nxt) and (
                    nxt in ("gone", "done", "seen", "made", "had")
                    or after in AFTER_VERB_WORDS or ends_clause or not after)):
                tense.append(phrase(tokens, i, j))
            continue
        if w in BE_FORMS and nxt.endswith("ing") and len(nxt) > 4 \
                and nxt not in ING_ALLOW and nxt not in ING_ADJECTIVES \
                and not norm(tokens[j])[:1].isupper():
            tense.append(phrase(tokens, i, j))
            continue
        if nxt == "gone" or (nxt.startswith("un") and nxt.endswith("ed")):
            continue  # adjectives: "is gone", "was unchanged", "are unused"
        if is_participle(nxt):
            p = phrase(tokens, i, j)
            if nxt in ATTRIBUTIVE and after and not ends_clause \
                    and after not in PASSIVE_FOLLOW and not after.endswith("ly"):
                continue  # "are dated records": an adjective before a noun
            if after == "by":
                passive.append(p + " by")
            elif w in GET_FORMS or w in BE_ALWAYS_PASSIVE or nxt not in STATE_ADJECTIVES:
                passive.append(p)
    return passive, tense


VERB_ALT = "|".join(sorted(IMPERATIVE_VERBS))


def find_multi(tokens, vi):
    if vi < 0:
        return []
    s = " ".join(tokens)
    low = [norm(t).lower() for t in tokens]
    hits = []
    rest = " ".join(tokens[vi + 1:])  # ignore a lead "Only then" / "Then"
    if re.search(r"[,;] then\b", rest) or re.search(r"\bthen (%s)\b" % VERB_ALT, rest):
        hits.append("then")
    for k in range(vi + 1, len(tokens) - 1):
        if tokens[k - 1].endswith(";") and low[k] in IMPERATIVE_VERBS:
            hits.append("; " + low[k])
        if low[k - 1] in ("and", "but"):
            if low[k] == "do" and low[k + 1] == "not":
                hits.append(low[k - 1] + " do not")
            elif low[k] in IMPERATIVE_VERBS and low[k] != "do" and k - 1 > vi + 1 \
                    and low[k + 1] in DETERMINERS and not tokens[k][:1].isupper():
                hits.append(low[k - 1] + " " + low[k])
    return hits


def term_regex(term):
    """Build the regex for one glossary term (see the module docstring)."""
    def body(t):
        return re.escape(t).replace(r"\ ", r"\s+")
    left, right = r"(?<![\w/.-])", r"(?![\w/-]|\.\w)"
    if term != term.lower():
        return re.compile(left + body(term) + right)
    cap = term[:1].upper() + term[1:]
    return re.compile(left + body(term) + right + "|" +
                      r"^[^\w]*" + body(cap) + right)


def load_glossary(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("ste_check: cannot read glossary %s: %s" % (path, e), file=sys.stderr)
        return [], {}
    rules = []
    for entry in data.get("terms", []):
        for bad in entry.get("avoid", []):
            rules.append((term_regex(bad), bad, entry["use"]))
        for rx in entry.get("avoid_regex", []):
            rules.append((re.compile(rx["re"]), rx["label"], entry["use"]))
    return rules, data


def glossary_md(data, level=3):
    """Return the glossary as Markdown: one table for each group."""
    out, group = [], None
    for e in data.get("terms", []):
        if e.get("group") != group:
            group = e.get("group")
            out += ["", "#" * level + " " + (group or "Terms"), "",
                    "| Use | Meaning | Do not use | Notes |",
                    "| --- | --- | --- | --- |"]
        bad = ", ".join(e.get("avoid", []) + [r["label"] for r in e.get("avoid_regex", [])])
        ctx = ", ".join(e.get("avoid_in_context", []))
        if ctx:
            bad = (bad + "; " if bad else "") + "in this meaning: " + ctx
        cells = ["**%s**" % e["use"], e["meaning"], bad or "-", e.get("note") or "-"]
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(out).strip("\n")


def check_file(path, glossary, enabled):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return [(0, "error", str(e), "")]
    findings = []
    for _start, _kind, parts in extract_units(text):
        utext, marks = unit_text(parts)
        if not utext:
            continue
        for off, sent in split_sentences(utext):
            tokens = words_of(sent)
            if not tokens:
                continue
            ln = line_at(marks, off)
            nwords = len(tokens)
            vi = instruction_start(tokens)
            short = sent if len(sent) <= 160 else sent[:157] + "..."
            if vi >= 0 and nwords > 20:
                findings.append((ln, "long-instr", "%d words" % nwords, short))
            elif vi < 0 and nwords > 25:
                findings.append((ln, "long-desc", "%d words" % nwords, short))
            for w in find_ing(sent):
                findings.append((ln, "ing", w, short))
            passive, tense = find_passive_and_tense(tokens)
            for p in passive:
                findings.append((ln, "passive", p, short))
            for t in tense:
                findings.append((ln, "tense", t, short))
            for m in find_multi(tokens, vi):
                findings.append((ln, "multi", m, short))
            for rx, bad, use in glossary:
                if rx.search(sent):
                    findings.append((ln, "glossary", "%s -> %s" % (bad, use), short))
    return [f for f in findings if f[1] in enabled or f[1] == "error"]


RULES = ["long-desc", "long-instr", "ing", "passive", "tense", "glossary", "multi"]


def expand(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules"))
                for fn in sorted(files):
                    if fn.endswith(".md"):
                        yield os.path.join(root, fn)
        else:
            yield p


def main(argv):
    summary = False
    rules = set(RULES)
    gpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.json")
    want_md = 0
    paths = []
    it = iter(argv)
    for a in it:
        if a == "--summary":
            summary = True
        elif a == "--rules":
            rules = set(next(it, "").split(","))
        elif a.startswith("--rules="):
            rules = set(a.split("=", 1)[1].split(","))
        elif a == "--glossary":
            gpath = next(it, gpath)
        elif a.startswith("--glossary="):
            gpath = a.split("=", 1)[1]
        elif a == "--glossary-md":
            want_md = 3
        elif a.startswith("--glossary-md="):
            want_md = int(a.split("=", 1)[1])
        elif a in ("-h", "--help"):
            print(__doc__)
            return
        else:
            paths.append(a)
    glossary, data = load_glossary(gpath) if ("glossary" in rules or want_md) else ([], {})
    if want_md:
        print(glossary_md(data, want_md))
        return
    if not paths:
        print(__doc__)
        return
    total = {r: 0 for r in RULES}
    nfiles = 0
    for path in expand(paths):
        nfiles += 1
        findings = check_file(path, glossary, rules)
        counts = {r: 0 for r in RULES}
        for f in findings:
            if f[1] in counts:
                counts[f[1]] += 1
                total[f[1]] += 1
        detail = " ".join("%s=%d" % (r, counts[r]) for r in RULES if r in rules)
        print("%s: %d findings (%s)" % (path, sum(counts.values()), detail))
        if not summary:
            for ln, rule, what, sent in findings:
                print("  L%d [%s] %s | %s" % (ln, rule, what, sent))
    if nfiles > 1:
        detail = " ".join("%s=%d" % (r, total[r]) for r in RULES if r in rules)
        print("TOTAL %d files: %d findings (%s)" % (nfiles, sum(total.values()), detail))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:  # a review aid must never fail the caller
        print("ste_check: internal error: %r" % (e,), file=sys.stderr)
    sys.exit(0)
