import pytest

from tracker import classify


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Compliance Officer", "Compliance"),
        ("Ofițer Conformitate", "Compliance"),
        ("Regional Compliance Specialist", "Compliance"),
        ("AML Compliance Officer", "AML / KYC / FinCrime"),
        ("KYC Analyst Revue", "AML / KYC / FinCrime"),
        ("Due Dilligence Analyst with Greek", "AML / KYC / FinCrime"),
        ("Money Laundering Reporting Officer", "AML / KYC / FinCrime"),
        ("Data Protection Officer (2 ani)", "Data protection / DPO"),
        ("Chief Data Office Protection Analyst", "Data protection / DPO"),
        ("Specialist Guvernanta Date cu Caracter Personal", "Data protection / DPO"),
        ("Cybersecurity Compliance Manager", "Adjacent"),
        ("Trade Compliance Analyst", "Adjacent"),
        ("Regulatory Affairs Specialist", "Adjacent"),
        ("AML Detection Data Scientist with French", "Adjacent"),
        ("Junior Fraud Prevention Specialist", "Adjacent"),
        ("Process Referent with French- CDD 12 Months", None),
        ("Analyste AML FOA - CDD 2 years", "AML / KYC / FinCrime"),
        ("Specialist Etichetare Produse", None),
        ("Ethics & Compliance Counsel", "Compliance"),
        ("Booking Holdings Romania - IT Risk & Compliance Officer (Cloud Risk)", "Adjacent"),
        ("Global Accounting Compliance Systems Accountant", "Adjacent"),
        ("Java Developer", None),
        ("Relationship Manager", None),
    ],
)
def test_category(title, expected):
    assert classify.category(title) == expected


@pytest.mark.parametrize(
    "location,city,in_romania,remote_ro",
    [
        ("Bucharest, Bucharest, Romania", "Bucharest", True, False),
        ("București", "Bucharest", True, False),
        ("Ilfov, Romania", "Bucharest", True, False),
        ("Timişoara, Timiş, Romania", "Timișoara", True, False),
        ("Iaşi, Iaşi, Romania", "Iași", True, False),
        ("Romania", "Romania", True, False),
        ("Remote - EMEA", "Remote - EMEA", False, True),
        ("Remote (Romania)", "Romania", True, True),
        ("Berlin, Germany", "Berlin", False, False),
        ("Remote - US", "Remote - US", False, False),
    ],
)
def test_place(location, city, in_romania, remote_ro):
    p = classify.place(location)
    assert (p["city"], p["in_romania"], p["remote_ro"]) == (city, in_romania, remote_ro)


def test_should_alert():
    base = {"is_core": True, "city": "Bucharest", "remote_ro": False}
    assert classify.should_alert(base)
    assert not classify.should_alert({**base, "is_core": False})
    assert not classify.should_alert({**base, "city": "Cluj-Napoca"})
    assert classify.should_alert({**base, "city": "Remote - EMEA", "remote_ro": True})


def test_fingerprint_ignores_company_suffixes_and_gender_tags():
    a = classify.fingerprint("ING Hubs Romania", "Data Protection Expert @ING Hubs Romania", "Bucharest")
    b = classify.fingerprint("ING", "Data Protection Expert", "Bucharest")
    assert a == b
    assert classify.fingerprint("Acme SRL", "Compliance Officer (m/f/d)", "Bucharest") == classify.fingerprint(
        "Acme", "Compliance Officer", "Bucharest"
    )
