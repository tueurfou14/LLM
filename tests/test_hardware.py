from cerveau.hardware import Hardware, pick_tier


def test_macbook_air_m3_16gb_gets_small_tier():
    hw = Hardware(system="Darwin", machine="arm64", cpu="Apple M3", ram_gb=16.0, unified_memory=True)
    assert 8.0 <= hw.model_memory_gb <= 12.0
    assert pick_tier(hw).name == "small"


def test_mac_studio_64gb_gets_large_tier():
    hw = Hardware(system="Darwin", machine="arm64", cpu="Apple M2 Max", ram_gb=64.0, unified_memory=True)
    assert pick_tier(hw).name == "large"


def test_rtx_3060_gets_small_tier():
    hw = Hardware(system="Windows", machine="AMD64", cpu="i7", ram_gb=32.0, gpu="RTX 3060", vram_gb=12.0)
    assert pick_tier(hw).name == "small"


def test_rtx_4090_gets_large_tier():
    hw = Hardware(system="Windows", machine="AMD64", cpu="i9", ram_gb=64.0, gpu="RTX 4090", vram_gb=24.0)
    assert pick_tier(hw).name == "large"


def test_cpu_only_8gb_gets_tiny_tier():
    hw = Hardware(system="Linux", machine="x86_64", cpu="i5", ram_gb=8.0)
    assert pick_tier(hw).name == "tiny"
