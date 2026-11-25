#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_replica_links
version_added: '1.6.0'
short_description: Collect replica links and replication targets from Pure1
description:
  - Collect the replica links that Pure1 knows, which are the ActiveDR pod
    replica links of FlashArrays and the file-system and bucket replica links
    of FlashBlades, each with its sources, targets, status, lag and recovery
    point, and the object replication targets of FlashBlades.
  - It gives a replication report for every array of the organization from
    one credential, and a coarse check before a disaster recovery test.
  - Pure1 lags the arrays. Every link carries the time at which Pure1 last
    refreshed it and its age (C(as_of), C(as_of_age_seconds)), which is
    seconds to minutes for most links and has been seen to be more than a
    year for a link that Pure1 still reports as replicating. The recovery
    points of FlashBlade links have been seen about an hour behind.
  - The module is a report and a coarse check. It is not a recovery point
    objective gate, for which the arrays themselves are the authority, and
    it derives no healthy or unhealthy verdict of its own.
  - It makes one request for each link type, one for the replication
    targets, one for the policies of the file-system links when the result
    holds a file-system link, and one for I(array).
options:
  link_type:
    description:
      - The kinds of replica link to collect.
      - C(pod) are the ActiveDR pod replica links of FlashArrays,
        C(file_system) and C(bucket) the replica links of FlashBlades.
      - The result has an empty list for a kind that is not collected, and
        its summary has counts of zero, no status and null maximum values.
      - An empty list counts as not set, and all three kinds are collected.
    type: list
    elements: str
    choices: [ pod, file_system, bucket ]
    default: [ pod, file_system, bucket ]
  array:
    description:
      - Collect only the links that have a member, which is a source or a
        target, on one of these arrays, and only the replication targets of
        these arrays.
      - Without it, or with an empty list, the links and the replication
        targets of every array are collected.
      - The names are matched without regard to case, and the result carries
        the names as Pure1 spells them.
      - The task fails for a name that Pure1 does not know.
      - Only the arrays that the names stand for are selected, which are
        told from every other array by their ID in Pure1. Another array
        whose name differs from one of them in case only is not selected.
      - Pure1 has been seen to name one array of a pod that is on two arrays
        in one answer and both in the next. A link of such a pod can then be
        selected by one task and not by the next.
    type: list
    elements: str
  array_role:
    description:
      - The side of a link that the arrays of I(array) must be on.
      - C(source) keeps the links that have a source on one of the arrays,
        C(target) those that have a target on one of them and C(any) both.
      - A bucket link that replicates in both directions has each bucket as
        a source and as a target, so its arrays match either role.
      - C(source) and C(target) need I(array) with at least one name. The
        task fails without it and with an empty list.
      - It does not narrow the replication targets.
    type: str
    choices: [ any, source, target ]
    default: any
  name:
    description:
      - Collect only the links of the pods, file systems or buckets with
        these names. A link is returned when one of its members, which are
        its sources and its targets, has one of the names.
      - Without it, or with an empty list, the links are not narrowed by
        name.
      - The names are matched without regard to case.
      - A name that matches no link is not an error. The module gives a
        warning for it.
      - It does not narrow the replication targets.
    type: list
    elements: str
  replication_targets:
    description:
      - Collect the object replication targets of FlashBlades as well.
    type: bool
    default: true
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect every replica link and replication target
  everpure.pure1.pure1_replica_links:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: replication

- name: Show the summary of the links and of the replication targets
  ansible.builtin.debug:
    msg: "{{ replication['pure1_replica_links']['summary'] }}"

- name: Name the replication targets that are not connected
  ansible.builtin.debug:
    msg: "{{ replication['pure1_replica_links']['summary']['replication_targets']['not_connected'] }}"
  when: replication['pure1_replica_links']['summary']['replication_targets']['not_connected'] | length > 0

- name: Collect the pod replica links that array1 is the source of
  everpure.pure1.pure1_replica_links:
    link_type:
      - pod
    array:
      - array1
    array_role: source
    replication_targets: false
  register: outbound

- name: Check before a disaster recovery test that Pure1 shows no pod link in trouble
  ansible.builtin.assert:
    that:
      - outbound['pure1_replica_links']['summary']['pods']['total'] > 0
      - outbound['pure1_replica_links']['summary']['pods']['paused'] == 0
      - outbound['pure1_replica_links']['summary']['pods']['unhealthy'] == 0
      - outbound['pure1_replica_links']['summary']['pods']['never_replicated'] == 0
      - outbound['pure1_replica_links']['summary']['pods']['max_as_of_age_seconds'] < 3600
    fail_msg: Pure1 shows no pod replica link with array1 as source, or one that is paused, unhealthy, new or stale

- name: Collect the replica links of two file systems
  everpure.pure1.pure1_replica_links:
    link_type:
      - file_system
    name:
      - fs1
      - fs2
    replication_targets: false
  register: fs_links

- name: Show the targets of the file-system links that have never replicated
  ansible.builtin.debug:
    msg: "{{ item['targets'] }}"
  loop: "{{ fs_links['pure1_replica_links']['file_systems'] | selectattr('never_replicated') | list }}"
"""

RETURN = r"""
pure1_replica_links:
  description: The replica links and the replication targets collected from Pure1
  returned: always
  type: dict
  contains:
    pods:
      description:
        - The ActiveDR pod replica links, ordered by the array and the name
          of their source.
        - Empty when Pure1 has none, when none is selected, and when
          I(link_type) leaves out C(pod).
        - A key is null when Pure1 reports no value for it.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the link in Pure1.
          type: str
        sources:
          description: The source pods of the link.
          type: list
          elements: dict
          contains:
            name:
              description: The name of the pod. Null when Pure1 has no name for it.
              type: str
            id:
              description: The ID of the pod in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The arrays that the pod is on. Empty when Pure1 names none.
                - Pure1 has been seen to name one array of a pod that is on
                  two arrays in one answer and both in the next, which can
                  also move the link in the list.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        targets:
          description: The target pods of the link.
          type: list
          elements: dict
          contains:
            name:
              description: The name of the pod. Null when Pure1 has no name for it.
              type: str
            id:
              description: The ID of the pod in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The arrays that the pod is on. Empty when Pure1 names none.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        bidirectional:
          description: Whether a pod of the link is a source and a target of it.
          type: bool
        status:
          description:
            - The status of the link as Pure1 reports it, for example
              C(replicating), C(idle), C(baselining), C(paused), C(quiescing),
              C(quiesced) or C(unhealthy).
            - Pure1 can report C(replicating) for a link that it has not
              refreshed for a long time. See C(as_of_age_seconds).
          type: str
        status_details:
          description: Always null. Pure1 has no status details for a pod replica link.
          type: str
        paused:
          description: Whether the link is paused.
          type: bool
        lag:
          description:
            - How far the target is behind the source, in milliseconds, as
              Pure1 reports it.
          type: int
        lag_seconds:
          description: The same in whole seconds.
          type: int
        recovery_point:
          description:
            - The time of the last replicated data, which is what the target
              pod holds when it is promoted, as C(YYYY-MM-DD HH:MM:SS UTC).
            - Null when Pure1 has no recovery point for the link.
          type: str
        recovery_point_epoch:
          description:
            - The same in milliseconds since the epoch, as Pure1 sends it.
              Pure1 sends 0 or nothing when it has no recovery point.
          type: int
        never_replicated:
          description:
            - True when Pure1 has no recovery point for the link, as for a
              link that is still baselining.
            - Such a link is left out of the maximum values of the summary.
          type: bool
        as_of:
          description: The time at which Pure1 last refreshed the link, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same in milliseconds since the epoch.
          type: int
        as_of_age_seconds:
          description:
            - The seconds between that time and the time the module ran, by
              the clock of the host that ran it.
          type: int
    file_systems:
      description:
        - The file-system replica links of FlashBlades, ordered by the array
          and the name of their source.
        - Empty when Pure1 has none, when none is selected, and when
          I(link_type) leaves out C(file_system).
        - A key is null when Pure1 reports no value for it.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the link in Pure1.
          type: str
        sources:
          description: The source file systems of the link.
          type: list
          elements: dict
          contains:
            name:
              description:
                - The name of the file system. Null when Pure1 has no name
                  for it, which has been seen on the source of a link that
                  never replicated.
              type: str
            id:
              description: The ID of the file system in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The array that the file system is on. Empty when Pure1
                  names none.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        targets:
          description: The target file systems of the link.
          type: list
          elements: dict
          contains:
            name:
              description: The name of the file system. Null when Pure1 has no name for it.
              type: str
            id:
              description: The ID of the file system in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The array that the file system is on. Empty when Pure1
                  names none.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        bidirectional:
          description: Whether a file system of the link is a source and a target of it.
          type: bool
        status:
          description:
            - The status of the link as Pure1 reports it, for example
              C(replicating), C(idle) or C(unhealthy).
          type: str
        status_details:
          description: What Pure1 reports about the status of a link that is unhealthy.
          type: str
        paused:
          description: Whether the link is paused.
          type: bool
        lag:
          description:
            - How far the target is behind the source, in milliseconds, as
              Pure1 reports it.
            - It has been seen to be the time between C(recovery_point) and
              C(as_of), and never under an hour on a link that replicates.
              For a link that never replicated it is then the time since
              1970.
          type: int
        lag_seconds:
          description: The same in whole seconds.
          type: int
        recovery_point:
          description:
            - The time at which the last replicated snapshot was created,
              as C(YYYY-MM-DD HH:MM:SS UTC).
            - Null when Pure1 has no recovery point for the link.
          type: str
        recovery_point_epoch:
          description:
            - The same in milliseconds since the epoch, as Pure1 sends it.
              Pure1 sends 0 or nothing when it has no recovery point.
          type: int
        never_replicated:
          description:
            - True when Pure1 has no recovery point for the link.
            - Such a link is left out of the maximum values of the summary.
          type: bool
        as_of:
          description: The time at which Pure1 last refreshed the link, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same in milliseconds since the epoch.
          type: int
        as_of_age_seconds:
          description:
            - The seconds between that time and the time the module ran, by
              the clock of the host that ran it.
          type: int
        policies:
          description:
            - The names of the policies of the link, in alphabetical order.
              Empty when the link has none.
          type: list
          elements: str
    buckets:
      description:
        - The bucket replica links of FlashBlades, ordered by the array and
          the name of their source.
        - Empty when Pure1 has none, when none is selected, and when
          I(link_type) leaves out C(bucket).
        - A key is null when Pure1 reports no value for it.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the link in Pure1.
          type: str
        sources:
          description:
            - The source buckets of the link. A link that replicates in both
              directions has both buckets here and under C(targets).
          type: list
          elements: dict
          contains:
            name:
              description: The name of the bucket. Null when Pure1 has no name for it.
              type: str
            id:
              description: The ID of the bucket in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The array that the bucket is on. Empty when Pure1 names
                  none.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        targets:
          description: The target buckets of the link.
          type: list
          elements: dict
          contains:
            name:
              description: The name of the bucket. Null when Pure1 has no name for it.
              type: str
            id:
              description: The ID of the bucket in Pure1. Null when Pure1 has none for it.
              type: str
            arrays:
              description:
                - The array that the bucket is on. Empty when Pure1 names
                  none.
              type: list
              elements: dict
              contains:
                name:
                  description: The name of the array.
                  type: str
                fqdn:
                  description: The fully qualified domain name of the array.
                  type: str
        bidirectional:
          description:
            - Whether a bucket of the link is a source and a target of it,
              which is a link that replicates in both directions.
          type: bool
        status:
          description:
            - The status of the link as Pure1 reports it, for example
              C(replicating), C(idle) or C(unhealthy).
          type: str
        status_details:
          description: What Pure1 reports about the status of a link that is unhealthy.
          type: str
        paused:
          description: Whether the link is paused.
          type: bool
        lag:
          description:
            - How far the target is behind the source, in milliseconds, as
              Pure1 reports it.
          type: int
        lag_seconds:
          description: The same in whole seconds.
          type: int
        recovery_point:
          description:
            - The time of the last replicated data, as
              C(YYYY-MM-DD HH:MM:SS UTC). It has been seen about an hour
              behind C(as_of) on links whose C(lag) was a minute or two.
            - Null when Pure1 has no recovery point for the link.
          type: str
        recovery_point_epoch:
          description:
            - The same in milliseconds since the epoch, as Pure1 sends it.
              Pure1 sends 0 or nothing when it has no recovery point.
          type: int
        never_replicated:
          description:
            - True when Pure1 has no recovery point for the link.
            - Such a link is left out of the maximum values of the summary.
          type: bool
        as_of:
          description: The time at which Pure1 last refreshed the link, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same in milliseconds since the epoch.
          type: int
        as_of_age_seconds:
          description:
            - The seconds between that time and the time the module ran, by
              the clock of the host that ran it.
          type: int
    replication_targets:
      description:
        - The object replication targets of FlashBlades, as a dictionary
          keyed by array name.
        - The value for an array is a list with one dictionary per target,
          ordered by name, with the keys listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when Pure1 has none and when I(replication_targets) is
          false.
        - Where two arrays have one name, the array that Pure1 lists first
          keeps the name. The targets of the other one are left out, and
          the module gives a warning that counts them.
      type: dict
      contains:
        name:
          description: The name of the target.
          type: str
        id:
          description: The ID of the target in Pure1.
          type: str
        address:
          description: The IP address or the fully qualified domain name of the target system.
          type: str
        status:
          description: The status of the connection, for example C(connected) or C(connecting).
          type: str
        status_details:
          description: What Pure1 reports about a connection that is in trouble.
          type: str
        as_of:
          description: The time at which Pure1 last refreshed the target, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: The same in milliseconds since the epoch.
          type: int
    summary:
      description:
        - Counts over the links and the replication targets of the result.
      type: dict
      contains:
        pods:
          description: Counts over the links under C(pods).
          type: dict
          contains:
            total:
              description: The number of links.
              type: int
            by_status:
              description:
                - The number of links of each status, as a dictionary keyed
                  by status. A link for which Pure1 reports no status is not
                  counted here.
              type: dict
            paused:
              description: The number of links that are paused.
              type: int
            unhealthy:
              description: The number of links whose status is C(unhealthy).
              type: int
            never_replicated:
              description: The number of links for which Pure1 has no recovery point.
              type: int
            max_lag_seconds:
              description:
                - The largest C(lag_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
            max_as_of_age_seconds:
              description:
                - The largest C(as_of_age_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
        file_systems:
          description: Counts over the links under C(file_systems).
          type: dict
          contains:
            total:
              description: The number of links.
              type: int
            by_status:
              description:
                - The number of links of each status, as a dictionary keyed
                  by status. A link for which Pure1 reports no status is not
                  counted here.
              type: dict
            paused:
              description: The number of links that are paused.
              type: int
            unhealthy:
              description: The number of links whose status is C(unhealthy).
              type: int
            never_replicated:
              description: The number of links for which Pure1 has no recovery point.
              type: int
            max_lag_seconds:
              description:
                - The largest C(lag_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
            max_as_of_age_seconds:
              description:
                - The largest C(as_of_age_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
        buckets:
          description: Counts over the links under C(buckets).
          type: dict
          contains:
            total:
              description: The number of links.
              type: int
            by_status:
              description:
                - The number of links of each status, as a dictionary keyed
                  by status. A link for which Pure1 reports no status is not
                  counted here.
              type: dict
            paused:
              description: The number of links that are paused.
              type: int
            unhealthy:
              description: The number of links whose status is C(unhealthy).
              type: int
            never_replicated:
              description: The number of links for which Pure1 has no recovery point.
              type: int
            max_lag_seconds:
              description:
                - The largest C(lag_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
            max_as_of_age_seconds:
              description:
                - The largest C(as_of_age_seconds) of the links that have a
                  recovery point. Null when there is none.
              type: int
        replication_targets:
          description: Counts over the targets under C(replication_targets).
          type: dict
          contains:
            total:
              description: The number of targets.
              type: int
            not_connected:
              description:
                - The names of the targets whose status is not C(connected),
                  in alphabetical order, one entry for each target.
              type: list
              elements: str
  sample:
    pods:
      - id: 5d1d8a2b-0a35-4f0e-9c7c-2f2f6f3a1b10
        sources:
          - name: pod1
            id: 0b7d2c2e-6a51-4e6c-8d57-0c6f4f0e2a11
            arrays:
              - name: array1
                fqdn: array1.example.com
        targets:
          - name: pod1-dr
            id: 9f3b6f0c-1d0e-4b53-a2a4-7e1c5a8d3c12
            arrays:
              - name: array2
                fqdn: array2.example.com
        bidirectional: false
        status: replicating
        status_details: null
        paused: false
        lag: 127537
        lag_seconds: 127
        recovery_point: 2021-02-03 13:01:57 UTC
        recovery_point_epoch: 1612357317463
        never_replicated: false
        as_of: 2021-02-03 13:04:05 UTC
        as_of_epoch: 1612357445000
        as_of_age_seconds: 27
    file_systems:
      - id: 2a6c1f7e-3b8d-4c1a-9e55-6d0b7a4c8e13
        sources:
          - name: fs1
            id: 7c4e9a1d-5f2b-4d3c-8a66-1e9c0b5d7f14
            arrays:
              - name: array3
                fqdn: array3.example.com
        targets:
          - name: fs1
            id: 3e8b5d2f-7a1c-4e9d-b077-4f2a6c8e0a15
            arrays:
              - name: array4
                fqdn: array4.example.com
        bidirectional: false
        status: idle
        status_details: null
        paused: false
        lag: 3964000
        lag_seconds: 3964
        recovery_point: 2021-02-03 11:58:01 UTC
        recovery_point_epoch: 1612353481000
        never_replicated: false
        as_of: 2021-02-03 13:04:05 UTC
        as_of_epoch: 1612357445000
        as_of_age_seconds: 1365
        policies:
          - policy1
    buckets: []
    replication_targets:
      array3:
        - name: target1
          id: 6b0f3c9a-2e7d-4a5b-9c88-5a3d7e1f2b16
          address: 192.0.2.10
          status: connecting
          status_details: Error in SSL Handshake.
          as_of: 2021-02-03 13:04:05 UTC
          as_of_epoch: 1612357445000
    summary:
      pods:
        total: 1
        by_status:
          replicating: 1
        paused: 0
        unhealthy: 0
        never_replicated: 0
        max_lag_seconds: 127
        max_as_of_age_seconds: 27
      file_systems:
        total: 1
        by_status:
          idle: 1
        paused: 0
        unhealthy: 0
        never_replicated: 0
        max_lag_seconds: 3964
        max_as_of_age_seconds: 1365
      buckets:
        total: 0
        by_status: {}
        paused: 0
        unhealthy: 0
        never_replicated: 0
        max_lag_seconds: null
        max_as_of_age_seconds: null
      replication_targets:
        total: 1
        not_connected:
          - target1
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)
import time

# The key of the result for each link type, in the order they are read
LINK_KEYS = {"pod": "pods", "file_system": "file_systems", "bucket": "buckets"}
# The fields of a link that hold the members of each value of array_role
ROLE_MEMBERS = {
    "source": ("sources",),
    "target": ("targets",),
    "any": ("sources", "targets", "members"),
}


def list_all(module, action, request, kind):
    """Return every item of a listing, with a warning when Pure1 sent fewer"""
    listing = pure1_list(module, action, request)
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items), total, kind
            )
        )
    return listing.items


def list_links(module, pure_1, link_type, names):
    """Return the links of one type, those with a member on an array of these names

    Pure1 compares the names whatever their case: select_links() keeps the
    links of the arrays themselves.
    """
    if link_type == "pod":
        # A pod has a list of locations, a file system or a bucket has one
        pod_filter = filter_in("members.locations.name", names)
        return list_all(
            module,
            "get_pod_replica_links",
            lambda **page: pure_1.get_pod_replica_links(filter=pod_filter, **page),
            "pod replica links",
        )
    member_filter = filter_in("members.location.name", names)
    if link_type == "file_system":
        return list_all(
            module,
            "get_file_system_replica_links",
            lambda **page: pure_1.get_file_system_replica_links(
                filter=member_filter, **page
            ),
            "file-system replica links",
        )
    return list_all(
        module,
        "get_bucket_replica_links",
        lambda **page: pure_1.get_bucket_replica_links(filter=member_filter, **page),
        "bucket replica links",
    )


def member_locations(member):
    """Return the references to the arrays that a member of a link is on"""
    locations = pure1_attr(member, "locations")
    if locations is None:
        locations = [pure1_attr(member, "location")]
    return locations


def member_arrays(member):
    """Return the arrays that a member of a link is on, each as name and fqdn"""
    arrays = []
    for location in member_locations(member):
        name = pure1_attr(location, "name")
        fqdn = pure1_attr(location, "fqdn")
        # A reference that Pure1 left out arrives with no field set
        if name is not None or fqdn is not None:
            arrays.append({"name": name, "fqdn": fqdn})
    return arrays


def is_one_of(arrays, reference):
    """Tell whether a reference to an array is to one of the arrays of the task

    arrays are the arrays as pure1_resolve_arrays() returns them. The id
    tells an array from another one of the same name, or of that name in
    another case. Where Pure1 sent no id, the name must be the same as
    Pure1 spells it.
    """
    reference_id = pure1_attr(reference, "id")
    name = pure1_attr(reference, "name")
    for array in arrays:
        if reference_id is not None and array["id"] is not None:
            if reference_id == array["id"]:
                return True
        elif name is not None and name == array["name"]:
            return True
    return False


def keeps_name(owners, name, array_id):
    """Tell whether an array is the one that has its name in the result

    A result that is keyed by array name has one array under a name: the
    array that is seen first keeps it, and owners holds its id by the name.
    Arrays are told apart by their ids, so a reference without an id is
    taken for the array that has the name.
    """
    owner = owners.setdefault(name, array_id)
    if owner is None:
        owners[name] = array_id
    return owner is None or array_id is None or owner == array_id


def link_members(link, role):
    """Return the sources, the targets or the members of a link"""
    return [
        {
            "name": pure1_attr(member, "name"),
            "id": pure1_attr(member, "id"),
            "arrays": member_arrays(member),
        }
        for member in pure1_attr(link, role, [])
        if member is not None
    ]


def on_arrays(link, role, arrays):
    """Tell whether a member on that side of a link is on one of the arrays"""
    return any(
        is_one_of(arrays, location)
        for field in ROLE_MEMBERS[role]
        for member in pure1_attr(link, field, [])
        for location in member_locations(member)
    )


def link_row(link, sources, targets, now):
    """Return a link as it is reported; now is in milliseconds since the epoch"""
    lag = pure1_attr(link, "lag")
    recovery_point = pure1_attr(link, "recovery_point")
    as_of = pure1_attr(link, "as_of")
    source_ids = set(member["id"] for member in sources if member["id"] is not None)
    return {
        "id": pure1_attr(link, "id"),
        "sources": sources,
        "targets": targets,
        "bidirectional": any(member["id"] in source_ids for member in targets),
        "status": pure1_attr(link, "status"),
        "status_details": pure1_attr(link, "status_details"),
        "paused": pure1_attr(link, "paused"),
        "lag": lag,
        "lag_seconds": None if lag is None else lag // 1000,
        # Pure1 sends 0 for a link that has no recovery point
        "recovery_point": pure1_time(recovery_point) if recovery_point else None,
        "recovery_point_epoch": recovery_point,
        "never_replicated": not recovery_point,
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "as_of_age_seconds": None if as_of is None else (now - as_of) // 1000,
    }


def order_of(row):
    """Return the sort key of a link: the array and the name of its source first"""
    key = []
    for members in (row["sources"], row["targets"]):
        first = members[0] if members else {"name": None, "arrays": []}
        array = first["arrays"][0]["name"] if first["arrays"] else None
        key.extend([array or "", first["name"] or ""])
    key.append(row["id"] or "")
    return key


def select_links(module, links, arrays, matched, now):
    """Return the rows of the links that the array, array_role and name options select

    arrays holds the arrays of the task as pure1_resolve_arrays() returns
    them, or is None. The names of the name option that a link has are
    added to matched.
    """
    names = module.params["name"]
    wanted = set(name.lower() for name in names) if names else None
    rows = []
    for link in links:
        if arrays is not None and not on_arrays(
            link, module.params["array_role"], arrays
        ):
            continue
        sources = link_members(link, "sources")
        targets = link_members(link, "targets")
        if wanted is not None:
            found = wanted.intersection(
                member["name"].lower()
                for member in sources + targets + link_members(link, "members")
                if member["name"] is not None
            )
            if not found:
                continue
            matched.update(found)
        rows.append(link_row(link, sources, targets, now))
    return sorted(rows, key=order_of)


def add_policies(module, pure_1, rows):
    """Give each file-system link the names of its policies"""
    for row in rows:
        row["policies"] = []
    if not rows:
        return

    def all_memberships(**page):
        return pure_1.get_policies_file_system_replica_links(**page)

    by_link = {}
    left_out = 0
    for membership in list_all(
        module,
        "get_policies_file_system_replica_links",
        all_memberships,
        "policies of file-system replica links",
    ):
        link_id = pure1_attr(membership, "member.id")
        policy = pure1_attr(membership, "policy.name")
        if link_id is None or policy is None:
            left_out += 1
        elif policy not in by_link.setdefault(link_id, []):
            by_link[link_id].append(policy)
    if left_out:
        module.warn(
            "Policies of file-system replica links that Pure1 returned without "
            "a link or without a policy name are left out of the result: "
            "{0}.".format(left_out)
        )
    for row in rows:
        row["policies"] = sorted(by_link.get(row["id"], []))


def summarise_links(rows):
    """Return the counts over the links of one type"""
    by_status = {}
    for row in rows:
        if row["status"] is not None:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    # A link without a recovery point has no lag that means anything
    replicated = [row for row in rows if not row["never_replicated"]]
    lags = [row["lag_seconds"] for row in replicated if row["lag_seconds"] is not None]
    ages = [
        row["as_of_age_seconds"]
        for row in replicated
        if row["as_of_age_seconds"] is not None
    ]
    return {
        "total": len(rows),
        "by_status": by_status,
        "paused": len([row for row in rows if row["paused"]]),
        "unhealthy": by_status.get("unhealthy", 0),
        "never_replicated": len(rows) - len(replicated),
        "max_lag_seconds": max(lags) if lags else None,
        "max_as_of_age_seconds": max(ages) if ages else None,
    }


def generate_targets_dict(module, pure_1, arrays):
    """Return the replication targets by array, and the counts over them

    arrays holds the arrays to report as pure1_resolve_arrays() returns
    them, or is None for every array.
    """
    targets_info = {}
    summary = {"total": 0, "not_connected": []}
    if not module.params["replication_targets"]:
        return targets_info, summary
    # Pure1 compares the names whatever their case: is_one_of() keeps the
    # targets of the arrays themselves
    target_filter = filter_in("arrays.name", [array["name"] for array in arrays or []])
    targets = list_all(
        module,
        "get_targets",
        lambda **page: pure_1.get_targets(filter=target_filter, **page),
        "replication targets",
    )
    owners = {}
    left_out = 0
    other_array = 0
    for target in targets:
        name = pure1_attr(target, "name")
        references = [
            reference
            for reference in pure1_attr(target, "arrays", [])
            if pure1_attr(reference, "name") is not None
        ]
        if name is None or not references:
            left_out += 1
            continue
        # A target is filed once under an array, however often Pure1 names it
        on = []
        others = set()
        for reference in references:
            if arrays is not None and not is_one_of(arrays, reference):
                continue
            array = pure1_attr(reference, "name")
            array_id = pure1_attr(reference, "id")
            if not keeps_name(owners, array, array_id):
                others.add((array, array_id))
            elif array not in on:
                on.append(array)
        other_array += len(others)
        if not on:
            continue
        as_of = pure1_attr(target, "as_of")
        row = {
            "name": name,
            "id": pure1_attr(target, "id"),
            "address": pure1_attr(target, "address"),
            "status": pure1_attr(target, "status"),
            "status_details": pure1_attr(target, "status_details"),
            "as_of": pure1_time(as_of),
            "as_of_epoch": as_of,
        }
        for array in on:
            targets_info.setdefault(array, []).append(row)
        summary["total"] += 1
        if row["status"] != "connected":
            summary["not_connected"].append(name)
    if left_out:
        module.warn(
            "Replication targets that Pure1 returned without a name or without "
            "an array are left out of the result: {0}.".format(left_out)
        )
    if other_array:
        module.warn(
            "Replication targets of an array that has the name of another "
            "array are left out of the result: {0}.".format(other_array)
        )
    for rows in targets_info.values():
        rows.sort(key=lambda row: (row["name"], row["id"] or ""))
    summary["not_connected"].sort()
    return targets_info, summary


def generate_replica_links_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    names = [array["name"] for array in arrays] if arrays else None
    if names is None and module.params["array_role"] != "any":
        module.fail_json(
            msg="array_role is {0} but array names no array".format(
                module.params["array_role"]
            )
        )
    links = {}
    wanted = module.params["link_type"] or list(LINK_KEYS)
    for link_type, key in LINK_KEYS.items():
        if link_type in wanted:
            links[key] = list_links(module, pure_1, link_type, names)
    # The age of a link is measured after the last of them is read
    now = int(time.time() * 1000)
    matched = set()
    info = {}
    for key in LINK_KEYS.values():
        info[key] = select_links(module, links.get(key, []), arrays, matched, now)
    add_policies(module, pure_1, info["file_systems"])
    summary = dict((key, summarise_links(info[key])) for key in LINK_KEYS.values())
    unmatched = []
    for name in module.params["name"] or []:
        if name.lower() not in matched and name not in unmatched:
            unmatched.append(name)
    if unmatched:
        text = (
            "The name {0} matches no member of the selected replica links: "
            "the result holds no link for it."
        )
        if len(unmatched) > 1:
            text = (
                "The names {0} match no member of the selected replica links: "
                "the result holds no link for them."
            )
        module.warn(text.format(", ".join(unmatched)))
    info["replication_targets"], summary["replication_targets"] = generate_targets_dict(
        module, pure_1, arrays
    )
    info["summary"] = summary
    return info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            link_type=dict(
                type="list",
                elements="str",
                choices=["pod", "file_system", "bucket"],
                default=["pod", "file_system", "bucket"],
            ),
            array=dict(type="list", elements="str"),
            array_role=dict(
                type="str", choices=["any", "source", "target"], default="any"
            ),
            name=dict(type="list", elements="str"),
            replication_targets=dict(type="bool", default=True),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[
            ["array_role", "source", ["array"]],
            ["array_role", "target", ["array"]],
        ],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)

    module.exit_json(
        changed=False,
        pure1_replica_links=generate_replica_links_dict(module, pure_1),
    )


if __name__ == "__main__":
    main()
