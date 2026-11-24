# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_info module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ast
import datetime
import inspect
import json
import re
import time

import pytest
import yaml
from pypureclient import ErrorResponse, PureError
from urllib3.exceptions import ProtocolError

from plugins.module_utils.pure1 import pure1_argument_spec
from plugins.modules import pure1_info
from plugins.modules.pure1_info import (
    Pure1ResponseError,
    collect_subset,
    generate_appliances_dict,
    generate_default_dict,
    generate_esg_dict,
    generate_subscriptions_dict,
    generate_subscription_licenses_dict,
    generate_contract_dict,
    generate_invoices_dict,
)
from tests.unit.conftest import (
    MODELS,
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

# 00:00:00 UTC on the first day of three months of 2021, in milliseconds
JAN_2021_MS = 1609459200000
FEB_2021_MS = 1612137600000
MAR_2021_MS = 1614556800000


def _received_model(class_name, **wire):
    """Return an SDK model as the SDK builds it from the JSON of an answer.

    The SDK does not validate what Pure1 sends (from_dict), so a null inside
    a list reaches the module; the constructor behind sdk_model refuses one.
    The keys are the names on the wire, and one that the model does not have
    is refused, as sdk_model refuses it.
    """
    cls = getattr(MODELS, class_name)
    known = [field.alias for field in cls.__fields__.values()]
    unknown = sorted(set(wire) - set(known))
    if unknown:
        raise TypeError(
            "{0} has no {1} on the wire; it has: {2}".format(
                class_name, ", ".join(unknown), ", ".join(known)
            )
        )
    return cls.from_dict(wire)


def _answer_with(client, method, answer):
    """Make one client method return the given error answer on every call"""
    getattr(client, method).side_effect = None
    getattr(client, method).return_value = answer


class TestStandIns:
    """The fixtures must not invent what the SDK does not have."""

    def test_model_rejects_a_field_the_sdk_model_does_not_have(self):
        with pytest.raises(TypeError):
            sdk_model("Subscription", name="sub1", org_name="ACME")

    def test_unset_field_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            getattr(sdk_model("Tag", key="site", value="lab"), "tag_organization_id")

    def test_client_has_no_get_array_tags(self, pure1_client):
        with pytest.raises(AttributeError):
            getattr(pure1_client, "get_array_tags")

    def test_received_model_keeps_a_null_the_constructor_refuses(self):
        with pytest.raises(ValueError):
            sdk_model("MetricHistory", data=[[JAN_2021_MS, None]])

        series = _received_model("MetricHistory", data=[[JAN_2021_MS, None]])

        assert isinstance(series, MODELS.MetricHistory)
        assert series.data == [[JAN_2021_MS, None]]
        with pytest.raises(AttributeError):
            getattr(series, "name")
        with pytest.raises(TypeError):
            _received_model("MetricHistory", site="lab")

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_host_west_of_utc_shows_the_day_before(self):
        seconds = JAN_2021_MS / 1000

        assert time.strftime("%Y-%m-%d", time.localtime(seconds)) == "2020-12-31"
        local = datetime.datetime.fromtimestamp(seconds)
        assert local.strftime("%Y-%m-%d") == "2020-12-31"
        assert time.strftime("%Y-%m-%d", time.gmtime(seconds)) == "2021-01-01"


class TestGenerateDefaultDict:
    """Tests for generate_default_dict."""

    def _client(self, client):
        client.get_arrays.return_value = valid_response(
            [
                sdk_model("Array", os="Purity//FA"),
                sdk_model("Array", os="Purity"),
                sdk_model("Array", os="Purity//FB"),
                sdk_model("Array", os="Elasticity"),
            ]
        )
        client.get_volumes.return_value = valid_response(total_item_count=5)
        client.get_volume_snapshots.return_value = valid_response(total_item_count=6)
        client.get_file_systems.return_value = valid_response(total_item_count=7)
        client.get_file_system_snapshots.return_value = valid_response(
            total_item_count=8
        )
        client.get_buckets.return_value = valid_response(total_item_count=9)
        client.get_directories.return_value = valid_response(total_item_count=10)
        client.get_pods.return_value = valid_response(total_item_count=11)
        client.get_object_store_accounts.return_value = valid_response(
            total_item_count=12
        )
        return client

    def test_counts_appliances_by_os_and_totals(self, pure1_client):
        result = generate_default_dict(self._client(pure1_client))

        assert result["FlashArrays"] == 2  # Purity//FA + Purity
        assert result["FlashBlades"] == 1
        assert result["ObjectEngines"] == 1
        assert result["volumes"] == 5
        assert result["volume_snapshots"] == 6
        assert result["filesystems"] == 7
        assert result["filesystem_snapshots"] == 8
        assert result["buckets"] == 9
        assert result["directories"] == 10
        assert result["pods"] == 11
        assert result["object_store_accounts"] == 12

    def test_each_appliance_count_comes_from_its_own_os(self, pure1_client):
        client = self._client(pure1_client)
        systems = ["Purity//FA", "Purity//FA", "Purity", "Purity//FB", "Purity//FB"]
        systems += ["Elasticity", "Other"]
        client.get_arrays.return_value = valid_response(
            [sdk_model("Array", os=array_os) for array_os in systems]
        )

        result = generate_default_dict(client)

        assert result["FlashArrays"] == 3
        assert result["FlashBlades"] == 2
        assert result["ObjectEngines"] == 1

    def test_array_without_an_os_is_counted_in_no_type(self, pure1_client):
        client = self._client(pure1_client)
        client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="a1"), sdk_model("Array", os="Purity//FA")]
        )

        result = generate_default_dict(client)

        assert result["FlashArrays"] == 1
        assert result["FlashBlades"] == 0
        assert result["ObjectEngines"] == 0

    @pytest.mark.parametrize(
        "method",
        [
            "get_arrays",
            "get_volumes",
            "get_volume_snapshots",
            "get_file_systems",
            "get_file_system_snapshots",
            "get_buckets",
            "get_directories",
            "get_pods",
            "get_object_store_accounts",
        ],
    )
    def test_error_answer_on_a_count_is_raised(self, pure1_client, method):
        client = self._client(pure1_client)
        _answer_with(client, method, error_response(500, "Internal server error"))

        with pytest.raises(Pure1ResponseError) as error:
            generate_default_dict(client)

        assert error.value.res.status_code == 500


class TestGenerateSubscriptionsDict:
    """Tests for generate_subscriptions_dict."""

    def _subscription(self, **kwargs):
        fields = dict(
            name="sub1",
            start_date=JAN_2021_MS,
            expiration_date=JAN_2021_MS,
            service="Evergreen//One",
            status="active",
        )
        fields.update(kwargs)
        return sdk_model("Subscription", **fields)

    def test_maps_subscription_fields_and_dates(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [
                self._subscription(
                    organization=sdk_model("FixedReference", id="org-1", name="ACME"),
                    customer_name="ACME Storage",
                    partner_name="PartnerCo",
                    subscription_term=36,
                )
            ]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result["sub1"] == {
            "start_date": "2021-01-01 00:00:00 UTC",
            "expiration_date": "2021-01-01 00:00:00 UTC",
            "service": "Evergreen//One",
            "status": "active",
            "org_name": "ACME",
            "partner_name": "PartnerCo",
            "subscription_term": 36,
        }

    def test_each_date_comes_from_its_own_field(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [self._subscription(start_date=JAN_2021_MS, expiration_date=FEB_2021_MS)]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result["sub1"]["start_date"] == "2021-01-01 00:00:00 UTC"
        assert result["sub1"]["expiration_date"] == "2021-02-01 00:00:00 UTC"

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_dates_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [self._subscription(start_date=JAN_2021_MS, expiration_date=FEB_2021_MS)]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result["sub1"]["start_date"] == "2021-01-01 00:00:00 UTC"
        assert result["sub1"]["expiration_date"] == "2021-02-01 00:00:00 UTC"

    def test_optional_fields_default_to_none(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [self._subscription()]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result["sub1"]["org_name"] is None
        assert result["sub1"]["partner_name"] is None
        assert result["sub1"]["subscription_term"] is None

    def test_empty_organization_reference_gives_no_org_name(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [self._subscription(organization=sdk_model("FixedReference"))]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result["sub1"]["org_name"] is None

    def test_subscription_with_only_a_name(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response(
            [sdk_model("Subscription", name="sub1")]
        )

        result = generate_subscriptions_dict(pure1_client)

        assert result == {
            "sub1": {
                "start_date": None,
                "expiration_date": None,
                "service": None,
                "status": None,
                "org_name": None,
                "partner_name": None,
                "subscription_term": None,
            }
        }

    def test_subscription_without_a_name_is_left_out(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = valid_response(
            [
                sdk_model("Subscription", service="Evergreen//One"),
                self._subscription(),
                sdk_model("Subscription", status="active"),
            ]
        )

        result = generate_subscriptions_dict(pure1_client, module)

        assert result == {
            "sub1": {
                "start_date": "2021-01-01 00:00:00 UTC",
                "expiration_date": "2021-01-01 00:00:00 UTC",
                "service": "Evergreen//One",
                "status": "active",
                "org_name": None,
                "partner_name": None,
                "subscription_term": None,
            }
        }
        module.warn.assert_called_once_with(
            "Subscriptions without a name were left out of the "
            "subscriptions subset: 2."
        )

    def test_subscription_with_an_empty_name_is_reported(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = valid_response(
            [sdk_model("Subscription", name="", status="active")]
        )

        result = generate_subscriptions_dict(pure1_client, module)

        # an empty string is a name that Pure1 sent: the object is reported
        assert list(result) == [""]
        assert result[""]["status"] == "active"
        module.warn.assert_not_called()

    def test_empty_subscriptions_returns_empty_dict(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response()

        assert generate_subscriptions_dict(pure1_client) == {}

    def test_error_answer_is_raised(self, pure1_client):
        pure1_client.get_subscriptions.return_value = error_response(400, "Bad request")

        with pytest.raises(Pure1ResponseError):
            generate_subscriptions_dict(pure1_client)


class TestGenerateContractDict:
    """Tests for generate_contract_dict (contract_state logic)."""

    def _client_with_contract(self, client, start_ms, end_ms):
        client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo")]
        )
        client.get_arrays_support_contracts.return_value = valid_response(
            [sdk_model("SupportContract", start_date=start_ms, end_date=end_ms)]
        )
        return client

    def test_active_contract(self, pure1_client):
        now = int(time.time() * 1000)
        client = self._client_with_contract(pure1_client, now - 1000, now + 86400000)

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Active"

    def test_grace_period_contract(self, pure1_client):
        now = int(time.time() * 1000)
        # ended 1 day ago, well within the 30-day grace window
        client = self._client_with_contract(
            pure1_client, now - 86400000 * 365, now - 86400000
        )

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Grace Period"

    def test_expired_contract(self, pure1_client):
        now = int(time.time() * 1000)
        # ended 60 days ago, past the 30-day grace window
        client = self._client_with_contract(
            pure1_client, now - 86400000 * 365, now - 86400000 * 60
        )

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Expired"

    @pytest.mark.parametrize("days_ago, state", [(29, "Grace Period"), (31, "Expired")])
    def test_grace_period_lasts_thirty_days(self, pure1_client, days_ago, state):
        now = int(time.time() * 1000)
        client = self._client_with_contract(
            pure1_client, now - 86400000 * 365, now - 86400000 * days_ago
        )

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == state

    def test_contract_is_read_for_the_array_by_name(self, pure1_client):
        client = self._client_with_contract(pure1_client, JAN_2021_MS, FEB_2021_MS)

        generate_contract_dict(client)

        client.get_arrays.assert_called_once_with()
        client.get_arrays_support_contracts.assert_called_once_with(
            filter="resource.name='foo'"
        )

    def test_contract_dates_are_returned(self, pure1_client):
        client = self._client_with_contract(pure1_client, JAN_2021_MS, FEB_2021_MS)

        result = generate_contract_dict(client)

        assert result["foo"] == {
            "contract_start": "2021-01-01",
            "contract_end": "2021-02-01",
            "contract_state": "Expired",
            "contract_found": True,
        }

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_dates_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        client = self._client_with_contract(pure1_client, JAN_2021_MS, FEB_2021_MS)

        result = generate_contract_dict(client)

        assert result["foo"]["contract_start"] == "2021-01-01"
        assert result["foo"]["contract_end"] == "2021-02-01"

    @pytest.mark.parametrize(
        "dates, start, end",
        [
            ({}, None, None),
            ({"end_date": FEB_2021_MS}, None, "2021-02-01"),
            ({"start_date": JAN_2021_MS}, "2021-01-01", None),
        ],
        ids=["no dates", "no start date", "no end date"],
    )
    def test_record_without_a_date_gives_null_for_it(
        self, pure1_client, dates, start, end
    ):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo")]
        )
        pure1_client.get_arrays_support_contracts.return_value = valid_response(
            [sdk_model("SupportContract", **dates)]
        )

        result = generate_contract_dict(pure1_client)

        assert result["foo"] == {
            "contract_start": start,
            "contract_end": end,
            "contract_state": "Expired",
            "contract_found": True,
        }

    def test_dates_are_not_carried_over_from_the_previous_array(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo"), sdk_model("Array", name="bar")]
        )
        pure1_client.get_arrays_support_contracts.side_effect = [
            valid_response(
                [
                    sdk_model(
                        "SupportContract", start_date=JAN_2021_MS, end_date=FEB_2021_MS
                    )
                ]
            ),
            valid_response([sdk_model("SupportContract")]),
        ]

        result = generate_contract_dict(pure1_client)

        assert result == {
            "foo": {
                "contract_start": "2021-01-01",
                "contract_end": "2021-02-01",
                "contract_state": "Expired",
                "contract_found": True,
            },
            "bar": {
                "contract_start": None,
                "contract_end": None,
                "contract_state": "Expired",
                "contract_found": True,
            },
        }

    def test_date_of_zero_counts_as_no_date(self, pure1_client):
        client = self._client_with_contract(pure1_client, 0, 0)

        result = generate_contract_dict(client)

        assert result["foo"] == {
            "contract_start": None,
            "contract_end": None,
            "contract_state": "Expired",
            "contract_found": True,
        }

    def test_array_name_is_escaped_in_the_filter(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="o'brien\\x")]
        )
        pure1_client.get_arrays_support_contracts.return_value = valid_response()

        result = generate_contract_dict(pure1_client)

        pure1_client.get_arrays_support_contracts.assert_called_once_with(
            filter="resource.name='o\\'brien\\\\x'"
        )
        assert list(result) == ["o'brien\\x"]

    def test_no_contract_data_is_expired(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo")]
        )
        pure1_client.get_arrays_support_contracts.return_value = valid_response()

        result = generate_contract_dict(pure1_client)

        assert result["foo"] == {"contract_state": "Expired", "contract_found": False}

    def test_contract_found_tells_a_lapsed_contract_from_none(self, pure1_client):
        now = int(time.time() * 1000)
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo"), sdk_model("Array", name="bar")]
        )
        pure1_client.get_arrays_support_contracts.side_effect = [
            valid_response(
                [
                    sdk_model(
                        "SupportContract",
                        start_date=now - 86400000 * 365,
                        end_date=now - 86400000 * 60,
                    )
                ]
            ),
            valid_response(),
        ]

        result = generate_contract_dict(pure1_client)

        assert result["foo"]["contract_state"] == "Expired"
        assert result["foo"]["contract_found"] is True
        assert result["bar"] == {"contract_state": "Expired", "contract_found": False}

    def _client_with_unnamed_arrays(self, client):
        client.get_arrays.return_value = valid_response(
            [
                sdk_model("Array", os="Purity//FA"),
                sdk_model("Array", name="foo"),
                sdk_model("Array", model="FA-X70R4"),
            ]
        )
        client.get_arrays_support_contracts.return_value = valid_response()
        return client

    def test_array_without_a_name_is_left_out(self, pure1_client):
        module = ansible_module()
        client = self._client_with_unnamed_arrays(pure1_client)

        result = generate_contract_dict(client, module)

        assert result == {"foo": {"contract_state": "Expired", "contract_found": False}}
        # no contract is asked for without a name to ask by
        client.get_arrays_support_contracts.assert_called_once_with(
            filter="resource.name='foo'"
        )
        module.warn.assert_called_once_with(
            "Arrays without a name were left out of the contracts subset: 2."
        )

    def test_array_without_a_name_is_left_out_without_a_module(self, pure1_client):
        client = self._client_with_unnamed_arrays(pure1_client)

        result = generate_contract_dict(client)

        assert result == {"foo": {"contract_state": "Expired", "contract_found": False}}

    def test_array_with_an_empty_name_is_reported(self, pure1_client):
        module = ansible_module()
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="")]
        )
        pure1_client.get_arrays_support_contracts.return_value = valid_response()

        result = generate_contract_dict(pure1_client, module)

        # an empty string is a name that Pure1 sent: the object is reported
        assert result == {"": {"contract_state": "Expired", "contract_found": False}}
        module.warn.assert_not_called()

    def test_error_answer_for_an_array_is_raised(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo")]
        )
        pure1_client.get_arrays_support_contracts.return_value = error_response(
            404, "Not found"
        )

        with pytest.raises(Pure1ResponseError):
            generate_contract_dict(pure1_client)


class TestGenerateEsgDict:
    """Tests for generate_esg_dict."""

    def _client(self, client, **assessment):
        fields = dict(
            assessment_level="good",
            power_average=512.5,
            rack_units=3,
            interval_start=JAN_2021_MS,
            interval_end=JAN_2021_MS,
        )
        fields.update(assessment)
        client.get_assessment_sustainability_arrays.return_value = valid_response(
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="assessment_ready",
                    install_address=sdk_model(
                        "InstallAddress",
                        geolocation=sdk_model(
                            "Geolocation", latitude=37.4, longitude=-122.1
                        ),
                        street_address="1 Main Street",
                        updated=JAN_2021_MS,
                    ),
                    assessment=sdk_model("SustainabilityAssessment", **fields),
                ),
                sdk_model(
                    "SustainabilityArray",
                    name="fb1",
                    reporting_status="not_enough_data",
                ),
            ]
        )
        insights = [
            sdk_model(
                "SustainabilityInsightArray",
                resource=sdk_model(
                    "FixedReferenceFqdn", name="fa1", fqdn="fa1.example.com"
                ),
                type="high_power_consumption",
                severity="medium",
                additional_data={"power": 512.5},
            )
        ]
        client.get_assessment_sustainability_insights_arrays.return_value = (
            valid_response(insights)
        )
        return client

    def _result(self, client, appliances, insights=()):
        """Return the module and the section for these appliances and insights"""
        client.get_assessment_sustainability_arrays.return_value = valid_response(
            appliances
        )
        client.get_assessment_sustainability_insights_arrays.return_value = (
            valid_response(insights)
        )
        module = ansible_module()
        return module, generate_esg_dict(module, client)

    def test_ready_appliance_with_an_insight(self, pure1_client):
        result = generate_esg_dict(ansible_module(), self._client(pure1_client))

        assert result["fa1"]["reporting_status"] == "assessment_ready"
        assert result["fa1"]["location"] == {
            "longitude": -122.1,
            "latitude": 37.4,
            "updated": "2021-01-01 00:00:00 UTC",
            "address": "1 Main Street",
        }
        assessment = result["fa1"]["assessment"]
        assert len(assessment) == 18
        assert assessment["assessment_level"] == "good"
        assert assessment["power_average"] == 512.5
        assert assessment["rack_units"] == 3
        assert assessment["heat_average"] is None
        assert assessment["start"] == "2021-01-01 00:00:00 UTC"
        assert assessment["end"] == "2021-01-01 00:00:00 UTC"
        assert result["fa1"]["insights"] == [
            {
                "fqdn": "fa1.example.com",
                "type": "high_power_consumption",
                "severity": "medium",
                "insight_data": {"power": 512.5},
            }
        ]
        json.dumps(result)

    def test_assessment_holds_each_field_of_the_sdk_model(self, pure1_client):
        client = self._client(
            pure1_client,
            array_data_reduction=4.5,
            array_total_load=0.25,
            blades=15,
            capacity_utilization=0.75,
            chassis=2,
            heat_average=1748.5,
            heat_peak_spec=4000,
            heat_typical_spec=3000,
            power_peak_spec=1200,
            power_per_usable_capacity=1.5,
            power_per_used_space=2.5,
            power_typical_spec=900,
            shelves=1,
            interval_start=JAN_2021_MS,
            interval_end=FEB_2021_MS,
        )

        result = generate_esg_dict(ansible_module(), client)

        assert result["fa1"]["assessment"] == {
            "array_data_reduction": 4.5,
            "assessment_level": "good",
            "blades": 15,
            "capacity_utilization": 0.75,
            "chassis": 2,
            "power_average": 512.5,
            "power_per_usable_capacity": 1.5,
            "power_per_used_space": 2.5,
            "power_typical_spec": 900,
            "power_peak_spec": 1200,
            "heat_typical_spec": 3000,
            "heat_peak_spec": 4000,
            "heat_average": 1748.5,
            "rack_units": 3,
            "shelves": 1,
            "array_total_load": 0.25,
            "start": "2021-01-01 00:00:00 UTC",
            "end": "2021-02-01 00:00:00 UTC",
        }

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        client = self._client(
            pure1_client, interval_start=JAN_2021_MS, interval_end=FEB_2021_MS
        )

        result = generate_esg_dict(ansible_module(), client)

        assert result["fa1"]["location"]["updated"] == "2021-01-01 00:00:00 UTC"
        assert result["fa1"]["assessment"]["start"] == "2021-01-01 00:00:00 UTC"
        assert result["fa1"]["assessment"]["end"] == "2021-02-01 00:00:00 UTC"

    def test_appliance_without_an_assessment(self, pure1_client):
        result = generate_esg_dict(ansible_module(), self._client(pure1_client))

        assert result["fb1"] == {
            "insights": [],
            "location": {},
            "assessment": {},
            "reporting_status": "not_enough_data",
        }

    def test_reporting_status_is_the_string_for_every_appliance(self, pure1_client):
        statuses = {
            "fa1": "assessment_ready",
            "fa2": "not_enough_data",
            "fa3": "not_phoning_home",
            "fa4": "unsupported_purity_version",
        }
        module, result = self._result(
            pure1_client,
            [
                sdk_model("SustainabilityArray", name=name, reporting_status=status)
                for name, status in statuses.items()
            ],
        )

        assert {
            name: entry["reporting_status"] for name, entry in result.items()
        } == statuses
        module.warn.assert_not_called()

    def test_appliance_with_only_a_name(self, pure1_client):
        module, result = self._result(
            pure1_client, [sdk_model("SustainabilityArray", name="fa1")]
        )

        assert result == {
            "fa1": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": None,
            }
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "address, location",
        [
            (
                {"street_address": "1 Main Street", "updated": JAN_2021_MS},
                {
                    "longitude": None,
                    "latitude": None,
                    "updated": "2021-01-01 00:00:00 UTC",
                    "address": "1 Main Street",
                },
            ),
            (
                {},
                {
                    "longitude": None,
                    "latitude": None,
                    "updated": None,
                    "address": None,
                },
            ),
        ],
        ids=["no geolocation", "no field at all"],
    )
    def test_address_without_a_geolocation(self, pure1_client, address, location):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="not_enough_data",
                    install_address=sdk_model("InstallAddress", **address),
                )
            ],
        )

        assert result["fa1"]["location"] == location

    def test_geolocation_without_coordinates(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    install_address=sdk_model(
                        "InstallAddress", geolocation=sdk_model("Geolocation")
                    ),
                )
            ],
        )

        assert result["fa1"]["location"] == {
            "longitude": None,
            "latitude": None,
            "updated": None,
            "address": None,
        }

    def test_ready_appliance_without_an_assessment(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="assessment_ready",
                )
            ],
        )

        assert result == {
            "fa1": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": "assessment_ready",
            }
        }

    def test_ready_appliance_with_an_empty_assessment(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="assessment_ready",
                    assessment=sdk_model("SustainabilityAssessment"),
                )
            ],
        )

        assessment = result["fa1"]["assessment"]
        assert len(assessment) == 18
        assert set(assessment.values()) == {None}

    def test_appliance_that_is_not_ready_has_no_assessment(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="not_enough_data",
                    assessment=sdk_model(
                        "SustainabilityAssessment", assessment_level="good"
                    ),
                )
            ],
        )

        assert result == {
            "fa1": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": "not_enough_data",
            }
        }

    def test_insight_for_an_array_that_is_not_listed(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray",
                    name="fa1",
                    reporting_status="not_enough_data",
                )
            ],
            [
                sdk_model(
                    "SustainabilityInsightArray",
                    resource=sdk_model(
                        "FixedReferenceFqdn", name="other1", fqdn="other1.example.com"
                    ),
                    type="high_power_consumption",
                    severity="high",
                    additional_data={"power": 512.5},
                )
            ]
            * 2,
        )

        insight = {
            "fqdn": "other1.example.com",
            "type": "high_power_consumption",
            "severity": "high",
            "insight_data": {"power": 512.5},
        }
        assert result == {
            "fa1": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": "not_enough_data",
            },
            "other1": {
                "insights": [insight, insight],
                "location": {},
                "assessment": {},
                "reporting_status": None,
            },
        }
        module.warn.assert_not_called()

    def test_insight_without_an_array_name_is_left_out(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [sdk_model("SustainabilityArray", name="fa1")],
            [
                sdk_model("SustainabilityInsightArray", type="no_resource"),
                sdk_model(
                    "SustainabilityInsightArray",
                    resource=sdk_model("FixedReferenceFqdn", fqdn="x1.example.com"),
                    type="no_name",
                ),
                sdk_model(
                    "SustainabilityInsightArray",
                    resource=sdk_model("FixedReferenceFqdn", name="fa1"),
                    type="named",
                ),
            ],
        )

        assert list(result) == ["fa1"]
        assert [insight["type"] for insight in result["fa1"]["insights"]] == ["named"]
        module.warn.assert_called_once_with(
            "Insights without an array name were left out of the "
            "environmental subset: 2."
        )

    def test_insight_with_only_an_array_name(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [sdk_model("SustainabilityArray", name="fa1")],
            [
                sdk_model(
                    "SustainabilityInsightArray",
                    resource=sdk_model("FixedReferenceFqdn", name="fa1"),
                )
            ],
        )

        assert result["fa1"]["insights"] == [
            {"fqdn": None, "type": None, "severity": None, "insight_data": None}
        ]
        module.warn.assert_not_called()

    def test_appliance_without_a_name_is_left_out(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model("SustainabilityArray", reporting_status="not_enough_data"),
                sdk_model("SustainabilityArray", name="fa1"),
                sdk_model(
                    "SustainabilityArray",
                    reporting_status="assessment_ready",
                    assessment=sdk_model(
                        "SustainabilityAssessment", assessment_level="good"
                    ),
                ),
            ],
        )

        assert result == {
            "fa1": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": None,
            }
        }
        module.warn.assert_called_once_with(
            "Arrays without a name were left out of the environmental subset: 2."
        )

    def test_appliance_with_an_empty_name_is_reported(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model(
                    "SustainabilityArray", name="", reporting_status="not_enough_data"
                )
            ],
        )

        # an empty string is a name that Pure1 sent: the object is reported
        assert result == {
            "": {
                "insights": [],
                "location": {},
                "assessment": {},
                "reporting_status": "not_enough_data",
            }
        }
        module.warn.assert_not_called()

    def test_unnamed_appliances_and_insights_have_a_warning_each(self, pure1_client):
        module, result = self._result(
            pure1_client,
            [
                sdk_model("SustainabilityArray", reporting_status="not_enough_data"),
                sdk_model("SustainabilityArray", name="fa1"),
            ],
            [sdk_model("SustainabilityInsightArray", type="no_resource")],
        )

        assert list(result) == ["fa1"]
        arrays, insights = [call[0][0] for call in module.warn.call_args_list]
        assert arrays == (
            "Arrays without a name were left out of the environmental subset: 1."
        )
        assert insights == (
            "Insights without an array name were left out of the "
            "environmental subset: 1."
        )

    @pytest.mark.parametrize(
        "method",
        [
            "get_assessment_sustainability_arrays",
            "get_assessment_sustainability_insights_arrays",
        ],
    )
    def test_error_answer_is_raised(self, pure1_client, method):
        client = self._client(pure1_client)
        _answer_with(client, method, error_response(500, "Server error"))

        with pytest.raises(Pure1ResponseError):
            generate_esg_dict(ansible_module(), client)


def _metric(data, unit, metric_name):
    return sdk_model(
        "CurrentMetric",
        data=data,
        unit=unit,
        metric=sdk_model("FixedReference", name=metric_name),
    )


LICENSE_KEYS = {
    "start_date",
    "expiration_date",
    "last_updated",
    "marketplace_partner",
    "service_tier",
    "location",
    "pre_ratio",
    "energy_usage",
    "subscription",
    "average_on_demand",
    "reservation",
    "usage",
    "quarter_on_demand",
    "resources",
}


class TestGenerateSubscriptionLicensesDict:
    """Tests for generate_subscription_licenses_dict."""

    def _license(self, **kwargs):
        fields = dict(
            name="lic1",
            start_date=JAN_2021_MS,
            expiration_date=JAN_2021_MS,
            last_updated_date=JAN_2021_MS,
            marketplace_partner=sdk_model("MarketplacePartner"),
            service_tier="//Block - Performance",
            subscription=sdk_model("FixedReference", id="sid-1", name="sub1"),
            average_on_demand=_metric(1, "B", "avg"),
            reservation=_metric(2, "B", "res"),
            usage=_metric(3, "B", "use"),
            quarter_on_demand=_metric(4, "B", "qod"),
            resources=[
                sdk_model(
                    "LicenseResourceReference",
                    id="rid-1",
                    name="res1",
                    resource_type="arrays",
                    fqdn="array1.example.com",
                    activation_time=JAN_2021_MS,
                    usage=_metric(10, "B", "used"),
                ),
                sdk_model(
                    "LicenseResourceReference",
                    name="res2",
                    activation_time=JAN_2021_MS,
                    usage=sdk_model(
                        "CurrentMetric",
                        unit="B",
                        metric=sdk_model("FixedReference", name="used"),
                    ),
                ),
            ],
        )
        fields.update(kwargs)
        return sdk_model("SubscriptionLicense", **fields)

    def _result(self, client, license):
        client.get_subscription_licenses.return_value = valid_response([license])
        return generate_subscription_licenses_dict(client)

    def test_maps_license_fields(self, pure1_client):
        result = self._result(pure1_client, self._license())

        assert set(result["lic1"]) == LICENSE_KEYS
        assert result["lic1"]["service_tier"] == "//Block - Performance"
        assert result["lic1"]["subscription"] == "sub1"
        assert result["lic1"]["start_date"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["expiration_date"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["last_updated"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["average_on_demand"] == {
            "data": 1,
            "unit": "B",
            "metric": "avg",
        }
        assert result["lic1"]["reservation"]["metric"] == "res"
        assert result["lic1"]["usage"]["metric"] == "use"
        assert result["lic1"]["quarter_on_demand"]["data"] == 4

    def test_each_date_comes_from_its_own_field(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(
                start_date=JAN_2021_MS,
                expiration_date=FEB_2021_MS,
                last_updated_date=MAR_2021_MS,
            ),
        )

        assert result["lic1"]["start_date"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["expiration_date"] == "2021-02-01 00:00:00 UTC"
        assert result["lic1"]["last_updated"] == "2021-03-01 00:00:00 UTC"

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_dates_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        result = self._result(pure1_client, self._license())

        assert result["lic1"]["start_date"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["expiration_date"] == "2021-01-01 00:00:00 UTC"
        assert result["lic1"]["last_updated"] == "2021-01-01 00:00:00 UTC"
        resource = result["lic1"]["resources"]["res1"]
        assert resource["activation_time"] == "2021-01-01 00:00:00 UTC"

    def test_empty_marketplace_partner_gives_none(self, pure1_client):
        result = self._result(pure1_client, self._license())

        assert result["lic1"]["marketplace_partner"] is None

    def test_marketplace_partner_name(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(
                marketplace_partner=sdk_model("MarketplacePartner", name="AWS")
            ),
        )

        assert result["lic1"]["marketplace_partner"] == "AWS"

    def test_location_comes_from_the_site_address(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(
                site_address=sdk_model(
                    "BaseAddress",
                    city="Example City",
                    state="EX",
                    country="EXAMPLE COUNTRY",
                )
            ),
        )

        assert result["lic1"]["location"] == {
            "city": "Example City",
            "state": "EX",
            "country": "EXAMPLE COUNTRY",
        }

    def test_location_and_energy_usage_are_none_without_site_address(
        self, pure1_client
    ):
        result = self._result(pure1_client, self._license())

        assert result["lic1"]["location"] is None
        assert result["lic1"]["energy_usage"] is None

    def test_empty_site_address_gives_no_location(self, pure1_client):
        result = self._result(
            pure1_client, self._license(site_address=sdk_model("BaseAddress"))
        )

        assert result["lic1"]["location"] is None

    def test_pre_ratio_is_returned_as_plain_values(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(
                pre_ratio=sdk_model("SubscriptionLicensePreRatio", data=3.45, unit="")
            ),
        )

        assert result["lic1"]["pre_ratio"] == {"data": 3.45, "unit": ""}

    def test_pre_ratio_is_none_when_not_set(self, pure1_client):
        result = self._result(pure1_client, self._license())

        assert result["lic1"]["pre_ratio"] is None

    def test_empty_pre_ratio_gives_none(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(pre_ratio=sdk_model("SubscriptionLicensePreRatio")),
        )

        assert result["lic1"]["pre_ratio"] is None

    def test_resources_are_nested_under_the_license(self, pure1_client):
        result = self._result(pure1_client, self._license())

        assert result["lic1"]["resources"] == {
            "res1": {
                "resource_type": "arrays",
                "fqdn": "array1.example.com",
                "activation_time": "2021-01-01 00:00:00 UTC",
                "usage": {"data": 10, "unit": "B", "metric": "used"},
            },
            "res2": {
                "resource_type": None,
                "fqdn": None,
                "activation_time": "2021-01-01 00:00:00 UTC",
                "usage": {"data": None, "unit": "B", "metric": "used"},
            },
        }

    def test_license_with_only_a_name(self, pure1_client):
        result = self._result(
            pure1_client, sdk_model("SubscriptionLicense", name="lic1")
        )

        assert set(result["lic1"]) == LICENSE_KEYS
        assert result["lic1"]["start_date"] is None
        assert result["lic1"]["service_tier"] is None
        assert result["lic1"]["subscription"] is None
        assert result["lic1"]["usage"] == {"data": None, "unit": None, "metric": None}
        assert result["lic1"]["resources"] == {}

    def test_result_holds_plain_values_only(self, pure1_client):
        result = self._result(
            pure1_client,
            self._license(
                pre_ratio=sdk_model("SubscriptionLicensePreRatio", data=3.45, unit=""),
                site_address=sdk_model("BaseAddress", city="Example City"),
            ),
        )

        json.dumps(result)

    def test_license_without_a_name_is_left_out(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscription_licenses.return_value = valid_response(
            [
                sdk_model("SubscriptionLicense", service_tier="//Block - Performance"),
                sdk_model("SubscriptionLicense", name="lic1"),
                sdk_model("SubscriptionLicense", start_date=JAN_2021_MS),
            ]
        )

        result = generate_subscription_licenses_dict(pure1_client, module)

        assert list(result) == ["lic1"]
        assert set(result["lic1"]) == LICENSE_KEYS
        module.warn.assert_called_once_with(
            "Licenses without a name were left out of the subscriptions subset: 2."
        )

    def test_resource_without_a_name_is_left_out_of_its_license(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscription_licenses.return_value = valid_response(
            [
                sdk_model(
                    "SubscriptionLicense",
                    name="lic1",
                    resources=[
                        sdk_model("LicenseResourceReference", resource_type="arrays"),
                        sdk_model("LicenseResourceReference", name="res1"),
                    ],
                ),
                sdk_model(
                    "SubscriptionLicense",
                    name="lic2",
                    resources=[
                        sdk_model("LicenseResourceReference", fqdn="array2.example.com")
                    ],
                ),
            ]
        )

        result = generate_subscription_licenses_dict(pure1_client, module)

        assert {name: entry["resources"] for name, entry in result.items()} == {
            "lic1": {
                "res1": {
                    "resource_type": None,
                    "fqdn": None,
                    "activation_time": None,
                    "usage": {"data": None, "unit": None, "metric": None},
                }
            },
            "lic2": {},
        }
        # one warning for the subset, whatever the number of licenses
        module.warn.assert_called_once_with(
            "License resources without a name were left out of the "
            "subscriptions subset: 2."
        )

    def test_license_and_resource_with_an_empty_name_are_reported(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscription_licenses.return_value = valid_response(
            [
                sdk_model(
                    "SubscriptionLicense",
                    name="",
                    resources=[
                        sdk_model(
                            "LicenseResourceReference", name="", resource_type="arrays"
                        )
                    ],
                )
            ]
        )

        result = generate_subscription_licenses_dict(pure1_client, module)

        # an empty string is a name that Pure1 sent: the object is reported
        assert list(result) == [""]
        assert result[""]["resources"] == {
            "": {
                "resource_type": "arrays",
                "fqdn": None,
                "activation_time": None,
                "usage": {"data": None, "unit": None, "metric": None},
            }
        }
        module.warn.assert_not_called()

    def test_empty_licenses_returns_empty_dict(self, pure1_client):
        pure1_client.get_subscription_licenses.return_value = valid_response()

        assert generate_subscription_licenses_dict(pure1_client) == {}

    def test_error_answer_is_raised(self, pure1_client):
        pure1_client.get_subscription_licenses.return_value = error_response(
            500, "Server error"
        )

        with pytest.raises(Pure1ResponseError):
            generate_subscription_licenses_dict(pure1_client)


class TestGenerateInvoicesDict:
    """Tests for generate_invoices_dict."""

    def _line(self, **kwargs):
        fields = dict(
            start_date=JAN_2021_MS,
            end_date=JAN_2021_MS,
            item="FA-X70",
            quantity=2,
            description="Flash storage",
            components=[
                sdk_model(
                    "InvoiceLineComponent",
                    item="Shelf",
                    quantity=1,
                    description="Shelf",
                ),
                sdk_model("InvoiceLineComponent", item="Cable"),
            ],
            unit_price=1000,
            amount=2000,
            tax=sdk_model("Tax", percentage=0.1, amount=200),
        )
        fields.update(kwargs)
        return sdk_model("InvoiceLine", **fields)

    def _invoice(self, **kwargs):
        fields = dict(
            id="INV-001",
            var_date=JAN_2021_MS,
            due_date=JAN_2021_MS,
            ship_date=JAN_2021_MS,
            status="paid",
            amount=2000,
            payment_terms="NET30",
            sales_representative="Jane Rep",
            partner_purchase_order="PO-1",
            end_user_purchase_order="PO-2",
            end_user_name="ACME",
            subscription=sdk_model("FixedReference", id="sid-1", name="sub1"),
            lines=[self._line()],
        )
        fields.update(kwargs)
        return sdk_model("Invoice", **fields)

    def _result(self, client, invoice):
        client.get_invoices.return_value = valid_response([invoice])
        return generate_invoices_dict(ansible_module(), client)

    def test_maps_invoice_header_fields(self, pure1_client):
        result = self._result(pure1_client, self._invoice())

        assert result["INV-001"] == {
            "lines": result["INV-001"]["lines"],
            "status": "paid",
            "amount": 2000,
            "date": "2021-01-01",
            "due_date": "2021-01-01",
            "ship_date": "2021-01-01",
            "payment_terms": "NET30",
            "sales_rep": "Jane Rep",
            "partner_po": "PO-1",
            "end_user_po": "PO-2",
            "end_user_name": "ACME",
            "subscription_id": "sid-1",
            "subscription_name": "sub1",
        }

    def test_each_date_comes_from_its_own_field(self, pure1_client):
        result = self._result(
            pure1_client,
            self._invoice(
                var_date=JAN_2021_MS,
                due_date=FEB_2021_MS,
                ship_date=MAR_2021_MS,
                lines=[self._line(start_date=FEB_2021_MS, end_date=MAR_2021_MS)],
            ),
        )

        assert result["INV-001"]["date"] == "2021-01-01"
        assert result["INV-001"]["due_date"] == "2021-02-01"
        assert result["INV-001"]["ship_date"] == "2021-03-01"
        assert result["INV-001"]["lines"][0]["start_date"] == "2021-02-01"
        assert result["INV-001"]["lines"][0]["end_date"] == "2021-03-01"

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_dates_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        result = self._result(
            pure1_client,
            self._invoice(
                var_date=JAN_2021_MS,
                due_date=FEB_2021_MS,
                ship_date=MAR_2021_MS,
                lines=[self._line(start_date=FEB_2021_MS, end_date=MAR_2021_MS)],
            ),
        )

        assert result["INV-001"]["date"] == "2021-01-01"
        assert result["INV-001"]["due_date"] == "2021-02-01"
        assert result["INV-001"]["ship_date"] == "2021-03-01"
        assert result["INV-001"]["lines"][0]["start_date"] == "2021-02-01"
        assert result["INV-001"]["lines"][0]["end_date"] == "2021-03-01"

    def test_invoice_date_is_read_from_var_date_alone(self):
        # no supported py-pure-client names the date of an invoice _date
        fields = generate_invoices_dict.__code__.co_consts

        assert "var_date" in fields
        assert "_date" not in fields

    def test_invoice_lines_are_appended(self, pure1_client):
        result = self._result(pure1_client, self._invoice())

        assert result["INV-001"]["lines"] == [
            {
                "item": "FA-X70",
                "quantity": 2,
                "description": "Flash storage",
                "start_date": "2021-01-01",
                "end_date": "2021-01-01",
                "components": [
                    {"item": "Shelf", "quantity": 1, "description": "Shelf"},
                    {"item": "Cable", "quantity": None, "description": None},
                ],
                "unit_price": 1000,
                "amount": 2000,
                "tax_percentage": 0.1,
                "tax_amount": 200,
                "tax_exemption_statement": None,
            }
        ]

    def test_result_holds_plain_values_only(self, pure1_client):
        json.dumps(self._result(pure1_client, self._invoice()))

    def test_invoice_with_only_an_id(self, pure1_client):
        result = self._result(pure1_client, sdk_model("Invoice", id="INV-001"))

        assert result["INV-001"] == {
            "lines": [],
            "status": None,
            "amount": 0,
            "date": None,
            "due_date": None,
            "ship_date": None,
            "payment_terms": None,
            "sales_rep": None,
            "partner_po": None,
            "end_user_po": None,
            "end_user_name": None,
            "subscription_id": None,
            "subscription_name": None,
        }

    def test_line_without_tax_and_components(self, pure1_client):
        result = self._result(
            pure1_client,
            self._invoice(lines=[sdk_model("InvoiceLine", item="FA-X70")]),
        )

        assert result["INV-001"]["lines"] == [
            {
                "item": "FA-X70",
                "quantity": 0,
                "description": None,
                "start_date": None,
                "end_date": None,
                "components": None,
                "unit_price": 0,
                "amount": 0,
                "tax_percentage": None,
                "tax_amount": None,
                "tax_exemption_statement": None,
            }
        ]

    def test_unset_tax_fields_keep_their_defaults(self, pure1_client):
        result = self._result(
            pure1_client,
            self._invoice(
                lines=[self._line(tax=sdk_model("Tax", exemption_statement="Exempt"))],
            ),
        )

        line = result["INV-001"]["lines"][0]
        assert line["tax_percentage"] == 0
        assert line["tax_amount"] == 0
        assert line["tax_exemption_statement"] == "Exempt"

    def test_invoice_without_a_number_is_left_out(self, pure1_client):
        module = ansible_module()
        pure1_client.get_invoices.return_value = valid_response(
            [
                sdk_model("Invoice", status="open"),
                self._invoice(),
                sdk_model("Invoice", lines=[self._line()]),
            ]
        )

        result = generate_invoices_dict(module, pure1_client)

        assert list(result) == ["INV-001"]
        assert result["INV-001"]["status"] == "paid"
        assert len(result["INV-001"]["lines"]) == 1
        module.warn.assert_called_once_with(
            "Invoices without a number were left out of the invoices subset: 2."
        )

    def test_invoice_with_an_empty_number_is_reported(self, pure1_client):
        module = ansible_module()
        pure1_client.get_invoices.return_value = valid_response(
            [sdk_model("Invoice", id="", status="open")]
        )

        result = generate_invoices_dict(module, pure1_client)

        # an empty string is a number that Pure1 sent: the invoice is reported
        assert list(result) == [""]
        assert result[""]["status"] == "open"
        module.warn.assert_not_called()

    def test_no_invoices_returns_empty_dict(self, pure1_client):
        pure1_client.get_invoices.return_value = valid_response()

        assert generate_invoices_dict(ansible_module(), pure1_client) == {}

    def test_non_200_response_returns_empty_dict(self, pure1_client):
        module = ansible_module()
        pure1_client.get_invoices.return_value = error_response(
            500, "Internal server error"
        )

        assert generate_invoices_dict(module, pure1_client) == {}
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "invoices" in warning
        assert "500" in warning
        assert "Internal server error" in warning
        module.fail_json.assert_not_called()

    def test_403_returns_empty_dict_with_a_warning(self, pure1_client):
        module = ansible_module()
        pure1_client.get_invoices.return_value = error_response(403)

        assert generate_invoices_dict(module, pure1_client) == {}
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "invoices" in warning
        assert "403" in warning
        assert "None" not in warning
        module.fail_json.assert_not_called()


FLASHARRAY_METRICS = {
    "array_read_bandwidth": [5242880, 11088932.0],
    "array_write_bandwidth": [1048576],
    "array_read_latency_us": [1234],
    "array_write_latency_us": [250],
    "array_read_iops": [1000.4],
    "array_write_iops": [1999.6],
    "array_total_load": [0.4567891],
}


def _points(*values):
    """The data of a series, one point per value"""
    return [[JAN_2021_MS + index * 180000, value] for index, value in enumerate(values)]


def _series(metric, *values):
    """A metric answer with one series, one point per value"""
    return valid_response(
        [sdk_model("MetricHistory", name=metric, data=_points(*values))]
    )


def _appliances_client(client, arrays, tags=None, metrics=None):
    """Give the client its answers for the appliances subset.

    tags maps an array name to its tags. metrics maps a metric name to the
    values of its series, or to a function that returns the answer.
    """
    tags = tags or {}
    metrics = metrics or {}

    def arrays_tags(resource_names):
        return valid_response(tags.get(resource_names[0], []))

    def metrics_history(names, **kwargs):
        answer = metrics.get(names[0], [])
        if callable(answer):
            return answer()
        if answer:
            return _series(names[0], *answer)
        return valid_response()

    client.get_arrays.return_value = valid_response(arrays)
    client.get_arrays_tags.side_effect = arrays_tags
    client.get_metrics_history.side_effect = metrics_history
    return client


def _array(name, os):
    return sdk_model(
        "Array",
        name=name,
        os=os,
        version="6.5.0",
        model="FA-X70R4",
        fqdn=name + ".example.com",
    )


class TestGenerateAppliancesDict:
    """Tests for generate_appliances_dict."""

    def test_appliances_are_grouped_by_os(self, pure1_client):
        client = _appliances_client(
            pure1_client,
            [
                _array("fa1", "Purity//FA"),
                _array("fa2", "Purity"),
                _array("fb1", "Purity//FB"),
                _array("oe1", "Elasticity"),
            ],
        )

        result = generate_appliances_dict(ansible_module(), client)

        assert sorted(result["FlashArray"]) == ["fa1", "fa2"]
        assert sorted(result["FlashBlade"]) == ["fb1"]
        assert sorted(result["ObjectEngine"]) == ["oe1"]
        assert result["FlashArray"]["fa1"] == {
            "os_version": "6.5.0",
            "model": "FA-X70R4",
            "fqdn": "fa1.example.com",
            "tags": [],
        }

    def test_array_without_fqdn(self, pure1_client):
        client = _appliances_client(
            pure1_client,
            [
                sdk_model(
                    "Array",
                    name="fa1",
                    os="Purity//FA",
                    version="6.5.0",
                    model="FA-X70R4",
                )
            ],
        )

        result = generate_appliances_dict(ansible_module(), client)

        assert result["FlashArray"]["fa1"]["fqdn"] == ""

    @pytest.mark.parametrize(
        "array_os, section",
        [
            ("Purity//FA", "FlashArray"),
            ("Purity//FB", "FlashBlade"),
            ("Elasticity", "ObjectEngine"),
        ],
    )
    def test_tags_are_read_with_get_arrays_tags(self, pure1_client, array_os, section):
        client = _appliances_client(
            pure1_client,
            [_array("a1", array_os)],
            tags={
                "a1": [
                    sdk_model("Tag", key="site", value="lab", namespace="pure1"),
                    sdk_model("Tag", key="owner", value="ops", tag_organization_id=42),
                ]
            },
        )

        result = generate_appliances_dict(ansible_module(), client)

        client.get_arrays_tags.assert_called_once_with(resource_names=["a1"])
        assert result[section]["a1"]["tags"] == [
            {"key": "site", "value": "lab", "org_id": None, "namespace": "pure1"},
            {"key": "owner", "value": "ops", "org_id": 42, "namespace": None},
        ]

    def test_error_answer_for_tags_leaves_them_empty(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(pure1_client, [_array("fa1", "Purity//FA")])
        client.get_arrays_tags.side_effect = None
        client.get_arrays_tags.return_value = error_response(400, "Bad request")

        result = generate_appliances_dict(module, client)

        assert result["FlashArray"]["fa1"]["tags"] == []
        module.warn.assert_called_once_with(
            "Tags not collected for fa1. Status code: 400. Error: Bad request"
        )

    @pytest.mark.parametrize(
        "array_os, section",
        [
            ("Purity//FA", "FlashArray"),
            ("Purity//FB", "FlashBlade"),
            ("Elasticity", "ObjectEngine"),
        ],
    )
    def test_failed_tag_call_gives_a_warning_for_each_array(
        self, pure1_client, array_os, section
    ):
        module = ansible_module()
        client = _appliances_client(
            pure1_client, [_array("a1", array_os), _array("a2", array_os)]
        )
        client.get_arrays_tags.side_effect = [
            error_response(403),
            valid_response([sdk_model("Tag", key="site", value="lab")]),
        ]

        result = generate_appliances_dict(module, client)

        assert result[section] == {
            "a1": {
                "os_version": "6.5.0",
                "model": "FA-X70R4",
                "fqdn": "a1.example.com",
                "tags": [],
            },
            "a2": {
                "os_version": "6.5.0",
                "model": "FA-X70R4",
                "fqdn": "a2.example.com",
                "tags": [
                    {"key": "site", "value": "lab", "org_id": None, "namespace": None}
                ],
            },
        }
        module.warn.assert_called_once_with(
            "Tags not collected for a1. Status code: 403."
        )

    @pytest.mark.parametrize(
        "array_os, section",
        [
            ("Purity//FA", "FlashArray"),
            ("Purity//FB", "FlashBlade"),
            ("Elasticity", "ObjectEngine"),
        ],
    )
    def test_array_with_only_a_name_and_an_os(self, pure1_client, array_os, section):
        module = ansible_module()
        client = _appliances_client(
            pure1_client, [sdk_model("Array", name="a1", os=array_os)]
        )

        result = generate_appliances_dict(module, client)

        assert result[section] == {
            "a1": {"os_version": None, "model": None, "fqdn": "", "tags": []}
        }
        module.warn.assert_not_called()

    def test_array_without_an_os_gives_a_warning(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(pure1_client, [sdk_model("Array", name="a1")])

        result = generate_appliances_dict(module, client)

        assert result == {"FlashArray": {}, "FlashBlade": {}, "ObjectEngine": {}}
        module.warn.assert_called_once_with("Unknown operating system detected: None.")
        client.get_arrays_tags.assert_not_called()

    def test_array_without_a_name_is_left_out_before_any_call(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client,
            [
                sdk_model("Array", os="Purity//FA", version="6.5.0"),
                sdk_model("Array", os="Purity//FB"),
                sdk_model("Array", os="Elasticity"),
                # without a name the operating system is not looked at
                sdk_model("Array", model="FA-X70R4"),
                _array("fa1", "Purity//FA"),
            ],
        )

        result = generate_appliances_dict(module, client)

        assert result == {
            "FlashArray": {
                "fa1": {
                    "os_version": "6.5.0",
                    "model": "FA-X70R4",
                    "fqdn": "fa1.example.com",
                    "tags": [],
                }
            },
            "FlashBlade": {},
            "ObjectEngine": {},
        }
        # tags and metrics are asked for by array name
        client.get_arrays_tags.assert_called_once_with(resource_names=["fa1"])
        calls = client.get_metrics_history.call_args_list
        assert [call[1]["resource_names"] for call in calls] == [["fa1"]] * 7
        module.warn.assert_called_once_with(
            "Arrays without a name were left out of the appliances subset: 4."
        )

    def test_tag_without_a_key_is_left_out(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client,
            [
                _array("fa1", "Purity//FA"),
                _array("fb1", "Purity//FB"),
                _array("oe1", "Elasticity"),
            ],
            tags={
                "fa1": [
                    sdk_model("Tag", value="lab"),
                    sdk_model("Tag", key="site", value="lab"),
                    sdk_model("Tag", value="ops"),
                ],
                "fb1": [sdk_model("Tag", namespace="pure1")],
                "oe1": [
                    sdk_model("Tag", key="owner", value="ops"),
                    sdk_model("Tag", tag_organization_id=42),
                ],
            },
        )

        result = generate_appliances_dict(module, client)

        assert result["FlashArray"]["fa1"]["tags"] == [
            {"key": "site", "value": "lab", "org_id": None, "namespace": None}
        ]
        assert result["FlashBlade"]["fb1"]["tags"] == []
        assert result["ObjectEngine"]["oe1"]["tags"] == [
            {"key": "owner", "value": "ops", "org_id": None, "namespace": None}
        ]
        # one warning for the subset, whatever the number of arrays
        module.warn.assert_called_once_with(
            "Tags without a key were left out of the appliances subset: 4."
        )

    def test_tag_without_a_value(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client,
            [_array("fa1", "Purity//FA")],
            tags={"fa1": [sdk_model("Tag", key="site")]},
        )

        result = generate_appliances_dict(module, client)

        assert result["FlashArray"]["fa1"]["tags"] == [
            {"key": "site", "value": None, "org_id": None, "namespace": None}
        ]
        module.warn.assert_not_called()

    def test_array_with_an_empty_name_is_reported(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client, [sdk_model("Array", name="", os="Purity//FB")]
        )

        result = generate_appliances_dict(module, client)

        # an empty string is a name that Pure1 sent: the object is reported
        assert result == {
            "FlashArray": {},
            "FlashBlade": {
                "": {"os_version": None, "model": None, "fqdn": "", "tags": []}
            },
            "ObjectEngine": {},
        }
        module.warn.assert_not_called()

    def test_tag_with_an_empty_key_is_reported(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client,
            [_array("fa1", "Purity//FA")],
            tags={"fa1": [sdk_model("Tag", key="", value="lab")]},
        )

        result = generate_appliances_dict(module, client)

        # an empty string is a key that Pure1 sent: the tag is reported
        assert result["FlashArray"]["fa1"]["tags"] == [
            {"key": "", "value": "lab", "org_id": None, "namespace": None}
        ]
        module.warn.assert_not_called()

    def test_flasharray_metrics(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(
            pure1_client, [_array("fa1", "Purity//FA")], metrics=FLASHARRAY_METRICS
        )

        result = generate_appliances_dict(module, client)

        # The latest point of each series: B/s to MiB/s, us to ms, ratio to %
        assert result["FlashArray"]["fa1"] == {
            "os_version": "6.5.0",
            "model": "FA-X70R4",
            "fqdn": "fa1.example.com",
            "tags": [],
            "bandwidth (read) [MB/s]": 10.575,
            "bandwidth (write) [MB/s]": 1.0,
            "latency (read) [ms]": 1.23,
            "latency (write) [ms]": 0.25,
            "iops (read)": 1000,
            "iops (write)": 2000,
            "load [%]": 45.679,
        }
        module.warn.assert_not_called()

    def test_flashblade_metrics(self, pure1_client):
        client = _appliances_client(
            pure1_client, [_array("fb1", "Purity//FB")], metrics=FLASHARRAY_METRICS
        )

        result = generate_appliances_dict(ansible_module(), client)

        assert result["FlashBlade"]["fb1"] == {
            "os_version": "6.5.0",
            "model": "FA-X70R4",
            "fqdn": "fb1.example.com",
            "tags": [],
            "bandwidth (read) [MB/s]": 10.575,
            "bandwidth (write) [MB/s]": 1.0,
            "iops (read)": 1000,
            "iops (write)": 2000,
            "latency (read) [ms]": 1.23,
            "latency (write) [ms]": 0.25,
        }

    def test_object_engine_holds_the_fields_of_the_array(self, pure1_client):
        client = _appliances_client(
            pure1_client, [_array("oe1", "Elasticity")], metrics=FLASHARRAY_METRICS
        )

        result = generate_appliances_dict(ansible_module(), client)

        assert result["ObjectEngine"]["oe1"] == {
            "os_version": "6.5.0",
            "model": "FA-X70R4",
            "fqdn": "oe1.example.com",
            "tags": [],
        }

    def test_metric_calls_ask_for_one_metric_of_one_array(self, pure1_client):
        client = _appliances_client(pure1_client, [_array("fa1", "Purity//FA")])

        generate_appliances_dict(ansible_module(), client)

        calls = client.get_metrics_history.call_args_list
        assert [call[1]["names"] for call in calls] == [
            ["array_read_bandwidth"],
            ["array_write_bandwidth"],
            ["array_read_latency_us"],
            ["array_write_latency_us"],
            ["array_read_iops"],
            ["array_write_iops"],
            ["array_total_load"],
        ]
        for call in calls:
            assert call[1]["resource_names"] == ["fa1"]
            assert call[1]["aggregation"] == "max"
            assert call[1]["resolution"] == 180000
            assert call[1]["end_time"] - call[1]["start_time"] == 18000000

    def test_metric_window_is_the_five_hours_up_to_now(self, pure1_client):
        client = _appliances_client(pure1_client, [_array("fa1", "Purity//FA")])

        before = int(time.time()) * 1000
        generate_appliances_dict(ansible_module(), client)
        after = int(time.time()) * 1000

        calls = client.get_metrics_history.call_args_list
        assert len(calls) == 7
        for call in calls:
            assert before <= call[1]["end_time"] <= after
            assert before <= call[1]["start_time"] + 18000000 <= after

    def test_object_engine_has_no_metric_calls(self, pure1_client):
        client = _appliances_client(pure1_client, [_array("oe1", "Elasticity")])

        generate_appliances_dict(ansible_module(), client)

        client.get_metrics_history.assert_not_called()

    def test_failed_metric_call_skips_the_key_with_a_warning(self, pure1_client):
        module = ansible_module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_read_latency_us"] = lambda: error_response(404, "No such metric")
        client = _appliances_client(
            pure1_client, [_array("fa1", "Purity//FA")], metrics=metrics
        )

        result = generate_appliances_dict(module, client)

        array = result["FlashArray"]["fa1"]
        assert "latency (read) [ms]" not in array
        assert array["latency (write) [ms]"] == 0.25
        assert array["load [%]"] == 45.679
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "array_read_latency_us" in warning
        assert "fa1" in warning
        assert "404" in warning
        assert "No such metric" in warning

    def test_failed_metric_warning_carries_the_error_context(self, pure1_client):
        module = ansible_module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_total_load"] = lambda: error_response(
            404, "Not found", "No arrays with name: fa1"
        )
        client = _appliances_client(
            pure1_client, [_array("fa1", "Purity//FA")], metrics=metrics
        )

        generate_appliances_dict(module, client)

        module.warn.assert_called_once_with(
            "Metric array_total_load not collected for fa1. Status code: 404. "
            "Error: Not found: No arrays with name: fa1"
        )

    @pytest.mark.parametrize(
        "answer",
        [
            valid_response,
            lambda: valid_response(
                [sdk_model("MetricHistory", name="array_read_iops")]
            ),
            lambda: valid_response(
                [sdk_model("MetricHistory", name="array_read_iops", data=[])]
            ),
            # Pure1 can send the two nulls: the SDK does not validate an answer
            lambda: valid_response(
                [
                    _received_model(
                        "MetricHistory",
                        name="array_read_iops",
                        data=_points(1000, None),
                    )
                ]
            ),
            lambda: valid_response(
                [
                    sdk_model(
                        "MetricHistory", name="array_read_iops", data=[[JAN_2021_MS]]
                    )
                ]
            ),
            lambda: valid_response(
                [_received_model("MetricHistory", name="array_read_iops", data=[None])]
            ),
        ],
        ids=[
            "no series",
            "series without data",
            "empty data",
            "null value",
            "point without a value",
            "null point",
        ],
    )
    def test_metric_without_a_value_skips_the_key(self, pure1_client, answer):
        module = ansible_module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_read_iops"] = answer
        client = _appliances_client(
            pure1_client, [_array("fa1", "Purity//FA")], metrics=metrics
        )

        result = generate_appliances_dict(module, client)

        array = result["FlashArray"]["fa1"]
        assert "iops (read)" not in array
        assert array["iops (write)"] == 2000
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "array_os, section, count",
        [("Purity//FA", "FlashArray", 7), ("Purity//FB", "FlashBlade", 6)],
    )
    def test_zero_metric_values_are_returned(
        self, pure1_client, array_os, section, count
    ):
        metrics = {metric: [0] for metric in FLASHARRAY_METRICS}
        client = _appliances_client(
            pure1_client, [_array("a1", array_os)], metrics=metrics
        )

        result = generate_appliances_dict(ansible_module(), client)

        array = result[section]["a1"]
        metric_keys = set(array) - {"os_version", "model", "fqdn", "tags"}
        assert len(metric_keys) == count
        assert [array[key] for key in metric_keys] == [0] * count

    def test_unknown_os_gives_a_warning(self, pure1_client):
        module = ansible_module()
        client = _appliances_client(pure1_client, [_array("x1", "Other")])

        result = generate_appliances_dict(module, client)

        assert result == {"FlashArray": {}, "FlashBlade": {}, "ObjectEngine": {}}
        module.warn.assert_called_once_with("Unknown operating system detected: Other.")

    def test_error_answer_for_the_arrays_is_raised(self, pure1_client):
        pure1_client.get_arrays.return_value = error_response(500, "Server error")

        with pytest.raises(Pure1ResponseError):
            generate_appliances_dict(ansible_module(), pure1_client)


class TestCollectSubset:
    """Tests for collect_subset, the rule for an error answer."""

    def test_returns_the_section(self):
        module = ansible_module()

        result = collect_subset(module, "minimum", lambda value: {"a": value}, 1)

        assert result == {"a": 1}
        module.warn.assert_not_called()

    def test_403_returns_the_section_empty_with_a_warning(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = error_response(403)

        result = collect_subset(
            module, "subscriptions", generate_subscriptions_dict, pure1_client
        )

        assert result == {}
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "subscriptions" in warning
        assert "403" in warning
        assert warning == (
            "No permission to collect the subscriptions subset (status code 403). "
            "The section is returned empty."
        )
        module.fail_json.assert_not_called()

    def test_other_error_fails_with_the_api_message(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = error_response(
            500, "Internal server error"
        )

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        message = module.fail_json.call_args[1]["msg"]
        assert "subscriptions" in message
        assert "500" in message
        assert "Internal server error" in message
        module.warn.assert_not_called()

    def test_failure_carries_the_error_context(self, pure1_client):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = error_response(
            400, "Invalid filter", "filter=name='sub1"
        )

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to collect the subscriptions subset. Status code: 400. "
            "Error: Invalid filter: filter=name='sub1"
        )

    @pytest.mark.parametrize(
        "answer",
        [ErrorResponse(502, [], {}), error_response(502)],
        ids=["no errors", "error without a message"],
    )
    def test_error_answer_without_a_message_still_fails(self, pure1_client, answer):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = answer

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to collect the subscriptions subset. Status code: 502."
        )

    @pytest.mark.parametrize("status", [401, 404])
    def test_no_other_refusal_returns_the_section_empty(self, pure1_client, status):
        module = ansible_module()
        pure1_client.get_subscriptions.return_value = error_response(status, "Refused")

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        module.fail_json.assert_called_once_with(
            msg="Failed to collect the subscriptions subset. "
            "Status code: {0}. Error: Refused".format(status)
        )
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "error, text",
        [
            (PureError("boom"), "PureError: boom"),
            (
                ProtocolError("Connection aborted."),
                "ProtocolError: Connection aborted.",
            ),
            (
                PureError("(500)\nReason: Server Error\nHTTP response body: {}\n"),
                "PureError: (500) Reason: Server Error",
            ),
        ],
        ids=["SDK error", "transport error", "text of several lines"],
    )
    def test_raised_error_fails_with_one_line(self, pure1_client, error, text):
        module = ansible_module()
        pure1_client.get_subscriptions.side_effect = error

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        module.fail_json.assert_called_once_with(
            msg="Failed to collect the subscriptions subset. " + text
        )
        module.warn.assert_not_called()

    def test_error_while_the_items_are_read_fails_with_one_line(self, pure1_client):
        module = ansible_module()

        def later_page():
            yield sdk_model("Subscription", name="sub1")
            raise PureError("boom")

        answer = valid_response()
        answer.items = later_page()
        pure1_client.get_subscriptions.return_value = answer

        with pytest.raises(SystemExit):
            collect_subset(
                module, "subscriptions", generate_subscriptions_dict, pure1_client
            )

        module.fail_json.assert_called_once_with(
            msg="Failed to collect the subscriptions subset. PureError: boom"
        )


# The listing and count calls of each subset but invoices, with the section
# of the result each one fills: (subset, client method, section)
LISTING_CALLS = [
    ("minimum", "get_arrays", "default"),
    ("minimum", "get_volumes", "default"),
    ("minimum", "get_volume_snapshots", "default"),
    ("minimum", "get_file_systems", "default"),
    ("minimum", "get_file_system_snapshots", "default"),
    ("minimum", "get_buckets", "default"),
    ("minimum", "get_directories", "default"),
    ("minimum", "get_pods", "default"),
    ("minimum", "get_object_store_accounts", "default"),
    ("appliances", "get_arrays", "appliances"),
    ("subscriptions", "get_subscriptions", "subscriptions"),
    ("subscriptions", "get_subscription_licenses", "subscription_licenses"),
    ("contracts", "get_arrays", "contracts"),
    ("contracts", "get_arrays_support_contracts", "contracts"),
    ("environmental", "get_assessment_sustainability_arrays", "environmental"),
    (
        "environmental",
        "get_assessment_sustainability_insights_arrays",
        "environmental",
    ),
]

# Every client call of each subset: (subset, client method)
CLIENT_CALLS = [call[:2] for call in LISTING_CALLS] + [
    ("appliances", "get_arrays_tags"),
    ("appliances", "get_metrics_history"),
    ("invoices", "get_invoices"),
]

# The sections of the result that each subset fills
SUBSET_SECTIONS = {
    "minimum": {"default"},
    "appliances": {"appliances"},
    "subscriptions": {"subscriptions", "subscription_licenses"},
    "contracts": {"contracts"},
    "environmental": {"environmental"},
    "invoices": {"invoices"},
}

# What the minimum subset returns for the answers of TestMain._client
MINIMUM_SECTION = {
    "FlashArrays": 1,
    "FlashBlades": 1,
    "ObjectEngines": 0,
    "volumes": 3,
    "volume_snapshots": 3,
    "filesystems": 3,
    "filesystem_snapshots": 3,
    "buckets": 3,
    "directories": 3,
    "pods": 3,
    "object_store_accounts": 3,
}


class TestMain:
    """Tests for main."""

    def _client(self, client):
        arrays = [_array("fa1", "Purity//FA"), _array("fb1", "Purity//FB")]

        def get_arrays(**kwargs):
            # get_pure1 makes the first call, for the count alone
            if kwargs == {"limit": 0}:
                return valid_response(total_item_count=len(arrays))
            # each subset that lists arrays makes one more, without arguments
            if kwargs:
                raise TypeError("get_arrays() was called with {0}".format(kwargs))
            return valid_response(arrays)

        client.get_arrays.side_effect = get_arrays
        for method in (
            "get_volumes",
            "get_volume_snapshots",
            "get_file_systems",
            "get_file_system_snapshots",
            "get_buckets",
            "get_directories",
            "get_pods",
            "get_object_store_accounts",
        ):
            getattr(client, method).return_value = valid_response(total_item_count=3)
        client.get_arrays_tags.side_effect = lambda resource_names: valid_response(
            [sdk_model("Tag", key="site", value="lab", namespace="pure1")]
        )
        client.get_metrics_history.side_effect = lambda names, **kwargs: _series(
            names[0], 1048576
        )
        client.get_subscriptions.return_value = valid_response(
            [
                sdk_model(
                    "Subscription",
                    name="sub1",
                    start_date=JAN_2021_MS,
                    expiration_date=JAN_2021_MS,
                    service="Evergreen//One",
                    status="active",
                    organization=sdk_model("FixedReference", name="ACME"),
                )
            ]
        )
        client.get_subscription_licenses.return_value = valid_response(
            [
                sdk_model(
                    "SubscriptionLicense",
                    name="lic1",
                    start_date=JAN_2021_MS,
                    expiration_date=JAN_2021_MS,
                    last_updated_date=JAN_2021_MS,
                    marketplace_partner=sdk_model("MarketplacePartner"),
                    pre_ratio=sdk_model(
                        "SubscriptionLicensePreRatio", data=3.45, unit=""
                    ),
                    resources=[
                        sdk_model(
                            "LicenseResourceReference",
                            name="res1",
                            activation_time=JAN_2021_MS,
                        )
                    ],
                )
            ]
        )
        client.get_arrays_support_contracts.side_effect = (
            lambda **kwargs: valid_response()
        )
        client.get_assessment_sustainability_arrays.return_value = valid_response()
        client.get_assessment_sustainability_insights_arrays.return_value = (
            valid_response()
        )
        client.get_invoices.return_value = error_response(403)
        return client

    def _answer_with(self, client, method, answer):
        """Give one listing call an error answer on every call main() makes.

        get_pure1 checks the new client with get_arrays(limit=0) and fails the
        task itself on an error answer, so that call keeps a valid one. The
        one subset the test asks for then makes the second call.
        """
        if method == "get_arrays":
            client.get_arrays.side_effect = [valid_response(), answer]
        else:
            _answer_with(client, method, answer)

    def test_all_completes_when_invoices_answer_403(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=["all"])

        module.exit_json.assert_called_once()
        result = module.exit_json.call_args[1]
        assert result["changed"] is False
        info = result["pure1_info"]
        assert set(info) == {
            "default",
            "appliances",
            "subscriptions",
            "subscription_licenses",
            "contracts",
            "environmental",
            "invoices",
        }
        assert info["invoices"] == {}
        assert info["default"]["FlashArrays"] == 1
        assert info["appliances"]["FlashArray"]["fa1"]["bandwidth (read) [MB/s]"] == 1.0
        assert info["subscriptions"]["sub1"]["org_name"] == "ACME"
        assert info["subscription_licenses"]["lic1"]["pre_ratio"]["data"] == 3.45
        assert info["contracts"]["fa1"] == {
            "contract_state": "Expired",
            "contract_found": False,
        }
        json.dumps(info)
        module.warn.assert_called_once()
        assert "invoices" in module.warn.call_args[0][0]
        assert "403" in module.warn.call_args[0][0]

    def test_gather_subset_defaults_to_minimum(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_info={"default": MINIMUM_SECTION}
        )

    def test_subsets_are_collected_in_check_mode(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, check_mode=True, gather_subset=["minimum"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_info={"default": MINIMUM_SECTION}
        )

    @pytest.mark.parametrize("subset", sorted(SUBSET_SECTIONS))
    def test_subset_returns_its_own_sections_only(self, pure1_client, subset):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=[subset])

        info = module.exit_json.call_args[1]["pure1_info"]
        assert set(info) == SUBSET_SECTIONS[subset]

    @pytest.mark.parametrize(
        "subset, calls",
        [
            ("minimum", 2),
            ("appliances", 2),
            ("subscriptions", 1),
            ("contracts", 2),
            ("environmental", 1),
            ("invoices", 1),
        ],
    )
    def test_arrays_are_listed_only_for_a_subset_that_reports_them(
        self, pure1_client, subset, calls
    ):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=[subset])

        module.exit_json.assert_called_once()
        # one of the calls is the check of get_pure1
        assert pure1_client.get_arrays.call_count == calls

    def test_subset_names_are_matched_in_any_case(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=["Contracts", "MINIMUM"])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_info={
                "default": MINIMUM_SECTION,
                "contracts": {
                    "fa1": {"contract_state": "Expired", "contract_found": False},
                    "fb1": {"contract_state": "Expired", "contract_found": False},
                },
            },
        )

    def test_unknown_subset_fails_the_task(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=["minimum", "volumes"])

        module.fail_json.assert_called_once_with(
            msg="gather_subset must be one or more of: all, minimum, appliances, "
            "subscriptions, contracts, environmental, invoices, got: minimum, volumes"
        )
        module.exit_json.assert_not_called()
        # nothing was collected: the one call is the check of get_pure1
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_volumes.assert_not_called()

    def test_other_error_fails_the_task(self, pure1_client):
        client = self._client(pure1_client)
        client.get_subscription_licenses.return_value = error_response(
            500, "Internal server error"
        )

        module = run_main(pure1_info, gather_subset=["all"])

        assert "Internal server error" in module.fail_json.call_args[1]["msg"]
        # the message shows that the module failed, not get_pure1 before it
        module.fail_json.assert_called_once_with(
            msg="Failed to collect the subscriptions subset. Status code: 500. "
            "Error: Internal server error"
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("subset, method", [call[:2] for call in LISTING_CALLS])
    def test_error_answer_on_a_listing_call_fails_the_task(
        self, pure1_client, subset, method
    ):
        client = self._client(pure1_client)
        self._answer_with(client, method, error_response(500, "boom"))

        module = run_main(pure1_info, gather_subset=[subset])

        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to collect the {0} subset. Status code: 500. "
            "Error: boom".format(subset)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("subset, method, section", LISTING_CALLS)
    def test_403_on_a_listing_call_returns_the_section_empty(
        self, pure1_client, subset, method, section
    ):
        client = self._client(pure1_client)
        self._answer_with(client, method, error_response(403))

        module = run_main(pure1_info, gather_subset=[subset])

        module.exit_json.assert_called_once()
        info = module.exit_json.call_args[1]["pure1_info"]
        assert info[section] == {}
        assert all(info[key] for key in info if key != section)
        module.warn.assert_called_once()
        assert subset in module.warn.call_args[0][0]
        assert "403" in module.warn.call_args[0][0]

    def test_invoices_error_returns_the_section_empty(self, pure1_client):
        client = self._client(pure1_client)
        client.get_invoices.return_value = error_response(500, "Internal server error")

        module = run_main(pure1_info, gather_subset=["invoices"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_info={"invoices": {}}
        )
        module.warn.assert_called_once_with(
            "Failed to collect the invoices subset, the section is returned "
            "empty. Status code: 500. Error: Internal server error"
        )

    @pytest.mark.parametrize("subset, method", CLIENT_CALLS)
    def test_raised_error_on_a_client_call_fails_the_task(
        self, pure1_client, subset, method
    ):
        client = self._client(pure1_client)
        if method == "get_arrays":
            # the first call is the check of get_pure1, which fails by itself
            client.get_arrays.side_effect = [valid_response(), PureError("boom")]
        else:
            getattr(client, method).side_effect = PureError("boom")

        module = run_main(pure1_info, gather_subset=[subset])

        module.fail_json.assert_called_once_with(
            msg="Failed to collect the {0} subset. PureError: boom".format(subset)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_main_uses_every_generate_function(self):
        used = pure1_info.main.__code__.co_names
        functions = [name for name in vars(pure1_info) if name.startswith("generate_")]

        assert len(functions) == 7
        assert [name for name in functions if name not in used] == []

    def test_raised_error_in_one_subset_of_all_fails_the_task(self, pure1_client):
        client = self._client(pure1_client)
        client.get_invoices.side_effect = ProtocolError("Connection aborted.")

        module = run_main(pure1_info, gather_subset=["all"])

        module.fail_json.assert_called_once_with(
            msg="Failed to collect the invoices subset. "
            "ProtocolError: Connection aborted."
        )
        module.exit_json.assert_not_called()


# The SDK model classes whose fields the module reads
MODEL_CLASSES = [
    "Array",
    "Tag",
    "MetricHistory",
    "SupportContract",
    "Subscription",
    "SubscriptionLicense",
    "LicenseResourceReference",
    "CurrentMetric",
    "MarketplacePartner",
    "BaseAddress",
    "SubscriptionLicensePreRatio",
    "FixedReference",
    "FixedReferenceFqdn",
    "SustainabilityArray",
    "InstallAddress",
    "Geolocation",
    "SustainabilityAssessment",
    "SustainabilityInsightArray",
    "Invoice",
    "InvoiceLine",
    "InvoiceLineComponent",
    "Tax",
]

# What each subset warns of for the answers of TestObjectWithoutAnyField
EMPTY_OBJECT_WARNINGS = {
    "minimum": [],
    "appliances": [
        "Arrays without a name were left out of the appliances subset: 1.",
        "Tags without a key were left out of the appliances subset: 4.",
    ],
    "subscriptions": [
        "Subscriptions without a name were left out of the subscriptions subset: 1.",
        "Licenses without a name were left out of the subscriptions subset: 1.",
        "License resources without a name were left out of the subscriptions subset: 2.",
    ],
    "contracts": ["Arrays without a name were left out of the contracts subset: 1."],
    "environmental": [
        "Arrays without a name were left out of the environmental subset: 1.",
        "Insights without an array name were left out of the environmental subset: 2.",
    ],
    "invoices": ["Invoices without a number were left out of the invoices subset: 1."],
}


class TestObjectWithoutAnyField:
    """Pure1 returns an object without any of its fields among complete ones.

    main() collects each subset twice: from complete objects, and from the
    same objects with an empty one of every class the subset reads among
    them. The second result is the first plus the entries of the objects
    named hollow, which have a name and hold the empty nested objects, and
    the warnings say what was left out.
    """

    def _arrays(self, client, arrays):
        def get_arrays(**kwargs):
            # the first call is the check of get_pure1, for the count alone
            if kwargs == {"limit": 0}:
                return valid_response(total_item_count=len(arrays))
            return valid_response(arrays)

        client.get_arrays.side_effect = get_arrays

    def _minimum(self, client, empty):
        arrays = [_array("fa1", "Purity//FA"), _array("fb1", "Purity//FB")]
        if empty:
            arrays.insert(0, sdk_model("Array"))
        self._arrays(client, arrays)
        for method in (
            "get_volumes",
            "get_volume_snapshots",
            "get_file_systems",
            "get_file_system_snapshots",
            "get_buckets",
            "get_directories",
            "get_pods",
            "get_object_store_accounts",
        ):
            getattr(client, method).return_value = valid_response(total_item_count=3)
        return {}

    def _appliances(self, client, empty):
        arrays = [
            _array("fa1", "Purity//FA"),
            _array("fb1", "Purity//FB"),
            _array("oe1", "Elasticity"),
        ]
        tags = {
            name: [
                sdk_model(
                    "Tag",
                    key="site",
                    value="lab",
                    namespace="pure1",
                    tag_organization_id=42,
                )
            ]
            for name in ("fa1", "fb1", "oe1")
        }
        if empty:
            arrays.insert(0, sdk_model("Array"))
            arrays.append(sdk_model("Array", name="hollow1", os="Purity//FA"))
            tags["hollow1"] = []
            for tags_of_array in tags.values():
                tags_of_array.insert(0, sdk_model("Tag"))
        self._arrays(client, arrays)

        def arrays_tags(resource_names):
            return valid_response(tags[resource_names[0]])

        def metrics_history(names, resource_names, **kwargs):
            series = sdk_model(
                "MetricHistory",
                name=names[0],
                data=_points(*FLASHARRAY_METRICS[names[0]]),
            )
            if resource_names == ["hollow1"]:
                # the module reads the first series of an answer
                return valid_response([sdk_model("MetricHistory"), series])
            return valid_response([series])

        client.get_arrays_tags.side_effect = arrays_tags
        client.get_metrics_history.side_effect = metrics_history
        return {
            ("appliances", "FlashArray", "hollow1"): {
                "os_version": None,
                "model": None,
                "fqdn": "",
                "tags": [],
            }
        }

    def _subscriptions(self, client, empty):
        subscriptions = [
            TestGenerateSubscriptionsDict()._subscription(
                organization=sdk_model("FixedReference", id="org-1", name="ACME"),
                partner_name="PartnerCo",
                subscription_term=36,
            )
        ]
        licenses = [
            TestGenerateSubscriptionLicensesDict()._license(
                site_address=sdk_model("BaseAddress", city="Example City"),
                pre_ratio=sdk_model("SubscriptionLicensePreRatio", data=3.45, unit=""),
            )
        ]
        if empty:
            subscriptions.insert(0, sdk_model("Subscription"))
            subscriptions.append(
                sdk_model(
                    "Subscription",
                    name="hollow1",
                    organization=sdk_model("FixedReference"),
                )
            )
            licenses[0].resources.insert(0, sdk_model("LicenseResourceReference"))
            licenses.insert(0, sdk_model("SubscriptionLicense"))
            licenses.append(
                sdk_model(
                    "SubscriptionLicense",
                    name="hollow1",
                    marketplace_partner=sdk_model("MarketplacePartner"),
                    site_address=sdk_model("BaseAddress"),
                    pre_ratio=sdk_model("SubscriptionLicensePreRatio"),
                    subscription=sdk_model("FixedReference"),
                    average_on_demand=sdk_model("CurrentMetric"),
                    reservation=sdk_model(
                        "CurrentMetric", metric=sdk_model("FixedReference")
                    ),
                    usage=sdk_model("CurrentMetric"),
                    quarter_on_demand=sdk_model("CurrentMetric"),
                    resources=[
                        sdk_model("LicenseResourceReference"),
                        sdk_model(
                            "LicenseResourceReference",
                            name="hollow2",
                            usage=sdk_model("CurrentMetric"),
                        ),
                    ],
                )
            )
        client.get_subscriptions.return_value = valid_response(subscriptions)
        client.get_subscription_licenses.return_value = valid_response(licenses)
        no_metric = {"data": None, "unit": None, "metric": None}
        return {
            ("subscriptions", "hollow1"): {
                "start_date": None,
                "expiration_date": None,
                "service": None,
                "status": None,
                "org_name": None,
                "partner_name": None,
                "subscription_term": None,
            },
            ("subscription_licenses", "hollow1"): {
                "start_date": None,
                "expiration_date": None,
                "last_updated": None,
                "marketplace_partner": None,
                "service_tier": None,
                "location": None,
                "pre_ratio": None,
                "energy_usage": None,
                "subscription": None,
                "average_on_demand": no_metric,
                "reservation": no_metric,
                "usage": no_metric,
                "quarter_on_demand": no_metric,
                "resources": {
                    "hollow2": {
                        "resource_type": None,
                        "fqdn": None,
                        "activation_time": None,
                        "usage": no_metric,
                    }
                },
            },
        }

    def _contracts(self, client, empty):
        arrays = [_array("fa1", "Purity//FA"), _array("fb1", "Purity//FB")]
        if empty:
            arrays.insert(0, sdk_model("Array"))
            arrays.append(sdk_model("Array", name="hollow1"))
        self._arrays(client, arrays)

        def support_contracts(**kwargs):
            contracts = [
                sdk_model(
                    "SupportContract", start_date=JAN_2021_MS, end_date=FEB_2021_MS
                )
            ]
            if kwargs == {"filter": "resource.name='hollow1'"}:
                # the module reads the first record of an answer
                contracts.insert(0, sdk_model("SupportContract"))
            return valid_response(contracts)

        client.get_arrays_support_contracts.side_effect = support_contracts
        return {
            ("contracts", "hollow1"): {
                "contract_start": None,
                "contract_end": None,
                "contract_state": "Expired",
                "contract_found": True,
            }
        }

    def _environmental(self, client, empty):
        appliances = [
            sdk_model(
                "SustainabilityArray",
                name="fa1",
                reporting_status="assessment_ready",
                install_address=sdk_model(
                    "InstallAddress",
                    geolocation=sdk_model(
                        "Geolocation", latitude=37.4, longitude=-122.1
                    ),
                    street_address="1 Main Street",
                    updated=JAN_2021_MS,
                ),
                assessment=sdk_model(
                    "SustainabilityAssessment",
                    assessment_level="good",
                    power_average=512.5,
                    interval_start=JAN_2021_MS,
                    interval_end=FEB_2021_MS,
                ),
            )
        ]
        insights = [
            sdk_model(
                "SustainabilityInsightArray",
                resource=sdk_model(
                    "FixedReferenceFqdn", name="fa1", fqdn="fa1.example.com"
                ),
                type="high_power_consumption",
                severity="medium",
                additional_data={"power": 512.5},
            )
        ]
        if empty:
            appliances.insert(0, sdk_model("SustainabilityArray"))
            appliances.append(
                sdk_model(
                    "SustainabilityArray",
                    name="hollow1",
                    reporting_status="assessment_ready",
                    install_address=sdk_model("InstallAddress"),
                    assessment=sdk_model("SustainabilityAssessment"),
                )
            )
            appliances.append(
                sdk_model(
                    "SustainabilityArray",
                    name="hollow2",
                    install_address=sdk_model(
                        "InstallAddress", geolocation=sdk_model("Geolocation")
                    ),
                )
            )
            insights.insert(0, sdk_model("SustainabilityInsightArray"))
            insights.append(
                sdk_model(
                    "SustainabilityInsightArray",
                    resource=sdk_model("FixedReferenceFqdn"),
                )
            )
        client.get_assessment_sustainability_arrays.return_value = valid_response(
            appliances
        )
        client.get_assessment_sustainability_insights_arrays.return_value = (
            valid_response(insights)
        )
        no_location = {
            "longitude": None,
            "latitude": None,
            "updated": None,
            "address": None,
        }
        return {
            ("environmental", "hollow1"): {
                "insights": [],
                "location": no_location,
                "assessment": dict.fromkeys(
                    (
                        "array_data_reduction",
                        "assessment_level",
                        "blades",
                        "capacity_utilization",
                        "chassis",
                        "power_average",
                        "power_per_usable_capacity",
                        "power_per_used_space",
                        "power_typical_spec",
                        "power_peak_spec",
                        "heat_typical_spec",
                        "heat_peak_spec",
                        "heat_average",
                        "rack_units",
                        "shelves",
                        "array_total_load",
                        "start",
                        "end",
                    )
                ),
                "reporting_status": "assessment_ready",
            },
            ("environmental", "hollow2"): {
                "insights": [],
                "location": no_location,
                "assessment": {},
                "reporting_status": None,
            },
        }

    def _invoices(self, client, empty):
        invoices = [TestGenerateInvoicesDict()._invoice()]
        if empty:
            invoices.insert(0, sdk_model("Invoice"))
            invoices.append(
                sdk_model(
                    "Invoice",
                    id="hollow1",
                    subscription=sdk_model("FixedReference"),
                    lines=[
                        sdk_model("InvoiceLine"),
                        sdk_model(
                            "InvoiceLine",
                            components=[sdk_model("InvoiceLineComponent")],
                            tax=sdk_model("Tax"),
                        ),
                    ],
                )
            )
        client.get_invoices.return_value = valid_response(invoices)
        no_line = {
            "item": None,
            "quantity": 0,
            "description": None,
            "start_date": None,
            "end_date": None,
            "components": None,
            "unit_price": 0,
            "amount": 0,
            "tax_percentage": None,
            "tax_amount": None,
            "tax_exemption_statement": None,
        }
        no_component = {"item": None, "quantity": None, "description": None}
        return {
            ("invoices", "hollow1"): {
                "lines": [
                    no_line,
                    dict(
                        no_line,
                        components=[no_component],
                        tax_percentage=0,
                        tax_amount=0,
                    ),
                ],
                "status": None,
                "amount": 0,
                "date": None,
                "due_date": None,
                "ship_date": None,
                "payment_terms": None,
                "sales_rep": None,
                "partner_po": None,
                "end_user_po": None,
                "end_user_name": None,
                "subscription_id": None,
                "subscription_name": None,
            }
        }

    @pytest.mark.parametrize("subset", sorted(SUBSET_SECTIONS))
    def test_subset_returns_the_complete_objects_with_a_warning(
        self, pure1_client, subset
    ):
        answers = getattr(self, "_" + subset)
        answers(pure1_client, False)
        complete = run_main(pure1_info, gather_subset=[subset])
        complete.exit_json.assert_called_once()
        complete.warn.assert_not_called()
        expected = complete.exit_json.call_args[1]["pure1_info"]
        assert set(expected) == SUBSET_SECTIONS[subset]
        assert all(expected.values())

        hollow = answers(pure1_client, True)
        module = run_main(pure1_info, gather_subset=[subset])

        for path, entry in hollow.items():
            section = expected
            for key in path[:-1]:
                section = section[key]
            assert path[-1] not in section
            section[path[-1]] = entry
        module.exit_json.assert_called_once_with(changed=False, pure1_info=expected)
        warnings = [call[0][0] for call in module.warn.call_args_list]
        assert warnings == EMPTY_OBJECT_WARNINGS[subset]

    def test_no_field_of_an_sdk_model_is_read_without_a_default(self):
        # reading a field that Pure1 did not send raises AttributeError
        fields = set()
        for name in MODEL_CLASSES:
            fields |= set(getattr(MODELS, name).__fields__)
        tree = ast.parse(inspect.getsource(pure1_info))
        read = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert {"name", "id", "key", "value", "fqdn", "lines"} <= fields
        assert {"items", "status_code", "get_arrays", "warn"} <= read
        assert sorted(read & fields) == []


# What each subset warns of for the answers of TestNullAmongTheObjects
NULL_ENTRY_WARNINGS = {
    "minimum": [],
    "appliances": [
        "Arrays without a name were left out of the appliances subset: 1.",
        "Tags without a key were left out of the appliances subset: 2.",
    ],
    "subscriptions": [
        "Subscriptions without a name were left out of the subscriptions subset: 1.",
        "Licenses without a name were left out of the subscriptions subset: 1.",
        "License resources without a name were left out of the subscriptions subset: 1.",
    ],
    "contracts": ["Arrays without a name were left out of the contracts subset: 1."],
    "environmental": [
        "Arrays without a name were left out of the environmental subset: 1.",
        "Insights without an array name were left out of the environmental subset: 1.",
    ],
    "invoices": ["Invoices without a number were left out of the invoices subset: 1."],
}


class TestNullAmongTheObjects:
    """Pure1 returns a null among the objects of a listing.

    The SDK does not check what Pure1 sends, so the null reaches the module
    as None. It has no name: a subset leaves it out and counts it as it does
    an object without its name, and the minimum subset counts it in no type.
    """

    def _answers(self, client, null):
        nulls = [None] if null else []
        answers = TestObjectWithoutAnyField()
        answers._minimum(client, False)
        answers._arrays(
            client, nulls + [_array("fa1", "Purity//FA"), _array("fb1", "Purity//FB")]
        )
        client.get_arrays_tags.side_effect = lambda resource_names: valid_response(
            nulls + [sdk_model("Tag", key="site", value="lab")]
        )
        client.get_metrics_history.side_effect = lambda names, **kwargs: _series(
            names[0], 1048576
        )
        client.get_arrays_support_contracts.side_effect = (
            lambda **kwargs: valid_response()
        )
        client.get_subscriptions.return_value = valid_response(
            nulls + [sdk_model("Subscription", name="sub1")]
        )
        license = sdk_model(
            "SubscriptionLicense",
            name="lic1",
            resources=[sdk_model("LicenseResourceReference", name="res1")],
        )
        if null:
            # the constructor of the model refuses a null in a list
            license.resources.insert(0, None)
        client.get_subscription_licenses.return_value = valid_response(
            nulls + [license]
        )
        client.get_assessment_sustainability_arrays.return_value = valid_response(
            nulls + [sdk_model("SustainabilityArray", name="fa1")]
        )
        client.get_assessment_sustainability_insights_arrays.return_value = (
            valid_response(
                nulls
                + [
                    sdk_model(
                        "SustainabilityInsightArray",
                        resource=sdk_model("FixedReferenceFqdn", name="fa1"),
                    )
                ]
            )
        )
        client.get_invoices.return_value = valid_response(
            nulls + [sdk_model("Invoice", id="INV-001")]
        )

    @pytest.mark.parametrize("subset", sorted(SUBSET_SECTIONS))
    def test_null_is_left_out_and_counted_like_an_object_without_its_name(
        self, pure1_client, subset
    ):
        self._answers(pure1_client, False)
        complete = run_main(pure1_info, gather_subset=[subset])
        complete.exit_json.assert_called_once()
        complete.warn.assert_not_called()
        expected = complete.exit_json.call_args[1]["pure1_info"]
        assert set(expected) == SUBSET_SECTIONS[subset]
        assert all(expected.values())

        self._answers(pure1_client, True)
        module = run_main(pure1_info, gather_subset=[subset])

        module.exit_json.assert_called_once_with(changed=False, pure1_info=expected)
        warnings = [call[0][0] for call in module.warn.call_args_list]
        assert warnings == NULL_ENTRY_WARNINGS[subset]


# The levels of the result whose keys are names: of arrays, subscriptions,
# licenses, the resources of a license and invoices
NAMED_LEVELS = [
    ("appliances", "FlashArray"),
    ("appliances", "FlashBlade"),
    ("appliances", "ObjectEngine"),
    ("subscriptions",),
    ("subscription_licenses",),
    ("subscription_licenses", "resources"),
    ("contracts",),
    ("environmental",),
    ("invoices",),
]

# What Pure1 defines and the module returns as it is
OPAQUE_VALUES = [("environmental", "insights", "insight_data")]

# The keys whose value Pure1 leaves out for some objects, and RETURN says so
NULL_KEYS = [
    ("appliances", "FlashArray", "tags", "org_id"),
    ("appliances", "FlashBlade", "tags", "org_id"),
    ("appliances", "ObjectEngine", "tags", "org_id"),
    ("subscriptions", "partner_name"),
    ("subscription_licenses", "marketplace_partner"),
    ("subscription_licenses", "resources", "resource_type"),
    ("subscription_licenses", "resources", "fqdn"),
    ("subscription_licenses", "resources", "usage", "data"),
    ("environmental", "assessment", "array_total_load"),
]


def _returned_keys(value, path=(), named=True):
    """Return the path of every key of a result, the names in it left out"""
    keys = set()
    if isinstance(value, list):
        for entry in value:
            keys |= _returned_keys(entry, path, named)
    elif isinstance(value, dict) and path not in OPAQUE_VALUES:
        if named and path in NAMED_LEVELS:
            # one entry for each name: the keys of the entries count
            for entry in value.values():
                keys |= _returned_keys(entry, path, False)
        else:
            for key, entry in value.items():
                keys.add(path + (key,))
                keys |= _returned_keys(entry, path + (key,))
    return keys


def _documented_keys(contains, path=()):
    """Return the path of every key under a contains block of RETURN"""
    keys = set()
    for key, entry in contains.items():
        keys.add(path + (key,))
        keys |= _documented_keys(entry.get("contains", {}), path + (key,))
    return keys


def _described_keys(sections):
    """Return the path of every key that RETURN names in a description

    The keys of the performance figures are no identifiers. validate-modules
    refuses such a key under contains, so the description of the appliance
    type names them, each between single quotes.
    """
    keys = set()
    for kind in ("FlashArray", "FlashBlade"):
        description = sections["appliances"]["contains"][kind]["description"]
        for key in re.findall(r"'([^']+)'", " ".join(description)):
            keys.add(("appliances", kind, key))
    return keys


class TestDocumentation:
    """DOCUMENTATION, EXAMPLES and RETURN against the code."""

    def _client(self, client):
        """Answer every call with objects that have each field the module reads"""
        client = TestMain()._client(client)
        arrays = [
            _array("fa1", "Purity//FA"),
            _array("fb1", "Purity//FB"),
            _array("oe1", "Elasticity"),
        ]
        client.get_arrays.side_effect = lambda **kwargs: valid_response(arrays)
        client.get_subscription_licenses.return_value = valid_response(
            [
                TestGenerateSubscriptionLicensesDict()._license(
                    site_address=sdk_model("BaseAddress", city="Example City"),
                    pre_ratio=sdk_model(
                        "SubscriptionLicensePreRatio", data=3.45, unit=""
                    ),
                )
            ]
        )
        client.get_arrays_support_contracts.side_effect = (
            lambda **kwargs: valid_response(
                [
                    sdk_model(
                        "SupportContract", start_date=JAN_2021_MS, end_date=FEB_2021_MS
                    )
                ]
            )
        )
        TestGenerateEsgDict()._client(client)
        client.get_invoices.return_value = valid_response(
            [TestGenerateInvoicesDict()._invoice()]
        )
        return client

    def test_return_documents_the_keys_of_the_result(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=["all"])

        module.warn.assert_not_called()
        returned = _returned_keys(module.exit_json.call_args[1]["pure1_info"])
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        documented = _documented_keys(sections) | _described_keys(sections)
        assert sorted(returned - documented) == []
        assert sorted(documented - returned) == []

    def test_samples_of_return_hold_documented_keys(self):
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        samples = {name: section["sample"] for name, section in sections.items()}
        documented = _documented_keys(sections) | _described_keys(sections)

        json.dumps(samples)
        assert sorted(_returned_keys(samples) - documented) == []

    def test_every_key_under_contains_can_be_read_with_dot_notation(self):
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]

        for path in _documented_keys(sections):
            assert path[-1].isidentifier(), path
        assert len(_described_keys(sections)) == 13

    def test_sample_of_a_tax_is_a_ratio_the_sdk_model_can_hold(self):
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        line = sections["invoices"]["sample"]["1234567"]["lines"][0]
        documented = sections["invoices"]["contains"]["lines"]["contains"]

        # Pure1 gives the tax rate as a fraction of 1, the SDK model refuses more
        sdk_model("Tax", percentage=line["tax_percentage"], amount=line["tax_amount"])
        assert line["tax_amount"] == pytest.approx(
            line["amount"] * line["tax_percentage"]
        )
        description = " ".join(documented["tax_percentage"]["description"])
        assert "fraction of 1" in description
        assert "in percent" not in description

    def test_sample_location_names_no_real_place(self):
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        sample = sections["subscription_licenses"]["sample"]["license1"]

        assert sample["location"] == {
            "city": "Example City",
            "state": "EX",
            "country": "EXAMPLE COUNTRY",
        }

    @pytest.mark.parametrize("path", NULL_KEYS, ids=".".join)
    def test_return_says_which_values_come_back_null(self, path):
        entry = yaml.safe_load(pure1_info.RETURN)["pure1_info"]
        for key in path:
            entry = entry["contains"][key]
        description = entry["description"]
        if isinstance(description, list):
            description = " ".join(description)

        assert "null when Pure1 has none" in description

    def test_return_says_what_a_line_without_components_holds(self, pure1_client):
        pure1_client.get_invoices.return_value = valid_response(
            [
                sdk_model(
                    "Invoice",
                    id="INV-001",
                    lines=[
                        sdk_model("InvoiceLine", item="FA-X70"),
                        sdk_model("InvoiceLine", item="Shelf", components=[]),
                    ],
                )
            ]
        )

        result = generate_invoices_dict(ansible_module(), pure1_client)

        lines = result["INV-001"]["lines"]
        assert [line["components"] for line in lines] == [None, []]
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        documented = sections["invoices"]["contains"]["lines"]["contains"]
        assert (
            "null or empty when it has none" in documented["components"]["description"]
        )

    def test_new_return_key_says_when_it_was_added(self):
        sections = yaml.safe_load(pure1_info.RETURN)["pure1_info"]["contains"]
        contract_found = sections["contracts"]["contains"]["contract_found"]

        assert "Added in version 1.6.0." in contract_found["description"]

    def test_return_says_that_an_object_without_its_name_is_left_out(self):
        returned = yaml.safe_load(pure1_info.RETURN)["pure1_info"]
        description = " ".join(returned["description"])

        # the default section lists no object: it counts an array without a name
        assert (
            "An array, subscription, license or resource of a license that Pure1 "
            "returns without a name, an invoice without a number, a tag without a "
            "key and an insight without an array name are left out of the sections "
            "that list them, with a warning that counts them."
        ) in description

    def test_examples_register_at_task_level_and_read_what_was_registered(self):
        tasks = yaml.safe_load(pure1_info.EXAMPLES)
        options = set(pure1_argument_spec()) | {"gather_subset"}
        registered = set()

        for task in tasks:
            action = set(task) - {"name", "register"}
            if action == {"everpure.pure1.pure1_info"}:
                assert set(task["everpure.pure1.pure1_info"] or {}) <= options
                registered.add(task["register"])
            else:
                assert action == {"ansible.builtin.debug"}
                read = re.findall(r"{{\s*(\w+)", task["ansible.builtin.debug"]["msg"])
                assert read and set(read) <= registered
        assert tasks[0]["everpure.pure1.pure1_info"] == {
            "app_id": "pure1:apikey:dssf2331sd",
            "key_file": "/home/user/private.pem",
        }

    def test_description_says_what_the_minimum_subset_returns(self):
        documentation = yaml.safe_load(pure1_info.DOCUMENTATION)
        description = " ".join(documentation["description"])
        subsets = " ".join(documentation["options"]["gather_subset"]["description"])

        # the minimum subset holds counts, under the key default
        assert "data reduction" not in description
        assert "returned under the key C(default)" in description
        assert "returned under the key C(default)" in subsets
        assert "arguements" not in description
        assert "from a Pure1" not in description

    def test_module_has_no_metadata_block(self):
        assert not hasattr(pure1_info, "ANSIBLE_METADATA")
