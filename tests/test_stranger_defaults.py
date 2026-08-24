"""Stranger-simulation test set (PLAN.md "Verification before flipping public" step 1).

Everything else in this suite runs with the conftest autouse fixture that sets
MS_ADS_ENABLE_WRITES=true (Task C1) -- that's the "write-path regression tests" half of
the plan's required split. This module is the other half: a fresh install, no settings
file, no MS_ADS_* env flags at all. It asserts:

  1. Read tools still work (reads never touch the writes gate).
  2. Write tools refuse via the real draft-creation path, with the pointer message
     naming MS_ADS_ENABLE_WRITES.
  3. apply_recommendation refuses on its own second gate even once MS_ADS_ENABLE_WRITES
     is turned on, until MS_ADS_ALLOW_APPLY_RECOMMENDATION is also set (Task C3).
"""
import os
from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import (
    campaigns_write,
    extensions_write,
    insight,
    negatives_read,
    reads_misc,
)


@pytest.fixture(autouse=True)
def _no_ms_ads_env_at_all(monkeypatch):
    """Neutralize conftest's autouse _ms_ads_writes_enabled_by_default fixture (which sets
    MS_ADS_ENABLE_WRITES=true for the rest of the suite) and strip every other MS_ADS_*
    var too, simulating a stranger who just installed the server with no settings file
    and no flags. Module-level autouse fixtures instantiate after same-scope conftest
    autouse fixtures, so this reliably runs last and wins."""
    for name in list(os.environ):
        if name.startswith("MS_ADS_"):
            monkeypatch.delenv(name, raising=False)


# --- 1. Read tools work with no settings/flags at all ---

def test_read_tool_list_extensions_works(monkeypatch):
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetAdExtensionIdsByAccountId=lambda AccountId, AssociationType, AdExtensionType: NS(long=[71]),
        GetAdExtensionsByIds=lambda AccountId, AdExtensionIds, AdExtensionType: NS(
            AdExtensions=NS(AdExtension=[NS(Id=71, Type="SitelinkAdExtension", Status="Active",
                                            DisplayText="Coupons")])),
        factory=NS(create=lambda t: NS(long=None)),
    ))
    out = reads_misc.list_extensions()
    assert out["extensions"][0]["Type"] == "SitelinkAdExtension"


def test_read_tool_get_negative_keywords_works(monkeypatch):
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetNegativeKeywordsByEntityIds=lambda EntityIds, EntityType, ParentEntityId=None: NS(
            EntityNegativeKeywords=NS(EntityNegativeKeyword=[NS(
                EntityId=524066222, EntityType="Campaign",
                NegativeKeywords=NS(NegativeKeyword=[NS(Id=1, Text="sewage", MatchType="Phrase")]))])),
        GetSharedEntitiesByAccountId=lambda SharedEntityType: NS(
            SharedEntity=[NS(Id=900, Name="Master Negatives", Type="NegativeKeywordList")]),
        GetListItemsBySharedList=lambda SharedList: NS(
            SharedListItem=[NS(Id=2, Text="free", MatchType="Exact", Type="NegativeKeyword")]),
        GetSharedEntityAssociationsBySharedEntityIds=lambda EntityType, SharedEntityIds, SharedEntityType: NS(
            Associations=NS(SharedEntityAssociation=[NS(EntityId=524066222, SharedEntityId=900)])),
        factory=NS(create=lambda t: NS(long=None, Id=None, Type=None)),
    ))
    out = negatives_read.get_negative_keywords(campaign_id=524066222)
    assert out["campaign_negatives"]["524066222"][0]["Text"] == "sewage"
    assert out["shared_lists"][0]["name"] == "Master Negatives"


# --- 2. Write tools refuse with the pointer message, via the real draft path ---

def test_write_tool_draft_campaign_refuses(monkeypatch):
    assert "MS_ADS_ENABLE_WRITES" not in os.environ
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        campaigns_write.draft_campaign("z. test campaign", "Search", 10)


def test_write_tool_create_callouts_refuses(monkeypatch):
    assert "MS_ADS_ENABLE_WRITES" not in os.environ
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        extensions_write.create_callouts(524066223, ["Family Owned"])


# --- 3. apply_recommendation's second gate stays closed even with writes on ---

def test_apply_recommendation_refuses_without_second_flag(monkeypatch):
    monkeypatch.setattr(client, "svc", lambda name: NS(factory=NS(create=lambda t: NS())))
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "true")
    assert "MS_ADS_ALLOW_APPLY_RECOMMENDATION" not in os.environ
    with pytest.raises(rails.RailViolation, match="MS_ADS_ALLOW_APPLY_RECOMMENDATION"):
        insight.apply_recommendation("R1")
