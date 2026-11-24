# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_drives module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from pypureclient import ErrorResponse, PureError

from plugins.modules import pure1_drives
from plugins.modules.pure1_drives import generate_drives_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

DRIVE0 = {
    "drive0": {
        "capacity": 1099511627776,
        "protocol": "NVMe",
        "status": "healthy",
        "type": "SSD",
    }
}


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


def _drive_without_name():
    return sdk_model("Drive", arrays=[sdk_model("DriveArrayStatus", name="array1")])


def _drive_without_arrays():
    return sdk_model("Drive", name="drive1")


def _drive_with_empty_arrays():
    # The SDK reads a response without validating it, so an empty list
    # arrives as it is; the constructor of the model refuses one
    drive = _make_drive(name="drive1")
    del drive.arrays[:]
    return drive


def _drive_with_null_array():
    drive = _make_drive(name="drive1")
    drive.arrays[0] = None
    return drive


def _drive_on_array_without_name():
    return _make_drive(
        name="drive1", arrays=[sdk_model("DriveArrayStatus", fqdn="a.example.com")]
    )


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

    @pytest.mark.parametrize(
        "array, expected",
        [
            pytest.param("o'brien", "arrays.name='o\\'brien'", id="single quote"),
            pytest.param("back\\slash", "arrays.name='back\\\\slash'", id="backslash"),
        ],
    )
    def test_array_name_is_escaped_in_the_filter(self, pure1_client, array, expected):
        module = ansible_module(array=array)
        pure1_client.get_drives.return_value = valid_response([])

        result = generate_drives_dict(module, pure1_client)

        pure1_client.get_drives.assert_called_once_with(filter=expected)
        # the warning shows the name as the task gave it
        assert result == {}
        module.warn.assert_called_once_with(
            "No drives information available for array " + array
        )

    def test_array_filter_no_results_warns_and_exits(self, pure1_client):
        """The warning stays; main() exits with what is returned here."""
        module = ansible_module(array="myarray")
        pure1_client.get_drives.return_value = valid_response([])

        result = generate_drives_dict(module, pure1_client)

        assert result == {}
        module.warn.assert_called_once()
        assert module.warn.call_args[0][0] == (
            "No drives information available for array myarray"
        )
        module.exit_json.assert_not_called()

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

    def test_drive_with_two_array_references_is_filed_under_the_first(
        self, pure1_client
    ):
        drive = _make_drive(
            arrays=[
                sdk_model("DriveArrayStatus", name="array1"),
                sdk_model("DriveArrayStatus", name="array2"),
            ]
        )
        pure1_client.get_drives.return_value = valid_response([drive])

        result = generate_drives_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [DRIVE0]}

    @pytest.mark.parametrize(
        "array, name",
        [
            pytest.param("array1", "", id="empty name"),
            pytest.param("", "drive0", id="empty array name"),
        ],
    )
    def test_drive_with_an_empty_name_is_filed(self, pure1_client, array, name):
        """Only a name that Pure1 did not send leaves a drive out."""
        module = ansible_module(array=None)
        pure1_client.get_drives.return_value = valid_response(
            [_make_drive(array, name=name)]
        )

        result = generate_drives_dict(module, pure1_client)

        assert result == {array: [{name: DRIVE0["drive0"]}]}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_drive_without_name, id="no name"),
            pytest.param(_drive_without_arrays, id="no arrays"),
            pytest.param(_drive_with_empty_arrays, id="empty arrays"),
            pytest.param(_drive_with_null_array, id="null array"),
            pytest.param(_drive_on_array_without_name, id="array without name"),
        ],
    )
    def test_drive_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = ansible_module(array=None)
        pure1_client.get_drives.return_value = valid_response([broken(), _make_drive()])

        result = generate_drives_dict(module, pure1_client)

        assert result == {"array1": [DRIVE0]}
        module.warn.assert_called_once_with(
            "Drives that Pure1 returned without a name or without an array "
            "are left out of the result: 1"
        )

    def test_drives_left_out_give_one_warning(self, pure1_client):
        module = ansible_module(array="array1")
        pure1_client.get_drives.return_value = valid_response(
            [_drive_without_name(), _make_drive(), _drive_without_arrays()]
        )

        result = generate_drives_dict(module, pure1_client)

        assert result == {"array1": [DRIVE0]}
        module.warn.assert_called_once_with(
            "Drives that Pure1 returned without a name or without an array "
            "are left out of the result: 2"
        )


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

    def test_named_array_without_drives_gives_an_empty_result(self, pure1_client):
        pure1_client.get_drives.return_value = valid_response([])

        module = run_main(pure1_drives, array="myarray")

        pure1_client.get_drives.assert_called_once_with(filter="arrays.name='myarray'")
        module.fail_json.assert_not_called()
        module.warn.assert_called_once_with(
            "No drives information available for array myarray"
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_drives={"drives": {}}
        )

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_drives.return_value = error_response(403, "Access denied")

        module = run_main(pure1_drives)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    def test_api_error_for_named_array_fails(self, pure1_client):
        pure1_client.get_drives.return_value = error_response(403, "Access denied")

        module = run_main(pure1_drives, array="myarray")

        module.fail_json.assert_called_once()
        assert "Access denied" in module.fail_json.call_args[1]["msg"]
        module.warn.assert_not_called()

    @pytest.mark.parametrize("params", [{}, {"array": "myarray"}], ids=["all", "named"])
    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request get_drives failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "Pure1 request get_drives failed (HTTP 400): Illegal filter "
                "format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request get_drives failed (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_drives failed: PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request get_drives failed: "
                "TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, params, attribute, answer, expected
    ):
        setattr(pure1_client.get_drives, attribute, answer)

        module = run_main(pure1_drives, **params)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
