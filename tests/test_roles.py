from server import roles


class FakeApi:
    """Returns canned responses keyed by the path prefix."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        for prefix, response in self.responses.items():
            if prefix in path:
                return response
        raise AssertionError(f"unexpected path {path}")


WORKING_SOCKET = {
    "/status": {"success": True, "result": {
        "dpStatusRelationDTOS": [{"dpId": 19, "statusCode": "cur_power"}]}},
    "/specifications": {"success": True, "result": {"status": [
        {"code": "cur_power", "values": '{"unit":"W","scale":1}'},
        {"code": "add_ele", "values": '{"unit":"kW·h","scale":3}'},
    ]}},
}

BROKEN_SENSOR = {
    "/status": {"success": True, "result": {"dpStatusRelationDTOS": []}},
    "/specifications": {"success": True, "result": {"status": []}},
}


def test_device_with_dp_map_uses_push():
    profile = roles.device_profile(FakeApi(WORKING_SOCKET), "dev1")
    assert profile["push"] is True


def test_scales_come_from_the_specification():
    profile = roles.device_profile(FakeApi(WORKING_SOCKET), "dev1")
    assert profile["scales"]["cur_power"] == 1
    # roles reports what Tuya declares, unedited. add_ele is declared as 3 and
    # that is wrong for this hardware, but correcting it is units.SCALE_OVERRIDES'
    # job, not this function's -- keep the two concerns apart.
    assert profile["scales"]["add_ele"] == 3


def test_device_with_empty_dp_map_uses_polling():
    profile = roles.device_profile(FakeApi(BROKEN_SENSOR), "dev2")
    assert profile["push"] is False


def test_broken_device_still_gets_fallback_scales():
    profile = roles.device_profile(FakeApi(BROKEN_SENSOR), "dev2")
    assert profile["scales"]["temp_current"] == 1


def test_api_failure_falls_back_to_polling_not_a_crash():
    class Broken:
        def get(self, path, params=None):
            raise RuntimeError("network down")

    profile = roles.device_profile(Broken(), "dev3")
    assert profile["push"] is False
    assert profile["scales"]["cur_power"] == 1


def test_classify_splits_the_two_groups():
    class Mixed:
        def get(self, path, params=None):
            source = WORKING_SOCKET if "dev1" in path else BROKEN_SENSOR
            for prefix, response in source.items():
                if prefix in path:
                    return response
            raise AssertionError(path)

    push, poll, scales = roles.classify(Mixed(), ["dev1", "dev2"])
    assert push == ["dev1"]
    assert poll == ["dev2"]
    assert set(scales) == {"dev1", "dev2"}
