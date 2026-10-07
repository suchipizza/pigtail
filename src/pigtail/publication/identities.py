"""Person-name and handle minimization (PUB-006, PUB-007, PUB-008).

Deterministic role substitution only: "Jane Doe" -> "a co-founder", "posted by jdoe" -> "posted by a
Hacker News user". Text inside URLs is left alone because a URL is a source locator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

HANDLE_SURFACES = {
    "hacker_news": "a Hacker News user",
    "reddit": "a Reddit user",
    "github": "a GitHub user",
    "x": "an X user",
    "product_hunt": "a Product Hunt user",
}
TEAM_LABELS = {
    "the founder",
    "a founder",
    "a co-founder",
    "the maintainer",
    "the CEO",
    "the CTO",
    "the CPO",
    "an engineer",
    "a team lead",
    "a team member",
    "a marketing team member",
    "a support team member",
}
_ORG_WORDS = re.compile(r"\b(?:team|inc|llc|ltd|labs?|hq|staff|editors?|news|blog|press|company)\b", re.I)
_PERSONAL_NAME = re.compile(r"^[A-ZÀ-Þ][\w'’\-]+(?: [A-ZÀ-Þ][\w'’\-.]*){1,2}$")
_URL = re.compile(r"https?://\S+")
_ROLE_NOUN = (
    r"(?:[Cc]o-?founders?|[Ff]ounders?|[Mm]aintainers?|[Cc]reators?|CEO|CTO|CPO|[Pp]artners?|[Mm]arketers?"
    r"|[Ee]ngineers?|[Dd]evelopers?|[Dd]esigners?|[Ii]nvestors?|[Aa]dvisors?)"
)
_ADJECTIVES = {
    "technical",
    "marketing",
    "business",
    "solo",
    "fellow",
    "former",
    "current",
    "longtime",
    "lead",
    "sole",
    "original",
    "then",
    "other",
    "second",
    "first",
    "two",
    "both",
    "early",
    "founding",
}


_FOUNDER = re.compile(r"\bfounder\b", re.I)
_CO_FOUNDER = re.compile(r"\bco-?founder\b", re.I)


def is_sole_founder_role(role: str | None) -> bool:
    return bool(role and _FOUNDER.search(role) and not _CO_FOUNDER.search(role))


_EXECUTIVE = re.compile(
    r"\b(?:co-?founder|founder|ceo|cto|cpo|coo|cfo|cmo|president|chair(?:man|woman|person)?|head of"
    r"|general manager|vp|vice president|chief \w+ officer)\b",
    re.I,
)


def is_public_executive(role: str | None) -> bool:
    """Founders and executives acting in their company role are named (owner decision 2026-10-07).
    Everyone else (team members, contributors, maintainers of personal projects, investors…) is not."""
    return bool(role and _EXECUTIVE.search(role))


def role_label(role: str | None, n_founders: int) -> str:
    r = (role or "").lower()
    if _CO_FOUNDER.search(r):
        return "a co-founder"
    if _FOUNDER.search(r):
        return "the founder" if n_founders <= 1 else "a founder"
    if "contributor" in r or "community" in r:
        return "a community contributor" if "community" in r else "a contributor"
    for key, label in (("investor", "an investor"), ("advisor", "an advisor"), ("adviser", "an advisor")):
        if key in r:
            return label
    if "maintainer" in r:
        return "the maintainer"
    for key, label in (
        ("ceo", "the CEO"),
        ("cto", "the CTO"),
        ("cpo", "the CPO"),
        ("marketing", "a marketing team member"),
        ("support", "a support team member"),
        ("engineer", "an engineer"),
        ("developer", "an engineer"),
        ("contributor", "a contributor"),
        ("head of", "a team lead"),
        ("expert", "an outside expert"),
    ):
        if re.search(rf"\b{key}", r):
            return label
    if re.search(r"\b(?:team|employee|staff|manager|social media|growth|operations|founding)\b", r):
        return "a team member"
    return "someone"


# "former CTO of Porter" -> "Porter"
_ROLE_ORG = re.compile(r"\b(?:of|at)\s+([A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*)*)")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n\s*)$")


def _cap(label: str, m: re.Match[str]) -> str:
    """Capitalize a replacement that starts a sentence."""
    return label[0].upper() + label[1:] if _SENTENCE_START.search(m.string[: m.start()]) else label


_DETERMINERS = {"the", "a", "an", "its", "their", "his", "her", "our", "my", "your", "this", "that"}
_LOWER_STARTERS = {
    "as",
    "by",
    "from",
    "with",
    "and",
    "but",
    "when",
    "after",
    "before",
    "while",
    "since",
    "for",
    "to",
    "of",
}


def _role_with_article(m: re.Match[str]) -> str:
    """Keep the role noun; add an article unless a determiner or an owner name already precedes it."""
    noun = m.group(1)
    words = re.findall(r"[\w'’]+", m.string[: m.start()])
    prev = words[-1] if words else ""
    owned = (
        prev.lower() in _DETERMINERS | _ADJECTIVES
        or prev.endswith(("'s", "’s"))
        or (
            prev[:1].isupper()
            and prev.lower() not in _LOWER_STARTERS
            and not _SENTENCE_START.search(m.string[: m.start()])
        )
    )
    if owned:
        return noun
    lower = noun.lower()
    article = "a" if lower.startswith("co") and not lower.endswith("s") else "the"
    return _cap(f"{article} {noun if noun.isupper() else lower}", m)


_LABEL_NOUN = r"(?:maintainer|engineer|contributor|team member|team lead|co-founder|founder|CEO|CTO|CPO)"
# A role label produced by role_label(): "a marketing team member", "the maintainer", "a community contributor".
_LABEL_PHRASE = r"(?:(?:marketing |support |community |outside )?(?:" + _LABEL_NOUN[3:-1] + r"|expert))"
_ROLE_WORD = (
    r"(?:maintainer|engineer|developer|designer|contributor|manager|lead|head|director|marketer|writer|member"
    r"|hire|employee|co-founder|founder|CEO|CTO|CPO|COO|CFO|CMO|VP|advisor|specialist|officer|analyst)"
)


def _article(phrase: str) -> str:
    return "an" if re.match(r"(?:[aeiouAEIOU]|[FHLMNRSX](?:[A-Z]|$))", phrase) else "a"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Redactor:
    """Replaces known personal names and handles with role labels."""

    replacements: dict[str, str] = field(default_factory=dict)  # exact token -> label
    full_names: dict[str, str] = field(default_factory=dict)  # full person name -> label
    protected: set[str] = field(default_factory=set)
    kept: set[str] = field(default_factory=set)
    ambiguous: set[str] = field(default_factory=set)  # name parts shared by people with different roles
    founders: dict[str, dict] = field(default_factory=dict)  # named founder -> {names, orgs, sole}
    target_name: str = ""
    _rx: re.Pattern[str] | None = None
    _role_rx: list[tuple[re.Pattern[str], str]] = field(default_factory=list)

    @classmethod
    def from_bundle(cls, b: dict, keep_identities: set[str] | None = None) -> Redactor:
        keep = {k.strip().lower() for k in (keep_identities or set()) if k.strip()}
        t = b["target"]
        protected = {t["name"], *t["aliases"]}
        if t.get("domain"):
            protected.add(t["domain"])
            protected.add(t["domain"].split(".")[0])
        for r in b["repositories"]:
            protected.update({r["owner"], r["name"], f"{r['owner']}/{r['name']}"})
        protected_norm = {_norm(p) for p in protected if p}

        people = b.get("people", [])
        n_founders = sum(1 for p in people if is_sole_founder_role(p["role"]))
        token_labels: dict[str, set[str]] = {}
        full: dict[str, str] = {}
        kept: set[str] = set()
        person_keys: list[tuple[set[str], str]] = []
        founders: dict[str, dict] = {}
        for p in people:
            name = " ".join(p["name"].split())
            if not name or _norm(name) in protected_norm:
                continue
            parts = name.split(" ")
            if name.lower() in keep or is_public_executive(p["role"]):
                kept.add(name)
                if _FOUNDER.search(p["role"] or ""):
                    orgs = {o for o in _ROLE_ORG.findall(p["role"] or "") if _norm(o) not in protected_norm}
                    founders[name] = {
                        "names": {name, *(x for x in parts if len(x) >= 3)},
                        "orgs": orgs,
                        "sole": is_sole_founder_role(p["role"]),
                    }
                keys = {_norm(name)}
                if len(parts) >= 2:
                    keys |= {
                        _norm(parts[0] + parts[-1]),
                        _norm(parts[0][0] + parts[-1]),
                        _norm(parts[-1] + parts[0][0]),
                    }
                    for tok in (parts[0], parts[-1]):
                        if len(tok) >= 3 and tok[0].isupper() and _norm(tok) not in protected_norm:
                            token_labels.setdefault(tok, set()).add(f"KEPT:{name}")
                person_keys.append((keys, f"KEPT:{name}"))
                continue
            label = role_label(p["role"], n_founders)
            full[name] = label
            keys = {_norm(name)}
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                keys |= {_norm(first + last), _norm(first[0] + last), _norm(last + first[0])}
                for tok in (first, last):
                    if len(tok) >= 3 and tok[0].isupper() and _norm(tok) not in protected_norm:
                        token_labels.setdefault(tok, set()).add(label)
                        keys.add(_norm(tok))
            person_keys.append((keys, label))

        replacements: dict[str, str] = dict(full)
        ambiguous: set[str] = set()
        for tok, labels in token_labels.items():
            if tok in replacements or tok in kept:
                continue
            if all(lb.startswith("KEPT:") for lb in labels) and len(labels) == 1:
                continue  # a named person's first or last name
            if len(labels) == 1:
                replacements[tok] = next(iter(labels))
            else:
                # Shared by people with different roles ("Vohra"): never guess which one is meant.
                ambiguous.add(tok)

        # Source authors: handles on community surfaces; personal names on the web.
        for s in b["sources"]:
            author = (s.get("author") or "").strip()
            if not author or author in replacements or _norm(author) in protected_norm:
                continue
            if author.lower() in keep:
                kept.add(author)
                continue
            match = next((label for keys, label in person_keys if _norm(author) in keys), None)
            if match and match.startswith("KEPT:"):
                name = match[5:]
                # A named person's account handle becomes their name; their name or first name stays as is.
                if s["surface_key"] in HANDLE_SURFACES and " " not in author and author not in name:
                    replacements[author] = name
                continue
            if s["surface_key"] in HANDLE_SURFACES:
                replacements[author] = match or HANDLE_SURFACES[s["surface_key"]]
            elif match:
                replacements[author] = match
            elif _PERSONAL_NAME.match(author) and not _ORG_WORDS.search(author):
                replacements[author] = "an author"
        replacements = {k: v for k, v in replacements.items() if len(k) >= 3}

        red = cls(
            replacements=replacements,
            full_names=full,
            protected=protected,
            kept=kept,
            ambiguous=ambiguous,
            founders=founders,
            target_name=t["name"],
        )
        red._compile()
        return red

    def _compile(self) -> None:
        if not self.replacements:
            self._rx = None
            return
        alts = "|".join(re.escape(k) for k in sorted(self.replacements, key=len, reverse=True))
        self._rx = re.compile(rf"(?<![\w@/])@?({alts})(?![\w])")
        names = "|".join(re.escape(k) for k in sorted(self.full_names, key=len, reverse=True))
        self._role_rx = []
        if names:
            # "founders Jane Doe and John Roe" -> "founders"
            self._role_rx.append(
                (
                    re.compile(rf"\b({_ROLE_NOUN})(,?)\s+(?:{alts})(?:\s*(?:,|and|&)\s*(?:{alts}))*(?![\w])(?(2),?)"),
                    "__ROLE_ONLY__",
                )
            )
            # "Jane Doe, co-founder of X," -> "a co-founder of X"
            self._role_rx.append(
                (
                    re.compile(rf"(?<![\w])(?:{names}),\s+(?:the\s+|a\s+)?({_ROLE_NOUN}(?: of [A-Z][\w.\-]*)?),?"),
                    "__ROLE__",
                )
            )
        # "community member Chandra" -> the person's role label
        self._role_rx.append(
            (
                re.compile(
                    rf"\b(?:[Cc]ommunity (?:member|contributor)|[Cc]ontributor|[Uu]ser)\s+@?(?P<tok>{alts})(?![\w])"
                ),
                "__LABEL__",
            )
        )
        # "co-founder abelanger's posts" -> "the co-founder's posts" (any known name, first name or handle)
        self._role_rx.append((re.compile(rf"\b({_ROLE_NOUN})\s+@?(?:{alts})(?![\w])"), "__ROLE_ONLY__"))

    def _redact_plain(self, text: str) -> str:
        for rx, repl in self._role_rx:
            if repl == "__ROLE_ONLY__":
                text = rx.sub(_role_with_article, text)
            elif repl == "__LABEL__":
                text = rx.sub(lambda m: _cap(self.replacements.get(m.group("tok"), "someone"), m), text)
            elif repl == "__ROLE__":

                def role_sub(m: re.Match[str]) -> str:
                    noun = m.group(1)
                    lower = noun.lower()
                    article = "a" if lower.startswith("co") or lower.endswith("s") else "the"
                    return _cap(f"{article} {noun}", m)

                text = rx.sub(role_sub, text)
            else:
                text = rx.sub(repl, text)
        text = self._sub_tokens(text)
        # "Postgres expert an outside expert" -> "An outside Postgres expert"
        text = re.sub(
            r"\b((?:[A-Z][\w-]+ )?)expert an outside expert\b",
            lambda m: _cap(f"an outside {m.group(1)}expert", m),
            text,
        )
        # "a co-founder and a co-founder" -> "the co-founders"
        text = re.sub(
            r"\b([Aa]|[Tt]he) (co-founder|founder|team member|engineer|contributor) and (?:a|the) \2\b",
            lambda m: ("The" if m.group(1)[0].isupper() else "the") + f" {m.group(2)}s",
            text,
        )
        # "A community contributor, a community contributor, did" -> "A community contributor did"
        text = re.sub(r"\b((?:[Aa]n?|[Tt]he) ((?:[\w-]+ )?[\w-]+)), (?:an?|the) \2,?", r"\1", text)
        # A role label followed by the role it was hired into says the role twice:
        # "Hatchet introduced the maintainer as its OSS maintainer" -> "Hatchet introduced an OSS maintainer"
        # "hiring a marketing team member as marketing manager" -> "hiring a marketing manager"
        text = re.sub(
            rf"\b(introduced|introducing|hired|hiring|hires?|named|appointed|welcomed|added|adding|announced)"
            rf" (?:the|an?) {_LABEL_PHRASE} as (?:its|their|our|his|her|an?|the)?\s*([^.,;]*?\b{_ROLE_WORD})\b",
            lambda m: f"{m.group(1)} {_article(m.group(2))} {m.group(2)}",
            text,
        )
        # "The maintainer introduced as OSS maintainer" -> "OSS maintainer introduced"
        text = re.sub(
            rf"(?:(?<=^)|(?<=[.!?] ))(?:[Tt]he|[Aa]n?) {_LABEL_PHRASE} (introduced|hired|named|appointed|joined|added)"
            rf" as (?:its |their |an? |the )?([^.,;]*?\b{_ROLE_WORD})\b",
            lambda m: m.group(2)[0].upper() + m.group(2)[1:] + " " + m.group(1),
            text,
        )
        # "A co-founder joined Plausible as co-founder" -> "A co-founder joined Plausible"
        return re.sub(
            r"\b((?:[Aa]n?|[Tt]he) (co-founder|founder|maintainer|CEO|CTO)\b[^.;]*?) as (?:an? |the )?\2\b",
            r"\1",
            text,
        )

    def name_founders(self, text: str, context: str) -> str:
        """'the founder' / 'a Hatchet co-founder' -> the named founder, when it is clear which one.

        Founders are named in their company role (PUB-006). 'The founder' is the only sole founder, unless
        that person is already named in the text. Otherwise, if no founder is named in the text, a past
        employer in the text, or a name or employer in the cited evidence, must point to exactly one
        founder. In every other case nothing changes."""
        if not self.founders or not text:
            return text
        tn = re.escape(self.target_name) if self.target_name else "(?!)"
        rx = re.compile(
            r"(?<!\bas )(?<!\bbecame )(?<!\bbecome )(?<!\bis )(?<!\bwas )"
            rf"\b(?:[Tt]he|[Aa]n?) (?:(?i:{tn})(?:['’]s)? )?(co-?)?founder\b(?!s|['’]s? (?:co-?)?founders|\s+of\b)"
        )
        if not rx.search(text):
            return text

        def mentions(keys: set[str], hay: str) -> bool:
            return any(re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", hay) for k in keys)

        named = {n for n, f in self.founders.items() if mentions(f["names"], text)}
        everyone = list(self.founders)

        def pick(co: bool) -> str | None:
            if not co and len(everyone) > 1:
                sole = [n for n in everyone if self.founders[n]["sole"]]
                if len(sole) == 1:
                    return None if sole[0] in named else sole[0]
            if named:
                others = [n for n in everyone if n not in named]
                # "Filip and a co-founder", "with the co-founder": the other one of two founders
                if len(others) == 1 and len(everyone) == 2 and re.search(r"\b(?:and|with|&)\s*$", before[0]):
                    return others[0]
                return None  # another founder is named here; which one is meant is unclear
            if len(everyone) == 1:
                return everyone[0]
            for hay, key in ((text, "orgs"), (context, "names"), (context, "orgs")):
                hits = [n for n in everyone if mentions(self.founders[n][key], hay)]
                if len(hits) == 1:
                    return hits[0]
                if len(hits) > 1:
                    return None
            return None

        before = [""]

        def sub(m: re.Match[str]) -> str:
            before[0] = m.string[: m.start()]
            name = pick(bool(m.group(1)))
            if name is None:
                return m.group(0)
            if (
                name in m.string[: m.start()]
                or re.search(r"\b(?:and|with|&)\s*$", m.string[: m.start()])
                or (self.target_name and re.search(rf"\b{tn}\b", m.string[: m.start()], re.I))
            ):
                return name
            role = "founder" if self.founders[name]["sole"] else "co-founder"
            return _cap(f"{self.target_name} {role} {name}" if self.target_name else name, m)

        return rx.sub(sub, text)

    def _sub_tokens(self, text: str) -> str:
        if self._rx is not None:
            # "posted by jdoe" -> "posted": the surface is already named, the account adds nothing.
            surface_handles = [k for k, v in self.replacements.items() if v in HANDLE_SURFACES.values()]
            if surface_handles:
                alts = "|".join(re.escape(k) for k in sorted(surface_handles, key=len, reverse=True))
                text = re.sub(rf"\s+by\s+@?(?:{alts})(?![\w])", "", text)
            rx2 = self._rx
            text = rx2.sub(lambda m: _cap(self.replacements[m.group(1)], m), text)
        return text

    def redact(self, text: str | None) -> str | None:
        if not text or (self._rx is None and not self._role_rx):
            return text
        out: list[str] = []
        pos = 0
        for m in _URL.finditer(text):
            out.append(self._redact_plain(text[pos : m.start()]))
            out.append(m.group(0))
            pos = m.end()
        out.append(self._redact_plain(text[pos:]))
        return "".join(out)

    def residual(self, text: str | None) -> list[str]:
        """Names or handles still present (case-insensitive), ignoring URLs."""
        if not text:
            return []
        plain = _URL.sub(" ", text)
        for name in sorted(self.kept, key=len, reverse=True):  # "Rahul Vohra" is not an ambiguous "Vohra"
            if " " in name:
                plain = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", " ", plain, flags=re.I)
        found = []
        for k in [*self.replacements, *sorted(self.ambiguous)]:
            if re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", plain, re.I):
                found.append(k)
        return found

    def names_person(self, text: str | None) -> bool:
        """True if the text names a known Person (not only an account handle)."""
        person_labels = set(self.full_names.values())
        return any(k in self.ambiguous or self.replacements.get(k) in person_labels for k in self.residual(text))

    def mentions_any(self, text: str | None) -> bool:
        return bool(self.residual(text))


def neutral_source_label(source: dict, target_domain: str | None) -> str:
    """A neutral visible title for a source whose title names a person (PUB-008)."""
    surface = source.get("surface_key")
    stype = source.get("source_type") or ""
    if surface == "hacker_news":
        return "Hacker News discussion"
    if surface == "reddit":
        return "Reddit discussion"
    if surface == "github":
        return "GitHub page"
    if surface == "product_hunt":
        return "Product Hunt page"
    if surface == "x":
        return "Post on X"
    host = (urlparse(source.get("url") or "").hostname or "").removeprefix("www.")
    if stype == "founder_interview":
        return "Founder interview"
    if "podcast" in stype:
        return "Podcast episode"
    if target_domain and (host == target_domain or host.endswith("." + target_domain)):
        return "Company blog post"
    return f"Article on {host}" if host else "Article"
