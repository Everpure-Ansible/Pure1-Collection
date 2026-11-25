#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_fleets
version_added: '1.6.0'
short_description: Collect fleets and their members from Pure1
description:
  - Collect the fleets of the organization from Pure1, each with its members,
    the membership status of every member, the coordinators of the fleet and
    what Pure1 knows of each member array.
  - Use it to check that every member of a fleet is joined before a change
    that spans the fleet, to find the coordinator of a fleet, or to get the
    arrays of a fleet for a loop.
  - Pure1 says in two places which fleet an array is in, the member rows of
    the fleet and the fleet named on the record of the array, and the two can
    differ. The module reports an array as a member of the fleet of its member
    row, and an array without a member row as a member of the fleet that its
    own record names. C(source) of a member tells which of the two places it
    comes from, and C(mismatches) lists the arrays for which they differ.
  - Pure1 refreshes the member rows as a periodic snapshot, about once an
    hour, so the status of a member can be about an hour old. C(as_of) of a
    member is the time of that snapshot. C(as_of) of the fleet is the time of
    the data of the fleet itself and says nothing about its members.
options:
  name:
    description:
      - Report only the fleets with these names.
      - An empty list selects nothing, and every fleet is reported.
      - A name is matched without regard to case, and the result carries the
        name as Pure1 spells it.
      - The task fails when Pure1 does not know a fleet of one of the names.
      - Mutually exclusive with I(array). A task that gives both options
        fails, also when one of them is an empty list.
    type: list
    elements: str
  array:
    description:
      - Report only the fleets that these arrays are members of, with
        C(members) narrowed to these arrays. C(member_count) and
        C(coordinators) still describe the whole fleet.
      - An empty list selects nothing, and every fleet is reported with all
        its members.
      - A name is matched without regard to case, and the result carries the
        name as Pure1 spells it.
      - The task fails when Pure1 does not know an array of one of the names.
        An array that is in no fleet is not an error, and when none of the
        arrays is in a fleet C(fleets) is empty.
      - Mutually exclusive with I(name). A task that gives both options
        fails, also when one of them is an empty list.
    type: list
    elements: str
notes:
  - This module requires py-pure-client 1.99.0 or later, the first release
    with the C(get_fleets_members), C(get_topology_groups) and
    C(get_topology_groups_members) methods of Pure1 REST 1.7. It also uses
    C(get_fleets) of Pure1 REST 1.6, which py-pure-client 1.93.0 and later
    have.
  - The module reads five listings whatever the options select, the arrays,
    the fleets, the fleet members, the topology groups and their members,
    each with one request for every 1,000 objects. I(array) costs one more
    request.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect every fleet with its members
  everpure.pure1.pure1_fleets:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: fleets

- name: Show the coordinators of each fleet
  ansible.builtin.debug:
    msg: "{{ item.key }} is coordinated by {{ item.value.coordinators | join(', ') }}"
  loop: "{{ fleets['pure1_fleets']['fleets'] | dict2items }}"

- name: Show the arrays whose fleet or topology group Pure1 reports in two ways
  ansible.builtin.debug:
    msg: "{{ fleets['pure1_fleets']['mismatches'] }}"
  when: fleets['pure1_fleets']['mismatches'] | length > 0

- name: Collect one fleet
  everpure.pure1.pure1_fleets:
    name:
      - fleet1
  register: fleet

- name: Check that every member of the fleet is joined
  ansible.builtin.assert:
    that:
      - members | length > 0
      - members | rejectattr('value.status', 'eq', 'joined') | list | length == 0
    fail_msg: The fleet has no member, or a member is not joined or has no member row
  vars:
    members: "{{ fleet['pure1_fleets']['fleets']['fleet1']['members'] | dict2items }}"

- name: Collect the fleets of two arrays
  everpure.pure1.pure1_fleets:
    array:
      - array1
      - array2
  register: placed

- name: Show the fleet of each of the two arrays that is in one
  ansible.builtin.debug:
    msg: "{{ item.value.members | list | join(', ') }} in {{ item.key }}, which has {{ item.value.member_count }} members"
  loop: "{{ placed['pure1_fleets']['fleets'] | dict2items }}"
"""

RETURN = r"""
pure1_fleets:
  description: The fleets that Pure1 knows, with their members
  returned: always
  type: dict
  contains:
    fleets:
      description:
        - The fleets, as a dictionary keyed by fleet name. The value for a
          fleet is a dictionary with the keys listed here.
        - A fleet without members is reported as well. A fleet that Pure1
          does not list, but that a member row, a topology group or the
          record of an array names, is reported with the C(id) of that
          reference and without a time.
        - Empty when Pure1 knows no fleet, and with I(array) when none of the
          arrays is in a fleet.
      type: dict
      contains:
        id:
          description: The ID of the fleet in Pure1.
          type: str
        as_of:
          description:
            - The time of the data Pure1 has of the fleet, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
            - Null for a fleet that Pure1 does not list.
          type: str
        as_of_epoch:
          description: The same time in milliseconds since the epoch, as Pure1 gives it.
          type: int
        member_count:
          description:
            - The number of members of the fleet.
            - It counts every member, also with I(array), which narrows
              C(members).
          type: int
        coordinators:
          description:
            - The names of the members that coordinate the fleet, in
              alphabetical order.
            - It names every coordinator, also with I(array), which narrows
              C(members).
          type: list
          elements: str
        members:
          description:
            - The members of the fleet, as a dictionary keyed by array name.
              The value for a member is a dictionary with the keys listed
              here.
            - A key is null when Pure1 reports no value for it.
          type: dict
          contains:
            status:
              description:
                - The membership status that the member row gives, one of
                  C(joining), C(joined) and C(removing).
                - Null for a member that has no member row (C(source) is
                  C(arrays)).
              type: str
            status_details:
              description:
                - More about the membership status, an empty string when
                  Pure1 has nothing to add.
                - Null for a member that has no member row.
              type: str
            coordinator:
              description:
                - Whether the member coordinates the fleet.
                - Null for a member that has no member row.
              type: bool
            source:
              description:
                - Where Pure1 says that the array is in the fleet.
                - C(both) when the array has a member row in the fleet and
                  its own record names the fleet as well.
                - C(members) when the array has a member row in the fleet
                  and its own record names no fleet or another one.
                - C(arrays) when only the record of the array names the
                  fleet and the array has no member row in any fleet.
              type: str
            as_of:
              description:
                - The time of the member row, in UTC, as
                  C(YYYY-MM-DD HH:MM:SS UTC). Pure1 refreshes the member rows
                  about once an hour, so this is how current C(status) is.
                - Null for a member that has no member row.
              type: str
            as_of_epoch:
              description: The same time in milliseconds since the epoch, as Pure1 gives it.
              type: int
            member_id:
              description:
                - The ID of the member in its member row. For a FlashArray it
                  is not the ID of the array in Pure1, which is C(array_id).
                - Null for a member that has no member row.
              type: str
            array_id:
              description:
                - The ID in Pure1 of the array that has the name of the
                  member.
                - Null when Pure1 knows no array of that name. C(fqdn),
                  C(os), C(model), C(version), C(topology_group) and
                  C(reported_fleet) are null then as well.
              type: str
            fqdn:
              description: The fully qualified domain name of the array.
              type: str
            os:
              description: The operating system of the array, for example C(Purity//FA).
              type: str
            model:
              description: The model of the array.
              type: str
            version:
              description: The version of the operating system of the array.
              type: str
            topology_group:
              description:
                - The name of the topology group that the array is directly
                  in.
                - It is the group of the member row the array has in a
                  topology group, and where it has none the group that the
                  record of the array names.
              type: str
            reported_fleet:
              description:
                - The name of the fleet that the record of the array names.
                - It differs from the fleet the member is reported in when
                  C(source) is C(members).
              type: str
    mismatches:
      description:
        - The arrays for which the member rows and the record of the array
          differ, about the fleet or about the topology group of the array.
          The module gives one warning that counts them.
        - With I(name) only the arrays that are members of one of the named
          fleets, whose record names one of them, or that are in a topology
          group of one of them, and with I(array) only the named arrays.
        - Empty when the two agree for every array.
      type: list
      elements: dict
      contains:
        array:
          description: The name of the array.
          type: str
        kind:
          description: What the two differ about, C(fleet) or C(topology_group).
          type: str
        rows:
          description:
            - The names of the fleets, or of the topology groups, in which
              the array has a member row, in alphabetical order.
            - Empty when it has no member row.
          type: list
          elements: str
        array_record:
          description:
            - The name of the fleet, or of the topology group, that the
              record of the array names.
            - Null when it names none.
          type: str
  sample:
    fleets:
      fleet1:
        id: 6a2a6d5c-9b1f-4e0a-8c7d-1f2e3d4c5b6a
        as_of: 2026-11-24 19:30:05 UTC
        as_of_epoch: 1795548605000
        member_count: 2
        coordinators:
          - array1
        members:
          array1:
            status: joined
            status_details: ''
            coordinator: true
            source: both
            as_of: 2026-11-24 19:02:11 UTC
            as_of_epoch: 1795546931000
            member_id: 0c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f
            array_id: 2b3c4d5e-6f70-4a1b-9c2d-3e4f5a6b7c8d
            fqdn: array1.example.com
            os: Purity//FA
            model: FA-X70R4
            version: 6.5.0
            topology_group: null
            reported_fleet: fleet1
          array2:
            status: joined
            status_details: ''
            coordinator: false
            source: members
            as_of: 2026-11-24 19:02:11 UTC
            as_of_epoch: 1795546931000
            member_id: 3c4d5e6f-7081-4b2c-8d3e-4f5a6b7c8d9e
            array_id: 3c4d5e6f-7081-4b2c-8d3e-4f5a6b7c8d9e
            fqdn: array2.example.com
            os: Purity//FB
            model: FlashBlade//S200
            version: 4.5.0
            topology_group: site1
            reported_fleet: null
    mismatches:
      - array: array2
        kind: fleet
        rows:
          - fleet1
        array_record: null
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.fleets import (
    pure1_fleet_map,
)
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
)

# The client methods the module needs, each with the first py-pure-client
# release that has it. The newest comes first, so that the message names the
# release that has them all.
REQUIRED_METHODS = (
    ("get_fleets_members", "1.99.0"),
    ("get_topology_groups", "1.99.0"),
    ("get_topology_groups_members", "1.99.0"),
    ("get_fleets", "1.93.0"),
)


def list_arrays(module, pure_1):
    """Return every array of Pure1 by its name, as the SDK models

    An array without a name, and one with the name of an array before it,
    cannot be filed and is counted in one warning.
    """

    def all_arrays(**page):
        return pure_1.get_arrays(**page)

    listing = pure1_list(module, "get_arrays", all_arrays)
    if listing.truncated:
        module.warn(
            "Pure1 returned {0} of {1} arrays: the result may be incomplete.".format(
                len(listing.items),
                "an unknown number of" if listing.total is None else listing.total,
            )
        )
    arrays = {}
    left_out = 0
    for array in listing.items:
        name = pure1_attr(array, "name")
        if name is None or name in arrays:
            left_out += 1
        else:
            arrays[name] = array
    if left_out:
        module.warn(
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: {0}.".format(left_out)
        )
    return arrays


def select_fleets(module, fleets, names):
    """Return the names of the fleets a task names, as Pure1 spells them

    fleets are the names of the fleets Pure1 knows. A name is matched
    without regard to case, as Pure1 itself compares the names of fleets, and
    the spelling of the task wins over the same name in another case. A name
    that matches no fleet fails the task, and so does an empty one.
    """
    if not all(name.strip() for name in names):
        module.fail_json(msg="The name of a fleet must not be empty.")
    known = {}
    for fleet in fleets:
        known.setdefault(fleet.lower(), []).append(fleet)
    found = []
    unknown = []
    for name in names:
        matches = known.get(name.lower(), [])
        if name in matches:
            matches = [name]
        if len(matches) > 1:
            module.fail_json(
                msg="Pure1 knows more than one fleet whose name is {0} in another "
                "case: {1}. Give the name as Pure1 spells it.".format(
                    name, ", ".join(sorted(matches))
                )
            )
        if not matches and name not in unknown:
            unknown.append(name)
        found.extend(matches)
    if unknown:
        text = "Fleet {0} is" if len(unknown) == 1 else "Fleets {0} are"
        module.fail_json(msg=(text + " not known to Pure1.").format(", ".join(unknown)))
    return found


def generate_fleets_dict(module, pure_1):
    """Return the fleets with their members and the arrays whose sources differ"""
    selected = pure1_resolve_arrays(module, pure_1, module.params["array"])
    arrays = list_arrays(module, pure_1)
    # The one rule of the collection for the fleet and the group of an array
    fleet_map = pure1_fleet_map(module, pure_1, arrays=list(arrays.values()))
    fleets = fleet_map["fleets"]
    mismatches = fleet_map["mismatches"]
    reported = sorted(fleets)
    # None: every member of a reported fleet
    members_wanted = None
    if module.params["name"]:
        reported = select_fleets(module, reported, module.params["name"])
        # The members of the fleets, and the arrays whose record names one
        concerned = {
            array
            for fleet, entry in fleets.items()
            for array, member in entry["members"].items()
            if fleet in reported or member["reported_fleet"] in reported
        }
        # The arrays that are directly in a topology group of one of the fleets
        for fleet in reported:
            for group in fleet_map["topology_groups"].get(fleet, {}).values():
                concerned.update(group["arrays"])
        mismatches = [entry for entry in mismatches if entry["array"] in concerned]
    elif selected:
        members_wanted = [array["name"] for array in selected]
        reported = [
            fleet
            for fleet in reported
            if any(array in fleets[fleet]["members"] for array in members_wanted)
        ]
        mismatches = [entry for entry in mismatches if entry["array"] in members_wanted]
    fleets_info = {}
    for fleet in sorted(reported):
        members = {}
        for name, member in fleets[fleet]["members"].items():
            if members_wanted is not None and name not in members_wanted:
                continue
            # The array that has the name of the member, None when none has
            array = arrays.get(name)
            placement = fleet_map["arrays"].get(name, {})
            members[name] = dict(
                member,
                fqdn=pure1_attr(array, "fqdn"),
                os=pure1_attr(array, "os"),
                model=pure1_attr(array, "model"),
                version=pure1_attr(array, "version"),
                topology_group=placement.get("topology_group"),
            )
        fleets_info[fleet] = dict(
            id=fleets[fleet]["id"],
            as_of=fleets[fleet]["as_of"],
            as_of_epoch=fleets[fleet]["as_of_epoch"],
            member_count=len(fleets[fleet]["members"]),
            coordinators=fleets[fleet]["coordinators"],
            members=members,
        )
    if mismatches:
        module.warn(
            "Differences between the member rows of Pure1 and its array records "
            "about the fleet or the topology group of an array are listed under "
            "mismatches in the result: {0}.".format(len(mismatches))
        )
    return dict(fleets=fleets_info, mismatches=mismatches)


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="list", elements="str"),
            array=dict(type="list", elements="str"),
        )
    )
    module = AnsibleModule(
        argument_spec,
        mutually_exclusive=[["name", "array"]],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)
    for method, minimum in REQUIRED_METHODS:
        pure1_require(module, pure_1, method, minimum)

    module.exit_json(changed=False, pure1_fleets=generate_fleets_dict(module, pure_1))


if __name__ == "__main__":
    main()
