from unittest.mock import patch

from dbus_fast import Variant

from proton.vpn.daemon.split_tunneling.service import SplitTunnelingDbus


def test_dbus_status_uses_stable_variant_fields():
    with patch("proton.vpn.daemon.split_tunneling.service.SplitTunnelingService") as service_type:
        interface = SplitTunnelingDbus()
    interface._service = service_type.return_value
    status_values = {
        "state": "launched",
        "unit": "proton-vpn-app.scope",
        "pid": 4242,
        "cgroup_inode": 12345,
    }

    status = interface._status_to_dbus(status_values)

    assert status == {
        "state": Variant("s", "launched"),
        "unit": Variant("s", "proton-vpn-app.scope"),
        "pid": Variant("u", 4242),
        "cgroup_inode": Variant("t", 12345),
    }