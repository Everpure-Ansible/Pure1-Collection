# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_alerts module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import inspect
import time

import pytest
import yaml
from pypureclient import PureError

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


# The row of _make_alert() when the task names the array
ROW = {
    "component_type": "hardware",
    "component_name": "ct0.fan0",
    "code": 42,
    "category": "array",
    "summary": "Fan failure",
}


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

    def test_empty_name_selects_all_appliances(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, name="", severity="critical")

        pure1_client.get_alerts.assert_called_once_with(
            filter="severity='critical' and state='open'"
        )
        module.exit_json.assert_called_once_with(
            changed=False, alert_info={0: dict(ROW, appliance_name="foo")}
        )

    def test_appliance_name_is_the_first_array_of_the_alert(self, pure1_client):
        alert = _make_alert(
            arrays=[
                sdk_model("FixedReferenceFqdn", name="foo"),
                sdk_model("FixedReferenceFqdn", name="bar"),
            ]
        )
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_called_once_with(
            changed=False, alert_info={0: dict(ROW, appliance_name="foo")}
        )

    def test_alerts_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, check_mode=True, severity="info")

        pure1_client.get_alerts.assert_called_once_with(
            filter="severity='info' and state='open'"
        )
        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["code"] == 42

    def test_no_alerts_for_named_array_gives_an_empty_result(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="open")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, alert_info={})
        # the array is not looked up: this call is the check get_pure1 makes
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_no_alerts_for_all_appliances_gives_an_empty_result(self, pure1_client):
        pure1_client.get_alerts.return_value = valid_response([])

        # name and state are left to the argument spec
        module = run_main(pure1_alerts, severity="critical")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, alert_info={})

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_alerts.return_value = error_response(403, "Access denied")

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    @pytest.mark.parametrize(
        "name", [None, "foo"], ids=["all appliances", "named array"]
    )
    def test_error_answer_gives_the_message_of_pure1(self, pure1_client, name):
        pure1_client.get_alerts.return_value = error_response(
            400, "Illegal filter format", "Unexpected input at character 16"
        )

        module = run_main(pure1_alerts, name=name, severity="critical")

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_alerts failed (HTTP 400): "
            "Illegal filter format: Unexpected input at character 16"
        )

    def test_error_raised_by_the_sdk_fails_the_task(self, pure1_client):
        pure1_client.get_alerts.side_effect = PureError("connection lost")

        module = run_main(pure1_alerts, name="foo", severity="critical")

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_alerts failed: PureError: connection lost"
        )

    @pytest.mark.parametrize(
        "name, quoted",
        [("o'brien", "'o\\'brien'"), ("back\\slash", "'back\\\\slash'")],
        ids=["single quote", "backslash"],
    )
    def test_array_name_is_escaped_in_the_filter(self, pure1_client, name, quoted):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        run_main(pure1_alerts, name=name, severity="critical")

        pure1_client.get_alerts.assert_called_once_with(
            filter="arrays.name=" + quoted + " and severity='critical' and state='open'"
        )

    @pytest.mark.parametrize(
        "name, alert_filter",
        [
            (None, "severity='warning' and state='closing'"),
            ("foo", "arrays.name='foo' and severity='warning' and state='closing'"),
        ],
        ids=["all appliances", "named array"],
    )
    def test_closing_alerts_can_be_selected(self, pure1_client, name, alert_filter):
        pure1_client.get_alerts.return_value = valid_response([_make_alert()])

        module = run_main(pure1_alerts, name=name, severity="warning", state="closing")

        pure1_client.get_alerts.assert_called_once_with(filter=alert_filter)
        assert module.exit_json.call_args[1]["alert_info"][0]["code"] == 42

    def test_state_refuses_a_value_that_is_no_choice(self, pure1_client):
        module = run_main(pure1_alerts, severity="warning", state="not_closed")

        module.fail_json.assert_called_once_with(
            msg="value of state must be one of: open, closing, closed, got: not_closed"
        )
        pure1_client.get_alerts.assert_not_called()

    def test_severity_refuses_a_value_that_is_no_choice(self, pure1_client):
        module = run_main(pure1_alerts, severity="bogus")

        module.fail_json.assert_called_once_with(
            msg="value of severity must be one of: "
            "info, warning, critical, hidden, got: bogus"
        )
        pure1_client.get_alerts.assert_not_called()

    def test_severity_is_required(self, pure1_client):
        module = run_main(pure1_alerts, name="foo")

        module.fail_json.assert_called_once_with(
            msg="missing required arguments: severity"
        )
        pure1_client.get_alerts.assert_not_called()

    @pytest.mark.parametrize("state", ["open", "closing"])
    def test_closed_time_is_returned_whatever_the_state(
        self, pure1_client, monkeypatch, state
    ):
        monkeypatch.setattr(pure1_alerts.time, "localtime", time.gmtime)
        alert = _make_alert(closed=1612357445000)
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, name="foo", severity="critical", state=state)

        alert_info = module.exit_json.call_args[1]["alert_info"]
        assert alert_info[0]["closed"] == "2021-02-03 13:04:05"

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_row_holds_each_time_as_local_time_and_as_epoch(self, pure1_client):
        """The strings are in the time of the host, here 13 hours ahead of UTC."""
        alert = _make_alert(
            created=1612357445000,
            updated=1612443906000,
            notified=1612530367000,
            closed=1612616828000,
        )
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="closed")

        module.exit_json.assert_called_once_with(
            changed=False,
            alert_info={
                0: dict(
                    ROW,
                    created="2021-02-04 02:04:05",
                    created_epoch=1612357445000,
                    updated="2021-02-05 02:05:06",
                    updated_epoch=1612443906000,
                    notified="2021-02-06 02:06:07",
                    notified_epoch=1612530367000,
                    closed="2021-02-07 02:07:08",
                    closed_epoch=1612616828000,
                )
            },
        )

    @pytest.mark.parametrize("field", ["created", "updated", "notified", "closed"])
    def test_epoch_key_is_there_exactly_when_its_time_is(
        self, pure1_client, monkeypatch, field
    ):
        """_make_alert() gives three times as 0 and no closed time."""
        monkeypatch.setattr(pure1_alerts.time, "localtime", time.gmtime)
        alert = _make_alert(**{field: 1612357445000})
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, name="foo", severity="critical", state="closed")

        row = dict(ROW)
        row[field] = "2021-02-03 13:04:05"
        row[field + "_epoch"] = 1612357445000
        module.exit_json.assert_called_once_with(changed=False, alert_info={0: row})

    def test_alert_with_no_field_set_gives_null_values(self, pure1_client):
        """Pure1 leaves out what an alert does not have; the others still follow."""
        pure1_client.get_alerts.return_value = valid_response(
            [sdk_model("Alert"), _make_alert()]
        )

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_called_once_with(
            changed=False,
            alert_info={
                0: {
                    "component_type": None,
                    "component_name": None,
                    "code": None,
                    "category": None,
                    "summary": None,
                    "appliance_name": None,
                },
                1: dict(ROW, appliance_name="foo"),
            },
        )

    @pytest.mark.parametrize("field", ["code", "summary"])
    def test_alert_without_a_code_or_a_summary_has_null_there(
        self, pure1_client, field
    ):
        alert = _make_alert(**{field: None})
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, name="foo", severity="critical")

        row = dict(ROW)
        row[field] = None
        module.exit_json.assert_called_once_with(changed=False, alert_info={0: row})

    @pytest.mark.parametrize(
        "arrays",
        [None, [], [sdk_model("FixedReferenceFqdn")]],
        ids=["no list", "empty list", "reference without a name"],
    )
    def test_alert_without_an_array_has_a_null_appliance_name(
        self, pure1_client, arrays
    ):
        alert = _make_alert(arrays=arrays)
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_called_once_with(
            changed=False, alert_info={0: dict(ROW, appliance_name=None)}
        )

    def test_null_entry_in_the_arrays_gives_a_null_appliance_name(self, pure1_client):
        alert = _make_alert(arrays=[])
        # The SDK reads a response without validating it, so a null entry of
        # the list arrives as None; the constructor of the model refuses one
        alert.arrays.append(None)
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, severity="critical")

        module.exit_json.assert_called_once_with(
            changed=False, alert_info={0: dict(ROW, appliance_name=None)}
        )


class TestPure1AlertsDocumentation:
    """The documentation of the module against what the module does."""

    def test_return_documents_every_key_of_a_row(self, pure1_client):
        alert = _make_alert(
            created=1612357445000,
            updated=1612443906000,
            notified=1612530367000,
            closed=1612616828000,
        )
        pure1_client.get_alerts.return_value = valid_response([alert])

        module = run_main(pure1_alerts, severity="critical")

        row = module.exit_json.call_args[1]["alert_info"][0]
        documented = yaml.safe_load(pure1_alerts.RETURN)["alert_info"]
        assert sorted(documented["contains"]) == sorted(row)
        assert sorted(documented["sample"]["0"]) == sorted(row)

    def test_return_says_what_an_alert_without_values_gives(self, pure1_client):
        """Null for a value Pure1 did not give, no key for a time it did not give."""
        pure1_client.get_alerts.return_value = valid_response([sdk_model("Alert")])

        module = run_main(pure1_alerts, severity="critical")

        row = module.exit_json.call_args[1]["alert_info"][0]
        documented = yaml.safe_load(pure1_alerts.RETURN)["alert_info"]
        for key, entry in documented["contains"].items():
            is_a_time = entry["returned"] == "when the alert has that time"
            assert (key not in row) == is_a_time, key
        assert "A time that the alert does not have is left out" in " ".join(
            documented["description"]
        )

    def test_time_strings_say_which_time_zone_they_are_in(self):
        returned = yaml.safe_load(pure1_alerts.RETURN)["alert_info"]["contains"]

        for field in ("created", "updated", "notified", "closed"):
            assert (
                "in the time zone of the host that runs the module"
                in returned[field]["description"]
            ), field

    def test_what_is_new_in_1_6_0_says_so(self):
        options = yaml.safe_load(pure1_alerts.DOCUMENTATION)["options"]
        returned = yaml.safe_load(pure1_alerts.RETURN)["alert_info"]["contains"]
        new = {"state": options["state"]["description"]}
        for field in ("created", "updated", "notified", "closed"):
            new[field + "_epoch"] = returned[field + "_epoch"]["description"]

        for name, description in new.items():
            assert "Added in version 1.6.0." in " ".join(description), name

    def test_examples_register_at_task_level_and_show_the_credentials(self):
        tasks = yaml.safe_load(pure1_alerts.EXAMPLES)
        calls = [task for task in tasks if "everpure.pure1.pure1_alerts" in task]
        registered = [task["register"] for task in calls]
        text = " ".join(str(task) for task in tasks if task not in calls)

        assert calls[0]["everpure.pure1.pure1_alerts"]["app_id"] == (
            "pure1:apikey:dssf2331sd"
        )
        assert calls[0]["everpure.pure1.pure1_alerts"]["key_file"] == (
            "/home/user/private.pem"
        )
        for task in calls:
            assert "register" not in task["everpure.pure1.pure1_alerts"]
        # every result is read by a later task
        assert registered and all(name in text for name in registered)

    @pytest.mark.parametrize(
        "words", ["fleet", "drtails", "with be used", "stae", "arrayt"]
    )
    def test_module_text_is_free_of(self, words):
        """A fleet is a Pure1 fleet, not all appliances; the others are misspellings."""
        assert words not in inspect.getsource(pure1_alerts).lower()

    def test_module_has_no_metadata_block(self):
        assert not hasattr(pure1_alerts, "ANSIBLE_METADATA")
