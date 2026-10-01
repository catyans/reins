from reins.projects.documents import FIELDS
from reins.projects.quality import SUMMARY_CONTRACT, assess, prose_supported


def test_summary_requires_provenance_and_factual_approval():
    views = {f: {"readme": "Tool reads files.\nIt supports CSV.\npip install tool"} for f in FIELDS}
    fields = {f: {"value": None, "source": None, "quote": None} for f in FIELDS}
    fields["purpose"] = {
        "value": "A CSV reader",
        "source": "readme",
        "quote": "Tool reads files. It supports CSV.",
    }
    scores = dict.fromkeys(FIELDS, True)
    assert not assess(fields, views, scores)["accepted"]
    assert assess(fields, views, scores, contract=SUMMARY_CONTRACT)["accepted"]
    scores["purpose"] = False
    assert not assess(fields, views, scores, contract=SUMMARY_CONTRACT)["accepted"]
    assert not assess(fields, views, {}, contract=SUMMARY_CONTRACT)["accepted"]


def test_commands_and_negation_are_not_normalized_away():
    answer = {"value": "A reader", "source": "readme", "quote": "Does support CSV"}
    assert not prose_supported(answer, {"readme": "Does not support CSV"})
    answer["quote"] = "use csv_reader"
    assert not prose_supported(answer, {"readme": "use csvreader"})
    fields = {f: {"value": None, "source": None, "quote": None} for f in FIELDS}
    fields["install_command"] = {
        "value": "pip install tool",
        "source": "readme",
        "quote": "pip install tool",
    }
    views = {f: {"readme": "pip install tool-dev"} for f in FIELDS}
    # Semantic correctness still rejects a misleading substring match.
    scores = dict.fromkeys(FIELDS, True)
    scores["install_command"] = False
    assert not assess(fields, views, scores, contract=SUMMARY_CONTRACT)["accepted"]
    views["install_command"] = {"readme": 'pip install "tool extra"'}
    fields["install_command"] = {
        "value": 'pip install "tool  extra"',
        "source": "readme",
        "quote": 'pip install "tool  extra"',
    }
    assert not assess(fields, views, dict.fromkeys(FIELDS, True), contract=SUMMARY_CONTRACT)[
        "accepted"
    ]
