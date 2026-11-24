# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_volumes module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from plugins.modules import pure1_volumes
from plugins.modules.pure1_volumes import generate_volumes_dict
from tests.unit.conftest import (
    ansible_module,
    array_reference,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _make_volume(**overrides):
    """Build a minimal Pure1 volume object as returned by the SDK."""
    fields = dict(
        id="abcd-1234",
        serial="ABC123",
        name="vol1",
        created=1609459200000,  # 2021-01-01 00:00:00 UTC, in milliseconds
        eradicated=False,
        destroyed=False,
        provisioned=1073741824,
        arrays=[array_reference("array1", fqdn="array1.example.com")],
    )
    fields.update(overrides)
    return sdk_model("Volume", **fields)


# The row of the volume that _make_volume() builds
VOL1 = {
    "id": "abcd-1234",
    "name": "vol1",
    "created": "2021-01-01 00:00:00 UTC",
    "eradicated": False,
    "destroyed": False,
    "provisioned": 1073741824,
    "source": [],
    "serial": "ABC123",
    "pod": [],
    "array": {"name": "array1", "fqdn": "array1.example.com"},
    "arrays": [{"name": "array1", "fqdn": "array1.example.com"}],
}


class TestGenerateVolumesDict:
    """Tests for generate_volumes_dict."""

    def test_basic_volume_mapping(self, pure1_client):
        """A volume is keyed by serial with its core fields mapped."""
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_volumes.assert_called_once_with()
        assert "ABC123" in result
        entry = result["ABC123"]
        assert entry["id"] == "abcd-1234"
        assert entry["name"] == "vol1"
        assert entry["created"] == "2021-01-01 00:00:00 UTC"
        assert entry["provisioned"] == 1073741824
        assert entry["array"] == {
            "name": "array1",
            "fqdn": "array1.example.com",
        }
        assert entry["arrays"] == [
            {"name": "array1", "fqdn": "array1.example.com"},
        ]

    def test_volume_spanning_multiple_arrays(self, pure1_client):
        """A volume on more than one array lists them all under arrays."""
        volume = _make_volume(
            arrays=[
                array_reference("array1", fqdn="array1.example.com"),
                array_reference("array2", fqdn="array2.example.com"),
            ],
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        entry = result["ABC123"]
        # array (singular) stays the first array for backwards compatibility
        assert entry["array"] == {"name": "array1", "fqdn": "array1.example.com"}
        assert entry["arrays"] == [
            {"name": "array1", "fqdn": "array1.example.com"},
            {"name": "array2", "fqdn": "array2.example.com"},
        ]

    def test_array_without_fqdn(self, pure1_client):
        """An array reference without an fqdn is reported with fqdn None."""
        volume = _make_volume(arrays=[array_reference("array1")])
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        entry = result["ABC123"]
        assert entry["array"] == {"name": "array1", "fqdn": None}
        assert entry["arrays"] == [{"name": "array1", "fqdn": None}]

    def test_no_source_or_pod_defaults_to_empty_list(self, pure1_client):
        """Without a source/pod the fields stay as empty lists."""
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"]["source"] == []
        assert result["ABC123"]["pod"] == []

    def test_source_and_pod_are_unwrapped_to_names(self, pure1_client):
        """A present source/pod is reduced to its name."""
        volume = _make_volume(
            source=sdk_model("FixedReference", name="parentvol"),
            pod=sdk_model("FixedReference", name="pod1"),
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"]["source"] == "parentvol"
        assert result["ABC123"]["pod"] == "pod1"

    def test_array_filter_is_applied(self, pure1_client):
        """The array param builds a Pure1 filter on arrays.name."""
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        generate_volumes_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_volumes.assert_called_once_with(filter="arrays.name='myarray'")

    def test_array_name_is_escaped_in_the_filter(self, pure1_client):
        """A quote or backslash in the array name cannot break the filter."""
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        generate_volumes_dict(ansible_module(array="my'arr\\ay"), pure1_client)

        pure1_client.get_volumes.assert_called_once_with(
            filter="arrays.name='my\\'arr\\\\ay'"
        )

    def test_multiple_volumes(self, pure1_client):
        """Every returned volume gets its own entry keyed by serial."""
        volumes = [
            _make_volume(serial="SERIAL1", name="vol-a"),
            _make_volume(serial="SERIAL2", name="vol-b"),
        ]
        pure1_client.get_volumes.return_value = valid_response(volumes)

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"SERIAL1", "SERIAL2"}
        assert result["SERIAL1"]["name"] == "vol-a"
        assert result["SERIAL2"]["name"] == "vol-b"

    def test_volume_row_holds_the_fields_of_the_volume(self, pure1_client):
        """Each key of the row is filled from the field of its own name."""
        volume = _make_volume(destroyed=True)
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result == {"ABC123": dict(VOL1, destroyed=True)}

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_created_is_utc_whatever_the_zone_of_the_host(self, pure1_client):
        """Pure1 gives a time in milliseconds; the row holds it as UTC."""
        # A time whose month, day, hour, minute and second all differ
        volume = _make_volume(created=1612357445000)
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"]["created"] == "2021-02-03 13:04:05 UTC"

    def test_volume_without_id(self, pure1_client):
        """A volume that reports no id is listed with id None."""
        volume = _make_volume(id=None)
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"] == dict(VOL1, id=None)


class TestPure1VolumesMain:
    """Tests for the pure1_volumes main() flow."""

    def test_volumes_are_returned_by_serial_number(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        # array is left to the argument spec
        module = run_main(pure1_volumes)

        pure1_client.get_volumes.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_volumes={"serial_numbers": {"ABC123": VOL1}}
        )

    def test_array_option_selects_the_volumes_of_one_array(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        module = run_main(pure1_volumes, array="array1")

        pure1_client.get_volumes.assert_called_once_with(filter="arrays.name='array1'")
        module.exit_json.assert_called_once_with(
            changed=False, pure1_volumes={"serial_numbers": {"ABC123": VOL1}}
        )

    def test_volumes_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])

        module = run_main(pure1_volumes, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_volumes={"serial_numbers": {"ABC123": VOL1}}
        )

    def test_no_volumes_gives_an_empty_result(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response([])

        module = run_main(pure1_volumes, array="array1")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_volumes={"serial_numbers": {}}
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_volumes reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_volumes.return_value = error_response(403, "Access denied")

        module = run_main(pure1_volumes)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message
