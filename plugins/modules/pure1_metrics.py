#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_metrics
version_added: '1.6.0'
short_description: Collect metric data of arrays and their objects from Pure1
description:
  - Collect the latest value, or the whole history, of any Pure1 metric for
    arrays, volumes, pods, file systems, managed directories, buckets, object
    store accounts or subscription licenses, and list the metrics Pure1 has
    for each of them.
  - Capacity, used space, data reduction and performance exist in Pure1 as
    metrics only, so this is the module for a capacity or chargeback report,
    for a check of load or latency before a placement, and for the usage of a
    subscription license over time.
  - Pure1 publishes data points with a delay. The newest point of a
    performance metric can be more than an hour old, and a capacity metric
    has one point a day.
  - M(everpure.pure1.pure1_volume_metrics) is the simpler module for the
    latest metric values of volumes, keyed by volume serial number.
options:
  resource_type:
    description:
      - The kind of resource to collect metrics for, as the Pure1 API spells
        it. C(directories) are the managed directories of FlashArray file
        systems.
    type: str
    required: true
    choices:
      - arrays
      - volumes
      - pods
      - file-systems
      - directories
      - buckets
      - object-store-accounts
      - subscription-licenses
  metrics:
    description:
      - Names of the metrics to collect. Required unless I(list_available) is
        true, and an empty list counts as not set.
      - The task fails when a name is not a metric of I(resource_type), and
        the message lists the names that are. I(list_available) returns them
        with their units and resolutions.
      - The C(pod_array_) metrics have one series for each array of a pod.
        They are metrics of I(resource_type=pods) only, although Pure1 lists
        them for arrays as well.
    type: list
    elements: str
  name:
    description:
      - Names of the resources to collect metrics for. Every resource of
        I(resource_type) is selected when neither I(name) nor I(ids) is set
        or when they are empty, on the arrays of I(array) if that is set.
      - A name is matched without regard to case, and it selects every
        resource of that name, which can be one on each of several arrays.
      - Give the whole name as Pure1 has it. A volume in a pod is named
        C(pod1::vol1) and a managed directory C(fs1:dir1).
      - The task fails when a name matches no resource.
      - All names of a task go into one request to Pure1, which holds about
        200 names of 20 characters and fewer when they are longer. With more
        the task fails with the message that the request is too long. Select
        with I(array), or use several tasks.
    type: list
    elements: str
  ids:
    description:
      - Pure1 IDs of the resources to collect metrics for, as the result of
        this module and the C(id) of the other modules of the collection
        give them. An empty list counts as not set.
      - The task fails when Pure1 has no resource of I(resource_type) with
        one of the IDs.
      - All IDs of a task go into one request to Pure1, which holds about
        130 of them. With more the task fails with the message that the
        request is too long, which speaks of names. Select with I(array),
        or use several tasks.
    type: list
    elements: str
  array:
    description:
      - Names of the arrays to collect the metrics of the resources of. The
        resources of all arrays are selected when it is not set or empty.
      - With I(resource_type=arrays) it selects these arrays themselves. A
        pod is selected when one of its arrays is named.
      - A name is matched without regard to case, and the result has the
        name as Pure1 spells it.
      - The task fails when Pure1 does not know one of the names.
      - Not valid with I(resource_type=subscription-licenses).
    type: list
    elements: str
  aggregation:
    description:
      - How Pure1 reduces the samples of each period of I(resolution) to one
        data point, to their average or to their maximum.
    type: str
    choices: [ avg, max ]
    default: avg
  resolution:
    description:
      - The time between two data points, in milliseconds.
      - When it is not set it is C(86400000), one day, and C(3600000), one
        hour, with I(resource_type=object-store-accounts), where a FlashBlade
        has no daily points.
      - One value applies to every metric of the task. Pure1 refuses a value
        finer than the finest resolution it stores one of the metrics at,
        which I(list_available) returns, and accepts a coarser one.
    type: int
  window:
    description:
      - How far back from now to collect data points, in seconds. The default
        is one day. It cannot be set together with I(start_time) and
        I(end_time), which take its place.
      - With the default I(resolution) of one day this asks for one point for
        each metric, which covers the last 24 hours.
      - Pure1 publishes data points with a delay, so a window can hold no
        point yet. The values are then null. A daily capacity metric can
        have no point for the last day, and a window of C(259200), three
        days, then returns the last daily point Pure1 has.
    type: int
    default: 86400
  start_time:
    description:
      - Start of the time range to collect data points of, in milliseconds
        since the epoch. Requires I(end_time).
    type: int
  end_time:
    description:
      - End of the time range to collect data points of, in milliseconds
        since the epoch. Requires I(start_time).
    type: int
  history:
    description:
      - Also return every data point of the time range, as C(data) of each
        metric. Without it only the last point is returned.
      - A long range at a fine resolution is a large result. 30 days at
        30000 milliseconds are 86,400 points for each metric of each
        resource.
    type: bool
    default: false
  list_available:
    description:
      - Return the metrics Pure1 has for I(resource_type) as
        C(available_metrics), and no metric data. No other option than
        I(resource_type) is used then.
    type: bool
    default: false
  max_series:
    description:
      - The highest number of series, resources times metrics, that the task
        may collect. The task fails before it collects any when the selection
        needs more. C(0) switches the check off.
      - It guards a task without I(name), I(ids) and I(array) against a
        resource type that has thousands of resources. Pure1 returns 32
        series for each request.
      - A task without I(name) and I(ids) lists no more resources than it
        takes to see that they are too many.
    type: int
    default: 3200
notes:
  - The module makes one request to Pure1 for the list of metrics, one for
    every 1,000 resources it has to list, one more when I(array) is set, and
    one for every 32 series. With I(resource_type=arrays) and I(array) the
    arrays are not listed a second time.
  - Pure1 can list a metric and have no data for it. Capacity metrics that do
    not apply to a product, and metrics that need a later Purity release than
    the array runs, come back without a point, and their values are null.
seealso:
  - module: everpure.pure1.pure1_volume_metrics
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: List the metrics Pure1 has for arrays
  everpure.pure1.pure1_metrics:
    resource_type: arrays
    list_available: true
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: catalog

- name: Show the name and the unit of each array metric
  ansible.builtin.debug:
    msg: "{{ item.name }} [{{ item.unit }}]"
  loop: "{{ catalog['pure1_metrics']['available_metrics'] }}"

# Pure1 can have no daily point yet for the last day: three days hold the last one
- name: Collect the size and the data reduction of every array
  everpure.pure1.pure1_metrics:
    resource_type: arrays
    metrics:
      - array_total_capacity
      - array_data_reduction
    window: 259200
  register: capacity

# The value is null for an array that Pure1 has no data point for
- name: Show the size of each array in bytes
  ansible.builtin.debug:
    msg: "{{ item.name }} has {{ item.metrics.array_total_capacity.value }} bytes"
  loop: "{{ capacity['pure1_metrics']['resources'].values() | list }}"
  when: item.metrics.array_total_capacity.value is not none

- name: Collect the highest load of two arrays in the last day
  everpure.pure1.pure1_metrics:
    resource_type: arrays
    array:
      - array1
      - array2
    metrics:
      - array_total_load
    aggregation: max
  register: load

# The load is a fraction of 1, and null when Pure1 has no data point in the time range
- name: Check that neither array was loaded above 80 percent
  ansible.builtin.assert:
    that:
      - load['pure1_metrics']['resources'].values()
        | map(attribute='metrics.array_total_load.value') | select('number')
        | select('gt', 0.8) | list | length == 0

- name: Collect the hourly read latency of two volumes of an array over a week
  everpure.pure1.pure1_metrics:
    resource_type: volumes
    array:
      - array1
    name:
      - vol1
      - vol2
    metrics:
      - volume_read_latency_us
    resolution: 3600000
    window: 604800
    history: true
  register: latency

- name: Show how many hourly points Pure1 has for each volume
  ansible.builtin.debug:
    msg: "{{ item.name }} has {{ item.metrics.volume_read_latency_us.data | length }} points"
  loop: "{{ latency['pure1_metrics']['resources'].values() | list }}"

- name: Collect the latest hourly write latency of a stretched pod on each of its arrays
  everpure.pure1.pure1_metrics:
    resource_type: pods
    name:
      - pod1
    metrics:
      - pod_array_write_latency_us
    resolution: 3600000
  register: pod_latency

- name: Show the latency of the pod on each of its arrays
  ansible.builtin.debug:
    msg: "{{ item.name }}: {{ item.metrics.pod_array_write_latency_us.arrays }}"
  loop: "{{ pod_latency['pure1_metrics']['resources'].values() | list }}"

- name: Collect the daily on-demand usage of every subscription license over 30 days
  everpure.pure1.pure1_metrics:
    resource_type: subscription-licenses
    metrics:
      - subscription_license_on_demand_space
    start_time: "{{ ((now().timestamp() | int) - 30 * 86400) * 1000 }}"
    end_time: "{{ (now().timestamp() | int) * 1000 }}"
    history: true
  register: usage

- name: Show the days each subscription license has usage for
  ansible.builtin.debug:
    msg: "{{ item.name }}: {{ item.metrics.subscription_license_on_demand_space.data | length }} days"
  loop: "{{ usage['pure1_metrics']['resources'].values() | list }}"
"""

RETURN = r"""
pure1_metrics:
  description: Returns the metric data collected from Pure1
  returned: always
  type: dict
  contains:
    available_metrics:
      description:
        - The metrics Pure1 has for I(resource_type), sorted by name. Empty
          unless I(list_available) is true.
        - A key is null when Pure1 reports no value for it.
      type: list
      elements: dict
      contains:
        name:
          description: The name of the metric, as I(metrics) takes it.
          type: str
        unit:
          description: The unit of the values, for example C(B), C(B/s), C(op/s) or C(us/op). Empty for a ratio.
          type: str
        description:
          description: What the metric measures.
          type: str
        resource_types:
          description:
            - The resource types of the metric. A C(pod_array_) metric has C(pods) and C(arrays).
          type: list
          elements: str
        availabilities:
          description: The resolutions Pure1 stores the metric at.
          type: list
          elements: dict
          contains:
            resolution:
              description: A resolution the metric is stored at, in milliseconds. It is the finest I(resolution) of its retention.
              type: int
            retention:
              description: How long Pure1 keeps the data at this resolution, in milliseconds.
              type: int
            aggregations:
              description: The values of I(aggregation) that Pure1 has at this resolution.
              type: list
              elements: str
    resources:
      description:
        - The selected resources, as a dictionary keyed by Pure1 resource ID.
          The value for a resource is a dictionary with the keys listed here.
        - Empty when I(list_available) is true and when nothing is selected.
      type: dict
      contains:
        name:
          description: The name of the resource, as Pure1 spells it.
          type: str
        resource_type:
          description: The value of I(resource_type).
          type: str
        serial:
          description: The serial number of the volume.
          returned: when I(resource_type) is C(volumes)
          type: str
        arrays:
          description:
            - The arrays the resource is on. A stretched pod has two.
          returned: unless I(resource_type) is C(arrays) or C(subscription-licenses)
          type: list
          elements: dict
          contains:
            id:
              description: The Pure1 ID of the array.
              type: str
            name:
              description: The name of the array.
              type: str
            fqdn:
              description: The fully qualified domain name of the array.
              type: str
        metrics:
          description:
            - The metrics of the resource, as a dictionary keyed by metric
              name. It has every name of I(metrics), and the value for a
              metric is a dictionary with the keys listed here.
          type: dict
          contains:
            unit:
              description: The unit of the values. Null when Pure1 gives none.
              type: str
            aggregation:
              description: The aggregation of the data points, C(avg) or C(max).
              type: str
            resolution:
              description: The time between two data points, in milliseconds.
              type: int
            timestamp:
              description:
                - Time of the last data point in the time range, as C(YYYY-MM-DD HH:MM:SS UTC). Pure1 stamps
                  a point with the start of the period it covers. For a daily point that is the start of
                  the time range plus whole days for some series and 00:00 UTC for others.
                - Null when Pure1 has no data point in the time range, and for a C(pod_array_) metric,
                  whose points are in C(arrays).
              type: str
            timestamp_epoch:
              description: Time of the last data point, in milliseconds since the epoch. Null as C(timestamp) is.
              type: int
            value:
              description:
                - Value of the last data point, in the unit of the metric, as Pure1 sends it. A whole
                  number can arrive as an integer. Null as C(timestamp) is.
              type: float
            data:
              description:
                - Every data point of the time range, the oldest first, each a list of the time in
                  milliseconds since the epoch and the value, as Pure1 sends them.
                - Empty when Pure1 has no data point, and for a C(pod_array_) metric.
              returned: when I(history) is true
              type: list
              elements: list
            arrays:
              description:
                - The data of a C(pod_array_) metric for each array of the pod, as a dictionary keyed by
                  array name. The value for an array is a dictionary with the keys listed here.
                - An array that Pure1 has no series for is not a key.
              returned: for a C(pod_array_) metric
              type: dict
              contains:
                timestamp:
                  description: Time of the last data point, as C(YYYY-MM-DD HH:MM:SS UTC). Null when there is no point.
                  type: str
                timestamp_epoch:
                  description: Time of the last data point, in milliseconds since the epoch.
                  type: int
                value:
                  description: Value of the last data point, in the unit of the metric.
                  type: float
                data:
                  description: Every data point of the time range, as C(data) of a metric.
                  returned: when I(history) is true
                  type: list
                  elements: list
    skipped:
      description:
        - The IDs of resources that Pure1 listed and no longer knew when their data was read. They are
          not in C(resources). The module gives a warning with their number.
        - Only a task without I(name) and I(ids) skips a resource. With either of them the task fails.
      type: list
      elements: str
  sample:
    available_metrics: []
    resources:
      2a7f5c1e-8d3b-4f6a-9c0e-1b2d3f4a5b6c:
        name: vol1
        resource_type: volumes
        serial: 7B8A1E6F0C2D4E3A00011010
        arrays:
          - id: 7c9e6679-7425-40de-944b-e07fc1f90ae7
            name: array1
            fqdn: array1.example.com
        metrics:
          volume_physical_capacity:
            unit: B
            aggregation: avg
            resolution: 86400000
            timestamp: "2026-01-02 03:04:05 UTC"
            timestamp_epoch: 1767323045000
            value: 1099511627776.0
    skipped: []
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    Pure1RequestError,
    filter_and,
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_items,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)
import re
import time

# Pure1 gives at most this many series, metrics times resources, in one answer
SERIES_PER_REQUEST = 32
DAY = 86400000
HOUR = 3600000
DEFAULT_WINDOW = 86400
DEFAULT_MAX_SERIES = 3200
# One resource and several of each type, as the messages name them
KINDS = {
    "arrays": ("Array", "Arrays"),
    "volumes": ("Volume", "Volumes"),
    "pods": ("Pod", "Pods"),
    "file-systems": ("File system", "File systems"),
    "directories": ("Directory", "Directories"),
    "buckets": ("Bucket", "Buckets"),
    "object-store-accounts": ("Object store account", "Object store accounts"),
    "subscription-licenses": ("Subscription license", "Subscription licenses"),
}
# The types whose resources are on arrays and whose listing says on which
ON_ARRAYS = (
    "volumes",
    "pods",
    "file-systems",
    "directories",
    "buckets",
    "object-store-accounts",
)


def warn_if_truncated(module, listing, kind):
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items), total, kind
            )
        )


def unique(values):
    """Return the values in their order, each once"""
    kept = []
    for value in values or []:
        if value not in kept:
            kept.append(value)
    return kept


def metric_catalog(module, pure_1, resource_type):
    """Return the metrics that can be collected for a resource type, by name

    A metric of two resource types has one series for each array of a pod
    and is collected with the ID of the pod. Pure1 lists it for arrays as
    well and refuses the ID of an array for it, so it is a metric of pods
    only.
    """

    def list_metrics(**page):
        return pure_1.get_metrics(resource_types=[resource_type], **page)

    listing = pure1_list(module, "get_metrics", list_metrics)
    warn_if_truncated(module, listing, "metrics")
    catalog = {}
    left_out = 0
    for metric in listing.items:
        name = pure1_attr(metric, "name")
        if name is None:
            left_out += 1
            continue
        resource_types = pure1_attr(metric, "resource_types")
        if resource_types is not None:
            resource_types = list(resource_types)
            if len(resource_types) > 1 and resource_type != "pods":
                continue
        catalog[name] = {
            "name": name,
            "unit": pure1_attr(metric, "unit"),
            "description": pure1_attr(metric, "description"),
            "resource_types": resource_types,
            "availabilities": [
                {
                    "resolution": pure1_attr(availability, "resolution"),
                    "retention": pure1_attr(availability, "retention"),
                    "aggregations": pure1_attr(availability, "aggregations"),
                }
                for availability in pure1_attr(metric, "availabilities", [])
                if availability is not None
            ],
        }
    if left_out:
        module.warn(
            "Pure1 returned {0} metrics without a name: they are left out of "
            "the result.".format(left_out)
        )
    return catalog


def per_array(metric):
    """Tell whether a metric of the catalog has one series for each array of a pod"""
    return len(metric["resource_types"] or []) > 1


def requested_metrics(module, catalog, resource_type):
    """Return the metric names of the task, each once, or fail for an unknown one"""
    metrics = unique(module.params["metrics"])
    unknown = [name for name in metrics if name not in catalog]
    if unknown:
        module.fail_json(
            msg="{0} {1} of resource_type {2}. Valid names: {3}.".format(
                ", ".join(unknown),
                "is not a metric" if len(unknown) == 1 else "are not metrics",
                resource_type,
                ", ".join(sorted(catalog)),
            )
        )
    return metrics


def list_resources(module, pure_1, resource_type, resource_filter, limit=None):
    """Return the listing of a resource type: all of it, or its first limit resources"""
    requests = {
        "arrays": (
            "get_arrays",
            lambda **page: pure_1.get_arrays(filter=resource_filter, **page),
        ),
        "volumes": (
            "get_volumes",
            lambda **page: pure_1.get_volumes(filter=resource_filter, **page),
        ),
        "pods": (
            "get_pods",
            lambda **page: pure_1.get_pods(filter=resource_filter, **page),
        ),
        "file-systems": (
            "get_file_systems",
            lambda **page: pure_1.get_file_systems(filter=resource_filter, **page),
        ),
        "directories": (
            "get_directories",
            lambda **page: pure_1.get_directories(filter=resource_filter, **page),
        ),
        "buckets": (
            "get_buckets",
            lambda **page: pure_1.get_buckets(filter=resource_filter, **page),
        ),
        "object-store-accounts": (
            "get_object_store_accounts",
            lambda **page: pure_1.get_object_store_accounts(
                filter=resource_filter, **page
            ),
        ),
        "subscription-licenses": (
            "get_subscription_licenses",
            lambda **page: pure_1.get_subscription_licenses(
                filter=resource_filter, **page
            ),
        ),
    }
    action, request = requests[resource_type]
    return pure1_list(module, action, request, limit=limit)


def array_references(item):
    """Return the arrays of a listed resource as dictionaries"""
    references = []
    for array in pure1_attr(item, "arrays", []):
        reference = {
            "id": pure1_attr(array, "id"),
            "name": pure1_attr(array, "name"),
            "fqdn": pure1_attr(array, "fqdn"),
        }
        # A reference Pure1 left out arrives as an object with no field set
        if reference["id"] is not None or reference["name"] is not None:
            references.append(reference)
    return references


def on_selected_array(references, arrays):
    """Tell whether one of the arrays of a resource is an array of the task

    Arrays are compared by ID: the name filter of Pure1 ignores case, so it
    also returns the resources of an array whose name differs in case only.
    """
    for array in arrays:
        for reference in references:
            if array["id"] is None:
                if reference["name"] == array["name"]:
                    return True
            elif reference["id"] == array["id"]:
                return True
    return False


def fail_not_known(module, resource_type, missing, by_id):
    """Fail the task for the names or IDs of the task that select nothing"""
    one, many = KINDS[resource_type]
    subject = one if len(missing) == 1 else many
    if by_id:
        subject = one + (" ID" if len(missing) == 1 else " IDs")
    verb = "is" if len(missing) == 1 else "are"
    arrays = module.params["array"] or []
    if not arrays:
        where = "known to Pure1"
    elif resource_type == "arrays":
        where = "among the arrays named in array"
    else:
        where = "known to Pure1 on array{0} {1}".format(
            "" if len(arrays) == 1 else "s", ", ".join(arrays)
        )
    module.fail_json(
        msg="{0} {1} {2} not {3}.".format(subject, ", ".join(missing), verb, where)
    )


def listed_rows(items, resource_type, arrays):
    """Return the Pure1 ID and the row of the result of each listed resource

    With arrays, only of the resources that are on one of them.
    """
    found = []
    for item in items:
        row = {"name": pure1_attr(item, "name"), "resource_type": resource_type}
        if resource_type == "volumes":
            row["serial"] = pure1_attr(item, "serial")
        if resource_type in ON_ARRAYS:
            row["arrays"] = array_references(item)
            if arrays and not on_selected_array(row["arrays"], arrays):
                continue
        found.append((pure1_attr(item, "id"), row))
    return found


def fail_too_many(module, resource_type, resources, metrics):
    """Fail the task for more series than max_series allows"""
    module.fail_json(
        msg="The task needs {0} series ({1}: {2}, metrics: {3}), more than "
        "max_series allows ({4}): select fewer with name, ids or array, or "
        "raise max_series.".format(
            resources * metrics,
            KINDS[resource_type][1].lower(),
            resources,
            metrics,
            module.params["max_series"],
        )
    )


def select_resources(module, pure_1, resource_type, metrics):
    """Return the resources the task selects, as rows of the result by Pure1 ID

    metrics is the number of metrics of the task. The task fails when a name
    or an ID of the task selects nothing, and when a listing without name and
    ids has more resources than max_series allows for that many metrics.
    """
    names = unique(module.params["name"])
    ids = unique(module.params["ids"])
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    left_out = 0
    found = []
    if resource_type == "arrays" and arrays:
        # The arrays are known already
        found = [
            (array["id"], {"name": array["name"], "resource_type": resource_type})
            for array in arrays
        ]
    else:
        # The filter of the licenses compares the case of a name and of an
        # ID, which no other listing does: they are all listed
        resource_filter = None
        if resource_type != "subscription-licenses":
            resource_filter = filter_and(
                filter_in(
                    "arrays.name",
                    [array["name"] for array in arrays] if arrays else None,
                ),
                filter_in("name", names),
                filter_in("id", ids),
            )
        # Without name and ids the listing can be long: no more of it is read
        # than one resource above what max_series allows, which is enough to
        # refuse the task
        limit = None
        if module.params["max_series"] and not names and not ids:
            limit = module.params["max_series"] // metrics + 1
        listing = list_resources(module, pure_1, resource_type, resource_filter, limit)
        found = listed_rows(listing.items, resource_type, arrays)
        if limit is not None and listing.truncated and len(listing.items) >= limit:
            kept = set(row_id for row_id, row in found if row_id is not None)
            if len(kept) >= limit and listing.total is not None:
                fail_too_many(module, resource_type, listing.total, metrics)
            # Some of the first resources are left out, or Pure1 gave no
            # count: only the whole listing tells
            listing = list_resources(module, pure_1, resource_type, resource_filter)
            found = listed_rows(listing.items, resource_type, arrays)
        warn_if_truncated(module, listing, KINDS[resource_type][1].lower())
    wanted_names = set(name.lower() for name in names)
    wanted_ids = set(resource_id.lower() for resource_id in ids)
    resources = {}
    for resource_id, row in found:
        if resource_id is None:
            left_out += 1
        elif names and (row["name"] or "").lower() not in wanted_names:
            continue
        elif ids and resource_id.lower() not in wanted_ids:
            continue
        else:
            resources[resource_id] = row
    known_names = set((row["name"] or "").lower() for row in resources.values())
    known_ids = set(resource_id.lower() for resource_id in resources)
    missing = [name for name in names if name.lower() not in known_names]
    if missing:
        fail_not_known(module, resource_type, missing, False)
    missing = [
        resource_id for resource_id in ids if resource_id.lower() not in known_ids
    ]
    if missing:
        fail_not_known(module, resource_type, missing, True)
    if left_out:
        module.warn(
            "Pure1 returned {0} {1} without an ID: they are left out of the "
            "result.".format(left_out, KINDS[resource_type][1].lower())
        )
    return resources


def time_range(module, resource_type):
    """Return the query of the task: aggregation, resolution, start and end time"""
    start_time = module.params["start_time"]
    end_time = module.params["end_time"]
    if start_time is None:
        end_time = int(time.time()) * 1000
        start_time = end_time - module.params["window"] * 1000
    resolution = module.params["resolution"]
    if resolution is None:
        # A FlashBlade has the metrics of an account at one hour only
        resolution = HOUR if resource_type == "object-store-accounts" else DAY
    return {
        "aggregation": module.params["aggregation"],
        "resolution": resolution,
        "start_time": start_time,
        "end_time": end_time,
    }


def batches(metrics, resource_ids):
    """Return the (metric names, resource IDs) of the fewest requests

    Each request stays within the series Pure1 gives in one answer. The
    metrics go into groups of one size and each group is asked for as many
    resources as that size leaves room for: the size that needs the fewest
    requests is taken, and of two such sizes the larger.
    """
    if not metrics or not resource_ids:
        return []
    best = None
    for size in range(1, min(len(metrics), SERIES_PER_REQUEST) + 1):
        room = SERIES_PER_REQUEST // size
        requests = -(-len(metrics) // size) * -(-len(resource_ids) // room)
        if best is None or requests <= best[0]:
            best = (requests, size, room)
    size, room = best[1:]
    return [
        (metrics[first : first + size], resource_ids[start : start + room])
        for first in range(0, len(metrics), size)
        for start in range(0, len(resource_ids), room)
    ]


def request_history(module, pure_1, query, names, resource_ids):
    """Return the series of one request, or raise Pure1RequestError"""
    return pure1_items(
        module,
        "get_metrics_history",
        lambda: pure_1.get_metrics_history(
            aggregation=query["aggregation"],
            end_time=query["end_time"],
            resolution=query["resolution"],
            start_time=query["start_time"],
            names=names,
            resource_ids=resource_ids,
        ),
        fail=False,
    )


def names_id(text, resource_id):
    """Tell whether a message names an ID as a whole, not as a part of a longer one"""
    return bool(
        re.search(
            "(?<![0-9A-Za-z_-]){0}(?![0-9A-Za-z_-])".format(re.escape(resource_id)),
            text,
        )
    )


def read_history(module, pure_1, query, names, resource_ids, gone, skip_missing):
    """Return the series of the metrics for the resources

    Pure1 answers 404 for the whole request when it does not know one of the
    resources, and names them. With skip_missing those are added to gone and
    the others are asked for again. Any other error fails the task.
    """
    resource_ids = [
        resource_id for resource_id in resource_ids if resource_id not in gone
    ]
    while resource_ids:
        try:
            return request_history(module, pure_1, query, names, resource_ids)
        except Pure1RequestError as error:
            missing = []
            if skip_missing and error.status_code == 404:
                missing = [
                    resource_id
                    for resource_id in resource_ids
                    if names_id(error.text, resource_id)
                ]
            if not missing:
                module.fail_json(msg=str(error))
            gone.extend(missing)
            resource_ids = [
                resource_id
                for resource_id in resource_ids
                if resource_id not in missing
            ]
    return []


def last_point(series, history):
    """Return the keys of a series that tell of its data points"""
    points = [list(point) for point in pure1_attr(series, "data", []) if point]
    stamp = value = None
    if points:
        stamp = points[-1][0]
        if len(points[-1]) > 1:
            value = points[-1][1]
    keys = {"timestamp": pure1_time(stamp), "timestamp_epoch": stamp, "value": value}
    if history:
        keys["data"] = points
    return keys


def empty_metric(metric, query, history):
    """Return the row of a metric of the catalog before any series is read"""
    row = {
        "unit": metric["unit"],
        "aggregation": query["aggregation"],
        "resolution": query["resolution"],
        "timestamp": None,
        "timestamp_epoch": None,
        "value": None,
    }
    if history:
        row["data"] = []
    if per_array(metric):
        row["arrays"] = {}
    return row


def file_series(series, resources, array_ids, history):
    """Put a series into the row of its resource and metric

    Returns None when that is done, or why it is not: "unknown" for a series
    that is of no resource and metric of the task, "same name" for a second
    array of one name in a pod, and "empty" for a series for the arrays of a
    pod that names no array and has no data point.

    The series of a pod_array_ metric names the pod and the array. They are
    told apart by the ID of the pod, not by their position.
    """
    name = pure1_attr(series, "name")
    owner = None
    others = []
    for reference in pure1_attr(series, "resources", []):
        resource_id = pure1_attr(reference, "id")
        if owner is None and resource_id is not None and resource_id in resources:
            owner = resource_id
        elif reference is not None:
            others.append(reference)
    if owner is None or name not in resources[owner]["metrics"]:
        return "unknown"
    row = resources[owner]["metrics"][name]
    for key in ("unit", "aggregation", "resolution"):
        row[key] = pure1_attr(series, key, row[key])
    point = last_point(series, history)
    if "arrays" not in row:
        row.update(point)
        return None
    array = None
    for reference in others:
        if pure1_attr(reference, "name") is not None:
            array = reference
            break
    if array is None:
        return "empty" if point["timestamp_epoch"] is None else "unknown"
    seen = array_ids.setdefault((owner, name), {})
    if seen.setdefault(array.name, pure1_attr(array, "id")) != pure1_attr(array, "id"):
        return "same name"
    row["arrays"][array.name] = point
    return None


def has_point(row):
    """Tell whether the row of a metric has a data point"""
    if row["timestamp_epoch"] is not None:
        return True
    return any(
        point["timestamp_epoch"] is not None for point in row.get("arrays", {}).values()
    )


def collect_metrics(module, pure_1, resource_type, catalog):
    """Return the rows of the selected resources with their metrics, and the IDs skipped"""
    metrics = requested_metrics(module, catalog, resource_type)
    resources = select_resources(module, pure_1, resource_type, len(metrics))
    max_series = module.params["max_series"]
    if max_series and len(resources) * len(metrics) > max_series:
        fail_too_many(module, resource_type, len(resources), len(metrics))
    query = time_range(module, resource_type)
    history = module.params["history"]
    for row in resources.values():
        row["metrics"] = {
            name: empty_metric(catalog[name], query, history) for name in metrics
        }
    # Only a task that names no resource skips one that went away meanwhile
    skip_missing = not (module.params["name"] or module.params["ids"])
    gone = []
    array_ids = {}
    not_filed = {"unknown": 0, "same name": 0, "empty": 0}
    # Pure1 refuses the metrics of a pod and those of its arrays in one request
    for group in (
        [name for name in metrics if not per_array(catalog[name])],
        [name for name in metrics if per_array(catalog[name])],
    ):
        for names, resource_ids in batches(group, list(resources)):
            for found in read_history(
                module, pure_1, query, names, resource_ids, gone, skip_missing
            ):
                reason = file_series(found, resources, array_ids, history)
                if reason:
                    not_filed[reason] += 1
    for resource_id in gone:
        del resources[resource_id]
    if gone:
        module.warn(
            "Pure1 no longer knows {0} of the {1} it listed: they are named in "
            "skipped and are not in resources.".format(
                len(gone), KINDS[resource_type][1].lower()
            )
        )
    if not_filed["unknown"]:
        module.warn(
            "Pure1 returned {0} series that are of no resource and metric of the "
            "task: they are left out of the result.".format(not_filed["unknown"])
        )
    if not_filed["same name"]:
        module.warn(
            "Pure1 returned {0} series for another array of the same name in one "
            "pod: they are left out of the result.".format(not_filed["same name"])
        )
    if resources and not any(
        has_point(row)
        for resource in resources.values()
        for row in resource["metrics"].values()
    ):
        module.warn(
            "Pure1 returned no data point in the time range: every value is null, "
            "and the range may be too short or too recent for the delay Pure1 "
            "publishes data points with."
        )
    return resources, gone


def check_options(module):
    """Fail the task for what the argument spec cannot refuse"""
    resource_type = module.params["resource_type"]
    if not module.params["metrics"]:
        module.fail_json(
            msg="metrics must name at least one metric unless list_available is true."
        )
    if module.params["array"] and resource_type == "subscription-licenses":
        module.fail_json(
            msg="array cannot be used with resource_type subscription-licenses: "
            "a subscription license is not on an array."
        )
    window = module.params["window"]
    if window < 1:
        module.fail_json(msg="window must be 1 or more, got {0}".format(window))
    # required_together takes a null for a time that is set
    if (module.params["start_time"] is None) != (module.params["end_time"] is None):
        module.fail_json(msg="start_time and end_time must be set together.")
    max_series = module.params["max_series"]
    if max_series < 0:
        module.fail_json(msg="max_series must be 0 or more, got {0}".format(max_series))


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            resource_type=dict(type="str", required=True, choices=list(KINDS)),
            metrics=dict(type="list", elements="str"),
            name=dict(type="list", elements="str"),
            ids=dict(type="list", elements="str"),
            array=dict(type="list", elements="str"),
            aggregation=dict(type="str", choices=["avg", "max"], default="avg"),
            resolution=dict(type="int"),
            window=dict(type="int", default=DEFAULT_WINDOW),
            start_time=dict(type="int"),
            end_time=dict(type="int"),
            history=dict(type="bool", default=False),
            list_available=dict(type="bool", default=False),
            max_series=dict(type="int", default=DEFAULT_MAX_SERIES),
        )
    )
    module = AnsibleModule(
        argument_spec,
        mutually_exclusive=[
            ["name", "ids"],
            ["window", "start_time"],
            ["window", "end_time"],
        ],
        required_together=[["start_time", "end_time"]],
        required_if=[["list_available", False, ["metrics"]]],
        supports_check_mode=True,
    )
    if not module.params["list_available"]:
        check_options(module)
    pure_1 = get_pure1(module)

    resource_type = module.params["resource_type"]
    catalog = metric_catalog(module, pure_1, resource_type)
    result = {"available_metrics": [], "resources": {}, "skipped": []}
    if module.params["list_available"]:
        result["available_metrics"] = [catalog[name] for name in sorted(catalog)]
    else:
        result["resources"], result["skipped"] = collect_metrics(
            module, pure_1, resource_type, catalog
        )

    module.exit_json(changed=False, pure1_metrics=result)


if __name__ == "__main__":
    main()
