# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_info module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import datetime
import json
import sys
import time
import types
import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock external dependencies before importing the module
sys.modules["ansible"] = MagicMock()
sys.modules["ansible.module_utils"] = MagicMock()
sys.modules["ansible.module_utils.basic"] = MagicMock()
sys.modules["pypureclient"] = MagicMock()
sys.modules["pypureclient.pure1"] = MagicMock()
sys.modules["ansible_collections"] = MagicMock()
sys.modules["ansible_collections.everpure"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins.module_utils"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins.module_utils.pure1"] = (
    MagicMock()
)

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


class _Model:
    """Strict stand-in for a py-pure-client model.

    It takes only the fields the SDK model has, and reading a field that was
    not set raises AttributeError, as the SDK models do for an unset field.
    """

    fields = ()

    def __init__(self, **kwargs):
        unknown = sorted(set(kwargs) - set(self.fields))
        if unknown:
            raise TypeError("not a field of the SDK model: " + ", ".join(unknown))
        for key, value in kwargs.items():
            if value is not None:
                setattr(self, key, value)


def _sdk_model(name, fields):
    return type(name, (_Model,), {"fields": tuple(fields.split())})


# Field names of the models in pypureclient/pure1/Pure1_1_7/models (1.99.0)
Array = _sdk_model("Array", "as_of id name fleet fqdn model os topology_group version")
Tag = _sdk_model("Tag", "key namespace resource tag_organization_id value")
MetricHistory = _sdk_model(
    "MetricHistory", "as_of id name aggregation data resolution resources unit"
)
FixedReference = _sdk_model("FixedReference", "id name resource_type")
SupportContract = _sdk_model("SupportContract", "end_date resource start_date")
Subscription = _sdk_model(
    "Subscription",
    "as_of id name customer_name expiration_date initial_name last_updated_date "
    "organization partner_name service service_details start_date status "
    "subscription_term",
)
SubscriptionLicense = _sdk_model(
    "SubscriptionLicense",
    "as_of id name add_on_slas average_on_demand expiration_date "
    "last_updated_date marketplace_partner pre_ratio quarter_on_demand "
    "reservation resources service_tier site_address start_date subscription usage",
)
MarketplacePartner = _sdk_model("MarketplacePartner", "name reference_id")
BaseAddress = _sdk_model("BaseAddress", "city country state")
SubscriptionLicensePreRatio = _sdk_model(
    "SubscriptionLicensePreRatio", "data metric unit"
)
CurrentMetric = _sdk_model("CurrentMetric", "data metric unit")
LicenseResourceReference = _sdk_model(
    "LicenseResourceReference", "id name resource_type fqdn activation_time usage"
)
Invoice = _sdk_model(
    "Invoice",
    "amount currency var_date due_date end_user_name end_user_purchase_order id "
    "lines partner_purchase_order payment_terms sales_representative ship_date "
    "status subscription",
)
InvoiceLine = _sdk_model(
    "InvoiceLine",
    "amount components description end_date item quantity start_date tax unit_price",
)
InvoiceLineComponent = _sdk_model("InvoiceLineComponent", "description item quantity")
Tax = _sdk_model("Tax", "amount exemption_statement percentage")
SustainabilityArray = _sdk_model(
    "SustainabilityArray", "as_of id name assessment install_address reporting_status"
)
SustainabilityAssessment = _sdk_model(
    "SustainabilityAssessment",
    "as_of interval_end interval_start array_data_reduction array_total_load "
    "assessment_level blades capacity_utilization chassis heat_average "
    "heat_peak_spec heat_typical_spec power_average power_peak_spec "
    "power_per_usable_capacity power_per_used_space power_typical_spec "
    "rack_units shelves",
)
InstallAddress = _sdk_model("InstallAddress", "geolocation street_address updated")
Geolocation = _sdk_model("Geolocation", "latitude longitude")
SustainabilityInsightArray = _sdk_model(
    "SustainabilityInsightArray",
    "as_of interval_end interval_start additional_data resource severity type",
)
FixedReferenceFqdn = _sdk_model("FixedReferenceFqdn", "id name resource_type fqdn")

# The methods of pypureclient.pure1.Client that pure1_info calls. A client
# stand-in raises AttributeError for any other name.
CLIENT_METHODS = [
    "get_arrays",
    "get_arrays_support_contracts",
    "get_arrays_tags",
    "get_assessment_sustainability_arrays",
    "get_assessment_sustainability_insights_arrays",
    "get_buckets",
    "get_directories",
    "get_file_system_snapshots",
    "get_file_systems",
    "get_invoices",
    "get_metrics_history",
    "get_object_store_accounts",
    "get_pods",
    "get_subscription_assets",
    "get_subscription_licenses",
    "get_subscriptions",
    "get_volume_snapshots",
    "get_volumes",
]

# 2021-01-01 00:00:00 UTC expressed in milliseconds
JAN_2021_MS = 1609459200000


class _FailJson(Exception):
    """Raised by the stand-in for module.fail_json, which ends the module"""


def _client():
    return Mock(spec=CLIENT_METHODS)


def _module(gather_subset=None):
    module = Mock(spec=["params", "warn", "fail_json", "exit_json"])
    module.params = {"gather_subset": gather_subset or ["minimum"]}

    def fail_json(**kwargs):
        raise _FailJson(kwargs["msg"])

    module.fail_json.side_effect = fail_json
    return module


def _ok(items=None, total_item_count=None):
    """Stand-in for a ValidResponse; items is an iterator, as in the SDK"""
    items = list(items or [])
    if total_item_count is None:
        total_item_count = len(items)
    return types.SimpleNamespace(
        status_code=200, items=iter(items), total_item_count=total_item_count
    )


def _error(status_code, message=None, context=None):
    """Stand-in for an ErrorResponse; it has errors and no items"""
    return types.SimpleNamespace(
        status_code=status_code,
        errors=[types.SimpleNamespace(context=context, message=message)],
    )


def _answer_with(client, method, answer):
    """Make one client method return the given answer on every call"""
    getattr(client, method).side_effect = None
    getattr(client, method).return_value = answer


def _local_date(epoch_ms):
    return datetime.datetime.fromtimestamp(int(epoch_ms / 1000)).strftime("%Y-%m-%d")


class TestStandIns:
    """The fixtures must not invent what the SDK does not have."""

    def test_model_rejects_a_field_the_sdk_model_does_not_have(self):
        with pytest.raises(TypeError):
            Subscription(name="sub1", org_name="ACME")

    def test_unset_field_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            getattr(Tag(key="site", value="lab"), "tag_organization_id")

    def test_client_has_no_get_array_tags(self):
        with pytest.raises(AttributeError):
            getattr(_client(), "get_array_tags")


class TestGenerateDefaultDict:
    """Tests for generate_default_dict."""

    def _client(self):
        client = _client()
        client.get_arrays.return_value = _ok(
            [
                Array(os="Purity//FA"),
                Array(os="Purity"),
                Array(os="Purity//FB"),
                Array(os="Elasticity"),
            ]
        )
        client.get_volumes.return_value = _ok(total_item_count=5)
        client.get_volume_snapshots.return_value = _ok(total_item_count=6)
        client.get_file_systems.return_value = _ok(total_item_count=7)
        client.get_file_system_snapshots.return_value = _ok(total_item_count=8)
        client.get_buckets.return_value = _ok(total_item_count=9)
        client.get_directories.return_value = _ok(total_item_count=10)
        client.get_pods.return_value = _ok(total_item_count=11)
        client.get_object_store_accounts.return_value = _ok(total_item_count=12)
        return client

    def test_counts_appliances_by_os_and_totals(self):
        result = generate_default_dict(self._client())

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
    def test_error_answer_on_a_count_is_raised(self, method):
        client = self._client()
        _answer_with(client, method, _error(500, "Internal server error"))

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
        return Subscription(**fields)

    def test_maps_subscription_fields_and_dates(self):
        client = _client()
        client.get_subscriptions.return_value = _ok(
            [
                self._subscription(
                    organization=FixedReference(id="org-1", name="ACME"),
                    customer_name="ACME Storage",
                    partner_name="PartnerCo",
                    subscription_term=36,
                )
            ]
        )

        result = generate_subscriptions_dict(client)

        assert result["sub1"] == {
            "start_date": "2021-01-01 00:00:00 UTC",
            "expiration_date": "2021-01-01 00:00:00 UTC",
            "service": "Evergreen//One",
            "status": "active",
            "org_name": "ACME",
            "partner_name": "PartnerCo",
            "subscription_term": 36,
        }

    def test_optional_fields_default_to_none(self):
        client = _client()
        client.get_subscriptions.return_value = _ok([self._subscription()])

        result = generate_subscriptions_dict(client)

        assert result["sub1"]["org_name"] is None
        assert result["sub1"]["partner_name"] is None
        assert result["sub1"]["subscription_term"] is None

    def test_empty_organization_reference_gives_no_org_name(self):
        client = _client()
        client.get_subscriptions.return_value = _ok(
            [self._subscription(organization=FixedReference())]
        )

        result = generate_subscriptions_dict(client)

        assert result["sub1"]["org_name"] is None

    def test_empty_subscriptions_returns_empty_dict(self):
        client = _client()
        client.get_subscriptions.return_value = _ok()

        assert generate_subscriptions_dict(client) == {}

    def test_error_answer_is_raised(self):
        client = _client()
        client.get_subscriptions.return_value = _error(400, "Bad request")

        with pytest.raises(Pure1ResponseError):
            generate_subscriptions_dict(client)


# generate_subscription_assets_dict is not called by the module and its
# fixture is not shaped like the SDK model.
class TestGenerateSubscriptionAssetsDict:
    """Tests for generate_subscription_assets_dict."""

    def test_maps_asset_fields_and_activation_date(self):
        client = Mock()
        client.get_subscription_assets.return_value = Mock(
            items=[
                types.SimpleNamespace(
                    name="asset1",
                    install_location="DC1",
                    activation_date=JAN_2021_MS,
                    version="1.2.3",
                    model="FA-X70",
                    chassis_serial_number="CH-001",
                    effective_use=10,
                    utilization=0.5,
                    total_usable=1000,
                    total_reduction=3.5,
                    subscription=types.SimpleNamespace(name="sub1", id="sid-1"),
                    license=types.SimpleNamespace(name="lic1", id="lid-1"),
                )
            ]
        )

        result = generate_subscription_assets_dict(client)

        assert result["asset1"]["install_location"] == "DC1"
        assert result["asset1"]["activation_date"] == "2021-01-01 00:00:00 UTC"
        assert result["asset1"]["model"] == "FA-X70"
        assert result["asset1"]["subscription_name"] == "sub1"
        assert result["asset1"]["subscription_id"] == "sid-1"
        assert result["asset1"]["license_name"] == "lic1"

    def test_empty_assets_returns_empty_dict(self):
        client = Mock()
        client.get_subscription_assets.return_value = Mock(items=[])

        assert generate_subscription_assets_dict(client) == {}


class TestGenerateContractDict:
    """Tests for generate_contract_dict (contract_state logic)."""

    def _client_with_contract(self, start_ms, end_ms):
        client = _client()
        client.get_arrays.return_value = _ok([Array(name="foo")])
        client.get_arrays_support_contracts.return_value = _ok(
            [SupportContract(start_date=start_ms, end_date=end_ms)]
        )
        return client

    def test_active_contract(self):
        now = int(time.time() * 1000)
        client = self._client_with_contract(now - 1000, now + 86400000)

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Active"

    def test_grace_period_contract(self):
        now = int(time.time() * 1000)
        # ended 1 day ago, well within the 30-day grace window
        client = self._client_with_contract(now - 86400000 * 365, now - 86400000)

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Grace Period"

    def test_expired_contract(self):
        now = int(time.time() * 1000)
        # ended 60 days ago, past the 30-day grace window
        client = self._client_with_contract(now - 86400000 * 365, now - 86400000 * 60)

        result = generate_contract_dict(client)

        assert result["foo"]["contract_state"] == "Expired"

    def test_no_contract_data_is_expired(self):
        client = _client()
        client.get_arrays.return_value = _ok([Array(name="foo")])
        client.get_arrays_support_contracts.return_value = _ok()

        result = generate_contract_dict(client)

        assert result["foo"] == {"contract_state": "Expired"}

    def test_error_answer_for_an_array_is_raised(self):
        client = _client()
        client.get_arrays.return_value = _ok([Array(name="foo")])
        client.get_arrays_support_contracts.return_value = _error(404, "Not found")

        with pytest.raises(Pure1ResponseError):
            generate_contract_dict(client)


class TestGenerateEsgDict:
    """Tests for generate_esg_dict."""

    def _client(self):
        client = _client()
        client.get_assessment_sustainability_arrays.return_value = _ok(
            [
                SustainabilityArray(
                    name="fa1",
                    reporting_status="assessment_ready",
                    install_address=InstallAddress(
                        geolocation=Geolocation(latitude=37.4, longitude=-122.1),
                        street_address="1 Main Street",
                        updated=JAN_2021_MS,
                    ),
                    assessment=SustainabilityAssessment(
                        assessment_level="good",
                        power_average=512.5,
                        rack_units=3,
                        interval_start=JAN_2021_MS,
                        interval_end=JAN_2021_MS,
                    ),
                ),
                SustainabilityArray(name="fb1", reporting_status="not_enough_data"),
            ]
        )
        client.get_assessment_sustainability_insights_arrays.return_value = _ok(
            [
                SustainabilityInsightArray(
                    resource=FixedReferenceFqdn(name="fa1", fqdn="fa1.example.com"),
                    type="high_power_consumption",
                    severity="medium",
                    additional_data={"power": 512.5},
                )
            ]
        )
        return client

    def test_ready_appliance_with_an_insight(self):
        result = generate_esg_dict(_module(), self._client())

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

    def test_appliance_without_an_assessment(self):
        result = generate_esg_dict(_module(), self._client())

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
    def test_error_answer_is_raised(self, method):
        client = self._client()
        _answer_with(client, method, _error(500, "Server error"))

        with pytest.raises(Pure1ResponseError):
            generate_esg_dict(_module(), client)


def _metric(data, unit, metric_name):
    return CurrentMetric(data=data, unit=unit, metric=FixedReference(name=metric_name))


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
            marketplace_partner=MarketplacePartner(),
            service_tier="//Block - Performance",
            subscription=FixedReference(id="sid-1", name="sub1"),
            average_on_demand=_metric(1, "B", "avg"),
            reservation=_metric(2, "B", "res"),
            usage=_metric(3, "B", "use"),
            quarter_on_demand=_metric(4, "B", "qod"),
            resources=[
                LicenseResourceReference(
                    id="rid-1",
                    name="res1",
                    resource_type="arrays",
                    fqdn="array1.example.com",
                    activation_time=JAN_2021_MS,
                    usage=_metric(10, "B", "used"),
                ),
                LicenseResourceReference(
                    name="res2",
                    activation_time=JAN_2021_MS,
                    usage=CurrentMetric(unit="B", metric=FixedReference(name="used")),
                ),
            ],
        )
        fields.update(kwargs)
        return SubscriptionLicense(**fields)

    def _result(self, license):
        client = _client()
        client.get_subscription_licenses.return_value = _ok([license])
        return generate_subscription_licenses_dict(client)

    def test_maps_license_fields(self):
        result = self._result(self._license())

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

    def test_empty_marketplace_partner_gives_none(self):
        result = self._result(self._license())

        assert result["lic1"]["marketplace_partner"] is None

    def test_marketplace_partner_name(self):
        result = self._result(
            self._license(marketplace_partner=MarketplacePartner(name="AWS"))
        )

        assert result["lic1"]["marketplace_partner"] == "AWS"

    def test_location_comes_from_the_site_address(self):
        result = self._result(
            self._license(
                site_address=BaseAddress(
                    city="Mountain View", state="CA", country="UNITED STATES"
                )
            )
        )

        assert result["lic1"]["location"] == {
            "city": "Mountain View",
            "state": "CA",
            "country": "UNITED STATES",
        }

    def test_location_and_energy_usage_are_none_without_site_address(self):
        result = self._result(self._license())

        assert result["lic1"]["location"] is None
        assert result["lic1"]["energy_usage"] is None

    def test_empty_site_address_gives_no_location(self):
        result = self._result(self._license(site_address=BaseAddress()))

        assert result["lic1"]["location"] is None

    def test_pre_ratio_is_returned_as_plain_values(self):
        result = self._result(
            self._license(pre_ratio=SubscriptionLicensePreRatio(data=3.45, unit=""))
        )

        assert result["lic1"]["pre_ratio"] == {"data": 3.45, "unit": ""}

    def test_pre_ratio_is_none_when_not_set(self):
        result = self._result(self._license())

        assert result["lic1"]["pre_ratio"] is None

    def test_empty_pre_ratio_gives_none(self):
        result = self._result(self._license(pre_ratio=SubscriptionLicensePreRatio()))

        assert result["lic1"]["pre_ratio"] is None

    def test_resources_are_nested_under_the_license(self):
        result = self._result(self._license())

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

    def test_license_with_only_a_name(self):
        result = self._result(SubscriptionLicense(name="lic1"))

        assert set(result["lic1"]) == LICENSE_KEYS
        assert result["lic1"]["start_date"] is None
        assert result["lic1"]["service_tier"] is None
        assert result["lic1"]["subscription"] is None
        assert result["lic1"]["usage"] == {"data": None, "unit": None, "metric": None}
        assert result["lic1"]["resources"] == {}

    def test_result_holds_plain_values_only(self):
        result = self._result(
            self._license(
                pre_ratio=SubscriptionLicensePreRatio(data=3.45, unit=""),
                site_address=BaseAddress(city="Mountain View"),
            )
        )

        json.dumps(result)

    def test_empty_licenses_returns_empty_dict(self):
        client = _client()
        client.get_subscription_licenses.return_value = _ok()

        assert generate_subscription_licenses_dict(client) == {}

    def test_error_answer_is_raised(self):
        client = _client()
        client.get_subscription_licenses.return_value = _error(500, "Server error")

        with pytest.raises(Pure1ResponseError):
            generate_subscription_licenses_dict(client)


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
                InvoiceLineComponent(item="Shelf", quantity=1, description="Shelf"),
                InvoiceLineComponent(item="Cable"),
            ],
            unit_price=1000,
            amount=2000,
            tax=Tax(percentage=0.1, amount=200),
        )
        fields.update(kwargs)
        return InvoiceLine(**fields)

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
            subscription=FixedReference(id="sid-1", name="sub1"),
            lines=[self._line()],
        )
        fields.update(kwargs)
        return Invoice(**fields)

    def _result(self, invoice):
        client = _client()
        client.get_invoices.return_value = _ok([invoice])
        return generate_invoices_dict(_module(), client)

    def test_maps_invoice_header_fields(self):
        result = self._result(self._invoice())

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

    def test_date_is_read_from_an_older_sdk_model(self):
        # py-pure-client models generated with swagger name the field _date
        invoice = types.SimpleNamespace(id="INV-001", _date=JAN_2021_MS)

        result = self._result(invoice)

        assert result["INV-001"]["date"] == _local_date(JAN_2021_MS)

    def test_invoice_lines_are_appended(self):
        result = self._result(self._invoice())

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

    def test_result_holds_plain_values_only(self):
        json.dumps(self._result(self._invoice()))

    def test_invoice_with_only_an_id(self):
        result = self._result(Invoice(id="INV-001"))

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

    def test_line_without_tax_and_components(self):
        result = self._result(self._invoice(lines=[InvoiceLine(item="FA-X70")]))

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

    def test_unset_tax_fields_keep_their_defaults(self):
        result = self._result(
            self._invoice(
                lines=[self._line(tax=Tax(exemption_statement="Exempt"))],
            )
        )

        line = result["INV-001"]["lines"][0]
        assert line["tax_percentage"] == 0
        assert line["tax_amount"] == 0
        assert line["tax_exemption_statement"] == "Exempt"

    def test_no_invoices_returns_empty_dict(self):
        client = _client()
        client.get_invoices.return_value = _ok()

        assert generate_invoices_dict(_module(), client) == {}

    def test_non_200_response_returns_empty_dict(self):
        module = _module()
        client = _client()
        client.get_invoices.return_value = _error(500, "Internal server error")

        assert generate_invoices_dict(module, client) == {}
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "invoices" in warning
        assert "500" in warning
        assert "Internal server error" in warning
        module.fail_json.assert_not_called()

    def test_403_returns_empty_dict_with_a_warning(self):
        module = _module()
        client = _client()
        client.get_invoices.return_value = _error(403)

        assert generate_invoices_dict(module, client) == {}
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


def _series(metric, *values):
    """A metric answer with one series, one point per value"""
    data = [[JAN_2021_MS + index * 180000, value] for index, value in enumerate(values)]
    return _ok([MetricHistory(name=metric, data=data)])


def _appliances_client(arrays, tags=None, metrics=None):
    """A client for the appliances subset.

    tags maps an array name to its tags. metrics maps a metric name to the
    values of its series, or to a function that returns the answer.
    """
    tags = tags or {}
    metrics = metrics or {}

    def arrays_tags(resource_names):
        return _ok(tags.get(resource_names[0], []))

    def metrics_history(names, **kwargs):
        answer = metrics.get(names[0], [])
        if callable(answer):
            return answer()
        if answer:
            return _series(names[0], *answer)
        return _ok()

    client = _client()
    client.get_arrays.return_value = _ok(arrays)
    client.get_arrays_tags.side_effect = arrays_tags
    client.get_metrics_history.side_effect = metrics_history
    return client


def _array(name, os):
    return Array(
        name=name,
        os=os,
        version="6.5.0",
        model="FA-X70R4",
        fqdn=name + ".example.com",
    )


class TestGenerateAppliancesDict:
    """Tests for generate_appliances_dict."""

    def test_appliances_are_grouped_by_os(self):
        client = _appliances_client(
            [
                _array("fa1", "Purity//FA"),
                _array("fa2", "Purity"),
                _array("fb1", "Purity//FB"),
                _array("oe1", "Elasticity"),
            ]
        )

        result = generate_appliances_dict(_module(), client)

        assert sorted(result["FlashArray"]) == ["fa1", "fa2"]
        assert sorted(result["FlashBlade"]) == ["fb1"]
        assert sorted(result["ObjectEngine"]) == ["oe1"]
        assert result["FlashArray"]["fa1"] == {
            "os_version": "6.5.0",
            "model": "FA-X70R4",
            "fqdn": "fa1.example.com",
            "tags": [],
        }

    def test_array_without_fqdn(self):
        client = _appliances_client(
            [Array(name="fa1", os="Purity//FA", version="6.5.0", model="FA-X70R4")]
        )

        result = generate_appliances_dict(_module(), client)

        assert result["FlashArray"]["fa1"]["fqdn"] == ""

    @pytest.mark.parametrize(
        "array_os, section",
        [
            ("Purity//FA", "FlashArray"),
            ("Purity//FB", "FlashBlade"),
            ("Elasticity", "ObjectEngine"),
        ],
    )
    def test_tags_are_read_with_get_arrays_tags(self, array_os, section):
        client = _appliances_client(
            [_array("a1", array_os)],
            tags={
                "a1": [
                    Tag(key="site", value="lab", namespace="pure1"),
                    Tag(key="owner", value="ops", tag_organization_id=42),
                ]
            },
        )

        result = generate_appliances_dict(_module(), client)

        client.get_arrays_tags.assert_called_once_with(resource_names=["a1"])
        assert result[section]["a1"]["tags"] == [
            {"key": "site", "value": "lab", "org_id": None, "namespace": "pure1"},
            {"key": "owner", "value": "ops", "org_id": 42, "namespace": None},
        ]

    def test_error_answer_for_tags_leaves_them_empty(self):
        client = _appliances_client([_array("fa1", "Purity//FA")])
        client.get_arrays_tags.side_effect = None
        client.get_arrays_tags.return_value = _error(400, "Bad request")

        result = generate_appliances_dict(_module(), client)

        assert result["FlashArray"]["fa1"]["tags"] == []

    def test_flasharray_metrics(self):
        module = _module()
        client = _appliances_client(
            [_array("fa1", "Purity//FA")], metrics=FLASHARRAY_METRICS
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

    def test_flashblade_metrics(self):
        client = _appliances_client(
            [_array("fb1", "Purity//FB")], metrics=FLASHARRAY_METRICS
        )

        result = generate_appliances_dict(_module(), client)

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

    def test_metric_calls_ask_for_one_metric_of_one_array(self):
        client = _appliances_client([_array("fa1", "Purity//FA")])

        generate_appliances_dict(_module(), client)

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

    def test_object_engine_has_no_metric_calls(self):
        client = _appliances_client([_array("oe1", "Elasticity")])

        generate_appliances_dict(_module(), client)

        client.get_metrics_history.assert_not_called()

    def test_failed_metric_call_skips_the_key_with_a_warning(self):
        module = _module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_read_latency_us"] = lambda: _error(404, "No such metric")
        client = _appliances_client([_array("fa1", "Purity//FA")], metrics=metrics)

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

    def test_failed_metric_warning_carries_the_error_context(self):
        module = _module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_total_load"] = lambda: _error(
            404, "Not found", "No arrays with name: fa1"
        )
        client = _appliances_client([_array("fa1", "Purity//FA")], metrics=metrics)

        generate_appliances_dict(module, client)

        module.warn.assert_called_once_with(
            "Metric array_total_load not collected for fa1. Status code: 404. "
            "Error: Not found: No arrays with name: fa1"
        )

    @pytest.mark.parametrize(
        "answer",
        [
            _ok,
            lambda: _ok([MetricHistory(name="array_read_iops")]),
            lambda: _ok([MetricHistory(name="array_read_iops", data=[])]),
            lambda: _series("array_read_iops", 1000, None),
            lambda: _ok([MetricHistory(name="array_read_iops", data=[[JAN_2021_MS]])]),
            lambda: _ok([MetricHistory(name="array_read_iops", data=[None])]),
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
    def test_metric_without_a_value_skips_the_key(self, answer):
        module = _module()
        metrics = dict(FLASHARRAY_METRICS)
        metrics["array_read_iops"] = answer
        client = _appliances_client([_array("fa1", "Purity//FA")], metrics=metrics)

        result = generate_appliances_dict(module, client)

        array = result["FlashArray"]["fa1"]
        assert "iops (read)" not in array
        assert array["iops (write)"] == 2000
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "array_os, section, count",
        [("Purity//FA", "FlashArray", 7), ("Purity//FB", "FlashBlade", 6)],
    )
    def test_zero_metric_values_are_returned(self, array_os, section, count):
        metrics = {metric: [0] for metric in FLASHARRAY_METRICS}
        client = _appliances_client([_array("a1", array_os)], metrics=metrics)

        result = generate_appliances_dict(_module(), client)

        array = result[section]["a1"]
        metric_keys = set(array) - {"os_version", "model", "fqdn", "tags"}
        assert len(metric_keys) == count
        assert [array[key] for key in metric_keys] == [0] * count

    def test_unknown_os_gives_a_warning(self):
        module = _module()
        client = _appliances_client([_array("x1", "Other")])

        result = generate_appliances_dict(module, client)

        assert result == {"FlashArray": {}, "FlashBlade": {}, "ObjectEngine": {}}
        module.warn.assert_called_once_with("Unknown operating system detected: Other.")

    def test_error_answer_for_the_arrays_is_raised(self):
        client = _client()
        client.get_arrays.return_value = _error(500, "Server error")

        with pytest.raises(Pure1ResponseError):
            generate_appliances_dict(_module(), client)


class TestCollectSubset:
    """Tests for collect_subset, the rule for an error answer."""

    def test_returns_the_section(self):
        module = _module()

        result = collect_subset(module, "minimum", lambda value: {"a": value}, 1)

        assert result == {"a": 1}
        module.warn.assert_not_called()

    def test_403_returns_the_section_empty_with_a_warning(self):
        module = _module()
        client = _client()
        client.get_subscriptions.return_value = _error(403)

        result = collect_subset(
            module, "subscriptions", generate_subscriptions_dict, client
        )

        assert result == {}
        module.warn.assert_called_once()
        warning = module.warn.call_args[0][0]
        assert "subscriptions" in warning
        assert "403" in warning
        module.fail_json.assert_not_called()

    def test_other_error_fails_with_the_api_message(self):
        module = _module()
        client = _client()
        client.get_subscriptions.return_value = _error(500, "Internal server error")

        with pytest.raises(_FailJson) as failure:
            collect_subset(module, "subscriptions", generate_subscriptions_dict, client)

        message = str(failure.value)
        assert "subscriptions" in message
        assert "500" in message
        assert "Internal server error" in message
        module.warn.assert_not_called()

    def test_failure_carries_the_error_context(self):
        module = _module()
        client = _client()
        client.get_subscriptions.return_value = _error(
            400, "Invalid filter", "filter=name='sub1"
        )

        with pytest.raises(_FailJson) as failure:
            collect_subset(module, "subscriptions", generate_subscriptions_dict, client)

        assert str(failure.value) == (
            "Failed to collect the subscriptions subset. Status code: 400. "
            "Error: Invalid filter: filter=name='sub1"
        )

    @pytest.mark.parametrize(
        "answer",
        [types.SimpleNamespace(status_code=502, errors=[]), _error(502)],
        ids=["no errors", "error without a message"],
    )
    def test_error_answer_without_a_message_still_fails(self, answer):
        module = _module()
        client = _client()
        client.get_subscriptions.return_value = answer

        with pytest.raises(_FailJson) as failure:
            collect_subset(module, "subscriptions", generate_subscriptions_dict, client)

        assert str(failure.value) == (
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


class TestMain:
    """Tests for main."""

    def _client(self):
        client = _client()
        arrays = [_array("fa1", "Purity//FA"), _array("fb1", "Purity//FB")]
        client.get_arrays.side_effect = lambda: _ok(arrays)
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
            getattr(client, method).return_value = _ok(total_item_count=3)
        client.get_arrays_tags.side_effect = lambda resource_names: _ok(
            [Tag(key="site", value="lab", namespace="pure1")]
        )
        client.get_metrics_history.side_effect = lambda names, **kwargs: _series(
            names[0], 1048576
        )
        client.get_subscriptions.return_value = _ok(
            [
                Subscription(
                    name="sub1",
                    start_date=JAN_2021_MS,
                    expiration_date=JAN_2021_MS,
                    service="Evergreen//One",
                    status="active",
                    organization=FixedReference(name="ACME"),
                )
            ]
        )
        client.get_subscription_licenses.return_value = _ok(
            [
                SubscriptionLicense(
                    name="lic1",
                    start_date=JAN_2021_MS,
                    expiration_date=JAN_2021_MS,
                    last_updated_date=JAN_2021_MS,
                    marketplace_partner=MarketplacePartner(),
                    pre_ratio=SubscriptionLicensePreRatio(data=3.45, unit=""),
                    resources=[
                        LicenseResourceReference(
                            name="res1", activation_time=JAN_2021_MS
                        )
                    ],
                )
            ]
        )
        client.get_arrays_support_contracts.side_effect = lambda **kwargs: _ok()
        client.get_assessment_sustainability_arrays.return_value = _ok()
        client.get_assessment_sustainability_insights_arrays.return_value = _ok()
        client.get_invoices.return_value = _error(403)
        return client

    def _run(self, module, client):
        with patch.object(pure1_info, "AnsibleModule", return_value=module):
            with patch.object(pure1_info, "get_pure1", return_value=client):
                pure1_info.main()

    def test_all_completes_when_invoices_answer_403(self):
        module = _module(["all"])

        self._run(module, self._client())

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

    def test_other_error_fails_the_task(self):
        module = _module(["all"])
        client = self._client()
        client.get_subscription_licenses.return_value = _error(
            500, "Internal server error"
        )

        with pytest.raises(_FailJson) as failure:
            self._run(module, client)

        assert "Internal server error" in str(failure.value)
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("subset, method", [call[:2] for call in LISTING_CALLS])
    def test_error_answer_on_a_listing_call_fails_the_task(self, subset, method):
        module = _module([subset])
        client = self._client()
        _answer_with(client, method, _error(500, "boom"))

        with pytest.raises(_FailJson) as failure:
            self._run(module, client)

        assert str(failure.value) == (
            "Failed to collect the {0} subset. Status code: 500. "
            "Error: boom".format(subset)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("subset, method, section", LISTING_CALLS)
    def test_403_on_a_listing_call_returns_the_section_empty(
        self, subset, method, section
    ):
        module = _module([subset])
        client = self._client()
        _answer_with(client, method, _error(403))

        self._run(module, client)

        module.exit_json.assert_called_once()
        info = module.exit_json.call_args[1]["pure1_info"]
        assert info[section] == {}
        assert all(info[key] for key in info if key != section)
        module.warn.assert_called_once()
        assert subset in module.warn.call_args[0][0]
        assert "403" in module.warn.call_args[0][0]

    def test_invoices_error_returns_the_section_empty(self):
        module = _module(["invoices"])
        client = self._client()
        client.get_invoices.return_value = _error(500, "Internal server error")

        self._run(module, client)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_info={"invoices": {}}
        )
        module.warn.assert_called_once_with(
            "Failed to collect the invoices subset, the section is returned "
            "empty. Status code: 500. Error: Internal server error"
        )
