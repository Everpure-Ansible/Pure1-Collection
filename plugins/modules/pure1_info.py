#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_info
version_added: '1.0.0'
short_description: Collect information from Pure1
description:
  - Collect information from Pure1.
  - By default, the module collects the C(minimum) subset, which is the number
    of appliances of each type and the number of volumes, volume snapshots,
    filesystems, filesystem snapshots, buckets, directories, pods and object
    store accounts. It is returned under the key C(default).
  - Additional information can be collected based on the configured set of arguments.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
options:
  gather_subset:
    description:
      - When supplied, this argument will define the information to be collected.
        Possible values for this include all, minimum, appliances, subscriptions,
        contracts, environmental and invoices.
      - The C(minimum) subset is returned under the key C(default) and the
        C(subscriptions) subset under the keys C(subscriptions) and
        C(subscription_licenses). Every other subset is returned under its own name.
    type: list
    elements: str
    required: false
    default: minimum
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect default set of information
  everpure.pure1.pure1_info:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: pure1_info

- name: Show default information
  ansible.builtin.debug:
    msg: "{{ pure1_info['pure1_info']['default'] }}"

- name: Collect all information
  everpure.pure1.pure1_info:
    gather_subset:
      - all
  register: pure1_info

- name: Show all information
  ansible.builtin.debug:
    msg: "{{ pure1_info['pure1_info'] }}"
"""

RETURN = r"""
pure1_info:
  description:
    - The information collected from Pure1, with one key for each section that the
      requested subsets fill.
    - A section that the API key is not permitted to read (status code 403) is returned
      empty with a warning. The C(invoices) section is returned empty with a warning
      for any error answer.
    - An array, subscription, license or resource of a license that Pure1 returns
      without a name, an invoice without a number, a tag without a key and an
      insight without an array name are left out of the sections that list them,
      with a warning that counts them.
  returned: always
  type: dict
  contains:
    default:
      description:
        - The C(minimum) subset, the number of appliances of each type and of the
          objects on them.
      returned: when I(gather_subset) includes C(minimum) or C(all)
      type: dict
      contains:
        FlashArrays:
          description: Number of appliances that run Purity//FA.
          type: int
        FlashBlades:
          description: Number of appliances that run Purity//FB.
          type: int
        ObjectEngines:
          description: Number of appliances that run Elasticity.
          type: int
        volumes:
          description: Number of volumes.
          type: int
        volume_snapshots:
          description: Number of volume snapshots.
          type: int
        filesystems:
          description: Number of filesystems.
          type: int
        filesystem_snapshots:
          description: Number of filesystem snapshots.
          type: int
        buckets:
          description: Number of buckets.
          type: int
        directories:
          description: Number of directories.
          type: int
        pods:
          description: Number of pods.
          type: int
        object_store_accounts:
          description: Number of object store accounts.
          type: int
      sample:
        FlashArrays: 2
        FlashBlades: 1
        ObjectEngines: 0
        volumes: 120
        volume_snapshots: 340
        filesystems: 12
        filesystem_snapshots: 48
        buckets: 6
        directories: 20
        pods: 4
        object_store_accounts: 3
    appliances:
      description:
        - The C(appliances) subset, the appliances of each type with their tags and
          their latest performance figures.
        - A performance figure is the latest value of the last five hours, where each
          value is the maximum of three minutes. Its key is left out when Pure1 has no
          value for the appliance, and with a warning when Pure1 refuses the request.
      returned: when I(gather_subset) includes C(appliances) or C(all)
      type: dict
      contains:
        FlashArray:
          description:
            - The appliances that run Purity//FA, keyed by array name.
            - Each array also holds its performance figures, under keys that hold
              spaces and brackets and so need bracket notation in a template.
            - The keys 'bandwidth (read) [MB/s]' and 'bandwidth (write) [MB/s]' hold
              bytes per second divided by 1048576 (MiB/s), rounded to three decimals.
            - The keys 'latency (read) [ms]' and 'latency (write) [ms]' hold
              milliseconds, rounded to two decimals.
            - The keys 'iops (read)' and 'iops (write)' hold operations per second,
              rounded to a whole number.
            - The key 'load [%]' holds the load of the array in percent, rounded to
              three decimals.
          type: dict
          contains:
            os_version:
              description: Purity version of the array.
              type: str
            model:
              description: Model of the array.
              type: str
            fqdn:
              description:
                - Fully qualified domain name of the array, an empty string when
                  Pure1 has none.
              type: str
            tags:
              description:
                - Tags of the array. Empty when the array has none, and with a
                  warning when Pure1 refuses to list them.
              type: list
              elements: dict
              contains:
                key:
                  description: Key of the tag.
                  type: str
                value:
                  description: Value of the tag, null when Pure1 has none.
                  type: str
                org_id:
                  description: ID of the organization of the tag, null when Pure1 has none.
                  type: int
                namespace:
                  description: Namespace of the tag.
                  type: str
        FlashBlade:
          description:
            - The appliances that run Purity//FB, keyed by array name.
            - Each array also holds its performance figures, under keys that hold
              spaces and brackets and so need bracket notation in a template.
            - The keys 'bandwidth (read) [MB/s]' and 'bandwidth (write) [MB/s]' hold
              bytes per second divided by 1048576 (MiB/s), rounded to three decimals.
            - The keys 'latency (read) [ms]' and 'latency (write) [ms]' hold
              milliseconds, rounded to two decimals.
            - The keys 'iops (read)' and 'iops (write)' hold operations per second,
              rounded to a whole number.
          type: dict
          contains:
            os_version:
              description: Purity version of the array.
              type: str
            model:
              description: Model of the array.
              type: str
            fqdn:
              description:
                - Fully qualified domain name of the array, an empty string when
                  Pure1 has none.
              type: str
            tags:
              description:
                - Tags of the array. Empty when the array has none, and with a
                  warning when Pure1 refuses to list them.
              type: list
              elements: dict
              contains:
                key:
                  description: Key of the tag.
                  type: str
                value:
                  description: Value of the tag, null when Pure1 has none.
                  type: str
                org_id:
                  description: ID of the organization of the tag, null when Pure1 has none.
                  type: int
                namespace:
                  description: Namespace of the tag.
                  type: str
        ObjectEngine:
          description:
            - The appliances that run Elasticity, keyed by appliance name. No
              performance figures are collected for them.
          type: dict
          contains:
            os_version:
              description: Version of the operating system of the appliance.
              type: str
            model:
              description: Model of the appliance.
              type: str
            fqdn:
              description:
                - Fully qualified domain name of the appliance, an empty string when
                  Pure1 has none.
              type: str
            tags:
              description:
                - Tags of the appliance. Empty when the appliance has none, and with
                  a warning when Pure1 refuses to list them.
              type: list
              elements: dict
              contains:
                key:
                  description: Key of the tag.
                  type: str
                value:
                  description: Value of the tag, null when Pure1 has none.
                  type: str
                org_id:
                  description: ID of the organization of the tag, null when Pure1 has none.
                  type: int
                namespace:
                  description: Namespace of the tag.
                  type: str
      sample:
        FlashArray:
          array1:
            os_version: "6.5.0"
            model: FA-X70R4
            fqdn: array1.example.com
            tags:
              - key: site
                value: lab
                org_id: null
                namespace: pure1
            "bandwidth (read) [MB/s]": 10.575
            "bandwidth (write) [MB/s]": 1.0
            "latency (read) [ms]": 1.23
            "latency (write) [ms]": 0.25
            "iops (read)": 1000
            "iops (write)": 2000
            "load [%]": 45.679
        FlashBlade:
          array2:
            os_version: "4.5.2"
            model: FlashBlade
            fqdn: array2.example.com
            tags: []
            "bandwidth (read) [MB/s]": 2.047
            "bandwidth (write) [MB/s]": 20.473
            "latency (read) [ms]": 0.66
            "latency (write) [ms]": 0.92
            "iops (read)": 61
            "iops (write)": 2432
        ObjectEngine: {}
    subscriptions:
      description:
        - The subscriptions of the organization, keyed by subscription name. It is
          the first of the two sections of the C(subscriptions) subset.
      returned: when I(gather_subset) includes C(subscriptions) or C(all)
      type: dict
      contains:
        start_date:
          description: Start of the subscription, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        expiration_date:
          description: End of the subscription, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        service:
          description: Service type of the subscription.
          type: str
        status:
          description: Status of the subscription.
          type: str
        org_name:
          description: Name of the Pure1 organization of the subscription.
          type: str
        partner_name:
          description:
            - Name of the partner who invoices for the subscription, null when Pure1
              has none.
          type: str
        subscription_term:
          description: Length of the subscription in months.
          type: int
      sample:
        subscription1:
          start_date: "2021-01-01 00:00:00 UTC"
          expiration_date: "2024-01-01 00:00:00 UTC"
          service: Evergreen//One
          status: active
          org_name: Example Org
          partner_name: null
          subscription_term: 36
    subscription_licenses:
      description:
        - The licenses of the subscriptions, keyed by license name. It is the second
          of the two sections of the C(subscriptions) subset.
      returned: when I(gather_subset) includes C(subscriptions) or C(all)
      type: dict
      contains:
        start_date:
          description: Start of the license, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        expiration_date:
          description: End of the license, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        last_updated:
          description: Date of the last amendment of the license, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        marketplace_partner:
          description: Name of the marketplace partner of the license, null when Pure1 has none.
          type: str
        service_tier:
          description: Tier of the service of the license.
          type: str
        location:
          description: Site address of the license, null when Pure1 has none.
          type: dict
          contains:
            city:
              description: City of the site.
              type: str
            state:
              description: State or province of the site.
              type: str
            country:
              description: Country of the site.
              type: str
        pre_ratio:
          description: Pre-ratio of the license, null when Pure1 has none.
          type: dict
          contains:
            data:
              description: The value.
              type: float
            unit:
              description: Unit of the value.
              type: str
        energy_usage:
          description: Always null, Pure1 reports no energy usage for a license.
          type: raw
        subscription:
          description: Name of the subscription of the license.
          type: str
        average_on_demand:
          description:
            - Estimated daily on-demand usage of the license in the current calendar
              quarter to date.
          type: dict
          contains:
            data:
              description: The value.
              type: float
            unit:
              description: Unit of the value.
              type: str
            metric:
              description: Name of the metric that the value measures.
              type: str
        reservation:
          description: Reservation of the license.
          type: dict
          contains:
            data:
              description: The value.
              type: float
            unit:
              description: Unit of the value.
              type: str
            metric:
              description: Name of the metric that the value measures.
              type: str
        usage:
          description: Usage of the license, averaged over the last day.
          type: dict
          contains:
            data:
              description: The value.
              type: float
            unit:
              description: Unit of the value.
              type: str
            metric:
              description: Name of the metric that the value measures.
              type: str
        quarter_on_demand:
          description:
            - Estimated total on-demand usage of the license in the current calendar
              quarter to date.
          type: dict
          contains:
            data:
              description: The value.
              type: float
            unit:
              description: Unit of the value.
              type: str
            metric:
              description: Name of the metric that the value measures.
              type: str
        resources:
          description: The resources that operate under the license, keyed by resource name.
          type: dict
          contains:
            resource_type:
              description: Type of the resource, for example C(arrays), null when Pure1 has none.
              type: str
            fqdn:
              description:
                - Fully qualified domain name of the resource when it is an array, null
                  when Pure1 has none.
              type: str
            activation_time:
              description:
                - Time the resource was activated under the license, as
                  C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            usage:
              description: Usage of the resource under the license.
              type: dict
              contains:
                data:
                  description: The value, null when Pure1 has none.
                  type: float
                unit:
                  description: Unit of the value.
                  type: str
                metric:
                  description: Name of the metric that the value measures.
                  type: str
      sample:
        license1:
          start_date: "2021-01-01 00:00:00 UTC"
          expiration_date: "2024-01-01 00:00:00 UTC"
          last_updated: "2021-06-01 00:00:00 UTC"
          marketplace_partner: null
          service_tier: //Block - Performance
          location:
            city: Example City
            state: EX
            country: EXAMPLE COUNTRY
          pre_ratio:
            data: 3.45
            unit: ""
          energy_usage: null
          subscription: subscription1
          average_on_demand:
            data: 0.0
            unit: B
            metric: subscription_license_on_demand_space
          reservation:
            data: 109951162777600.0
            unit: B
            metric: subscription_license_reserved_space
          usage:
            data: 46729244180480.0
            unit: B
            metric: subscription_license_effective_used_space
          quarter_on_demand:
            data: 0.0
            unit: B
            metric: subscription_license_on_demand_space
          resources:
            array1:
              resource_type: arrays
              fqdn: array1.example.com
              activation_time: "2021-01-01 00:00:00 UTC"
              usage:
                data: 46729244180480.0
                unit: B
                metric: array_effective_used_space
    contracts:
      description:
        - The C(contracts) subset, the support contract of each appliance, keyed by
          array name.
      returned: when I(gather_subset) includes C(contracts) or C(all)
      type: dict
      contains:
        contract_start:
          description:
            - Start date of the support contract in UTC, as C(YYYY-MM-DD). Null when
              the record has no start date.
          returned: when Pure1 has a support contract record for the array
          type: str
        contract_end:
          description:
            - End date of the support contract in UTC, as C(YYYY-MM-DD). Null when
              the record has no end date.
          returned: when Pure1 has a support contract record for the array
          type: str
        contract_state:
          description:
            - C(Active) up to the end date of the support contract, C(Grace Period)
              for 30 days after it and C(Expired) after that.
            - Also C(Expired) when the record has no end date or when Pure1 has no
              support contract record for the array, see C(contract_found).
          type: str
        contract_found:
          description:
            - Whether Pure1 returned a support contract record for the array.
            - Added in version 1.6.0.
          type: bool
      sample:
        array1:
          contract_start: "2021-01-01"
          contract_end: "2027-01-01"
          contract_state: Active
          contract_found: true
        array2:
          contract_state: Expired
          contract_found: false
    environmental:
      description:
        - The C(environmental) subset, the sustainability assessment of each
          appliance, keyed by array name.
      returned: when I(gather_subset) includes C(environmental) or C(all)
      type: dict
      contains:
        reporting_status:
          description:
            - Status of the latest assessment as Pure1 reports it, for example
              C(assessment_ready), C(not_enough_data), C(not_phoning_home) or
              C(unsupported_purity_version). Null when Pure1 reports none.
          type: str
        location:
          description: Install address of the appliance, empty when Pure1 has none.
          type: dict
          contains:
            longitude:
              description: Longitude of the address.
              type: float
            latitude:
              description: Latitude of the address.
              type: float
            updated:
              description: Time the address was updated, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            address:
              description: Street address in one line.
              type: str
        assessment:
          description:
            - The assessment of the appliance, empty unless C(reporting_status) is
              C(assessment_ready).
            - An average is taken over the assessment window.
          type: dict
          contains:
            assessment_level:
              description: One of C(good), C(recommendation) and C(action_required).
              type: str
            array_data_reduction:
              description: Average data reduction ratio.
              type: float
            array_total_load:
              description: Average load as a fraction of 1, null when Pure1 has none.
              type: float
            capacity_utilization:
              description: Average used capacity as a fraction of 1.
              type: float
            power_average:
              description: Average power consumption in watts.
              type: float
            power_per_usable_capacity:
              description: Average power consumption in watts per TiB of usable capacity.
              type: float
            power_per_used_space:
              description: Average power consumption in watts per TiB of used space.
              type: float
            power_typical_spec:
              description: Typical power consumption of the model and configuration in watts.
              type: float
            power_peak_spec:
              description: Peak power consumption of the model and configuration in watts.
              type: float
            heat_average:
              description: Average heat production in BTU per hour.
              type: float
            heat_typical_spec:
              description: Typical heat production of the model and configuration in BTU per hour.
              type: float
            heat_peak_spec:
              description: Peak heat production of the model and configuration in BTU per hour.
              type: float
            rack_units:
              description: Number of rack units the appliance occupies.
              type: int
            blades:
              description: Number of blades, always 0 for a FlashArray.
              type: int
            chassis:
              description: Number of chassis, always 1 for a FlashArray.
              type: int
            shelves:
              description: Number of expansion shelves, always 0 for a FlashBlade.
              type: int
            start:
              description: Start of the assessment window, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            end:
              description: End of the assessment window, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
        insights:
          description: The sustainability insights that Pure1 has for the appliance.
          type: list
          elements: dict
          contains:
            fqdn:
              description: Fully qualified domain name of the appliance.
              type: str
            type:
              description: Type of the insight.
              type: str
            severity:
              description: Severity of the insight.
              type: str
            insight_data:
              description: Data of the insight, as Pure1 gives it.
              type: dict
      sample:
        array1:
          reporting_status: assessment_ready
          location:
            longitude: -122.1
            latitude: 37.4
            updated: "2021-01-01 00:00:00 UTC"
            address: 1 Main Street, Example City
          assessment:
            assessment_level: good
            array_data_reduction: 4.5
            array_total_load: 0.25
            capacity_utilization: 0.75
            power_average: 512.5
            power_per_usable_capacity: 1.5
            power_per_used_space: 2.5
            power_typical_spec: 900.0
            power_peak_spec: 1200.0
            heat_average: 1748.5
            heat_typical_spec: 3000.0
            heat_peak_spec: 4000.0
            rack_units: 3
            blades: 0
            chassis: 1
            shelves: 0
            start: "2021-01-01 00:00:00 UTC"
            end: "2021-01-08 00:00:00 UTC"
          insights:
            - fqdn: array1.example.com
              type: not_enough_workloads
              severity: low
              insight_data: {}
    invoices:
      description:
        - The C(invoices) subset, the invoices of the organization, keyed by invoice
          number.
      returned: when I(gather_subset) includes C(invoices) or C(all)
      type: dict
      contains:
        status:
          description: Status of the invoice, for example C(open) or C(paid).
          type: str
        amount:
          description: Total amount of the invoice, 0 when Pure1 gives none.
          type: float
        date:
          description: Date of issue in UTC, as C(YYYY-MM-DD).
          type: str
        due_date:
          description: Date the payment is due in UTC, as C(YYYY-MM-DD).
          type: str
        ship_date:
          description: Date of shipment in UTC, as C(YYYY-MM-DD).
          type: str
        payment_terms:
          description: Payment terms of the invoice.
          type: str
        sales_rep:
          description: Sales representative who issued the invoice.
          type: str
        partner_po:
          description: Purchase order number of the partner.
          type: str
        end_user_po:
          description: Purchase order number of the end user.
          type: str
        end_user_name:
          description: Name of the end user.
          type: str
        subscription_id:
          description: ID of the subscription of the invoice.
          type: str
        subscription_name:
          description: Name of the subscription of the invoice.
          type: str
        lines:
          description: The line items of the invoice.
          type: list
          elements: dict
          contains:
            item:
              description: Name of the item.
              type: str
            quantity:
              description: Quantity of the item, 0 when Pure1 gives none.
              type: int
            description:
              description: Description of the item.
              type: str
            start_date:
              description: Start date of the item in UTC, as C(YYYY-MM-DD).
              type: str
            end_date:
              description: End date of the item in UTC, as C(YYYY-MM-DD).
              type: str
            components:
              description: The components of the item, null or empty when it has none.
              type: list
              elements: dict
              contains:
                item:
                  description: Name of the component.
                  type: str
                quantity:
                  description: Quantity of the component.
                  type: int
                description:
                  description: Description of the component.
                  type: str
            unit_price:
              description: Unit price of the item, 0 when Pure1 gives none.
              type: float
            amount:
              description: Total price of the item, 0 when Pure1 gives none.
              type: float
            tax_percentage:
              description:
                - Tax rate of the item as a fraction of 1 (0.1 is 10 percent), null when
                  the item has no tax.
              type: float
            tax_amount:
              description: Total tax of the item, null when the item has no tax.
              type: float
            tax_exemption_statement:
              description: Tax exemption statement of the item.
              type: str
      sample:
        "1234567":
          status: paid
          amount: 2200.0
          date: "2021-01-01"
          due_date: "2021-01-31"
          ship_date: "2021-01-02"
          payment_terms: NET30
          sales_rep: Jane Doe
          partner_po: PO-1
          end_user_po: PO-2
          end_user_name: Example Org
          subscription_id: 3fa85f64-5717-4562-b3fc-2c963f66afa6
          subscription_name: subscription1
          lines:
            - item: Evergreen//One
              quantity: 2
              description: Storage service
              start_date: "2021-01-01"
              end_date: "2021-03-31"
              components:
                - item: Reserved capacity
                  quantity: 1
                  description: Reserved capacity in TiB
              unit_price: 1000.0
              amount: 2000.0
              tax_percentage: 0.1
              tax_amount: 200.0
              tax_exemption_statement: null
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_error_text,
)
import time


class Pure1ResponseError(Exception):
    """An error answer from Pure1, reported by collect_subset"""

    def __init__(self, res):
        super(Pure1ResponseError, self).__init__()
        self.res = res


def checked(res):
    """Return an answer that can be read, raise for an error answer"""
    if res.status_code != 200:
        raise Pure1ResponseError(res)
    return res


def response_error(res):
    """Return the status and the API message of an error answer as text"""
    text = "Status code: {0}.".format(res.status_code)
    errors = getattr(res, "errors", None)
    if errors:
        parts = [
            getattr(errors[0], "message", None),
            getattr(errors[0], "context", None),
        ]
        parts = [str(part) for part in parts if part]
        if parts:
            text += " Error: " + ": ".join(parts)
    return text


def plain_dict(model, fields):
    """Return the fields of a model as plain values, None if none is set"""
    plain = {field: getattr(model, field, None) for field in fields}
    if all(value is None for value in plain.values()):
        return None
    return plain


def format_utc(epoch):
    """Return a millisecond epoch as a UTC time string, None if it is not set"""
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch / 1000))


def format_utc_date(epoch):
    """Return a millisecond epoch as a UTC date string, None if it is not set

    Pure1 gives a date as 00:00 UTC of that day, so local time would show
    the day before on a host west of UTC.
    """
    if not epoch:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(epoch / 1000))


def current_metric_dict(metric):
    """Return a license metric as plain values, None for what is not set"""
    return {
        "data": getattr(metric, "data", None),
        "unit": getattr(metric, "unit", None),
        "metric": getattr(getattr(metric, "metric", None), "name", None),
    }


def warn_left_out(module, what, subset, count):
    """Warn of the objects that a subset left out, if there are any

    what names the objects and the field that Pure1 sent them without: an
    object without the field that it is listed under cannot be reported.
    A caller that has no module gets no warning.
    """
    if count and module is not None:
        module.warn(
            "{0} were left out of the {1} subset: {2}.".format(what, subset, count)
        )


def generate_default_dict(pure_1):
    default_info = {}
    fb_count = fa_count = os_count = 0
    appliances = list(checked(pure_1.get_arrays()).items)
    for appliance in range(0, len(appliances)):
        array_os = getattr(appliances[appliance], "os", None)
        if array_os in ["Purity//FA", "Purity"]:
            fa_count += 1
        elif array_os == "Purity//FB":
            fb_count += 1
        elif array_os == "Elasticity":
            os_count += 1
    default_info["FlashArrays"] = fa_count
    default_info["FlashBlades"] = fb_count
    default_info["ObjectEngines"] = os_count
    default_info["volumes"] = checked(pure_1.get_volumes()).total_item_count
    default_info["volume_snapshots"] = checked(
        pure_1.get_volume_snapshots()
    ).total_item_count
    default_info["filesystems"] = checked(pure_1.get_file_systems()).total_item_count
    default_info["filesystem_snapshots"] = checked(
        pure_1.get_file_system_snapshots()
    ).total_item_count
    default_info["buckets"] = checked(pure_1.get_buckets()).total_item_count
    default_info["directories"] = checked(pure_1.get_directories()).total_item_count
    default_info["pods"] = checked(pure_1.get_pods()).total_item_count
    default_info["object_store_accounts"] = checked(
        pure_1.get_object_store_accounts()
    ).total_item_count
    return default_info


def generate_subscription_licenses_dict(pure_1, module=None):
    """Return the licenses of the subscriptions by name

    module is warned of the licenses and resources that are left out.
    """
    licenses_info = {}
    unnamed = unnamed_resources = 0
    licenses = list(checked(pure_1.get_subscription_licenses()).items)
    if licenses:
        for license in range(0, len(licenses)):
            name = getattr(licenses[license], "name", None)
            if name is None:
                unnamed += 1
                continue
            location = plain_dict(
                getattr(licenses[license], "site_address", None),
                ("city", "state", "country"),
            )
            pre_ratio = plain_dict(
                getattr(licenses[license], "pre_ratio", None), ("data", "unit")
            )
            licenses_info[name] = {
                "start_date": format_utc(
                    getattr(licenses[license], "start_date", None)
                ),
                "expiration_date": format_utc(
                    getattr(licenses[license], "expiration_date", None)
                ),
                "last_updated": format_utc(
                    getattr(licenses[license], "last_updated_date", None)
                ),
                "marketplace_partner": getattr(
                    getattr(licenses[license], "marketplace_partner", None),
                    "name",
                    None,
                ),
                "service_tier": getattr(licenses[license], "service_tier", None),
                "location": location,
                "pre_ratio": pre_ratio,
                "energy_usage": None,
                "subscription": getattr(
                    getattr(licenses[license], "subscription", None), "name", None
                ),
                "average_on_demand": current_metric_dict(
                    getattr(licenses[license], "average_on_demand", None)
                ),
                "reservation": current_metric_dict(
                    getattr(licenses[license], "reservation", None)
                ),
                "usage": current_metric_dict(getattr(licenses[license], "usage", None)),
                "quarter_on_demand": current_metric_dict(
                    getattr(licenses[license], "quarter_on_demand", None)
                ),
                "resources": {},
            }
            resources = getattr(licenses[license], "resources", None) or []
            for resource in range(0, len(resources)):
                res_name = getattr(resources[resource], "name", None)
                if res_name is None:
                    unnamed_resources += 1
                    continue
                licenses_info[name]["resources"][res_name] = {
                    "resource_type": getattr(
                        resources[resource], "resource_type", None
                    ),
                    "fqdn": getattr(resources[resource], "fqdn", None),
                    "activation_time": format_utc(
                        getattr(resources[resource], "activation_time", None)
                    ),
                    "usage": current_metric_dict(
                        getattr(resources[resource], "usage", None)
                    ),
                }
    warn_left_out(module, "Licenses without a name", "subscriptions", unnamed)
    warn_left_out(
        module, "License resources without a name", "subscriptions", unnamed_resources
    )
    return licenses_info


def generate_subscriptions_dict(pure_1, module=None):
    """Return the subscriptions by name

    module is warned of the subscriptions that are left out.
    """
    subscriptions_info = {}
    unnamed = 0
    subscriptions = list(checked(pure_1.get_subscriptions()).items)
    if subscriptions:
        for subscription in range(0, len(subscriptions)):
            name = getattr(subscriptions[subscription], "name", None)
            if name is None:
                unnamed += 1
                continue
            subscriptions_info[name] = {
                "start_date": format_utc(
                    getattr(subscriptions[subscription], "start_date", None)
                ),
                "expiration_date": format_utc(
                    getattr(subscriptions[subscription], "expiration_date", None)
                ),
                "service": getattr(subscriptions[subscription], "service", None),
                "status": getattr(subscriptions[subscription], "status", None),
                "org_name": getattr(
                    getattr(subscriptions[subscription], "organization", None),
                    "name",
                    None,
                ),
                "partner_name": getattr(
                    subscriptions[subscription], "partner_name", None
                ),
                "subscription_term": getattr(
                    subscriptions[subscription], "subscription_term", None
                ),
            }
    warn_left_out(module, "Subscriptions without a name", "subscriptions", unnamed)
    return subscriptions_info


def generate_esg_dict(module, pure_1):
    esg_info = {}
    current_date = int(time.time() * 1000)
    unnamed_arrays = 0
    appliances = list(checked(pure_1.get_assessment_sustainability_arrays()).items)
    for appliance in range(0, len(appliances)):
        name = getattr(appliances[appliance], "name", None)
        if name is None:
            unnamed_arrays += 1
            continue
        status = getattr(appliances[appliance], "reporting_status", None)
        esg_info[name] = {
            "insights": [],
            "location": {},
            "assessment": {},
            "reporting_status": status,
        }
        address = getattr(appliances[appliance], "install_address", None)
        if address:
            geolocation = getattr(address, "geolocation", None)
            esg_info[name]["location"] = {
                "longitude": getattr(geolocation, "longitude", None),
                "latitude": getattr(geolocation, "latitude", None),
                "updated": getattr(address, "updated", None),
                "address": getattr(address, "street_address", None),
            }
            if esg_info[name]["location"]["updated"]:
                esg_info[name]["location"]["updated"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC",
                    time.gmtime(esg_info[name]["location"]["updated"] / 1000),
                )
        assessment = getattr(appliances[appliance], "assessment", None)
        if status == "assessment_ready" and assessment is not None:
            esg_info[name]["assessment"] = {
                "array_data_reduction": getattr(
                    assessment, "array_data_reduction", None
                ),
                "assessment_level": getattr(assessment, "assessment_level", None),
                "blades": getattr(assessment, "blades", None),
                "capacity_utilization": getattr(
                    assessment, "capacity_utilization", None
                ),
                "chassis": getattr(assessment, "chassis", None),
                "power_average": getattr(assessment, "power_average", None),
                "power_per_usable_capacity": getattr(
                    assessment, "power_per_usable_capacity", None
                ),
                "power_per_used_space": getattr(
                    assessment, "power_per_used_space", None
                ),
                "power_typical_spec": getattr(assessment, "power_typical_spec", None),
                "power_peak_spec": getattr(assessment, "power_peak_spec", None),
                "heat_typical_spec": getattr(assessment, "heat_typical_spec", None),
                "heat_peak_spec": getattr(assessment, "heat_peak_spec", None),
                "heat_average": getattr(assessment, "heat_average", None),
                "rack_units": getattr(assessment, "rack_units", None),
                "shelves": getattr(assessment, "shelves", None),
                "array_total_load": getattr(assessment, "array_total_load", None),
                "start": getattr(assessment, "interval_start", None),
                "end": getattr(assessment, "interval_end", None),
            }
            if esg_info[name]["assessment"]["start"]:
                esg_info[name]["assessment"]["start"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC",
                    time.gmtime(esg_info[name]["assessment"]["start"] / 1000),
                )
            if esg_info[name]["assessment"]["end"]:
                esg_info[name]["assessment"]["end"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC",
                    time.gmtime(esg_info[name]["assessment"]["end"] / 1000),
                )
    insights = list(
        checked(pure_1.get_assessment_sustainability_insights_arrays()).items
    )
    unnamed = 0
    for insight in range(0, len(insights)):
        resource = getattr(insights[insight], "resource", None)
        name = getattr(resource, "name", None)
        if not name:
            unnamed += 1
            continue
        # Pure1 can report an insight for an array that is not in its list
        esg_info.setdefault(
            name,
            {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": None,
            },
        )
        esg_info[name]["insights"].append(
            {
                "fqdn": getattr(resource, "fqdn", None),
                "type": getattr(insights[insight], "type", None),
                "severity": getattr(insights[insight], "severity", None),
                "insight_data": getattr(insights[insight], "additional_data", None),
            }
        )
    warn_left_out(module, "Arrays without a name", "environmental", unnamed_arrays)
    if unnamed:
        module.warn(
            "Insights without an array name were left out of the "
            "environmental subset: {0}.".format(unnamed)
        )
    return esg_info


def generate_contract_dict(pure_1, module=None):
    """Return the support contract of each array by array name

    module is warned of the arrays that are left out.
    """
    contract_info = {}
    grace_period = 2592000000  # 30 days in ms
    current_date = int(time.time() * 1000)
    unnamed = 0
    appliances = list(checked(pure_1.get_arrays()).items)
    for appliance in range(0, len(appliances)):
        contract_state = "Expired"
        name = getattr(appliances[appliance], "name", None)
        if name is None:
            # The contract is asked for by array name
            unnamed += 1
            continue
        contract_info[name] = {}
        contract_data = list(
            checked(
                pure_1.get_arrays_support_contracts(
                    filter="resource.name=" + filter_string(name)
                )
            ).items
        )
        if contract_data:
            contract_start_epoch = getattr(contract_data[0], "start_date", None)
            contract_end_epoch = getattr(contract_data[0], "end_date", None)
            contract_info[name]["contract_start"] = format_utc_date(
                contract_start_epoch
            )
            contract_info[name]["contract_end"] = format_utc_date(contract_end_epoch)
            if contract_end_epoch:
                if current_date <= contract_end_epoch:
                    contract_state = "Active"
                elif contract_end_epoch + grace_period >= current_date:
                    contract_state = "Grace Period"
        contract_info[name]["contract_state"] = contract_state
        contract_info[name]["contract_found"] = bool(contract_data)
    warn_left_out(module, "Arrays without a name", "contracts", unnamed)
    return contract_info


def generate_invoices_dict(module, pure_1):
    invoices_info = {}
    res = pure_1.get_invoices()
    if res.status_code == 200:
        unnamed = 0
        invoices = list(res.items)
        for invoice in range(0, len(invoices)):
            name = getattr(invoices[invoice], "id", None)
            if name is None:
                unnamed += 1
                continue
            subscription = getattr(invoices[invoice], "subscription", None)

            invoices_info[name] = {
                "lines": [],
                "status": getattr(invoices[invoice], "status", None),
                "amount": getattr(invoices[invoice], "amount", 0),
                # py-pure-client names the date of the invoice var_date
                "date": format_utc_date(getattr(invoices[invoice], "var_date", None)),
                "due_date": format_utc_date(
                    getattr(invoices[invoice], "due_date", None)
                ),
                "ship_date": format_utc_date(
                    getattr(invoices[invoice], "ship_date", None)
                ),
                "payment_terms": getattr(invoices[invoice], "payment_terms", None),
                "sales_rep": getattr(invoices[invoice], "sales_representative", None),
                "partner_po": getattr(
                    invoices[invoice], "partner_purchase_order", None
                ),
                "end_user_po": getattr(
                    invoices[invoice], "end_user_purchase_order", None
                ),
                "end_user_name": getattr(invoices[invoice], "end_user_name", None),
                "subscription_id": getattr(subscription, "id", None),
                "subscription_name": getattr(subscription, "name", None),
            }
            lines = getattr(invoices[invoice], "lines", None) or []
            for line in range(0, len(lines)):
                tax = getattr(lines[line], "tax", None)
                components = getattr(lines[line], "components", None)
                if components:
                    components = [
                        {
                            "item": getattr(component, "item", None),
                            "quantity": getattr(component, "quantity", None),
                            "description": getattr(component, "description", None),
                        }
                        for component in components
                    ]
                invoices_info[name]["lines"].append(
                    {
                        "item": getattr(lines[line], "item", None),
                        "quantity": getattr(lines[line], "quantity", 0),
                        "description": getattr(lines[line], "description", None),
                        "start_date": format_utc_date(
                            getattr(lines[line], "start_date", None)
                        ),
                        "end_date": format_utc_date(
                            getattr(lines[line], "end_date", None)
                        ),
                        "components": components,
                        "unit_price": getattr(lines[line], "unit_price", 0),
                        "amount": getattr(lines[line], "amount", 0),
                        "tax_percentage": (
                            getattr(tax, "percentage", 0) if tax else None
                        ),
                        "tax_amount": getattr(tax, "amount", 0) if tax else None,
                        "tax_exemption_statement": getattr(
                            tax, "exemption_statement", None
                        ),
                    }
                )
        warn_left_out(module, "Invoices without a number", "invoices", unnamed)
    else:
        # An error answer returns the section empty, it does not fail the task
        module.warn(
            "Failed to collect the invoices subset, the section is returned "
            "empty. {0}".format(response_error(res))
        )
    return invoices_info


def get_latest_metric(module, pure_1, name, metric):
    """Return the latest value of an array metric, None if there is none"""
    res = pure_1.get_metrics_history(
        names=[metric],
        resource_names=[name],
        aggregation="max",
        resolution=180000,
        end_time=int(time.time()) * 1000,
        start_time=(int(time.time()) * 1000) - 18000000,
    )
    if res.status_code != 200:
        module.warn(
            "Metric {0} not collected for {1}. {2}".format(
                metric, name, response_error(res)
            )
        )
        return None
    series = list(res.items)
    data = getattr(series[0], "data", None) if series else None
    if not data:
        return None
    point = data[-1]
    if not point or len(point) < 2:
        return None
    return point[1]


def get_tags(module, pure_1, name):
    """Return the tags of an array and the number left out for having no key

    No tags with a warning if they are refused.
    """
    res = pure_1.get_arrays_tags(resource_names=[name])
    if res.status_code != 200:
        module.warn("Tags not collected for {0}. {1}".format(name, response_error(res)))
        return [], 0
    tags = []
    unkeyed = 0
    for tag in list(res.items):
        key = getattr(tag, "key", None)
        if key is None:
            unkeyed += 1
            continue
        tags.append(
            {
                "key": key,
                "value": getattr(tag, "value", None),
                "org_id": getattr(tag, "tag_organization_id", None),
                "namespace": getattr(tag, "namespace", None),
            }
        )
    return tags, unkeyed


def generate_appliances_dict(module, pure_1):
    names_info = {"FlashArray": {}, "FlashBlade": {}, "ObjectEngine": {}}
    unnamed = unkeyed_tags = 0
    appliances = list(checked(pure_1.get_arrays()).items)
    for appliance in range(0, len(appliances)):
        name = getattr(appliances[appliance], "name", None)
        if name is None:
            # Tags and metrics are asked for by array name
            unnamed += 1
            continue
        fqdn = getattr(appliances[appliance], "fqdn", "")
        array_os = getattr(appliances[appliance], "os", None)
        if array_os in ["Purity//FA", "Purity"]:
            tags, unkeyed = get_tags(module, pure_1, name)
            unkeyed_tags += unkeyed
            names_info["FlashArray"][name] = {
                "os_version": getattr(appliances[appliance], "version", None),
                "model": getattr(appliances[appliance], "model", None),
                "fqdn": fqdn,
                "tags": tags,
            }
            value = get_latest_metric(module, pure_1, name, "array_read_bandwidth")
            if value is not None:
                names_info["FlashArray"][name]["bandwidth (read) [MB/s]"] = round(
                    value / 1048576, 3
                )
            value = get_latest_metric(module, pure_1, name, "array_write_bandwidth")
            if value is not None:
                names_info["FlashArray"][name]["bandwidth (write) [MB/s]"] = round(
                    value / 1048576, 3
                )
            value = get_latest_metric(module, pure_1, name, "array_read_latency_us")
            if value is not None:
                names_info["FlashArray"][name]["latency (read) [ms]"] = round(
                    value / 1000, 2
                )
            value = get_latest_metric(module, pure_1, name, "array_write_latency_us")
            if value is not None:
                names_info["FlashArray"][name]["latency (write) [ms]"] = round(
                    value / 1000, 2
                )
            value = get_latest_metric(module, pure_1, name, "array_read_iops")
            if value is not None:
                names_info["FlashArray"][name]["iops (read)"] = round(value)
            value = get_latest_metric(module, pure_1, name, "array_write_iops")
            if value is not None:
                names_info["FlashArray"][name]["iops (write)"] = round(value)
            value = get_latest_metric(module, pure_1, name, "array_total_load")
            if value is not None:
                names_info["FlashArray"][name]["load [%]"] = round(value * 100, 3)
        elif array_os == "Elasticity":
            tags, unkeyed = get_tags(module, pure_1, name)
            unkeyed_tags += unkeyed
            names_info["ObjectEngine"][name] = {
                "os_version": getattr(appliances[appliance], "version", None),
                "model": getattr(appliances[appliance], "model", None),
                "fqdn": fqdn,
                "tags": tags,
            }
        elif array_os == "Purity//FB":
            tags, unkeyed = get_tags(module, pure_1, name)
            unkeyed_tags += unkeyed
            names_info["FlashBlade"][name] = {
                "os_version": getattr(appliances[appliance], "version", None),
                "model": getattr(appliances[appliance], "model", None),
                "fqdn": fqdn,
                "tags": tags,
            }
            value = get_latest_metric(module, pure_1, name, "array_read_bandwidth")
            if value is not None:
                names_info["FlashBlade"][name]["bandwidth (read) [MB/s]"] = round(
                    value / 1048576, 3
                )
            value = get_latest_metric(module, pure_1, name, "array_write_bandwidth")
            if value is not None:
                names_info["FlashBlade"][name]["bandwidth (write) [MB/s]"] = round(
                    value / 1048576, 3
                )
            value = get_latest_metric(module, pure_1, name, "array_read_iops")
            if value is not None:
                names_info["FlashBlade"][name]["iops (read)"] = round(value)
            value = get_latest_metric(module, pure_1, name, "array_write_iops")
            if value is not None:
                names_info["FlashBlade"][name]["iops (write)"] = round(value)
            value = get_latest_metric(module, pure_1, name, "array_read_latency_us")
            if value is not None:
                names_info["FlashBlade"][name]["latency (read) [ms]"] = round(
                    value / 1000, 2
                )
            value = get_latest_metric(module, pure_1, name, "array_write_latency_us")
            if value is not None:
                names_info["FlashBlade"][name]["latency (write) [ms]"] = round(
                    value / 1000, 2
                )
        else:
            module.warn("Unknown operating system detected: {0}.".format(array_os))
    warn_left_out(module, "Arrays without a name", "appliances", unnamed)
    warn_left_out(module, "Tags without a key", "appliances", unkeyed_tags)
    return names_info


def collect_subset(module, subset, generate, *args):
    """Return one section of a subset

    A 403 answer returns the section empty with a warning, any other
    error answer fails the task with the API message. An error raised by
    the SDK or by the transport fails the task with its type and text.
    """
    try:
        return generate(*args)
    except Pure1ResponseError as error:
        res = error.res
        if res.status_code == 403:
            module.warn(
                "No permission to collect the {0} subset (status code 403). "
                "The section is returned empty.".format(subset)
            )
            return {}
        module.fail_json(
            msg="Failed to collect the {0} subset. {1}".format(
                subset, response_error(res)
            )
        )
    except Exception as error:
        module.fail_json(
            msg="Failed to collect the {0} subset. {1}".format(
                subset, pure1_error_text(module, error)
            )
        )


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(gather_subset=dict(default="minimum", type="list", elements="str"))
    )

    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    subset = [test.lower() for test in module.params["gather_subset"]]
    valid_subsets = (
        "all",
        "minimum",
        "appliances",
        "subscriptions",
        "contracts",
        "environmental",
        "invoices",
    )
    subset_test = (test in valid_subsets for test in subset)
    if not all(subset_test):
        module.fail_json(
            msg="gather_subset must be one or more of: %s, got: %s"
            % (", ".join(valid_subsets), ", ".join(subset))
        )

    info = {}

    if "minimum" in subset or "all" in subset:
        info["default"] = collect_subset(
            module, "minimum", generate_default_dict, pure_1
        )
    if "appliances" in subset or "all" in subset:
        info["appliances"] = collect_subset(
            module, "appliances", generate_appliances_dict, module, pure_1
        )
    if "subscriptions" in subset or "all" in subset:
        info["subscriptions"] = collect_subset(
            module, "subscriptions", generate_subscriptions_dict, pure_1, module
        )
        info["subscription_licenses"] = collect_subset(
            module, "subscriptions", generate_subscription_licenses_dict, pure_1, module
        )
    if "contracts" in subset or "all" in subset:
        info["contracts"] = collect_subset(
            module, "contracts", generate_contract_dict, pure_1, module
        )
    if "environmental" in subset or "all" in subset:
        info["environmental"] = collect_subset(
            module, "environmental", generate_esg_dict, module, pure_1
        )
    if "invoices" in subset or "all" in subset:
        info["invoices"] = collect_subset(
            module, "invoices", generate_invoices_dict, module, pure_1
        )

    module.exit_json(changed=False, pure1_info=info)


if __name__ == "__main__":
    main()
