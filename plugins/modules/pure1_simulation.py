#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_simulation
version_added: '1.6.0'
short_description: Collect the projected load and capacity of arrays from Pure1
description:
  - Returns the load and the capacity that Pure1 projects for the named
    arrays, for each of them as a time series with its first, last, lowest
    and highest point. Unless the options say otherwise, Pure1 projects 90
    days ahead in daily points, the first of them at the last midnight UTC.
  - Use it to check that an array is projected to stay below a threshold
    before more is provisioned on it, or to report the arrays whose capacity
    is projected to grow.
  - Pure1 projects FlashArrays. When this was written it refused a
    FlashBlade and a Cloud Block Store array. One array that Pure1 refuses
    fails the task, also for the other arrays of the task.
  - The values are returned as Pure1 sends them. When this was written they
    were fractions of 1 with an empty C(unit). For the load, C(0.5) is half
    of the performance capacity of the array in use. For the capacity,
    C(0.5) is half of the capacity of the array in use.
  - Pure1 computes a projection when it is asked for one. The C(as_of) of a
    projection is the time that Pure1 gives for the freshness of its data.
  - The module asks for the projections with a POST request that stores and
    changes nothing in Pure1. C(changed) is therefore always false, and the
    request is sent in check mode as well.
options:
  arrays:
    description:
      - Names of the arrays to return the projections of. At least one name
        is required.
      - Pure1 simulates at most 10 arrays in one request. A task that names
        more arrays fails before any projection is asked for. An array that
        is named more than once counts once.
      - A name is matched without regard to case and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task before any projection is asked for.
      - Pure1 names an array that it refuses to project by its id. The
        message of the failed task has the name of the array in its place.
    type: list
    elements: str
    required: true
  metrics:
    description:
      - The projections to return. Both when not set or empty.
    type: list
    elements: str
    choices: [ projected_load, projected_capacity ]
    default: [ projected_load, projected_capacity ]
  aggregation:
    description:
      - The way Pure1 aggregates the projected load. Pure1 uses
        C(blended_max) when it is not set.
      - It applies to C(projected_load) only. When I(metrics) does not name
        C(projected_load) it is not sent and the module gives a warning.
    type: str
    choices: [ blended_max, average ]
  projection_days:
    description:
      - The number of days to project, 1 to 36500. The projection starts at
        the last midnight UTC, and the module asks for it to end this number
        of days later.
      - Pure1 returns one point for each day, the first at the last midnight
        UTC, so that a projection of 30 days has 30 points.
      - Pure1 projects 90 days when it is not set.
    type: int
  training_days:
    description:
      - The number of days of history to compute the projection from, 1 to
        36500. The history ends at the last midnight UTC, and the module asks
        for it to start this number of days earlier.
      - Pure1 refuses a history that is too short for it, which fails the
        task. When this was written it refused 14 days and less and accepted
        21 days.
      - Pure1 uses the history of three months when it is not set.
    type: int
notes:
  - This module makes the Pure1 REST 1.6 call C(post_arrays_simulations),
    which came with py-pure-client 1.99.0. It makes its requests through
    Pure1 REST 1.6, also where the installed py-pure-client knows a newer
    version, because Pure1 did not accept the request for a simulation on
    the Pure1 REST 1.7 path when this was written.
  - The module makes two requests to Pure1 whatever the options are, one for
    the arrays of the names and one for the projections.
  - Pure1 computes the projections while the request waits for them, and
    py-pure-client waits 15 seconds for the answer to a request. A request
    for one or two arrays took a few seconds when this was written. A task
    that fails with a read timeout may succeed with fewer arrays.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the projected load and capacity of two arrays
  everpure.pure1.pure1_simulation:
    arrays:
      - array1
      - array2
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: projection

- name: Show the last point of the capacity projected for each array
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value['projected_capacity']['last'] | default('no projection', true) }}"
  loop: "{{ projection['pure1_simulation']['arrays'] | dict2items }}"

- name: Collect the average load projected for 30 days from the history of 60 days
  everpure.pure1.pure1_simulation:
    arrays:
      - array1
    metrics:
      - projected_load
    aggregation: average
    projection_days: 30
    training_days: 60
  register: month

- name: Check that the load of the array is not projected to rise by more than half
  ansible.builtin.assert:
    that:
      - load['data'] | default([]) | length > 0
      - load['max']['value'] <= load['first']['value'] * 1.5
    fail_msg: The load of array1 has no projection or is projected to rise by more than half
  vars:
    load: "{{ month['pure1_simulation']['arrays']['array1']['projected_load'] }}"

- name: Collect the capacity projected for 90 days
  everpure.pure1.pure1_simulation:
    arrays:
      - array1
      - array2
    metrics:
      - projected_capacity
    projection_days: 90
  register: capacity

- name: Report the arrays whose capacity is projected to grow by more than a tenth
  ansible.builtin.debug:
    msg: >-
      {{ item.key }} is projected to grow from
      {{ item.value['projected_capacity']['first']['value'] }} to
      {{ item.value['projected_capacity']['last']['value'] }}
      by {{ item.value['projected_capacity']['last']['time'] }}
  loop: "{{ capacity['pure1_simulation']['arrays'] | dict2items }}"
  when:
    - item.value['projected_capacity']['data'] | default([]) | length > 0
    - item.value['projected_capacity']['last']['value'] > item.value['projected_capacity']['first']['value'] * 1.1
"""

RETURN = r"""
pure1_simulation:
  description: The projections that Pure1 computed, and what the module asked for.
  returned: always
  type: dict
  contains:
    request:
      description:
        - What the module asked Pure1 to project.
        - A time is null when the task did not set the option that gives it.
          Nothing is sent for it then, and Pure1 uses its default.
      type: dict
      contains:
        resources:
          description: The names of the arrays, as Pure1 spells them.
          type: list
          elements: str
        metrics:
          description: The projections that were asked for, in the order of I(metrics).
          type: list
          elements: dict
          contains:
            name:
              description: The name of the projection, C(projected_load) or C(projected_capacity).
              type: str
            aggregation:
              description:
                - The aggregation that was asked for. Null when none was,
                  which is always so for C(projected_capacity).
              type: str
        projection_end_time:
          description:
            - The end of the projection that I(projection_days) gives, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        projection_end_time_epoch:
          description:
            - The end of the projection that I(projection_days) gives, in
              milliseconds since the epoch.
          type: int
        training_start_time:
          description:
            - The start of the history that I(training_days) gives, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        training_start_time_epoch:
          description:
            - The start of the history that I(training_days) gives, in
              milliseconds since the epoch.
          type: int
    arrays:
      description:
        - The projections of each array, as a dictionary keyed by array name.
          The value for an array is a dictionary with the keys listed here.
        - Every array of I(arrays) is listed. When this was written Pure1
          answered with the projections of every array or refused the whole
          request, which fails the task.
        - A key is null when Pure1 reports no value for it.
      type: dict
      contains:
        id:
          description: The id of the array in Pure1.
          type: str
        fqdn:
          description: The fully qualified domain name of the array.
          type: str
        projected_load:
          description:
            - The projected load of the array, a dictionary with the keys
              listed here.
            - An empty dictionary when I(metrics) does not name
              C(projected_load), and when the answer of Pure1 has no such
              projection for the array, which was not seen when this was
              written.
          type: dict
          contains:
            unit:
              description:
                - The unit of the values, as Pure1 sends it. It was an empty
                  string when this was written, and the values were
                  fractions of 1. C(0.5) is half of the performance capacity
                  of the array in use.
              type: str
            aggregation:
              description:
                - The aggregation of the values, as Pure1 names it,
                  C(blended_max) or C(average).
              type: str
            resolution:
              description:
                - The time between two points, in milliseconds. Pure1
                  projects in daily points, C(86400000).
              type: int
            as_of:
              description:
                - The time that Pure1 gives for the freshness of the
                  projection, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            as_of_epoch:
              description:
                - The time that Pure1 gives for the freshness of the
                  projection, in milliseconds since the epoch.
              type: int
            first:
              description:
                - The earliest point of the projection. Null when the
                  projection has no point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The projected value at that time, an integer when Pure1
                      sends a whole number.
                  type: float
            last:
              description:
                - The latest point of the projection. Null when the
                  projection has no point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The projected value at that time, an integer when Pure1
                      sends a whole number.
                  type: float
            min:
              description:
                - The point with the lowest value, the earliest of them when
                  several points have it. Null when the projection has no
                  point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The lowest projected value, an integer when Pure1 sends
                      a whole number.
                  type: float
            max:
              description:
                - The point with the highest value, the earliest of them when
                  several points have it. Null when the projection has no
                  point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The highest projected value, an integer when Pure1 sends
                      a whole number.
                  type: float
            data:
              description:
                - The points of the projection as Pure1 returned them. Each
                  point is a list of its time, in milliseconds since the
                  epoch, and the projected value at that time.
                - Pure1 sends one point for each day, the first at the last
                  midnight UTC.
              type: list
              elements: list
        projected_capacity:
          description:
            - The projected capacity of the array, a dictionary with the keys
              listed here.
            - An empty dictionary when I(metrics) does not name
              C(projected_capacity), and when the answer of Pure1 has no such
              projection for the array, which was not seen when this was
              written.
          type: dict
          contains:
            unit:
              description:
                - The unit of the values, as Pure1 sends it. It was an empty
                  string when this was written, and the values were
                  fractions of 1. C(0.5) is half of the capacity of the array
                  in use.
              type: str
            aggregation:
              description:
                - The aggregation of the values, as Pure1 names it. Pure1
                  reports one for the capacity as well, C(blended_max) when
                  this was written, although I(aggregation) applies to the
                  load only.
              type: str
            resolution:
              description:
                - The time between two points, in milliseconds. Pure1
                  projects in daily points, C(86400000).
              type: int
            as_of:
              description:
                - The time that Pure1 gives for the freshness of the
                  projection, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            as_of_epoch:
              description:
                - The time that Pure1 gives for the freshness of the
                  projection, in milliseconds since the epoch.
              type: int
            first:
              description:
                - The earliest point of the projection. Null when the
                  projection has no point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The projected value at that time, an integer when Pure1
                      sends a whole number.
                  type: float
            last:
              description:
                - The latest point of the projection. Null when the
                  projection has no point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The projected value at that time, an integer when Pure1
                      sends a whole number.
                  type: float
            min:
              description:
                - The point with the lowest value, the earliest of them when
                  several points have it. Null when the projection has no
                  point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The lowest projected value, an integer when Pure1 sends
                      a whole number.
                  type: float
            max:
              description:
                - The point with the highest value, the earliest of them when
                  several points have it. Null when the projection has no
                  point with a time and a value.
              type: dict
              contains:
                time:
                  description: The time of the point, as C(YYYY-MM-DD HH:MM:SS UTC).
                  type: str
                time_epoch:
                  description: The time of the point, in milliseconds since the epoch.
                  type: int
                value:
                  description:
                    - The highest projected value, an integer when Pure1 sends
                      a whole number.
                  type: float
            data:
              description:
                - The points of the projection as Pure1 returned them. Each
                  point is a list of its time, in milliseconds since the
                  epoch, and the projected value at that time.
                - Pure1 sends one point for each day, the first at the last
                  midnight UTC.
              type: list
              elements: list
  sample:
    request:
      resources:
        - array1
      metrics:
        - name: projected_load
          aggregation: average
      projection_end_time: 2026-11-27 00:00:00 UTC
      projection_end_time_epoch: 1795737600000
      training_start_time: 2026-09-25 00:00:00 UTC
      training_start_time_epoch: 1790294400000
    arrays:
      array1:
        id: 0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d
        fqdn: array1.example.com
        projected_load:
          unit: ''
          aggregation: average
          resolution: 86400000
          as_of: 2026-11-24 10:15:30 UTC
          as_of_epoch: 1795515330000
          first:
            time: 2026-11-24 00:00:00 UTC
            time_epoch: 1795478400000
            value: 0.41
          last:
            time: 2026-11-26 00:00:00 UTC
            time_epoch: 1795651200000
            value: 0.42
          min:
            time: 2026-11-24 00:00:00 UTC
            time_epoch: 1795478400000
            value: 0.41
          max:
            time: 2026-11-25 00:00:00 UTC
            time_epoch: 1795564800000
            value: 0.44
          data:
            - [1795478400000, 0.41]
            - [1795564800000, 0.44]
            - [1795651200000, 0.42]
        projected_capacity: {}
"""


import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    Pure1RequestError,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_require,
    pure1_resolve_arrays,
    pure1_response,
    pure1_time,
)

METRICS = ["projected_load", "projected_capacity"]
AGGREGATIONS = ["blended_max", "average"]
# Only the load is aggregated: Pure1 computes the capacity in daily points
AGGREGATED = "projected_load"
DAY = 86400000
# A hundred years: a period far beyond it gives a time that cannot be shown
MAX_DAYS = 36500
# Pure1 refuses a request that names more arrays (seen 2026-11-25)
MAX_ARRAYS = 10
# The simulation is asked for through Pure1 REST 1.6. Pure1 refuses the
# request that py-pure-client sends on the REST 1.7 path ("Unrecognized field
# resources", seen 2026-11-25) and answers the same request on the 1.6 path.
REST_VERSION = "1.6"


def check_options(module):
    """Fail the task for a value that the argument spec cannot refuse"""
    if not module.params["arrays"]:
        module.fail_json(msg="arrays must name at least one array.")
    for option in ("projection_days", "training_days"):
        value = module.params[option]
        if value is not None and value < 1:
            module.fail_json(msg="{0} must be 1 or more, got {1}".format(option, value))
    for option in ("projection_days", "training_days"):
        value = module.params[option]
        if value is not None and value > MAX_DAYS:
            module.fail_json(
                msg="{0} must be {1} or less, got {2}".format(option, MAX_DAYS, value)
            )


def last_midnight():
    """Return the last midnight UTC, in milliseconds since the epoch"""
    return int(time.time()) // 86400 * DAY


def array_names(module, arrays):
    """Return the names the simulation is asked for, as Pure1 spells them

    arrays is what pure1_resolve_arrays() returned. Each name comes once, in
    the order of the task. The task fails when they are more than Pure1
    simulates in one request.
    """
    names = []
    for array in arrays:
        if array["name"] not in names:
            names.append(array["name"])
    if len(names) > MAX_ARRAYS:
        module.fail_json(
            msg="arrays names {0} arrays, and Pure1 simulates at most {1} in one "
            "request.".format(len(names), MAX_ARRAYS)
        )
    return names


def simulation_body(module, names):
    """Return the body of the simulation request for the options of the task

    names is what array_names() returned. A field that no option gives is
    left out, for Pure1 to use its default. The time between two points is
    never sent: Pure1 takes one day only.
    """
    params = module.params
    metrics = []
    for name in params["metrics"] or METRICS:
        if name not in metrics:
            metrics.append(name)
    aggregation = params["aggregation"]
    if aggregation and AGGREGATED not in metrics:
        module.warn(
            "aggregation applies to projected_load only, which metrics does "
            "not name: it is not sent to Pure1."
        )
    body = {"resources": [{"name": name} for name in names], "metrics": []}
    for name in metrics:
        metric = {"name": name}
        if aggregation and name == AGGREGATED:
            metric["aggregation"] = aggregation
        body["metrics"].append(metric)
    if params["projection_days"] is not None or params["training_days"] is not None:
        midnight = last_midnight()
        if params["projection_days"] is not None:
            body["projection_end_time"] = midnight + params["projection_days"] * DAY
        if params["training_days"] is not None:
            body["training_start_time"] = midnight - params["training_days"] * DAY
    return body


def request_row(body):
    """Return what the result tells of the request, from its body"""
    projection_end = body.get("projection_end_time")
    training_start = body.get("training_start_time")
    return {
        "resources": [array["name"] for array in body["resources"]],
        "metrics": [
            {"name": metric["name"], "aggregation": metric.get("aggregation")}
            for metric in body["metrics"]
        ],
        "projection_end_time": pure1_time(projection_end),
        "projection_end_time_epoch": projection_end,
        "training_start_time": pure1_time(training_start),
        "training_start_time_epoch": training_start,
    }


def refusal_text(error, arrays):
    """Return the message of a simulation that failed, with its arrays named

    Pure1 names an array that it refuses to project by its id. Each id of
    an array of the task is replaced by the name of that array as Pure1
    spells it. Any other text is the line of the shared helpers unchanged.
    """
    names = {}
    for array in arrays:
        # The SDK does not validate an answer: only a string can be looked for
        if isinstance(array["id"], str) and array["id"]:
            names.setdefault(array["id"], array["name"])
    text = str(error)
    if not names:
        return text
    # An id counts where it stands alone, not as a part of a longer one, and
    # the longest id that stands there is the one
    ids = "|".join(
        re.escape(array_id) for array_id in sorted(names, key=len, reverse=True)
    )
    return re.sub(
        r"(?<![\w-])(?:{0})(?![\w-])".format(ids),
        lambda found: names[found.group(0)],
        text,
    )


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def point_row(point):
    return {
        "time": pure1_time(point[0]),
        "time_epoch": point[0],
        "value": point[1],
    }


def projection_row(item):
    """Return the result of one projection: its fields, points and summary

    Only a point with a time and a value counts for first, last, min and max.
    """
    as_of = pure1_attr(item, "as_of")
    received = pure1_attr(item, "data", [])
    if not isinstance(received, (list, tuple)):
        # The SDK does not validate an answer: what is no list has no point
        received = []
    data = [list(point) for point in received if isinstance(point, (list, tuple))]
    # In the order of time; the sort keeps the order of points of one time
    points = sorted(
        (
            point
            for point in data
            if len(point) >= 2 and is_number(point[0]) and is_number(point[1])
        ),
        key=lambda point: point[0],
    )
    row = {
        "unit": pure1_attr(item, "unit"),
        "aggregation": pure1_attr(item, "aggregation"),
        "resolution": pure1_attr(item, "resolution"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "first": None,
        "last": None,
        "min": None,
        "max": None,
        "data": data,
    }
    if points:
        row["first"] = point_row(points[0])
        row["last"] = point_row(points[-1])
        # min() and max() return the first of several points of one value
        row["min"] = point_row(min(points, key=lambda point: point[1]))
        row["max"] = point_row(max(points, key=lambda point: point[1]))
    return row


def generate_simulation_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["arrays"])
    body = simulation_body(module, array_names(module, arrays))
    wanted = [metric["name"] for metric in body["metrics"]]

    try:
        answer = pure1_response(
            module,
            "post_arrays_simulations",
            lambda: pure_1.post_arrays_simulations(planning_simulation_post=body),
            fail=False,
        )
    except Pure1RequestError as error:
        module.fail_json(msg=refusal_text(error, arrays))
    # The answer has one page: a simulation is not listed with a token
    projections = [] if answer.items is None else list(answer.items)
    total = getattr(answer, "total_item_count", None)
    if total is not None and len(projections) < total:
        module.warn(
            "Pure1 returned {0} of {1} projections: the result may be "
            "incomplete.".format(len(projections), total)
        )
    elif total == 0 and answer.items is not None and len(answer.items):
        # py-pure-client hands on no more items than Pure1 counts, and the
        # length of the items is the number that came when the count is 0
        module.warn(
            "Projections that Pure1 sent and counted as none cannot be read "
            "and are left out of the result: {0}.".format(len(answer.items))
        )

    # Every array of the task is in the result, with or without a projection
    arrays_info = {}
    by_id = {}
    same_name = 0
    for array in arrays:
        name = array["name"]
        if name in arrays_info:
            # Pure1 has two arrays of one name: the first one keeps the name
            same_name += 1
            if array["id"] is not None:
                by_id[array["id"]] = None
            continue
        arrays_info[name] = {
            "id": array["id"],
            "fqdn": array["fqdn"],
            "projected_load": {},
            "projected_capacity": {},
        }
        if array["id"] is not None:
            by_id[array["id"]] = name

    left_out = 0
    repeated = 0
    several = 0
    for item in projections:
        metric = pure1_attr(item, "name")
        references = [
            resource
            for resource in pure1_attr(item, "resources", [])
            if resource is not None
        ]
        reference = references[0] if references else None
        # The SDK does not validate an answer: only a string tells an array
        array_id = pure1_attr(reference, "id")
        if isinstance(array_id, str) and array_id in by_id:
            # The id tells the array, whatever name the projection has for it
            name = by_id[array_id]
            if name is None:
                # counted with its array
                continue
        else:
            name = pure1_attr(reference, "name")
        if metric not in wanted or not isinstance(name, str) or name not in arrays_info:
            left_out += 1
        elif arrays_info[name][metric]:
            repeated += 1
        else:
            arrays_info[name][metric] = projection_row(item)
            if len(references) > 1:
                several += 1
    if same_name:
        module.warn(
            "Arrays that carry the name of another array of the task are left "
            "out of the result with their projections: {0}.".format(same_name)
        )
    if left_out:
        module.warn(
            "Projections that Pure1 returned for no array and metric of the "
            "task are left out of the result: {0}.".format(left_out)
        )
    if repeated:
        module.warn(
            "Projections that Pure1 returned after another one of the same "
            "array and metric are left out of the result: {0}.".format(repeated)
        )
    if several:
        module.warn(
            "Projections that name more than one array are in the result of "
            "the first array they name only: {0}.".format(several)
        )
    return {"request": request_row(body), "arrays": arrays_info}


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            arrays=dict(type="list", elements="str", required=True),
            metrics=dict(
                type="list", elements="str", choices=METRICS, default=list(METRICS)
            ),
            aggregation=dict(type="str", choices=AGGREGATIONS),
            projection_days=dict(type="int"),
            training_days=dict(type="int"),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    check_options(module)
    pure_1 = get_pure1(module, version=REST_VERSION)
    pure1_require(module, pure_1, "post_arrays_simulations", "1.99.0")

    module.exit_json(
        changed=False, pure1_simulation=generate_simulation_dict(module, pure_1)
    )


if __name__ == "__main__":
    main()
