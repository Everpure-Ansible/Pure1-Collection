# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the fleet, topology group and tag helpers of module_utils.fleets.

The first fixture has the shape of a reading of a real organisation, under
invented names: a fleet with six member rows of which one array has no fleet
on its own record, a fleet that only one array record names, a root group
with three empty child groups and a root group that holds two arrays. What
that reading cannot show is built by hand in the tests after it.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy

import pytest
from pypureclient import ErrorResponse, PureError, ValidResponse
from pypureclient.responses import ItemIterator

from plugins.module_utils import fleets as fleets_utils
from plugins.module_utils.fleets import (
    ARRAY_TYPES,
    pure1_array_record,
    pure1_fleet_map,
    pure1_tag_map,
)
from tests.unit.conftest import (
    ansible_module,
    error_response,
    paged_answers,
    sdk_model,
    valid_response,
)

ARRAY_AS_OF = 1612357445000
ARRAY_TIME = "2021-02-03 13:04:05 UTC"
# The member rows of a fleet and of the topology groups share one stamp
ROWS_AS_OF = 1612353784000
ROWS_TIME = "2021-02-03 12:03:04 UTC"
FLEET1_AS_OF = 1612447506000
FLEET1_TIME = "2021-02-04 14:05:06 UTC"
FLEET2_AS_OF = 1612537567000
FLEET2_TIME = "2021-02-05 15:06:07 UTC"
PRODUCTS = {
    "fa": ("Purity//FA", "FA-X70R4", "6.5.0"),
    "fb": ("Purity//FB", "FlashBlade//S200", "4.5.0"),
}


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


def _fleet(name, as_of=FLEET1_AS_OF, **overrides):
    fields = dict(id="id-" + name, name=name, as_of=as_of)
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


def _group(group_name, fleet="fleet1", parent=None, **overrides):
    fields = dict(
        id="id-" + group_name,
        name=group_name,
        fleet=_ref(fleet, "fleets"),
        parent_topology_group=_ref(parent, "topology-groups"),
        as_of=ROWS_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("TopologyGroup", **fields)


def _group_member(group, name, fleet="fleet1", kind="arrays", **overrides):
    """Return the member row of an array, or of a child group, in a group."""
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


def _serve(client, arrays=(), fleets=(), members=(), groups=(), group_members=()):
    """Answer each of the five listings with one page."""
    client.get_arrays.return_value = valid_response(arrays)
    client.get_fleets.return_value = valid_response(fleets)
    client.get_fleets_members.return_value = valid_response(members)
    client.get_topology_groups.return_value = valid_response(groups)
    client.get_topology_groups_members.return_value = valid_response(group_members)


def _warnings(module):
    return [call[0][0] for call in module.warn.call_args_list]


# What the tests after the first fixture expect, with the values the builders give


def _row_member(name, source="both", reported="fleet1", **overrides):
    entry = dict(
        status="joined",
        status_details="",
        coordinator=False,
        source=source,
        as_of=ROWS_TIME,
        as_of_epoch=ROWS_AS_OF,
        member_id="id-" + name,
        array_id="id-" + name,
        reported_fleet=reported,
    )
    entry.update(overrides)
    return entry


def _record_member(name, fleet):
    return dict(
        status=None,
        status_details=None,
        coordinator=None,
        source="arrays",
        as_of=None,
        as_of_epoch=None,
        member_id=None,
        array_id="id-" + name,
        reported_fleet=fleet,
    )


def _fleet_entry(name, members=None, as_of=(FLEET1_TIME, FLEET1_AS_OF), **overrides):
    entry = dict(
        id="id-" + name,
        as_of=as_of[0],
        as_of_epoch=as_of[1],
        coordinators=[],
        members=members or {},
    )
    entry.update(overrides)
    return entry


def _placed(
    fleet=None,
    status=None,
    source=None,
    group=None,
    group_source=None,
    group_fleet=None,
    path=None,
):
    return dict(
        fleet=fleet,
        fleet_status=status,
        fleet_source=source,
        topology_group=group,
        topology_group_source=group_source,
        topology_fleet=group_fleet,
        topology_path=path,
    )


def _row_array(name, source="both", **overrides):
    entry = dict(
        status="joined",
        status_details="",
        source=source,
        as_of=ROWS_TIME,
        as_of_epoch=ROWS_AS_OF,
        member_id="id-" + name,
        array_id="id-" + name,
    )
    entry.update(overrides)
    return entry


def _record_array(name):
    return dict(
        status=None,
        status_details=None,
        source="arrays",
        as_of=None,
        as_of_epoch=None,
        member_id=None,
        array_id="id-" + name,
    )


def _group_entry(name, parent=None, path=None, children=(), arrays=None, below=()):
    """Return a listed group; below are the arrays of the groups under it."""
    arrays = arrays or {}
    return dict(
        id="id-" + name,
        as_of=ROWS_TIME,
        as_of_epoch=ROWS_AS_OF,
        parent=parent,
        path=path or [name],
        children=list(children),
        arrays=arrays,
        all_arrays=sorted(set(arrays) | set(below)),
    )


def _reference_group(name, **overrides):
    """Return a group that only a reference names."""
    entry = _group_entry(name)
    entry.update(as_of=None, as_of_epoch=None)
    entry.update(overrides)
    return entry


def _lab_arrays():
    return [
        _array("array1", fleet="fleet1"),
        _array("array2", fleet="fleet1"),
        _array("array3", fleet="fleet1"),
        _array("array4", fleet="fleet1"),
        # A joined member whose own record names no fleet, but a group of the fleet
        _array("array5", "fb", group="site1"),
        _array("array6", "fb", fleet="fleet1", group="site1"),
        # The only trace of fleet2 beside its own entry
        _array("array7", fleet="fleet2"),
        _array("array8", fqdn=None),
        _array("array9", "fb"),
    ]


def _lab_fleets():
    return [_fleet("fleet2", FLEET2_AS_OF), _fleet("fleet1")]


def _lab_members():
    # The member id of a FlashArray is not its Pure1 id, that of a FlashBlade is
    return [
        _member("fleet1", "array4", member=_ref("array4", "arrays", id="member-4")),
        _member("fleet1", "array2", True, member=_ref("array2", "arrays", id="m-2")),
        _member("fleet1", "array1", member=_ref("array1", "arrays", id="member-1")),
        _member("fleet1", "array3", member=_ref("array3", "arrays", id="member-3")),
        _member("fleet1", "array5"),
        _member("fleet1", "array6"),
    ]


def _lab_groups():
    return [
        _group("site1"),
        _group("zone-a", parent="region1"),
        _group("region1"),
        _group("zone-b", parent="region1"),
        _group("zone-c", parent="region1"),
    ]


def _lab_group_members():
    return [
        _group_member("region1", "zone-a", kind="topology-groups"),
        _group_member("region1", "zone-b", kind="topology-groups"),
        _group_member("site1", "array5"),
        _group_member("region1", "zone-c", kind="topology-groups"),
        _group_member("site1", "array6"),
    ]


def _serve_lab(client):
    _serve(
        client,
        _lab_arrays(),
        _lab_fleets(),
        _lab_members(),
        _lab_groups(),
        _lab_group_members(),
    )


def _lab_member(member_id, array_id, source="both", coordinator=False):
    return {
        "status": "joined",
        "status_details": "",
        "coordinator": coordinator,
        "source": source,
        "as_of": "2021-02-03 12:03:04 UTC",
        "as_of_epoch": 1612353784000,
        "member_id": member_id,
        "array_id": array_id,
        "reported_fleet": "fleet1" if source == "both" else None,
    }


def _lab_group(name, parent=None, children=(), arrays=None):
    return {
        "id": "id-" + name,
        "as_of": "2021-02-03 12:03:04 UTC",
        "as_of_epoch": 1612353784000,
        "parent": parent,
        "path": [parent, name] if parent else [name],
        "children": list(children),
        "arrays": arrays or {},
        "all_arrays": sorted(arrays or {}),
    }


def _lab_group_array(name):
    return {
        "status": "joined",
        "status_details": "",
        "source": "both",
        "as_of": "2021-02-03 12:03:04 UTC",
        "as_of_epoch": 1612353784000,
        "member_id": "id-" + name,
        "array_id": "id-" + name,
    }


IN_FLEET1 = {
    "fleet": "fleet1",
    "fleet_status": "joined",
    "fleet_source": "both",
    "topology_group": None,
    "topology_group_source": None,
    "topology_fleet": None,
    "topology_path": None,
}
IN_NO_FLEET = {
    "fleet": None,
    "fleet_status": None,
    "fleet_source": None,
    "topology_group": None,
    "topology_group_source": None,
    "topology_fleet": None,
    "topology_path": None,
}
IN_SITE1 = {
    "topology_group": "site1",
    "topology_group_source": "both",
    "topology_fleet": "fleet1",
    "topology_path": ["site1"],
}
LAB_FLEETS = {
    "fleet1": {
        "id": "id-fleet1",
        "as_of": "2021-02-04 14:05:06 UTC",
        "as_of_epoch": 1612447506000,
        "coordinators": ["array2"],
        "members": {
            "array1": _lab_member("member-1", "id-array1"),
            "array2": _lab_member("m-2", "id-array2", coordinator=True),
            "array3": _lab_member("member-3", "id-array3"),
            "array4": _lab_member("member-4", "id-array4"),
            "array5": _lab_member("id-array5", "id-array5", source="members"),
            "array6": _lab_member("id-array6", "id-array6"),
        },
    },
    "fleet2": {
        "id": "id-fleet2",
        "as_of": "2021-02-05 15:06:07 UTC",
        "as_of_epoch": 1612537567000,
        "coordinators": [],
        "members": {
            "array7": {
                "status": None,
                "status_details": None,
                "coordinator": None,
                "source": "arrays",
                "as_of": None,
                "as_of_epoch": None,
                "member_id": None,
                "array_id": "id-array7",
                "reported_fleet": "fleet2",
            }
        },
    },
}
LAB_MISMATCHES = [
    {"array": "array5", "kind": "fleet", "rows": ["fleet1"], "array_record": None},
    {"array": "array7", "kind": "fleet", "rows": [], "array_record": "fleet2"},
]
LAB_MAP = {
    "fleets": LAB_FLEETS,
    "arrays": {
        "array1": IN_FLEET1,
        "array2": IN_FLEET1,
        "array3": IN_FLEET1,
        "array4": IN_FLEET1,
        "array5": dict(IN_FLEET1, fleet_source="members", **IN_SITE1),
        "array6": dict(IN_FLEET1, **IN_SITE1),
        "array7": dict(IN_NO_FLEET, fleet="fleet2", fleet_source="arrays"),
        "array8": IN_NO_FLEET,
        "array9": IN_NO_FLEET,
    },
    "topology_groups": {
        "fleet1": {
            "region1": _lab_group("region1", children=["zone-a", "zone-b", "zone-c"]),
            "site1": _lab_group(
                "site1",
                arrays={
                    "array5": _lab_group_array("array5"),
                    "array6": _lab_group_array("array6"),
                },
            ),
            "zone-a": _lab_group("zone-a", parent="region1"),
            "zone-b": _lab_group("zone-b", parent="region1"),
            "zone-c": _lab_group("zone-c", parent="region1"),
        }
    },
    "mismatches": LAB_MISMATCHES,
}


class TestLabShape:
    """The shape of the reading: two sources that differ in both directions."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_whole_map(self, pure1_client):
        module = ansible_module()
        _serve_lab(pure1_client)

        result = pure1_fleet_map(module, pure1_client)

        assert result == LAB_MAP
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()

    def test_the_joined_member_without_a_fleet_on_its_record_is_in_the_fleet(
        self, pure1_client
    ):
        _serve_lab(pure1_client)

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert len(result["fleets"]["fleet1"]["members"]) == 6
        assert result["fleets"]["fleet1"]["members"]["array5"]["source"] == "members"
        assert result["arrays"]["array5"]["fleet"] == "fleet1"
        assert result["fleets"]["fleet2"]["members"]["array7"]["source"] == "arrays"
        assert [entry["array"] for entry in result["mismatches"]] == [
            "array5",
            "array7",
        ]

    def test_each_listing_is_requested_once_and_unfiltered(self, pure1_client):
        _serve_lab(pure1_client)

        pure1_fleet_map(ansible_module(), pure1_client)

        pure1_client.get_arrays.assert_called_once_with()
        pure1_client.get_fleets.assert_called_once_with()
        pure1_client.get_fleets_members.assert_called_once_with()
        pure1_client.get_topology_groups.assert_called_once_with()
        pure1_client.get_topology_groups_members.assert_called_once_with()

    def test_arrays_of_the_caller_are_not_listed_again(self, pure1_client):
        module = ansible_module()
        _serve_lab(pure1_client)

        result = pure1_fleet_map(module, pure1_client, arrays=_lab_arrays())

        assert result == LAB_MAP
        pure1_client.get_arrays.assert_not_called()
        pure1_client.get_fleets.assert_called_once_with()
        module.warn.assert_not_called()

    def test_no_arrays_of_the_caller_are_not_listed_either(self, pure1_client):
        _serve_lab(pure1_client)

        result = pure1_fleet_map(ansible_module(), pure1_client, arrays=[])

        pure1_client.get_arrays.assert_not_called()
        assert result["arrays"] == {}
        assert result["mismatches"] == []
        # Every member is then one that no array has the name of
        members = result["fleets"]["fleet1"]["members"]
        assert sorted(members) == ["array" + str(n) for n in range(1, 7)]
        assert [member["array_id"] for member in members.values()] == [None] * 6
        assert [member["source"] for member in members.values()] == ["members"] * 6
        assert result["fleets"]["fleet2"]["members"] == {}
        assert result["topology_groups"]["fleet1"]["site1"]["all_arrays"] == []

    def test_arrays_as_dictionaries_are_a_programming_error(self, pure1_client):
        """What pure1_resolve_arrays returns has no field an SDK model has."""
        module = ansible_module()
        _serve_lab(pure1_client)
        resolved = [dict(id="id-array1", name="array1", fqdn=None, os="Purity//FA")]

        with pytest.raises(TypeError) as raised:
            pure1_fleet_map(module, pure1_client, arrays=resolved)

        assert str(raised.value) == (
            "the arrays of pure1_fleet_map() must be SDK models, as get_arrays() "
            "returns them, not dictionaries"
        )
        pure1_client.get_fleets.assert_not_called()
        module.fail_json.assert_not_called()

    def test_without_topology(self, pure1_client):
        module = ansible_module()
        _serve_lab(pure1_client)

        result = pure1_fleet_map(module, pure1_client, topology=False)

        on_record = dict(
            topology_group="site1",
            topology_group_source="arrays",
            topology_fleet=None,
            topology_path=None,
        )
        assert result == {
            "fleets": LAB_FLEETS,
            "arrays": dict(
                LAB_MAP["arrays"],
                array5=dict(IN_FLEET1, fleet_source="members", **on_record),
                array6=dict(IN_FLEET1, **on_record),
            ),
            "topology_groups": {},
            "mismatches": LAB_MISMATCHES,
        }
        pure1_client.get_fleets_members.assert_called_once_with()
        pure1_client.get_topology_groups.assert_not_called()
        pure1_client.get_topology_groups_members.assert_not_called()
        module.warn.assert_not_called()

    @pytest.mark.parametrize("order", [list, reversed], ids=["as listed", "reversed"])
    def test_every_dictionary_and_list_is_in_order(self, pure1_client, order):
        _serve(
            pure1_client,
            order(_lab_arrays()),
            order(_lab_fleets()),
            order(_lab_members()),
            order(_lab_groups()),
            order(_lab_group_members()),
        )

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert result == LAB_MAP
        assert list(result) == ["fleets", "arrays", "topology_groups", "mismatches"]
        assert list(result["fleets"]) == ["fleet1", "fleet2"]
        assert list(result["fleets"]["fleet1"]["members"]) == [
            "array" + str(n) for n in range(1, 7)
        ]
        assert list(result["arrays"]) == ["array" + str(n) for n in range(1, 10)]
        groups = result["topology_groups"]["fleet1"]
        assert list(groups) == ["region1", "site1", "zone-a", "zone-b", "zone-c"]
        assert list(groups["site1"]["arrays"]) == ["array5", "array6"]
        assert groups["region1"]["children"] == ["zone-a", "zone-b", "zone-c"]
        assert groups["site1"]["all_arrays"] == ["array5", "array6"]
        assert result["mismatches"] == LAB_MISMATCHES

    def test_an_organisation_without_anything(self, pure1_client):
        module = ansible_module()
        _serve(pure1_client)

        result = pure1_fleet_map(module, pure1_client)

        assert result == {
            "fleets": {},
            "arrays": {},
            "topology_groups": {},
            "mismatches": [],
        }
        module.warn.assert_not_called()

    def test_arrays_in_no_fleet_and_no_group(self, pure1_client):
        _serve(pure1_client, [_array("array1"), _array("array2")])

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert result == {
            "fleets": {},
            "arrays": {"array1": _placed(), "array2": _placed()},
            "topology_groups": {},
            "mismatches": [],
        }


class TestFleetMembership:
    """What the reading does not show of the fleets."""

    def test_member_that_is_not_joined(self, pure1_client):
        module = ansible_module()
        row = _member(
            "fleet1", "array1", status="joining", status_details="Waiting for array1"
        )
        _serve(
            pure1_client, [_array("array1", fleet="fleet1")], [_fleet("fleet1")], [row]
        )

        result = pure1_fleet_map(module, pure1_client, topology=False)

        assert result == {
            "fleets": {
                "fleet1": _fleet_entry(
                    "fleet1",
                    {
                        "array1": _row_member(
                            "array1",
                            status="joining",
                            status_details="Waiting for array1",
                        )
                    },
                )
            },
            "arrays": {"array1": _placed("fleet1", "joining", "both")},
            "topology_groups": {},
            "mismatches": [],
        }
        module.warn.assert_not_called()

    def test_member_whose_name_no_array_has(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1")],
            [_fleet("fleet1")],
            [_member("fleet1", "array1"), _member("fleet1", "array2")],
        )

        result = pure1_fleet_map(module, pure1_client, topology=False)

        assert result["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1"),
            "array2": _row_member(
                "array2", source="members", reported=None, array_id=None
            ),
        }
        # It is no array of Pure1, so in no selection and no mismatch
        assert result["arrays"] == {"array1": _placed("fleet1", "joined", "both")}
        assert result["mismatches"] == []
        module.warn.assert_not_called()

    def test_member_is_joined_to_its_array_by_name_not_by_id(self, pure1_client):
        other = _ref("array2", "arrays", id="id-array1")
        _serve(
            pure1_client,
            [_array("array1"), _array("array2", id="id-other")],
            [_fleet("fleet1")],
            [_member("fleet1", "array2", member=other)],
        )

        result = pure1_fleet_map(ansible_module(), pure1_client, topology=False)

        assert result["fleets"]["fleet1"]["members"] == {
            "array2": _row_member(
                "array2",
                source="members",
                reported=None,
                member_id="id-array1",
                array_id="id-other",
            )
        }
        assert result["arrays"] == {
            "array1": _placed(),
            "array2": _placed("fleet1", "joined", "members"),
        }

    def test_row_and_array_record_name_different_fleets(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet2")],
            [_fleet("fleet1"), _fleet("fleet2", FLEET2_AS_OF)],
            [_member("fleet1", "array1")],
        )

        result = pure1_fleet_map(module, pure1_client, topology=False)

        assert result == {
            "fleets": {
                "fleet1": _fleet_entry(
                    "fleet1",
                    {
                        "array1": _row_member(
                            "array1", source="members", reported="fleet2"
                        )
                    },
                ),
                # The row wins: the array is no member of the fleet of its record
                "fleet2": _fleet_entry("fleet2", as_of=(FLEET2_TIME, FLEET2_AS_OF)),
            },
            "arrays": {"array1": _placed("fleet1", "joined", "members")},
            "topology_groups": {},
            "mismatches": [
                {
                    "array": "array1",
                    "kind": "fleet",
                    "rows": ["fleet1"],
                    "array_record": "fleet2",
                }
            ],
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "record, statuses, expected, source",
        [
            pytest.param(
                "fleet2",
                {"fleet2": "joined", "fleet1": "joined"},
                "fleet2",
                "both",
                id="the fleet of the array record",
            ),
            pytest.param(
                "fleet2",
                {"fleet2": "removing", "fleet1": "joined"},
                "fleet2",
                "both",
                id="the fleet of the array record before a joined one",
            ),
            pytest.param(
                None,
                {"fleet1": "removing", "fleet2": "joined"},
                "fleet2",
                "members",
                id="the joined one",
            ),
            pytest.param(
                None,
                {"fleet2": "joining", "fleet1": "joining"},
                "fleet1",
                "members",
                id="the first by name",
            ),
            pytest.param(
                "fleet3",
                {"fleet2": "joined", "fleet1": "removing"},
                "fleet2",
                "members",
                id="the joined one when the record names a third",
            ),
        ],
    )
    def test_rows_in_two_fleets(self, pure1_client, record, statuses, expected, source):
        rows = [
            _member(fleet, "array1", status=status)
            for fleet, status in statuses.items()
        ]
        _serve(
            pure1_client,
            [_array("array1", fleet=record)],
            [_fleet("fleet1"), _fleet("fleet2"), _fleet("fleet3")],
            rows,
        )

        result = pure1_fleet_map(ansible_module(), pure1_client, topology=False)

        assert result["arrays"] == {
            "array1": _placed(expected, statuses[expected], source)
        }
        # Each row is a member, and the fleet of the record gets none without a row
        assert [sorted(fleet["members"]) for fleet in result["fleets"].values()] == [
            ["array1"],
            ["array1"],
            [],
        ]
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "fleet",
                "rows": ["fleet1", "fleet2"],
                "array_record": record,
            }
        ]

    def test_fleets_that_only_a_reference_names(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet8"), _array("array2")],
            [],
            [_member("fleet9", "array2")],
            [_group("zone-a", fleet="fleet7")],
        )

        result = pure1_fleet_map(module, pure1_client)

        unlisted = dict(as_of=(None, None))
        assert result["fleets"] == {
            "fleet7": _fleet_entry("fleet7", **unlisted),
            "fleet8": _fleet_entry(
                "fleet8", {"array1": _record_member("array1", "fleet8")}, **unlisted
            ),
            "fleet9": _fleet_entry(
                "fleet9",
                {"array2": _row_member("array2", source="members", reported=None)},
                **unlisted
            ),
        }
        assert result["arrays"] == {
            "array1": _placed("fleet8", None, "arrays"),
            "array2": _placed("fleet9", "joined", "members"),
        }
        assert list(result["topology_groups"]) == ["fleet7"]
        module.warn.assert_not_called()

    def test_coordinators(self, pure1_client):
        lists_none = _member("fleet1", "array3", True)
        lists_none.coordinator_of.insert(0, None)
        rows = [
            _member("fleet1", "array4", True),
            _member("fleet1", "array1", coordinator_of=[_ref("fleet2", "fleets")]),
            _member("fleet1", "array2", coordinator_of=[_ref(), _ref("fleet1")]),
            lists_none,
            # Pure1 sent no list at all
            _member("fleet1", "array5", coordinator_of=None),
            _member("fleet2", "array6", True),
        ]
        _serve(pure1_client, [], [_fleet("fleet1"), _fleet("fleet2")], rows)

        result = pure1_fleet_map(ansible_module(), pure1_client, topology=False)

        assert result["fleets"]["fleet1"]["coordinators"] == [
            "array2",
            "array3",
            "array4",
        ]
        assert result["fleets"]["fleet2"]["coordinators"] == ["array6"]
        assert {
            name: member["coordinator"]
            for name, member in result["fleets"]["fleet1"]["members"].items()
        } == {
            "array1": False,
            "array2": True,
            "array3": True,
            "array4": True,
            "array5": False,
        }

    def test_objects_with_only_their_names(self, pure1_client):
        module = ansible_module()
        bare = dict(
            arrays=[sdk_model("Array", name="array1"), sdk_model("Array", name="a2")],
            fleets=[sdk_model("Fleet", name="fleet1")],
            members=[
                sdk_model(
                    "FleetMember",
                    fleet=sdk_model("FixedReference", name="fleet1"),
                    member=sdk_model("FixedReference", name="array1"),
                )
            ],
            groups=[
                sdk_model(
                    "TopologyGroup",
                    name="zone-a",
                    fleet=sdk_model("FixedReference", name="fleet1"),
                )
            ],
            group_members=[
                sdk_model(
                    "TopologyGroupMember",
                    fleet=sdk_model("FixedReference", name="fleet1"),
                    topology_group=sdk_model("FixedReference", name="zone-a"),
                    member=sdk_model("FixedReference", name="array1"),
                )
            ],
        )
        _serve(pure1_client, **bare)

        result = pure1_fleet_map(module, pure1_client)

        assert result == {
            "fleets": {
                "fleet1": {
                    "id": None,
                    "as_of": None,
                    "as_of_epoch": None,
                    "coordinators": [],
                    "members": {
                        "array1": {
                            "status": None,
                            "status_details": None,
                            "coordinator": False,
                            "source": "members",
                            "as_of": None,
                            "as_of_epoch": None,
                            "member_id": None,
                            "array_id": None,
                            "reported_fleet": None,
                        }
                    },
                }
            },
            "arrays": {
                "a2": _placed(),
                "array1": _placed(
                    "fleet1", None, "members", "zone-a", "members", "fleet1", ["zone-a"]
                ),
            },
            "topology_groups": {
                "fleet1": {
                    "zone-a": {
                        "id": None,
                        "as_of": None,
                        "as_of_epoch": None,
                        "parent": None,
                        "path": ["zone-a"],
                        "children": [],
                        "arrays": {
                            "array1": {
                                "status": None,
                                "status_details": None,
                                "source": "members",
                                "as_of": None,
                                "as_of_epoch": None,
                                "member_id": None,
                                "array_id": None,
                            }
                        },
                        "all_arrays": ["array1"],
                    }
                }
            },
            "mismatches": [
                {
                    "array": "array1",
                    "kind": "fleet",
                    "rows": ["fleet1"],
                    "array_record": None,
                },
                {
                    "array": "array1",
                    "kind": "topology_group",
                    "rows": ["zone-a"],
                    "array_record": None,
                },
            ],
        }
        module.warn.assert_not_called()

    def test_an_empty_name_is_a_name(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("", fleet="")],
            [_fleet("")],
            [_member("", "")],
            [_group("", fleet="")],
            [_group_member("", "", fleet="")],
        )

        result = pure1_fleet_map(module, pure1_client)

        assert result["arrays"] == {
            "": _placed("", "joined", "both", "", "members", "", [""])
        }
        assert list(result["fleets"][""]["members"]) == [""]
        assert result["topology_groups"][""][""]["all_arrays"] == [""]
        module.warn.assert_not_called()

    def test_arrays_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        arrays = [
            sdk_model("Array", id="id-nameless"),
            _array("array1", fleet="fleet1"),
            _array("array1", id="id-second"),
        ]
        _serve(pure1_client, arrays, [_fleet("fleet1")], [_member("fleet1", "array1")])

        result = pure1_fleet_map(module, pure1_client, topology=False)

        # The first array of a name is the one that is filed
        assert result["arrays"] == {"array1": _placed("fleet1", "joined", "both")}
        assert result["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1")
        }
        assert result["mismatches"] == []
        module.warn.assert_called_once_with(
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 2."
        )

    def test_arrays_of_the_caller_give_no_warning(self, pure1_client):
        module = ansible_module()
        arrays = [
            sdk_model("Array", id="id-nameless"),
            _array("array1", fleet="fleet1"),
            _array("array1", id="id-second"),
        ]
        _serve(pure1_client, [], [_fleet("fleet1")], [_member("fleet1", "array1")])

        result = pure1_fleet_map(module, pure1_client, arrays=arrays, topology=False)

        assert result["arrays"] == {"array1": _placed("fleet1", "joined", "both")}
        assert result["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1")
        }
        module.warn.assert_not_called()

    def test_fleets_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        listed = [
            sdk_model("Fleet", id="id-nameless"),
            _fleet("fleet1"),
            _fleet("fleet1", FLEET2_AS_OF, id="id-second"),
        ]
        _serve(pure1_client, [], listed)

        result = pure1_fleet_map(module, pure1_client, topology=False)

        assert result["fleets"] == {"fleet1": _fleet_entry("fleet1")}
        module.warn.assert_called_once_with(
            "Fleets that Pure1 returned without a name or with the name of "
            "another fleet are left out of the result: 2."
        )

    def test_member_rows_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        rows = [
            _member("fleet1", "array1", fleet=None),
            _member("fleet1", "array1", fleet=_ref()),
            _member("fleet1", "array1", member=_ref()),
            _member("fleet1", "array1", member=_ref("array1", "hosts")),
            _member("fleet1", "array2"),
            _member("fleet1", "array2", status="removing", member=_ref("array2")),
        ]
        _serve(pure1_client, [_array("array1")], [_fleet("fleet1")], rows)

        result = pure1_fleet_map(module, pure1_client, topology=False)

        # The first row of a member in a fleet is the one that is filed
        assert result["fleets"] == {
            "fleet1": _fleet_entry(
                "fleet1",
                {"array2": _row_member("array2", "members", None, array_id=None)},
            )
        }
        assert result["arrays"] == {"array1": _placed()}
        module.warn.assert_called_once_with(
            "Fleet member rows that Pure1 returned without a fleet or a member "
            "name, for a member that is no array or a second time for one member "
            "are left out of the result: 5."
        )

    def test_member_row_without_a_resource_type_is_an_array(self, pure1_client):
        module = ansible_module()
        row = _member("fleet1", "array1", member=_ref("array1"))
        _serve(pure1_client, [_array("array1")], [_fleet("fleet1")], [row])

        result = pure1_fleet_map(module, pure1_client, topology=False)

        assert result["arrays"] == {"array1": _placed("fleet1", "joined", "members")}
        module.warn.assert_not_called()


class TestTopology:
    """What the reading does not show of the topology groups."""

    def test_array_below_a_nested_group(self, pure1_client):
        module = ansible_module()
        arrays = [_array("array" + str(n)) for n in (1, 2, 3, 5)]
        groups = [
            _group("rack1", parent="zone-a"),
            _group("zone-b", parent="region1"),
            _group("zone-a", parent="region1"),
            _group("region1"),
            _group("site1"),
        ]
        rows = [
            _group_member("rack1", "array2"),
            _group_member("rack1", "array1"),
            # No array of Pure1 has this name
            _group_member("rack1", "array4"),
            _group_member("zone-a", "array3"),
            _group_member("region1", "array5"),
            _group_member("region1", "zone-a", kind="topology-groups"),
            _group_member("zone-a", "rack1", kind="topology-groups"),
        ]
        _serve(pure1_client, arrays, [_fleet("fleet1")], [], groups, rows)

        result = pure1_fleet_map(module, pure1_client)

        in_rack1 = {
            "array1": _row_array("array1", "members"),
            "array2": _row_array("array2", "members"),
            "array4": _row_array("array4", "members", array_id=None),
        }
        assert result["topology_groups"] == {
            "fleet1": {
                "rack1": dict(
                    _group_entry(
                        "rack1", "zone-a", ["region1", "zone-a", "rack1"], (), in_rack1
                    ),
                    all_arrays=["array1", "array2"],
                ),
                "region1": _group_entry(
                    "region1",
                    None,
                    ["region1"],
                    ["zone-a", "zone-b"],
                    {"array5": _row_array("array5", "members")},
                    ["array1", "array2", "array3"],
                ),
                "site1": _group_entry("site1"),
                "zone-a": _group_entry(
                    "zone-a",
                    "region1",
                    ["region1", "zone-a"],
                    ["rack1"],
                    {"array3": _row_array("array3", "members")},
                    ["array1", "array2"],
                ),
                "zone-b": _group_entry("zone-b", "region1", ["region1", "zone-b"]),
            }
        }
        in_group = dict(group_source="members", group_fleet="fleet1")
        assert result["arrays"] == {
            "array1": _placed(
                group="rack1", path=["region1", "zone-a", "rack1"], **in_group
            ),
            "array2": _placed(
                group="rack1", path=["region1", "zone-a", "rack1"], **in_group
            ),
            "array3": _placed(group="zone-a", path=["region1", "zone-a"], **in_group),
            "array5": _placed(group="region1", path=["region1"], **in_group),
        }
        assert [entry["array"] for entry in result["mismatches"]] == [
            "array1",
            "array2",
            "array3",
            "array5",
        ]
        module.warn.assert_not_called()

    def test_the_path_of_an_array_is_a_list_of_its_own(self, pure1_client):
        _serve(
            pure1_client,
            [_array("array1"), _array("array2")],
            groups=[_group("zone-a")],
            group_members=[
                _group_member("zone-a", "array1"),
                _group_member("zone-a", "array2"),
            ],
        )

        result = pure1_fleet_map(ansible_module(), pure1_client)
        result["arrays"]["array1"]["topology_path"].append("changed")

        assert result["arrays"]["array2"]["topology_path"] == ["zone-a"]
        assert result["topology_groups"]["fleet1"]["zone-a"]["path"] == ["zone-a"]

    @pytest.mark.parametrize(
        "parents, expected, cut",
        [
            pytest.param(
                {"zone-a": "zone-b", "zone-b": "zone-a"},
                {"zone-a": ["zone-b", "zone-a"], "zone-b": ["zone-b"]},
                1,
                id="two groups",
            ),
            pytest.param(
                {"zone-a": "zone-a", "zone-b": None},
                {"zone-a": ["zone-a"], "zone-b": ["zone-b"]},
                1,
                id="a group that is its own parent",
            ),
            pytest.param(
                {"zone-a": "zone-b", "zone-b": "zone-c", "zone-c": "zone-a"},
                {
                    "zone-a": ["zone-c", "zone-b", "zone-a"],
                    "zone-b": ["zone-c", "zone-b"],
                    "zone-c": ["zone-c"],
                },
                1,
                id="three groups",
            ),
            pytest.param(
                {
                    "zone-a": "zone-b",
                    "zone-b": "zone-c",
                    "zone-c": "zone-b",
                    "zone-d": "zone-d",
                },
                {
                    "zone-a": ["zone-c", "zone-b", "zone-a"],
                    "zone-b": ["zone-c", "zone-b"],
                    "zone-c": ["zone-c"],
                    "zone-d": ["zone-d"],
                },
                2,
                id="a cycle above a group and a second cycle",
            ),
        ],
    )
    def test_cycle_of_parents_is_cut(self, pure1_client, parents, expected, cut):
        module = ansible_module()
        groups = [_group(name, parent=parent) for name, parent in parents.items()]
        _serve(
            pure1_client,
            [_array("array1")],
            groups=groups,
            group_members=[_group_member("zone-a", "array1")],
        )

        result = pure1_fleet_map(module, pure1_client)

        entries = result["topology_groups"]["fleet1"]
        assert {name: entry["path"] for name, entry in entries.items()} == expected
        assert {name: entry["parent"] for name, entry in entries.items()} == {
            name: (path[-2] if len(path) > 1 else None)
            for name, path in expected.items()
        }
        assert {name: entry["children"] for name, entry in entries.items()} == {
            name: sorted(
                child for child, path in expected.items() if path[-2:-1] == [name]
            )
            for name in expected
        }
        # array1 is in zone-a and so in every group of the path of zone-a
        assert {name: entry["all_arrays"] for name, entry in entries.items()} == {
            name: ["array1"] if name in expected["zone-a"] else [] for name in expected
        }
        assert result["arrays"]["array1"]["topology_path"] == expected["zone-a"]
        module.warn.assert_called_once_with(
            "Parents of topology groups that form a cycle are left out of the "
            "result: {0}.".format(cut)
        )

    def test_cycle_in_one_fleet_leaves_the_other_fleet_alone(self, pure1_client):
        module = ansible_module()
        groups = [
            _group("zone-a", "fleet1", parent="zone-b"),
            _group("zone-b", "fleet1", parent="zone-a"),
            _group("zone-a", "fleet2", parent="zone-b", id="id-2a"),
            _group("zone-b", "fleet2", id="id-2b"),
        ]
        _serve(pure1_client, groups=groups)

        result = pure1_fleet_map(module, pure1_client)

        assert {
            fleet: {name: entry["path"] for name, entry in entries.items()}
            for fleet, entries in result["topology_groups"].items()
        } == {
            "fleet1": {"zone-a": ["zone-b", "zone-a"], "zone-b": ["zone-b"]},
            "fleet2": {"zone-a": ["zone-b", "zone-a"], "zone-b": ["zone-b"]},
        }
        module.warn.assert_called_once_with(
            "Parents of topology groups that form a cycle are left out of the "
            "result: 1."
        )

    def test_parent_from_a_member_row_where_the_group_record_has_none(
        self, pure1_client
    ):
        module = ansible_module()
        groups = [
            _group("region1"),
            _group("region2"),
            _group("zone-a"),
            _group("zone-b", parent="region2"),
        ]
        rows = [
            _group_member("region1", "zone-a", kind="topology-groups"),
            # The record of zone-b names another parent, which wins
            _group_member("region1", "zone-b", kind="topology-groups"),
        ]
        _serve(pure1_client, groups=groups, group_members=rows)

        result = pure1_fleet_map(module, pure1_client)

        assert result["topology_groups"] == {
            "fleet1": {
                "region1": _group_entry("region1", children=["zone-a"]),
                "region2": _group_entry("region2", children=["zone-b"]),
                "zone-a": _group_entry("zone-a", "region1", ["region1", "zone-a"]),
                "zone-b": _group_entry("zone-b", "region2", ["region2", "zone-b"]),
            }
        }
        module.warn.assert_not_called()

    def test_groups_that_only_a_reference_names(self, pure1_client):
        module = ansible_module()
        groups = [_group("zone-a", parent="region9")]
        rows = [
            _group_member("region1", "zone-x", kind="topology-groups"),
            _group_member("zone-y", "array1", fleet="fleet2"),
        ]
        _serve(pure1_client, [_array("array1")], [], [], groups, rows)

        result = pure1_fleet_map(module, pure1_client)

        assert result["topology_groups"] == {
            "fleet1": {
                "region1": _reference_group("region1", children=["zone-x"]),
                "region9": _reference_group("region9", children=["zone-a"]),
                "zone-a": _group_entry("zone-a", "region9", ["region9", "zone-a"]),
                "zone-x": _reference_group(
                    "zone-x", parent="region1", path=["region1", "zone-x"]
                ),
            },
            "fleet2": {
                "zone-y": _reference_group(
                    "zone-y",
                    arrays={"array1": _row_array("array1", "members")},
                    all_arrays=["array1"],
                )
            },
        }
        # The fleet of a group, and of a row, is a fleet of the result
        assert result["fleets"] == {
            "fleet1": _fleet_entry("fleet1", as_of=(None, None)),
            "fleet2": _fleet_entry("fleet2", as_of=(None, None)),
        }
        assert result["arrays"] == {
            "array1": _placed(
                group="zone-y",
                group_source="members",
                group_fleet="fleet2",
                path=["zone-y"],
            )
        }
        module.warn.assert_not_called()

    def test_a_listed_group_is_not_replaced_by_a_reference_to_it(self, pure1_client):
        groups = [_group("zone-a", parent="region1"), _group("region1")]
        rows = [
            _group_member(
                "region1",
                "zone-a",
                kind="topology-groups",
                topology_group=_ref("region1", id="other-id"),
                member=_ref("zone-a", "topology-groups", id="other-id"),
            )
        ]
        _serve(pure1_client, groups=groups, group_members=rows)

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert result["topology_groups"] == {
            "fleet1": {
                "region1": _group_entry("region1", children=["zone-a"]),
                "zone-a": _group_entry("zone-a", "region1", ["region1", "zone-a"]),
            }
        }

    def test_array_that_only_its_own_record_places(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1", group="zone-a"), _array("array2")],
            [_fleet("fleet1")],
            [_member("fleet1", "array1")],
            [_group("region1"), _group("zone-a", parent="region1")],
        )

        result = pure1_fleet_map(module, pure1_client)

        assert result["topology_groups"] == {
            "fleet1": {
                "region1": _group_entry(
                    "region1", children=["zone-a"], below=["array1"]
                ),
                "zone-a": _group_entry(
                    "zone-a",
                    "region1",
                    ["region1", "zone-a"],
                    arrays={"array1": _record_array("array1")},
                ),
            }
        }
        assert result["arrays"] == {
            "array1": _placed(
                "fleet1",
                "joined",
                "both",
                "zone-a",
                "arrays",
                "fleet1",
                ["region1", "zone-a"],
            ),
            "array2": _placed(),
        }
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": [],
                "array_record": "zone-a",
            }
        ]
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "fleet, reference, expected",
        [
            pytest.param(
                None,
                dict(name="zone-a", id="id-2a"),
                "fleet2",
                id="by the id of the group",
            ),
            pytest.param(
                "fleet1",
                dict(name="zone-a", id="id-2a"),
                "fleet2",
                id="by the id before the fleet of the array",
            ),
            pytest.param(
                None,
                dict(name="renamed", id="id-2a"),
                "fleet2",
                id="by the id whatever the name",
            ),
            pytest.param(
                "fleet2",
                dict(name="zone-a"),
                "fleet2",
                id="by the name in the fleet of the array",
            ),
            pytest.param(
                "fleet2",
                dict(name="zone-a", id="id-gone"),
                "fleet2",
                id="by the name when no group has the id",
            ),
            pytest.param(
                None,
                dict(name="zone-b"),
                "fleet2",
                id="by a name that one fleet alone has",
            ),
            pytest.param(
                "fleet1",
                dict(name="zone-b"),
                "fleet2",
                id="by such a name in another fleet than that of the array",
            ),
            pytest.param(
                None, dict(name="zone-a"), None, id="not by a name two fleets have"
            ),
            pytest.param(
                "fleet3",
                dict(name="zone-a"),
                None,
                id="not by such a name with the array in a third fleet",
            ),
            pytest.param(
                "fleet1", dict(name="zone-x"), None, id="not a group no listing has"
            ),
        ],
    )
    def test_group_of_an_array_record_is_found(
        self, pure1_client, fleet, reference, expected
    ):
        module = ansible_module()
        array = _array(
            "array1",
            fleet=fleet,
            topology_group=sdk_model("FixedReference", **reference),
        )
        groups = [
            _group("zone-a", "fleet1"),
            _group("zone-a", "fleet2", id="id-2a"),
            _group("zone-b", "fleet2", id="id-2b"),
        ]
        _serve(pure1_client, [array], groups=groups)

        result = pure1_fleet_map(module, pure1_client)

        name = "zone-b" if reference["name"] == "zone-b" else "zone-a"
        holding = [
            (key, group)
            for key, entries in result["topology_groups"].items()
            for group, entry in entries.items()
            if entry["arrays"]
        ]
        placement = result["arrays"]["array1"]
        if expected is None:
            assert holding == []
            assert placement == _placed(
                fleet,
                None,
                "arrays" if fleet else None,
                reference["name"],
                "arrays",
                None,
                None,
            )
        else:
            assert holding == [(expected, name)]
            assert result["topology_groups"][expected][name]["arrays"] == {
                "array1": _record_array("array1")
            }
            assert placement == _placed(
                fleet,
                None,
                "arrays" if fleet else None,
                name,
                "arrays",
                expected,
                [name],
            )
        assert sorted(result["topology_groups"]) == ["fleet1", "fleet2"]
        assert result["mismatches"][-1] == {
            "array": "array1",
            "kind": "topology_group",
            "rows": [],
            "array_record": reference["name"],
        }
        module.warn.assert_not_called()

    def test_row_and_array_record_name_different_groups(self, pure1_client):
        module = ansible_module()
        _serve(
            pure1_client,
            [_array("array1", group="zone-b"), _array("array2", group="zone-a")],
            groups=[_group("zone-a"), _group("zone-b")],
            group_members=[
                _group_member("zone-a", "array1"),
                _group_member("zone-a", "array2"),
            ],
        )

        result = pure1_fleet_map(module, pure1_client)

        assert result["topology_groups"] == {
            "fleet1": {
                "zone-a": _group_entry(
                    "zone-a",
                    arrays={
                        "array1": _row_array("array1", "members"),
                        "array2": _row_array("array2", "both"),
                    },
                ),
                # The row wins: the array is not in the group of its record
                "zone-b": _group_entry("zone-b"),
            }
        }
        in_zone_a = dict(group="zone-a", group_fleet="fleet1", path=["zone-a"])
        assert result["arrays"] == {
            "array1": _placed(group_source="members", **in_zone_a),
            "array2": _placed(group_source="both", **in_zone_a),
        }
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": ["zone-a"],
                "array_record": "zone-b",
            }
        ]
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "record, statuses, expected, source",
        [
            pytest.param(
                "zone-b",
                {"zone-b": "joined", "zone-a": "joined"},
                "zone-b",
                "both",
                id="the group of the array record",
            ),
            pytest.param(
                "zone-b",
                {"zone-b": "removing", "zone-a": "joined"},
                "zone-b",
                "both",
                id="the group of the array record before a joined one",
            ),
            pytest.param(
                None,
                {"zone-a": "removing", "zone-b": "joined"},
                "zone-b",
                "members",
                id="the joined one",
            ),
            pytest.param(
                None,
                {"zone-b": "joining", "zone-a": "joining"},
                "zone-a",
                "members",
                id="the first by name",
            ),
        ],
    )
    def test_rows_in_two_groups(self, pure1_client, record, statuses, expected, source):
        rows = [
            _group_member(group, "array1", status=status)
            for group, status in statuses.items()
        ]
        _serve(
            pure1_client,
            [_array("array1", group=record)],
            groups=[_group("zone-a"), _group("zone-b")],
            group_members=rows,
        )

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert result["arrays"] == {
            "array1": _placed(
                group=expected,
                group_source=source,
                group_fleet="fleet1",
                path=[expected],
            )
        }
        entries = result["topology_groups"]["fleet1"]
        assert [list(entry["arrays"]) for entry in entries.values()] == [
            ["array1"],
            ["array1"],
        ]
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": ["zone-a", "zone-b"],
                "array_record": record,
            }
        ]

    def test_rows_in_groups_of_one_name_in_two_fleets(self, pure1_client):
        rows = [
            _group_member("zone-a", "array1", fleet="fleet2"),
            _group_member("zone-a", "array1", fleet="fleet1"),
        ]
        _serve(pure1_client, [_array("array1", group="zone-a")], group_members=rows)

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert result["arrays"] == {
            "array1": _placed(
                group="zone-a",
                group_source="both",
                group_fleet="fleet1",
                path=["zone-a"],
            )
        }
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": ["zone-a", "zone-a"],
                "array_record": "zone-a",
            }
        ]
        assert list(result["topology_groups"]) == ["fleet1", "fleet2"]

    def test_mismatches_are_in_the_order_of_array_and_kind(self, pure1_client):
        _serve(
            pure1_client,
            [
                _array("array2", fleet="fleet1", group="zone-a"),
                _array("array1", group="zone-a"),
            ],
            groups=[_group("zone-a")],
        )

        result = pure1_fleet_map(ansible_module(), pure1_client)

        assert [(entry["array"], entry["kind"]) for entry in result["mismatches"]] == [
            ("array1", "topology_group"),
            ("array2", "fleet"),
            ("array2", "topology_group"),
        ]

    def test_array_in_a_group_and_in_a_group_below_it_is_counted_once(
        self, pure1_client
    ):
        _serve(
            pure1_client,
            [_array("array1")],
            groups=[_group("region1"), _group("zone-a", parent="region1")],
            group_members=[
                _group_member("region1", "array1"),
                _group_member("zone-a", "array1"),
            ],
        )

        result = pure1_fleet_map(ansible_module(), pure1_client)

        entries = result["topology_groups"]["fleet1"]
        assert entries["region1"]["all_arrays"] == ["array1"]
        assert entries["zone-a"]["all_arrays"] == ["array1"]

    def test_groups_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        groups = [
            _group("zone-a", name=None),
            _group("zone-a", fleet=None),
            _group("zone-a"),
            _group("zone-a", id="id-second", parent="region1"),
            _group("zone-a", "fleet2", id="id-2a"),
        ]
        _serve(pure1_client, groups=groups)

        result = pure1_fleet_map(module, pure1_client)

        # The first group of a name in a fleet is the one that is filed
        assert result["topology_groups"] == {
            "fleet1": {"zone-a": _group_entry("zone-a")},
            "fleet2": {"zone-a": dict(_group_entry("zone-a"), id="id-2a")},
        }
        module.warn.assert_called_once_with(
            "Topology groups that Pure1 returned without a name or a fleet, or "
            "with the name of another group of the fleet, are left out of the "
            "result: 3."
        )

    def test_group_member_rows_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        rows = [
            _group_member("zone-a", "array1", fleet=None),
            _group_member("zone-a", "array1", topology_group=_ref()),
            _group_member("zone-a", "array1", member=_ref()),
            _group_member("zone-a", "array1", kind="hosts"),
            _group_member("zone-a", "array2"),
            _group_member("zone-a", "array2", status="removing"),
            _group_member("zone-a", "array3", member=_ref("array3")),
        ]
        _serve(pure1_client, groups=[_group("zone-a")], group_members=rows)

        result = pure1_fleet_map(module, pure1_client)

        # The first row of an array in a group is the one that is filed, and
        # a row without a resource type is one of an array
        assert result["topology_groups"] == {
            "fleet1": {
                "zone-a": dict(
                    _group_entry(
                        "zone-a",
                        arrays={
                            "array2": _row_array("array2", "members", array_id=None),
                            "array3": _row_array("array3", "members", array_id=None),
                        },
                    ),
                    all_arrays=[],
                )
            }
        }
        module.warn.assert_called_once_with(
            "Topology group member rows that Pure1 returned without a fleet, a "
            "group or a member name, for a member that is neither an array nor "
            "a group or a second time for one array are left out of the result: 5."
        )


LISTINGS = [
    pytest.param(
        "get_arrays",
        "ArrayGetResponse",
        "arrays",
        lambda: [_array("array" + str(n)) for n in (1, 2, 3)],
        lambda result: list(result["arrays"]),
        ["array1", "array2", "array3"],
        id="get_arrays",
    ),
    pytest.param(
        "get_fleets",
        "FleetGetResponse",
        "fleets",
        lambda: [_fleet("fleet" + str(n)) for n in (1, 2, 3)],
        lambda result: list(result["fleets"]),
        ["fleet1", "fleet2", "fleet3"],
        id="get_fleets",
    ),
    pytest.param(
        "get_fleets_members",
        "FleetMemberGetResponse",
        "fleet member rows",
        lambda: [_member("fleet1", "array" + str(n)) for n in (1, 2, 3)],
        lambda result: list(result["fleets"]["fleet1"]["members"]),
        ["array1", "array2", "array3"],
        id="get_fleets_members",
    ),
    pytest.param(
        "get_topology_groups",
        "TopologyGroupGetResponse",
        "topology groups",
        lambda: [_group("zone-" + letter) for letter in "abc"],
        lambda result: list(result["topology_groups"]["fleet1"]),
        ["zone-a", "zone-b", "zone-c"],
        id="get_topology_groups",
    ),
    pytest.param(
        "get_topology_groups_members",
        "TopologyGroupMemberGetResponse",
        "topology group member rows",
        lambda: [_group_member("zone-a", "array" + str(n)) for n in (1, 2, 3)],
        lambda result: list(result["topology_groups"]["fleet1"]["zone-a"]["arrays"]),
        ["array1", "array2", "array3"],
        id="get_topology_groups_members",
    ),
]
FAILURES = [
    pytest.param(
        "return_value",
        error_response(403, "Access denied"),
        "Pure1 request {0} failed (HTTP 403): Access denied",
        id="error answer",
    ),
    pytest.param(
        "return_value",
        error_response(400, "Invalid value", "Unknown fleet"),
        "Pure1 request {0} failed (HTTP 400): Invalid value: Unknown fleet",
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
        "Pure1 request {0} failed: TimeoutError: The read operation timed out",
        id="transport error",
    ),
]
ORDER = [
    "get_arrays",
    "get_fleets",
    "get_fleets_members",
    "get_topology_groups",
    "get_topology_groups_members",
]


class TestRequests:
    """Every listing goes through pure1_list: pages, totals and failures."""

    @pytest.mark.parametrize("method, answer, what, items, read, expected", LISTINGS)
    def test_listing_is_read_across_pages(
        self, pure1_client, method, answer, what, items, read, expected
    ):
        module = ansible_module()
        _serve(pure1_client)
        answers = paged_answers(answer, items(), page_size=2)
        getattr(pure1_client, method).side_effect = answers

        result = pure1_fleet_map(module, pure1_client)

        assert answers.requests == [{}, {"continuation_token": "page-2"}]
        assert read(result) == expected
        module.warn.assert_not_called()

    @pytest.mark.parametrize("method, answer, what, items, read, expected", LISTINGS)
    def test_listing_that_comes_short_gives_a_warning(
        self, pure1_client, method, answer, what, items, read, expected
    ):
        module = ansible_module()
        _serve(pure1_client)
        answers = paged_answers(answer, items(), total_item_count=5)
        getattr(pure1_client, method).side_effect = answers

        result = pure1_fleet_map(module, pure1_client)

        # pure1_list asks once more, for what would follow the three items
        assert answers.requests == [{}, {"offset": 3}]
        assert read(result) == expected
        module.warn.assert_called_once_with(
            "Pure1 returned 3 of 5 {0}: the result may be incomplete.".format(what)
        )

    def test_listing_that_comes_short_without_a_total(self, pure1_client):
        module = ansible_module()
        _serve(pure1_client)
        requests = []

        def answer(**page):
            requests.append(page)
            if "offset" in page:
                raise TypeError(
                    "get_fleets() got an unexpected keyword argument 'offset'"
                )
            items = ItemIterator(
                None,
                {},
                None,
                None,
                [_fleet("fleet1")],
                None,
                more_items_remaining=False,
            )
            return ValidResponse(200, None, None, items, {})

        pure1_client.get_fleets.side_effect = answer

        result = pure1_fleet_map(module, pure1_client)

        assert requests == [{}, {"offset": 1}]
        assert list(result["fleets"]) == ["fleet1"]
        module.warn.assert_called_once_with(
            "Pure1 returned 1 of an unknown number of fleets: the result may be "
            "incomplete."
        )

    @pytest.mark.parametrize("method", ORDER)
    @pytest.mark.parametrize("attribute, answer, expected", FAILURES)
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, expected
    ):
        module = ansible_module()
        _serve_lab(pure1_client)
        failing = getattr(pure1_client, method)
        failing.return_value = None
        setattr(failing, attribute, answer)

        with pytest.raises(SystemExit):
            pure1_fleet_map(module, pure1_client)

        module.fail_json.assert_called_once_with(msg=expected.format(method))
        module.warn.assert_not_called()
        # Nothing is asked for after the request that failed
        for later in ORDER[ORDER.index(method) + 1 :]:
            getattr(pure1_client, later).assert_not_called()
        for earlier in ORDER[: ORDER.index(method) + 1]:
            getattr(pure1_client, earlier).assert_called_once_with()

    def test_error_answer_to_a_later_request_fails_the_task(self, pure1_client):
        module = ansible_module()
        _serve(pure1_client)
        rows = [_member("fleet1", "array" + str(n)) for n in (1, 2, 3)]
        answers = paged_answers("FleetMemberGetResponse", rows, total_item_count=5)

        def answer(**page):
            if "offset" in page:
                return error_response(500, "Internal server error")
            return answers(**page)

        pure1_client.get_fleets_members.side_effect = answer

        with pytest.raises(SystemExit):
            pure1_fleet_map(module, pure1_client)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_fleets_members failed (HTTP 500): Internal "
            "server error"
        )
        module.warn.assert_not_called()
        pure1_client.get_topology_groups.assert_not_called()


def _tag(array, tag_key, value="v", namespace="pure1", **overrides):
    fields = dict(
        key=tag_key, value=value, namespace=namespace, resource=_ref(array, "arrays")
    )
    fields.update(overrides)
    return sdk_model("Tag", **fields)


class TestTagMap:
    """The tags of every array from one listing."""

    def test_tags_are_grouped_by_array_id(self, pure1_client):
        module = ansible_module()
        pure1_client.get_arrays_tags.return_value = valid_response(
            [
                _tag("array2", "contact", "team1"),
                _tag("array1", "site", "east"),
                _tag("array1", "class", "gold, silver"),
                _tag("array1", "site", "west", namespace="other"),
                _tag("array1", "site", "north", namespace="other"),
            ]
        )

        result = pure1_tag_map(module, pure1_client)

        assert result == {
            "id-array1": [
                {"key": "class", "value": "gold, silver", "namespace": "pure1"},
                {"key": "site", "value": "north", "namespace": "other"},
                {"key": "site", "value": "west", "namespace": "other"},
                {"key": "site", "value": "east", "namespace": "pure1"},
            ],
            "id-array2": [{"key": "contact", "value": "team1", "namespace": "pure1"}],
        }
        pure1_client.get_arrays_tags.assert_called_once_with()
        pure1_client.get_arrays.assert_not_called()
        module.warn.assert_not_called()

    def test_no_tags(self, pure1_client):
        module = ansible_module()
        pure1_client.get_arrays_tags.return_value = valid_response([])

        assert pure1_tag_map(module, pure1_client) == {}
        pure1_client.get_arrays_tags.assert_called_once_with()
        module.warn.assert_not_called()

    def test_tag_with_only_its_key_and_its_array(self, pure1_client):
        module = ansible_module()
        bare = sdk_model(
            "Tag", key="contact", resource=sdk_model("FixedReference", id="id-array1")
        )
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("array1", "contact", "", ""), bare]
        )

        result = pure1_tag_map(module, pure1_client)

        assert result == {
            "id-array1": [
                {"key": "contact", "value": "", "namespace": ""},
                {"key": "contact", "value": None, "namespace": None},
            ]
        }
        module.warn.assert_not_called()

    def test_tags_that_cannot_be_filed_are_left_out(self, pure1_client):
        module = ansible_module()
        pure1_client.get_arrays_tags.return_value = valid_response(
            [
                _tag("array1", "contact", key=None),
                _tag("array1", "contact", resource=sdk_model("FixedReference")),
                _tag(
                    "array1",
                    "contact",
                    resource=sdk_model("FixedReference", name="array1"),
                ),
                _tag("array1", "contact", resource=None),
                _tag("array1", "", ""),
            ]
        )

        result = pure1_tag_map(module, pure1_client)

        # An empty key is a key
        assert result == {"id-array1": [{"key": "", "value": "", "namespace": "pure1"}]}
        module.warn.assert_called_once_with(
            "Array tags that Pure1 returned without a key or without the id of "
            "their array are left out of the result: 4."
        )

    def test_tags_are_read_across_pages(self, pure1_client):
        module = ansible_module()
        tags = [_tag("array1", "key" + str(n)) for n in (1, 2, 3)]
        answers = paged_answers("TagGetResponse", tags, page_size=2)
        pure1_client.get_arrays_tags.side_effect = answers

        result = pure1_tag_map(module, pure1_client)

        assert answers.requests == [{}, {"continuation_token": "page-2"}]
        assert [tag["key"] for tag in result["id-array1"]] == ["key1", "key2", "key3"]
        module.warn.assert_not_called()

    def test_listing_that_comes_short_gives_one_warning(self, pure1_client):
        """Pure1 ignores limit and offset on this listing and has no token for it."""
        module = ansible_module()
        tags = [_tag("array1", "key" + str(n)) for n in (1, 2, 3)]
        requests = []

        def answer(**page):
            requests.append(page)
            return valid_response(copy.deepcopy(tags), total_item_count=5)

        pure1_client.get_arrays_tags.side_effect = answer

        result = pure1_tag_map(module, pure1_client)

        # pure1_list asks for what follows and gets the same three again
        assert requests == [{}, {"offset": 3}]
        assert [tag["key"] for tag in result["id-array1"]] == ["key1", "key2", "key3"]
        module.warn.assert_called_once_with(
            "Pure1 returned 3 of 5 array tags: the result may be incomplete."
        )

    @pytest.mark.parametrize("attribute, answer, expected", FAILURES)
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, attribute, answer, expected
    ):
        module = ansible_module()
        setattr(pure1_client.get_arrays_tags, attribute, answer)

        with pytest.raises(SystemExit):
            pure1_tag_map(module, pure1_client)

        module.fail_json.assert_called_once_with(msg=expected.format("get_arrays_tags"))
        module.warn.assert_not_called()


TAGS = {
    "id-array1": [
        {"key": "class", "value": "gold, silver", "namespace": "pure1"},
        {"key": "site", "value": "east", "namespace": "pure1"},
    ],
    "id-array2": [{"key": "contact", "value": "team1", "namespace": "pure1"}],
}
# 13:04:05.000 UTC and 59.999 seconds
NOW = ARRAY_AS_OF + 59999


class TestArrayRecord:
    """One array as every module returns it."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_whole_record(self):
        array = _array("array1", fleet="fleet1", group="zone-a")

        record = pure1_array_record(array, tags=TAGS, now_ms=NOW)

        assert record == {
            "id": "id-array1",
            "name": "array1",
            "fqdn": "array1.example.com",
            "os": "Purity//FA",
            "type": "flasharray",
            "model": "FA-X70R4",
            "version": "6.5.0",
            "fleet": "fleet1",
            "fleet_source": "arrays",
            "topology_group": "zone-a",
            "tags": [
                {"key": "class", "value": "gold, silver", "namespace": "pure1"},
                {"key": "site", "value": "east", "namespace": "pure1"},
            ],
            "as_of": "2021-02-03 13:04:05 UTC",
            "as_of_epoch": 1612357445000,
            "age_seconds": 59,
        }
        assert list(record) == [
            "id",
            "name",
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

    def test_array_with_only_its_name(self):
        record = pure1_array_record(sdk_model("Array", name="array1"))

        assert record == {
            "id": None,
            "name": "array1",
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

    def test_array_with_empty_references(self):
        """Pure1 sends an empty object for the fleet of an array that has none."""
        record = pure1_array_record(_array("array1"), tags=TAGS, now_ms=NOW)

        assert record["fleet"] is None
        assert record["fleet_source"] is None
        assert record["topology_group"] is None

    @pytest.mark.parametrize(
        "system, expected",
        [
            ("Purity//FA", "flasharray"),
            ("Purity", "flasharray"),
            ("Purity//FB", "flashblade"),
            ("Elasticity", "objectengine"),
            ("purity//fa", None),
            ("Solaris", None),
            ("", None),
            (None, None),
        ],
    )
    def test_type_is_derived_from_the_operating_system(self, system, expected):
        record = pure1_array_record(_array("array1", os=system))

        assert record["os"] == system
        assert record["type"] == expected

    def test_array_types(self):
        assert ARRAY_TYPES == {
            "Purity//FA": "flasharray",
            "Purity": "flasharray",
            "Purity//FB": "flashblade",
            "Elasticity": "objectengine",
        }

    def test_tags_are_those_of_the_id_of_the_array(self):
        named_as_an_id = _array("id-array2", id="id-array9")

        assert pure1_array_record(_array("array2"), tags=TAGS)["tags"] == [
            {"key": "contact", "value": "team1", "namespace": "pure1"}
        ]
        assert pure1_array_record(_array("array3"), tags=TAGS)["tags"] == []
        assert pure1_array_record(named_as_an_id, tags=TAGS)["tags"] == []
        assert pure1_array_record(_array("array1"), tags={})["tags"] == []
        assert pure1_array_record(_array("array1"))["tags"] == []
        assert pure1_array_record(sdk_model("Array", name="a"), tags=TAGS)["tags"] == []

    def test_tags_of_the_record_are_a_copy(self):
        tags = copy.deepcopy(TAGS)

        record = pure1_array_record(_array("array1"), tags=tags)
        record["tags"][0]["value"] = "changed"
        record["tags"].pop()

        assert tags == TAGS

    def test_membership_wins_over_the_array_record(self, pure1_client):
        """The record of array5 names no fleet, the member rows do."""
        _serve_lab(pure1_client)
        arrays = {array.name: array for array in _lab_arrays()}
        membership = pure1_fleet_map(ansible_module(), pure1_client)

        records = {
            name: pure1_array_record(array, membership=membership, now_ms=NOW)
            for name, array in arrays.items()
        }

        assert {
            name: (
                record["type"],
                record["fleet"],
                record["fleet_source"],
                record["topology_group"],
            )
            for name, record in records.items()
        } == {
            "array1": ("flasharray", "fleet1", "both", None),
            "array2": ("flasharray", "fleet1", "both", None),
            "array3": ("flasharray", "fleet1", "both", None),
            "array4": ("flasharray", "fleet1", "both", None),
            "array5": ("flashblade", "fleet1", "members", "site1"),
            "array6": ("flashblade", "fleet1", "both", "site1"),
            "array7": ("flasharray", "fleet2", "arrays", None),
            "array8": ("flasharray", None, None, None),
            "array9": ("flashblade", None, None, None),
        }
        assert records["array8"]["fqdn"] is None
        assert records["array5"] == {
            "id": "id-array5",
            "name": "array5",
            "fqdn": "array5.example.com",
            "os": "Purity//FB",
            "type": "flashblade",
            "model": "FlashBlade//S200",
            "version": "4.5.0",
            "fleet": "fleet1",
            "fleet_source": "members",
            "topology_group": "site1",
            "tags": [],
            "as_of": "2021-02-03 13:04:05 UTC",
            "as_of_epoch": 1612357445000,
            "age_seconds": 59,
        }

    def test_membership_replaces_what_the_array_record_names(self):
        array = _array("array1", fleet="fleet2", group="zone-b")
        membership = {
            "arrays": {"array1": _placed("fleet1", "joining", "members", "zone-a")}
        }

        record = pure1_array_record(array, membership=membership)

        assert (record["fleet"], record["fleet_source"]) == ("fleet1", "members")
        assert record["topology_group"] == "zone-a"

    def test_membership_that_places_the_array_nowhere(self):
        array = _array("array1", fleet="fleet2", group="zone-b")

        record = pure1_array_record(array, membership={"arrays": {"array1": _placed()}})

        assert (record["fleet"], record["fleet_source"]) == (None, None)
        assert record["topology_group"] is None

    @pytest.mark.parametrize(
        "membership",
        [
            pytest.param(None, id="none"),
            pytest.param({}, id="empty"),
            pytest.param(
                {"arrays": {"array2": _placed("fleet1", "joined", "both", "zone-a")}},
                id="without the array",
            ),
        ],
    )
    def test_array_record_alone(self, membership):
        array = _array("array1", fleet="fleet2", group="zone-b")

        record = pure1_array_record(array, membership=membership)

        assert (record["fleet"], record["fleet_source"]) == ("fleet2", "arrays")
        assert record["topology_group"] == "zone-b"

    @pytest.mark.parametrize(
        "now_ms, expected",
        [
            pytest.param(ARRAY_AS_OF, 0, id="the same millisecond"),
            pytest.param(ARRAY_AS_OF + 999, 0, id="under a second"),
            pytest.param(ARRAY_AS_OF + 1000, 1, id="one second"),
            pytest.param(ARRAY_AS_OF + 3661999, 3661, id="whole seconds"),
            pytest.param(ARRAY_AS_OF - 1, 0, id="a clock behind Pure1"),
            pytest.param(ARRAY_AS_OF - 5000, 0, id="a clock far behind Pure1"),
            pytest.param(0, 0, id="a clock at 1970"),
        ],
    )
    def test_age_is_measured_against_now(self, now_ms, expected):
        record = pure1_array_record(_array("array1"), now_ms=now_ms)

        assert record["age_seconds"] == expected
        assert isinstance(record["age_seconds"], int)

    def test_age_is_measured_against_the_clock_without_now(self, monkeypatch):
        monkeypatch.setattr(fleets_utils.time, "time", lambda: 1612357505.75)

        record = pure1_array_record(_array("array1"))

        assert record["age_seconds"] == 60

    def test_no_age_without_a_stamp(self):
        record = pure1_array_record(_array("array1", as_of=None), now_ms=NOW)

        assert (record["as_of"], record["as_of_epoch"]) == (None, None)
        assert record["age_seconds"] is None

    def test_stamp_of_1970(self):
        record = pure1_array_record(_array("array1", as_of=0), now_ms=5000)

        assert record["as_of"] == "1970-01-01 00:00:00 UTC"
        assert record["as_of_epoch"] == 0
        assert record["age_seconds"] == 5
