# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_volumes module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import re

import pytest
import yaml

from pypureclient import PureError

from plugins.module_utils.pure1 import filter_string
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


def _make_stretched_volume(**overrides):
    """Build a volume of a stretched pod: it is on array1 and on array2."""
    fields = dict(
        arrays=[
            array_reference("array1", fqdn="array1.example.com"),
            array_reference("array2", fqdn="array2.example.com"),
        ]
    )
    fields.update(overrides)
    return _make_volume(**fields)


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
ARRAY1 = {"name": "array1", "fqdn": "array1.example.com"}
ARRAY2 = {"name": "array2", "fqdn": "array2.example.com"}


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

    def test_array_option_selects_the_array_of_the_row(self, pure1_client):
        """With array set, array (singular) is that array, not the first."""
        pure1_client.get_volumes.return_value = valid_response(
            [_make_stretched_volume()]
        )

        result = generate_volumes_dict(ansible_module(array="array2"), pure1_client)

        assert result == {"ABC123": dict(VOL1, array=ARRAY2, arrays=[ARRAY1, ARRAY2])}

    def test_array_option_that_is_the_first_array(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response(
            [_make_stretched_volume()]
        )

        result = generate_volumes_dict(ansible_module(array="array1"), pure1_client)

        assert result == {"ABC123": dict(VOL1, array=ARRAY1, arrays=[ARRAY1, ARRAY2])}

    def test_array_option_match_ignores_case(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response(
            [_make_stretched_volume()]
        )

        result = generate_volumes_dict(ansible_module(array="ARRAY2"), pure1_client)

        pure1_client.get_volumes.assert_called_once_with(filter="arrays.name='ARRAY2'")
        assert result["ABC123"]["array"] == ARRAY2
        assert result["ABC123"]["arrays"] == [ARRAY1, ARRAY2]

    def test_array_option_not_among_the_arrays_keeps_the_first(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response(
            [_make_stretched_volume()]
        )

        result = generate_volumes_dict(ansible_module(array="other"), pure1_client)

        assert result["ABC123"]["array"] == ARRAY1
        assert result["ABC123"]["arrays"] == [ARRAY1, ARRAY2]

    def test_array_option_skips_an_array_reference_without_name(self, pure1_client):
        volume = _make_volume(
            arrays=[
                sdk_model("FixedReferenceFqdn", fqdn="array1.example.com"),
                array_reference("array2", fqdn="array2.example.com"),
            ]
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array="array2"), pure1_client)

        assert result["ABC123"]["array"] == ARRAY2
        assert result["ABC123"]["arrays"] == [
            {"name": None, "fqdn": "array1.example.com"},
            ARRAY2,
        ]

    def test_array_without_fqdn(self, pure1_client):
        """An array reference without an fqdn is reported with fqdn None."""
        volume = _make_volume(arrays=[array_reference("array1")])
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        entry = result["ABC123"]
        assert entry["array"] == {"name": "array1", "fqdn": None}
        assert entry["arrays"] == [{"name": "array1", "fqdn": None}]

    def test_array_without_name(self, pure1_client):
        """An array reference without a name is reported with name None."""
        volume = _make_volume(
            arrays=[sdk_model("FixedReferenceFqdn", fqdn="array1.example.com")]
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        unnamed = {"name": None, "fqdn": "array1.example.com"}
        assert result == {"ABC123": dict(VOL1, array=unnamed, arrays=[unnamed])}

    @pytest.mark.parametrize("arrays", [None, []], ids=["unset", "empty"])
    def test_volume_without_arrays(self, pure1_client, arrays):
        """A volume that reports no array is listed, with no array named."""
        volume = _make_volume(arrays=arrays)
        pure1_client.get_volumes.return_value = valid_response([volume])
        module = ansible_module(array=None)

        result = generate_volumes_dict(module, pure1_client)

        assert result == {
            "ABC123": dict(VOL1, array={"name": None, "fqdn": None}, arrays=[])
        }
        module.warn.assert_not_called()

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

    def test_source_and_pod_without_name_stay_empty_lists(self, pure1_client):
        """A source/pod reference that carries no name is no source/pod."""
        volume = _make_volume(
            source=sdk_model("FixedReference", id="source-id"),
            pod=sdk_model("FixedReference", id="pod-id"),
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result == {"ABC123": VOL1}

    def test_source_and_pod_with_an_empty_name_keep_it(self, pure1_client):
        """Only a name Pure1 did not send is no source/pod: an empty name is a name."""
        volume = _make_volume(
            source=sdk_model("FixedReference", name=""),
            pod=sdk_model("FixedReference", name=""),
        )
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result == {"ABC123": dict(VOL1, source="", pod="")}

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

    def test_filter_string_is_the_one_of_module_utils(self):
        assert pure1_volumes.filter_string is filter_string

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

    def test_created_at_the_epoch_is_a_time(self, pure1_client):
        """Only a time Pure1 did not send is None: 0 is a time."""
        volume = _make_volume(created=0)
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"]["created"] == "1970-01-01 00:00:00 UTC"

    def test_volume_without_id(self, pure1_client):
        """A volume that reports no id is listed with id None."""
        volume = _make_volume(id=None)
        pure1_client.get_volumes.return_value = valid_response([volume])

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        assert result["ABC123"] == dict(VOL1, id=None)

    @pytest.mark.parametrize(
        "field", ["name", "created", "eradicated", "destroyed", "provisioned"]
    )
    def test_volume_without_an_optional_field(self, pure1_client, field):
        """A volume that reports no value for a field is listed with None for it."""
        volume = _make_volume(**{field: None})
        pure1_client.get_volumes.return_value = valid_response([volume])
        module = ansible_module(array=None)

        result = generate_volumes_dict(module, pure1_client)

        assert result == {"ABC123": dict(VOL1, **{field: None})}
        module.warn.assert_not_called()

    def test_volume_with_only_a_serial_number(self, pure1_client):
        """Every key but the serial number has a default."""
        volume = sdk_model("Volume", serial="ABC123")
        pure1_client.get_volumes.return_value = valid_response([volume])
        module = ansible_module(array="array1")

        result = generate_volumes_dict(module, pure1_client)

        assert result == {
            "ABC123": {
                "id": None,
                "name": None,
                "created": None,
                "eradicated": None,
                "destroyed": None,
                "provisioned": None,
                "source": [],
                "serial": "ABC123",
                "pod": [],
                "array": {"name": None, "fqdn": None},
                "arrays": [],
            }
        }
        module.warn.assert_not_called()

    def test_volume_without_serial_is_left_out_with_a_warning(self, pure1_client):
        """The result is keyed on the serial number: no serial, no row."""
        pure1_client.get_volumes.return_value = valid_response(
            [
                _make_volume(serial=None, name="vol2"),
                _make_volume(),
                sdk_model("Volume", name="vol3"),
            ]
        )
        module = ansible_module(array=None)

        result = generate_volumes_dict(module, pure1_client)

        assert result == {"ABC123": VOL1}
        module.warn.assert_called_once_with(
            "Volumes left out of the result: 2 without a serial number"
        )

    def test_no_warning_when_every_volume_is_listed(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response(
            [_make_volume(), _make_stretched_volume(serial="DEF456")]
        )
        module = ansible_module(array=None)

        generate_volumes_dict(module, pure1_client)

        module.warn.assert_not_called()


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

    def test_array_option_names_the_requested_array_of_a_stretched_volume(
        self, pure1_client
    ):
        pure1_client.get_volumes.return_value = valid_response(
            [_make_stretched_volume()]
        )

        module = run_main(pure1_volumes, array="array2")

        pure1_client.get_volumes.assert_called_once_with(filter="arrays.name='array2'")
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_volumes={
                "serial_numbers": {
                    "ABC123": dict(VOL1, array=ARRAY2, arrays=[ARRAY1, ARRAY2])
                }
            },
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

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_volumes.return_value = error_response(403, "Access denied")

        module = run_main(pure1_volumes)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    @pytest.mark.parametrize("params", [{}, {"array": "array1"}], ids=["all", "array"])
    def test_error_answer_fails_with_the_message_of_pure1(self, pure1_client, params):
        pure1_client.get_volumes.return_value = error_response(
            400, "Illegal filter format", "Unexpected input at character 16"
        )

        module = run_main(pure1_volumes, **params)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_volumes failed (HTTP 400): Illegal filter "
            "format: Unexpected input at character 16"
        )

    @pytest.mark.parametrize("params", [{}, {"array": "array1"}], ids=["all", "array"])
    def test_raised_error_fails_the_task_in_one_line(self, pure1_client, params):
        pure1_client.get_volumes.side_effect = PureError("connection lost")

        module = run_main(pure1_volumes, **params)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_volumes failed: PureError: connection lost"
        )


class TestDocumentation:
    """The documentation strings of the module against what it does."""

    def test_return_documents_the_keys_of_a_volume(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response([_make_volume()])
        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        documented = yaml.safe_load(pure1_volumes.RETURN)["pure1_volumes"]["contains"]
        assert list(documented) == ["serial_numbers"]
        keys = documented["serial_numbers"]["contains"]
        assert set(keys) == set(result["ABC123"])
        assert set(keys["array"]["contains"]) == set(result["ABC123"]["array"])
        assert set(keys["arrays"]["contains"]) == set(result["ABC123"]["arrays"][0])
        for new in ("id", "arrays"):
            assert keys[new]["description"][-1] == "Added in version 1.6.0."

    def test_sample_is_what_the_module_returns(self, pure1_client):
        pure1_client.get_volumes.return_value = valid_response(
            [
                _make_volume(
                    id="01234567-89ab-cdef-0123-456789abcdef",
                    serial="0123456789ABCDEF00011010",
                    created=1612357445000,
                ),
                _make_stretched_volume(
                    id="12345678-9abc-def0-1234-56789abcdef0",
                    serial="0123456789ABCDEF00011011",
                    name="pod-1::vol2",
                    created=1612357446000,
                    provisioned=2147483648,
                    source=sdk_model("FixedReference", name="vol1"),
                    pod=sdk_model("FixedReference", name="pod-1"),
                ),
            ]
        )

        result = generate_volumes_dict(ansible_module(array=None), pure1_client)

        sample = yaml.safe_load(pure1_volumes.RETURN)["pure1_volumes"]["sample"]
        assert sample == {"serial_numbers": result}

    def test_ansible_doc_shows_the_text_whole(self):
        """ansible-doc drops two characters, a space and a word in front of ::."""
        # so the volume of a pod in the sample is not "name: pod1::vol2"
        for text in (pure1_volumes.DOCUMENTATION, pure1_volumes.RETURN):
            assert not re.search(r".. \w+?::", text)

    def test_examples_register_at_task_level_and_read_the_result(self):
        tasks = yaml.safe_load(pure1_volumes.EXAMPLES)
        module_tasks = [
            task for task in tasks if "everpure.pure1.pure1_volumes" in task
        ]

        assert module_tasks[0]["everpure.pure1.pure1_volumes"] == {
            "app_id": "pure1:apikey:dssf2331sd",
            "key_file": "/home/user/private.pem",
        }
        for task in module_tasks:
            assert "register" not in task["everpure.pure1.pure1_volumes"]
            later = json.dumps(tasks[tasks.index(task) + 1 :])
            assert "{{ %s['pure1_volumes']" % task["register"] in later

    def test_examples_read_only_what_an_earlier_task_registered(self):
        registered = set()
        for task in yaml.safe_load(pure1_volumes.EXAMPLES):
            known = registered | ({"item"} if "loop" in task else set())
            read = set(re.findall(r"\{\{\s*(\w+)", json.dumps(task)))
            assert read <= known, task["name"]
            if "register" in task:
                registered.add(task["register"])

    def test_examples_name_every_module_in_full(self):
        for task in yaml.safe_load(pure1_volumes.EXAMPLES):
            (action,) = set(task) - {"name", "register", "loop"}
            assert action.count(".") == 2, task["name"]

    def test_module_has_no_ansible_metadata(self):
        assert not hasattr(pure1_volumes, "ANSIBLE_METADATA")
