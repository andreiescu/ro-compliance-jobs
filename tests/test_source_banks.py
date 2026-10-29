import json
from pathlib import Path

from tracker.sources import banks

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / f"banks_{name}").read_text()


def test_parse_libra():
    jobs = {j.native_id: j for j in banks.parse_libra(_read("libra.html"))}
    assert set(jobs) == {"libra:1481", "libra:1470", "libra:1407"}
    job = jobs["libra:1481"]
    assert job.title == "AML Compliance Officer"
    assert job.location == "Divizia Conformitate, Romania"
    assert job.url == "https://www.librabank.ro/Cariere/AML+Compliance+Officer/1481"
    assert job.source == "banks" and job.company == "Libra Internet Bank"
    assert jobs["libra:1407"].location == "Sucursala Cluj-Napoca, Romania"


def test_parse_libra_posting():
    info = banks.parse_libra_posting(_read("libra_job.html"))
    assert info["posted"] == "2026-10-29"
    assert "AML" in info["description"]


def test_parse_signal():
    jobs = {j.native_id: j for j in banks.parse_signal(_read("signal.html"))}
    job = jobs["signal:job-senior-compliance-officer"]
    assert job.title == "Senior Compliance Officer"
    assert job.location == "Romania"
    assert job.extra["department"] == "Juridic și Conformitate"
    info = banks.parse_signal_posting(_read("signal_job.html"))
    assert info["posted"] == "2026-07-09"
    assert "Candidatul potrivit" in info["description"]


def test_parse_netopia():
    jobs = banks.parse_netopia(_read("netopia.html"))
    assert len(jobs) == 2
    assert all(j.description and j.url.startswith("https://netopia-payments.com/joburi/") for j in jobs)
    assert jobs[0].native_id == "netopia:" + jobs[0].url.rstrip("/").rsplit("/", 1)[-1]


def test_parse_raiffeisen():
    jobs, last = banks.parse_raiffeisen(_read("raiffeisen.html"))
    assert last == 3
    assert len(jobs) == 2
    job = jobs[0]
    assert job.native_id == "raiffeisen:3921"
    assert job.title == "Expert Clienti PF Satu Mare"
    assert job.location == "Satu Mare, Romania"
    assert job.posted == "2026-10-28"
    assert job.company == "Raiffeisen Bank"


def test_parse_cec():
    jobs = banks.parse_cec(json.loads(_read("cec.json")))
    assert len(jobs) == 2
    assert jobs[0].native_id == "cec:Q_IgSQ"
    assert jobs[0].url == "https://cec.mingle.ro/ro/apply/Q_IgSQ"
    assert jobs[0].location == "Brasov, Romania"
    assert jobs[0].description


def test_parse_successfactors():
    jobs, total = banks.parse_successfactors(_read("bcr.html"), "bcr", "BCR", banks.BCR)
    assert total == 31 and len(jobs) == 3
    assert jobs[0].native_id == "bcr:1442661433"
    assert jobs[0].url == "https://erstegroup-careers.com/bcr/job/Bucuresti-Corporate-Relationship-Manager/1442661433/"
    assert jobs[0].location == "Bucuresti, Romania"
    jobs, total = banks.parse_successfactors(_read("groupama.html"), "groupama", "Groupama Asigurări", banks.GROUPAMA)
    assert total == 13
    assert jobs[0].url.startswith("https://cariere.groupama.ro/job/")
    assert jobs[0].location == "Bucuresti, Romania"
    info = banks.parse_successfactors_posting(_read("sf_job.html"))
    assert info["posted"] == "2026-10-09"
    assert info["description"]


def test_parse_unicredit():
    jobs, total = banks.parse_unicredit(_read("unicredit.html"))
    assert total == 97
    by_id = {j.native_id: j for j in jobs}
    job = by_id["unicredit:117157"]
    assert job.title == "Senior Digital Product Specialist_AML TxM_Compliance"
    assert job.location == "Romania"
    info = banks.parse_unicredit_posting(_read("unicredit_job.html"))
    assert info["city"] == "București, Iași"
    assert "AML" in info["description"]


def test_parse_tbi():
    jobs = {j.native_id: j for j in banks.parse_tbi(json.loads(_read("tbi.json")))}
    job = jobs["tbi:2802023"]
    assert job.location == "Bucuresti, București, Romania"
    assert job.posted == "2026-10-29"
    assert job.description
    remote = jobs["tbi:2407629"]
    assert remote.remote and remote.location == "Remote, Bucuresti, Romania"
