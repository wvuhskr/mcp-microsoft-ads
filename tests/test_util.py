from mcp_microsoft_ads.util import suds_to_dict


def test_suds_to_dict_passthrough():
    assert suds_to_dict({"a": [1, "x", None]}) == {"a": [1, "x", None]}
    assert suds_to_dict([1, 2]) == [1, 2]
    assert suds_to_dict(5) == 5

def test_suds_to_dict_suds_object():
    from suds.sudsobject import Object
    o = Object()
    o.Name = "camp"
    inner = Object()
    inner.Amount = 20.0
    o.Bid = inner
    assert suds_to_dict(o) == {"Name": "camp", "Bid": {"Amount": 20.0}}
