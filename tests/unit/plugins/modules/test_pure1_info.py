# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_info module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import datetime
import json
import time

import pytest
from pypureclient import ErrorResponse

from plugins.modules import pure1_info
from plugins.modules.pure1_info import (
    Pure1ResponseError,
    collect_subset,
    generate_appliances_dict,
    generate_default_dict,
    generate_esg_dict,
    generate_subscriptions_dict,
    generate_subscription_assets_dict,
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


class _SwaggerInvoice:
    """Invoice of a py-pure-client whose models were generated with swagger.

    Those models name the date of the invoice _date. The installed SDK cannot
    build one, so this object alone is written by hand. Like an SDK model it
    raises AttributeError for a field that was not set.
    """

    def __init__(self, **fields):
        vars(self).update(fields)


def _answer_with(client, method, answer):
    """Make one client method return the given error answer on every call"""
    getattr(client, method).side_effect = None
    getattr(client, method).return_value = answer


def _local_date(epoch_ms):
    return datetime.datetime.fromtimestamp(int(epoch_ms / 1000)).strftime("%Y-%m-%d")


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

    def test_empty_subscriptions_returns_empty_dict(self, pure1_client):
        pure1_client.get_subscriptions.return_value = valid_response()

        assert generate_subscriptions_dict(pure1_client) == {}

    def test_error_answer_is_raised(self, pure1_client):
        pure1_client.get_subscriptions.return_value = error_response(400, "Bad request")

        with pytest.raises(Pure1ResponseError):
            generate_subscriptions_dict(pure1_client)


# generate_subscription_assets_dict is not called by the module. The asset is
# the SDK model: it has none of the eight fields the function reads on it.
class TestGenerateSubscriptionAssetsDict:
    """Tests for generate_subscription_assets_dict."""

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D035 assets code reads eight fields SubscriptionAsset does not have",
    )
    def test_maps_asset_fields_and_activation_date(self, pure1_client):
        pure1_client.get_subscription_assets.return_value = valid_response(
            [
                sdk_model(
                    "SubscriptionAsset",
                    name="asset1",
                    activation_date=JAN_2021_MS,
                    # Pure1 sends these three under array, not on the asset
                    array=sdk_model(
                        "SubscriptionAssetArray",
                        version="1.2.3",
                        model="FA-X70",
                        chassis_serial_number="CH-001",
                    ),
                    subscription=sdk_model("FixedReference", name="sub1", id="sid-1"),
                    license=sdk_model("FixedReference", name="lic1", id="lid-1"),
                )
            ]
        )

        result = generate_subscription_assets_dict(pure1_client)

        assert result["asset1"]["install_location"] == "DC1"
        assert result["asset1"]["activation_date"] == "2021-01-01 00:00:00 UTC"
        assert result["asset1"]["model"] == "FA-X70"
        assert result["asset1"]["subscription_name"] == "sub1"
        assert result["asset1"]["subscription_id"] == "sid-1"
        assert result["asset1"]["license_name"] == "lic1"

    def test_empty_assets_returns_empty_dict(self, pure1_client):
        pure1_client.get_subscription_assets.return_value = valid_response()

        assert generate_subscription_assets_dict(pure1_client) == {}


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
            "contract_start": _local_date(JAN_2021_MS),
            "contract_end": _local_date(FEB_2021_MS),
            "contract_state": "Expired",
        }

    def test_no_contract_data_is_expired(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="foo")]
        )
        pure1_client.get_arrays_support_contracts.return_value = valid_response()

        result = generate_contract_dict(pure1_client)

        assert result["foo"] == {"contract_state": "Expired"}

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

    def test_ready_appliance_with_an_insight(self, pure1_client):
        result = generate_esg_dict(ansible_module(), self._client(pure1_client))

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
                    city="Mountain View",
                    state="CA",
                    country="UNITED STATES",
                )
            ),
        )

        assert result["lic1"]["location"] == {
            "city": "Mountain View",
            "state": "CA",
            "country": "UNITED STATES",
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
                site_address=sdk_model("BaseAddress", city="Mountain View"),
            ),
        )

        json.dumps(result)

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
            "date": _local_date(JAN_2021_MS),
            "due_date": _local_date(JAN_2021_MS),
            "ship_date": _local_date(JAN_2021_MS),
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

        assert result["INV-001"]["date"] == _local_date(JAN_2021_MS)
        assert result["INV-001"]["due_date"] == _local_date(FEB_2021_MS)
        assert result["INV-001"]["ship_date"] == _local_date(MAR_2021_MS)
        assert result["INV-001"]["lines"][0]["start_date"] == _local_date(FEB_2021_MS)
        assert result["INV-001"]["lines"][0]["end_date"] == _local_date(MAR_2021_MS)

    def test_date_is_read_from_an_older_sdk_model(self, pure1_client):
        # py-pure-client models generated with swagger name the field _date
        invoice = _SwaggerInvoice(id="INV-001", _date=JAN_2021_MS)

        result = self._result(pure1_client, invoice)

        assert result["INV-001"]["date"] == _local_date(JAN_2021_MS)

    def test_invoice_lines_are_appended(self, pure1_client):
        result = self._result(pure1_client, self._invoice())

        assert result["INV-001"]["lines"] == [
            {
                "item": "FA-X70",
                "quantity": 2,
                "description": "Flash storage",
                "start_date": _local_date(JAN_2021_MS),
                "end_date": _local_date(JAN_2021_MS),
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
        client = _appliances_client(pure1_client, [_array("fa1", "Purity//FA")])
        client.get_arrays_tags.side_effect = None
        client.get_arrays_tags.return_value = error_response(400, "Bad request")

        result = generate_appliances_dict(ansible_module(), client)

        assert result["FlashArray"]["fa1"]["tags"] == []

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
        assert info["contracts"]["fa1"] == {"contract_state": "Expired"}
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
                    "fa1": {"contract_state": "Expired"},
                    "fb1": {"contract_state": "Expired"},
                },
            },
        )

    def test_unknown_subset_fails_the_task(self, pure1_client):
        self._client(pure1_client)

        module = run_main(pure1_info, gather_subset=["minimum", "volumes"])

        # The wording is the one the module has (D037)
        module.fail_json.assert_called_once_with(
            msg="value must gather_subset must be one or more of: all,minimum,"
            "appliances,subscriptions,contracts,environmental,invoices, "
            "got: minimum,volumes"
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
