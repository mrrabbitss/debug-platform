from app.services.rag import tokenize


def test_tokenizer_keeps_error_codes_and_symbols():
    tokens = tokenize("hostapd error -22 in wifi_set_channel")
    assert "hostapd" in tokens
    assert "-22" in tokens
    assert "wifi_set_channel" in tokens
