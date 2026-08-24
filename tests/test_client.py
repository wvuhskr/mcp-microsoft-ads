from types import SimpleNamespace as NS

from mcp_microsoft_ads import client


def test_partial_errors_none():
    assert client.partial_errors(NS()) == []
    assert client.partial_errors(NS(PartialErrors=None)) == []

def test_partial_errors_collected():
    resp = NS(PartialErrors=NS(BatchError=[
        NS(Index=0, ErrorCode="CampaignServiceInvalidBudget", Code=1113, Message="bad budget"),
        NS(Index=2, ErrorCode="X", Code=9, Message="y"),
    ]))
    errs = client.partial_errors(resp)
    assert len(errs) == 2
    assert errs[0] == {"index": 0, "code": "CampaignServiceInvalidBudget", "number": 1113, "message": "bad budget"}

def test_partial_errors_single_object():
    """suds unmarshals single-error arrays as bare objects, not lists."""
    resp = NS(PartialErrors=NS(BatchError=NS(Index=0, ErrorCode="BadValue", Code=105, Message="invalid")))
    errs = client.partial_errors(resp)
    assert len(errs) == 1
    assert errs[0] == {"index": 0, "code": "BadValue", "number": 105, "message": "invalid"}

def test_partial_errors_nested_none():
    """AddCampaignCriterions-shaped response with no errors on either field."""
    assert client.partial_errors(NS(NestedPartialErrors=None)) == []


def test_partial_errors_nested_single_collection():
    """suds unmarshals a single BatchErrorCollection as a bare object, not a list."""
    resp = NS(NestedPartialErrors=NS(BatchErrorCollection=NS(
        Index=1, ErrorCode="CriterionError", Code=4001, Message="overlap",
        BatchErrors=NS(BatchError=NS(Index=1, ErrorCode="Overlap", Code=4002, Message="time overlap")))))
    errs = client.partial_errors(resp)
    assert errs == [
        {"index": 1, "code": "CriterionError", "number": 4001, "message": "overlap"},
        {"index": 1, "code": "Overlap", "number": 4002, "message": "time overlap"},
    ]


def test_partial_errors_nested_list_of_collections():
    resp = NS(NestedPartialErrors=NS(BatchErrorCollection=[
        NS(Index=0, ErrorCode="C0", Code=1, Message="m0",
           BatchErrors=NS(BatchError=[NS(Index=0, ErrorCode="I0a", Code=11, Message="i0a"),
                                       NS(Index=0, ErrorCode="I0b", Code=12, Message="i0b")])),
        NS(Index=3, ErrorCode="C3", Code=2, Message="m3", BatchErrors=None),
    ]))
    errs = client.partial_errors(resp)
    assert len(errs) == 4
    assert errs[0] == {"index": 0, "code": "C0", "number": 1, "message": "m0"}
    assert errs[1] == {"index": 0, "code": "I0a", "number": 11, "message": "i0a"}
    assert errs[2] == {"index": 0, "code": "I0b", "number": 12, "message": "i0b"}
    assert errs[3] == {"index": 3, "code": "C3", "number": 2, "message": "m3"}


def test_partial_errors_stripped_flat():
    """suds strips one wrapper level when *Response declares exactly one child
    (bindings/binding.py::get_reply). For Update*/Delete* whose only declared child
    is PartialErrors, resp IS the ArrayOfBatchError itself — no .PartialErrors attr."""
    resp = NS(BatchError=[
        NS(Index=0, ErrorCode="CampaignServiceInvalidBudget", Code=1113, Message="bad budget"),
        NS(Index=2, ErrorCode="X", Code=9, Message="y"),
    ])
    errs = client.partial_errors(resp)
    assert len(errs) == 2
    assert errs[0] == {"index": 0, "code": "CampaignServiceInvalidBudget", "number": 1113, "message": "bad budget"}


def test_partial_errors_stripped_nested():
    """Same wrapper-strip, nested shape: resp IS the ArrayOfBatchErrorCollection."""
    resp = NS(BatchErrorCollection=NS(
        Index=1, ErrorCode="CriterionError", Code=4001, Message="overlap",
        BatchErrors=NS(BatchError=NS(Index=1, ErrorCode="Overlap", Code=4002, Message="time overlap"))))
    errs = client.partial_errors(resp)
    assert errs == [
        {"index": 1, "code": "CriterionError", "number": 4001, "message": "overlap"},
        {"index": 1, "code": "Overlap", "number": 4002, "message": "time overlap"},
    ]


def test_long_ids_skips_nils():
    """Add* responses are ArrayOfNullableOflong: a NIL entry marks a batch item that
    FAILED (error in PartialErrors, keyed by Index). int(None) must not raise."""
    assert client.long_ids(NS(long=[111, None, 333])) == [111, 333]


def test_svc_cached(monkeypatch):
    client.reset()
    made = []
    monkeypatch.setattr(client, "_authorization", lambda: "AUTH")
    monkeypatch.setattr(client, "ServiceClient",
                        lambda service, version, authorization_data, environment: made.append(service) or f"SVC-{service}")
    a = client.svc("CampaignManagementService")
    b = client.svc("CampaignManagementService")
    assert a is b and made == ["CampaignManagementService"]
