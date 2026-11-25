#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_controllers
version_added: '1.6.0'
short_description: Collect array controllers information from Pure1
description:
  - Collect the controllers of FlashArray and Cloud Block Store arrays from
    Pure1, each with its mode, status, type, model and Purity version, and a
    roll-up for each array that says whether it has a primary and a secondary
    controller that are both ready.
  - Use it to check the controller redundancy of many arrays in one task, for
    example before an upgrade.
  - Pure1 refreshes controller data often, and a row is usually less than a
    minute old. C(as_of) gives the time of each row, so that a playbook can
    refuse data that is too old.
options:
  array:
    description:
      - Names of the arrays to return the controllers of. Without it, or with
        an empty list, the controllers of every array are returned.
      - A name is matched without regard to case and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - Pure1 reports no controller for a FlashBlade. A named array without a
        controller is absent from the result, and the module gives one
        warning that names those arrays.
      - Naming arrays costs one more request to Pure1.
    type: list
    elements: str
seealso:
  - module: everpure.pure1.pure1_hardware
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the controllers of every array
  everpure.pure1.pure1_controllers:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: controllers

- name: Show the mode and status of every controller
  ansible.builtin.debug:
    msg: "{{ controllers['pure1_controllers']['controllers'] }}"

- name: Name the arrays that have no redundant pair of ready controllers
  ansible.builtin.debug:
    msg: >-
      {{ item.key }} primary {{ item.value.primary | default('none', true) }}
      secondary {{ item.value.secondary | default('none', true) }}
  loop: "{{ controllers['pure1_controllers']['arrays'] | dict2items }}"
  when: not item.value.redundant

- name: Collect the controllers of two arrays
  everpure.pure1.pure1_controllers:
    array:
      - array1
      - array2
  register: pair

- name: Check that both arrays are redundant before an upgrade
  ansible.builtin.assert:
    that:
      - pair['pure1_controllers']['arrays'] | length == 2
      - pair['pure1_controllers']['arrays'].values() | rejectattr('redundant') | list | length == 0
      # Pure1 refreshed the controllers of both arrays in the last ten minutes
      - pair['pure1_controllers']['arrays'].values() | map(attribute='as_of_epoch') | map('int') | min
        > (now().timestamp() * 1000) | int - 600000
    fail_msg: An array has no redundant pair of ready controllers, or its data is more than ten minutes old
"""

RETURN = r"""
pure1_controllers:
  description: The controllers that Pure1 reports and a roll-up for each array.
  returned: always
  type: dict
  contains:
    controllers:
      description:
        - The controllers of each array, as a dictionary keyed by array name.
        - The value for an array is a dictionary keyed by controller name,
          such as C(CT0), and the value for a controller is a dictionary with
          the keys listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when Pure1 reports no controller. An array without a
          controller, as every FlashBlade, has no key.
        - Where two arrays carry one name, the array whose controller Pure1
          returns first keeps the name. The controllers of the other one are
          left out, and the module gives a warning that counts the arrays
          left out.
      type: dict
      contains:
        id:
          description: The ID of the controller in Pure1.
          type: str
        mode:
          description:
            - Mode of the controller.
            - Pure1 names C(primary), C(secondary), C(offline) and
              C(not present).
          type: str
        status:
          description:
            - Status of the controller.
            - Pure1 names C(ready), C(not ready), C(updating) and C(unknown).
          type: str
        type:
          description:
            - Type of the controller.
            - Pure1 names C(array_controller) and C(shelf_controller).
          type: str
        model:
          description: Model of the controller.
          type: str
        version:
          description: Purity version that the controller runs.
          type: str
        as_of:
          description:
            - Time at which Pure1 last refreshed the controller, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - Time at which Pure1 last refreshed the controller, in
              milliseconds since the epoch.
          type: int
    arrays:
      description:
        - A roll-up of the controllers of each array, as a dictionary keyed by
          array name whose values are dictionaries with the keys listed here.
        - It has the same arrays as C(controllers).
        - Only the controllers of type C(array_controller) count in it.
      type: dict
      contains:
        id:
          description: The ID of the array in Pure1.
          type: str
        fqdn:
          description:
            - Fully qualified domain name of the array.
            - Null where Pure1 has none, as for a Cloud Block Store array.
          type: str
        as_of:
          description:
            - Time at which Pure1 last refreshed the controllers of the
              array, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
            - The older time when the controllers differ.
          type: str
        as_of_epoch:
          description:
            - Time at which Pure1 last refreshed the controllers of the
              array, in milliseconds since the epoch.
          type: int
        controller_count:
          description: Number of controllers of type C(array_controller) of the array.
          type: int
        primary:
          description:
            - Name of the controller whose mode is C(primary).
            - Null when no controller has that mode, and when more than one
              has it.
          type: str
        secondary:
          description:
            - Name of the controller whose mode is C(secondary).
            - Null when no controller has that mode, and when more than one
              has it.
          type: str
        all_ready:
          description:
            - Whether the status of every controller of the array is
              C(ready).
            - False when the array has no controller of type
              C(array_controller).
          type: bool
        redundant:
          description:
            - Whether the array has exactly one primary and exactly one
              secondary controller and the status of both is C(ready).
          type: bool
        versions:
          description:
            - The Purity versions that the controllers of the array run, each
              once, the lowest first.
            - More than one entry when the controllers report different
              versions, as can happen during an upgrade.
          type: list
          elements: str
  sample:
    controllers:
      array1:
        CT0:
          id: 2a6d4e2c-9d5b-3f6a-8a43-1f2b3c4d5e6f
          mode: primary
          status: ready
          type: array_controller
          model: FA-X70R4
          version: 6.5.0
          as_of: 2021-02-03 13:04:05 UTC
          as_of_epoch: 1612357445000
        CT1:
          id: 7b1c9f0a-4e2d-3a5b-9c8d-6f5e4d3c2b1a
          mode: secondary
          status: ready
          type: array_controller
          model: FA-X70R4
          version: 6.5.0
          as_of: 2021-02-03 13:04:05 UTC
          as_of_epoch: 1612357445000
    arrays:
      array1:
        id: 0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9
        fqdn: array1.example.com
        as_of: 2021-02-03 13:04:05 UTC
        as_of_epoch: 1612357445000
        controller_count: 2
        primary: CT0
        secondary: CT1
        all_ready: true
        redundant: true
        versions:
          - 6.5.0
"""


import re

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_is_one_of,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)

# The type of the controllers that the roll-up of an array counts
ARRAY_CONTROLLER = "array_controller"


def version_key(version):
    """Return the sort key of a version, which puts 6.9.2 before 6.10.7"""
    parts = re.split("([0-9]+)", version)
    # The numbers of the version are the parts with an odd index
    return [int(part) if index % 2 else part for index, part in enumerate(parts)]


def controller_array(controller):
    """Return the reference to the array of a controller, None without a named one"""
    for array in pure1_attr(controller, "arrays", []):
        if pure1_attr(array, "name") is not None:
            return array
    return None


def controller_row(controller):
    """Return what the result holds for a controller"""
    as_of = pure1_attr(controller, "as_of")
    return {
        "id": pure1_attr(controller, "id"),
        "mode": pure1_attr(controller, "mode"),
        "status": pure1_attr(controller, "status"),
        "type": pure1_attr(controller, "type"),
        "model": pure1_attr(controller, "model"),
        "version": pure1_attr(controller, "version"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
    }


def only_one(rows, mode):
    """Return the name of the controller in a mode, None unless exactly one is"""
    names = [name for name in rows if rows[name]["mode"] == mode]
    return names[0] if len(names) == 1 else None


def first_value(references, field):
    """Return a field of the first reference that has it, None when none has"""
    for reference in references:
        value = pure1_attr(reference, field)
        if value is not None:
            return value
    return None


def array_rollup(references, controllers):
    """Return the roll-up of an array

    references are the references to the array that its controllers hold and
    controllers the rows of its controllers by name.
    """
    rows = {
        name: row
        for name, row in controllers.items()
        if row["type"] == ARRAY_CONTROLLER
    }
    primary = only_one(rows, "primary")
    secondary = only_one(rows, "secondary")
    ready = [name for name in rows if rows[name]["status"] == "ready"]
    times = [
        row["as_of_epoch"] for row in rows.values() if row["as_of_epoch"] is not None
    ]
    as_of = min(times) if times else None
    versions = {row["version"] for row in rows.values() if row["version"] is not None}
    return {
        "id": first_value(references, "id"),
        "fqdn": first_value(references, "fqdn"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "controller_count": len(rows),
        "primary": primary,
        "secondary": secondary,
        "all_ready": bool(rows) and len(ready) == len(rows),
        "redundant": primary in ready and secondary in ready,
        "versions": sorted(versions, key=version_key),
    }


def generate_controllers_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    # Pure1 can know more than one array by a name: the name is wanted once
    names = list(dict.fromkeys(array["name"] for array in arrays)) if arrays else None
    controller_filter = filter_in("arrays.name", names)

    def list_controllers(**page):
        return pure_1.get_controllers(filter=controller_filter, **page)

    listing = pure1_list(module, "get_controllers", list_controllers)
    if listing.truncated:
        module.warn(
            "Pure1 returned {0} of {1} controllers: the result may be "
            "incomplete.".format(
                len(listing.items),
                "an unknown number of" if listing.total is None else listing.total,
            )
        )

    found = {}
    references = {}
    # The ID of the array that the result holds under a name
    owners = {}
    namesakes = set()
    unfiled = 0
    repeated = 0
    for controller in listing.items:
        reference = controller_array(controller)
        name = pure1_attr(controller, "name")
        if reference is None or name is None:
            unfiled += 1
            continue
        array = reference.name
        if arrays is not None and not pure1_is_one_of(arrays, reference):
            # Pure1 matches a name whatever its case: the controllers of an
            # array that is none of those the task names were not asked for
            continue
        array_id = pure1_attr(reference, "id")
        owner = owners.get(array)
        if owner is not None and array_id is not None and array_id != owner:
            # Another array of the same name. The result is keyed by name and
            # never puts the controllers of two arrays under one key.
            namesakes.add((array, array_id))
            continue
        if name in found.get(array, {}):
            repeated += 1
            continue
        found.setdefault(array, {})[name] = controller_row(controller)
        references.setdefault(array, []).append(reference)
        if array_id is not None:
            owners.setdefault(array, array_id)
    if unfiled:
        module.warn(
            "Controllers that Pure1 returned without a name or without an "
            "array are left out of the result: {0}.".format(unfiled)
        )
    if repeated:
        module.warn(
            "Controllers that Pure1 returned under the name of another "
            "controller of the same array are left out of the result: "
            "{0}.".format(repeated)
        )
    if namesakes:
        module.warn(
            "Arrays that Pure1 returned under the name of another array are "
            "left out of the result: {0}.".format(len(namesakes))
        )
    absent = [name for name in names or [] if name not in found]
    if absent:
        text = (
            "Pure1 reports no controller for array {0}: it is not in the result."
            if len(absent) == 1
            else "Pure1 reports no controller for arrays {0}: they are not in "
            "the result."
        )
        module.warn(text.format(", ".join(absent)))

    controllers = {}
    rollups = {}
    for array in sorted(found):
        controllers[array] = {name: found[array][name] for name in sorted(found[array])}
        rollups[array] = array_rollup(references[array], controllers[array])
    return {"controllers": controllers, "arrays": rollups}


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(array=dict(type="list", elements="str")))
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    module.exit_json(
        changed=False, pure1_controllers=generate_controllers_dict(module, pure_1)
    )


if __name__ == "__main__":
    main()
