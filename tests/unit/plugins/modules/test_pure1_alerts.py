# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_alerts module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import time

import pytest

from plugins.modules import pure1_alerts
from tests.unit.conftest import error_response, run_main, sdk_model, valid_response


def _make_alert(**overrides):
    fields = dict(
        component_type="hardware",
        component_name="ct0.fan0",
        code=42,
        category="array",
        summary="Fan failure",
        created=0,
        updated=0,
        notified=0,
        arrays=[sdk_model("FixedReferenceFqdn", name="foo")],
    )
    fields.update(overrides)
    return sdk_model("Alert", **fields)


class TestPure1AlertsMain:
    """Tests for the pure1_alerts main() flow."""

    def test_named_array_alerts_are_collected(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="open")

        pure1_client.get_alerts.assert_called_once_with(
            filter="arrays.name='foo' and severity='critical' and state='open'"
        )
        module.exit_json.assert_called_once()
        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["code"] == 42
        assert alert_info[0]["summary"] == "Fan failure"
        # appliance_name is only added for fleet-wide (no name) queries
        assert "appliance_name" not in alert_info[0]

    def test_alert_row_holds_the_fields_of_the_alert(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="open")

        module.exit_json.assert_called_once_with(
            changed=False,
            alert_info={
                0: {
                    "component_type": "hardware",
                    "component_name": "ct0.fan0",
                    "code": 42,
                    "category": "array",
                    "summary": "Fan failure",
                }
            },
        )

    def test_closed_alert_row_holds_its_times(self, pure1_client, monkeypatch):
        """Pure1 gives a time in milliseconds; the row holds it as local time."""
        monkeypatch.setattr(pure1_alerts.time, "localtime", time.gmtime)
        alert = _make_alert(
            created=1787330499000,
            updated=1787330500000,
            notified=1787330501000,
            closed=1787330582000,
        )
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="closed")

        pure1_client.get_alerts.assert_called_once_with(
            filter="arrays.name='foo' and severity='critical' and state='closed'"
        )
        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["created"] == "2026-08-21 16:41:39"
        assert alert_info[0]["updated"] == "2026-08-21 16:41:40"
        assert alert_info[0]["notified"] == "2026-08-21 16:41:41"
        assert alert_info[0]["closed"] == "2026-08-21 16:43:02"

    def test_fleet_alerts_include_appliance_name(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, name=None, severity="warning", state="open")

        pure1_client.get_alerts.assert_called_once_with(
            filter="severity='warning' and state='open'"
        )
        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["appliance_name"] == "foo"

    def test_alerts_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, check_mode=True, severity="info")

        pure1_client.get_alerts.assert_called_once_with(
            filter="severity='info' and state='open'"
        )
        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["code"] == 42

    def test_no_alerts_for_named_array_fails(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="open")

        module.fail_json.assert_called_once()
        # the message shows that the module failed, not get_pure1 before it
        assert module.fail_json.call_args[1]["msg"] == (
            "No open alerts of severity critical for array foo found."
        )

    def test_no_alerts_for_fleet_fails(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([])

        # name and state are left to the argument spec
        module = run_main(pure1_alerts, severity="critical")

        module.fail_json.assert_called_once_with(
            msg="Failed to get any open alerts of severity critical for the fleet."
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_alerts reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_alerts.return_value = error_response(403, "Access denied")

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message
