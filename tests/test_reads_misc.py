from types import SimpleNamespace as NS

from mcp_microsoft_ads import client
from mcp_microsoft_ads.tools import reads_misc


def fake_svc(name):
    return NS(
        # live-probed: response is the ArrayOflong itself (r.long), no AdExtensionIds wrapper
        # — same unwrapping suds does for GetSharedEntitiesByAccountId (negatives_read.py).
        GetAdExtensionIdsByAccountId=lambda AccountId, AssociationType, AdExtensionType: NS(
            long=[71]),
        GetAdExtensionsByIds=lambda AccountId, AdExtensionIds, AdExtensionType: NS(
            AdExtensions=NS(AdExtension=[NS(Id=71, Type="SitelinkAdExtension", Status="Active",
                                            DisplayText="Coupons")])),
        GetConversionGoalsByIds=lambda ConversionGoalIds, ConversionGoalTypes: NS(
            ConversionGoals=NS(ConversionGoal=[NS(Id=42, Name="CRM Booked Job", Type="OfflineConversion",
                                                  Status="Active")])),
        # WSDL-verified (campaignmanagement_service.xml): both responses wrap their array in a
        # named field (Ads/Keywords) matching the ArrayOfAd/ArrayOfKeyword complexType — brief's
        # fake had these unwrapped directly onto Ad/Keyword, which does not match the schema.
        # live-probed: AdTypes is a required kwarg (live faults without it despite minOccurs=0).
        # stripped shape: GetAdsByEditorialStatusResponse/GetKeywordsByEditorialStatusResponse
        # each declare exactly one child (Ads/Keywords) so suds unwraps it away — resp IS
        # the ArrayOfAd/ArrayOfKeyword itself (resp.Ad / resp.Keyword directly).
        GetAdsByEditorialStatus=lambda AdGroupId, EditorialStatus, AdTypes: NS(
            Ad=[NS(Id=7, EditorialStatus="Disapproved")]),
        GetKeywordsByEditorialStatus=lambda AdGroupId, EditorialStatus: NS(
            Keyword=[]),
        factory=NS(create=lambda t: NS(long=None, string=None, AdType=None)),
    )

def test_extensions(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    out = reads_misc.list_extensions()
    assert out["extensions"][0]["Type"] == "SitelinkAdExtension"

def test_conversion_actions(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_svc)
    out = reads_misc.get_conversion_actions()
    assert out["goals"][0]["Name"] == "CRM Booked Job"

def test_policy_issues(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_svc)
    out = reads_misc.get_policy_issues(ad_group_id=111)
    assert out["disapproved_ads"][0]["Id"] == 7
    assert out["disapproved_keywords"] == []

def test_extensions_batches_over_100(monkeypatch):
    """GetAdExtensionsByIds caps at 100 ids/call — 150 ids must produce 2 calls, 150 results."""
    all_ids = list(range(150))
    calls = []

    def fake_by_ids(AccountId, AdExtensionIds, AdExtensionType):
        chunk = AdExtensionIds.long
        calls.append(list(chunk))
        return NS(AdExtensions=NS(AdExtension=[
            NS(Id=i, Type="SitelinkAdExtension", Status="Active") for i in chunk]))

    def fake_batch_svc(name):
        return NS(
            GetAdExtensionIdsByAccountId=lambda AccountId, AssociationType, AdExtensionType: NS(
                long=all_ids),
            GetAdExtensionsByIds=fake_by_ids,
            factory=NS(create=lambda t: NS(long=None)),
        )

    monkeypatch.setattr(client, "svc", fake_batch_svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    out = reads_misc.list_extensions()
    assert len(calls) == 2
    assert len(calls[0]) == 100
    assert len(calls[1]) == 50
    assert len(out["extensions"]) == 150

def test_extensions_campaign_id_filtering(monkeypatch):
    # live-probed: AdExtensionAssociationCollection is double-nested — the response field is an
    # ArrayOfAdExtensionAssociationCollection wrapper whose own list field is (confusingly) also
    # named AdExtensionAssociationCollection. Matches the brief's original fake shape.
    def fake_assoc_svc(name):
        return NS(
            GetAdExtensionsAssociations=lambda AccountId, AdExtensionType, AssociationType, EntityIds: NS(
                AdExtensionAssociationCollection=NS(
                    AdExtensionAssociationCollection=[NS(
                        AdExtensionAssociations=NS(AdExtensionAssociation=[NS(
                            AdExtension=NS(Id=71, Type="SitelinkAdExtension", Status="Active",
                                           DisplayText="Coupons"),
                            AssociationType="Campaign", EntityId=524066222)]))])),
            factory=NS(create=lambda t: NS(long=None)),
        )

    monkeypatch.setattr(client, "svc", fake_assoc_svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    out = reads_misc.list_extensions(campaign_id=524066222)
    assert out["extensions"][0]["Id"] == 71
    assert out["extensions"][0]["DisplayText"] == "Coupons"

def test_bare_single_item_shapes(monkeypatch):
    """One extension / one conversion goal collapses to a bare object (client.as_list)."""
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetAdExtensionIdsByAccountId=lambda AccountId, AssociationType, AdExtensionType: NS(long=71),
        GetAdExtensionsByIds=lambda AccountId, AdExtensionIds, AdExtensionType: NS(
            AdExtensions=NS(AdExtension=NS(Id=71, Type="SitelinkAdExtension", DisplayText="Coupons"))),
        GetConversionGoalsByIds=lambda ConversionGoalIds, ConversionGoalTypes: NS(
            ConversionGoals=NS(ConversionGoal=NS(Id=42, Name="CRM Booked Job", Type="OfflineConversion"))),
        factory=NS(create=lambda t: NS(long=None, string=None, AdType=None)),
    ))
    assert reads_misc.list_extensions()["extensions"][0]["Id"] == 71
    assert reads_misc.get_conversion_actions()["goals"][0]["Name"] == "CRM Booked Job"
