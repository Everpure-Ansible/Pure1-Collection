#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_security
version_added: '1.6.0'
short_description: Collect the security assessment of arrays from Pure1
description:
  - Collect the security assessment that Pure1 makes of each array. It has
    the security insights of the array, such as an unsafe password
    configuration, a Purity version at its end of life or CVEs that were
    detected, the IDs of those CVEs by severity, and the security anomalies
    that Pure1 found on the array.
  - Use it for a security gate before a change, for a regular report, or to
    start a runbook when Pure1 reports an anomaly.
    M(everpure.pure1.pure1_cves) returns the details of the CVEs.
  - Pure1 works the insights out periodically, so they can be some hours old.
    C(as_of) of an insight is the time Pure1 generated it.
options:
  array:
    description:
      - Names of the arrays to return the security assessment of. Every array
        that Pure1 reports an insight or an anomaly for is returned when it
        is not set or empty.
      - A name is matched without regard to case, and the result has the
        name as Pure1 spells it.
      - The task fails when Pure1 does not know one of the names.
      - Every array named here is in the result, with empty lists when Pure1
        reports nothing for it.
      - Pure1 cannot select anomalies by array. The module reads the
        anomalies of all arrays and keeps those of the arrays named here.
    type: list
    elements: str
  gather_subset:
    description:
      - What to collect. C(insights) are the security insights of the arrays
        and the CVEs they carry, C(anomalies) are the security anomalies, and
        C(all) is both. An empty list is C(all) as well.
      - What is not collected costs no request, and its lists in the result
        are empty. Without the insights C(summary.insight_counts) is empty
        and C(summary.arrays_with_insights) is null, and without the
        anomalies C(summary.open_anomalies) is null.
    type: list
    elements: str
    choices: [all, insights, anomalies]
    default: [all]
  insight_types:
    description:
      - Return only the insights of these types. The insights of every type
        are returned when it is not set or empty.
      - The CVEs of an array are read from its returned insights. C(cves) of
        an array is empty unless the type of the wanted severity, such as
        C(critical_cve_detected), is among the types given here.
      - It selects insights only and has no effect on the anomalies.
    type: list
    elements: str
    choices:
      - appliance_not_phoning_home
      - end_of_life_purity_version_in_use
      - public_access_risk
      - purity_optimizations_available
      - unsafe_password_configuration
      - remote_assist_longer_than_eight_hours
      - remote_assist_longer_than_one_day
      - open_critical_alerts_detected
      - open_anomalies_detected
      - data_protection_needs_review
      - ntp_server_not_enabled
      - remote_access_control_not_enabled
      - critical_cve_detected
      - high_cve_detected
      - medium_cve_detected
      - low_cve_detected
  anomaly_status:
    description:
      - Return the anomalies that are C(open), those that are C(closed), or
        C(all) of them.
      - Pure1 calls an anomaly open while its end is less than one week old,
        and moves the end of an anomaly forward while it goes on. So open
        does not mean that nobody has dealt with the anomaly, and closed does
        not mean that somebody has. An anomaly closes by itself one week
        after it ended.
    type: str
    choices: [open, closed, all]
    default: open
notes:
  - This module requires py-pure-client 1.91.0 or later, the first release
    with the C(get_assessment_security_insights_arrays) and
    C(get_assessment_security_anomalies) methods of Pure1 REST 1.5.
  - The module makes one request to Pure1 for every 1,000 insights and one
    for every 1,000 anomalies, whatever the number of arrays. I(array) costs
    one more request.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the security assessment of all arrays
  everpure.pure1.pure1_security:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: security

- name: Show the insight types of each array
  ansible.builtin.debug:
    msg: "{{ item.key }} has {{ item.value.insight_types }}"
  loop: "{{ security['pure1_security']['arrays'] | dict2items }}"

- name: Check that Pure1 reports no open anomaly
  ansible.builtin.assert:
    that:
      - security['pure1_security']['summary']['open_anomalies'] == 0
    fail_msg: Pure1 reports open security anomalies

- name: Collect the password and CVE insights of two arrays
  everpure.pure1.pure1_security:
    array:
      - array1
      - array2
    gather_subset:
      - insights
    insight_types:
      - unsafe_password_configuration
      - critical_cve_detected
      - high_cve_detected
  register: posture

# every array that the task names is in the result
- name: Check that neither array has an unsafe password or a critical or high CVE
  ansible.builtin.assert:
    that:
      - item.value.insight_types | length == 0
    fail_msg: >-
      {{ item.key }} has {{ item.value.insight_types }} with the CVEs
      {{ item.value.cves.critical + item.value.cves.high }}
  loop: "{{ posture['pure1_security']['arrays'] | dict2items }}"

- name: Collect the anomalies of one array, also those that are closed
  everpure.pure1.pure1_security:
    array:
      - array1
    gather_subset:
      - anomalies
    anomaly_status: all
  register: history

- name: Show the anomalies of the array
  ansible.builtin.debug:
    msg: "{{ item.type }} from {{ item.start }} to {{ item.end }} is {{ item.status }}"
  loop: "{{ history['pure1_security']['arrays']['array1']['anomalies'] }}"
"""

RETURN = r"""
pure1_security:
  description: Returns the security assessment collected from Pure1
  returned: always
  type: dict
  contains:
    arrays:
      description:
        - The security assessment of each array, as a dictionary keyed by
          array name. The value for an array is a dictionary with the keys
          listed here.
        - Without I(array) an array is present when Pure1 reports an insight
          or an anomaly for it that the options select, and the dictionary is
          empty when it reports none. With I(array) every named array is
          present, with empty lists when nothing is reported for it.
        - Pure1 can know two arrays by one name. The array that is read first
          then keeps the name, what Pure1 reports for the other one is left
          out and the module gives a warning. C(id) tells which array it is.
      type: dict
      contains:
        id:
          description: The globally unique Pure1 ID of the array.
          type: str
        fqdn:
          description:
            - The fully qualified domain name of the array. Null when Pure1
              has none for the array, as for a Cloud Block Store array.
          type: str
        insight_types:
          description:
            - The types of the returned insights of the array, each once, in
              alphabetical order.
          type: list
          elements: str
        insights:
          description:
            - The security insights of the array, in the order of their type
              and for one type the oldest first. Empty when insights are not
              collected.
          type: list
          elements: dict
          contains:
            type:
              description:
                - The type of the insight, one of the values of
                  I(insight_types) or a type that Pure1 has added since.
              type: str
            as_of:
              description: Time Pure1 generated the insight, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            as_of_epoch:
              description: Time Pure1 generated the insight, in milliseconds since the epoch.
              type: int
        cves:
          description:
            - The IDs of the CVEs that the returned insights of the array
              carry, by severity. Each list is in the order of year and
              number, and empty when no returned insight carries a CVE of the
              severity.
            - M(everpure.pure1.pure1_cves) returns the details of a CVE.
          type: dict
          contains:
            critical:
              description: The IDs of the CVEs of critical severity.
              type: list
              elements: str
            high:
              description: The IDs of the CVEs of high severity.
              type: list
              elements: str
            medium:
              description: The IDs of the CVEs of medium severity.
              type: list
              elements: str
            low:
              description: The IDs of the CVEs of low severity.
              type: list
              elements: str
        anomalies:
          description:
            - The security anomalies that Pure1 found on the array, the one
              that started first at the top. Empty when anomalies are not
              collected.
            - An anomaly that Pure1 found on several arrays is listed under
              each of them, or with I(array) under each of them that is named
              there, every time with all its resources.
          type: list
          elements: dict
          contains:
            id:
              description: The globally unique Pure1 ID of the anomaly.
              type: str
            type:
              description:
                - The category of the anomaly, for example C(capacity),
                  C(volume_snapshot_count) or C(failed_and_unusual_logins).
              type: str
            status:
              description:
                - Either C(open) or C(closed). An anomaly is open while its
                  end is less than one week old.
              type: str
            description:
              description: What an anomaly of this type is, in the words of Pure1.
              type: str
            start:
              description: Time the anomaly started, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            start_epoch:
              description: Time the anomaly started, in milliseconds since the epoch.
              type: int
            end:
              description:
                - Time the anomaly ended, as C(YYYY-MM-DD HH:MM:SS UTC). Pure1
                  moves it forward while the anomaly goes on.
              type: str
            end_epoch:
              description: Time the anomaly ended, in milliseconds since the epoch.
              type: int
            resources:
              description:
                - The objects that Pure1 found the anomaly on, in the order
                  Pure1 lists them.
              type: list
              elements: dict
              contains:
                name:
                  description: Name of the object.
                  type: str
                resource_type:
                  description:
                    - The kind of object, one of C(arrays), C(volumes),
                      C(file-systems), C(buckets), C(directories) and
                      C(admins).
                  type: str
                arrays:
                  description: Names of the arrays the object is on.
                  type: list
                  elements: str
                metrics:
                  description: The metrics of the object that made Pure1 report the anomaly.
                  type: list
                  elements: dict
                  contains:
                    name:
                      description: Name of the metric, for example C(array_snapshot_count).
                      type: str
                    value_change_percentage:
                      description:
                        - The change of the metric that Pure1 took for an
                          anomaly, as a fraction. C(-0.25) is a fall by 25
                          percent.
                        - Null for a metric that has no such value, as the
                          metrics of administrator activity and of logins.
                      type: float
    summary:
      description: Counts over the arrays of the result.
      type: dict
      contains:
        insight_counts:
          description:
            - The number of returned insights of each type, as a dictionary
              keyed by insight type. A type without a returned insight is no
              key of it, and it is empty when no insight is returned.
          type: dict
        arrays_with_insights:
          description:
            - The number of arrays with at least one returned insight. Null
              when insights are not collected.
          type: int
        open_anomalies:
          description:
            - The number of returned anomalies whose status is C(open), each
              counted once on however many arrays it is. An anomaly that is
              left out of the result with a warning is not counted.
            - Null when the task does not read the open anomalies, which is
              when anomalies are not collected or I(anomaly_status) is
              C(closed).
          type: int
  sample:
    arrays:
      array1:
        id: 2a7f5c1e-8d3b-4f6a-9c0e-1b2d3f4a5b6c
        fqdn: array1.example.com
        insight_types:
          - medium_cve_detected
          - open_anomalies_detected
          - unsafe_password_configuration
        insights:
          - type: medium_cve_detected
            as_of: "2026-01-02 03:04:05 UTC"
            as_of_epoch: 1767323045000
          - type: open_anomalies_detected
            as_of: "2026-01-02 03:04:05 UTC"
            as_of_epoch: 1767323045000
          - type: unsafe_password_configuration
            as_of: "2026-01-02 03:04:05 UTC"
            as_of_epoch: 1767323045000
        cves:
          critical: []
          high: []
          medium:
            - CVE-2023-2650
            - CVE-2023-48795
          low: []
        anomalies:
          - id: 7c9e6679-7425-40de-944b-e07fc1f90ae7
            type: volume_snapshot_count
            status: open
            description: An unusual change in the number of volume snapshots of an array.
            start: "2025-12-20 00:00:00 UTC"
            start_epoch: 1766188800000
            end: "2026-01-01 00:00:00 UTC"
            end_epoch: 1767225600000
            resources:
              - name: array1
                resource_type: arrays
                arrays:
                  - array1
                metrics:
                  - name: array_snapshot_count
                    value_change_percentage: -0.25
      array2:
        id: 0f8fad5b-d9cb-469f-a165-70867728950e
        fqdn: null
        insight_types: []
        insights: []
        cves:
          critical: []
          high: []
          medium: []
          low: []
        anomalies: []
    summary:
      insight_counts:
        medium_cve_detected: 1
        open_anomalies_detected: 1
        unsafe_password_configuration: 1
      arrays_with_insights: 1
      open_anomalies: 1
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
    pure1_time,
)
from ansible_collections.everpure.pure1.plugins.module_utils.security import (
    CVE_SEVERITIES,
    INSIGHT_TYPES,
    pure1_security_insights,
)


def empty_entry(array_id, fqdn):
    """Return the entry of an array for which no insight is returned"""
    return dict(
        id=array_id,
        fqdn=fqdn,
        insight_types=[],
        insights=[],
        cves={severity: [] for severity in CVE_SEVERITIES},
    )


def anomaly_row(anomaly):
    """Return an anomaly as the result has it"""
    start = pure1_attr(anomaly, "start_time")
    end = pure1_attr(anomaly, "end_time")
    resources = []
    for resource in pure1_attr(anomaly, "resources", []):
        if resource is None:
            continue
        on_arrays = []
        for array in pure1_attr(resource, "arrays", []):
            name = pure1_attr(array, "name")
            if name is not None and name not in on_arrays:
                on_arrays.append(name)
        metrics = [
            dict(
                name=pure1_attr(metric, "name"),
                # The SDK keeps this one value of the data of a metric
                value_change_percentage=pure1_attr(
                    metric, "additional_data.actual_instance.value_change_percentage"
                ),
            )
            for metric in pure1_attr(resource, "metrics", [])
            if metric is not None
        ]
        resources.append(
            dict(
                name=pure1_attr(resource, "name"),
                resource_type=pure1_attr(resource, "resource_type"),
                arrays=on_arrays,
                metrics=metrics,
            )
        )
    return dict(
        id=pure1_attr(anomaly, "id"),
        type=pure1_attr(anomaly, "type"),
        status=pure1_attr(anomaly, "status"),
        description=pure1_attr(anomaly, "description"),
        start=pure1_time(start),
        start_epoch=start,
        end=pure1_time(end),
        end_epoch=end,
        resources=resources,
    )


def anomaly_arrays(anomaly):
    """Return the arrays of the resources of an anomaly, as (id, name, fqdn)

    Only those that have a name, and an array once for each resource on it.
    """
    found = []
    for resource in pure1_attr(anomaly, "resources", []):
        for array in pure1_attr(resource, "arrays", []):
            name = pure1_attr(array, "name")
            if name is not None:
                found.append((pure1_attr(array, "id"), name, pure1_attr(array, "fqdn")))
    return found


def named_array(arrays, array_id, name):
    """Return the array of the task that a reference of Pure1 is to, or None

    arrays is what pure1_resolve_arrays() returned. The ID decides where both
    have one: Pure1 can know another array by the same name.
    """
    for array in arrays:
        if array["id"] is not None and array_id is not None:
            if array["id"] == array_id:
                return array
        elif array["name"] == name:
            return array
    return None


def anomaly_order(row):
    """Return what orders the anomalies of an array: the one that started first"""
    start = row["start_epoch"]
    return (-1 if start is None else start, row["id"] or "")


def add_anomalies(module, pure_1, arrays, entries):
    """Give the entry of each array its anomalies and return how many are open

    entries are the entries of the result by array name. An array that has
    none yet gets one, unless the task names the arrays. An anomaly is
    counted once on however many arrays it is.
    """
    status = module.params["anomaly_status"]
    anomaly_filter = None if status == "all" else filter_in("status", status)

    def list_anomalies(**page):
        return pure_1.get_assessment_security_anomalies(filter=anomaly_filter, **page)

    listing = pure1_list(module, "get_assessment_security_anomalies", list_anomalies)
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} security anomalies: the result may be "
            "incomplete.".format(len(listing.items), total)
        )

    seen = set()
    unfiled = 0
    other_array = 0
    open_anomalies = 0
    for anomaly in listing.items:
        row = anomaly_row(anomaly)
        if row["id"] is not None:
            # A listing that moved while it was read can give an anomaly twice
            if row["id"] in seen:
                continue
            seen.add(row["id"])
        on_arrays = anomaly_arrays(anomaly)
        if not on_arrays:
            unfiled += 1
            continue
        filed = []
        refused = []
        for array_id, name, fqdn in on_arrays:
            if arrays is not None:
                array = named_array(arrays, array_id, name)
                if array is None:
                    continue
                name = array["name"]
            if name in filed:
                # Another resource of the anomaly is on the same array
                continue
            if name not in entries:
                entries[name] = dict(empty_entry(array_id, fqdn), anomalies=[])
            entry = entries[name]
            if None not in (entry["id"], array_id) and entry["id"] != array_id:
                # Another array of the same name has the entry
                refused.append(name)
                continue
            # A reference can lack what another reference to the array has
            if entry["id"] is None:
                entry["id"] = array_id
            if entry["fqdn"] is None:
                entry["fqdn"] = fqdn
            entry["anomalies"].append(row)
            filed.append(name)
        other_array += len(set(refused) - set(filed))
        if filed and row["status"] == "open":
            open_anomalies += 1
    for entry in entries.values():
        entry["anomalies"].sort(key=anomaly_order)
    if unfiled:
        module.warn(
            "Security anomalies that Pure1 returned without the name of an "
            "array are left out of the result: {0}.".format(unfiled)
        )
    if other_array:
        module.warn(
            "Security anomalies of an array that has the name of another array "
            "are left out of the result: {0}.".format(other_array)
        )
    return open_anomalies


def generate_security_dict(module, pure_1):
    subsets = module.params["gather_subset"] or ["all"]
    with_insights = "all" in subsets or "insights" in subsets
    with_anomalies = "all" in subsets or "anomalies" in subsets
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])

    entries = {}
    if with_insights:
        entries = pure1_security_insights(
            module, pure_1, arrays=arrays, types=module.params["insight_types"]
        )["arrays"]
    # Every array the task names has its entry, whatever is collected
    for array in arrays or []:
        if array["name"] not in entries:
            entries[array["name"]] = empty_entry(array["id"], array["fqdn"])
    for entry in entries.values():
        entry["anomalies"] = []

    open_anomalies = None
    if with_anomalies:
        open_anomalies = add_anomalies(module, pure_1, arrays, entries)
        if module.params["anomaly_status"] == "closed":
            # No open anomaly was asked for: none in the result says nothing
            open_anomalies = None

    insight_counts = {}
    for entry in entries.values():
        for insight in entry["insights"]:
            kind = insight["type"]
            insight_counts[kind] = insight_counts.get(kind, 0) + 1
    arrays_with_insights = None
    if with_insights:
        arrays_with_insights = len(
            [entry for entry in entries.values() if entry["insights"]]
        )
    return dict(
        arrays={name: entries[name] for name in sorted(entries)},
        summary=dict(
            insight_counts={
                kind: insight_counts[kind] for kind in sorted(insight_counts)
            },
            arrays_with_insights=arrays_with_insights,
            open_anomalies=open_anomalies,
        ),
    )


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            gather_subset=dict(
                type="list",
                elements="str",
                choices=["all", "insights", "anomalies"],
                default=["all"],
            ),
            insight_types=dict(
                type="list", elements="str", choices=list(INSIGHT_TYPES)
            ),
            anomaly_status=dict(
                type="str", choices=["open", "closed", "all"], default="open"
            ),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)
    pure1_require(module, pure_1, "get_assessment_security_insights_arrays", "1.91.0")
    pure1_require(module, pure_1, "get_assessment_security_anomalies", "1.91.0")

    security = generate_security_dict(module, pure_1)

    module.exit_json(changed=False, pure1_security=security)


if __name__ == "__main__":
    main()
