import tuya_local


def test_dps_101_energy_uses_the_standard_slash_1000_scale():
    # DPS 101 is the standard add_ele-equivalent slot at Tuya's declared
    # 0.001 kWh scale.
    result = tuya_local.parse_socket_status({"dps": {"101": 450}})
    assert result["energy"] == "0.45"


def test_dps_17_energy_uses_the_same_slash_100_scale_as_add_ele_everywhere_else():
    # DPS 17 is add_ele in Tuya's standard cz instruction set. Every other
    # add_ele site in this repo (tuya_sharing_api.py, server/units.py
    # SCALE_OVERRIDES) divides by 100 because the hardware disagrees with its
    # own declared scale of 3. This used to be lumped in with DPS 101's /1000,
    # which would have read the same raw value 10x low.
    result = tuya_local.parse_socket_status({"dps": {"17": 45}})
    assert result["energy"] == "0.45"
