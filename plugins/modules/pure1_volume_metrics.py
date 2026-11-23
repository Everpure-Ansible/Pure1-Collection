#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    "supported_by": "community",
}

DOCUMENTATION = r"""
---
module: pure1_volume_metrics
version_added: '1.6.0'
short_description: Collect volume metrics from Pure1
description:
  - Collect historical metric data (such as capacity or performance) for
    volumes from Pure1, keyed on volume serial number.
  - Pure1 exposes per-volume capacity and performance as time-series metrics
    rather than as fields on the volume object, so this module queries the
    Pure1 metrics history endpoint.
  - Use I(list_available) to discover which metric names are valid for volumes
    in your environment before requesting them with I(metrics).
  - With the default I(resolution) of one day and I(window) of 86400 seconds,
    Pure1 returns one data point for each metric, stamped at the start of the
    window.
  - A metric with no data point in the window is absent from the C(metrics)
    dictionary of the volume.
  - When neither I(array) nor I(volumes) is set, a volume that is removed
    from Pure1 while the module runs is skipped with a warning.
options:
  array:
    description:
      - Filter to provide only volumes for a specifically named array.
      - Can be combined with I(volumes) to select named volumes on that array.
    type: str
  volumes:
    description:
      - List of specific volume names to query.
      - If not specified, all volumes are queried.
      - The task fails if the list is empty, if a named volume does not
        exist, or if it is not on I(array) when that is also set.
    type: list
    elements: str
  metrics:
    description:
      - List of Pure1 volume metric names to collect.
      - Required unless I(list_available) is set.
      - Use I(list_available) to discover the valid metric names.
      - The task fails if the list is empty or contains a name that is not a
        volume metric.
    type: list
    elements: str
  aggregation:
    description:
      - Aggregation to apply to the metric data.
    type: str
    choices: [ avg, max ]
    default: avg
  resolution:
    description:
      - The duration of time between individual data points, in milliseconds.
      - Defaults to one day.
      - I(list_available) returns the resolutions each metric is stored at.
        Pure1 rejects a resolution finer than the finest of them and accepts
        a coarser one.
    type: int
    default: 86400000
  window:
    description:
      - How far back, in seconds, to request metric data.
      - The most recent data point in the window is returned for each metric.
      - Pure1 publishes data points with a delay that can exceed an hour, so
        a short window can return no data point. The metric is then absent
        from the result, and a warning is given when no volume has a data
        point.
    type: int
    default: 86400
  list_available:
    description:
      - If set, return the list of metric names available for volumes, with
        the resolutions each one is stored at, instead of querying any
        metric data.
    type: bool
    default: false
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: List the metric names available for volumes
  everpure.pure1.pure1_volume_metrics:
    list_available: true
  register: available

- name: Show available volume metric names
  debug:
    msg: "{{ available['pure1_volume_metrics']['available_metrics'] }}"

- name: Collect a capacity metric for all volumes
  everpure.pure1.pure1_volume_metrics:
    metrics:
      - volume_physical_capacity
  register: usage

- name: Show per-volume metric data
  debug:
    msg: "{{ usage['pure1_volume_metrics']['serial_numbers'] }}"

- name: Collect multiple metrics for volumes on a single array
  everpure.pure1.pure1_volume_metrics:
    array: flasharray1
    metrics:
      - volume_physical_capacity
      - volume_write_iops

- name: Collect the peak IOPS of the latest hour for named volumes on an array
  everpure.pure1.pure1_volume_metrics:
    array: flasharray1
    volumes:
      - vol1
      - vol2
    metrics:
      - volume_read_iops
      - volume_write_iops
    aggregation: max
    resolution: 3600000
"""

RETURN = r"""
pure1_volume_metrics:
  description: Returns the volume metric data collected from Pure1
  returned: always
  type: dict
  contains:
    available_metrics:
      description: List of metrics available for volumes (only when I(list_available) is set)
      type: list
      elements: dict
      contains:
        name:
          description: The metric name
          type: str
        unit:
          description: The unit of the metric
          type: str
        description:
          description: The description of the metric
          type: str
        resource_types:
          description: The resource types the metric is available for
          type: list
          elements: str
        availabilities:
          description: The resolutions the metric is stored at
          type: list
          elements: dict
          contains:
            resolution:
              description: A resolution the metric is stored at, in milliseconds
              type: int
            retention:
              description: How long data is kept at this resolution, in milliseconds
              type: int
            aggregations:
              description: The aggregations available at this resolution
              type: list
              elements: str
    serial_numbers:
      description: Per-volume metric data keyed on volume serial number
      type: dict
      contains:
        id:
          description: The globally unique Pure1 ID (NAID) of the volume
          type: str
        name:
          description: The volume name
          type: str
        metrics:
          description:
            - Dictionary of requested metrics keyed on metric name
            - A metric with no data point in the window is absent
          type: dict
          contains:
            unit:
              description: The unit of the metric
              type: str
            timestamp:
              description: UTC millisecond epoch of the returned data point
              type: int
            value:
              description: The value of the most recent data point in the window
              type: float
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    get_pure1,
    pure1_argument_spec,
)
import time

# Pure1 metrics history allows up to 32 time series (metrics x resources) per call.
MAX_TIME_SERIES = 32


def filter_string(value):
    # Pure1 filters escape a backslash or a single quote in a string with a backslash.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def api_error(response):
    errors = []
    for error in getattr(response, "errors", None) or []:
        parts = [getattr(error, "message", None), getattr(error, "context", None)]
        errors.append(": ".join(str(part) for part in parts if part))
    return "; ".join(errors) or "HTTP status %s" % getattr(
        response, "status_code", None
    )


def call_api(module, action, method, **kwargs):
    # Returns (200, items) or (status, error text). An SDK error response has
    # no items attribute, so the status is checked before items is read.
    try:
        response = method(**kwargs)
        status = getattr(response, "status_code", None)
        if status == 200:
            return status, list(response.items)
        return status, api_error(response)
    except Exception as err:
        module.fail_json(msg="Failed to %s: %s" % (action, str(err)))


def get_items(module, action, method, **kwargs):
    status, result = call_api(module, action, method, **kwargs)
    if status != 200:
        module.fail_json(msg="Failed to %s: %s" % (action, result))
    return result


def list_volume_metrics(module, pure_1):
    metrics = get_items(
        module, "list volume metrics", pure_1.get_metrics, resource_types=["volumes"]
    )
    available = []
    for metric in metrics:
        name = getattr(metric, "name", None)
        if name is None:
            continue
        available.append(
            {
                "name": name,
                "unit": getattr(metric, "unit", None),
                "description": getattr(metric, "description", None),
                "resource_types": getattr(metric, "resource_types", None),
                "availabilities": [
                    {
                        "resolution": getattr(availability, "resolution", None),
                        "retention": getattr(availability, "retention", None),
                        "aggregations": getattr(availability, "aggregations", None),
                    }
                    for availability in getattr(metric, "availabilities", None) or []
                ],
            }
        )
    return available


def validate_metrics(module, pure_1):
    metrics = module.params["metrics"]
    valid = sorted(metric["name"] for metric in list_volume_metrics(module, pure_1))
    if not metrics:
        module.fail_json(
            msg="metrics must name at least one volume metric. Valid names: %s"
            % ", ".join(valid)
        )
    unknown = [name for name in metrics if name not in valid]
    if unknown:
        module.fail_json(
            msg="Unknown volume metric(s): %s. Valid names: %s"
            % (", ".join(unknown), ", ".join(valid))
        )


def resolve_volumes(module, pure_1):
    kwargs = {}
    if module.params["volumes"] is not None and not module.params["volumes"]:
        module.fail_json(msg="volumes must name at least one volume")
    if module.params["array"]:
        kwargs["filter"] = "arrays.name=" + filter_string(module.params["array"])
    if module.params["volumes"]:
        kwargs["names"] = module.params["volumes"]
    volumes = get_items(module, "list volumes", pure_1.get_volumes, **kwargs)
    if module.params["array"] and module.params["volumes"]:
        # Pure1 fails an unknown name, but only leaves out a named volume
        # that exists on another array. It matches names in any case.
        found = [str(getattr(volume, "name", None)).lower() for volume in volumes]
        missing = [
            name for name in module.params["volumes"] if name.lower() not in found
        ]
        if missing:
            module.fail_json(
                msg="Volume(s) %s not found on array %s"
                % (", ".join(missing), module.params["array"])
            )
    return volumes


def get_history(module, pure_1, volume_ids, start_time, end_time, skip_missing):
    # Returns (series, ids of missing volumes). Pure1 answers 404 for the whole
    # call when one volume is unknown. With skip_missing the batch is retried
    # one volume at a time and a volume named in its 404 is reported as
    # missing. Any other error fails the task with the API message.
    status, result = call_api(
        module,
        "collect volume metrics",
        pure_1.get_metrics_history,
        aggregation=module.params["aggregation"],
        resolution=module.params["resolution"],
        start_time=start_time,
        end_time=end_time,
        names=module.params["metrics"],
        resource_ids=volume_ids,
    )
    if status == 200:
        return result, []
    if status == 404 and skip_missing:
        if len(volume_ids) > 1:
            series = []
            missing = []
            for volume_id in volume_ids:
                found, gone = get_history(
                    module, pure_1, [volume_id], start_time, end_time, skip_missing
                )
                series.extend(found)
                missing.extend(gone)
            return series, missing
        if volume_ids[0] in result:
            return [], volume_ids
    module.fail_json(msg="Failed to collect volume metrics: %s" % result)


def generate_metrics_dict(module, pure_1):
    metrics = module.params["metrics"]
    validate_metrics(module, pure_1)
    volumes = resolve_volumes(module, pure_1)

    # Seed the result with every in-scope volume so the report is complete even
    # when a volume has no data points for the requested window.
    volumes_info = {}
    vol_by_id = {}
    for volume in volumes:
        serial = getattr(volume, "serial", None)
        volume_id = getattr(volume, "id", None)
        if serial is None or volume_id is None:
            continue
        volumes_info[serial] = {
            "id": volume_id,
            "name": getattr(volume, "name", None),
            "metrics": {},
        }
        vol_by_id[volume_id] = serial

    end_time = int(time.time()) * 1000
    start_time = end_time - (module.params["window"] * 1000)

    # Chunk volumes so that metrics x volumes stays within the 32 series limit.
    vols_per_call = max(1, MAX_TIME_SERIES // len(metrics))
    volume_ids = list(vol_by_id.keys())
    # Only a run over all volumes skips a volume removed after it was listed.
    skip_missing = not (module.params["array"] or module.params["volumes"])

    for start in range(0, len(volume_ids), vols_per_call):
        batch = volume_ids[start : start + vols_per_call]
        history, missing = get_history(
            module, pure_1, batch, start_time, end_time, skip_missing
        )
        for volume_id in missing:
            volume = volumes_info.pop(vol_by_id.pop(volume_id), None) or {}
            module.warn(
                "Volume %s (%s) is no longer known to Pure1 and was skipped"
                % (volume.get("name"), volume_id)
            )
        for series in history:
            if not getattr(series, "data", None) or not getattr(
                series, "resources", None
            ):
                continue
            name = getattr(series, "name", None)
            volume = volumes_info.get(
                vol_by_id.get(getattr(series.resources[0], "id", None))
            )
            if name is None or volume is None:
                continue
            latest = series.data[-1]
            volume["metrics"][name] = {
                "unit": getattr(series, "unit", None),
                "timestamp": latest[0],
                "value": latest[1],
            }
    if volumes_info and not any(volume["metrics"] for volume in volumes_info.values()):
        module.warn(
            "No data points were returned for the window of %s seconds. Pure1 "
            "publishes data points with a delay, so a longer window may be needed."
            % module.params["window"]
        )
    return volumes_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="str"),
            volumes=dict(type="list", elements="str"),
            metrics=dict(type="list", elements="str"),
            aggregation=dict(type="str", choices=["avg", "max"], default="avg"),
            resolution=dict(type="int", default=86400000),
            window=dict(type="int", default=86400),
            list_available=dict(type="bool", default=False),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[["list_available", False, ["metrics"]]],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)

    result = {}
    if module.params["list_available"]:
        result["available_metrics"] = list_volume_metrics(module, pure_1)
    else:
        result["serial_numbers"] = generate_metrics_dict(module, pure_1)

    module.exit_json(changed=False, pure1_volume_metrics=result)


if __name__ == "__main__":
    main()
