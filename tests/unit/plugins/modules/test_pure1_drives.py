# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_drives module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from plugins.modules import pure1_drives
from plugins.modules.pure1_drives import generate_drives_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _make_drive(array_name="array1", **overrides):
    fields = dict(
        name="drive0",
        capacity=1099511627776,
        protocol="NVMe",
        status="healthy",
        type="SSD",
        arrays=[sdk_model("DriveArrayStatus", name=array_name)],
    )
    fields.update(overrides)
    return sdk_model("Drive", **fields)


class TestGenerateDrivesDict:
    """Tests for generate_drives_dict."""

    def test_basic_mapping_grouped_by_array(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([_make_drive()])

        result = generate_drives_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_drives.assert_called_once_with()
        assert "array1" in result
        assert result["array1"] == [
            {
                "drive0": {
                    "capacity": 1099511627776,
                    "protocol": "NVMe",
                    "status": "healthy",
                    "type": "SSD",
                }
            }
        ]

    def test_array_filter_applied_with_results(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([_make_drive("myarray")])

        result = generate_drives_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_drives.assert_called_once_with(filter="arrays.name='myarray'")
        assert "myarray" in result

    def test_array_filter_no_results_warns_and_exits(self, pure1_client):
        module = ansible_module(array="myarray")
        pure1_client.get_drives.return_value = valid_response([])

        with pytest.raises(SystemExit):
            generate_drives_dict(module, pure1_client)

        module.warn.assert_called_once()
        assert module.warn.call_args[0][0] == (
            "No drives information available for array myarray"
        )
        module.exit_json.assert_called_once_with(changed=False)

    def test_multiple_drives_same_array_grouped(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response(
            [
                _make_drive("array1", name="drive0"),
                _make_drive("array1", name="drive1"),
            ]
        )

        result = generate_drives_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1"}
        assert len(result["array1"]) == 2

    def test_missing_attributes_default_to_none(self, pure1_client):
        bare = sdk_model(
            "Drive",
            name="drive0",
            arrays=[sdk_model("DriveArrayStatus", name="array1")],
        )
        pure1_client.get_drives.return_value = valid_response([bare])

        result = generate_drives_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["drive0"] == {
            "capacity": None,
            "protocol": None,
            "status": None,
            "type": None,
        }

    def test_each_array_holds_its_own_drives(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response(
            [
                _make_drive("array1", name="drive0"),
                _make_drive("array2", name="drive1", status="failed"),
                _make_drive("array1", name="drive2"),
            ]
        )

        result = generate_drives_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}
        assert [list(drive) for drive in result["array1"]] == [["drive0"], ["drive2"]]
        assert [list(drive) for drive in result["array2"]] == [["drive1"]]
        assert result["array2"][0]["drive1"]["status"] == "failed"


class TestPure1DrivesMain:
    """Tests for the pure1_drives main() flow."""

    def test_all_drives_are_returned(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([_make_drive()])

        # array is left to the argument spec
        module = run_main(pure1_drives)

        pure1_client.get_drives.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_drives={
                "drives": {
                    "array1": [
                        {
                            "drive0": {
                                "capacity": 1099511627776,
                                "protocol": "NVMe",
                                "status": "healthy",
                                "type": "SSD",
                            }
                        }
                    ]
                }
            },
        )

    def test_drives_of_named_array_are_returned(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([_make_drive("myarray")])

        module = run_main(pure1_drives, array="myarray")

        pure1_client.get_drives.assert_called_once_with(filter="arrays.name='myarray'")
        module.warn.assert_not_called()
        drives = module.exit_json.call_args[1]["pure1_drives"]["drives"]
        assert list(drives) == ["myarray"]
        assert list(drives["myarray"][0]) == ["drive0"]

    def test_drives_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([_make_drive()])

        module = run_main(pure1_drives, check_mode=True)

        pure1_client.get_drives.assert_called_once_with()
        drives = module.exit_json.call_args[1]["pure1_drives"]["drives"]
        assert drives["array1"][0]["drive0"]["status"] == "healthy"

    def test_no_drives_gives_an_empty_result(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([])

        module = run_main(pure1_drives)

        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_drives={"drives": {}}
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_drives reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_drives.return_value = error_response(403, "Access denied")

        module = run_main(pure1_drives)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="D062 an API error for a named array ends ok with a warning",
    )
    def test_api_error_for_named_array_fails(self, pure1_client):
        pure1_client.get_drives.return_value = error_response(403, "Access denied")

        module = run_main(pure1_drives, array="myarray")

        module.fail_json.assert_called_once()
        assert "Access denied" in module.fail_json.call_args[1]["msg"]
        module.warn.assert_not_called()
