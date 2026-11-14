"""Checksum validators for national identifiers. A content filter can name one so that only structurally valid numbers match."""
import re
from datetime import date
from typing import Callable

from .pii import iban_ok, luhn as _luhn_digits, pesel_ok


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", s).upper()


def _weighted(d: str, weights, mod: int) -> int:
    return sum(int(c) * w for c, w in zip(d, weights)) % mod


def luhn_any(s: str) -> bool:
    """Luhn over any number of digits (SIREN, SIRET, personnummer, Partita IVA ...)."""
    d = _digits(s)
    if len(d) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def card(s: str) -> bool:
    return _luhn_digits(_digits(s))


def iban(s: str) -> bool:
    return iban_ok(s)


def pesel(s: str) -> bool:
    return pesel_ok(_digits(s))


def nip_pl(s: str) -> bool:
    d = _digits(s)
    return len(d) == 10 and _weighted(d[:9], (6, 5, 7, 2, 3, 4, 5, 6, 7), 11) == int(d[9]) and _weighted(d[:9], (6, 5, 7, 2, 3, 4, 5, 6, 7), 11) != 10


def regon(s: str) -> bool:
    d = _digits(s)
    if len(d) == 9:
        return _weighted(d[:8], (8, 9, 2, 3, 4, 5, 6, 7), 11) % 10 == int(d[8])
    if len(d) == 14:
        return regon(d[:9]) and _weighted(d[:13], (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8), 11) % 10 == int(d[13])
    return False


def pl_id_card(s: str) -> bool:
    a = _alnum(s)
    if not re.fullmatch(r"[A-Z]{3}\d{6}", a):
        return False
    vals = [ord(c) - 55 if c.isalpha() else int(c) for c in a]
    w = (7, 3, 1, 0, 7, 3, 1, 7, 3)
    return sum(v * x for v, x in zip(vals, w)) % 10 == vals[3]


def nhs(s: str) -> bool:
    d = _digits(s)
    if len(d) != 10:
        return False
    check = 11 - _weighted(d[:9], range(10, 1, -1), 11)
    check = 0 if check == 11 else check
    return check != 10 and check == int(d[9])


def de_taxid(s: str) -> bool:
    d = _digits(s)
    if len(d) != 11 or d[0] == "0":
        return False
    product = 10
    for c in d[:10]:
        total = (int(c) + product) % 10 or 10
        product = (2 * total) % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(d[10])


def _cz_month_ok(mm: int) -> bool:
    return 1 <= mm <= 12 or 21 <= mm <= 32 or 51 <= mm <= 62 or 71 <= mm <= 82


def cz_birth(s: str) -> bool:
    d = _digits(s)
    if len(d) not in (9, 10):
        return False
    yy, mm, dd = int(d[0:2]), int(d[2:4]), int(d[4:6])
    if not _cz_month_ok(mm) or not 1 <= dd <= 31:
        return False
    if len(d) == 9:
        return yy < 54          # nine digits were only issued up to 1953
    n = int(d)
    return n % 11 == 0 or (int(d[:9]) % 11 == 10 and d[9] == "0")


def cz_ico(s: str) -> bool:
    d = _digits(s)
    if len(d) != 8:
        return False
    rem = _weighted(d[:7], range(8, 1, -1), 11)
    check = 1 if rem == 0 else 0 if rem == 1 else 11 - rem
    return check == int(d[7])


_CF_ODD = {**{str(i): v for i, v in enumerate((1, 0, 5, 7, 9, 13, 15, 17, 19, 21))},
           **{chr(65 + i): v for i, v in enumerate((1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23))}}


def it_codice_fiscale(s: str) -> bool:
    c = _alnum(s)
    if not re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", c):
        return False
    total = 0
    for i, ch in enumerate(c[:15]):
        total += _CF_ODD[ch] if i % 2 == 0 else (int(ch) if ch.isdigit() else ord(ch) - 65)
    return chr(65 + total % 26) == c[15]


def it_piva(s: str) -> bool:
    d = _digits(s)
    return len(d) == 11 and luhn_any(d)


def fr_nir(s: str) -> bool:
    c = re.sub(r"\s", "", s).upper()
    m = re.fullmatch(r"([12]\d{2}(?:0[1-9]|1[0-2]|[2-9]\d)(?:\d{2}|2A|2B)\d{6})(\d{2})?", c)
    if not m:
        return False
    body, key = m.group(1), m.group(2)
    if key is None:
        return True    # no key given: only the structure can be checked
    n = int(body.replace("2A", "19").replace("2B", "18"))
    return 97 - n % 97 == int(key)


def fr_siren_siret(s: str) -> bool:
    d = _digits(s)
    return len(d) in (9, 14) and luhn_any(d)


def phone_intl(s: str) -> bool:
    d = _digits(s)
    d = d[2:] if s.strip().startswith("00") else d
    return 8 <= len(d) <= 15


def nl_bsn(s: str) -> bool:
    d = _digits(s)
    if len(d) == 8:
        d = "0" + d
    if len(d) != 9:
        return False
    total = sum(int(c) * w for c, w in zip(d, (9, 8, 7, 6, 5, 4, 3, 2, -1)))
    return total != 0 and total % 11 == 0


def se_pnr(s: str) -> bool:
    d = _digits(s)
    if len(d) == 12:
        d = d[2:]
    if len(d) != 10:
        return False
    mm, dd = int(d[2:4]), int(d[4:6])
    if dd > 60:
        dd -= 60                 # samordningsnummer (coordination number)
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    return luhn_any(d)


def se_orgnr(s: str) -> bool:
    d = _digits(s)
    return len(d) == 10 and int(d[2:4]) >= 20 and luhn_any(d)


def ua_ipn(s: str) -> bool:
    d = _digits(s)
    return len(d) == 10 and (sum(int(c) * w for c, w in zip(d[:9], (-1, 5, 7, 9, 4, 6, 10, 5, 7))) % 11) % 10 == int(d[9])


def aba(s: str) -> bool:
    d = _digits(s)
    return len(d) == 9 and sum(int(c) * w for c, w in zip(d, (3, 7, 1, 3, 7, 1, 3, 7, 1))) % 10 == 0


# name -> (function, label shown in the editor)
VALIDATORS: dict = {
    "card": (card, "Payment card (Luhn)"),
    "iban": (iban, "IBAN (mod 97)"),
    "pesel": (pesel, "PL PESEL"),
    "nip_pl": (nip_pl, "PL NIP"),
    "regon": (regon, "PL REGON"),
    "pl_id_card": (pl_id_card, "PL ID card"),
    "nhs": (nhs, "UK NHS number"),
    "de_taxid": (de_taxid, "DE Steuer-ID"),
    "cz_birth": (cz_birth, "CZ rodné číslo"),
    "cz_ico": (cz_ico, "CZ IČO"),
    "it_codice_fiscale": (it_codice_fiscale, "IT codice fiscale"),
    "it_piva": (it_piva, "IT partita IVA"),
    "fr_nir": (fr_nir, "FR NIR"),
    "fr_siren_siret": (fr_siren_siret, "FR SIREN / SIRET"),
    "phone_intl": (phone_intl, "International phone (8-15 digits)"),
    "nl_bsn": (nl_bsn, "NL BSN (11-proof)"),
    "se_pnr": (se_pnr, "SE personnummer"),
    "se_orgnr": (se_orgnr, "SE organisationsnummer"),
    "ua_ipn": (ua_ipn, "UA RNTRC / ІПН"),
    "aba": (aba, "US ABA routing number"),
}


def get(name) -> "Callable | None":
    return VALIDATORS[name][0] if name in VALIDATORS else None
