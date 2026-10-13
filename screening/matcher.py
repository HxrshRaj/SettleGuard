"""Name matching for sanctions / watchlist screening (pure Python, no dependencies).

Scoring is deliberately explainable: each candidate gets a score in [0, 1] and a list of the signals
that produced it, so an analyst can see why a hit was raised. Techniques used:
  * normalisation: case, accents, punctuation, honorifics and company suffixes removed
  * token-set Jaro-Winkler: word order does not matter ("Ivanov Sergei" == "Sergei Ivanov")
  * Soundex on tokens as a phonetic tiebreak ("Mohammed" / "Muhammad")
  * alias lists and an exact-match fast path
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_STOP = {"mr", "mrs", "ms", "dr", "shri", "sri", "the", "of", "and",
         "ltd", "limited", "llc", "inc", "corp", "co", "pvt", "private", "plc", "gmbh", "sa", "bv"}


def normalise(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [t for t in s.split() if t and t not in _STOP]


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(la, lb) // 2 - 1
    am, bm = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not bm[j] and b[j] == ca:
                am[i] = bm[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if am[i]:
            while not bm[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    t //= 2
    return (matches / la + matches / lb + (matches - t) / matches) / 3


def jaro_winkler(a: str, b: str, p: float = 0.1) -> float:
    j = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return j + prefix * p * (1 - j)


def soundex(w: str) -> str:
    if not w:
        return ""
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
             "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    out, last = w[0].upper(), codes.get(w[0], "")
    for ch in w[1:]:
        c = codes.get(ch, "")
        if c and c != last:
            out += c
        if ch not in "hw":
            last = c
    return (out + "000")[:4]


def token_set_score(a: list[str], b: list[str]) -> float:
    """Average best-match Jaro-Winkler of each token in the shorter name against the longer one."""
    if not a or not b:
        return 0.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    used: set[int] = set()
    total = 0.0
    for t in short:
        best, best_i = 0.0, -1
        for i, u in enumerate(long_):
            if i in used:
                continue
            s = jaro_winkler(t, u)
            if s > best:
                best, best_i = s, i
        if best_i >= 0:
            used.add(best_i)
        total += best
    coverage = len(short) / len(long_)  # penalise matching one word of a long name
    return (total / len(short)) * (0.75 + 0.25 * coverage)


@dataclass
class Match:
    entry_id: str
    list_name: str
    matched_name: str
    score: float
    signals: list[str] = field(default_factory=list)
    country: str | None = None
    entity_type: str | None = None


def score_against(query: str, entry: dict) -> Match | None:
    q = normalise(query)
    if not q:
        return None
    best_score, best_name, signals = 0.0, "", []
    for cand in [entry["name"], *entry.get("aliases", [])]:
        c = normalise(cand)
        sig = []
        if q == c or sorted(q) == sorted(c):
            s = 1.0
            sig.append("exact (order-insensitive)")
        else:
            s = token_set_score(q, c)
            sig.append(f"token JW {s:.2f}")
            if len(q) == len(c) and all(soundex(x) == soundex(y) for x, y in zip(sorted(q), sorted(c))):
                s = min(1.0, s + 0.05)
                sig.append("phonetic match")
        if s > best_score:
            best_score, best_name, signals = s, cand, sig
    return Match(entry["id"], entry["list"], best_name, round(best_score, 4), signals,
                 entry.get("country"), entry.get("type"))


def screen(query: str, watchlist: list[dict], threshold: float = 0.88, top: int = 5,
           country: str | None = None) -> list[Match]:
    out = []
    for e in watchlist:
        m = score_against(query, e)
        if not m or m.score < threshold:
            continue
        if country and e.get("country") and e["country"].upper() != country.upper():
            m.score = round(m.score * 0.97, 4)  # same name, different country: slightly less likely
            m.signals.append("country differs")
            if m.score < threshold:
                continue
        out.append(m)
    return sorted(out, key=lambda m: -m.score)[:top]
