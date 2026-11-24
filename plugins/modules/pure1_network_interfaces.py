#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_network_interfaces
version_added: '1.1.0'
deprecated:
  removed_in: '2.0.0'
  why: Replaced by more functional module
  alternative: Use M(everpure.pure1.pure1_nics) instead
short_description: Collect array network interface details from Pure1
description:
  - Collect array network interface details from a Pure1.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
options:
  name:
    description:
      - Name of appliance to obtain network interface details.
    type: str
    required: true
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect network interface details for array foo
  everpure.pure1.pure1_network_interfaces:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
    name: foo
"""

RETURN = r"""
network_info:
  description:
    - Returns information on appliance network port configurations
    - A dictionary with one key, the appliance name as given in I(name). Its
      value is a dictionary keyed by interface name, and the value for an
      interface is a dictionary with the keys listed here.
  returned: when the array has network interfaces
  type: dict
  contains:
    services:
      description:
        - Services that are enabled on the interface.
        - An empty list when none is enabled, null when Pure1 does not report the field.
      type: list
      elements: str
    enabled:
      description: Whether the interface is enabled, null when Pure1 does not say.
      type: bool
    gateway:
      description: Gateway of the interface, an empty string when Pure1 reports none.
      type: str
    mtu:
      description: Maximum transmission unit of the interface, an empty string when Pure1 reports none.
      type: int
    netmask:
      description: Netmask of the interface, an empty string when Pure1 reports none.
      type: str
    address:
      description: IP address of the interface, an empty string when Pure1 reports none.
      type: str
    subinterfaces:
      description:
        - Names of the subinterfaces of the interface.
        - An empty list when the interface has none, null when Pure1 does not report the field.
      type: list
      elements: str
    mac_address:
      description: Hardware address of the interface, an empty string when Pure1 reports none.
      type: str
    speed:
      description: Speed of the interface in whole gigabits per second, 0 when Pure1 reports none.
      type: int
  sample:
    foo:
      ct0.eth0:
        services:
          - management
        enabled: true
        gateway: 192.0.2.1
        mtu: 1500
        netmask: 255.255.255.0
        address: 192.0.2.10
        subinterfaces: []
        mac_address: "00:00:5e:00:53:10"
        speed: 1
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(name=dict(type="str", required=True)))

    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    network_info = {}

    array_filter = "arrays.name=" + filter_string(module.params["name"])
    interfaces = pure1_items(
        module,
        "get_network_interfaces",
        lambda: pure_1.get_network_interfaces(filter=array_filter),
    )
    if not interfaces:
        module.fail_json(
            msg="Failed to get network interfaces information. Check provided array name."
        )
    network_info[module.params["name"]] = {}

    left_out = 0
    for iface in range(0, len(interfaces)):
        port_name = getattr(interfaces[iface], "name", None)
        if port_name is None:
            left_out += 1
            continue
        network_info[module.params["name"]][port_name] = {
            "services": getattr(interfaces[iface], "services", None),
            "enabled": getattr(interfaces[iface], "enabled", None),
            "gateway": getattr(interfaces[iface], "gateway", ""),
            "mtu": getattr(interfaces[iface], "mtu", ""),
            "netmask": getattr(interfaces[iface], "netmask", ""),
            "address": getattr(interfaces[iface], "address", ""),
            "subinterfaces": getattr(interfaces[iface], "subinterfaces", None),
            "mac_address": getattr(interfaces[iface], "hwaddr", ""),
            "speed": round(getattr(interfaces[iface], "speed", 0) / 1000000000),
        }
    if left_out:
        module.warn(
            "Network interfaces that Pure1 returned without a name are left "
            "out of the result: {0}".format(left_out)
        )

    module.exit_json(changed=False, network_info=network_info)


if __name__ == "__main__":
    main()
