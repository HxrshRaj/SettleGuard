"""Offline identifier validation. Aadhaar numbers are never stored or logged in full."""
from __future__ import annotations

import re

# Verhoeff checksum tables (dihedral group D5), the scheme Aadhaar uses for its last digit.
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(payload: str) -> int:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return _INV[c]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def validate_aadhaar(number: str) -> dict:
    digits = re.sub(r"[\s-]", "", number or "")
    if not re.fullmatch(r"\d{12}", digits):
        return {"valid": False, "reason": "must be 12 digits"}
    if digits[0] in "01":
        return {"valid": False, "reason": "cannot start with 0 or 1"}
    if len(set(digits)) == 1:
        return {"valid": False, "reason": "repeated digit"}
    if not verhoeff_valid(digits):
        return {"valid": False, "reason": "checksum failed"}
    return {"valid": True, "masked": "XXXX XXXX " + digits[-4:]}


_PAN_ENTITY = {"P": "individual", "C": "company", "H": "HUF", "F": "firm", "A": "AOP", "T": "trust",
               "B": "BOI", "L": "local authority", "J": "artificial juridical person", "G": "government"}


def validate_pan(pan: str) -> dict:
    p = (pan or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", p):
        return {"valid": False, "reason": "format must be AAAAA9999A"}
    if p[3] not in _PAN_ENTITY:
        return {"valid": False, "reason": "unknown holder type (4th character)"}
    return {"valid": True, "holder_type": _PAN_ENTITY[p[3]], "masked": p[:2] + "X" * 7 + p[-1]}


def validate_ifsc(ifsc: str) -> dict:
    i = (ifsc or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", i):
        return {"valid": False, "reason": "format must be 4 letters, a zero, then 6 alphanumerics"}
    return {"valid": True, "bank_code": i[:4], "branch_code": i[5:]}


_VPA = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,49}@[a-zA-Z][a-zA-Z0-9]{1,30}")


def validate_vpa(vpa: str) -> dict:
    v = (vpa or "").strip()
    if not _VPA.fullmatch(v):
        return {"valid": False, "reason": "expected name@handle"}
    return {"valid": True, "handle": v.split("@")[1].lower()}
