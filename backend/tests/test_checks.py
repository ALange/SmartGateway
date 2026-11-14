import pytest

from app.guardrails import checks, filters, presets

VALID = {
    "pesel": "44051401359", "nip_pl": "123-456-32-18", "regon": "123456785", "pl_id_card": "ABA300000", "nhs": "943 476 5919",
    "de_taxid": "86095742719", "cz_birth": "710319/2745", "cz_ico": "45274649", "it_codice_fiscale": "RSSMRA85T10A562S",
    "it_piva": "12345678903", "fr_nir": "1 84 12 76 451 089 46", "fr_siren_siret": "732 829 320", "nl_bsn": "111222333",
    "se_pnr": "811218-9876", "se_orgnr": "556016-0680", "ua_ipn": "3011309572", "aba": "021000021", "card": "4111 1111 1111 1111",
    "iban": "DE89 3704 0044 0532 0130 00", "phone_intl": "+48 501 234 567",
}
INVALID = {
    "pesel": "44051401358", "nip_pl": "123-456-32-19", "regon": "123456786", "pl_id_card": "ABA300001", "nhs": "943 476 5918",
    "de_taxid": "86095742718", "cz_birth": "710319/2746", "cz_ico": "45274640", "it_codice_fiscale": "RSSMRA85T10A562T",
    "it_piva": "12345678904", "fr_nir": "1 84 12 76 451 089 47", "fr_siren_siret": "732 829 321", "nl_bsn": "111222334",
    "se_pnr": "811218-9877", "se_orgnr": "556016-0681", "ua_ipn": "3011309573", "aba": "021000022", "card": "4111 1111 1111 1112",
    "iban": "DE89 3704 0044 0532 0130 01", "phone_intl": "+48 5",
}


def test_every_validator_is_covered():
    assert set(VALID) == set(checks.VALIDATORS) == set(INVALID)


@pytest.mark.parametrize("name", sorted(VALID))
def test_validator_accepts_valid_and_rejects_changed_numbers(name):
    fn = checks.get(name)
    assert fn(VALID[name]) is True
    assert fn(INVALID[name]) is False


def test_validator_edge_cases():
    assert checks.regon("12345678512347") and not checks.regon("12345678512348")        # 14-digit REGON
    assert checks.cz_birth("530101/123") and not checks.cz_birth("540101/123")            # 9 digits only existed until 1953
    assert checks.cz_birth("7153192745") == (7153192745 % 11 == 0)                          # women's months (+50) are structurally fine; the mod-11 rule decides
    assert checks.se_pnr("198112189876") and not checks.se_pnr("198112189877")             # 12-digit personnummer with century
    assert checks.se_pnr("811278-9873")                                                      # samordningsnummer: day 18 + 60
    assert checks.luhn_any("8113329877") and not checks.se_pnr("8113329877")                 # checksum fits but month 13 / day 32 is not a date
    assert checks.nl_bsn("011122233") == checks.nl_bsn("11122233")                           # an 8-digit BSN is zero padded
    assert checks.fr_nir("1 84 12 76 451 089") is True                                    # key missing: structure only
    assert not checks.de_taxid("06095742719")                                              # leading zero is never valid


# ------------------------------------------------------------------ the preset catalog
def rule_of(p):
    return {"pattern": p["pattern"], "validator": p["validator"], "stage": p["stage"], "action": p["action"]}


@pytest.mark.parametrize("p", presets.FILTERS, ids=[p["name"] for p in presets.FILTERS])
def test_preset_compiles_and_matches_its_examples(p):
    rule = rule_of(p)
    filters.validate_rule(rule)
    assert p["match"] and p["no_match"], "every preset needs positive and negative examples"
    for s in p["match"]:
        assert filters.find(rule, s), f"should match: {s!r}"
    for s in p["no_match"]:
        assert not filters.find(rule, s), f"should not match: {s!r}"


def test_catalog_is_consistent():
    names = [p["name"].lower() for p in presets.FILTERS]
    assert len(names) == len(set(names)) and len(presets.FILTERS) >= 70
    assert all(g in presets.GROUPS for p in presets.FILTERS for g in p["groups"])
    for country in ("Poland", "United Kingdom", "USA", "Germany", "Czech Republic", "Italy", "France", "Netherlands", "Sweden", "Ukraine"):
        members = [p for p in presets.FILTERS if f"PII – {country}" in p["groups"]]
        assert len(members) >= 5, country
        assert all(f"PII – {country}" in p["groups"] and "PII – All countries" in p["groups"] for p in members)
    in_eu = {p["name"] for p in presets.FILTERS if "PII – EU member states" in p["groups"]}
    assert not any(n.startswith(("UK –", "US –", "UA –")) for n in in_eu) and any(n.startswith("PL –") for n in in_eu)
    for g in ("GDPR – Personal identifiers", "GDPR – Special categories (Art. 9)", "GDPR – Criminal data (Art. 10)"):
        assert all(g in p["groups"] for p in presets.FILTERS if "GDPR – Everything" in p["groups"] and g in p["groups"])
    assert sum("GDPR – Special categories (Art. 9)" in p["groups"] for p in presets.FILTERS) == 7


BENIGN = """Hi team, the quarterly report is ready. We shipped version 2.4.1 on 2026-11-12 at 10:30 and the build took 3 minutes.
Meeting room 4B, invoice 2026/11/0042, order 123456, total 1,299.00 EUR, ref ABC-12345. Please review the pull request
and update the docs. The sentence structure of the third paragraph needs work; the race car chapter is fine. Call me later."""


def test_presets_leave_ordinary_text_alone():
    hits = [(p["name"], [m.group(0) for m in filters.find(rule_of(p), BENIGN)]) for p in presets.FILTERS]
    assert [h for h in hits if h[1]] == []
