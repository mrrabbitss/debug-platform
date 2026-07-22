from app.services.storage import normalize_debug_log_filename


def test_normalize_debug_log_filename_adds_txt_to_extensionless_name():
    original, normalized = normalize_debug_log_filename(
        "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    )

    assert original == "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490"
    assert normalized == "001122334455_GW_collectDebuginfo_2026_03_02_11_22_490.txt"


def test_normalize_debug_log_filename_preserves_existing_suffix_and_removes_path():
    original, normalized = normalize_debug_log_filename(r"C:\fakepath\device.log")

    assert original == "device.log"
    assert normalized == "device.log"
