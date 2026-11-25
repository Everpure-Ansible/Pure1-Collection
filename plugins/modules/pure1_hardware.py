#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_hardware
version_added: '1.6.0'
short_description: Collect the hardware components of arrays from Pure1
description:
  - Collect the hardware components of the FlashArray, Cloud Block Store and
    FlashBlade arrays that Pure1 knows, such as chassis, controllers, shelves,
    drive bays, blades, power supplies, fans, temperature sensors and ports,
    each with its status, the details of a failure, model, serial number,
    slot, temperature and voltage.
  - The result also holds a summary of each array, the components that are
    not healthy and the components whose ID light is lit, for a hardware
    inventory or a daily report across all arrays.
  - Pure1 refreshes the hardware data of an array only a few times a day, so
    the result can be most of a day old. Use the module for an inventory and
    a daily report, not as a live check. The C(as_of) values tell how old the
    data is.
options:
  array:
    description:
      - Names of the arrays whose components are returned. All arrays when
        not set or empty.
      - A name is matched without regard to case, and the result carries the
        name as Pure1 spells it.
      - A name that Pure1 does not know fails the task.
      - Naming arrays costs one more request to Pure1.
      - A named array for which Pure1 has no hardware data is absent from the
        result and the module gives a warning. With I(type), I(status) or
        I(unhealthy_only) an array without a matching component is absent
        without a warning.
    type: list
    elements: str
  type:
    description:
      - Return only the components of these types.
      - A FlashArray has components of the types C(chassis), C(controller),
        C(storage_shelf), C(drive_bay), C(nvram_bay), C(power_supply),
        C(cooling), C(temp_sensor), C(eth_port) and C(fc_port), among others.
        A FlashBlade has C(chassis), C(controller), C(flash_blade), C(drive),
        C(power_supply), C(fan), C(eth_port) and C(mgmt_port), among others.
      - A type is matched without regard to case. A type that no component
        has matches nothing and is not an error.
      - An empty list counts as not set, and the components are not narrowed
        by type.
    type: list
    elements: str
  status:
    description:
      - Return only the components whose status is one of these words, for
        example C(critical) or C(unused).
      - A status is matched without regard to case. A status that no
        component has matches nothing and is not an error.
      - An empty list counts as not set, and the components are not narrowed
        by status.
      - Mutually exclusive with I(unhealthy_only). A task that gives both
        options fails, also when I(status) is an empty list or
        I(unhealthy_only) is C(false).
    type: list
    elements: str
  unhealthy_only:
    description:
      - Return only the components that are not healthy.
      - A component is healthy when its status is C(ok), C(healthy),
        C(not_installed), C(unused) or C(identifying). Any other status
        counts as not healthy, also one that Pure1 adds later. A component
        without a status is not counted as unhealthy.
      - Mutually exclusive with I(status). A task that gives both options
        fails, also when I(status) is an empty list or I(unhealthy_only) is
        C(false).
    type: bool
    default: false
  connectors:
    description:
      - Also read the connectors of the ports and return each one as
        C(connector) of its port. Pure1 has connectors for the Ethernet and
        the management ports of a FlashBlade.
      - This costs one more request to Pure1.
    type: bool
    default: false
seealso:
  - module: everpure.pure1.pure1_controllers
  - module: everpure.pure1.pure1_drives
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the hardware components of all arrays
  everpure.pure1.pure1_hardware:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: hardware

- name: Show the components that are not healthy
  ansible.builtin.debug:
    msg: "{{ hardware['pure1_hardware']['unhealthy'] }}"

- name: Show the arrays whose hardware data is older than a day
  ansible.builtin.debug:
    msg: "{{ item.key }} was last refreshed at {{ item.value.as_of }}"
  loop: "{{ hardware['pure1_hardware']['arrays'] | dict2items }}"
  when: >-
    item.value.as_of_epoch | default(0, true)
    < now().timestamp() * 1000 - 86400000

- name: Collect the components of two arrays that are not healthy
  everpure.pure1.pure1_hardware:
    array:
      - array1
      - array2
    unhealthy_only: true
  register: faults

- name: Check that every component of the two arrays is healthy
  ansible.builtin.assert:
    that:
      - faults['pure1_hardware']['unhealthy'] | length == 0
    fail_msg: "{{ faults['pure1_hardware']['unhealthy'] | length }} components need attention"

- name: Collect the power supplies and the fans of all arrays
  everpure.pure1.pure1_hardware:
    type:
      - power_supply
      - cooling
      - fan
  register: power

- name: Show the number of power supplies and fans of each array
  ansible.builtin.debug:
    msg: "{{ item.key }} has {{ item.value.components }}"
  loop: "{{ power['pure1_hardware']['arrays'] | dict2items }}"

- name: Collect the empty slots and bays of one array
  everpure.pure1.pure1_hardware:
    array:
      - array1
    status:
      - unused
      - not_installed
  register: empty

- name: Show the number of empty slots and bays
  ansible.builtin.debug:
    msg: "{{ empty['pure1_hardware']['hardware'].get('array1', {}) | length }}"

- name: Collect the Ethernet ports of a FlashBlade with their connectors
  everpure.pure1.pure1_hardware:
    array:
      - array2
    type:
      - eth_port
    connectors: true
  register: ports

- name: Show the transceiver of each port that has a connector
  ansible.builtin.debug:
    msg: "{{ item.key }} {{ item.value.connector.transceiver_type }}"
  loop: "{{ ports['pure1_hardware']['hardware'].get('array2', {}) | dict2items }}"
  when: item.value.connector is not none
"""

RETURN = r"""
pure1_hardware:
  description:
    - The hardware components collected from Pure1.
    - Every part describes the components that the task selected. With
      I(array), I(type), I(status) or I(unhealthy_only) the summaries and the
      lists cover those components only.
  returned: always
  type: dict
  contains:
    hardware:
      description:
        - The components of each array, as a dictionary keyed by the name of
          the array as Pure1 spells it.
        - The value for an array is a dictionary keyed by the name of the
          component, for example C(CT0), C(CH0.BAY1) or C(CH1.FB3). The value
          for a component is a dictionary with the keys listed here.
        - Arrays and components are in the order of their names, numbers
          ordered as numbers, so C(CH0.BAY2) comes before C(CH0.BAY10).
        - Where two arrays carry one name, the array that Pure1 returns first
          keeps the name. The components of the other one are left out, and
          the module gives a warning that counts them.
        - A key is null when Pure1 reports no value for it.
        - Empty when no component matches.
      type: dict
      contains:
        id:
          description:
            - ID of the component in Pure1.
            - Null where Pure1 has none, as for most components of a
              FlashArray and for an empty slot.
          type: str
        type:
          description: Type of the component, for example C(controller) or C(drive_bay).
          type: str
        status:
          description:
            - Status of the component as Pure1 reports it.
            - A FlashArray reports C(ok) and C(not_installed) where a
              FlashBlade reports C(healthy) and C(unused).
          type: str
        details:
          description: What is wrong with a component that is not healthy, when Pure1 says so.
          type: str
        model:
          description: Model of the component.
          type: str
        serial:
          description: Serial number of the component.
          type: str
        slot:
          description: Number of the slot that the component is in.
          type: int
        speed:
          description:
            - Speed of a port in bits per second, as Pure1 sends it.
            - A FlashArray reports the speed at which a port runs, and 0 for
              a port that is not in use.
            - A FlashBlade reports what an Ethernet port is capable of, also
              for a port that is not in use, and 0 for a management port.
          type: int
        temperature:
          description: Temperature of the component in degrees Celsius.
          type: int
        voltage:
          description: Voltage of the component in volts.
          type: int
        identify_enabled:
          description: Whether the ID light of the component is lit.
          type: bool
        as_of:
          description:
            - Time at which Pure1 last refreshed the data of the component,
              in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same time in milliseconds since the epoch.
          type: int
        connector:
          description:
            - The connector of the port.
            - Null for a component that has no connector in Pure1.
          returned: when I(connectors) is true
          type: dict
          contains:
            connector_type:
              description: Form factor of the connector, for example C(QSFP) or C(RJ-45).
              type: str
            lane_speed:
              description:
                - Configured speed of each lane of the connector in bits per
                  second, as Pure1 sends it.
              type: int
            port_count:
              description: Configured number of ports of the connector.
              type: int
            transceiver_type:
              description:
                - Type of the transceiver in the connector, for example
                  C(100GBASE-CR4), or C(UNKNOWN).
                - Null for a connector that takes no transceiver.
              type: str
    arrays:
      description:
        - A summary of each array, as a dictionary keyed by the name of the
          array as Pure1 spells it. The value for an array is a dictionary
          with the keys listed here.
        - Empty when no component matches.
      type: dict
      contains:
        id:
          description: ID of the array in Pure1.
          type: str
        fqdn:
          description:
            - Fully qualified domain name of the array.
            - Null where Pure1 has none, as for a Cloud Block Store.
          type: str
        components:
          description: Number of components of the array in C(hardware).
          type: int
        by_status:
          description:
            - Number of those components for each status, as a dictionary
              keyed by the status, for example C(ok) and C(critical).
            - A component without a status is not counted here.
          type: dict
        as_of:
          description:
            - Time of the oldest data among those components, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same time in milliseconds since the epoch.
          type: int
    unhealthy:
      description:
        - The components that are not healthy, in the order of C(hardware).
          A component is listed when it has a status that is none of C(ok),
          C(healthy), C(not_installed), C(unused) and C(identifying).
        - Empty when every component is healthy.
      type: list
      elements: dict
      contains:
        array:
          description: Name of the array of the component.
          type: str
        name:
          description: Name of the component.
          type: str
        type:
          description: Type of the component.
          type: str
        status:
          description: Status of the component.
          type: str
        details:
          description: What is wrong with the component, when Pure1 says so.
          type: str
        model:
          description: Model of the component.
          type: str
        serial:
          description: Serial number of the component.
          type: str
    identifying:
      description:
        - The components whose ID light is lit, in the order of C(hardware).
          These are the components with C(identify_enabled) true and those
          whose status is C(identifying).
        - Empty when no ID light is lit.
      type: list
      elements: dict
      contains:
        array:
          description: Name of the array of the component.
          type: str
        name:
          description: Name of the component.
          type: str
        type:
          description: Type of the component.
          type: str
        status:
          description: Status of the component.
          type: str
  sample:
    hardware:
      array1:
        CH0.BAY1:
          id: 5d8ac90f-9a1e-3ab4-b1e7-6a1a7e0b5c11
          type: drive_bay
          status: ok
          details: null
          model: DFSC1-0000
          serial: SN0000000001
          slot: null
          speed: null
          temperature: null
          voltage: null
          identify_enabled: false
          as_of: 2026-11-24 06:10:15 UTC
          as_of_epoch: 1795500615000
        CT0:
          id: 0f3c2c53-5a3b-3a0e-9d2d-2c7e5a7c9e01
          type: controller
          status: ok
          details: null
          model: FA-X70R4
          serial: SN0000000002
          slot: null
          speed: null
          temperature: null
          voltage: null
          identify_enabled: false
          as_of: 2026-11-24 06:10:15 UTC
          as_of_epoch: 1795500615000
        CT0.ETH4:
          id: null
          type: eth_port
          status: ok
          details: null
          model: null
          serial: null
          slot: 0
          speed: 25000000000
          temperature: null
          voltage: null
          identify_enabled: false
          as_of: 2026-11-24 06:10:15 UTC
          as_of_epoch: 1795500615000
      array2:
        CH1.FB3:
          id: 3b0a6c1e-7f1d-3c55-8a0b-4d2f6e8a9c02
          type: flash_blade
          status: critical
          details: The blade has failed and must be replaced.
          model: FB-17TB
          serial: SN0000000003
          slot: 3
          speed: null
          temperature: 31
          voltage: null
          identify_enabled: true
          as_of: 2026-11-24 01:45:00 UTC
          as_of_epoch: 1795484700000
    arrays:
      array1:
        id: 2a6d0e4b-1c3f-4b5a-9e7d-8f0a1b2c3d04
        fqdn: array1.example.com
        components: 3
        by_status:
          ok: 3
        as_of: 2026-11-24 06:10:15 UTC
        as_of_epoch: 1795500615000
      array2:
        id: 7c9e1f3a-5b2d-4c6e-8a0f-1d3b5c7e9f05
        fqdn: array2.example.com
        components: 1
        by_status:
          critical: 1
        as_of: 2026-11-24 01:45:00 UTC
        as_of_epoch: 1795484700000
    unhealthy:
      - array: array2
        name: CH1.FB3
        type: flash_blade
        status: critical
        details: The blade has failed and must be replaced.
        model: FB-17TB
        serial: SN0000000003
    identifying:
      - array: array2
        name: CH1.FB3
        type: flash_blade
        status: critical
"""


import re

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_is_one_of,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)

# The status of a component that needs no attention. A FlashArray reports ok
# and not_installed where a FlashBlade reports healthy and unused. Any other
# status counts as unhealthy, also one that Pure1 adds later.
HEALTHY = ("ok", "healthy", "not_installed", "unused", "identifying")


def name_order(name):
    """Return the sort key of a name that puts CH0.BAY2 before CH0.BAY10"""
    parts = re.split(r"(\d+)", name.lower())
    # The parts at the odd places are the numbers
    return [int(part) if place % 2 else part for place, part in enumerate(parts)], name


def warn_if_truncated(module, listing, kind):
    """Warn when Pure1 sent fewer items than it counted"""
    if listing.truncated:
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items),
                "an unknown number of" if listing.total is None else listing.total,
                kind,
            )
        )


def file_by_array(module, items, kind, owners, arrays):
    """Return the items as {array name: {item name: item}}

    Pure1 sorts no listing of hardware, most items have no id and two
    requests can return the same item, so the items are filed by the names
    that identify them. An item without a name or without an array cannot be
    filed, and of two items of an array with the same name the first is
    kept. Two arrays can carry the same name, and the result has one place
    for a name: owners holds, as {array name: array id}, the array whose
    items are filed under a name, which is the first one seen, and the items
    of another array of that name are left out. The listings of one task
    share owners, so that all of them keep the same array. Each of the three
    gives one warning that counts what was left out.

    arrays are the arrays of the task as pure1_resolve_arrays() returned
    them, None for a task that names none. Pure1 matches the name of an
    array whatever its case, so the listing of a task that names array1 also
    holds the items of an array ARRAY1: an item of an array that is none of
    those of the task was not asked for and is left out without a warning.
    """
    filed = {}
    nameless = 0
    namesakes = 0
    repeated = 0
    for item in items:
        references = pure1_attr(item, "arrays", [])
        reference = references[0] if references else None
        array = pure1_attr(reference, "name")
        array_id = pure1_attr(reference, "id")
        name = pure1_attr(item, "name")
        if array is None or name is None:
            nameless += 1
            continue
        if arrays is not None and not pure1_is_one_of(arrays, reference):
            # The array of the item is none of those the task names
            continue
        if array_id is not None and owners.setdefault(array, array_id) != array_id:
            # Only two ids that differ show another array
            namesakes += 1
        elif name in filed.setdefault(array, {}):
            repeated += 1
        else:
            filed[array][name] = item
    if nameless:
        module.warn(
            "{0} that Pure1 returned without a name or without an array are "
            "left out of the result: {1}.".format(kind.capitalize(), nameless)
        )
    if namesakes:
        module.warn(
            "{0} of arrays that carry the name of another array are left out "
            "of the result: {1}.".format(kind.capitalize(), namesakes)
        )
    if repeated:
        module.warn(
            "{0} that Pure1 returned with the name of another one of the same "
            "array are left out of the result: {1}.".format(kind.capitalize(), repeated)
        )
    return filed


def component_row(item):
    """Return what the result holds for a hardware component"""
    as_of = pure1_attr(item, "as_of")
    return dict(
        id=pure1_attr(item, "id"),
        type=pure1_attr(item, "type"),
        status=pure1_attr(item, "status"),
        details=pure1_attr(item, "details"),
        model=pure1_attr(item, "model"),
        serial=pure1_attr(item, "serial"),
        slot=pure1_attr(item, "slot"),
        speed=pure1_attr(item, "speed"),
        temperature=pure1_attr(item, "temperature"),
        voltage=pure1_attr(item, "voltage"),
        identify_enabled=pure1_attr(item, "identify_enabled"),
        as_of=pure1_time(as_of),
        as_of_epoch=as_of,
    )


def connector_row(item):
    """Return what the result holds for the connector of a port, None for none"""
    if item is None:
        return None
    return dict(
        connector_type=pure1_attr(item, "connector_type"),
        lane_speed=pure1_attr(item, "lane_speed"),
        port_count=pure1_attr(item, "port_count"),
        transceiver_type=pure1_attr(item, "transceiver_type"),
    )


def is_unhealthy(row):
    """Tell whether a component has a status and it is not a healthy one"""
    return row["status"] is not None and str(row["status"]).lower() not in HEALTHY


def is_identifying(row):
    """Tell whether the ID light of a component is lit"""
    return bool(row["identify_enabled"]) or (
        str(row["status"]).lower() == "identifying"
    )


def absent_arrays(module, hardware):
    """Return the arrays the task names that have no component in the result

    The names are those of the task, each array once.
    """
    found = set(name.lower() for name in hardware)
    absent = []
    for name in module.params["array"] or []:
        if name.lower() not in found:
            found.add(name.lower())
            absent.append(name)
    return absent


def generate_hardware_dict(module, pure_1):
    params = module.params
    arrays = pure1_resolve_arrays(module, pure_1, params["array"])
    names = [array["name"] for array in arrays] if arrays else None
    hardware_filter = filter_and(
        filter_in("arrays.name", names),
        filter_in("type", params["type"]),
        filter_in("status", params["status"]),
        (
            "not({0})".format(filter_in("status", HEALTHY))
            if params["unhealthy_only"]
            else None
        ),
    )
    # Not sorted: most hardware of a FlashArray has no id and Pure1 refuses a
    # sort on arrays, so no sort orders every item, and pages read by offset
    # would repeat some items and lose others
    listing = pure1_list(
        module,
        "get_hardware",
        lambda **page: pure_1.get_hardware(filter=hardware_filter, **page),
    )
    warn_if_truncated(module, listing, "hardware components")
    owners = {}
    filed = file_by_array(module, listing.items, "hardware components", owners, arrays)

    connectors = {}
    if params["connectors"] and filed:
        connector_filter = filter_in("arrays.name", names)
        listing = pure1_list(
            module,
            "get_hardware_connectors",
            lambda **page: pure_1.get_hardware_connectors(
                filter=connector_filter, **page
            ),
        )
        warn_if_truncated(module, listing, "hardware connectors")
        connectors = file_by_array(
            module, listing.items, "hardware connectors", owners, arrays
        )

    hardware = {}
    summary = {}
    unhealthy = []
    identifying = []
    for array in sorted(filed, key=name_order):
        components = {}
        by_status = {}
        oldest = None
        reference = None
        for name in sorted(filed[array], key=name_order):
            item = filed[array][name]
            row = component_row(item)
            if params["unhealthy_only"] and not is_unhealthy(row):
                # Whatever Pure1 makes of the filter, the rule is the module's
                continue
            if params["connectors"]:
                row["connector"] = connector_row(connectors.get(array, {}).get(name))
            components[name] = row
            if reference is None:
                reference = pure1_attr(item, "arrays")[0]
            if row["status"] is not None:
                by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            if row["as_of_epoch"] is not None:
                if oldest is None or row["as_of_epoch"] < oldest:
                    oldest = row["as_of_epoch"]
            if is_unhealthy(row):
                unhealthy.append(
                    dict(
                        array=array,
                        name=name,
                        type=row["type"],
                        status=row["status"],
                        details=row["details"],
                        model=row["model"],
                        serial=row["serial"],
                    )
                )
            if is_identifying(row):
                identifying.append(
                    dict(array=array, name=name, type=row["type"], status=row["status"])
                )
        if not components:
            continue
        hardware[array] = components
        summary[array] = dict(
            id=pure1_attr(reference, "id"),
            fqdn=pure1_attr(reference, "fqdn"),
            components=len(components),
            by_status=dict(sorted(by_status.items())),
            as_of=pure1_time(oldest),
            as_of_epoch=oldest,
        )

    if not (params["type"] or params["status"] or params["unhealthy_only"]):
        absent = absent_arrays(module, hardware)
        if absent:
            module.warn(
                "Pure1 returned no hardware component for {0} {1}, which "
                "{2} absent from the result.".format(
                    "array" if len(absent) == 1 else "arrays",
                    ", ".join(absent),
                    "is" if len(absent) == 1 else "are",
                )
            )
    return dict(
        hardware=hardware, arrays=summary, unhealthy=unhealthy, identifying=identifying
    )


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            type=dict(type="list", elements="str"),
            status=dict(type="list", elements="str"),
            unhealthy_only=dict(type="bool", default=False),
            connectors=dict(type="bool", default=False),
        )
    )
    module = AnsibleModule(
        argument_spec,
        mutually_exclusive=[["status", "unhealthy_only"]],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)

    module.exit_json(
        changed=False, pure1_hardware=generate_hardware_dict(module, pure_1)
    )


if __name__ == "__main__":
    main()
