#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_alerts
version_added: '1.1.0'
short_description: Collect array alerts from Pure1 based on severity
description:
  - Collect array alerts based on severity. Select all appliances or by appliance
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
options:
  name:
    description:
      - Name of appliance to obtain alert details.
      - If not provided, all appliances will be used
      - The name is not looked up. A name that Pure1 does not know matches no alert.
    type: str
  severity:
    description: Severity of alerts to select
    type: str
    choices: [ info, warning, critical, hidden ]
    required: true
  state:
    description:
      - State of the alert
      - The choice C(closing) selects the alerts in the state of that name. Added in version 1.6.0.
    type: str
    default: open
    choices: [ open, closing, closed ]
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect critical alerts for array foo
  everpure.pure1.pure1_alerts:
    name: foo
    severity: critical
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: critical_alerts

- name: Show the code and the summary of each critical alert for array foo
  ansible.builtin.debug:
    msg: "{{ item.value.code }}: {{ item.value.summary }}"
  loop: "{{ critical_alerts.alert_info | dict2items }}"

- name: Collect the warning alerts in state closing for all appliances
  everpure.pure1.pure1_alerts:
    severity: warning
    state: closing
  register: closing_alerts

- name: Show the appliances that have such an alert
  ansible.builtin.debug:
    msg: "{{ closing_alerts.alert_info | dict2items | map(attribute='value.appliance_name') | unique }}"
"""

RETURN = r"""
alert_info:
  description:
    - The alerts that match, keyed by a running number that starts at 0.
      A registered result holds the numbers as strings.
    - An empty dictionary when no alert matches.
    - A value that Pure1 does not give is null, except for the times.
      A time that the alert does not have is left out, and so is its _epoch key.
  returned: always
  type: dict
  contains:
    component_type:
      description: Type of the component alerted about
      returned: always
      type: str
    component_name:
      description: Name of the component alerted about
      returned: always
      type: str
    code:
      description: Code associated with the alert
      returned: always
      type: int
    category:
      description: Category of the alert
      returned: always
      type: str
    summary:
      description: Summary of the alert
      returned: always
      type: str
    created:
      description: Time when the alert was created, in the time zone of the host that runs the module
      returned: when the alert has that time
      type: str
    created_epoch:
      description:
        - Time when the alert was created, in milliseconds since the UNIX epoch
        - Added in version 1.6.0.
      returned: when the alert has that time
      type: int
    updated:
      description: Time when the alert was last updated, in the time zone of the host that runs the module
      returned: when the alert has that time
      type: str
    updated_epoch:
      description:
        - Time when the alert was last updated, in milliseconds since the UNIX epoch
        - Added in version 1.6.0.
      returned: when the alert has that time
      type: int
    notified:
      description: Time when the user was notified of the alert, in the time zone of the host that runs the module
      returned: when the alert has that time
      type: str
    notified_epoch:
      description:
        - Time when the user was notified of the alert, in milliseconds since the UNIX epoch
        - Added in version 1.6.0.
      returned: when the alert has that time
      type: int
    closed:
      description: Time when the alert was closed, in the time zone of the host that runs the module
      returned: when the alert has that time
      type: str
    closed_epoch:
      description:
        - Time when the alert was closed, in milliseconds since the UNIX epoch
        - Added in version 1.6.0.
      returned: when the alert has that time
      type: int
    appliance_name:
      description: Name of the first array of the alert, null for an alert without an array
      returned: when name is not set
      type: str
  sample:
    "0":
      component_type: hardware
      component_name: ct0.fan0
      code: 42
      category: array
      summary: Fan failure
      created: "2026-08-21 16:41:39"
      created_epoch: 1787330499000
      updated: "2026-08-21 16:41:40"
      updated_epoch: 1787330500000
      notified: "2026-08-21 16:41:41"
      notified_epoch: 1787330501000
      closed: "2026-08-21 16:43:02"
      closed_epoch: 1787330582000
      appliance_name: array1
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)
import time


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str"),
            severity=dict(
                type="str",
                choices=["info", "warning", "critical", "hidden"],
                required=True,
            ),
            state=dict(
                default="open", type="str", choices=["open", "closing", "closed"]
            ),
        )
    )

    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)
    index = 0
    alert_info = {}
    alert_filter = (
        "severity="
        + filter_string(module.params["severity"])
        + " and state="
        + filter_string(module.params["state"])
    )
    if module.params["name"]:
        alert_filter = (
            "arrays.name="
            + filter_string(module.params["name"])
            + " and "
            + alert_filter
        )
    alerts = pure1_items(
        module, "get_alerts", lambda: pure_1.get_alerts(filter=alert_filter)
    )

    for alert in range(0, len(alerts)):
        alert_info[index] = {
            "component_type": getattr(alerts[alert], "component_type", None),
            "component_name": getattr(alerts[alert], "component_name", None),
            "code": getattr(alerts[alert], "code", None),
            "category": getattr(alerts[alert], "category", None),
            "summary": getattr(alerts[alert], "summary", None),
        }
        for field in ("created", "updated", "notified", "closed"):
            # milliseconds since the epoch, as Pure1 gives the time
            stamp = getattr(alerts[alert], field, 0)
            if stamp != 0:
                alert_info[index][field] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(int(stamp) / 1000)
                )
                alert_info[index][field + "_epoch"] = stamp
        if not module.params["name"]:
            arrays = getattr(alerts[alert], "arrays", None) or []
            alert_info[index]["appliance_name"] = (
                getattr(arrays[0], "name", None) if arrays else None
            )
        index += 1

    module.exit_json(changed=False, alert_info=alert_info)


if __name__ == "__main__":
    main()
