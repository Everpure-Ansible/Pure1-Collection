#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_recommendations
version_added: '1.6.0'
short_description: Collect the recommendations of Pure1
description:
  - Collect what Pure1 recommends for the arrays and subscriptions it knows.
    These are hardware upgrades of FlashArrays, with the recommended model
    and the components at their end of life, upgrades of Evergreen//One
    subscription licenses, with the recommended service tier and reserve, and
    renewals of support contracts, with the contracts they cover.
  - Use it to open a ticket for each recommendation, to report the support
    contracts that are due for renewal, or to stop a playbook when the
    reserve of a license is about to be exceeded.
  - Pure1 makes the recommendations itself and names no schedule for it. The
    C(created) of a recommendation gives the time it was made and its
    C(as_of) the time of its data, which can be older than the state of the
    array.
options:
  type:
    description:
      - The type of recommendation to return.
      - With C(all) every list of the result is filled, which takes four
        requests to Pure1. With one of the types only the list of that type
        is filled, which takes one request, and the other lists are empty.
    type: str
    choices: [ all, flasharray_hardware_upgrade, license_upgrade, support_contract_renewal ]
    default: all
  array:
    description:
      - Names of the arrays to return the recommendations of. Every
        recommendation when not set or empty, also one that is for no array.
      - A name is matched without regard to case and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - A recommendation is returned when its target is one of these arrays.
        A support contract renewal is also returned when one of its support
        contracts is for one of them, and then with all its support
        contracts.
      - A license upgrade can have a subscription license as its target and
        not an array. It is then not returned when I(array) is set.
      - Pure1 cannot select a renewal by the arrays of its support contracts,
        so the module reads every recommendation of the type and selects
        among them itself. Naming arrays costs one more request to Pure1.
    type: list
    elements: str
notes:
  - This module makes the Pure1 REST 1.6 calls C(get_recommendations),
    C(get_recommendations_flasharray_hardware_upgrade),
    C(get_recommendations_license_upgrade) and
    C(get_recommendations_support_contract_renewal).
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect every recommendation of Pure1
  everpure.pure1.pure1_recommendations:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: recommendations

- name: Show the arrays that have a hardware upgrade recommended
  ansible.builtin.debug:
    msg: "{{ recommendations['pure1_recommendations']['flasharray_hardware_upgrade'] | map(attribute='target.name') | list }}"

- name: Collect the license upgrade recommendations
  everpure.pure1.pure1_recommendations:
    type: license_upgrade
  register: license_upgrades

- name: Check that no reserve is exceeded within 30 days
  ansible.builtin.assert:
    that:
      - license_upgrades['pure1_recommendations']['license_upgrade']
        | selectattr('current_reserve_exceeded_in_days', 'number')
        | selectattr('current_reserve_exceeded_in_days', 'lt', 30) | list | length == 0
    fail_msg: The reserve of a subscription license is exceeded within 30 days

- name: Collect the support contract renewals of two arrays
  everpure.pure1.pure1_recommendations:
    type: support_contract_renewal
    array:
      - array1
      - array2
  register: renewals

- name: Show when each support contract of these renewals expires
  ansible.builtin.debug:
    msg: "{{ item.target.name }}: {{ item.expires }}"
  loop: "{{ renewals['pure1_recommendations']['support_contract_renewal'] | map(attribute='support_contracts') | flatten }}"
"""

RETURN = r"""
pure1_recommendations:
  description:
    - The recommendations that Pure1 has, as one list for each type. Each list
      is ordered by the time the recommendations were made, the newest first.
    - A key of a recommendation is null when Pure1 reports no value for it,
      unless the description of the key says otherwise.
  returned: always
  type: dict
  contains:
    flasharray_hardware_upgrade:
      description:
        - The hardware upgrades that Pure1 recommends for FlashArrays, among
          them capacity upgrades, controller upgrades and the replacement of
          hardware at its end of life. Each recommendation has the keys that
          apply to it set.
        - Empty when Pure1 has none, and when I(type) asks for another type.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the recommendation in Pure1.
          type: str
        name:
          description: The name that Pure1 gave the recommendation.
          type: str
        description:
          description: What Pure1 recommends, in words.
          type: str
        recommendation_type:
          description:
            - The type of the recommendation as Pure1 names it, here
              C(flasharray-hardware-upgrade).
          type: str
        created:
          description: The time the recommendation was made, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: The time the recommendation was made, in milliseconds since the epoch.
          type: int
        as_of:
          description:
            - The time of the data that Pure1 holds of the recommendation, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - The time of the data that Pure1 holds of the recommendation, in
              milliseconds since the epoch.
          type: int
        target:
          description:
            - What the recommendation is for, here an array.
            - Always a dictionary. Its keys are null when Pure1 names no target.
          type: dict
          contains:
            id:
              description: The ID of the target in Pure1.
              type: str
            name:
              description: The name of the target.
              type: str
            resource_type:
              description: The kind of the target as Pure1 names it, C(arrays) for an array.
              type: str
        trigger:
          description: The name of the program of Pure1 that made the recommendation.
          type: str
        company:
          description: The name of the company the recommendation is for.
          type: str
        subscription_license:
          description:
            - The subscription license that the recommendation is linked to.
              Null when it is linked to none.
          type: dict
          contains:
            id:
              description: The ID of the subscription license in Pure1.
              type: str
            name:
              description: The name of the subscription license.
              type: str
        current_model:
          description: The model of the controllers the array has now.
          type: str
        recommended_model:
          description: The model of the controllers that Pure1 recommends.
          type: str
        current_installed_capacity:
          description: The raw capacity the array has installed now, in bytes.
          type: int
        recommended_installed_capacity:
          description: The raw capacity installed after the upgrade, in bytes.
          type: int
        current_usable_capacity:
          description: The usable capacity the array has now, in bytes.
          type: int
        recommended_usable_capacity:
          description: The usable capacity after the upgrade, in bytes.
          type: int
        recommended_full_sku:
          description: The SKU of the whole recommended configuration.
          type: str
        recommended_upgrade_sku:
          description: The SKU of what the upgrade adds to the array.
          type: str
        end_of_life_components:
          description:
            - The components of the array that are at their end of life or
              come near it. Empty when the recommendation is not about the
              end of life of hardware.
          type: list
          elements: dict
          contains:
            component_type:
              description:
                - The type of the component, for example C(CONTROLLER),
                  C(CHASSIS), C(SHELF) or C(DRIVE).
              type: str
            protocol:
              description:
                - The protocol of the component, for example C(SAS) or
                  C(NVME). Null for a component that has none.
              type: str
            end_of_life:
              description:
                - The time of the end of life of the component, in UTC, as
                  C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            end_of_life_epoch:
              description:
                - The time of the end of life of the component, in
                  milliseconds since the epoch.
              type: int
    license_upgrade:
      description:
        - The upgrades that Pure1 recommends for Evergreen//One subscription
          licenses, among them a larger reserve where the use is about to
          exceed it and another service tier. Each recommendation has the
          keys that apply to it set.
        - Empty when Pure1 has none, and when I(type) asks for another type.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the recommendation in Pure1.
          type: str
        name:
          description: The name that Pure1 gave the recommendation.
          type: str
        description:
          description: What Pure1 recommends, in words.
          type: str
        recommendation_type:
          description:
            - The type of the recommendation as Pure1 names it, here
              C(license-upgrade).
          type: str
        created:
          description: The time the recommendation was made, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: The time the recommendation was made, in milliseconds since the epoch.
          type: int
        as_of:
          description:
            - The time of the data that Pure1 holds of the recommendation, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - The time of the data that Pure1 holds of the recommendation, in
              milliseconds since the epoch.
          type: int
        target:
          description:
            - What the recommendation is for, which can be an array or a
              subscription license.
            - Always a dictionary. Its keys are null when Pure1 names no target.
          type: dict
          contains:
            id:
              description: The ID of the target in Pure1.
              type: str
            name:
              description: The name of the target.
              type: str
            resource_type:
              description: The kind of the target as Pure1 names it, C(arrays) for an array.
              type: str
        trigger:
          description: The name of the program of Pure1 that made the recommendation.
          type: str
        company:
          description: The name of the company the recommendation is for.
          type: str
        subscription_license:
          description:
            - The subscription license that the recommendation is linked to.
              Null when it is linked to none.
          type: dict
          contains:
            id:
              description: The ID of the subscription license in Pure1.
              type: str
            name:
              description: The name of the subscription license.
              type: str
        current_service_tier:
          description: The service tier the license has now.
          type: str
        recommended_service_tier:
          description:
            - The service tier that Pure1 recommends. Null when only a larger
              reserve is recommended.
          type: str
        current_reserved_amount:
          description: The reserve the license has now, in the unit that C(reserved_unit) names.
          type: float
        recommended_reserved_amount:
          description: The reserve that Pure1 recommends, in the unit that C(reserved_unit) names.
          type: float
        reserved_unit:
          description:
            - The unit of C(current_reserved_amount) and
              C(recommended_reserved_amount), for example C(TiB) or C(VMs).
          type: str
        current_reserve_exceeded_in_days:
          description:
            - The number of days after which Pure1 expects the use to exceed
              the reserve the license has now.
          type: int
        current_iops_sla:
          description:
            - The IOPS that the service tier of the license commits to now.
              Null for a tier without such a commitment.
          type: float
        recommended_iops_sla:
          description:
            - The IOPS that the recommended service tier commits to. Null
              for a tier without such a commitment.
          type: float
        current_bandwidth_sla_bytes_per_sec:
          description:
            - The bandwidth that the service tier of the license commits to
              now, in bytes per second.
          type: float
        recommended_bandwidth_sla_bytes_per_sec:
          description:
            - The bandwidth that the recommended service tier commits to, in
              bytes per second.
          type: float
    support_contract_renewal:
      description:
        - The renewals that Pure1 recommends for support contracts that come
          near their end, each with the support contracts it covers.
        - Empty when Pure1 has none, and when I(type) asks for another type.
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the recommendation in Pure1.
          type: str
        name:
          description: The name that Pure1 gave the recommendation.
          type: str
        description:
          description: What Pure1 recommends, in words.
          type: str
        recommendation_type:
          description:
            - The type of the recommendation as Pure1 names it, here
              C(support-contract-renewal).
          type: str
        created:
          description: The time the recommendation was made, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: The time the recommendation was made, in milliseconds since the epoch.
          type: int
        as_of:
          description:
            - The time of the data that Pure1 holds of the recommendation, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - The time of the data that Pure1 holds of the recommendation, in
              milliseconds since the epoch.
          type: int
        target:
          description:
            - What the recommendation is for.
            - Always a dictionary. Its keys are null when Pure1 names no target.
          type: dict
          contains:
            id:
              description: The ID of the target in Pure1.
              type: str
            name:
              description: The name of the target.
              type: str
            resource_type:
              description: The kind of the target as Pure1 names it, C(arrays) for an array.
              type: str
        trigger:
          description: The name of the program of Pure1 that made the recommendation.
          type: str
        company:
          description: The name of the company the recommendation is for.
          type: str
        subscription_license:
          description:
            - The subscription license that the recommendation is linked to.
              Null when it is linked to none.
          type: dict
          contains:
            id:
              description: The ID of the subscription license in Pure1.
              type: str
            name:
              description: The name of the subscription license.
              type: str
        partner_name:
          description:
            - The name of the partner the renewal goes through. Null when it
              goes through none.
          type: str
        subscription_term_months:
          description: The length of the term that Pure1 proposes, in months.
          type: int
        support_contracts:
          description:
            - The support contracts that the renewal covers. Empty when Pure1
              names none.
          type: list
          elements: dict
          contains:
            target:
              description:
                - What the support contract covers, usually an array.
                - Always a dictionary. Its keys are null when Pure1 names no
                  target.
              type: dict
              contains:
                id:
                  description: The ID of the target in Pure1.
                  type: str
                name:
                  description: The name of the target.
                  type: str
                resource_type:
                  description: The kind of the target as Pure1 names it, C(arrays) for an array.
                  type: str
            target_model:
              description: The model of the target, where it is an appliance.
              type: str
            expires:
              description: The time the support contract ends, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            expires_epoch:
              description: The time the support contract ends, in milliseconds since the epoch.
              type: int
            subscription_evergreen_tier:
              description: The Evergreen tier of the subscription of the target.
              type: str
    other:
      description:
        - The recommendations of a type that has no list of its own, which
          are those of a type that Pure1 added after this module was written
          and those that Pure1 gives no type. They have the keys that every
          recommendation has.
        - Empty when Pure1 has none, and when I(type) is not C(all).
      type: list
      elements: dict
      contains:
        id:
          description: The ID of the recommendation in Pure1.
          type: str
        name:
          description: The name that Pure1 gave the recommendation.
          type: str
        description:
          description: What Pure1 recommends, in words.
          type: str
        recommendation_type:
          description: The type of the recommendation as Pure1 names it.
          type: str
        created:
          description: The time the recommendation was made, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: The time the recommendation was made, in milliseconds since the epoch.
          type: int
        as_of:
          description:
            - The time of the data that Pure1 holds of the recommendation, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - The time of the data that Pure1 holds of the recommendation, in
              milliseconds since the epoch.
          type: int
        target:
          description:
            - What the recommendation is for.
            - Always a dictionary. Its keys are null when Pure1 names no target.
          type: dict
          contains:
            id:
              description: The ID of the target in Pure1.
              type: str
            name:
              description: The name of the target.
              type: str
            resource_type:
              description: The kind of the target as Pure1 names it, C(arrays) for an array.
              type: str
        trigger:
          description: The name of the program of Pure1 that made the recommendation.
          type: str
        company:
          description: The name of the company the recommendation is for.
          type: str
        subscription_license:
          description:
            - The subscription license that the recommendation is linked to.
              Null when it is linked to none.
          type: dict
          contains:
            id:
              description: The ID of the subscription license in Pure1.
              type: str
            name:
              description: The name of the subscription license.
              type: str
  sample:
    flasharray_hardware_upgrade:
      - id: 0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d
        name: recommendation1
        description: Replace the controllers of array1 before their end of life
        recommendation_type: flasharray-hardware-upgrade
        created: 2026-11-02 08:30:00 UTC
        created_epoch: 1793608200000
        as_of: 2026-11-24 10:15:30 UTC
        as_of_epoch: 1795515330000
        target:
          id: 1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e
          name: array1
          resource_type: arrays
        trigger: program1
        company: company1
        subscription_license: null
        current_model: FA-X70R2
        recommended_model: FA-X70R4
        current_installed_capacity: 109951162777600
        recommended_installed_capacity: 219902325555200
        current_usable_capacity: 76965813944320
        recommended_usable_capacity: 153931627888640
        recommended_full_sku: sku1
        recommended_upgrade_sku: sku2
        end_of_life_components:
          - component_type: CONTROLLER
            protocol: null
            end_of_life: 2027-06-30 00:00:00 UTC
            end_of_life_epoch: 1814313600000
    license_upgrade:
      - id: 2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f
        name: recommendation2
        description: Raise the reserve of license1 before the use exceeds it
        recommendation_type: license-upgrade
        created: 2026-11-02 08:30:00 UTC
        created_epoch: 1793608200000
        as_of: 2026-11-24 10:15:30 UTC
        as_of_epoch: 1795515330000
        target:
          id: 1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e
          name: array1
          resource_type: arrays
        trigger: program1
        company: company1
        subscription_license:
          id: 3d4e5f6a-7b8c-4d9e-9f0a-2b3c4d5e6f7a
          name: license1
        current_service_tier: tier1
        recommended_service_tier: null
        current_reserved_amount: 100.0
        recommended_reserved_amount: 150.0
        reserved_unit: TiB
        current_reserve_exceeded_in_days: 45
        current_iops_sla: 2500.0
        recommended_iops_sla: 2500.0
        current_bandwidth_sla_bytes_per_sec: 100000000.0
        recommended_bandwidth_sla_bytes_per_sec: 100000000.0
    support_contract_renewal:
      - id: 4e5f6a7b-8c9d-4e0f-8a1b-3c4d5e6f7a8b
        name: recommendation3
        description: Renew the support contracts that end within the next months
        recommendation_type: support-contract-renewal
        created: 2026-11-02 08:30:00 UTC
        created_epoch: 1793608200000
        as_of: 2026-11-24 10:15:30 UTC
        as_of_epoch: 1795515330000
        target:
          id: 1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e
          name: array1
          resource_type: arrays
        trigger: program1
        company: company1
        subscription_license: null
        partner_name: partner1
        subscription_term_months: 36
        support_contracts:
          - target:
              id: 1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e
              name: array1
              resource_type: arrays
            target_model: FA-X70R2
            expires: 2027-03-31 00:00:00 UTC
            expires_epoch: 1806451200000
            subscription_evergreen_tier: tier1
    other: []
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
    pure1_time,
)

# The lists of the result that hold one type each, with the name Pure1 has
# for the type
TYPES = {
    "flasharray_hardware_upgrade": "flasharray-hardware-upgrade",
    "license_upgrade": "license-upgrade",
    "support_contract_renewal": "support-contract-renewal",
}
# The client methods of the module: all came with one py-pure-client release
METHODS = (
    "get_recommendations",
    "get_recommendations_flasharray_hardware_upgrade",
    "get_recommendations_license_upgrade",
    "get_recommendations_support_contract_renewal",
)


def warn_if_truncated(module, listing, kind):
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items), total, kind
            )
        )


def reference(item, path):
    """Return the reference an item has under a path, each key null if unset"""
    return {
        "id": pure1_attr(item, path + ".id"),
        "name": pure1_attr(item, path + ".name"),
        "resource_type": pure1_attr(item, path + ".resource_type"),
    }


def base_row(item):
    """Return what every recommendation has, whatever its type"""
    created = pure1_attr(item, "created_at")
    as_of = pure1_attr(item, "as_of")
    subscription_license = {
        "id": pure1_attr(item, "subscription_license.id"),
        "name": pure1_attr(item, "subscription_license.name"),
    }
    if subscription_license["id"] is None and subscription_license["name"] is None:
        # Pure1 names a license only where the recommendation has one
        subscription_license = None
    return {
        "id": pure1_attr(item, "id"),
        "name": pure1_attr(item, "name"),
        "description": pure1_attr(item, "description"),
        "recommendation_type": pure1_attr(item, "recommendation_type"),
        "created": pure1_time(created),
        "created_epoch": created,
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "target": reference(item, "target"),
        "trigger": pure1_attr(item, "trigger.name"),
        "company": pure1_attr(item, "company.name"),
        "subscription_license": subscription_license,
    }


def hardware_upgrade_row(item):
    row = base_row(item)
    for field in (
        "current_model",
        "recommended_model",
        "current_installed_capacity",
        "recommended_installed_capacity",
        "current_usable_capacity",
        "recommended_usable_capacity",
        "recommended_full_sku",
        "recommended_upgrade_sku",
    ):
        row[field] = pure1_attr(item, field)
    row["end_of_life_components"] = []
    for component in pure1_attr(item, "end_of_life_components", []):
        if component is None:
            continue
        end_of_life = pure1_attr(component, "end_of_life")
        row["end_of_life_components"].append(
            {
                "component_type": pure1_attr(component, "component_type"),
                "protocol": pure1_attr(component, "protocol"),
                "end_of_life": pure1_time(end_of_life),
                "end_of_life_epoch": end_of_life,
            }
        )
    return row


def license_upgrade_row(item):
    row = base_row(item)
    for field in (
        "current_service_tier",
        "recommended_service_tier",
        "current_reserved_amount",
        "recommended_reserved_amount",
        "reserved_unit",
        "current_reserve_exceeded_in_days",
        "current_iops_sla",
        "recommended_iops_sla",
        "current_bandwidth_sla_bytes_per_sec",
        "recommended_bandwidth_sla_bytes_per_sec",
    ):
        row[field] = pure1_attr(item, field)
    return row


def renewal_row(item):
    row = base_row(item)
    row["partner_name"] = pure1_attr(item, "partner_name")
    row["subscription_term_months"] = pure1_attr(item, "subscription_term_months")
    row["support_contracts"] = []
    for contract in pure1_attr(item, "support_contracts", []):
        if contract is None:
            continue
        expires = pure1_attr(contract, "expires")
        row["support_contracts"].append(
            {
                "target": reference(contract, "target"),
                "target_model": pure1_attr(contract, "target_model"),
                "expires": pure1_time(expires),
                "expires_epoch": expires,
                "subscription_evergreen_tier": pure1_attr(
                    contract, "subscription_evergreen_tier"
                ),
            }
        )
    return row


def list_recommendations(module, pure_1, kind):
    """Return the rows of one list of the result, of every array

    Pure1 takes one sort field only and no offset for a recommendation, so
    each listing is read unsorted and ordered here.
    """

    def hardware_upgrades(**page):
        return pure_1.get_recommendations_flasharray_hardware_upgrade(**page)

    def license_upgrades(**page):
        return pure_1.get_recommendations_license_upgrade(**page)

    def renewals(**page):
        return pure_1.get_recommendations_support_contract_renewal(**page)

    def every_type(**page):
        return pure_1.get_recommendations(**page)

    action, request, row, words = {
        "flasharray_hardware_upgrade": (
            "get_recommendations_flasharray_hardware_upgrade",
            hardware_upgrades,
            hardware_upgrade_row,
            "FlashArray hardware upgrade recommendations",
        ),
        "license_upgrade": (
            "get_recommendations_license_upgrade",
            license_upgrades,
            license_upgrade_row,
            "license upgrade recommendations",
        ),
        "support_contract_renewal": (
            "get_recommendations_support_contract_renewal",
            renewals,
            renewal_row,
            "support contract renewal recommendations",
        ),
        "other": ("get_recommendations", every_type, base_row, "recommendations"),
    }[kind]
    listing = pure1_list(module, action, request)
    warn_if_truncated(module, listing, words)
    rows = []
    listed = {}
    for item in listing.items:
        if item is None:
            # The SDK hands on a null that Pure1 sends among the items
            continue
        found = row(item)
        # The id tells the recommendations apart: one that Pure1 lists twice,
        # the same in every key, is returned once. Two of one id that differ
        # are both returned
        if found["id"] is not None:
            same_id = listed.setdefault(found["id"], [])
            if found in same_id:
                continue
            same_id.append(found)
        rows.append(found)
    return rows


def is_for(row, arrays):
    """Tell whether a recommendation is for one of the arrays of the task

    Its target is one of them, or the target of one of the support contracts
    of a renewal. A reference is to an array when it has the id of the array.
    It is also when it has the name of the array as Pure1 spells it, unless
    Pure1 gives it as another kind of object than an array: the subscription
    license that a license upgrade has for its target can carry the name of
    an array.
    """
    ids = {array["id"] for array in arrays}
    names = {array["name"] for array in arrays}
    targets = [row["target"]]
    targets += [contract["target"] for contract in row.get("support_contracts", [])]
    return any(
        (target["id"] is not None and target["id"] in ids)
        or (
            target["name"] is not None
            and target["name"] in names
            and target["resource_type"] in (None, "", "arrays")
        )
        for target in targets
    )


def newest_first(row):
    """Return what orders a list: the newest first, one without a time last"""
    created = row["created_epoch"]
    return (created is None, -(created or 0), row["id"] or "", row["name"] or "")


def generate_recommendations_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    wanted = module.params["type"]

    found = {}
    for kind in TYPES:
        found[kind] = []
        if wanted in ("all", kind):
            found[kind] = list_recommendations(module, pure_1, kind)
    found["other"] = []
    if wanted == "all":
        # Pure1 lists the recommendations of every type here, with what all
        # types have: a type that has a list of its own is complete there
        found["other"] = [
            row
            for row in list_recommendations(module, pure_1, "other")
            if row["recommendation_type"] not in TYPES.values()
        ]

    recommendations = {}
    for kind, rows in found.items():
        if arrays is not None:
            rows = [row for row in rows if is_for(row, arrays)]
        recommendations[kind] = sorted(rows, key=newest_first)
    return recommendations


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            type=dict(
                type="str",
                choices=[
                    "all",
                    "flasharray_hardware_upgrade",
                    "license_upgrade",
                    "support_contract_renewal",
                ],
                default="all",
            ),
            array=dict(type="list", elements="str"),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)
    for method in METHODS:
        pure1_require(module, pure_1, method, "1.99.0")

    module.exit_json(
        changed=False,
        pure1_recommendations=generate_recommendations_dict(module, pure_1),
    )


if __name__ == "__main__":
    main()
