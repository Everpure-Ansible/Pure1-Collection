# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_file_systems module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
from unittest.mock import call

import pytest
import yaml
from ansible.template import Templar
from pypureclient import ErrorResponse, PureError

try:
    from ansible.template import trust_as_template
except ImportError:
    # ansible-core before 2.19 templates every string
    def trust_as_template(value):
        return value


from plugins.modules import pure1_file_systems
from tests.unit.conftest import (
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# 2021-02-03 13:04:05 UTC and 2026-01-02 03:04:05 UTC
CREATED = 1612357445000
AS_OF = 1767323045000

BLADE_ROW = {
    "id": "fs-id-1",
    "created": "2021-02-03 13:04:05 UTC",
    "created_epoch": 1612357445000,
    "destroyed": False,
    "as_of": "2026-01-02 03:04:05 UTC",
    "as_of_epoch": 1767323045000,
    "provisioned": 1099511627776,
    "hard_limit_enabled": True,
    "fast_remove_directory_enabled": False,
    "snapshot_directory_enabled": True,
    "nfs": {"enabled": False, "rules": "*(rw,no_root_squash)"},
    "smb": {"enabled": True},
    "http": {"enabled": False},
    "same_name": [],
}
# What Pure1 sends for a FlashArray file system: no size, no protocol data
ARRAY_ROW = {
    "id": "fs-id-2",
    "created": "2021-02-03 13:04:06 UTC",
    "created_epoch": 1612357446000,
    "destroyed": True,
    "as_of": "2026-01-02 03:04:05 UTC",
    "as_of_epoch": 1767323045000,
    "provisioned": None,
    "hard_limit_enabled": None,
    "fast_remove_directory_enabled": None,
    "snapshot_directory_enabled": None,
    "nfs": {"enabled": None, "rules": None},
    "smb": {"enabled": None},
    "http": {"enabled": None},
    "same_name": [],
}
BARE_ROW = dict(ARRAY_ROW, id=None, created=None, created_epoch=None, destroyed=None)
BARE_ROW.update(as_of=None, as_of_epoch=None)
ROOT_DIRECTORY = {
    "id": "dir-id-1",
    "name": "pod1::fs2:root",
    "directory_name": "root",
    "path": "/",
    "created": "2021-02-03 13:04:07 UTC",
    "created_epoch": 1612357447000,
    "destroyed": False,
}
BARE_DIRECTORY = dict.fromkeys(ROOT_DIRECTORY)
# ansible-doc takes out of what it prints whatever matches this (ansible/cli/doc.py)
RST_DIRECTIVE = r".. \w+?::"
DIRECTORIES_EXAMPLE = "Show the paths of the managed directories of each file system"

NOT_FILED = (
    "Pure1 returned {0} file systems without a name or without an array: they "
    "are left out of the result."
)
NO_FILE_SYSTEM = (
    "Pure1 returned {0} managed directories whose file system it did not "
    "return: they are left out of the result."
)


def _blade_file_system(array="array1", **overrides):
    fields = dict(
        id="fs-id-1",
        name="fs1",
        arrays=[array_reference(array)],
        created=CREATED,
        destroyed=False,
        as_of=AS_OF,
        provisioned=1099511627776,
        hard_limit_enabled=True,
        fast_remove_directory_enabled=False,
        snapshot_directory_enabled=True,
        nfs=sdk_model("Nfs", enabled=False, rules="*(rw,no_root_squash)"),
        smb=sdk_model("Smb", enabled=True),
        http=sdk_model("Http", enabled=False),
    )
    fields.update(overrides)
    return sdk_model("FileSystem", **fields)


def _array_file_system(array="array2", **overrides):
    fields = dict(
        id="fs-id-2",
        name="pod1::fs2",
        arrays=[array_reference(array)],
        created=CREATED + 1000,
        destroyed=True,
        as_of=AS_OF,
    )
    fields.update(overrides)
    return sdk_model("FileSystem", **fields)


def _directory(file_system_id="fs-id-2", **overrides):
    fields = dict(
        id="dir-id-1",
        name="pod1::fs2:root",
        directory_name="root",
        path="/",
        created=CREATED + 2000,
        destroyed=False,
        as_of=AS_OF,
        arrays=[array_reference("array2")],
        file_system=sdk_model("FixedReference", id=file_system_id, name="pod1::fs2"),
    )
    fields.update(overrides)
    return sdk_model("Directory", **fields)


def _run(pure1_client, listed, managed=None, arrays=None, **params):
    """Run main() against these answers and return the module stand-in"""
    pure1_client.get_file_systems.return_value = valid_response(listed)
    if managed is not None:
        pure1_client.get_directories.return_value = valid_response(managed)
        params.setdefault("directories", True)
    if arrays is not None:
        # the first answer is for the check get_pure1 makes
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response([sdk_model("Array", name=name) for name in arrays]),
        ]
    return run_main(pure1_file_systems, **params)


def _file_systems(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_file_systems"]["file_systems"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _under_same_name(row, **changes):
    """Return a row as it stands in the same_name of another file system"""
    other = dict(row, **changes)
    del other["same_name"]
    return other


def _without_name():
    return sdk_model("FileSystem", id="fs-id-9", arrays=[array_reference("array1")])


def _without_arrays():
    return sdk_model("FileSystem", id="fs-id-9", name="fs9")


def _with_empty_arrays():
    # The SDK reads a response without validating it, so an empty list
    # arrives as it is; the constructor of the model refuses one
    file_system = _blade_file_system(id="fs-id-9", name="fs9")
    del file_system.arrays[:]
    return file_system


def _with_null_array():
    file_system = _blade_file_system(id="fs-id-9", name="fs9")
    file_system.arrays[0] = None
    return file_system


def _on_array_without_name():
    return _blade_file_system(
        id="fs-id-9",
        name="fs9",
        arrays=[sdk_model("FixedReferenceFqdn", fqdn="array1.example.com")],
    )


class TestPure1FileSystemsMain:
    """The whole result of main() and the requests it makes."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_file_systems_are_returned_by_array_and_name(self, pure1_client):
        module = _run(pure1_client, [_blade_file_system(), _array_file_system()])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_file_systems={
                "file_systems": {
                    "array1": {"fs1": BLADE_ROW},
                    "array2": {"pod1::fs2": ARRAY_ROW},
                }
            },
        )
        module.warn.assert_not_called()
        # one request, and the check of get_pure1
        pure1_client.get_file_systems.assert_called_once_with(filter=None)
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_directories.assert_not_called()

    def test_check_mode_returns_the_same(self, pure1_client):
        module = _run(
            pure1_client,
            [_blade_file_system(), _array_file_system()],
            check_mode=True,
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_file_systems={
                "file_systems": {
                    "array1": {"fs1": BLADE_ROW},
                    "array2": {"pod1::fs2": ARRAY_ROW},
                }
            },
        )
        pure1_client.get_file_systems.assert_called_once_with(filter=None)

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_option_at_once(self, pure1_client):
        module = _run(
            pure1_client,
            [_array_file_system()],
            managed=[_directory()],
            arrays=["array2"],
            array=["ARRAY2"],
            name=["POD1::FS2"],
            destroyed=True,
            check_mode=True,
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_file_systems={
                "file_systems": {
                    "array2": {
                        "pod1::fs2": dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])
                    }
                }
            },
        )
        module.warn.assert_not_called()
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='ARRAY2'"),
        ]
        pure1_client.get_file_systems.assert_called_once_with(
            filter="arrays.name='array2' and name='POD1::FS2' and destroyed=true"
        )
        # destroyed selects file systems, not directories
        pure1_client.get_directories.assert_called_once_with(
            filter="arrays.name='array2' and file_system.name='POD1::FS2'"
        )

    def test_no_file_systems_gives_an_empty_result(self, pure1_client):
        module = _run(pure1_client, [], managed=[])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_file_systems={"file_systems": {}}
        )
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        pure1_client.get_directories.assert_not_called()

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_values_that_are_zero_empty_or_false_are_returned(self, pure1_client):
        """A value Pure1 sends is no missing value, whatever its truth"""
        nothing_set = _blade_file_system(
            created=0,
            as_of=0,
            provisioned=0,
            hard_limit_enabled=False,
            snapshot_directory_enabled=False,
            nfs=sdk_model("Nfs", enabled=False, rules=""),
            smb=sdk_model("Smb", enabled=False),
        )
        directory = _directory(
            "fs-id-1", name="", directory_name="", path="", created=0
        )

        module = _run(pure1_client, [nothing_set], managed=[directory])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_file_systems={
                "file_systems": {
                    "array1": {
                        "fs1": {
                            "id": "fs-id-1",
                            "created": "1970-01-01 00:00:00 UTC",
                            "created_epoch": 0,
                            "destroyed": False,
                            "as_of": "1970-01-01 00:00:00 UTC",
                            "as_of_epoch": 0,
                            "provisioned": 0,
                            "hard_limit_enabled": False,
                            "fast_remove_directory_enabled": False,
                            "snapshot_directory_enabled": False,
                            "nfs": {"enabled": False, "rules": ""},
                            "smb": {"enabled": False},
                            "http": {"enabled": False},
                            "directories": [
                                {
                                    "id": "dir-id-1",
                                    "name": "",
                                    "directory_name": "",
                                    "path": "",
                                    "created": "1970-01-01 00:00:00 UTC",
                                    "created_epoch": 0,
                                    "destroyed": False,
                                }
                            ],
                            "same_name": [],
                        }
                    }
                }
            },
        )
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params, row",
        [
            pytest.param({}, BARE_ROW, id="default"),
            pytest.param(
                {"directories": True}, dict(BARE_ROW, directories=[]), id="directories"
            ),
        ],
    )
    def test_file_system_with_only_a_name_and_an_array(self, pure1_client, params, row):
        bare = sdk_model("FileSystem", name="fs1", arrays=[array_reference("array1")])
        pure1_client.get_directories.return_value = valid_response([])

        module = _run(pure1_client, [bare], **params)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_file_systems={"file_systems": {"array1": {"fs1": row}}},
        )
        module.warn.assert_not_called()

    def test_result_has_the_keys_that_return_documents(self, pure1_client):
        """In both directions, with the directories and without"""
        documented = yaml.safe_load(pure1_file_systems.RETURN)["pure1_file_systems"]
        assert list(documented["contains"]) == ["file_systems"]
        keys = documented["contains"]["file_systems"]["contains"]

        module = _run(
            pure1_client,
            [_array_file_system(), _array_file_system(id="fs-id-3")],
            managed=[_directory()],
        )

        row = _file_systems(module)["array2"]["pod1::fs2"]
        assert list(row) == list(keys)
        for protocol in ("nfs", "smb", "http"):
            assert list(row[protocol]) == list(keys[protocol]["contains"])
        (other,) = row["same_name"]
        assert list(other) == [key for key in keys if key != "same_name"]
        assert list(other["directories"][0]) == list(keys["directories"]["contains"])
        assert set(BLADE_ROW) == set(keys) - {"directories"}
        sample = documented["sample"]["file_systems"]
        assert [list(row) for row in sample["array1"].values()] == [list(keys)]
        assert [list(row) for row in sample["array2"].values()] == [list(keys)]
        assert sample["array2"]["pod-1::fs2"]["directories"] == [
            dict(
                ROOT_DIRECTORY,
                id="0f8fad5b-d9cb-469f-a165-70867728950e",
                name="pod-1::fs2:root",
                created="2021-02-03 13:04:05 UTC",
                created_epoch=1612357445000,
            )
        ]

    @pytest.mark.parametrize("block", ["DOCUMENTATION", "RETURN"])
    def test_ansible_doc_prints_the_documentation_as_it_is(self, block):
        """A sample name such as pod1::fs2 after a space is taken for RST"""
        documented = yaml.safe_load(getattr(pure1_file_systems, block))
        printed = yaml.dump(documented, default_flow_style=False, width=1000)

        assert "::" in printed
        assert re.findall(RST_DIRECTIVE, printed) == []

    @pytest.mark.parametrize("option", ["array", "name"])
    def test_documentation_says_what_an_empty_list_selects(self, option):
        """test_empty_list_selects_nothing pins what it does"""
        options = yaml.safe_load(pure1_file_systems.DOCUMENTATION)["options"]

        assert "returned when it is not set or empty." in options[option][
            "description"
        ][0].replace("\n", " ")

    @pytest.mark.parametrize(
        "listed, expected",
        [
            pytest.param([_array_file_system], ["pod1::fs2 has ['/']"], id="found"),
            pytest.param([], [], id="array without file systems"),
        ],
    )
    def test_example_that_loops_over_the_file_systems_of_an_array(
        self, pure1_client, listed, expected
    ):
        """The loop and the message of the example, as ansible-core evaluates them"""
        (task,) = [
            task
            for task in yaml.safe_load(pure1_file_systems.EXAMPLES)
            if task["name"] == DIRECTORIES_EXAMPLE
        ]
        module = _run(
            pure1_client,
            [file_system("array1") for file_system in listed],
            managed=[_directory()],
            arrays=["array1"],
            array=["array1"],
        )
        registered = {"pure1_file_systems": {"file_systems": _file_systems(module)}}

        items = Templar(
            loader=None, variables={"with_directories": registered}
        ).template(trust_as_template(task["loop"]))
        messages = [
            Templar(loader=None, variables={"item": item}).template(
                trust_as_template(task["ansible.builtin.debug"]["msg"])
            )
            for item in items
        ]

        assert messages == expected


class TestOptions:
    """The requests each option makes and what it selects."""

    def test_array_is_resolved_and_filters_with_the_names_of_pure1(self, pure1_client):
        module = _run(
            pure1_client,
            [_blade_file_system("Array1"), _array_file_system()],
            arrays=["Array1", "array2"],
            array=["ARRAY1", "array2"],
        )

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        pure1_client.get_file_systems.assert_called_once_with(
            filter="arrays.name=('Array1','array2')"
        )
        pure1_client.get_directories.assert_not_called()
        assert _file_systems(module) == {
            "Array1": {"fs1": BLADE_ROW},
            "array2": {"pod1::fs2": ARRAY_ROW},
        }

    def test_array_that_pure1_does_not_know_fails_the_task(self, pure1_client):
        module = _run(pure1_client, [], arrays=["array1"], array=["array1", "foo"])

        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")
        module.exit_json.assert_not_called()
        pure1_client.get_file_systems.assert_not_called()

    def test_named_array_without_file_systems_is_not_in_the_result(self, pure1_client):
        module = _run(
            pure1_client,
            [_blade_file_system()],
            arrays=["array1", "array2"],
            array=["array1", "array2"],
        )

        assert _file_systems(module) == {"array1": {"fs1": BLADE_ROW}}
        module.warn.assert_not_called()

    @pytest.mark.parametrize("option", ["array", "name"])
    def test_empty_list_selects_nothing(self, pure1_client, option):
        module = _run(pure1_client, [_blade_file_system()], **{option: []})

        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_file_systems.assert_called_once_with(filter=None)
        assert _file_systems(module) == {"array1": {"fs1": BLADE_ROW}}

    @pytest.mark.parametrize(
        "names, expected",
        [
            pytest.param(["fs1"], "name='fs1'", id="one"),
            pytest.param(
                ["fs1", "pod1::fs2", "fs1"], "name=('fs1','pod1::fs2')", id="several"
            ),
            pytest.param(["o'brien"], "name='o\\'brien'", id="single quote"),
            pytest.param(["back\\slash"], "name='back\\\\slash'", id="backslash"),
        ],
    )
    def test_name_filters_the_file_systems(self, pure1_client, names, expected):
        _run(pure1_client, [], name=names)

        pure1_client.get_file_systems.assert_called_once_with(filter=expected)
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_directories.assert_not_called()

    def test_name_that_matches_nothing_is_no_error(self, pure1_client):
        module = _run(pure1_client, [], name=["no-such-file-system"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_file_systems={"file_systems": {}}
        )
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param({}, None, id="unset"),
            pytest.param({"destroyed": True}, "destroyed=true", id="true"),
            pytest.param({"destroyed": False}, "destroyed=false", id="false"),
            pytest.param({"destroyed": "yes"}, "destroyed=true", id="yes"),
        ],
    )
    def test_destroyed_filters_the_file_systems(self, pure1_client, params, expected):
        _run(pure1_client, [], **params)

        pure1_client.get_file_systems.assert_called_once_with(filter=expected)
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_directories.assert_not_called()

    @pytest.mark.parametrize("option", ["destroyed", "directories"])
    def test_value_that_is_no_boolean_is_refused(self, pure1_client, option):
        module = _run(pure1_client, [], **{option: "maybe"})

        message = module.fail_json.call_args[1]["msg"]
        assert message.startswith(
            "argument '{0}' is of type <class 'str'> and we were unable to "
            "convert to bool: The value 'maybe' is not a valid boolean.".format(option)
        )
        pure1_client.get_file_systems.assert_not_called()

    @pytest.mark.parametrize("option", ["filter", "limit", "sort", "file_systems"])
    def test_there_is_no_other_option(self, pure1_client, option):
        module = _run(pure1_client, [], **{option: "1"})

        module.fail_json.assert_called_once_with(
            msg="Unsupported parameters for module: {0}. Supported parameters "
            "include: app_id, array, destroyed, directories, key_file, name, "
            "password, validate_certs.".format(option)
        )
        pure1_client.get_file_systems.assert_not_called()

    def test_directories_false_makes_no_request_for_them(self, pure1_client):
        module = _run(pure1_client, [_array_file_system()], directories=False)

        pure1_client.get_directories.assert_not_called()
        assert _file_systems(module) == {"array2": {"pod1::fs2": ARRAY_ROW}}

    @pytest.mark.parametrize(
        "params, arrays, expected",
        [
            pytest.param({}, None, None, id="default"),
            pytest.param({"destroyed": False}, None, None, id="destroyed"),
            pytest.param(
                {"array": ["ARRAY1", "array2"]},
                ["array1", "array2"],
                "arrays.name=('array1','array2')",
                id="array",
            ),
            pytest.param(
                {"name": ["fs1", "o'brien"]},
                None,
                "file_system.name=('fs1','o\\'brien')",
                id="name",
            ),
            pytest.param(
                {"array": ["array1"], "name": ["fs1"], "destroyed": True},
                ["array1"],
                "arrays.name='array1' and file_system.name='fs1'",
                id="all",
            ),
        ],
    )
    def test_directories_are_one_more_request(
        self, pure1_client, params, arrays, expected
    ):
        _run(
            pure1_client,
            [_array_file_system("array1")],
            managed=[],
            arrays=arrays,
            **params
        )

        pure1_client.get_directories.assert_called_once_with(filter=expected)
        assert pure1_client.get_file_systems.call_count == 1
        assert pure1_client.get_arrays.call_count == (2 if arrays else 1)

    @pytest.mark.parametrize(
        "listed, params, arrays, warnings",
        [
            pytest.param([], {}, None, [], id="default"),
            pytest.param([], {"destroyed": True}, None, [], id="destroyed"),
            pytest.param([], {"array": ["array1"]}, ["array1"], [], id="array"),
            pytest.param([], {"name": ["fs1"]}, None, [], id="name"),
            pytest.param(
                [_without_name], {}, None, [NOT_FILED.format(1)], id="none filed"
            ),
        ],
    )
    def test_directories_are_not_requested_without_a_file_system(
        self, pure1_client, listed, params, arrays, warnings
    ):
        """No row could take one: 2,500 directories would be three requests"""
        directories = [
            _directory(id="dir-id-{0}".format(number)) for number in range(5)
        ]
        joined = paged_answers("DirectoryGetResponse", directories, page_size=2)
        pure1_client.get_directories.side_effect = joined
        pure1_client.get_file_systems.return_value = valid_response(
            [file_system() for file_system in listed]
        )
        if arrays:
            pure1_client.get_arrays.side_effect = [
                valid_response(),
                valid_response([sdk_model("Array", name=name) for name in arrays]),
            ]

        module = run_main(pure1_file_systems, directories=True, **params)

        assert joined.requests == []
        pure1_client.get_directories.assert_not_called()
        assert _file_systems(module) == {}
        assert _warnings(module) == warnings


class TestFiling:
    """Where a file system goes, and which are left out."""

    def test_file_systems_of_one_name_on_two_arrays(self, pure1_client):
        module = _run(
            pure1_client,
            [
                _blade_file_system("array1"),
                _blade_file_system("array2", id="fs-id-3", hard_limit_enabled=False),
                _blade_file_system("array1", id="fs-id-4", name="FS1"),
            ],
        )

        assert _file_systems(module) == {
            "array1": {"fs1": BLADE_ROW, "FS1": dict(BLADE_ROW, id="fs-id-4")},
            "array2": {"fs1": dict(BLADE_ROW, id="fs-id-3", hard_limit_enabled=False)},
        }
        module.warn.assert_not_called()

    def test_file_system_on_two_arrays_is_listed_under_each(self, pure1_client):
        stretched = _array_file_system(
            arrays=[array_reference("array2"), array_reference("array3")]
        )
        stretched.arrays.append(None)
        stretched.arrays.append(array_reference("array2"))

        module = _run(pure1_client, [stretched], managed=[_directory()])

        row = dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])
        assert _file_systems(module) == {
            "array2": {"pod1::fs2": row},
            "array3": {"pod1::fs2": row},
        }
        module.warn.assert_not_called()

    def test_file_system_on_two_arrays_goes_under_the_arrays_of_the_task(
        self, pure1_client
    ):
        stretched = _array_file_system(
            arrays=[array_reference("array2"), array_reference("Array3")]
        )

        module = _run(pure1_client, [stretched], arrays=["Array3"], array=["ARRAY3"])

        assert _file_systems(module) == {"Array3": {"pod1::fs2": ARRAY_ROW}}
        module.warn.assert_not_called()

    def test_file_system_with_an_empty_name_is_filed(self, pure1_client):
        """Only a name that Pure1 did not send leaves a file system out."""
        module = _run(pure1_client, [_blade_file_system("", name="")])

        assert _file_systems(module) == {"": {"": BLADE_ROW}}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_without_name, id="no name"),
            pytest.param(_without_arrays, id="no arrays"),
            pytest.param(_with_empty_arrays, id="empty arrays"),
            pytest.param(_with_null_array, id="null array"),
            pytest.param(_on_array_without_name, id="array without name"),
        ],
    )
    def test_file_system_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        # its directory goes with it, without a warning of its own
        module = _run(
            pure1_client,
            [broken(), _blade_file_system()],
            managed=[_directory("fs-id-9")],
        )

        assert _file_systems(module) == {
            "array1": {"fs1": dict(BLADE_ROW, directories=[])}
        }
        assert _warnings(module) == [NOT_FILED.format(1)]

    def test_file_systems_left_out_give_one_warning(self, pure1_client):
        module = _run(
            pure1_client,
            [_without_name(), _blade_file_system(), _without_arrays()],
        )

        assert _file_systems(module) == {"array1": {"fs1": BLADE_ROW}}
        assert _warnings(module) == [NOT_FILED.format(2)]

    def test_file_system_on_an_array_the_task_did_not_name_is_left_out(
        self, pure1_client
    ):
        """Pure1 filters by array, so this is an answer it should not give"""
        module = _run(
            pure1_client,
            [_blade_file_system("array1"), _array_file_system("array2")],
            arrays=["array1"],
            array=["array1"],
        )

        assert _file_systems(module) == {"array1": {"fs1": BLADE_ROW}}
        assert _warnings(module) == [NOT_FILED.format(1)]

    @pytest.mark.parametrize("first", ["older", "newer"])
    @pytest.mark.parametrize(
        "older, newer, created",
        [
            pytest.param(
                dict(id="fs-id-7", created=CREATED),
                dict(id="fs-id-5", created=CREATED + 1000),
                "2021-02-03 13:04:06 UTC",
                id="created",
            ),
            pytest.param(
                dict(id="fs-id-7"),
                dict(id="fs-id-5", created=0),
                "1970-01-01 00:00:00 UTC",
                id="no created",
            ),
            pytest.param(
                dict(id="fs-id-5", created=CREATED),
                dict(id="fs-id-7", created=CREATED),
                "2021-02-03 13:04:05 UTC",
                id="same created",
            ),
            pytest.param(
                dict(id="fs-id-5"), dict(id="fs-id-7"), None, id="neither created"
            ),
            pytest.param(
                dict(created=CREATED),
                dict(id="fs-id-7", created=CREATED),
                "2021-02-03 13:04:05 UTC",
                id="no id",
            ),
            pytest.param(
                dict(created=CREATED),
                dict(created=CREATED + 1000),
                "2021-02-03 13:04:06 UTC",
                id="neither id",
            ),
        ],
    )
    def test_two_file_systems_of_one_array_under_one_name(
        self, pure1_client, first, older, newer, created
    ):
        """The one created last has the name and the other is in its same_name

        Whatever the order of Pure1, and nothing is left out, so no warning.
        """
        pair = [
            sdk_model(
                "FileSystem", name="fs1", arrays=[array_reference("array1")], **fields
            )
            for fields in (older, newer)
        ]

        module = _run(pure1_client, pair if first == "older" else pair[::-1])

        assert _file_systems(module) == {
            "array1": {
                "fs1": dict(
                    BARE_ROW,
                    id=newer.get("id"),
                    created_epoch=newer.get("created"),
                    created=created,
                    same_name=[
                        _under_same_name(
                            BARE_ROW,
                            id=older.get("id"),
                            created_epoch=older.get("created"),
                            created=(
                                "2021-02-03 13:04:05 UTC"
                                if "created" in older
                                else None
                            ),
                        )
                    ],
                )
            }
        }
        module.warn.assert_not_called()

    def test_directories_go_with_the_file_system_that_is_returned(self, pure1_client):
        """Each of two file systems of one name has its own directories"""
        module = _run(
            pure1_client,
            [
                _array_file_system(id="fs-id-3", created=CREATED + 5000),
                _array_file_system(),
            ],
            managed=[_directory(), _directory("fs-id-3", id="dir-id-3")],
        )

        assert _file_systems(module) == {
            "array2": {
                "pod1::fs2": dict(
                    ARRAY_ROW,
                    id="fs-id-3",
                    created="2021-02-03 13:04:10 UTC",
                    created_epoch=CREATED + 5000,
                    directories=[dict(ROOT_DIRECTORY, id="dir-id-3")],
                    same_name=[
                        _under_same_name(ARRAY_ROW, directories=[ROOT_DIRECTORY])
                    ],
                )
            }
        }
        module.warn.assert_not_called()

    def test_three_file_systems_under_one_name_and_one_that_cannot_be_filed(
        self, pure1_client
    ):
        """The others of the name are listed with the one created last first"""
        module = _run(
            pure1_client,
            [
                _blade_file_system(),
                _blade_file_system(id="fs-id-5", created=CREATED + 2),
                _without_arrays(),
                _blade_file_system(id="fs-id-6", created=CREATED + 1),
            ],
        )

        assert _file_systems(module) == {
            "array1": {
                "fs1": dict(
                    BLADE_ROW,
                    id="fs-id-5",
                    created_epoch=CREATED + 2,
                    same_name=[
                        _under_same_name(
                            BLADE_ROW, id="fs-id-6", created_epoch=CREATED + 1
                        ),
                        _under_same_name(BLADE_ROW),
                    ],
                )
            }
        }
        assert _warnings(module) == [NOT_FILED.format(1)]

    def test_same_name_is_that_of_one_array(self, pure1_client):
        """A file system on two arrays shares its name on one of them only"""
        stretched = _array_file_system(
            arrays=[array_reference("array2"), array_reference("array3")]
        )
        local = _array_file_system("array3", id="fs-id-3", created=CREATED)

        module = _run(
            pure1_client,
            [stretched, local],
            managed=[_directory(), _directory("fs-id-3", id="dir-id-3")],
        )

        row = dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])
        assert _file_systems(module) == {
            "array2": {"pod1::fs2": row},
            "array3": {
                "pod1::fs2": dict(
                    row,
                    same_name=[
                        _under_same_name(
                            ARRAY_ROW,
                            id="fs-id-3",
                            created="2021-02-03 13:04:05 UTC",
                            created_epoch=CREATED,
                            directories=[dict(ROOT_DIRECTORY, id="dir-id-3")],
                        )
                    ],
                )
            },
        }
        module.warn.assert_not_called()

    def test_same_name_selected_by_array_and_name(self, pure1_client):
        """Both file systems of the name are in the result of a task that names it"""
        module = _run(
            pure1_client,
            [_array_file_system(), _array_file_system(id="fs-id-3", destroyed=False)],
            arrays=["array2"],
            array=["array2"],
            name=["pod1::fs2"],
        )

        found = _file_systems(module)["array2"]["pod1::fs2"]
        assert [found["id"]] + [other["id"] for other in found["same_name"]] == [
            "fs-id-3",
            "fs-id-2",
        ]
        module.warn.assert_not_called()

    def test_file_system_that_pure1_lists_twice_is_returned_once(self, pure1_client):
        """A listing that moved while it was read repeats an item"""
        stretched = dict(arrays=[array_reference("array2"), array_reference("array3")])

        module = _run(
            pure1_client,
            [
                _array_file_system(**stretched),
                _blade_file_system(),
                _array_file_system(**stretched),
                _blade_file_system(),
            ],
            managed=[
                _directory(),
                _directory(),
                _directory(id=None),
                _directory(id=None),
            ],
        )

        row = dict(
            ARRAY_ROW,
            directories=[dict(ROOT_DIRECTORY, id=None)] * 2 + [ROOT_DIRECTORY],
        )
        assert _file_systems(module) == {
            "array1": {"fs1": dict(BLADE_ROW, directories=[])},
            "array2": {"pod1::fs2": row},
            "array3": {"pod1::fs2": row},
        }
        module.warn.assert_not_called()


class TestDirectories:
    """The join of the managed directories on the ID of the file system."""

    def test_directories_are_joined_on_the_id_of_the_file_system(self, pure1_client):
        """Two file systems have one name: the name cannot place a directory"""
        module = _run(
            pure1_client,
            [
                _array_file_system("array2"),
                _array_file_system("array3", id="fs-id-3"),
                _blade_file_system(),
            ],
            managed=[
                _directory("fs-id-3", id="dir-id-4", name="pod1::fs2:data"),
                _directory("fs-id-2"),
                _directory("fs-id-3", id="dir-id-2", name="pod1::fs2:Apps"),
                _directory("fs-id-3", id="dir-id-5", name=None),
                _directory("fs-id-3", id="dir-id-3", name="pod1::fs2:data"),
            ],
        )

        assert _file_systems(module) == {
            "array1": {"fs1": dict(BLADE_ROW, directories=[])},
            "array2": {"pod1::fs2": dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])},
            "array3": {
                "pod1::fs2": dict(
                    ARRAY_ROW,
                    id="fs-id-3",
                    # sorted by name, then by ID
                    directories=[
                        dict(ROOT_DIRECTORY, id="dir-id-5", name=None),
                        dict(ROOT_DIRECTORY, id="dir-id-2", name="pod1::fs2:Apps"),
                        dict(ROOT_DIRECTORY, id="dir-id-3", name="pod1::fs2:data"),
                        dict(ROOT_DIRECTORY, id="dir-id-4", name="pod1::fs2:data"),
                    ],
                )
            },
        }
        module.warn.assert_not_called()
        pure1_client.get_directories.assert_called_once_with(filter=None)
        pure1_client.get_file_systems.assert_called_once_with(filter=None)
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_directory_with_only_its_file_system(self, pure1_client):
        bare = sdk_model(
            "Directory", file_system=sdk_model("FixedReference", id="fs-id-2")
        )

        module = _run(pure1_client, [_array_file_system()], managed=[bare])

        assert _file_systems(module) == {
            "array2": {"pod1::fs2": dict(ARRAY_ROW, directories=[BARE_DIRECTORY])}
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params, counted",
        [
            pytest.param({}, 3, id="default"),
            pytest.param({"name": ["pod1::fs2"]}, 3, id="name"),
            # the file system of a directory can be one that destroyed left out
            pytest.param({"destroyed": True}, 2, id="destroyed true"),
            pytest.param({"destroyed": False}, 2, id="destroyed false"),
        ],
    )
    def test_directory_without_a_file_system_of_pure1_is_left_out(
        self, pure1_client, params, counted
    ):
        no_reference = _directory(id="dir-id-2")
        no_reference.file_system = None
        directories = [
            sdk_model("Directory", id="dir-id-3", name="fs3:root"),
            _directory(),
            no_reference,
            _directory("fs-id-8", id="dir-id-4"),
        ]

        module = _run(
            pure1_client, [_array_file_system()], managed=directories, **params
        )

        assert _file_systems(module) == {
            "array2": {"pod1::fs2": dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])}
        }
        assert _warnings(module) == [NO_FILE_SYSTEM.format(counted)]

    def test_directory_of_a_file_system_without_id_cannot_be_joined(self, pure1_client):
        module = _run(
            pure1_client,
            [_array_file_system(id=None)],
            managed=[_directory(), _directory(None)],
        )

        assert _file_systems(module) == {
            "array2": {"pod1::fs2": dict(ARRAY_ROW, id=None, directories=[])}
        }
        assert _warnings(module) == [NO_FILE_SYSTEM.format(2)]


class TestListings:
    """Pages, listings that came short, and requests that failed."""

    def test_every_page_of_both_listings_is_read(self, pure1_client):
        file_systems = [
            _array_file_system(id="fs-id-{0}".format(n), name="fs{0}".format(n))
            for n in range(5)
        ]
        directories = [
            _directory("fs-id-{0}".format(n), id="dir-id-{0}".format(n))
            for n in (4, 0, 2)
        ]
        listed = paged_answers("FileSystemGetResponse", file_systems, page_size=2)
        joined = paged_answers("DirectoryGetResponse", directories, page_size=2)
        pure1_client.get_file_systems.side_effect = listed
        pure1_client.get_directories.side_effect = joined

        module = run_main(pure1_file_systems, directories=True, name=["fs"])

        assert listed.requests == [
            {"filter": "name='fs'"},
            {"filter": "name='fs'", "continuation_token": "page-2"},
            {"filter": "name='fs'", "continuation_token": "page-4"},
        ]
        assert joined.requests == [
            {"filter": "file_system.name='fs'"},
            {"filter": "file_system.name='fs'", "continuation_token": "page-2"},
        ]
        found = _file_systems(module)["array2"]
        assert list(found) == ["fs0", "fs1", "fs2", "fs3", "fs4"]
        assert [
            [directory["id"] for directory in row["directories"]]
            for row in found.values()
        ] == [["dir-id-0"], [], ["dir-id-2"], [], ["dir-id-4"]]
        module.warn.assert_not_called()

    def test_listings_that_came_short_give_a_warning_each(self, pure1_client):
        listed = paged_answers(
            "FileSystemGetResponse", [_array_file_system()], total_item_count=3
        )
        joined = paged_answers(
            "DirectoryGetResponse", [_directory()], total_item_count=2
        )
        pure1_client.get_file_systems.side_effect = listed
        pure1_client.get_directories.side_effect = joined

        module = run_main(pure1_file_systems, directories=True)

        assert _file_systems(module) == {
            "array2": {"pod1::fs2": dict(ARRAY_ROW, directories=[ROOT_DIRECTORY])}
        }
        assert _warnings(module) == [
            "Pure1 returned 1 of 3 file systems: the result may be incomplete.",
            "Pure1 returned 1 of 2 managed directories: the result may be "
            "incomplete.",
        ]
        assert listed.requests == [{"filter": None}, {"filter": None, "offset": 1}]
        assert joined.requests == [{"filter": None}, {"filter": None, "offset": 1}]

    def test_listing_without_a_total_that_cannot_be_paged(self, pure1_client):
        listed = paged_answers(
            "FileSystemGetResponse", [_array_file_system()], total_item_count=None
        )

        def first_page_again(**kwargs):
            kwargs.pop("offset", None)
            return listed(**kwargs)

        pure1_client.get_file_systems.side_effect = first_page_again

        module = run_main(pure1_file_systems)

        assert _file_systems(module) == {"array2": {"pod1::fs2": ARRAY_ROW}}
        assert _warnings(module) == [
            "Pure1 returned 1 of an unknown number of file systems: the result "
            "may be incomplete."
        ]

    @pytest.mark.parametrize("method", ["get_file_systems", "get_directories"])
    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request {0} failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "Pure1 request {0} failed (HTTP 400): Illegal filter "
                "format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request {0} failed (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request {0} failed: PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request {0} failed: TimeoutError: The read operation "
                "timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, expected
    ):
        pure1_client.get_file_systems.return_value = valid_response(
            [_array_file_system()]
        )
        setattr(getattr(pure1_client, method), attribute, answer)

        module = run_main(pure1_file_systems, directories=True)

        module.fail_json.assert_called_once_with(msg=expected.format(method))
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_failed_request_for_the_arrays_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(400, "Illegal filter format"),
        ]

        module = run_main(pure1_file_systems, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 400): Illegal filter format"
        )
        pure1_client.get_file_systems.assert_not_called()
