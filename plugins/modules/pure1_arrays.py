#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_arrays
version_added: '1.6.0'
short_description: Collect the arrays of the organization from Pure1
description:
  - Collect the arrays that Pure1 knows of the organization, keyed by array
    name, each with its ID, its fully qualified domain name, its product,
    model and operating system version, its fleet, its topology group, its
    tags and the age of its data.
  - Use it to select the arrays for a later task by name, product, model,
    version range, fleet, topology group or tag, for example the arrays that
    need an upgrade or the arrays of one site, and to look up the ID or the
    domain name of an array.
  - Every option narrows the result. An array is returned when it matches
    every option that is set, and without options every array is returned.
  - Pure1 keeps what an array last reported to it. C(as_of) of an array is the
    time of that data and C(age_seconds) its age when the module ran. Pure1
    refreshes the member rows of fleets and topology groups, which give
    C(fleet) and C(topology_group), about once an hour.
options:
  name:
    description:
      - Return only the arrays with these names.
      - A name is matched without regard to case, and the result has the name
        as Pure1 spells it.
      - A name that no array has is not an error. The result then has no key
        for it.
      - An empty list counts as not set, and the arrays are not narrowed by
        name.
    type: list
    elements: str
  type:
    description:
      - Return only the arrays of this product.
      - C(flasharray) is a FlashArray or a Cloud Block Store, whose operating
        system is C(Purity//FA) or C(Purity), C(flashblade) a FlashBlade
        (C(Purity//FB)) and C(objectengine) an ObjectEngine, which runs
        C(Elasticity).
    type: str
    choices: [ flasharray, flashblade, objectengine ]
  model:
    description:
      - Return only the arrays whose model contains this text, without regard
        to case. C(x90) selects C(FA-X90R3) and C(FA-X90R4).
      - It takes one text. An empty text counts as not set.
    type: str
  version_below:
    description:
      - Return only the arrays whose operating system version is lower than
        this version.
      - Pure1 compares the versions number by number, so C(6.9.2) is lower
        than C(6.10.0).
      - Requires I(type), because the versions of different products cannot
        be compared.
      - Quote the value. YAML reads an unquoted C(6.10) as the number 6.1.
      - The task fails when the value is empty. It also fails when Pure1
        cannot read it as a version, unless the other options select no
        array, because the versions are not compared then.
      - This option and I(version_at_least) cost one more request to Pure1
        between them.
    type: str
  version_at_least:
    description:
      - Return only the arrays whose operating system version is this
        version or a higher one.
      - Pure1 compares the versions number by number, so C(6.10.0) is higher
        than C(6.9.2).
      - Requires I(type), because the versions of different products cannot
        be compared.
      - Quote the value. YAML reads an unquoted C(6.10) as the number 6.1.
      - The task fails when the value is empty. It also fails when Pure1
        cannot read it as a version, unless the other options select no
        array, because the versions are not compared then.
      - With I(version_below) it selects a range of versions.
    type: str
  fleet:
    description:
      - Return only the arrays of this fleet. It takes one name.
      - The arrays of a fleet are the arrays that have a member row in the
        fleet, and the arrays without a member row in any fleet whose own
        record names the fleet. They are the arrays whose C(fleet) in the
        result is this fleet. An array that has member rows in more than one
        fleet is in the fleet that its own record names, else in one it has
        joined, else in the first by name.
      - The name is matched without regard to case.
      - The task fails when the name is empty, and when Pure1 does not know
        the fleet or knows more than one fleet of the name in another case
        and none as the task spells it.
      - A fleet without arrays gives an empty result.
    type: str
  topology_group:
    description:
      - Return only the arrays that are in this topology group or in a group
        below it. It takes one name.
      - The name is matched without regard to case.
      - A name that groups of several fleets have selects the arrays of each
        of these groups. Add I(fleet) to select the arrays of the group of
        one fleet.
      - An array that has member rows in more than one group counts for the
        group that C(topology_group) in the result names.
      - The task fails when the name is empty, and when Pure1 knows no
        topology group of the name. With I(fleet) it fails when that fleet
        has no group of the name and none of its arrays is in or below a
        group of the name.
      - A group without arrays gives an empty result.
    type: str
  tag:
    description:
      - Return only the arrays that have every one of these tags.
      - A tag is given as C(key:value), split at the first colon, or as
        C(key) alone, which selects the arrays that have a tag of this key
        with any value. C(key:) selects the arrays whose tag of this key has
        an empty value.
      - The key and the value are compared exactly, also in their case, and
        nothing is trimmed. The namespace of a tag does not count. A tag
        whose key holds a colon cannot be selected.
      - Quote a tag that holds a colon followed by a space. YAML reads it as
        a dictionary otherwise.
      - An empty list counts as not set, and the arrays are not narrowed by
        tag.
      - The task fails when a tag has no key.
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
    each with one request for every 1,000 objects, and the tags of the arrays
    with one request. I(version_below) and I(version_at_least) cost one more
    request between them. When the other options select no array, the
    versions are not compared and the tags are not read.
  - Pure1 says in two places which fleet and which topology group an array is
    in, the member rows and the record of the array itself, and the two can
    differ. The member rows count first, and the record of the array where no
    row names the array. C(fleet_source) of an array says which of the two
    the result has. The M(everpure.pure1.pure1_fleets) module lists the
    arrays for which they differ.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect every array
  everpure.pure1.pure1_arrays:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: everything

- name: Show the product, the model and the version of each array
  ansible.builtin.debug:
    msg: "{{ item.key }} is a {{ item.value.type }} {{ item.value.model }} on {{ item.value.version }}"
  loop: "{{ everything.pure1_arrays.arrays | dict2items }}"

- name: Look up two arrays by name
  everpure.pure1.pure1_arrays:
    name:
      - array1
      - array2
  register: named

- name: Check that Pure1 knows both arrays and has heard of them in the last hour
  ansible.builtin.assert:
    that:
      - named.pure1_arrays.arrays | length == 2
      - named.pure1_arrays.arrays | dict2items | selectattr('value.age_seconds', 'none') | list | length == 0
      - named.pure1_arrays.arrays | dict2items | selectattr('value.age_seconds', 'ge', 3600) | list | length == 0
    fail_msg: Pure1 does not know both arrays, or its data of one has no time or is older than an hour

- name: Collect the FlashArray//X90 arrays of one site of a fleet that run a release before 6.5.10
  everpure.pure1.pure1_arrays:
    type: flasharray
    model: X90
    version_below: "6.5.10"
    fleet: fleet1
    topology_group: site1
  register: outdated

- name: Stop when an array of the site still has to be upgraded
  ansible.builtin.assert:
    that:
      - outdated.pure1_arrays.arrays | length == 0
    fail_msg: "Upgrade these arrays first: {{ outdated.pure1_arrays.arrays | list | join(', ') }}"

- name: Collect the FlashBlades on a 4.5 release that are tagged as highly critical and have a team
  everpure.pure1.pure1_arrays:
    type: flashblade
    version_at_least: "4.5.0"
    version_below: "4.6.0"
    tag:
      - Criticality:High
      - Team
  register: tagged

- name: Show where to reach each of them
  ansible.builtin.debug:
    msg: "{{ item.key }} ({{ item.value.id }}) is at {{ item.value.fqdn }}"
  loop: "{{ tagged.pure1_arrays.arrays | dict2items }}"
  when: item.value.fqdn is not none
"""

RETURN = r"""
pure1_arrays:
  description: The arrays that Pure1 knows and the options select
  returned: always
  type: dict
  contains:
    arrays:
      description:
        - The arrays, as a dictionary keyed by array name, in the order of
          the names. The value for an array is a dictionary with the keys
          listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when the options select no array.
      type: dict
      contains:
        id:
          description: The ID of the array in Pure1.
          type: str
        fqdn:
          description:
            - The fully qualified domain name of the array.
            - Null where Pure1 has none, as for a Cloud Block Store.
          type: str
        os:
          description: The operating system of the array, for example C(Purity//FA).
          type: str
        type:
          description:
            - The product, one of C(flasharray), C(flashblade) and
              C(objectengine), as I(type) selects it.
            - Null for an operating system the module does not know.
          type: str
        model:
          description: The model of the array.
          type: str
        version:
          description: The version of the operating system of the array.
          type: str
        fleet:
          description:
            - The name of the fleet of the array, null for an array in no
              fleet.
          type: str
        fleet_source:
          description:
            - Where Pure1 says that the array is in the fleet.
            - C(both) when the array has a member row in the fleet and its
              own record names the fleet as well, C(members) when it has the
              member row only and C(arrays) when only its own record names
              the fleet.
            - Null for an array in no fleet.
          type: str
        topology_group:
          description:
            - The name of the topology group that the array is directly in,
              null for an array in no group.
            - It is the group of the member row the array has in a topology
              group, and where it has none the group that the record of the
              array names.
          type: str
        tags:
          description:
            - The tags of the array, in the order of key, namespace and
              value.
            - Empty for an array without tags.
          type: list
          elements: dict
          contains:
            key:
              description: The key of the tag.
              type: str
            value:
              description: The value of the tag.
              type: str
            namespace:
              description: The namespace of the tag, for example C(pure1).
              type: str
        as_of:
          description:
            - The time of the data Pure1 has of the array, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same time in milliseconds since the epoch, as Pure1 gives it.
          type: int
        age_seconds:
          description:
            - The age of that data in whole seconds when the module ran, by
              the clock of the host the module ran on, and never below 0.
          type: int
  sample:
    arrays:
      array1:
        id: 2b3c4d5e-6f70-4a1b-9c2d-3e4f5a6b7c8d
        fqdn: array1.example.com
        os: Purity//FA
        type: flasharray
        model: FA-X90R4
        version: 6.5.8
        fleet: fleet1
        fleet_source: both
        topology_group: site1
        tags:
          - key: Criticality
            value: High
            namespace: pure1
          - key: Team
            value: storage-team
            namespace: pure1
        as_of: "2026-11-24 19:30:05 UTC"
        as_of_epoch: 1795548605000
        age_seconds: 42
      array2:
        id: 3c4d5e6f-7081-4b2c-8d3e-4f5a6b7c8d9e
        fqdn: null
        os: Purity//FB
        type: flashblade
        model: FlashBlade//S200
        version: 4.5.6
        fleet: null
        fleet_source: null
        topology_group: null
        tags: []
        as_of: "2026-11-24 19:29:41 UTC"
        as_of_epoch: 1795548581000
        age_seconds: 66
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.fleets import (
    ARRAY_TYPES,
    pure1_array_record,
    pure1_fleet_map,
    pure1_tag_map,
)
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
)
import time

# The client methods the module needs, each with the first py-pure-client
# release that has it. The newest comes first, so that the message names the
# release that has them all.
REQUIRED_METHODS = (
    ("get_fleets_members", "1.99.0"),
    ("get_topology_groups", "1.99.0"),
    ("get_topology_groups_members", "1.99.0"),
    ("get_fleets", "1.93.0"),
)


def list_arrays(module, pure_1, array_filter=None, what="arrays"):
    """Return the arrays of Pure1 as SDK models, all of them or those of a filter"""

    def listed(**page):
        return pure_1.get_arrays(filter=array_filter, **page)

    listing = pure1_list(module, "get_arrays", listed)
    if listing.truncated:
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items),
                "an unknown number of" if listing.total is None else listing.total,
                what,
            )
        )
    return listing.items


def arrays_by_name(module, arrays):
    """Return the arrays by their name

    An array without a name, and one with the name of an array before it,
    cannot be filed and is counted in one warning.
    """
    by_name = {}
    left_out = 0
    for array in arrays:
        name = pure1_attr(array, "name")
        if name is None or name in by_name:
            left_out += 1
        else:
            by_name[name] = array
    if left_out:
        module.warn(
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: {0}.".format(left_out)
        )
    return by_name


def wanted_tags(module, tags):
    """Return the tags a task asks for as (key, value), value None for any value"""
    wanted = []
    for tag in tags or []:
        key, colon, value = tag.partition(":")
        if not key:
            module.fail_json(
                msg="A tag of the tag option must have a key: give it as key "
                "or as key:value."
            )
        pair = (key, value if colon else None)
        if pair not in wanted:
            wanted.append(pair)
    return wanted


def has_tags(tags, wanted):
    """Tell whether the tags of an array hold every tag that is asked for"""
    return all(
        any(tag["key"] == key and value in (None, tag["value"]) for tag in tags)
        for key, value in wanted
    )


def resolve_fleet(module, fleets, name):
    """Return the fleet of the task as Pure1 spells it, None when it names none"""
    if name is None:
        return None
    # The spelling of the task wins over the same name in another case
    found = [fleet for fleet in fleets if fleet == name] or [
        fleet for fleet in fleets if fleet.lower() == name.lower()
    ]
    if len(found) > 1:
        module.fail_json(
            msg="Pure1 knows more than one fleet whose name is {0} in another "
            "case: {1}. Give the name as Pure1 spells it.".format(
                name, ", ".join(found)
            )
        )
    if not found:
        module.fail_json(msg="Fleet {0} is not known to Pure1.".format(name))
    return found[0]


def arrays_in_group(module, membership, group, fleet):
    """Return the names of the arrays in a topology group or in a group below it

    group is the name the task gives. fleet is the fleet of the task as
    Pure1 spells it: only its groups and the groups of its arrays are looked
    at then. With None the groups of every fleet are. A name that no group
    has fails the task.
    """
    places = {}
    for key, entries in membership["topology_groups"].items():
        if fleet in (None, key):
            for name in entries:
                places[(key, name)] = []
    for array, placement in membership["arrays"].items():
        if placement["topology_group"] is None:
            continue
        if fleet is not None and placement["fleet"] != fleet:
            continue
        # A group that only the record of the array names has no path
        path = placement["topology_path"] or [placement["topology_group"]]
        for above in path:
            places.setdefault((placement["topology_fleet"], above), []).append(array)
    found = [place for place in places if place[1].lower() == group.lower()]
    if not found:
        text = "Topology group {0} is not known to Pure1"
        if fleet is not None:
            text += " in fleet {1}"
        module.fail_json(msg=(text + ".").format(group, fleet))
    return set(array for place in found for array in places[place])


def identity(array):
    """Return what tells an array from every other: its id, else its name"""
    array_id = pure1_attr(array, "id")
    if array_id is None:
        return ("name", pure1_attr(array, "name"))
    return ("id", array_id)


def arrays_in_version_range(module, pure_1, product, below, at_least):
    """Return the identity of each array of a product with a version in the range

    Pure1 compares the versions, as versions and not as text.
    """
    systems = [system for system, kind in ARRAY_TYPES.items() if kind == product]
    version_filter = filter_and(
        filter_in("os", systems),
        None if at_least is None else "version>=" + filter_string(at_least),
        None if below is None else "version<" + filter_string(below),
    )
    arrays = list_arrays(module, pure_1, version_filter, "arrays of the version range")
    return set(identity(array) for array in arrays)


def check_texts(module):
    """Fail the task for a name or a version that is given empty"""
    for option, what in (("fleet", "a fleet"), ("topology_group", "a topology group")):
        value = module.params[option]
        if value is not None and not value.strip():
            module.fail_json(msg="The name of {0} must not be empty.".format(what))
    for option in ("version_below", "version_at_least"):
        value = module.params[option]
        if value is not None and not value.strip():
            module.fail_json(msg="The value of {0} must not be empty.".format(option))


def generate_arrays_dict(module, pure_1):
    """Return the arrays that the options select, by name"""
    params = module.params
    tags_wanted = wanted_tags(module, params["tag"])
    check_texts(module)
    arrays = arrays_by_name(module, list_arrays(module, pure_1))
    # One time for the age of every array of the result, read when the arrays
    # are here: Pure1 stamps a fresh array while it answers
    now_ms = int(time.time() * 1000)
    # The one rule of the collection for the fleet and the group of an array.
    # It needs every array, so the options narrow the result here.
    membership = pure1_fleet_map(module, pure_1, arrays=list(arrays.values()))
    selected = sorted(arrays)
    if params["name"]:
        names = set(name.lower() for name in params["name"])
        selected = [name for name in selected if name.lower() in names]
    if params["type"]:
        selected = [
            name
            for name in selected
            if ARRAY_TYPES.get(pure1_attr(arrays[name], "os")) == params["type"]
        ]
    if params["model"]:
        text = params["model"].lower()
        selected = [
            name
            for name in selected
            if text in pure1_attr(arrays[name], "model", "").lower()
        ]
    fleet = resolve_fleet(module, membership["fleets"], params["fleet"])
    if fleet is not None:
        selected = [
            name for name in selected if membership["arrays"][name]["fleet"] == fleet
        ]
    if params["topology_group"] is not None:
        in_group = arrays_in_group(module, membership, params["topology_group"], fleet)
        selected = [name for name in selected if name in in_group]
    below, at_least = params["version_below"], params["version_at_least"]
    if selected and (below is not None or at_least is not None):
        in_range = arrays_in_version_range(
            module, pure_1, params["type"], below, at_least
        )
        selected = [name for name in selected if identity(arrays[name]) in in_range]
    # Nothing to attach the tags to when no array is left
    tags = pure1_tag_map(module, pure_1) if selected else {}
    result = {}
    for name in selected:
        record = pure1_array_record(arrays[name], tags, membership, now_ms)
        if has_tags(record["tags"], tags_wanted):
            # The name is the key of the array
            del record["name"]
            result[name] = record
    return dict(arrays=result)


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="list", elements="str"),
            type=dict(type="str", choices=["flasharray", "flashblade", "objectengine"]),
            model=dict(type="str"),
            version_below=dict(type="str"),
            version_at_least=dict(type="str"),
            fleet=dict(type="str"),
            topology_group=dict(type="str"),
            tag=dict(type="list", elements="str"),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_by=dict(version_below="type", version_at_least="type"),
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)
    for method, minimum in REQUIRED_METHODS:
        pure1_require(module, pure_1, method, minimum)

    module.exit_json(changed=False, pure1_arrays=generate_arrays_dict(module, pure_1))


if __name__ == "__main__":
    main()
