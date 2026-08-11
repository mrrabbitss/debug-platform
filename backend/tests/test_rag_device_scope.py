from app.services.rag import knowledge_matches_device_type


def test_general_knowledge_matches_gw_and_ap_device_scopes() -> None:
    assert knowledge_matches_device_type("GENERAL", "GW") is True
    assert knowledge_matches_device_type("GENERAL", "AP") is True
    assert knowledge_matches_device_type("general", "AP") is True


def test_legacy_other_knowledge_remains_shared() -> None:
    assert knowledge_matches_device_type("OTHER", "GW") is True
    assert knowledge_matches_device_type("OTHER", "AP") is True


def test_specific_knowledge_does_not_cross_device_scopes() -> None:
    assert knowledge_matches_device_type("GW", "GW") is True
    assert knowledge_matches_device_type("AP", "AP") is True
    assert knowledge_matches_device_type("GW", "AP") is False
    assert knowledge_matches_device_type("AP", "GW") is False
    assert knowledge_matches_device_type(None, "GW") is True
    assert knowledge_matches_device_type("GW", None) is True
