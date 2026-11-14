"""Ready-made content filters (national identifiers, GDPR sensitive data) and groups. Seeded once; afterwards they are ordinary, editable filters.

Every entry carries `match` / `no_match` examples that the test-suite runs against the pattern, so a broken pattern cannot ship.
Patterns are case-insensitive by default; (?-i:...) switches that off for a part (postcodes, acronyms).
"""

LABEL_GAP = r"[^\d\n]{0,25}?"   # words between a label and the number it introduces

FILTERS: list = []
GROUPS: dict = {}               # group name -> description (members are collected from FILTERS)


def add(name, description, pattern, groups, validator=None, match=(), no_match=(), action="redact", stage="input"):
    FILTERS.append({"name": name, "description": description, "pattern": pattern, "validator": validator, "groups": list(groups),
                    "action": action, "stage": stage, "match": list(match), "no_match": list(no_match)})


def group(name, description):
    GROUPS[name] = description
    return name


def iban(cc, length):
    return rf"(?<![A-Z0-9]){cc}\d{{2}}(?: ?[A-Z0-9]){{{length - 4}}}(?![A-Z0-9])"


ALL = group("PII – All countries", "Personal identifiers of Poland, UK, USA, Germany, Czech Republic, Italy, France, Netherlands, Sweden and Ukraine")
EU = group("PII – EU member states", "Personal identifiers of the EU countries covered here: Poland, Germany, Czech Republic, Italy, France, Netherlands, Sweden")
COUNTRIES = {
    "PL": ("Poland", True), "UK": ("United Kingdom", False), "US": ("USA", False), "DE": ("Germany", True), "CZ": ("Czech Republic", True),
    "IT": ("Italy", True), "FR": ("France", True), "NL": ("Netherlands", True), "SE": ("Sweden", True), "UA": ("Ukraine", False),
}
G = {}
for code, (country, eu) in COUNTRIES.items():
    G[code] = [group(f"PII – {country}", f"Personal identifiers used in {country}: national ID numbers, tax and company numbers, phone numbers, IBAN. Checksums are verified where the format has one.")] + [ALL] + ([EU] if eu else [])

# ------------------------------------------------------------------ Poland
add("PL – PESEL", "Polish national ID number (11 digits, checksum verified)", r"(?<!\d)\d{11}(?!\d)", G["PL"], "pesel",
    match=["PESEL 44051401359", "numer 44051401359."], no_match=["44051401358", "12345678901", "numer zamówienia 20261112005"])
add("PL – NIP", "Polish tax number (checksum verified)", r"(?<![\d-])(?:(?:NIP|VAT)[\s:.-]*(?:PL)?\s?\d{10}|PL\s?\d{10}|\d{3}-\d{3}-\d{2}-\d{2}|\d{3}-\d{2}-\d{2}-\d{3})(?![\d-])", G["PL"], "nip_pl",
    match=["NIP 1234563218", "NIP: 123-456-32-18", "PL1234563218", "123-45-63-218"], no_match=["NIP 1234563219", "123-456-32-19", "1234563218"])
add("PL – REGON", "Polish business register number (needs the word REGON, checksum verified)", rf"\bREGON[\s:.-]*(?P<n>\d{{14}}|\d{{9}})(?!\d)", G["PL"], "regon",
    match=["REGON: 123456785", "REGON 12345678512347"], no_match=["REGON 123456786", "123456785"])
add("PL – ID card number", "Polish identity card (3 letters + 6 digits, checksum verified)", r"(?<![A-Z0-9])[A-Z]{3}[ -]?\d{6}(?!\d)", G["PL"], "pl_id_card",
    match=["dowód ABA300000", "ABA 300000"], no_match=["ABA300001", "ABC123456", "ABA30000"])
add("PL – Passport", "Polish passport number (needs the word paszport/passport nearby)", rf"\b(?:paszport\w*|passport){LABEL_GAP}(?P<n>[A-Z]{{2}}\s?\d{{7}})(?!\d)", G["PL"],
    match=["paszport nr EA1234567", "passport: EA 1234567"], no_match=["EA1234567", "paszport wydany w 2020"])
add("PL – Phone number", "Polish mobile and landline numbers", r"(?<![\w+])(?:(?:\+|00)48[ -]?)?[1-9]\d{2}[ -]\d{3}[ -]\d{3}(?![\d-])|(?<![\w+])(?:\+|00)48[ -]?[1-9]\d{8}(?!\d)|(?<![\w+])(?:\+48[ -]?)?\(?\d{2}\)?[ -]\d{3}[ -]\d{2}[ -]\d{2}(?![\d-])", G["PL"],
    match=["+48 501 234 567", "501-234-567", "+48501234567", "tel. 22 123 45 67"], no_match=["2026-11-12", "123456789", "w 2026 roku"])
add("PL – IBAN", "Polish bank account (IBAN, checksum verified)", iban("PL", 28), G["PL"], "iban",
    match=["PL61 1090 1014 0000 0712 1981 2874", "PL61109010140000071219812874"], no_match=["PL61 1090 1014 0000 0712 1981 2875"])

# ------------------------------------------------------------------ United Kingdom
add("UK – National Insurance number", "UK NINO (prefix rules applied)", r"(?<![A-Z0-9])(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D](?![A-Z0-9])", G["UK"],
    match=["NI number AB123456C", "AB 12 34 56 C"], no_match=["DA123456C", "BG123456C", "AB123456E", "AB1234567"])
add("UK – NHS number", "UK NHS number (checksum verified)", r"(?<![\d-])(?:\d{3}[ -]\d{3}[ -]\d{4}|NHS[^\d\n]{0,20}\d{10})(?![\d-])", G["UK"], "nhs",
    match=["943 476 5919", "NHS number 9434765919"], no_match=["943 476 5918", "NHS number 9434765918"])
add("UK – UTR (tax reference)", "Unique Taxpayer Reference (needs the word UTR)", rf"\bUTR{LABEL_GAP}(?P<n>\d{{5}} ?\d{{5}})(?!\d)", G["UK"],
    match=["UTR: 12345 67890", "UTR 1234567890"], no_match=["12345 67890", "UTR pending"])
add("UK – Passport", "UK passport number (needs the word passport nearby)", rf"\bpassport{LABEL_GAP}(?P<n>\d{{9}})(?!\d)", G["UK"],
    match=["passport no. 123456789"], no_match=["123456789", "passport expired"])
add("UK – Driving licence", "UK driving licence number", r"(?-i:(?<![A-Z0-9])[A-Z9]{5}\d{6}[A-Z9]{2}\d[A-Z]{2}(?![A-Z0-9]))", G["UK"],
    match=["licence MORGA657054SM9IJ"], no_match=["MORGA657054SM9I", "morga657054sm9ij"])
add("UK – Postcode", "UK postcode (upper case)", r"(?-i:(?<![A-Z0-9])(?:GIR ?0AA|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})(?![A-Z0-9]))", G["UK"],
    match=["SW1A 1AA", "M1 1AE", "EC1A1BB"], no_match=["ABC 123", "sw1a 1aa", "12345"])
add("UK – Phone number", "UK mobile and landline numbers", r"(?<![\w+])(?:\+44|0044|0)[ -]?(?:\(0\) ?)?(?:7\d{3}[ -]?\d{6}|2\d[ -]?\d{4}[ -]?\d{4}|[1-9]\d{2,3}[ -]?\d{3}[ -]?\d{3,4})(?![\d-])", G["UK"],
    match=["+44 7911 123456", "07911 123456", "020 7946 0958", "+44 20 7946 0958"], no_match=["2026-11-12 10:00:00", "1234"])
add("UK – IBAN", "UK bank account (IBAN, checksum verified)", iban("GB", 22), G["UK"], "iban",
    match=["GB29 NWBK 6016 1331 9268 19"], no_match=["GB29 NWBK 6016 1331 9268 18"])

# ------------------------------------------------------------------ USA
add("US – Social Security number", "US SSN, with dashes or after the label SSN", r"(?<![\d-])(?:(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}|SSN[^\d\n]{0,10}(?!000|666|9\d\d)\d{9})(?![\d-])", G["US"],
    match=["SSN 123-45-6789", "123-45-6789", "SSN: 123456789"], no_match=["000-12-3456", "666-12-3456", "123-00-6789", "123456789"])
add("US – ITIN", "US Individual Taxpayer Identification Number", r"(?<![\d-])9\d{2}-(?:5\d|6[0-5]|7\d|8[0-8]|9[0-2]|9[4-9])-\d{4}(?![\d-])", G["US"],
    match=["912-70-1234"], no_match=["912-30-1234", "812-70-1234"])
add("US – EIN", "US employer identification number (needs the word EIN)", rf"\bEIN{LABEL_GAP}(?P<n>\d{{2}}-?\d{{7}})(?!\d)", G["US"],
    match=["EIN: 12-3456789", "EIN 123456789"], no_match=["12-3456789"])
add("US – Medicare MBI", "US Medicare Beneficiary Identifier", r"(?-i:(?<![A-Z0-9])[1-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?[AC-HJKMNP-RT-Y]{2}\d{2}(?![A-Z0-9]))", G["US"],
    match=["1EG4-TE5-MK73", "1EG4TE5MK73"], no_match=["1SG4-TE5-MK73", "0EG4-TE5-MK73"])
add("US – Bank routing number (ABA)", "US ABA routing number (needs the word routing/ABA, checksum verified)", rf"\b(?:routing|ABA|RTN){LABEL_GAP}(?P<n>\d{{9}})(?!\d)", G["US"], "aba",
    match=["routing number 021000021"], no_match=["routing number 021000022", "021000021"])
add("US – Passport", "US passport number (needs the word passport nearby)", rf"\bpassport{LABEL_GAP}(?P<n>[A-Z]?\d{{8,9}})(?![\dA-Z])", G["US"],
    match=["passport number: 123456789", "passport C12345678"], no_match=["123456789"])
add("US – Driver's licence", "US driver's licence number (needs the words driver's licence/DL)", rf"\b(?:driver'?s?[ -]licen[cs]e|DL)\b{LABEL_GAP}(?P<n>[A-Z]?\d{{6,12}})(?!\d)", G["US"],
    match=["driver's license number D1234567", "DL# 12345678"], no_match=["D1234567", "DL"])
add("US – ZIP+4 code", "US ZIP+4 postal code", r"(?<![\d-])\d{5}-\d{4}(?![\d-])", G["US"],
    match=["10001-1234"], no_match=["10001", "1000-12345"])
add("US – Phone number", "US/Canada phone numbers", r"(?<![\w+])(?:\+1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])", G["US"],
    match=["(212) 555-0123", "+1 212-555-0123", "212.555.0123"], no_match=["2026-11-12", "123-45-6789"])

# ------------------------------------------------------------------ Germany
add("DE – Steuer-ID", "German tax identification number (needs the word Steuer-ID/IdNr, checksum verified)", rf"\b(?:Steuer-?ID|Steueridentifikationsnummer|IdNr\.?|Identifikationsnummer){LABEL_GAP}(?P<n>\d{{2}} ?\d{{3}} ?\d{{3}} ?\d{{3}})(?!\d)", G["DE"], "de_taxid",
    match=["Steuer-ID: 86 095 742 719", "IdNr 86095742719"], no_match=["Steuer-ID: 86 095 742 718", "86095742719"])
add("DE – Rentenversicherungsnummer", "German social security number", r"(?<![A-Z0-9])\d{2} ?(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])\d{2} ?[A-Z] ?\d{3}(?![A-Z0-9])", G["DE"],
    match=["65 180539 W 004", "12010190A123"], no_match=["65 183239 W 004", "65 180539 004"])
add("DE – Personalausweis / Reisepass", "German identity card or passport number (needs the word Personalausweis/Reisepass)", r"\b(?:Personalausweis(?:nummer)?|Ausweisnummer|Reisepass(?:nummer)?)[ :.#-]{0,6}(?:Nr\.?[ :]*)?(?P<n>[CFGHJKLMNPRTVWXYZ0-9]{9})(?![A-Z0-9])", G["DE"],
    match=["Personalausweis: L01X00T47", "Reisepassnummer C01X00T47"], no_match=["L01X00T47", "Personalausweis: A01X00T47"])
add("DE – USt-IdNr", "German VAT number", r"(?<![A-Z0-9])DE ?\d{9}(?!\d)", G["DE"],
    match=["USt-IdNr DE123456789", "DE 123456789"], no_match=["DE12345678", "DE1234567890"])
add("DE – Phone number", "German mobile and landline numbers", r"(?<![\w+])(?:(?:\+49|0049)[ -]?\(?0?\)?|0)(?:1[5-7]\d|[2-9]\d{1,4})[ /-]?\d{3,8}(?:[ -]?\d{1,4})?(?![\d-])", G["DE"],
    match=["+49 151 23456789", "0151/23456789", "030 1234567", "+49 30 12345678"], no_match=["2026-11-12", "1234567"])
add("DE – IBAN", "German bank account (IBAN, checksum verified)", iban("DE", 22), G["DE"], "iban",
    match=["DE89 3704 0044 0532 0130 00"], no_match=["DE89 3704 0044 0532 0130 01"])

# ------------------------------------------------------------------ Czech Republic
add("CZ – Rodné číslo", "Czech birth number (date and checksum verified)", r"(?<![\d/])\d{6}/?\d{3,4}(?![\d/])", G["CZ"], "cz_birth",
    match=["rodné číslo 710319/2745", "7103192745", "530101/123"], no_match=["710319/2746", "123456789", "7113192745"])
add("CZ – IČO", "Czech company number (needs the word IČO, checksum verified)", rf"\b(?:IČO|IČ|ICO){LABEL_GAP}(?P<n>\d{{8}})(?!\d)", G["CZ"], "cz_ico",
    match=["IČO: 45274649", "ICO 45274649"], no_match=["IČO: 45274640", "45274649"])
add("CZ – DIČ", "Czech VAT number", r"(?<![A-Z0-9])CZ\d{8,10}(?!\d)", G["CZ"],
    match=["DIČ CZ45274649", "CZ7103192745"], no_match=["CZ1234567", "CZ12345678901"])
add("CZ – ID card / passport number", "Czech ID card or passport number (needs občanský průkaz / pas / OP nearby)", rf"\b(?:občansk\w*\s+průkaz\w*|cestovní\s+pas\w*|číslo\s+pasu|číslo\s+OP){LABEL_GAP}(?P<n>\d{{8,9}})(?!\d)", G["CZ"],
    match=["číslo OP: 123456789", "cestovní pas 12345678"], no_match=["123456789", "občanský průkaz platný"])
add("CZ – Phone number", "Czech phone numbers", r"(?<![\w+])(?:(?:\+|00)420[ -]?)?[1-9]\d{2}[ -]\d{3}[ -]\d{3}(?![\d-])|(?<![\w+])(?:\+|00)420[ -]?[1-9]\d{8}(?!\d)", G["CZ"],
    match=["+420 601 234 567", "601 234 567", "+420601234567"], no_match=["2026-11-12", "601234567"])
add("CZ – IBAN", "Czech bank account (IBAN, checksum verified)", iban("CZ", 24), G["CZ"], "iban",
    match=["CZ65 0800 0000 1920 0014 5399"], no_match=["CZ65 0800 0000 1920 0014 5398"])

# ------------------------------------------------------------------ Italy
add("IT – Codice fiscale", "Italian tax code (check letter verified)", r"(?<![A-Z0-9])[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z](?![A-Z0-9])", G["IT"], "it_codice_fiscale",
    match=["CF RSSMRA85T10A562S", "rssmra85t10a562s"], no_match=["RSSMRA85T10A562T", "RSSMRA85T10A562"])
add("IT – Partita IVA", "Italian VAT number (needs P.IVA or the IT prefix, checksum verified)", r"(?:\b(?:P\.? ?IVA|partita\s+IVA)[^\d\n]{0,10}|(?<![A-Z0-9])IT) ?(?P<n>\d{11})(?!\d)", G["IT"], "it_piva",
    match=["P.IVA 12345678903", "IT12345678903", "partita IVA: 12345678903"], no_match=["P.IVA 12345678904", "12345678903"])
add("IT – Identity card / passport", "Italian identity card (CIE/paper) or passport number (needs the words carta d'identità / passaporto)", rf"\b(?:carta\s+d'identit[àa]|CIE|passaporto){LABEL_GAP}(?P<n>[A-Z]{{2}}\d{{5}}[A-Z]{{2}}|[A-Z]{{2}}\d{{7}})(?![A-Z0-9])", G["IT"],
    match=["carta d'identità n. CA00000AA", "passaporto YA1234567"], no_match=["CA00000AA", "passaporto scaduto"])
add("IT – Phone number", "Italian mobile and landline numbers", r"(?<![\w+])(?:\+|00)39[ -]?3\d{2}[ -]?\d{3}[ -]?\d{3,4}(?![\d-])|(?<![\w+])3\d{2}[ -]\d{3}[ -]?\d{3,4}(?![\d-])|(?<![\w+])(?:\+|00)39[ -]?0\d{1,3}[ -]?\d{5,8}(?!\d)|(?<![\w+])0\d{1,3}[ -]\d{4}[ -]?\d{3,4}(?![\d-])", G["IT"],
    match=["+39 333 1234567", "333 123 4567", "+39 06 12345678", "06 1234 5678"], no_match=["2026-11-12", "3331234567"])
add("IT – IBAN", "Italian bank account (IBAN, checksum verified)", iban("IT", 27), G["IT"], "iban",
    match=["IT60 X054 2811 1010 0000 0123 456"], no_match=["IT60 X054 2811 1010 0000 0123 457"])

# ------------------------------------------------------------------ France
add("FR – NIR (numéro de sécurité sociale)", "French social security number (13 digits + key, key verified)", r"(?<!\d)[12] ?\d{2} ?(?:0[1-9]|1[0-2]|[2-9]\d) ?(?:\d{2}|2[AB]) ?\d{3} ?\d{3} ?\d{2}(?!\d)", G["FR"], "fr_nir",
    match=["1 84 12 76 451 089 46", "184127645108946"], no_match=["1 84 12 76 451 089 47", "1841276451089"])
add("FR – SIREN / SIRET", "French company number (needs the word SIREN/SIRET, checksum verified)", rf"\b(?:SIRET|SIREN){LABEL_GAP}(?P<n>\d{{3}} ?\d{{3}} ?\d{{3}}(?: ?\d{{5}})?)(?!\d)", G["FR"], "fr_siren_siret",
    match=["SIREN 732 829 320", "SIRET: 73282932000074"], no_match=["SIREN 732 829 321", "732829320"])
add("FR – TVA number", "French VAT number", r"(?<![A-Z0-9])FR ?[0-9A-Z]{2} ?\d{9}(?!\d)", G["FR"],
    match=["TVA FR40 303265045", "FR40303265045"], no_match=["FR4030326504", "FR40 3032650451"])
add("FR – Passport", "French passport number (needs the word passeport/passport)", rf"\b(?:passeport|passport){LABEL_GAP}(?P<n>\d{{2}}[A-Z]{{2}}\d{{5}})(?![A-Z0-9])", G["FR"],
    match=["passeport n° 12AB34567"], no_match=["12AB34567"])
add("FR – Phone number", "French phone numbers", r"(?<![\w+])(?:(?:\+|00)33[ .-]?\(?0?\)?|0)[1-9](?:[ .-]?\d{2}){4}(?![\d-])", G["FR"],
    match=["06 12 34 56 78", "+33 6 12 34 56 78", "0612345678", "+33612345678", "01.23.45.67.89"], no_match=["2026-11-12", "06 12 34"])
add("FR – IBAN", "French bank account (IBAN, checksum verified)", iban("FR", 27), G["FR"], "iban",
    match=["FR14 2004 1010 0505 0001 3M02 606"], no_match=["FR14 2004 1010 0505 0001 3M02 607"])

# ------------------------------------------------------------------ Netherlands
add("NL – BSN", "Dutch citizen service number (needs the word BSN, 11-proof verified)", rf"\b(?:BSN|burgerservicenummer|sofi(?:nummer)?){LABEL_GAP}(?P<n>\d{{8,9}})(?!\d)", G["NL"], "nl_bsn",
    match=["BSN: 111222333", "burgerservicenummer 111222333"], no_match=["BSN 111222334", "111222333"])
add("NL – KvK number", "Dutch chamber of commerce number (needs the word KvK)", rf"\bKvK{LABEL_GAP}(?P<n>\d{{8}})(?!\d)", G["NL"],
    match=["KvK 12345678", "KvK-nummer: 12345678"], no_match=["12345678"])
add("NL – BTW number", "Dutch VAT number", r"(?<![A-Z0-9])NL ?\d{9}B\d{2}(?![A-Z0-9])", G["NL"],
    match=["BTW NL123456789B01"], no_match=["NL123456789", "NL123456789C01"])
add("NL – Postcode", "Dutch postcode (upper case)", r"(?-i:(?<!\w)[1-9]\d{3} ?(?!SA|SD|SS)[A-Z]{2}(?![A-Z0-9]))", G["NL"],
    match=["1012 AB", "3511LX"], no_match=["1234 SA", "in 2026 is", "1012 ab"])
add("NL – Phone number", "Dutch phone numbers", r"(?<![\w+])(?:(?:\+|00)31[ -]?\(?0?\)?|0)(?:6[ -]?\d{8}|[1-5789]\d{1,2}[ -]?\d{6,7})(?![\d-])", G["NL"],
    match=["06 12345678", "+31 6 12345678", "020 1234567", "+31 20 1234567"], no_match=["2026-11-12", "06 123"])
add("NL – IBAN", "Dutch bank account (IBAN, checksum verified)", iban("NL", 18), G["NL"], "iban",
    match=["NL91 ABNA 0417 1643 00", "NL91ABNA0417164300"], no_match=["NL91 ABNA 0417 1643 01"])

# ------------------------------------------------------------------ Sweden
add("SE – Personnummer / samordningsnummer", "Swedish personal identity number (date and checksum verified)", r"(?<!\d)(?:19|20)?\d{6}[-+]?\d{4}(?!\d)", G["SE"], "se_pnr",
    match=["811218-9876", "198112189876", "personnummer 8112189876"], no_match=["811218-9877", "811318-9876", "123456-7890"])
add("SE – Organisationsnummer", "Swedish company number (needs the word organisationsnummer/orgnr, checksum verified)", rf"\b(?:org(?:anisations)?\.?\s?nr\.?|organisationsnummer|orgnr){LABEL_GAP}(?P<n>\d{{6}}-?\d{{4}})(?!\d)", G["SE"], "se_orgnr",
    match=["organisationsnummer 556016-0680", "orgnr: 5560160680"], no_match=["orgnr 556016-0681", "556016-0680"])
add("SE – VAT number", "Swedish VAT number", r"(?<![A-Z0-9])SE ?\d{10}01(?!\d)", G["SE"],
    match=["moms SE556016068001"], no_match=["SE5560160680", "SE556016068002"])
add("SE – Phone number", "Swedish mobile and landline numbers", r"(?<![\w+])(?:(?:\+|00)46[ -]?\(?0?\)?|0)(?:7[02369][ -]?\d{3}[ -]?\d{2}[ -]?\d{2}|[1-9]\d{0,2}[ -]\d{2,3}(?:[ -]?\d{2,3}){1,2})(?![\d-])", G["SE"],
    match=["070-123 45 67", "+46 70 123 45 67", "08-123 456 78", "0701234567"], no_match=["2026-11-12", "0712345"])
add("SE – IBAN", "Swedish bank account (IBAN, checksum verified)", iban("SE", 24), G["SE"], "iban",
    match=["SE45 5000 0000 0583 9825 7466"], no_match=["SE45 5000 0000 0583 9825 7467"])

# ------------------------------------------------------------------ Ukraine
add("UA – RNTRC / ІПН (tax number)", "Ukrainian taxpayer number (needs the word ІПН/РНОКПП, checksum verified)", rf"\b(?:ІПН|РНОКПП|ИНН|IPN|RNTRC|RNOKPP|податков\w+\s+номер|ідентифікаційн\w+\s+код){LABEL_GAP}(?P<n>\d{{10}})(?!\d)", G["UA"], "ua_ipn",
    match=["ІПН 3011309572", "РНОКПП: 3011309572"], no_match=["ІПН 3011309573", "3011309572"])
add("UA – Passport / ID card", "Ukrainian passport (2 letters + 6 digits) or ID card (9 digits), needs the word паспорт/passport", rf"\b(?:паспорт\w*|passport){LABEL_GAP}(?P<n>[А-ЯІЇЄҐA-Z]{{2}} ?\d{{6}}|\d{{9}})(?!\d)", G["UA"],
    match=["паспорт серії АВ 123456", "паспорт 123456789", "passport: AB 123456"], no_match=["АВ 123456", "паспорт виданий"])
add("UA – ЄДРПОУ (company code)", "Ukrainian company code (needs the word ЄДРПОУ/EDRPOU)", rf"\b(?:ЄДРПОУ|ЕДРПОУ|EDRPOU){LABEL_GAP}(?P<n>\d{{8}})(?!\d)", G["UA"],
    match=["ЄДРПОУ 12345678", "EDRPOU: 12345678"], no_match=["12345678"])
add("UA – Phone number", "Ukrainian phone numbers", r"(?<![\w+])(?:\+?380|0)[ -]?\(?\d{2}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?![\d-])", G["UA"],
    match=["+380 50 123 45 67", "050 123 45 67", "+380501234567", "(050) 123-45-67"], no_match=["2026-11-12", "12345"])
add("UA – IBAN", "Ukrainian bank account (IBAN, checksum verified)", iban("UA", 29), G["UA"], "iban",
    match=["UA21 3223 1300 0002 6007 2335 6600 1"], no_match=["UA21 3223 1300 0002 6007 2335 6600 2"])

# ------------------------------------------------------------------ GDPR
GID = group("GDPR – Personal identifiers", "Art. 4 identifiers that point to a person in any country: email, phone, IP and MAC address, payment card, IBAN, GPS position, labelled date of birth")
GS = group("GDPR – Special categories (Art. 9)", "Keywords for health, religion, ethnic origin, political opinions, trade-union membership, sexual orientation and genetic/biometric data in English, Polish, German, Czech, Italian, French, Dutch, Swedish and Ukrainian. Keyword matching is an approximation: it misses paraphrases and flags some harmless text.")
GC = group("GDPR – Criminal data (Art. 10)", "Keywords about convictions, sentences and criminal records in English, Polish, German, Czech, Italian, French, Dutch, Swedish and Ukrainian. Keyword matching is an approximation.")
GALL = group("GDPR – Everything", "Personal identifiers, special categories and criminal data combined")

add("GDPR – Email address", "Email addresses", r"\b[A-Z0-9._%+-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)*\.[A-Z]{2,}\b", [GID, GALL],
    match=["write to jan.kowalski@example.com", "a+b@mail.co.uk"], no_match=["user@localhost", "@handle"])
add("GDPR – International phone number", "Phone numbers in international format (+ or 00 prefix)", r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}[ .-]?(?:\(?\d{1,4}\)?[ .-]?){2,5}\d(?![\d-])", [GID, GALL], "phone_intl",
    match=["+48 501 234 567", "+1 (212) 555-0123", "0044 20 7946 0958"], no_match=["+48", "2026-11-12", "+12"])
add("GDPR – IPv4 address", "IPv4 addresses", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b", [GID, GALL],
    match=["server 192.168.1.10", "8.8.8.8"], no_match=["999.1.1.1", "1.2.3"])
add("GDPR – IPv6 address", "IPv6 addresses", r"(?<![\w:])(?:[0-9A-F]{1,4}:){7}[0-9A-F]{1,4}(?![\w:])|(?<![\w:])(?:[0-9A-F]{1,4}:){1,6}(?::[0-9A-F]{1,4}){1,6}(?![\w:])", [GID, GALL],
    match=["2001:0db8:85a3:0000:0000:8a2e:0370:7334", "fe80::1", "2001:db8::ff00:42:8329"], no_match=["12:30:45", "meeting at 10:30"])
add("GDPR – Payment card number", "Payment card numbers (Luhn verified)", r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])", [GID, GALL], "card",
    match=["4111 1111 1111 1111", "4111-1111-1111-1111", "5500005555555559"], no_match=["4111 1111 1111 1112", "1234567890123"])
add("GDPR – IBAN (any country)", "Bank account numbers in IBAN format (checksum verified)", r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Z0-9])", [GID, GALL], "iban",
    match=["DE89 3704 0044 0532 0130 00", "GB29NWBK60161331926819"], no_match=["DE89 3704 0044 0532 0130 01", "AB12 CDEF"])
add("GDPR – GPS coordinates", "Decimal latitude/longitude pairs", r"(?<![\d.])[-+]?(?:[1-8]?\d\.\d{4,}|90(?:\.0+)?)\s*[,;]\s*[-+]?(?:1[0-7]\d|\d{1,2})\.\d{4,}(?![\d.])", [GID, GALL],
    match=["52.2297, 21.0122", "-33.8688,151.2093", "48.8566; 2.3522"], no_match=["52.2, 21.0", "version 1.2.3, 4.5.6"])
add("GDPR – MAC address", "Network hardware (MAC) addresses", r"(?<![\w:-])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![\w:-])", [GID, GALL],
    match=["00:1A:2B:3C:4D:5E", "00-1a-2b-3c-4d-5e"], no_match=["00:1A:2B:3C:4D", "12:30:45"])
add("GDPR – Date of birth (labelled)", "A date after words like 'born', 'date of birth', 'data urodzenia', 'geboren am', 'né le' …",
    r"(?<!\w)(?:date\s+of\s+birth|DOB|born(?:\s+on)?|birth\s?date|data\s+urodzenia|ur\.|geboren(?:\s+am)?|geburtsdatum|datum\s+narození|data\s+di\s+nascita|nato\s+il|née?\s+le|date\s+de\s+naissance|geboortedatum|geboren\s+op|födelsedatum|född|дата\s+народження)[^\d\n]{0,10}(?P<n>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})", [GID, GALL],
    match=["date of birth: 12.03.1985", "DOB 1985-03-12", "ur. 12.03.1985", "geboren am 12.03.1985", "née le 12/03/1985"], no_match=["12.03.1985", "born in Warsaw"])


def kw(*parts):
    """One pattern from keyword fragments of several languages."""
    return r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)"


add("GDPR – Health data", "Health, diagnosis, disease and disability terms (9 languages)", kw(
    r"diagnos\w*", r"cancer\w*", r"tumou?r\w*", r"chemotherap\w+", r"(?-i:HIV|AIDS)", r"diabet\w+", r"depress(?:ion|ed)", r"schizophreni\w+", r"bipolar", r"epilep\w+", r"dementia", r"alzheimer\w*",
    r"pregnan\w+", r"miscarriage", r"medical (?:condition|record)s?", r"mental (?:illness|health)", r"sick leave",
    r"nowotw\w+", r"chemioterapi\w+", r"cukrzyc\w+", r"depresj\w+", r"schizofreni\w+", r"choro\w+ psychiczn\w+", r"padaczk\w+", r"demencj\w+", r"ciąż\w+", r"poronieni\w+", r"niepełnosprawn\w+", r"zwolnieni\w+ lekarsk\w+", r"dokumentacj\w+ medyczn\w+",
    r"krebs\w*", r"tumor\w*", r"chemotherapie", r"depression\w*", r"epilepsie", r"demenz", r"schwanger\w*", r"fehlgeburt", r"schwerbehindert\w*", r"behinderung", r"krankschreibung", r"psychische\w* erkrankung", r"krankenakte", r"patientenakte",
    r"rakovin\w+", r"nádor\w*", r"chemoterapi\w+", r"cukrovk\w+", r"deprese", r"epilepsi\w+", r"demenc\w+", r"těhotenstv\w+", r"těhotn\w+", r"potrat\w*", r"zdravotní postižen\w+", r"pracovní neschopnost\w*", r"zdravotní dokumentac\w+", r"duševní onemocnění",
    r"cancro", r"tumore", r"chemioterapia", r"diabete", r"depressione", r"schizofrenia", r"epilessia", r"demenza", r"gravidanza", r"incinta", r"aborto", r"malattia mentale", r"disturbo mentale", r"cartella clinica", r"certificato medico",
    r"cancer", r"tumeur\w*", r"chimiothérapie", r"diabète", r"dépression", r"schizophrénie", r"épilepsie", r"démence", r"grossesse", r"enceinte", r"fausse couche", r"avortement", r"handicap\w*", r"maladie mentale", r"dossier médical", r"arrêt maladie",
    r"kanker", r"chemotherapie", r"suikerziekte", r"depressie", r"schizofrenie", r"dementie", r"zwangerschap", r"zwanger\w*", r"miskraam", r"abortus", r"psychische\w* (?:aandoening|stoornis)", r"medisch dossier", r"ziekmelding",
    r"cellgift\w*", r"kemoterapi", r"graviditet", r"missfall", r"funktionsnedsättning", r"psykisk\w* (?:sjukdom|ohälsa)", r"patientjournal", r"sjukskriv\w+",
    r"діагноз\w*", r"онколог\w+", r"раков\w+", r"пухлин\w+", r"хіміотерап\w+", r"(?-i:ВІЛ|СНІД)", r"діабет\w*", r"депресі\w+", r"шизофрені\w+", r"епілепсі\w+", r"деменці\w+", r"вагітн\w+", r"викидень", r"аборт\w*", r"інвалід\w+", r"психічн\w+ (?:розлад|захворюванн)\w*", r"медичн\w+ картк\w+"),
    [GS, GALL], match=["he was diagnosed with cancer", "ma cukrzycę typu 2", "Schwangerschaft im 3. Monat", "pacjent z HIV", "il a la dépression", "хворий на діабет"],
    no_match=["The new feature aids in debugging", "Please summarise the quarterly report", "wir haben ein Meeting"])
add("GDPR – Religion and beliefs", "Religion, faith and belief terms (9 languages)", kw(
    r"religio\w+", r"muslim\w*", r"islam\w*", r"christian\w*", r"catholic\w*", r"protestant\w*", r"orthodox\w*", r"jewish", r"judaism", r"buddhis\w+", r"hindu\w*", r"atheis\w+", r"agnostic\w*", r"sikh\w*",
    r"religi\w+", r"muzułman\w*", r"chrześcijan\w*", r"katolik\w*", r"katolick\w+", r"prawosławn\w+", r"żyd\w*", r"judaizm", r"buddyst\w+", r"buddyzm", r"hindus\w*", r"ateis\w+", r"wyznani\w+",
    r"christ(?:en|lich)\w*", r"katholi\w+", r"evangelisch\w*", r"jüdisch\w*", r"juden", r"atheist\w*", r"konfession\w*",
    r"nábožen\w+", r"křesťan\w*", r"katolík\w*", r"pravoslavn\w+", r"žid\w*", r"židovsk\w+", r"hinduis\w+", r"ateis\w+", r"vyznání",
    r"religios\w+", r"musulman\w+", r"cristian\w+", r"cattolic\w+", r"ortodoss\w+", r"ebre\w+", r"giudaism\w*", r"buddis\w+", r"induis\w+", r"ateismo", r"confession\w+",
    r"chrétien\w*", r"catholique\w*", r"musulman\w*", r"juif\w*", r"judaïsme", r"bouddhis\w+", r"hindou\w*", r"athée\w*", r"athéisme",
    r"religie\w*", r"moslim\w*", r"christen\w*", r"katholiek\w*", r"joods\w*", r"boeddhis\w+", r"hindoe\w*", r"atheïs\w+", r"geloofs\w+",
    r"kristen\w*", r"kristna", r"katolik\w*", r"ortodox\w*", r"judisk\w*", r"trosuppfattning",
    r"релігі\w+", r"мусульман\w*", r"іслам\w*", r"християн\w*", r"католи\w+", r"православн\w+", r"іудей\w*", r"єврейськ\w+", r"буддис\w+", r"індуїс\w+", r"атеїс\w+", r"віросповідан\w+"),
    [GS, GALL], match=["he is a practising Muslim", "jest katolikiem", "evangelische Kirche", "il est musulman", "він християнин"],
    no_match=["Please summarise the quarterly report", "the interface is intuitive"])
add("GDPR – Racial or ethnic origin", "Race, ethnicity and origin terms (9 languages)", kw(
    r"racial\w*", r"ethnic\w*", r"skin colou?r", r"(?:african|asian|arab|latino|hispanic|roma|romani)\s+(?:descent|origin|heritage)",
    r"rasow\w+", r"etniczn\w+", r"kolor skóry", r"narodowość",
    r"rassisch\w*", r"ethnisch\w*", r"hautfarbe", r"abstammung",
    r"rasov\w+", r"etnick\w+", r"barva pleti", r"národnost\w*",
    r"razzial\w+", r"etnic\w+", r"colore della pelle",
    r"racial\w*", r"ethnique\w*", r"couleur de peau",
    r"raciaal", r"etnisch\w*", r"huidskleur", r"etnische afkomst",
    r"rasmässig\w*", r"etnisk\w*", r"hudfärg",
    r"расов\w+", r"етнічн\w+", r"колір шкіри", r"національність"),
    [GS, GALL], match=["of Roma origin", "pochodzenie etniczne", "ethnische Herkunft", "origine ethnique"], no_match=["a race car", "the national team"])
add("GDPR – Political opinions", "Political opinions and party membership terms (9 languages)", kw(
    r"political (?:opinions?|views?|beliefs?|affiliation|part(?:y|ies))", r"party member(?:ship)?", r"(?:voted|vote) for", r"far[- ](?:right|left)", r"communist\w*", r"socialist\w*", r"nationalist\w*",
    r"pogląd\w* polityczn\w+", r"partii? polityczn\w+", r"człon\w+ partii", r"głosował\w*", r"komunist\w+", r"socjalist\w+", r"nacjonalist\w+",
    r"politische\w* (?:meinung\w*|ansicht\w*|gesinnung)", r"parteimitglied\w*", r"parteizugehörigkeit", r"kommunist\w+", r"sozialist\w+",
    r"politick\w+ (?:názor\w*|přesvědčení|stran\w+)", r"člen\w* strany", r"volil\w*", r"komunist\w+", r"socialist\w+", r"nacionalist\w+",
    r"opinion\w+ politic\w+", r"partito politico", r"iscritto al partito", r"comunist\w+", r"nazionalist\w+", r"votato per",
    r"opinions? politiques?", r"parti politique", r"membre du parti", r"communiste\w*", r"socialiste\w*", r"nationaliste\w*", r"extrême[- ](?:droite|gauche)",
    r"politieke (?:voorkeur|overtuiging|opvatting\w*|partij)", r"partijlid", r"extreem(?:rechts|links)",
    r"politisk\w* (?:åsikt\w*|övertygelse|tillhörighet|parti\w*)", r"partimedlem", r"kommunist\w*",
    r"політичн\w+ (?:погляд\w*|переконанн\w+|парті\w+)", r"член\w* партії", r"комуніст\w+", r"соціаліст\w+", r"націоналіст\w+"),
    [GS, GALL], match=["his political opinions", "członek partii", "politische Meinung", "membre du parti", "член партії"], no_match=["The Party was fun", "a communication tool"])
add("GDPR – Trade-union membership", "Trade-union terms (9 languages)", kw(
    r"trade[- ]union\w*", r"union member\w*", r"labou?r union\w*",
    r"związ\w+ zawodow\w+", r"człon\w+ związku", r"gewerkschaft\w*", r"odborov\w+", r"člen\w* odbor\w+", r"sindacat\w+", r"syndicat\w*", r"vakbond\w*", r"fackförbund\w*", r"facklig\w*", r"профспілк\w+", r"професійн\w+ спілк\w+"),
    [GS, GALL], match=["a trade union member", "związek zawodowy", "Mitglied der Gewerkschaft", "sindacato", "профспілка"], no_match=["the European Union", "reunion party"])
add("GDPR – Sexual orientation and sex life", "Sexual orientation and gender-identity terms (9 languages)", kw(
    r"sexual orientation", r"sex life", r"homosexual\w*", r"lesbian\w*", r"bisexual\w*", r"transgender", r"lgbt\w*", r"gender identity",
    r"orientacj\w+ seksualn\w+", r"homoseksual\w+", r"lesbijk\w*", r"biseksual\w+", r"transpłciow\w+", r"życie seksualn\w+",
    r"sexuelle orientierung", r"homosexuell\w*", r"schwul\w*", r"lesbisch\w*", r"bisexuell\w*", r"transsexuell\w*", r"sexualleben",
    r"sexuální orientac\w+", r"homosexuál\w+", r"lesb\w+", r"bisexuál\w+", r"sexuální život\w*",
    r"orientamento sessuale", r"omosessual\w+", r"lesbic\w+", r"bisessual\w+", r"vita sessuale",
    r"orientation sexuelle", r"homosexuel\w*", r"lesbienne\w*", r"bisexuel\w*", r"transgenre", r"vie sexuelle",
    r"seksuele geaardheid", r"homoseksueel\w*", r"biseksueel\w*", r"seksleven",
    r"sexuell läggning", r"homosexuell\w*", r"lesbisk\w*", r"bisexuell\w*", r"transperson\w*", r"sexualliv", r"hbtq\w*",
    r"сексуальн\w+ орієнтаці\w+", r"гомосексуал\w+", r"лесбі\w+", r"бісексуал\w+", r"трансгендер\w*", r"інтимн\w+ життя", r"лгбт\w*"),
    [GS, GALL], match=["his sexual orientation", "orientacja seksualna", "sexuelle Orientierung", "orientamento sessuale"], no_match=["a gender-neutral form", "the sixth sense"])
add("GDPR – Genetic and biometric data", "Genetic and biometric terms (9 languages)", kw(
    r"genetic (?:data|tests?|information|profile)", r"(?-i:DNA|ДНК|ADN)", r"biometric\w*", r"fingerprint\w*", r"facial recognition", r"iris scan", r"retina scan", r"voice ?print",
    r"dane genetyczn\w+", r"biometryczn\w+", r"linie papilarne", r"odcisk\w* palców", r"rozpoznawanie twarzy",
    r"genetische\w* daten", r"gentest\w*", r"biometrisch\w*", r"fingerabdr\w+", r"gesichtserkennung",
    r"genetick\w+ údaj\w*", r"biometrick\w+", r"otisk\w* prstů", r"rozpoznávání obličeje",
    r"dati genetici", r"biometric\w+", r"impronte? digital\w+", r"riconoscimento facciale",
    r"données génétiques", r"biométrique\w*", r"empreintes? digitales?", r"reconnaissance faciale",
    r"genetische gegevens", r"vingerafdruk\w*", r"gezichtsherkenning",
    r"genetisk\w* (?:data|uppgifter)", r"biometri\w*", r"fingeravtryck", r"ansiktsigenkänning",
    r"генетичн\w+ дан\w+", r"біометричн\w+", r"відбиток\w* пальців", r"розпізнавання облич\w*"),
    [GS, GALL], match=["a DNA sample", "dane genetyczne", "biometrische Daten", "empreinte digitale", "відбиток пальців"], no_match=["the dna of the project", "a finger puppet"])
add("GDPR – Criminal convictions and offences", "Criminal record, conviction and arrest terms (9 languages)", kw(
    r"criminal (?:record|conviction|history|offen[cs]e)s?", r"convicted", r"conviction\w*", r"sentenced", r"prison sentence", r"arrest(?:ed| warrant)", r"police record", r"felony", r"misdemeanou?r", r"parole", r"probation",
    r"karalno\w+", r"karany", r"skazan\w+", r"wyrok\w*", r"aresztowan\w+", r"areszt\w*", r"przestępstw\w+", r"zakład karny", r"odbywa\w* karę",
    r"vorstrafe\w*", r"vorbestraft\w*", r"straftat\w*", r"verurteilt\w*", r"verurteilung\w*", r"haftstrafe\w*", r"freiheitsstrafe\w*", r"festgenommen", r"führungszeugnis", r"haftbefehl",
    r"trestn\w+ (?:čin\w*|rejstřík)", r"odsouzen\w+", r"odsouzení", r"vězení", r"zatčen\w+", r"rejstřík\w* trestů", r"podmíněn\w+ trest\w*",
    r"casellario giudiziale", r"precedenti penali", r"condannat\w+", r"condanna\w*", r"arrestat\w+", r"pena detentiva", r"carcere",
    r"casier judiciaire", r"condamn\w+", r"condamnation\w*", r"arrestation", r"infraction\w*", r"peine de prison", r"incarcér\w+",
    r"strafblad", r"veroordeeld\w*", r"veroordeling\w*", r"aangehouden", r"gearresteerd", r"gevangenisstraf", r"justitiële documentatie",
    r"belastningsregister", r"straffregister", r"dömd\w*", r"fängelse\w*", r"häktad\w*", r"brottsbelastad",
    r"судимість", r"судимост\w+", r"засуджен\w+", r"вирок\w*", r"арештован\w+", r"ув'язнен\w+", r"кримінальн\w+ (?:справ\w+|провадження|злочин\w*)"),
    [GC, GALL], match=["he has a criminal record", "był skazany", "Vorstrafen", "casier judiciaire", "ma судимість"], no_match=["a quiet afternoon", "the sentence structure is fine"])
