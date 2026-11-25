# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_arrays module.

The typical answer has the shape of a reading of a real organisation, under
invented names: six arrays of two products, a fleet with three member rows,
of which one array names no fleet on its own record, a fleet that only the
record of one array names, three topology groups in a tree and tags on three
of the arrays.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import types
from unittest.mock import call

import pypureclient
import pytest
import yaml
from ansible.module_utils.common.validation import check_required_by
from ansible.template import Templar
from pypureclient import ErrorResponse, PureError, ValidResponse
from pypureclient.responses import ItemIterator

try:
    from ansible.template import trust_as_template
except ImportError:
    # ansible-core before 2.19 templates every string
    def trust_as_template(value):
        return value


from plugins.modules import pure1_arrays
from plugins.modules.pure1_arrays import generate_arrays_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

ARRAY_AS_OF = 1612357445000
ARRAY_TIME = "2021-02-03 13:04:05 UTC"
# The time the module runs at: 55.5 seconds after the data of the arrays
NOW = 1612357500.5
ROWS_AS_OF = 1612353784000
FLEET_AS_OF = 1612447506000
PRODUCTS = {
    "fa": ("Purity//FA", "FA-X90R4", "6.5.8"),
    "fb": ("Purity//FB", "FlashBlade//S200", "4.5.6"),
}
# The listings of a run in the order the module asks for them
LISTINGS = [
    "get_arrays",
    "get_fleets",
    "get_fleets_members",
    "get_topology_groups",
    "get_topology_groups_members",
    "get_arrays_tags",
]
FA_FILTER = "os=('Purity//FA','Purity')"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Let the module run at NOW; a test sets clock.now for another time."""
    stopped = types.SimpleNamespace(now=NOW)
    stopped.time = lambda: stopped.now
    monkeypatch.setattr(pure1_arrays, "time", stopped)
    return stopped


def _ref(name=None, kind=None, **fields):
    """Return a reference; without a name the empty object Pure1 sends for none."""
    if name is not None:
        fields = dict(dict(name=name, id="id-" + name, resource_type=kind), **fields)
    return sdk_model("FixedReference", **fields)


def _array(name, product="fa", fleet=None, group=None, **overrides):
    system, model, version = PRODUCTS[product]
    fields = dict(
        id="id-" + name,
        name=name,
        fqdn=name + ".example.com",
        os=system,
        model=model,
        version=version,
        as_of=ARRAY_AS_OF,
        fleet=_ref(fleet, "fleets"),
        topology_group=_ref(group, "topology-groups"),
    )
    fields.update(overrides)
    return sdk_model("Array", **fields)


def _fleet(name, **overrides):
    fields = dict(id="id-" + name, name=name, as_of=FLEET_AS_OF)
    fields.update(overrides)
    return sdk_model("Fleet", **fields)


def _member(fleet_name, array_name, coordinator=False, **overrides):
    """Return the member row of an array in a fleet."""
    fields = dict(
        fleet=_ref(fleet_name, "fleets"),
        member=_ref(array_name, "arrays"),
        status="joined",
        status_details="",
        coordinator_of=[_ref(fleet_name, "fleets")] if coordinator else [],
        as_of=ROWS_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("FleetMember", **fields)


def _group(group_name, parent=None, fleet="fleet1"):
    return sdk_model(
        "TopologyGroup",
        id="id-{0}-{1}".format(fleet, group_name),
        name=group_name,
        fleet=_ref(fleet, "fleets"),
        parent_topology_group=_ref(parent, "topology-groups"),
        as_of=ROWS_AS_OF,
    )


def _group_member(group, name, kind="arrays", fleet="fleet1", **overrides):
    """Return the member row of an array, or of a child group, in a topology group."""
    fields = dict(
        fleet=_ref(fleet, "fleets"),
        topology_group=_ref(group, "topology-groups"),
        member=_ref(name, kind),
        status="joined",
        status_details="",
        as_of=ROWS_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("TopologyGroupMember", **fields)


def _tag(array, tag_key, value, namespace="pure1", **overrides):
    """Return a tag of an array; Pure1 names the array by its id."""
    fields = dict(
        key=tag_key,
        value=value,
        namespace=namespace,
        resource=_ref(array, "arrays"),
    )
    fields.update(overrides)
    return sdk_model("Tag", **fields)


def _serve(
    client,
    arrays=(),
    fleets=(),
    members=(),
    groups=(),
    group_members=(),
    tags=(),
    in_range=None,
):
    """Answer every request of one run of main() with one page.

    get_arrays is asked by get_pure1, then for every array, then for the
    arrays of a version range (in_range) when the task gives one.
    """
    answers = [valid_response(), valid_response(arrays)]
    if in_range is not None:
        answers.append(valid_response(in_range))
    client.get_arrays.side_effect = answers
    client.get_fleets.return_value = valid_response(fleets)
    client.get_fleets_members.return_value = valid_response(members)
    client.get_topology_groups.return_value = valid_response(groups)
    client.get_topology_groups_members.return_value = valid_response(group_members)
    client.get_arrays_tags.return_value = valid_response(tags)


def _lab_arrays():
    # Pure1 lists them in no order of their names
    return [
        # A joined member of fleet1 whose own record names no fleet and no group
        _array("array3", "fb"),
        _array("array1", fleet="fleet1"),
        _array(
            "array2", fleet="fleet1", group="site1", model="FA-C60R3", version="6.4.10"
        ),
        _array(
            "array4",
            "fb",
            model="FlashBlade",
            version="4.1.12",
            as_of=ARRAY_AS_OF - 7000,
        ),
        # The only trace of fleet2 beside its own entry
        _array("array5", fleet="fleet2", model="FA-X50R4", version="6.6.3"),
        # A Cloud Block Store has no domain name
        _array("array6", os="Purity", model="CBS-V10AR1", version="6.6.3", fqdn=None),
    ]


def _lab_fleets():
    return [_fleet("fleet2"), _fleet("fleet1")]


def _lab_members():
    return [
        _member("fleet1", "array2"),
        _member("fleet1", "array1", True),
        _member("fleet1", "array3"),
    ]


def _lab_groups():
    return [
        _group("site2", "region1"),
        _group("region1"),
        _group("site1", "region1"),
    ]


def _lab_group_members():
    return [
        _group_member("region1", "site1", "topology-groups"),
        _group_member("region1", "site2", "topology-groups"),
        _group_member("site1", "array2"),
        _group_member("site2", "array3"),
    ]


def _lab_tags():
    return [
        _tag("array1", "Team", "storage-team"),
        _tag("array4", "Team", ""),
        _tag("array3", "Criticality", "High"),
        _tag("array1", "Criticality", "High"),
        _tag("array4", "Criticality", "Low, High"),
    ]


def _serve_lab(client, in_range=None, **overrides):
    listings = dict(
        arrays=_lab_arrays(),
        fleets=_lab_fleets(),
        members=_lab_members(),
        groups=_lab_groups(),
        group_members=_lab_group_members(),
        tags=_lab_tags(),
    )
    listings.update(overrides)
    _serve(client, in_range=in_range, **listings)


def _result(module):
    module.fail_json.assert_not_called()
    module.exit_json.assert_called_once()
    assert list(module.exit_json.call_args[1]) == ["changed", "pure1_arrays"]
    assert module.exit_json.call_args[1]["changed"] is False
    result = module.exit_json.call_args[1]["pure1_arrays"]
    assert list(result) == ["arrays"]
    return result["arrays"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _documented_option(option):
    """Return the description of an option, as the list of its paragraphs."""
    return yaml.safe_load(pure1_arrays.DOCUMENTATION)["options"][option]["description"]


def _failure(module):
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    assert list(module.fail_json.call_args[1]) == ["msg"]
    return module.fail_json.call_args[1]["msg"]


def _requests(client):
    """Return the number of requests of a run for each listing, in their order."""
    return [len(getattr(client, name).call_args_list) for name in LISTINGS]


def _record(name, product="fa", **overrides):
    """Return what the module gives for an array that _array() made."""
    system, model, version = PRODUCTS[product]
    record = dict(
        id="id-" + name,
        fqdn=name + ".example.com",
        os=system,
        type={"fa": "flasharray", "fb": "flashblade"}[product],
        model=model,
        version=version,
        fleet=None,
        fleet_source=None,
        topology_group=None,
        tags=[],
        as_of=ARRAY_TIME,
        as_of_epoch=ARRAY_AS_OF,
        age_seconds=55,
    )
    record.update(overrides)
    return record


ARRAY1 = {
    "id": "id-array1",
    "fqdn": "array1.example.com",
    "os": "Purity//FA",
    "type": "flasharray",
    "model": "FA-X90R4",
    "version": "6.5.8",
    "fleet": "fleet1",
    "fleet_source": "both",
    "topology_group": None,
    "tags": [
        {"key": "Criticality", "value": "High", "namespace": "pure1"},
        {"key": "Team", "value": "storage-team", "namespace": "pure1"},
    ],
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "age_seconds": 55,
}
ARRAY2 = {
    "id": "id-array2",
    "fqdn": "array2.example.com",
    "os": "Purity//FA",
    "type": "flasharray",
    "model": "FA-C60R3",
    "version": "6.4.10",
    "fleet": "fleet1",
    "fleet_source": "both",
    "topology_group": "site1",
    "tags": [],
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "age_seconds": 55,
}
ARRAY3 = {
    "id": "id-array3",
    "fqdn": "array3.example.com",
    "os": "Purity//FB",
    "type": "flashblade",
    "model": "FlashBlade//S200",
    "version": "4.5.6",
    "fleet": "fleet1",
    "fleet_source": "members",
    "topology_group": "site2",
    "tags": [{"key": "Criticality", "value": "High", "namespace": "pure1"}],
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "age_seconds": 55,
}
ARRAY4 = {
    "id": "id-array4",
    "fqdn": "array4.example.com",
    "os": "Purity//FB",
    "type": "flashblade",
    "model": "FlashBlade",
    "version": "4.1.12",
    "fleet": None,
    "fleet_source": None,
    "topology_group": None,
    "tags": [
        {"key": "Criticality", "value": "Low, High", "namespace": "pure1"},
        {"key": "Team", "value": "", "namespace": "pure1"},
    ],
    "as_of": "2021-02-03 13:03:58 UTC",
    "as_of_epoch": 1612357438000,
    "age_seconds": 62,
}
ARRAY5 = {
    "id": "id-array5",
    "fqdn": "array5.example.com",
    "os": "Purity//FA",
    "type": "flasharray",
    "model": "FA-X50R4",
    "version": "6.6.3",
    "fleet": "fleet2",
    "fleet_source": "arrays",
    "topology_group": None,
    "tags": [],
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "age_seconds": 55,
}
ARRAY6 = {
    "id": "id-array6",
    "fqdn": None,
    "os": "Purity",
    "type": "flasharray",
    "model": "CBS-V10AR1",
    "version": "6.6.3",
    "fleet": None,
    "fleet_source": None,
    "topology_group": None,
    "tags": [],
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "age_seconds": 55,
}
LAB_ARRAYS = {
    "array1": ARRAY1,
    "array2": ARRAY2,
    "array3": ARRAY3,
    "array4": ARRAY4,
    "array5": ARRAY5,
    "array6": ARRAY6,
}


def _lab(*numbers):
    """Return the arrays of the typical answer with these numbers, as the result has them."""
    return {
        "array{0}".format(number): LAB_ARRAYS["array{0}".format(number)]
        for number in numbers
    }


class TestPure1ArraysMain:
    """The whole result of main() for the typical answer."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_array_is_returned(self, pure1_client):
        _serve_lab(pure1_client)

        # Every option is left to the argument spec
        module = run_main(pure1_arrays)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_arrays={
                "arrays": {
                    "array1": {
                        "id": "id-array1",
                        "fqdn": "array1.example.com",
                        "os": "Purity//FA",
                        "type": "flasharray",
                        "model": "FA-X90R4",
                        "version": "6.5.8",
                        "fleet": "fleet1",
                        "fleet_source": "both",
                        "topology_group": None,
                        "tags": [
                            {
                                "key": "Criticality",
                                "value": "High",
                                "namespace": "pure1",
                            },
                            {
                                "key": "Team",
                                "value": "storage-team",
                                "namespace": "pure1",
                            },
                        ],
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "age_seconds": 55,
                    },
                    "array2": {
                        "id": "id-array2",
                        "fqdn": "array2.example.com",
                        "os": "Purity//FA",
                        "type": "flasharray",
                        "model": "FA-C60R3",
                        "version": "6.4.10",
                        "fleet": "fleet1",
                        "fleet_source": "both",
                        "topology_group": "site1",
                        "tags": [],
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "age_seconds": 55,
                    },
                    "array3": {
                        "id": "id-array3",
                        "fqdn": "array3.example.com",
                        "os": "Purity//FB",
                        "type": "flashblade",
                        "model": "FlashBlade//S200",
                        "version": "4.5.6",
                        "fleet": "fleet1",
                        "fleet_source": "members",
                        "topology_group": "site2",
                        "tags": [
                            {
                                "key": "Criticality",
                                "value": "High",
                                "namespace": "pure1",
                            }
                        ],
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "age_seconds": 55,
                    },
                    "array4": {
                        "id": "id-array4",
                        "fqdn": "array4.example.com",
                        "os": "Purity//FB",
                        "type": "flashblade",
                        "model": "FlashBlade",
                        "version": "4.1.12",
                        "fleet": None,
                        "fleet_source": None,
                        "topology_group": None,
                        "tags": [
                            {
                                "key": "Criticality",
                                "value": "Low, High",
                                "namespace": "pure1",
                            },
                            {"key": "Team", "value": "", "namespace": "pure1"},
                        ],
                        "as_of": "2021-02-03 13:03:58 UTC",
                        "as_of_epoch": 1612357438000,
                        "age_seconds": 62,
                    },
                    "array5": {
                        "id": "id-array5",
                        "fqdn": "array5.example.com",
                        "os": "Purity//FA",
                        "type": "flasharray",
                        "model": "FA-X50R4",
                        "version": "6.6.3",
                        "fleet": "fleet2",
                        "fleet_source": "arrays",
                        "topology_group": None,
                        "tags": [],
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "age_seconds": 55,
                    },
                    "array6": {
                        "id": "id-array6",
                        "fqdn": None,
                        "os": "Purity",
                        "type": "flasharray",
                        "model": "CBS-V10AR1",
                        "version": "6.6.3",
                        "fleet": None,
                        "fleet_source": None,
                        "topology_group": None,
                        "tags": [],
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "age_seconds": 55,
                    },
                }
            },
        )
        module.fail_json.assert_not_called()
        # The arrays whose two sources differ give no warning: fleet_source says it
        module.warn.assert_not_called()

    def test_arrays_are_collected_in_check_mode(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": LAB_ARRAYS}
        )
        module.warn.assert_not_called()

    def test_each_listing_is_requested_once_and_unfiltered(self, pure1_client):
        _serve_lab(pure1_client)

        run_main(pure1_arrays)

        # The first is the check get_pure1 makes of every new client
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]
        pure1_client.get_fleets.assert_called_once_with()
        pure1_client.get_fleets_members.assert_called_once_with()
        pure1_client.get_topology_groups.assert_called_once_with()
        pure1_client.get_topology_groups_members.assert_called_once_with()
        pure1_client.get_arrays_tags.assert_called_once_with()

    @pytest.mark.parametrize(
        "params, requests",
        [
            pytest.param({}, [2, 1, 1, 1, 1, 1], id="default"),
            pytest.param({"name": ["array1"]}, [2, 1, 1, 1, 1, 1], id="name"),
            pytest.param({"type": "flashblade"}, [2, 1, 1, 1, 1, 1], id="type"),
            pytest.param({"model": "x90"}, [2, 1, 1, 1, 1, 1], id="model"),
            pytest.param({"fleet": "fleet1"}, [2, 1, 1, 1, 1, 1], id="fleet"),
            pytest.param(
                {"topology_group": "region1"}, [2, 1, 1, 1, 1, 1], id="topology_group"
            ),
            pytest.param({"tag": ["Team"]}, [2, 1, 1, 1, 1, 1], id="tag"),
            pytest.param(
                {"type": "flasharray", "version_below": "6.6.0"},
                [3, 1, 1, 1, 1, 1],
                id="version_below",
            ),
            pytest.param(
                {"type": "flasharray", "version_at_least": "6.5.0"},
                [3, 1, 1, 1, 1, 1],
                id="version_at_least",
            ),
            pytest.param(
                {
                    "type": "flasharray",
                    "version_at_least": "6.5.0",
                    "version_below": "6.6.0",
                },
                [3, 1, 1, 1, 1, 1],
                id="version range: one request for both",
            ),
            pytest.param(
                {
                    "name": ["array1", "array2"],
                    "type": "flasharray",
                    "model": "fa",
                    "version_below": "6.6.0",
                    "fleet": "fleet1",
                    "topology_group": "region1",
                    "tag": ["Team"],
                },
                [3, 1, 1, 1, 1, 1],
                id="every option",
            ),
            pytest.param(
                {"name": ["array9"]}, [2, 1, 1, 1, 1, 0], id="nothing selected: no tags"
            ),
            pytest.param(
                {"name": ["array9"], "type": "flasharray", "version_below": "6.6.0"},
                [2, 1, 1, 1, 1, 0],
                id="nothing selected: no versions and no tags",
            ),
            pytest.param(
                {"type": "flashblade", "version_below": "6.6.0"},
                [3, 1, 1, 1, 1, 0],
                id="no array in the version range: no tags",
            ),
        ],
    )
    def test_requests_of_a_run(self, pure1_client, params, requests):
        # What Pure1 finds in the version range of a task that gives one
        in_range = [_array("array1"), _array("array2")]
        if params.get("type") == "flashblade":
            in_range = []
        _serve_lab(pure1_client, in_range=in_range)

        module = run_main(pure1_arrays, **params)

        _result(module)
        # get_arrays: the check of get_pure1, the listing, the version range
        assert _requests(pure1_client) == requests

    @pytest.mark.parametrize(
        "params",
        [{"name": []}, {"tag": []}, {"model": ""}, {"name": [], "tag": []}],
        ids=["no name", "no tag", "no model", "no name and no tag"],
    )
    def test_empty_value_counts_as_not_set(self, pure1_client, params):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, **params)

        assert _result(module) == LAB_ARRAYS
        module.warn.assert_not_called()

    def test_arrays_are_in_the_order_of_their_names(self, pure1_client):
        _serve_lab(pure1_client)

        result = _result(run_main(pure1_arrays, name=["array5", "array3", "array4"]))

        assert list(result) == ["array3", "array4", "array5"]
        # as the keys of each array are in the order of the documentation
        assert list(result["array3"]) == [
            "id",
            "fqdn",
            "os",
            "type",
            "model",
            "version",
            "fleet",
            "fleet_source",
            "topology_group",
            "tags",
            "as_of",
            "as_of_epoch",
            "age_seconds",
        ]

    def test_answers_without_items(self, pure1_client):
        _serve(pure1_client)

        module = run_main(pure1_arrays)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        # No array to attach a tag to
        assert _requests(pure1_client) == [2, 1, 1, 1, 1, 0]

    def test_unknown_option_is_refused(self, pure1_client):
        module = run_main(pure1_arrays, filter="name='array1'")

        assert _failure(module).startswith(
            "Unsupported parameters for module: filter. Supported parameters include: "
        )

    def test_names_given_as_one_string_are_split(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name="array2,array1")

        assert _result(module) == _lab(1, 2)

    def test_function_returns_what_main_exits_with(self, pure1_client):
        module = ansible_module(
            name=None,
            type=None,
            model=None,
            version_below=None,
            version_at_least=None,
            fleet=None,
            topology_group=None,
            tag=None,
        )
        _serve_lab(pure1_client)
        # No get_pure1 here: the first answer is the one to its check
        pure1_client.get_arrays(limit=0)

        result = generate_arrays_dict(module, pure1_client)

        assert result == {"arrays": LAB_ARRAYS}
        module.exit_json.assert_not_called()


class TestRequiredRelease:
    """A py-pure-client without the methods fails the task before any request."""

    @pytest.mark.parametrize(
        "missing, named, minimum",
        [
            pytest.param(
                ["get_fleets_members"], "get_fleets_members", "1.99.0", id="members"
            ),
            pytest.param(
                ["get_topology_groups"], "get_topology_groups", "1.99.0", id="groups"
            ),
            pytest.param(
                ["get_topology_groups_members"],
                "get_topology_groups_members",
                "1.99.0",
                id="group members",
            ),
            pytest.param(["get_fleets"], "get_fleets", "1.93.0", id="fleets"),
            pytest.param(
                [
                    "get_fleets",
                    "get_fleets_members",
                    "get_topology_groups",
                    "get_topology_groups_members",
                ],
                "get_fleets_members",
                "1.99.0",
                id="none of them: the newest release is named",
            ),
        ],
    )
    def test_client_without_a_method(
        self, pure1_client, monkeypatch, missing, named, minimum
    ):
        _serve_lab(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.92.0")
        for method in missing:
            # What a client of an older release has for the name: nothing to call
            setattr(pure1_client, method, None)

        module = run_main(pure1_arrays)

        assert _failure(module) == (
            "py-pure-client {0} or later is required: this module needs "
            "Client.{1}(), which the installed py-pure-client 1.92.0 does not "
            "have.".format(minimum, named)
        )
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_arrays_tags.assert_not_called()


class TestNameOption:
    """name returns the arrays it names, and no more than Pure1 knows."""

    def test_one_array(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=["array4"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {"array4": ARRAY4}}
        )
        module.warn.assert_not_called()

    def test_several_arrays(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=["array6", "array1", "array3"])

        assert _result(module) == _lab(1, 3, 6)

    def test_names_are_not_sent_to_pure1(self, pure1_client):
        """Every array is listed, for the fleets: the names narrow the result here."""
        _serve_lab(pure1_client)

        run_main(pure1_arrays, name=["array1"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]

    @pytest.mark.parametrize(
        "names",
        [["ARRAY3"], ["Array3", "array3"], ["array3", "array3"], ["ARRAY3", "aRRAY3"]],
        ids=["other case", "two cases", "twice", "twice in other cases"],
    )
    def test_name_in_any_case_gives_the_array_once_as_pure1_spells_it(
        self, pure1_client, names
    ):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=names)

        assert _result(module) == {"array3": ARRAY3}

    def test_arrays_whose_names_differ_in_case_only_are_both_returned(
        self, pure1_client
    ):
        arrays = [_array("array1"), _array("Array1", "fb", id="id-other")]
        _serve(pure1_client, arrays)

        module = run_main(pure1_arrays, name=["ARRAY1"])

        assert _result(module) == {
            "Array1": _record("Array1", "fb", id="id-other"),
            "array1": _record("array1"),
        }

    def test_name_that_no_array_has_is_absent_and_no_error(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=["array9", "array2", "fleet1", ""])

        assert _result(module) == {"array2": ARRAY2}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "names",
        [["array9"], ["array"], ["array1.example.com"], ["id-array1"], [""]],
        ids=["unknown", "part of a name", "domain name", "id", "empty"],
    )
    def test_names_that_no_array_has_give_an_empty_result(self, pure1_client, names):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=names)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()


class TestTypeOption:
    """type returns the arrays of one product, by their operating system."""

    @pytest.mark.parametrize(
        "product, numbers",
        [("flasharray", (1, 2, 5, 6)), ("flashblade", (3, 4)), ("objectengine", ())],
    )
    def test_each_product(self, pure1_client, product, numbers):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, type=product)

        assert _result(module) == _lab(*numbers)
        # The product is not asked of Pure1: the result is narrowed here
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]

    def test_objectengine_is_the_array_that_runs_elasticity(self, pure1_client):
        arrays = [
            _array("array1"),
            _array("array2", os="Elasticity", model="OE-1", version="1.2.3"),
            _array("array3", "fb"),
        ]
        _serve(pure1_client, arrays)

        module = run_main(pure1_arrays, type="objectengine")

        assert _result(module) == {
            "array2": _record(
                "array2",
                os="Elasticity",
                type="objectengine",
                model="OE-1",
                version="1.2.3",
            )
        }

    @pytest.mark.parametrize("product", ["flasharray", "flashblade", "objectengine"])
    def test_array_of_an_unknown_system_is_of_no_product(self, pure1_client, product):
        arrays = [_array("array1", os="Other"), sdk_model("Array", name="array2")]
        _serve(pure1_client, arrays)

        of_the_product = _result(run_main(pure1_arrays, type=product))
        _serve(pure1_client, arrays)
        every = _result(run_main(pure1_arrays))

        assert of_the_product == {}
        assert every["array1"] == _record("array1", os="Other", type=None)
        assert every["array2"]["type"] is None

    def test_value_that_is_no_product_is_refused(self, pure1_client):
        module = run_main(pure1_arrays, type="cbs")

        assert _failure(module) == (
            "value of type must be one of: flasharray, flashblade, objectengine, "
            "got: cbs"
        )
        pure1_client.get_arrays.assert_not_called()


class TestModelOption:
    """model returns the arrays whose model holds the text, in any case."""

    @pytest.mark.parametrize(
        "text, numbers",
        [
            pytest.param("x90", (1,), id="lower case"),
            pytest.param("FA-X90R4", (1,), id="whole model"),
            pytest.param("fa-", (1, 2, 5), id="start of three models"),
            pytest.param("R4", (1, 5), id="end of two models"),
            pytest.param("flashblade", (3, 4), id="two models of one product"),
            pytest.param("//s", (3,), id="slashes"),
            pytest.param("r", (1, 2, 5, 6), id="one letter"),
            pytest.param("X70", (), id="no model"),
            pytest.param("x90 ", (), id="nothing is trimmed"),
        ],
    )
    def test_text_in_the_model(self, pure1_client, text, numbers):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, model=text)

        assert _result(module) == _lab(*numbers)
        module.warn.assert_not_called()

    def test_array_without_a_model_is_not_selected(self, pure1_client):
        arrays = [_array("array1"), _array("array2", model=None)]
        _serve(pure1_client, arrays)

        module = run_main(pure1_arrays, model="x")

        assert _result(module) == {"array1": _record("array1")}

    def test_model_is_not_sent_to_pure1(self, pure1_client):
        _serve_lab(pure1_client)

        run_main(pure1_arrays, model="x90")

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]


class TestVersionOptions:
    """Pure1 compares the versions: the module asks it for the arrays of the range."""

    def test_arrays_below_a_version(self, pure1_client):
        # What Pure1 finds below 6.6.0 among the FlashArrays
        _serve_lab(pure1_client, in_range=[_array("array2"), _array("array1")])

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == _lab(1, 2)
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
            call(filter="os=('Purity//FA','Purity') and version<'6.6.0'"),
        ]
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params, version_filter",
        [
            pytest.param(
                {"type": "flasharray", "version_at_least": "6.5.0"},
                "os=('Purity//FA','Purity') and version>='6.5.0'",
                id="flasharray at least",
            ),
            pytest.param(
                {"type": "flashblade", "version_below": "4.5.0"},
                "os='Purity//FB' and version<'4.5.0'",
                id="flashblade below",
            ),
            pytest.param(
                {
                    "type": "objectengine",
                    "version_below": "2.0.0",
                    "version_at_least": "1.2.0",
                },
                "os='Elasticity' and version>='1.2.0' and version<'2.0.0'",
                id="objectengine range",
            ),
            pytest.param(
                {"type": "flashblade", "version_at_least": "4"},
                "os='Purity//FB' and version>='4'",
                id="one number",
            ),
            pytest.param(
                {"type": "flashblade", "version_below": "4.5'x\\"},
                "os='Purity//FB' and version<'4.5\\'x\\\\'",
                id="quote and backslash are escaped",
            ),
            pytest.param(
                {"type": "flashblade", "version_below": " 4.5.0 "},
                "os='Purity//FB' and version<' 4.5.0 '",
                id="nothing is trimmed",
            ),
        ],
    )
    def test_filter_of_the_request(self, pure1_client, params, version_filter):
        # An array of each product, for a range to narrow
        arrays = _lab_arrays() + [_array("array7", os="Elasticity")]
        _serve_lab(pure1_client, arrays=arrays, in_range=[])

        module = run_main(pure1_arrays, **params)

        assert _result(module) == {}
        assert pure1_client.get_arrays.call_args_list[2:] == [
            call(filter=version_filter)
        ]

    def test_version_given_as_a_number_is_sent_as_text(self, pure1_client):
        """What YAML makes of an unquoted 6.10: the documentation says to quote."""
        _serve_lab(pure1_client, in_range=[])

        run_main(pure1_arrays, type="flasharray", version_below=6.10)

        assert pure1_client.get_arrays.call_args_list[2] == call(
            filter="os=('Purity//FA','Purity') and version<'6.1'"
        )

    def test_range_narrows_what_the_other_options_select(self, pure1_client):
        in_range = [_array("array1"), _array("array2"), _array("array5")]
        _serve_lab(pure1_client, in_range=in_range)

        module = run_main(
            pure1_arrays, type="flasharray", version_at_least="6.4.0", fleet="fleet1"
        )

        # array5 is in the range and in another fleet, array6 in neither
        assert _result(module) == _lab(1, 2)

    def test_array_is_joined_by_its_id_not_by_its_name(self, pure1_client):
        _serve_lab(pure1_client, in_range=[_array("array2", id="id-array1")])

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == _lab(1)

    def test_array_with_only_its_id_in_the_range(self, pure1_client):
        _serve_lab(pure1_client, in_range=[sdk_model("Array", id="id-array5")])

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == _lab(5)

    def test_array_without_an_id_is_joined_by_its_name(self, pure1_client):
        arrays = [
            _array("array1", id=None),
            _array("array2", id=None),
            _array("array3"),
        ]
        # An array that has an id is not found by a name alone
        in_range = [
            sdk_model("Array", name="array2"),
            sdk_model("Array", name="array3"),
        ]
        _serve(pure1_client, arrays, in_range=in_range)

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == {"array2": _record("array2", id=None)}

    def test_array_of_the_range_that_pure1_lists_no_more_is_absent(self, pure1_client):
        in_range = [_array("array1"), _array("array9")]
        _serve_lab(pure1_client, in_range=in_range)

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == _lab(1)
        module.warn.assert_not_called()

    def test_no_array_in_the_range(self, pure1_client):
        _serve_lab(pure1_client, in_range=[])

        module = run_main(pure1_arrays, type="flashblade", version_below="4.0.0")

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.warn.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    @pytest.mark.parametrize("option", ["version_below", "version_at_least"])
    def test_version_needs_the_product(self, pure1_client, option):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, **{option: "6.6.0"})

        # In the words that this ansible-core has for an option that needs
        # another one
        with pytest.raises(TypeError) as refused:
            check_required_by({option: "type"}, {option: "6.6.0"})
        message = _failure(module)
        assert message == str(refused.value)
        assert option in message and "type" in message
        # The argument spec refuses the task before the client is made
        pure1_client.get_arrays.assert_not_called()

    @pytest.mark.parametrize("option", ["version_below", "version_at_least"])
    def test_version_that_pure1_cannot_read_is_no_error_when_no_array_is_selected(
        self, pure1_client, option
    ):
        """The versions are not compared then, as the option says."""
        _serve_lab(pure1_client)

        module = run_main(
            pure1_arrays,
            name=["array9"],
            type="flasharray",
            **{option: "not a version"}
        )

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.fail_json.assert_not_called()
        # Pure1 never saw the text
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]
        text = " ".join(_documented_option(option))
        assert (
            "It also fails when Pure1 cannot read it as a version, unless the "
            "other options select no array, because the versions are not "
            "compared then."
        ) in text

    @pytest.mark.parametrize("option", ["version_below", "version_at_least"])
    @pytest.mark.parametrize("value", ["", "  "], ids=["empty", "blank"])
    def test_empty_version_fails_the_task(self, pure1_client, option, value):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, type="flasharray", **{option: value})

        assert _failure(module) == "The value of {0} must not be empty.".format(option)
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_version_that_pure1_cannot_read_fails_the_task(self, pure1_client):
        refused = error_response(
            400,
            "Illegal filter format",
            "Unable to parse filter: os='Purity//FB' and version>='new'",
        )
        _serve_lab(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response(_lab_arrays()),
            refused,
        ]

        module = run_main(pure1_arrays, type="flashblade", version_at_least="new")

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 400): Illegal filter format: "
            "Unable to parse filter: os='Purity//FB' and version>='new'"
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    def test_raised_error_of_the_request_for_the_range(self, pure1_client):
        _serve_lab(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response(_lab_arrays()),
            TimeoutError("The read operation timed out"),
        ]

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _failure(module) == (
            "Pure1 request get_arrays failed: TimeoutError: The read operation "
            "timed out"
        )

    def test_fewer_arrays_of_the_range_than_pure1_counted(self, pure1_client):
        _serve_lab(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response(_lab_arrays()),
            valid_response([_array("array1")], total_item_count=3),
            valid_response([], total_item_count=3),
        ]

        module = run_main(pure1_arrays, type="flasharray", version_below="6.6.0")

        assert _result(module) == _lab(1)
        assert _warnings(module) == [
            "Pure1 returned 1 of 3 arrays of the version range: the result may "
            "be incomplete."
        ]


class TestFleetOption:
    """fleet returns the arrays that the one rule of the collection places in it."""

    def test_arrays_of_a_fleet(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet="fleet1")

        # array3 by its member row alone: its own record names no fleet
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_arrays={
                "arrays": {"array1": ARRAY1, "array2": ARRAY2, "array3": ARRAY3}
            },
        )
        module.warn.assert_not_called()

    def test_fleet_that_only_an_array_record_places_an_array_in(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet="fleet2")

        assert _result(module) == {"array5": ARRAY5}

    @pytest.mark.parametrize("name", ["FLEET1", "Fleet1", "fLEET1"])
    def test_fleet_in_any_case(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet=name)

        assert _result(module) == _lab(1, 2, 3)

    def test_row_wins_over_the_fleet_of_the_array_record(self, pure1_client):
        """array7 has a row in fleet1 while its record names fleet2."""
        arrays = _lab_arrays() + [_array("array7", fleet="fleet2")]
        members = _lab_members() + [_member("fleet1", "array7")]
        array7 = _record("array7", fleet="fleet1", fleet_source="members")

        _serve_lab(pure1_client, arrays=arrays, members=members)
        of_fleet1 = _result(run_main(pure1_arrays, fleet="fleet1"))
        _serve_lab(pure1_client, arrays=arrays, members=members)
        of_fleet2 = _result(run_main(pure1_arrays, fleet="fleet2"))

        assert of_fleet1 == dict(_lab(1, 2, 3), array7=array7)
        assert of_fleet2 == {"array5": ARRAY5}

    def test_array_with_rows_in_two_fleets_is_in_the_fleet_the_result_names(
        self, pure1_client
    ):
        members = _lab_members() + [_member("fleet2", "array1")]

        _serve_lab(pure1_client, members=members)
        of_fleet1 = _result(run_main(pure1_arrays, fleet="fleet1"))
        _serve_lab(pure1_client, members=members)
        of_fleet2 = _result(run_main(pure1_arrays, fleet="fleet2"))

        # array1 is in the fleet that its own record names, and in that one only
        assert of_fleet1 == _lab(1, 2, 3)
        assert of_fleet2 == _lab(5)

    def test_fleet_without_arrays_gives_an_empty_result(self, pure1_client):
        _serve_lab(pure1_client, fleets=_lab_fleets() + [_fleet("fleet3")])

        module = run_main(pure1_arrays, fleet="fleet3")

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.warn.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    def test_fleet_whose_member_no_array_has_the_name_of(self, pure1_client):
        members = _lab_members() + [_member("fleet2", "array9")]
        arrays = [array for array in _lab_arrays() if array.name != "array5"]
        _serve_lab(pure1_client, arrays=arrays, members=members)

        module = run_main(pure1_arrays, fleet="fleet2")

        assert _result(module) == {}

    def test_fleet_that_pure1_does_not_list_can_be_named(self, pure1_client):
        """fleet7 is known by a member row alone."""
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet7"), _array("array2")],
            [],
            [_member("fleet7", "array1")],
        )

        module = run_main(pure1_arrays, fleet="Fleet7")

        assert _result(module) == {
            "array1": _record("array1", fleet="fleet7", fleet_source="both")
        }

    @pytest.mark.parametrize(
        "name",
        ["fleet9", "array1", "fleet", "site1", "id-fleet1", "Fleet9"],
        ids=[
            "unknown",
            "an array",
            "part of a name",
            "a group",
            "an id",
            "unknown with a capital",
        ],
    )
    def test_fleet_pure1_does_not_know_fails_the_task(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet=name)

        # The fleet as the task wrote it
        module.fail_json.assert_called_once_with(
            msg="Fleet {0} is not known to Pure1.".format(name)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    def test_unknown_fleet_fails_also_when_no_array_is_selected(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=["array9"], fleet="fleet9")

        assert _failure(module) == "Fleet fleet9 is not known to Pure1."

    @pytest.mark.parametrize("name", ["", "  "], ids=["empty", "blank"])
    def test_empty_fleet_fails_the_task(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet=name)

        assert _failure(module) == "The name of a fleet must not be empty."
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    @pytest.mark.parametrize(
        "name, number", [("Fleet1", 2), ("fleet1", 1)], ids=["upper", "lower"]
    )
    def test_fleets_that_differ_in_case_only(self, pure1_client, name, number):
        fleets = [_fleet("fleet1"), _fleet("Fleet1", id="id-other")]
        members = [_member("fleet1", "array1"), _member("Fleet1", "array2")]
        arrays = [_array("array1", fleet="fleet1"), _array("array2", fleet="Fleet1")]
        _serve(pure1_client, arrays, fleets, members)

        module = run_main(pure1_arrays, fleet=name)

        # The spelling of the task wins over the same name in another case
        array = "array{0}".format(number)
        assert _result(module) == {
            array: _record(array, fleet=name, fleet_source="both")
        }

    def test_name_that_matches_two_fleets_in_another_case_fails(self, pure1_client):
        fleets = [_fleet("fleet1"), _fleet("Fleet1", id="id-other")]
        _serve(pure1_client, [_array("array1")], fleets)

        module = run_main(pure1_arrays, fleet="FLEET1")

        assert _failure(module) == (
            "Pure1 knows more than one fleet whose name is FLEET1 in another "
            "case: Fleet1, fleet1. Give the name as Pure1 spells it."
        )


class TestTopologyGroupOption:
    """topology_group returns the arrays in the group and in every group below it."""

    def test_arrays_directly_in_a_group(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, topology_group="site1")

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {"array2": ARRAY2}}
        )
        module.warn.assert_not_called()

    def test_arrays_of_the_groups_below_a_group(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, topology_group="region1")

        # Neither array is directly in region1; array3 is in site2 by a row alone
        assert _result(module) == _lab(2, 3)

    def test_arrays_directly_in_the_group_and_below_it(self, pure1_client):
        rows = _lab_group_members() + [_group_member("region1", "array1")]
        _serve_lab(pure1_client, group_members=rows)

        module = run_main(pure1_arrays, topology_group="region1")

        assert _result(module) == dict(
            _lab(2, 3), array1=dict(ARRAY1, topology_group="region1")
        )

    def test_groups_three_deep(self, pure1_client):
        groups = _lab_groups() + [_group("room1", "site1")]
        rows = _lab_group_members() + [
            _group_member("site1", "room1", "topology-groups"),
            _group_member("room1", "array1"),
        ]
        in_room1 = dict(ARRAY1, topology_group="room1")

        selected = {}
        for group in ("region1", "site1", "room1", "site2"):
            _serve_lab(pure1_client, groups=groups, group_members=rows)
            selected[group] = _result(run_main(pure1_arrays, topology_group=group))

        assert selected == {
            "region1": dict(_lab(2, 3), array1=in_room1),
            "site1": dict(_lab(2), array1=in_room1),
            "room1": {"array1": in_room1},
            "site2": _lab(3),
        }

    @pytest.mark.parametrize("name", ["REGION1", "Region1", "rEGION1"])
    def test_group_in_any_case(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, topology_group=name)

        assert _result(module) == _lab(2, 3)

    def test_group_without_arrays_gives_an_empty_result(self, pure1_client):
        _serve_lab(pure1_client, groups=_lab_groups() + [_group("site3", "region1")])

        module = run_main(pure1_arrays, topology_group="site3")

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        module.warn.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    def test_group_that_only_the_record_of_an_array_names(self, pure1_client):
        """No listing has site7: the array is in it by its own record alone."""
        arrays = _lab_arrays() + [_array("array7", group="site7")]
        _serve_lab(pure1_client, arrays=arrays)

        module = run_main(pure1_arrays, topology_group="Site7")

        assert _result(module) == {"array7": _record("array7", topology_group="site7")}

    def test_group_of_the_record_of_an_array_that_a_listing_has(self, pure1_client):
        """array1 has no row in a group, and its record names site2."""
        arrays = _lab_arrays()
        arrays[1] = _array("array1", fleet="fleet1", group="site2")
        _serve_lab(pure1_client, arrays=arrays)

        module = run_main(pure1_arrays, topology_group="region1")

        assert _result(module) == dict(
            _lab(2, 3), array1=dict(ARRAY1, topology_group="site2")
        )

    def test_array_with_rows_in_two_groups_counts_for_the_group_the_result_names(
        self, pure1_client
    ):
        rows = _lab_group_members() + [_group_member("site2", "array2")]

        _serve_lab(pure1_client, group_members=rows)
        of_site1 = _result(run_main(pure1_arrays, topology_group="site1"))
        _serve_lab(pure1_client, group_members=rows)
        of_site2 = _result(run_main(pure1_arrays, topology_group="site2"))

        # The group that the record of array2 names
        assert of_site1 == _lab(2)
        assert of_site2 == _lab(3)

    @pytest.mark.parametrize(
        "name",
        ["site9", "array2", "site", "fleet1", "id-fleet1-site1", "Site9"],
        ids=[
            "unknown",
            "an array",
            "part of a name",
            "a fleet",
            "an id",
            "unknown with a capital",
        ],
    )
    def test_group_pure1_does_not_know_fails_the_task(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, topology_group=name)

        # The group as the task wrote it
        module.fail_json.assert_called_once_with(
            msg="Topology group {0} is not known to Pure1.".format(name)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        pure1_client.get_arrays_tags.assert_not_called()

    def test_unknown_group_fails_also_when_no_array_is_selected(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, name=["array9"], topology_group="site9")

        assert _failure(module) == "Topology group site9 is not known to Pure1."

    def test_known_group_is_no_error_when_the_other_options_select_no_array(
        self, pure1_client
    ):
        """site7 is known by the record of array7, which name leaves out."""
        arrays = _lab_arrays() + [_array("array7", group="site7")]
        _serve_lab(pure1_client, arrays=arrays)

        module = run_main(pure1_arrays, name=["array1"], topology_group="site7")

        assert _result(module) == {}

    def test_no_group_at_all_makes_every_name_unknown(self, pure1_client):
        _serve(pure1_client, [_array("array1")])

        module = run_main(pure1_arrays, topology_group="site1")

        assert _failure(module) == "Topology group site1 is not known to Pure1."

    @pytest.mark.parametrize("name", ["", "  "], ids=["empty", "blank"])
    def test_empty_group_fails_the_task(self, pure1_client, name):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, topology_group=name)

        assert _failure(module) == "The name of a topology group must not be empty."
        pure1_client.get_arrays.assert_called_once_with(limit=0)


def _two_fleets_with_one_group_name():
    """Return the listings of two fleets that each have a group site1."""
    return dict(
        arrays=[
            _array("array1", fleet="fleet1"),
            _array("array2", fleet="fleet2"),
            _array("array3", fleet="fleet2"),
            _array("array4"),
        ],
        fleets=[_fleet("fleet1"), _fleet("fleet2"), _fleet("fleet3")],
        members=[
            _member("fleet1", "array1"),
            _member("fleet2", "array2"),
            _member("fleet2", "array3"),
        ],
        groups=[
            _group("site1"),
            _group("Site1", fleet="fleet2"),
            _group("site2", fleet="fleet2"),
        ],
        group_members=[
            _group_member("site1", "array1"),
            _group_member("Site1", "array2", fleet="fleet2"),
            _group_member("site2", "array3", fleet="fleet2"),
        ],
    )


def _placed(name, fleet, group):
    return _record(name, fleet=fleet, fleet_source="both", topology_group=group)


class TestFleetWithTopologyGroup:
    """fleet beside topology_group selects the group of that fleet."""

    def test_group_of_the_fleet_and_below(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, fleet="fleet1", topology_group="region1")

        assert _result(module) == _lab(2, 3)
        module.warn.assert_not_called()

    def test_name_that_groups_of_two_fleets_have_selects_both(self, pure1_client):
        _serve(pure1_client, **_two_fleets_with_one_group_name())

        module = run_main(pure1_arrays, topology_group="site1")

        assert _result(module) == {
            "array1": _placed("array1", "fleet1", "site1"),
            "array2": _placed("array2", "fleet2", "Site1"),
        }

    @pytest.mark.parametrize(
        "fleet, array, group",
        [("fleet1", "array1", "site1"), ("FLEET2", "array2", "Site1")],
    )
    def test_fleet_selects_the_group_of_one_fleet(
        self, pure1_client, fleet, array, group
    ):
        _serve(pure1_client, **_two_fleets_with_one_group_name())

        module = run_main(pure1_arrays, fleet=fleet, topology_group="SITE1")

        assert _result(module) == {array: _placed(array, fleet.lower(), group)}

    @pytest.mark.parametrize(
        "fleet, group",
        [
            ("fleet1", "site2"),
            ("fleet3", "site1"),
            ("Fleet3", "site2"),
            ("FLEET2", "Site9"),
        ],
        ids=[
            "group of another fleet",
            "fleet without groups",
            "fleet in another case",
            "unknown group with a capital",
        ],
    )
    def test_group_that_the_fleet_does_not_have_fails_the_task(
        self, pure1_client, fleet, group
    ):
        _serve(pure1_client, **_two_fleets_with_one_group_name())

        module = run_main(pure1_arrays, fleet=fleet, topology_group=group)

        # The fleet as Pure1 spells it, the group as the task wrote it
        module.fail_json.assert_called_once_with(
            msg="Topology group {0} is not known to Pure1 in fleet {1}.".format(
                group, fleet.lower()
            )
        )
        module.exit_json.assert_not_called()

    def test_unknown_fleet_is_named_before_the_group(self, pure1_client):
        _serve(pure1_client, **_two_fleets_with_one_group_name())

        module = run_main(pure1_arrays, fleet="fleet9", topology_group="site9")

        assert _failure(module) == "Fleet fleet9 is not known to Pure1."

    def test_group_of_the_fleet_without_arrays_gives_an_empty_result(
        self, pure1_client
    ):
        """site3 is a group of fleet1 that no array is in or below."""
        listings = _two_fleets_with_one_group_name()
        listings["groups"].append(_group("site3"))

        _serve(pure1_client, **listings)
        of_fleet1 = run_main(pure1_arrays, fleet="fleet1", topology_group="Site3")
        _serve(pure1_client, **listings)
        of_fleet2 = run_main(pure1_arrays, fleet="fleet2", topology_group="Site3")

        # The fleet has the group: no array in it is no failure
        of_fleet1.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {}}
        )
        of_fleet1.fail_json.assert_not_called()
        of_fleet1.warn.assert_not_called()
        assert _failure(of_fleet2) == (
            "Topology group Site3 is not known to Pure1 in fleet fleet2."
        )

    def test_group_of_another_fleet_that_an_array_of_the_fleet_is_in(
        self, pure1_client
    ):
        """array4 is a member of fleet1 and has a row in site2 of fleet2."""
        listings = _two_fleets_with_one_group_name()
        listings["members"].append(_member("fleet1", "array4"))
        listings["group_members"].append(
            _group_member("site2", "array4", fleet="fleet2")
        )
        _serve(pure1_client, **listings)

        module = run_main(pure1_arrays, fleet="fleet1", topology_group="site2")

        assert _result(module) == {
            "array4": _record(
                "array4", fleet="fleet1", fleet_source="members", topology_group="site2"
            )
        }

    def test_group_that_only_the_record_of_an_array_of_the_fleet_names(
        self, pure1_client
    ):
        arrays = _lab_arrays() + [
            _array("array7", fleet="fleet2", group="site7"),
            _array("array8", group="site7"),
        ]
        array7 = _record(
            "array7", fleet="fleet2", fleet_source="arrays", topology_group="site7"
        )

        _serve_lab(pure1_client, arrays=arrays)
        of_fleet2 = run_main(pure1_arrays, fleet="fleet2", topology_group="site7")
        _serve_lab(pure1_client, arrays=arrays)
        of_fleet1 = run_main(pure1_arrays, fleet="fleet1", topology_group="site7")
        _serve_lab(pure1_client, arrays=arrays)
        of_any = run_main(pure1_arrays, topology_group="site7")

        assert _result(of_fleet2) == {"array7": array7}
        assert _failure(of_fleet1) == (
            "Topology group site7 is not known to Pure1 in fleet fleet1."
        )
        assert _result(of_any) == {
            "array7": array7,
            "array8": _record("array8", topology_group="site7"),
        }


class TestTagOption:
    """tag returns the arrays that have every tag it names."""

    @pytest.mark.parametrize(
        "tags, numbers",
        [
            pytest.param(["Criticality:High"], (1, 3), id="key and value"),
            pytest.param(["Criticality"], (1, 3, 4), id="key alone: any value"),
            pytest.param(["Team"], (1, 4), id="key alone: also an empty value"),
            pytest.param(["Team:storage-team"], (1,), id="value with a hyphen"),
            pytest.param(["Criticality:Low, High"], (4,), id="value with a comma"),
            pytest.param(["Team:"], (4,), id="empty value"),
            pytest.param(["Criticality:High", "Team"], (1,), id="two tags: both"),
            pytest.param(
                ["Team", "Criticality:High"], (1,), id="two tags: other order"
            ),
            pytest.param(["Criticality", "Team"], (1, 4), id="two keys"),
            pytest.param(
                ["Criticality:High", "Criticality:High"],
                (1, 3),
                id="one tag twice",
            ),
            pytest.param(
                ["Criticality:High", "Team:"],
                (),
                id="two tags that no array has both of",
            ),
            pytest.param(["criticality"], (), id="key in another case"),
            pytest.param(["Criticality:high"], (), id="value in another case"),
            pytest.param(["Criticality:Hig"], (), id="part of a value"),
            pytest.param(["Critical"], (), id="part of a key"),
            pytest.param(["Criticality:Low"], (), id="part of a value with a comma"),
            pytest.param(["Criticality: High"], (), id="nothing is trimmed"),
            pytest.param([" Team"], (), id="space before the key"),
            pytest.param(["Team "], (), id="space after the key"),
            pytest.param(["Team:storage-team "], (), id="space after the value"),
            pytest.param(["Rack"], (), id="key that no array has"),
            pytest.param(["pure1"], (), id="namespace is no key"),
            pytest.param(["array1"], (), id="name of an array is no key"),
        ],
    )
    def test_arrays_with_the_tags(self, pure1_client, tags, numbers):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, tag=tags)

        assert _result(module) == _lab(*numbers)
        module.warn.assert_not_called()

    def test_whole_result_for_a_tag(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, tag=["Team:"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_arrays={"arrays": {"array4": ARRAY4}}
        )

    def test_tags_are_not_asked_of_pure1_by_key(self, pure1_client):
        """One listing of every tag: the arrays show all their tags."""
        _serve_lab(pure1_client)

        run_main(pure1_arrays, tag=["Team:storage-team"])

        pure1_client.get_arrays_tags.assert_called_once_with()
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
        ]

    def test_tag_is_split_at_its_first_colon(self, pure1_client):
        tags = [
            _tag("array1", "url", "https://example.com:8443/a"),
            _tag("array2", "url:https", "//example.com:8443/a"),
            _tag("array3", "url", "https"),
        ]
        _serve_lab(pure1_client, tags=tags)

        module = run_main(pure1_arrays, tag=["url:https://example.com:8443/a"])

        # The key of array2 holds a colon: no tag of the task can name it
        assert list(_result(module)) == ["array1"]

    def test_tag_without_a_value_is_no_empty_value(self, pure1_client):
        tags = [_tag("array1", "Team", None), _tag("array2", "Team", "")]
        _serve_lab(pure1_client, tags=tags)

        with_key = _result(run_main(pure1_arrays, tag=["Team"]))
        _serve_lab(pure1_client, tags=tags)
        with_empty_value = _result(run_main(pure1_arrays, tag=["Team:"]))

        assert list(with_key) == ["array1", "array2"]
        assert with_key["array1"]["tags"] == [
            {"key": "Team", "value": None, "namespace": "pure1"}
        ]
        assert list(with_empty_value) == ["array2"]

    def test_namespace_of_a_tag_does_not_count(self, pure1_client):
        tags = [
            _tag("array1", "Team", "a", namespace="pure1"),
            _tag("array2", "Team", "a", namespace="other"),
            _tag("array3", "Team", "a", namespace=None),
        ]
        _serve_lab(pure1_client, tags=tags)

        module = run_main(pure1_arrays, tag=["Team:a"])

        assert list(_result(module)) == ["array1", "array2", "array3"]

    def test_two_tags_of_one_key_on_an_array(self, pure1_client):
        tags = [
            _tag("array1", "Team", "a", namespace="pure1"),
            _tag("array1", "Team", "b", namespace="other"),
        ]
        _serve_lab(pure1_client, tags=tags)

        module = run_main(pure1_arrays, tag=["Team:b", "Team:a"])

        assert _result(module) == {
            "array1": dict(
                ARRAY1,
                tags=[
                    {"key": "Team", "value": "b", "namespace": "other"},
                    {"key": "Team", "value": "a", "namespace": "pure1"},
                ],
            )
        }

    def test_tags_are_joined_to_an_array_by_its_id(self, pure1_client):
        tags = [_tag("array1", "Team", "a", resource=_ref("array2", "arrays"))]
        tags.append(
            _tag("array2", "Site", "b", resource=_ref("array9", id="id-array1"))
        )
        _serve_lab(pure1_client, tags=tags)

        module = run_main(pure1_arrays, tag=["Site:b"])

        assert _result(module) == {
            "array1": dict(
                ARRAY1, tags=[{"key": "Site", "value": "b", "namespace": "pure1"}]
            )
        }

    @pytest.mark.parametrize(
        "tags",
        [[""], [":High"], [":"], ["Team", ""]],
        ids=["empty", "value alone", "colon alone", "empty after a tag"],
    )
    def test_tag_without_a_key_fails_the_task(self, pure1_client, tags):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, tag=tags)

        module.fail_json.assert_called_once_with(
            msg="A tag of the tag option must have a key: give it as key or as "
            "key:value."
        )
        module.exit_json.assert_not_called()
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_tags_given_as_one_string_are_split_at_the_commas(self, pure1_client):
        """As ansible-core reads a list: a value with a comma needs the list form."""
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, tag="Criticality:High,Team")

        assert _result(module) == _lab(1)


class TestOptionsTogether:
    """An array is returned when it matches every option."""

    def test_every_option(self, pure1_client):
        in_range = [_array("array1"), _array("array2"), _array("array5")]
        rows = _lab_group_members() + [_group_member("site1", "array1")]
        _serve_lab(pure1_client, in_range=in_range, group_members=rows)

        module = run_main(
            pure1_arrays,
            name=["ARRAY1", "array2", "array3", "array5"],
            type="flasharray",
            model="x90",
            version_at_least="6.4.0",
            version_below="6.6.0",
            fleet="fleet1",
            topology_group="region1",
            tag=["Criticality:High", "Team"],
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_arrays={"arrays": {"array1": dict(ARRAY1, topology_group="site1")}},
        )
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter=None),
            call(filter=FA_FILTER + " and version>='6.4.0' and version<'6.6.0'"),
        ]

    @pytest.mark.parametrize(
        "params, numbers",
        [
            pytest.param({"type": "flasharray", "fleet": "fleet1"}, (1, 2), id="type"),
            pytest.param({"model": "flash", "fleet": "fleet1"}, (3,), id="model"),
            pytest.param(
                {"name": ["array3", "array4"], "tag": ["Criticality:High"]},
                (3,),
                id="name and tag",
            ),
            pytest.param(
                {"type": "flashblade", "tag": ["Team"]}, (4,), id="type and tag"
            ),
            pytest.param(
                {"fleet": "fleet1", "tag": ["Criticality"]}, (1, 3), id="fleet and tag"
            ),
            pytest.param(
                {"topology_group": "region1", "type": "flashblade"},
                (3,),
                id="group and type",
            ),
            pytest.param(
                {"name": ["array1"], "type": "flashblade"}, (), id="name and type"
            ),
            pytest.param(
                {"fleet": "fleet2", "topology_group": "region1", "name": ["array5"]},
                None,
                id="fleet that has no such group",
            ),
        ],
    )
    def test_two_options(self, pure1_client, params, numbers):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, **params)

        if numbers is None:
            assert _failure(module) == (
                "Topology group region1 is not known to Pure1 in fleet fleet2."
            )
        else:
            assert _result(module) == _lab(*numbers)


class TestArrays:
    """What the typical answer does not show of an array."""

    def test_array_with_only_its_name(self, pure1_client):
        _serve(pure1_client, [sdk_model("Array", name="array1")])

        module = run_main(pure1_arrays)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_arrays={
                "arrays": {
                    "array1": {
                        "id": None,
                        "fqdn": None,
                        "os": None,
                        "type": None,
                        "model": None,
                        "version": None,
                        "fleet": None,
                        "fleet_source": None,
                        "topology_group": None,
                        "tags": [],
                        "as_of": None,
                        "as_of_epoch": None,
                        "age_seconds": None,
                    }
                }
            },
        )
        module.warn.assert_not_called()

    def test_array_with_only_its_name_is_selected_by_its_name_alone(self, pure1_client):
        selected = {}
        for option, value in (
            ("name", ["ARRAY1"]),
            ("type", "flasharray"),
            ("model", "x"),
            ("tag", ["Team"]),
        ):
            _serve(pure1_client, [sdk_model("Array", name="array1")])
            selected[option] = list(_result(run_main(pure1_arrays, **{option: value})))

        assert selected == {"name": ["array1"], "type": [], "model": [], "tag": []}

    def test_values_that_are_empty_or_zero_are_returned_as_they_are(self, pure1_client):
        array = _array("array1", fqdn="", model="", version="", as_of=0)
        _serve(pure1_client, [array], tags=[_tag("array1", "Team", "")])

        module = run_main(pure1_arrays)

        assert _result(module) == {
            "array1": _record(
                "array1",
                fqdn="",
                model="",
                version="",
                tags=[{"key": "Team", "value": "", "namespace": "pure1"}],
                as_of="1970-01-01 00:00:00 UTC",
                as_of_epoch=0,
                age_seconds=1612357500,
            )
        }

    def test_data_newer_than_the_clock_of_the_host_has_the_age_0(self, pure1_client):
        _serve(pure1_client, [_array("array1", as_of=ARRAY_AS_OF + 3600000)])

        module = run_main(pure1_arrays)

        assert _result(module)["array1"]["age_seconds"] == 0
        assert _result(module)["array1"]["as_of"] == "2021-02-03 14:04:05 UTC"

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_time_is_in_utc_on_a_host_west_of_it(self, pure1_client):
        _serve(pure1_client, [_array("array1")])

        module = run_main(pure1_arrays)

        assert _result(module)["array1"]["as_of"] == "2021-02-03 13:04:05 UTC"

    def test_every_array_has_its_age_at_one_time(self, pure1_client, clock):
        """The clock is read once, however many arrays there are."""
        ticks = iter(range(100))
        clock.time = lambda: NOW + 10 * next(ticks)
        _serve_lab(pure1_client)

        result = _result(run_main(pure1_arrays))

        assert [array["age_seconds"] for array in result.values()] == [
            55,
            55,
            55,
            62,
            55,
            55,
        ]

    def test_age_is_measured_when_the_arrays_are_here(self, pure1_client, clock):
        """Pure1 stamps a fresh array while it answers the request for the arrays."""

        def arrays(**kwargs):
            if kwargs.get("limit") == 0:
                return valid_response()
            # The answer is here 2.5 seconds after Pure1 stamped the array
            clock.now = (ARRAY_AS_OF + 2500) / 1000
            return valid_response([_array("array1")])

        def fleets(**kwargs):
            # Every later listing takes its time as well
            clock.now += 10
            return valid_response()

        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = arrays
        pure1_client.get_fleets.side_effect = fleets
        # The request for the arrays begins 0.9 seconds before the stamp
        clock.now = (ARRAY_AS_OF - 900) / 1000

        module = run_main(pure1_arrays)

        # Not 0, the age before the request, and not 12, the age after the fleets
        assert _result(module)["array1"]["age_seconds"] == 2

    def test_age_is_in_whole_seconds_rounded_down(self, pure1_client, clock):
        clock.now = (ARRAY_AS_OF + 1999) / 1000
        _serve(pure1_client, [_array("array1")])

        module = run_main(pure1_arrays)

        assert _result(module)["array1"]["age_seconds"] == 1

    def test_fleet_and_group_of_the_array_record_alone(self, pure1_client):
        """No listing has the fleet or the group that the record names."""
        _serve(pure1_client, [_array("array1", fleet="fleet7", group="site7")])

        module = run_main(pure1_arrays)

        assert _result(module) == {
            "array1": _record(
                "array1", fleet="fleet7", fleet_source="arrays", topology_group="site7"
            )
        }
        module.warn.assert_not_called()

    def test_member_whose_name_no_array_has_is_no_array(self, pure1_client):
        members = _lab_members() + [_member("fleet1", "array9")]
        _serve_lab(pure1_client, members=members)

        module = run_main(pure1_arrays, fleet="fleet1")

        assert _result(module) == _lab(1, 2, 3)
        module.warn.assert_not_called()

    def test_tags_of_an_array_that_pure1_does_not_list_are_dropped(self, pure1_client):
        _serve_lab(pure1_client, tags=_lab_tags() + [_tag("array9", "Team", "x")])

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        module.warn.assert_not_called()


class TestWarnings:
    """Each warning of the module, and that those of the helpers reach the task."""

    def test_arrays_that_cannot_be_filed_are_counted_once(self, pure1_client):
        arrays = [
            _array("array1", fleet="fleet1"),
            sdk_model("Array", id="id-nameless", fleet=_ref("fleet1", "fleets")),
            # The first of a name is the array: the second is left out
            _array("array1", "fb", id="id-other", fqdn="other.example.com"),
        ]
        tags = [_tag("array1", "Team", "a"), _tag("other", "Team", "b")]
        _serve(
            pure1_client,
            arrays,
            [_fleet("fleet1")],
            [_member("fleet1", "array1")],
            tags=tags,
        )

        module = run_main(pure1_arrays)

        assert _result(module) == {
            "array1": _record(
                "array1",
                fleet="fleet1",
                fleet_source="both",
                tags=[{"key": "Team", "value": "a", "namespace": "pure1"}],
            )
        }
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 2."
        ]

    @pytest.mark.parametrize(
        "odd",
        [
            pytest.param(sdk_model("Array", id="id-nameless"), id="no name"),
            pytest.param(
                sdk_model("Array", id="id-nameless", fqdn="array9.example.com"),
                id="only an id and a domain name",
            ),
            pytest.param(
                _array("array1", "fb", id="id-other"), id="name of another array"
            ),
        ],
    )
    def test_one_array_that_cannot_be_filed_is_counted(self, pure1_client, odd):
        _serve(pure1_client, [_array("array1"), odd])

        module = run_main(pure1_arrays)

        assert _result(module) == {"array1": _record("array1")}
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 1."
        ]

    def test_fewer_arrays_than_pure1_counted(self, pure1_client):
        arrays = [_array("array1"), _array("array2"), _array("array3")]
        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response(arrays, total_item_count=5),
            valid_response([], total_item_count=5),
        ]

        module = run_main(pure1_arrays)

        assert list(_result(module)) == ["array1", "array2", "array3"]
        assert _warnings(module) == [
            "Pure1 returned 3 of 5 arrays: the result may be incomplete."
        ]

    def test_arrays_of_a_listing_without_a_total_that_cannot_be_paged(
        self, pure1_client
    ):
        arrays = [_array("array1"), _array("array2")]
        answers = paged_answers("ArrayGetResponse", arrays, total_item_count=None)

        def ignore_offset(**kwargs):
            kwargs.pop("offset", None)
            return answers(**kwargs)

        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = ignore_offset

        module = run_main(pure1_arrays)

        assert list(_result(module)) == ["array1", "array2"]
        assert _warnings(module) == [
            "Pure1 returned 2 of an unknown number of arrays: the result may be "
            "incomplete."
        ]

    def test_fewer_tags_than_pure1_counted(self, pure1_client):
        _serve_lab(pure1_client)
        # Pure1 sends every tag whatever the offset: the same page again
        pure1_client.get_arrays_tags.side_effect = [
            valid_response(_lab_tags(), total_item_count=9),
            valid_response(_lab_tags(), total_item_count=9),
        ]

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Pure1 returned 5 of 9 array tags: the result may be incomplete."
        ]

    def test_tags_that_cannot_be_filed_are_left_out_by_the_helper(self, pure1_client):
        tags = _lab_tags() + [
            sdk_model("Tag", key="Team", value="x"),
            sdk_model("Tag", value="x", resource=_ref("array2", "arrays")),
        ]
        _serve_lab(pure1_client, tags=tags)

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Array tags that Pure1 returned without a key or without the id of "
            "their array are left out of the result: 2."
        ]

    def test_fleet_without_a_name_is_left_out_by_the_helper(self, pure1_client):
        fleets = _lab_fleets() + [sdk_model("Fleet", id="id-nameless")]
        _serve_lab(pure1_client, fleets=fleets)

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Fleets that Pure1 returned without a name or with the name of "
            "another fleet are left out of the result: 1."
        ]

    def test_member_rows_that_cannot_be_filed_are_left_out_by_the_helper(
        self, pure1_client
    ):
        members = _lab_members() + [
            _member("fleet1", "array4", member=_ref()),
            sdk_model("FleetMember", status="joined"),
        ]
        _serve_lab(pure1_client, members=members)

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Fleet member rows that Pure1 returned without a fleet or a member "
            "name, for a member that is no array or a second time for one "
            "member are left out of the result: 2."
        ]

    def test_fewer_member_rows_than_pure1_counted(self, pure1_client):
        _serve_lab(pure1_client)
        pure1_client.get_fleets_members.side_effect = [
            valid_response(_lab_members(), total_item_count=6),
            valid_response([], total_item_count=6),
        ]

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Pure1 returned 3 of 6 fleet member rows: the result may be incomplete."
        ]

    def test_warnings_come_in_the_order_of_the_requests(self, pure1_client):
        arrays = _lab_arrays() + [sdk_model("Array", id="id-nameless")]
        tags = _lab_tags() + [sdk_model("Tag", key="Team", value="x")]
        fleets = _lab_fleets() + [sdk_model("Fleet", id="id-nameless")]
        _serve_lab(pure1_client, arrays=arrays, fleets=fleets, tags=tags)

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 1.",
            "Fleets that Pure1 returned without a name or with the name of "
            "another fleet are left out of the result: 1.",
            "Array tags that Pure1 returned without a key or without the id of "
            "their array are left out of the result: 1.",
        ]


class TestListingsInPages:
    """Every listing is read to its end, however many pages Pure1 makes of it."""

    def test_arrays_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("ArrayGetResponse", _lab_arrays(), page_size=2)
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert answers.requests == [
            {"limit": 0},
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "continuation_token": "page-4"},
        ]

    def test_arrays_without_a_total_are_asked_for_once_more(self, pure1_client):
        """The paging keywords of the shared helper reach the client."""
        _serve_lab(pure1_client)
        answers = paged_answers(
            "ArrayGetResponse", _lab_arrays(), total_item_count=None
        )
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        # Only an empty page after the last array tells that there are no more
        assert answers.requests == [
            {"limit": 0},
            {"filter": None},
            {"filter": None, "offset": 6},
        ]
        module.warn.assert_not_called()

    def test_arrays_of_the_version_range_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        every = paged_answers("ArrayGetResponse", _lab_arrays())
        in_range = paged_answers(
            "ArrayGetResponse",
            [_array("array5"), _array("array2"), _array("array1")],
            page_size=2,
        )

        def answer(**kwargs):
            return (in_range if kwargs.get("filter") else every)(**kwargs)

        pure1_client.get_arrays.side_effect = answer

        module = run_main(pure1_arrays, type="flasharray", version_below="7.0.0")

        assert _result(module) == _lab(1, 2, 5)
        version_filter = FA_FILTER + " and version<'7.0.0'"
        assert every.requests == [{"limit": 0}, {"filter": None}]
        assert in_range.requests == [
            {"filter": version_filter},
            {"filter": version_filter, "continuation_token": "page-2"},
        ]

    def test_arrays_of_the_version_range_without_a_total(self, pure1_client):
        """The paging keywords reach the request for the range as well."""
        _serve_lab(pure1_client)
        every = paged_answers("ArrayGetResponse", _lab_arrays())
        in_range = paged_answers(
            "ArrayGetResponse", [_array("array5")], total_item_count=None
        )

        def answer(**kwargs):
            return (in_range if kwargs.get("filter") else every)(**kwargs)

        pure1_client.get_arrays.side_effect = answer

        module = run_main(pure1_arrays, type="flasharray", version_below="7.0.0")

        assert _result(module) == _lab(5)
        version_filter = FA_FILTER + " and version<'7.0.0'"
        assert in_range.requests == [
            {"filter": version_filter},
            {"filter": version_filter, "offset": 1},
        ]

    def test_member_rows_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("FleetMemberGetResponse", _lab_members(), page_size=2)
        pure1_client.get_fleets_members.side_effect = answers

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert answers.requests == [{}, {"continuation_token": "page-2"}]

    def test_tags_come_in_one_answer(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("TagGetResponse", _lab_tags())
        pure1_client.get_arrays_tags.side_effect = answers

        module = run_main(pure1_arrays)

        assert _result(module) == LAB_ARRAYS
        assert answers.requests == [{}]


class TestFailedRequests:
    """A listing that fails, fails the task with the line of the shared helpers."""

    @pytest.mark.parametrize("method", LISTINGS)
    @pytest.mark.parametrize(
        "answer, reason",
        [
            pytest.param(
                error_response(403, "Access denied"),
                " (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                error_response(400, "Invalid value", "no match"),
                " (HTTP 400): Invalid value: no match",
                id="error answer with context",
            ),
            pytest.param(
                ErrorResponse(503, [], {}),
                " (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                PureError("token refused"), ": PureError: token refused", id="PureError"
            ),
            pytest.param(
                TimeoutError("The read operation timed out"),
                ": TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_listing_fails_the_task_with_one_line(
        self, pure1_client, method, answer, reason
    ):
        _serve_lab(pure1_client)
        answers = [answer]
        if method == "get_arrays":
            # The check of get_pure1 comes first and works
            answers.insert(0, valid_response())
        getattr(pure1_client, method).side_effect = answers

        module = run_main(pure1_arrays)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(method, reason)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        # No listing after the one that failed is asked for
        for later in LISTINGS[LISTINGS.index(method) + 1 :]:
            getattr(pure1_client, later).assert_not_called()

    def test_error_while_a_later_page_is_read(self, pure1_client):
        """The SDK raises when it cannot fetch the next page."""

        def next_page(**kwargs):
            raise PureError("connection reset")

        _serve_lab(pure1_client)
        page = ItemIterator(
            next_page,
            {},
            "next-page",
            5,
            _lab_tags()[:2],
            None,
            more_items_remaining=True,
        )
        pure1_client.get_arrays_tags.return_value = ValidResponse(
            200, "next-page", 5, page, {}
        )

        module = run_main(pure1_arrays)

        assert _failure(module) == (
            "Pure1 request get_arrays_tags failed: PureError: Failed to "
            "collect more items: PureError: connection reset"
        )


class TestDocumentation:
    """What DOCUMENTATION says of the code is what the code does."""

    @staticmethod
    def _documentation():
        return yaml.safe_load(pure1_arrays.DOCUMENTATION)

    def test_every_required_method_is_in_the_note_that_names_no_release(self):
        """The collection requires the py-pure-client that has them all: a
        note with a release would read as a requirement of its own."""
        notes = self._documentation()["notes"]

        assert notes[0] == (
            "This module makes the Pure1 REST 1.6 call C(get_fleets) and the "
            "Pure1 REST 1.7 calls C(get_fleets_members), C(get_topology_groups) "
            "and C(get_topology_groups_members)."
        )
        for method, _minimum in pure1_arrays.REQUIRED_METHODS:
            named = [note for note in notes if "C({0})".format(method) in note]
            assert named == [notes[0]], method
        assert "py-pure-client" not in " ".join(notes)

    def test_options_are_those_of_the_scope_and_no_raw_filter(self):
        options = self._documentation()["options"]

        assert sorted(options) == [
            "fleet",
            "model",
            "name",
            "tag",
            "topology_group",
            "type",
            "version_at_least",
            "version_below",
        ]
        assert options["type"]["choices"] == [
            "flasharray",
            "flashblade",
            "objectengine",
        ]

    def test_listing_longer_than_a_page_costs_another_request_as_a_note_says(
        self, pure1_client
    ):
        arrays = [_array("array{0}".format(number)) for number in range(1001)]
        _serve(pure1_client)
        # Pure1 gives 1,000 objects for one request
        answers = paged_answers("ArrayGetResponse", arrays)
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_arrays, name=["array1000"])

        assert _result(module) == {"array1000": _record("array1000")}
        assert answers.requests == [
            {"limit": 0},
            {"filter": None},
            {"filter": None, "continuation_token": "page-1000"},
        ]
        notes = " ".join(self._documentation()["notes"])
        assert "each with one request for every 1,000 objects" in notes

    def test_topology_group_says_when_it_fails_beside_a_fleet(self):
        text = " ".join(_documented_option("topology_group"))

        # A group of the fleet without arrays is no failure
        assert (
            "With I(fleet) it fails when that fleet has no group of the name "
            "and none of its arrays is in or below a group of the name."
        ) in text
        assert "the arrays of that fleet have no group of the name" not in text

    def test_tag_says_what_a_key_with_a_colon_and_no_value_selects(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, tag=["Team:"])

        # array1 has a team, array4 a tag Team with an empty value
        assert list(_result(module)) == ["array4"]
        assert (
            "C(key:) selects the arrays whose tag of this key has an empty value."
        ) in " ".join(_documented_option("tag"))

    @pytest.mark.parametrize("option", ["name", "tag"])
    def test_option_says_what_an_empty_list_does(self, option):
        text = " ".join(self._documentation()["options"][option]["description"])

        assert "An empty list counts as not set" in text

    @pytest.mark.parametrize("option", ["fleet", "topology_group", "model"])
    def test_text_option_says_that_it_takes_one(self, option):
        text = " ".join(self._documentation()["options"][option]["description"])

        assert "It takes one " in text

    @pytest.mark.parametrize("option", ["version_below", "version_at_least"])
    def test_version_option_says_what_it_needs_and_costs(self, option):
        options = self._documentation()["options"]
        text = " ".join(options[option]["description"])

        assert "Requires I(type)" in text
        assert "Quote the value" in text
        assert "one more request" in " ".join(options["version_below"]["description"])

    def test_organization_is_spelled_as_in_the_other_modules(self):
        documentation = (
            pure1_arrays.DOCUMENTATION + pure1_arrays.EXAMPLES + pure1_arrays.RETURN
        )

        assert "organisation" not in documentation.lower()
        assert "organization" in self._documentation()["description"][0]


def _documented():
    return yaml.safe_load(pure1_arrays.RETURN)["pure1_arrays"]


def _assert_keys(result, documented):
    """Compare a result with what RETURN describes, key by key, both ways."""
    assert set(result) == set(documented)
    array_keys = documented["arrays"]["contains"]
    for array in result["arrays"].values():
        assert list(array) == list(array_keys)
        for tag in array["tags"]:
            assert list(tag) == list(array_keys["tags"]["contains"])


class TestReturn:
    """RETURN describes what the module returns, in both directions."""

    @pytest.mark.parametrize(
        "params",
        [{}, {"fleet": "fleet1"}, {"tag": ["Team"]}],
        ids=["default", "fleet", "tag"],
    )
    def test_return_has_the_keys_of_a_result(self, pure1_client, params):
        _serve_lab(pure1_client)

        module = run_main(pure1_arrays, **params)

        result = module.exit_json.call_args[1]["pure1_arrays"]
        _assert_keys(result, _documented()["contains"])
        # The comparison saw an array with tags
        assert any(array["tags"] for array in result["arrays"].values())

    def test_sample_has_the_keys_of_a_result(self):
        sample = _documented()["sample"]

        _assert_keys(sample, _documented()["contains"])
        assert sample["arrays"]["array1"]["tags"]

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_sample_is_what_the_module_returns_for_such_an_organisation(
        self, pure1_client, clock
    ):
        sample = _documented()["sample"]
        array1 = sample["arrays"]["array1"]
        array2 = sample["arrays"]["array2"]
        clock.now = array1["as_of_epoch"] / 1000 + array1["age_seconds"]
        _serve(
            pure1_client,
            arrays=[
                _array(
                    "array2",
                    "fb",
                    id=array2["id"],
                    fqdn=None,
                    version=array2["version"],
                    as_of=array2["as_of_epoch"],
                ),
                _array(
                    "array1",
                    fleet="fleet1",
                    id=array1["id"],
                    version=array1["version"],
                    as_of=array1["as_of_epoch"],
                ),
            ],
            fleets=[_fleet("fleet1")],
            members=[_member("fleet1", "array1")],
            groups=[_group("site1")],
            group_members=[_group_member("site1", "array1")],
            tags=[
                _tag("array1", "Team", "storage-team", resource=_ref(id=array1["id"])),
                _tag(
                    "array1",
                    "Criticality",
                    "High",
                    resource=_ref(id=array1["id"]),
                ),
            ],
        )

        module = run_main(pure1_arrays)

        module.exit_json.assert_called_once_with(changed=False, pure1_arrays=sample)
        module.warn.assert_not_called()


MODULE = "everpure.pure1.pure1_arrays"
NOT_CURRENT = (
    "Pure1 does not know both arrays, or its data of one has no time or is "
    "older than an hour"
)


def _template(text, variables):
    """Return what ansible-core makes of a template of a task."""
    templar = Templar(loader=None, variables=variables)
    return templar.template(trust_as_template(text))


def _play_examples(client, **served):
    """Run the tasks of EXAMPLES in their order and return what they make

    A task of the module runs through main() with the options of the example
    and registers its result. Pure1 has the typical organisation for it, with
    the listings replaced that served gives under the name the task
    registers. Every other task is returned under the name that the task of
    the module before it registered, as a list with one entry for each item
    of its loop, one without a loop: its when, and unless that is false the
    templates of its arguments, as ansible-core evaluates them. The
    conditions of an assert end with the first that is false: ansible-core
    does not evaluate those after it.
    """
    registered = {}
    made = {}
    reads = None
    for task in yaml.safe_load(pure1_arrays.EXAMPLES):
        options = task.get(MODULE)
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            reads = task["register"]
            listings = dict(served.get(reads, {}))
            # What Pure1 finds in the version range of the task
            listings.setdefault("in_range", [])
            _serve_lab(client, **listings)
            module = run_main(pure1_arrays, **options)
            registered[reads] = dict(module.exit_json.call_args[1])
            made[reads] = []
            continue
        (action,) = set(task) - {"name", "when", "loop", "vars"}
        variables = dict(registered)
        items = _template(task["loop"], variables) if "loop" in task else [None]
        entries = []
        for item in items:
            scope = dict(variables, item=item)
            entry = {}
            if "when" in task:
                entry["when"] = _template("{{ " + task["when"] + " }}", scope)
            if entry.get("when", True):
                for key, value in task[action].items():
                    if key == "that":
                        entry[key] = []
                        for each in value:
                            holds = _template("{{ " + each + " }}", scope)
                            entry[key].append(holds)
                            if not holds:
                                break
                    elif isinstance(value, str):
                        entry[key] = _template(value, scope)
            entries.append(entry)
        made[reads].append(entries)
    return made


class TestExamples:
    """The tasks of EXAMPLES, on what the module returns."""

    def test_examples_read_what_the_module_returns(self, pure1_client):
        # Below 6.5.10 among the FlashArrays, and the FlashBlades on 4.5
        in_range = dict(
            outdated=dict(in_range=[_array("array1"), _array("array2")]),
            tagged=dict(in_range=[_array("array3")]),
        )

        made = _play_examples(pure1_client, **in_range)

        assert list(made) == ["everything", "named", "outdated", "tagged"]
        assert made["everything"] == [
            [
                {"msg": "array1 is a flasharray FA-X90R4 on 6.5.8"},
                {"msg": "array2 is a flasharray FA-C60R3 on 6.4.10"},
                {"msg": "array3 is a flashblade FlashBlade//S200 on 4.5.6"},
                {"msg": "array4 is a flashblade FlashBlade on 4.1.12"},
                {"msg": "array5 is a flasharray FA-X50R4 on 6.6.3"},
                {"msg": "array6 is a flasharray CBS-V10AR1 on 6.6.3"},
            ]
        ]
        assert made["named"] == [
            [{"that": [True, True, True], "fail_msg": NOT_CURRENT}]
        ]
        # The X90 of fleet1 is in no group of site1: nothing to upgrade there
        assert made["outdated"] == [
            [{"that": [True], "fail_msg": "Upgrade these arrays first: "}]
        ]
        # array3 is tagged as highly critical and has no team
        assert made["tagged"] == [[]]

    def test_examples_send_the_version_ranges_they_name(self, pure1_client):
        rows = _lab_group_members() + [_group_member("site1", "array1")]
        tags = _lab_tags() + [_tag("array3", "Team", "blade-team")]
        sent = []

        def recorded(**kwargs):
            if kwargs.get("filter"):
                sent.append(kwargs["filter"])
                return valid_response([_array("array1"), _array("array3")])
            if kwargs.get("limit") == 0:
                return valid_response()
            return valid_response(_lab_arrays())

        for task in yaml.safe_load(pure1_arrays.EXAMPLES):
            if MODULE not in task:
                continue
            options = {
                name: value
                for name, value in task[MODULE].items()
                if name not in ("app_id", "key_file")
            }
            _serve_lab(pure1_client, group_members=rows, tags=tags)
            pure1_client.get_arrays.side_effect = recorded
            _result(run_main(pure1_arrays, **options))

        assert sent == [
            FA_FILTER + " and version<'6.5.10'",
            "os='Purity//FB' and version>='4.5.0' and version<'4.6.0'",
        ]

    def test_check_of_two_arrays_fails_when_pure1_knows_one_only(self, pure1_client):
        arrays = [array for array in _lab_arrays() if array.name != "array2"]

        made = _play_examples(pure1_client, named=dict(arrays=arrays))

        # The first condition is false: the assert ends there
        assert made["named"] == [[{"that": [False], "fail_msg": NOT_CURRENT}]]

    def test_check_of_two_arrays_fails_when_neither_is_known(self, pure1_client):
        arrays = [_array("array7"), _array("array8")]

        made = _play_examples(pure1_client, named=dict(arrays=arrays))

        assert made["named"] == [[{"that": [False], "fail_msg": NOT_CURRENT}]]

    @pytest.mark.parametrize(
        "age, current", [(0, True), (3599, True), (3600, False), (86400, False)]
    )
    def test_check_of_two_arrays_fails_for_data_of_an_hour_ago(
        self, pure1_client, age, current
    ):
        arrays = _lab_arrays()
        arrays[1] = _array("array1", fleet="fleet1", as_of=int(NOW * 1000) - age * 1000)

        made = _play_examples(pure1_client, named=dict(arrays=arrays))

        assert made["named"] == [
            [{"that": [True, True, current], "fail_msg": NOT_CURRENT}]
        ]

    def test_check_of_two_arrays_fails_for_data_without_a_time(self, pure1_client):
        """An age that is null cannot be compared: the check says so before."""
        arrays = _lab_arrays()
        arrays[1] = _array("array1", fleet="fleet1", as_of=None)

        made = _play_examples(pure1_client, named=dict(arrays=arrays))

        assert made["named"] == [[{"that": [True, False], "fail_msg": NOT_CURRENT}]]

    def test_arrays_to_upgrade_are_named_and_stop_the_play(self, pure1_client):
        """array1 is in site1 here, and Pure1 finds it below the release."""
        rows = _lab_group_members() + [_group_member("site1", "array1")]
        listings = dict(
            group_members=rows, in_range=[_array("array2"), _array("array1")]
        )

        made = _play_examples(pure1_client, outdated=listings)

        assert made["outdated"] == [
            [{"that": [False], "fail_msg": "Upgrade these arrays first: array1"}]
        ]

    def test_tagged_arrays_are_shown_with_their_id_and_domain_name(self, pure1_client):
        tags = _lab_tags() + [_tag("array3", "Team", "blade-team")]
        listings = dict(tags=tags, in_range=[_array("array3")])

        made = _play_examples(pure1_client, tagged=listings)

        assert made["tagged"] == [
            [{"when": True, "msg": "array3 (id-array3) is at array3.example.com"}]
        ]

    def test_tagged_array_without_a_domain_name_is_not_shown(self, pure1_client):
        arrays = _lab_arrays()
        arrays[0] = _array("array3", "fb", fqdn=None)
        tags = _lab_tags() + [_tag("array3", "Team", "blade-team")]
        listings = dict(arrays=arrays, tags=tags, in_range=[_array("array3")])

        made = _play_examples(pure1_client, tagged=listings)

        assert made["tagged"] == [[{"when": False}]]
