# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_recommendations module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

import jinja2
import pypureclient
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule
from jinja2.nativetypes import NativeEnvironment
from pypureclient import ErrorResponse, PureError

from plugins.module_utils.pure1 import pure1_argument_spec, pure1_time
from plugins.modules import pure1_recommendations
from plugins.modules.pure1_recommendations import generate_recommendations_dict
from tests.unit.conftest import (
    MODELS,
    ansible_module,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# Every time differs in month, day, hour, minute and second, and no two
# kinds of time are the same
CREATED = 1612357445000
AS_OF = 1795515330000
END_OF_LIFE = 1612427400000
EXPIRES = 1795518995000

HARDWARE = "flasharray_hardware_upgrade"
LICENSE = "license_upgrade"
RENEWAL = "support_contract_renewal"
OTHER = "other"
# The lists of the result, in their order
KINDS = (HARDWARE, LICENSE, RENEWAL, OTHER)
# The client method that fills each list, the model of its answer and what
# a warning calls its items
METHODS = {
    HARDWARE: "get_recommendations_flasharray_hardware_upgrade",
    LICENSE: "get_recommendations_license_upgrade",
    RENEWAL: "get_recommendations_support_contract_renewal",
    OTHER: "get_recommendations",
}
RESPONSES = {
    HARDWARE: "FlasharrayHardwareUpgradeRecommendationGetResponse",
    LICENSE: "LicenseUpgradeRecommendationGetResponse",
    RENEWAL: "SupportContractRenewalRecommendationGetResponse",
    OTHER: "RecommendationGetResponse",
}
WORDS = {
    HARDWARE: "FlashArray hardware upgrade recommendations",
    LICENSE: "license upgrade recommendations",
    RENEWAL: "support contract renewal recommendations",
    OTHER: "recommendations",
}
# The type option that makes the module ask for a list
TYPE_OPTION = {HARDWARE: HARDWARE, LICENSE: LICENSE, RENEWAL: RENEWAL, OTHER: "all"}


def _reference(name="array1", resource_type="arrays", **overrides):
    fields = dict(id=name + "-id", name=name, resource_type=resource_type)
    fields.update(overrides)
    return sdk_model("FixedReference", **fields)


def _base_fields(name, slug, target):
    """target is the name of an array, a reference, or None for none"""
    if isinstance(target, str):
        target = _reference(target)
    return dict(
        id=name + "-id",
        name=name,
        description="Text of " + name,
        recommendation_type=slug,
        created_at=CREATED,
        as_of=AS_OF,
        target=target,
        trigger=sdk_model("RecommendationTrigger", name="program1"),
        company=sdk_model("Company", name="company1"),
        subscription_license=_reference("license1", "subscription-licenses"),
    )


def _component(**overrides):
    fields = dict(component_type="CONTROLLER", protocol="NVME", end_of_life=END_OF_LIFE)
    fields.update(overrides)
    return sdk_model("EolComponent", **fields)


def _contract(array="array1", **overrides):
    fields = dict(
        target=_reference(array),
        target_model="FA-X70R2",
        expires=EXPIRES,
        subscription_evergreen_tier="Evergreen//Forever",
    )
    fields.update(overrides)
    return sdk_model("SupportContractDetails", **fields)


def _make_hardware(name="hardware1", target="array1", **overrides):
    fields = _base_fields(name, "flasharray-hardware-upgrade", target)
    fields.update(
        current_model="FA-X70R2",
        recommended_model="FA-X70R4",
        current_installed_capacity=109951162777600,
        recommended_installed_capacity=219902325555200,
        current_usable_capacity=76965813944320,
        recommended_usable_capacity=153931627888640,
        recommended_full_sku="sku-full",
        recommended_upgrade_sku="sku-upgrade",
        end_of_life_components=[_component()],
    )
    fields.update(overrides)
    return sdk_model("FlasharrayHardwareUpgradeRecommendation", **fields)


def _make_license(name="license-upgrade1", target="array1", **overrides):
    fields = _base_fields(name, "license-upgrade", target)
    fields.update(
        current_service_tier="//Block - Capacity",
        recommended_service_tier="//Block - Performance",
        current_reserved_amount=100.5,
        recommended_reserved_amount=150.25,
        reserved_unit="TiB",
        current_reserve_exceeded_in_days=45,
        current_iops_sla=2500.5,
        recommended_iops_sla=5000.5,
        current_bandwidth_sla_bytes_per_sec=104857600.5,
        recommended_bandwidth_sla_bytes_per_sec=209715200.5,
    )
    fields.update(overrides)
    return sdk_model("LicenseUpgradeRecommendation", **fields)


def _make_renewal(name="renewal1", target="array1", **overrides):
    fields = _base_fields(name, "support-contract-renewal", target)
    fields.update(
        partner_name="partner1",
        subscription_term_months=36,
        support_contracts=[_contract("array1"), _contract("array2")],
    )
    fields.update(overrides)
    return sdk_model("SupportContractRenewalRecommendation", **fields)


def _make_other(name="other1", target="array1", **overrides):
    """A recommendation of a type that Pure1 added after the module was written"""
    fields = _base_fields(name, "flashblade-hardware-upgrade", target)
    fields.update(overrides)
    return sdk_model("Recommendation", **fields)


MAKE = {
    HARDWARE: _make_hardware,
    LICENSE: _make_license,
    RENEWAL: _make_renewal,
    OTHER: _make_other,
}


def _as_listed(*items):
    """Return recommendations as Pure1 lists them among those of every type

    That listing has what all types have and none of the fields of a type.
    """
    base = MODELS.Recommendation.__fields__
    return [
        sdk_model(
            "Recommendation", **{name: getattr(item, name, None) for name in base}
        )
        for item in items
    ]


def _target_row(name="array1", resource_type="arrays", **overrides):
    row = {"id": name + "-id", "name": name, "resource_type": resource_type}
    row.update(overrides)
    return row


def _base_row(name, slug, target):
    """target is the name of an array, or the whole result of a target"""
    if isinstance(target, str):
        target = _target_row(target)
    return {
        "id": name + "-id",
        "name": name,
        "description": "Text of " + name,
        "recommendation_type": slug,
        "created": "2021-02-03 13:04:05 UTC",
        "created_epoch": 1612357445000,
        "as_of": "2026-11-24 10:15:30 UTC",
        "as_of_epoch": 1795515330000,
        "target": target,
        "trigger": "program1",
        "company": "company1",
        "subscription_license": {"id": "license1-id", "name": "license1"},
    }


def _hardware_row(name="hardware1", target="array1", **overrides):
    """The result of the recommendation that _make_hardware(name) builds"""
    row = _base_row(name, "flasharray-hardware-upgrade", target)
    row.update(
        {
            "current_model": "FA-X70R2",
            "recommended_model": "FA-X70R4",
            "current_installed_capacity": 109951162777600,
            "recommended_installed_capacity": 219902325555200,
            "current_usable_capacity": 76965813944320,
            "recommended_usable_capacity": 153931627888640,
            "recommended_full_sku": "sku-full",
            "recommended_upgrade_sku": "sku-upgrade",
            "end_of_life_components": [
                {
                    "component_type": "CONTROLLER",
                    "protocol": "NVME",
                    "end_of_life": "2021-02-04 08:30:00 UTC",
                    "end_of_life_epoch": 1612427400000,
                }
            ],
        }
    )
    row.update(overrides)
    return row


def _license_row(name="license-upgrade1", target="array1", **overrides):
    """The result of the recommendation that _make_license(name) builds"""
    row = _base_row(name, "license-upgrade", target)
    row.update(
        {
            "current_service_tier": "//Block - Capacity",
            "recommended_service_tier": "//Block - Performance",
            "current_reserved_amount": 100.5,
            "recommended_reserved_amount": 150.25,
            "reserved_unit": "TiB",
            "current_reserve_exceeded_in_days": 45,
            "current_iops_sla": 2500.5,
            "recommended_iops_sla": 5000.5,
            "current_bandwidth_sla_bytes_per_sec": 104857600.5,
            "recommended_bandwidth_sla_bytes_per_sec": 209715200.5,
        }
    )
    row.update(overrides)
    return row


def _contract_row(array="array1", **overrides):
    row = {
        "target": _target_row(array),
        "target_model": "FA-X70R2",
        "expires": "2026-11-24 11:16:35 UTC",
        "expires_epoch": 1795518995000,
        "subscription_evergreen_tier": "Evergreen//Forever",
    }
    row.update(overrides)
    return row


def _renewal_row(name="renewal1", target="array1", **overrides):
    """The result of the recommendation that _make_renewal(name) builds"""
    row = _base_row(name, "support-contract-renewal", target)
    row.update(
        {
            "partner_name": "partner1",
            "subscription_term_months": 36,
            "support_contracts": [_contract_row("array1"), _contract_row("array2")],
        }
    )
    row.update(overrides)
    return row


def _other_row(name="other1", target="array1", **overrides):
    """The result of the recommendation that _make_other(name) builds"""
    row = _base_row(name, "flashblade-hardware-upgrade", target)
    row.update(overrides)
    return row


ROW = {
    HARDWARE: _hardware_row,
    LICENSE: _license_row,
    RENEWAL: _renewal_row,
    OTHER: _other_row,
}

# What a recommendation without any field gives, whatever its type
NO_BASE = {
    "id": None,
    "name": None,
    "description": None,
    "recommendation_type": None,
    "created": None,
    "created_epoch": None,
    "as_of": None,
    "as_of_epoch": None,
    "target": {"id": None, "name": None, "resource_type": None},
    "trigger": None,
    "company": None,
    "subscription_license": None,
}
# and what each type adds to that
NO_FIELDS = {
    HARDWARE: {
        "current_model": None,
        "recommended_model": None,
        "current_installed_capacity": None,
        "recommended_installed_capacity": None,
        "current_usable_capacity": None,
        "recommended_usable_capacity": None,
        "recommended_full_sku": None,
        "recommended_upgrade_sku": None,
        "end_of_life_components": [],
    },
    LICENSE: {
        "current_service_tier": None,
        "recommended_service_tier": None,
        "current_reserved_amount": None,
        "recommended_reserved_amount": None,
        "reserved_unit": None,
        "current_reserve_exceeded_in_days": None,
        "current_iops_sla": None,
        "recommended_iops_sla": None,
        "current_bandwidth_sla_bytes_per_sec": None,
        "recommended_bandwidth_sla_bytes_per_sec": None,
    },
    RENEWAL: {
        "partner_name": None,
        "subscription_term_months": None,
        "support_contracts": [],
    },
    OTHER: {},
}
MODEL = {
    HARDWARE: "FlasharrayHardwareUpgradeRecommendation",
    LICENSE: "LicenseUpgradeRecommendation",
    RENEWAL: "SupportContractRenewalRecommendation",
    OTHER: "Recommendation",
}


def _empty():
    return {HARDWARE: [], LICENSE: [], RENEWAL: [], OTHER: []}


def _params(**params):
    """The options as the argument spec leaves them when the task sets none"""
    options = dict(type="all", array=None)
    options.update(params)
    return options


def _answer(pure1_client, hardware=(), licenses=(), renewals=(), every=()):
    """Give each of the four calls its answer; every is the listing of all types"""
    answers = {HARDWARE: hardware, LICENSE: licenses, RENEWAL: renewals, OTHER: every}
    for kind, items in answers.items():
        getattr(pure1_client, METHODS[kind]).return_value = valid_response(items)


def _answer_kind(pure1_client, kind, items):
    """Give one call these items and the three others nothing"""
    _answer(pure1_client)
    getattr(pure1_client, METHODS[kind]).return_value = valid_response(items)


def _arrays(*names):
    return [sdk_model("Array", id=name + "-id", name=name) for name in names]


def _known_arrays(pure1_client, *names):
    """Answer the request that resolves the arrays of a task, after the check
    of get_pure1"""
    pure1_client.get_arrays.side_effect = [
        valid_response(),
        valid_response(_arrays(*names)),
    ]


def _calls(pure1_client):
    """Return the lists that the module asked Pure1 for"""
    return [kind for kind in KINDS if getattr(pure1_client, METHODS[kind]).called]


class TestGenerateRecommendationsDict:
    """Tests for generate_recommendations_dict."""

    def test_every_type_is_returned_in_its_list(self, pure1_client):
        module = ansible_module(**_params())
        hardware = [_make_hardware(), _make_hardware("hardware2", "array2")]
        licenses = [_make_license()]
        renewals = [_make_renewal()]
        _answer(
            pure1_client,
            hardware,
            licenses,
            renewals,
            every=_as_listed(*hardware + licenses + renewals) + [_make_other()],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert result == {
            HARDWARE: [_hardware_row(), _hardware_row("hardware2", "array2")],
            LICENSE: [_license_row()],
            RENEWAL: [_renewal_row()],
            OTHER: [_other_row()],
        }
        assert list(result) == list(KINDS)
        module.warn.assert_not_called()

    def test_all_types_take_four_requests_without_any_keyword(self, pure1_client):
        _answer(pure1_client)

        generate_recommendations_dict(ansible_module(**_params()), pure1_client)

        for kind in KINDS:
            getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        pure1_client.get_arrays.assert_not_called()

    @pytest.mark.parametrize("kind", [HARDWARE, LICENSE, RENEWAL])
    def test_one_type_takes_one_request_and_fills_one_list(self, pure1_client, kind):
        """The other calls have no answer: reading one would fail the test"""
        module = ansible_module(**_params(type=kind))
        getattr(pure1_client, METHODS[kind]).return_value = valid_response(
            [MAKE[kind]()]
        )

        result = generate_recommendations_dict(module, pure1_client)

        expected = _empty()
        expected[kind] = [ROW[kind]()]
        assert result == expected
        assert list(result) == list(KINDS)
        getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        assert _calls(pure1_client) == [kind]
        pure1_client.get_arrays.assert_not_called()
        module.warn.assert_not_called()

    def test_listing_of_every_type_fills_only_the_list_of_the_other_types(
        self, pure1_client
    ):
        """What it has of a type with a list of its own is not returned twice"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            every=_as_listed(_make_hardware(), _make_license(), _make_renewal())
            + [
                _make_other("other1"),
                _make_other("other2", recommendation_type="license-upgrade-2"),
                _make_other("other3", recommendation_type=""),
                _make_other("other4", recommendation_type=None),
                _make_other("other5", recommendation_type="LICENSE-UPGRADE"),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert result == {
            HARDWARE: [],
            LICENSE: [],
            RENEWAL: [],
            OTHER: [
                _other_row("other1"),
                _other_row("other2", recommendation_type="license-upgrade-2"),
                _other_row("other3", recommendation_type=""),
                _other_row("other4", recommendation_type=None),
                _other_row("other5", recommendation_type="LICENSE-UPGRADE"),
            ],
        }
        module.warn.assert_not_called()

    def test_recommendation_is_filed_by_the_call_that_returned_it(self, pure1_client):
        """Not by the type it names: the SDK does not check what Pure1 sends"""
        odd = MODELS.LicenseUpgradeRecommendation.from_dict(
            {"id": "odd-id", "recommendation_type": "support-contract-renewal"}
        )
        _answer_kind(pure1_client, LICENSE, [odd])

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        expected = _empty()
        expected[LICENSE] = [
            dict(
                NO_BASE,
                id="odd-id",
                recommendation_type="support-contract-renewal",
                **NO_FIELDS[LICENSE]
            )
        ]
        assert result == expected

    @pytest.mark.parametrize("kind", KINDS)
    def test_recommendation_with_only_its_id(self, pure1_client, kind):
        module = ansible_module(**_params())
        _answer_kind(pure1_client, kind, [sdk_model(MODEL[kind], id="only-id")])

        result = generate_recommendations_dict(module, pure1_client)

        assert result[kind] == [dict(NO_BASE, id="only-id", **NO_FIELDS[kind])]
        module.warn.assert_not_called()

    @pytest.mark.parametrize("kind", KINDS)
    def test_recommendation_without_id_and_name_is_returned(self, pure1_client, kind):
        """A list needs no name to file a recommendation under"""
        module = ansible_module(**_params())
        _answer_kind(
            pure1_client,
            kind,
            [sdk_model(MODEL[kind]), sdk_model(MODEL[kind], description="Text")],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert result[kind] == [
            dict(NO_BASE, **NO_FIELDS[kind]),
            dict(NO_BASE, description="Text", **NO_FIELDS[kind]),
        ]
        module.warn.assert_not_called()

    def test_references_without_a_field_are_null_or_have_null_keys(self, pure1_client):
        """An absent reference arrives as an object with no field set"""
        empty = dict(
            target=sdk_model("FixedReference"),
            trigger=sdk_model("RecommendationTrigger"),
            company=sdk_model("Company"),
            subscription_license=sdk_model("FixedReference"),
        )
        _answer(
            pure1_client,
            hardware=[_make_hardware(**empty)],
            renewals=[
                _make_renewal(
                    support_contracts=[
                        sdk_model("SupportContractDetails"),
                        _contract(target=sdk_model("FixedReference")),
                    ],
                    **empty
                )
            ],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        nothing = {
            "target": {"id": None, "name": None, "resource_type": None},
            "trigger": None,
            "company": None,
            "subscription_license": None,
        }
        assert result[HARDWARE] == [_hardware_row(**nothing)]
        assert result[RENEWAL] == [
            _renewal_row(
                support_contracts=[
                    {
                        "target": {"id": None, "name": None, "resource_type": None},
                        "target_model": None,
                        "expires": None,
                        "expires_epoch": None,
                        "subscription_evergreen_tier": None,
                    },
                    _contract_row(
                        target={"id": None, "name": None, "resource_type": None}
                    ),
                ],
                **nothing
            )
        ]

    @pytest.mark.parametrize(
        "license_reference, expected",
        [
            pytest.param(
                dict(id="license1-id"), {"id": "license1-id", "name": None}, id="id"
            ),
            pytest.param(
                dict(name="license1"), {"id": None, "name": "license1"}, id="name"
            ),
            pytest.param(dict(id="", name=""), {"id": "", "name": ""}, id="empty"),
            pytest.param(
                dict(resource_type="subscription-licenses"), None, id="no id, no name"
            ),
        ],
    )
    def test_subscription_license_is_null_only_without_id_and_name(
        self, pure1_client, license_reference, expected
    ):
        _answer_kind(
            pure1_client,
            LICENSE,
            [
                _make_license(
                    subscription_license=sdk_model(
                        "FixedReference", **license_reference
                    )
                )
            ],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        assert result[LICENSE] == [_license_row(subscription_license=expected)]

    def test_values_that_are_false_are_values(self, pure1_client):
        # without a type: the model of a type takes only its own for one
        base = dict(
            id="",
            name="",
            description="",
            created_at=0,
            as_of=0,
            target=sdk_model("FixedReference", id="", name="", resource_type=""),
            trigger=sdk_model("RecommendationTrigger", name=""),
            company=sdk_model("Company", name=""),
            subscription_license=sdk_model("FixedReference", id="", name=""),
        )
        base_row = {
            "id": "",
            "name": "",
            "description": "",
            "recommendation_type": None,
            "created": "1970-01-01 00:00:00 UTC",
            "created_epoch": 0,
            "as_of": "1970-01-01 00:00:00 UTC",
            "as_of_epoch": 0,
            "target": {"id": "", "name": "", "resource_type": ""},
            "trigger": "",
            "company": "",
            "subscription_license": {"id": "", "name": ""},
        }
        hardware = sdk_model(
            "FlasharrayHardwareUpgradeRecommendation",
            current_model="",
            recommended_model="",
            current_installed_capacity=0,
            recommended_installed_capacity=0,
            current_usable_capacity=0,
            recommended_usable_capacity=0,
            recommended_full_sku="",
            recommended_upgrade_sku="",
            end_of_life_components=[
                _component(component_type="", protocol="", end_of_life=0)
            ],
            **base
        )
        license_upgrade = sdk_model(
            "LicenseUpgradeRecommendation",
            current_service_tier="",
            recommended_service_tier="",
            current_reserved_amount=0,
            recommended_reserved_amount=0.0,
            reserved_unit="",
            current_reserve_exceeded_in_days=0,
            current_iops_sla=0,
            recommended_iops_sla=0.0,
            current_bandwidth_sla_bytes_per_sec=0,
            recommended_bandwidth_sla_bytes_per_sec=0.0,
            **base
        )
        renewal = sdk_model(
            "SupportContractRenewalRecommendation",
            partner_name="",
            subscription_term_months=0,
            support_contracts=[
                _contract(
                    target=sdk_model(
                        "FixedReference", id="", name="", resource_type=""
                    ),
                    target_model="",
                    expires=0,
                    subscription_evergreen_tier="",
                )
            ],
            **base
        )
        _answer(
            pure1_client,
            [hardware],
            [license_upgrade],
            [renewal],
            every=[sdk_model("Recommendation", recommendation_type="", **base)],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        assert result == {
            HARDWARE: [
                dict(
                    base_row,
                    current_model="",
                    recommended_model="",
                    current_installed_capacity=0,
                    recommended_installed_capacity=0,
                    current_usable_capacity=0,
                    recommended_usable_capacity=0,
                    recommended_full_sku="",
                    recommended_upgrade_sku="",
                    end_of_life_components=[
                        {
                            "component_type": "",
                            "protocol": "",
                            "end_of_life": "1970-01-01 00:00:00 UTC",
                            "end_of_life_epoch": 0,
                        }
                    ],
                )
            ],
            LICENSE: [
                dict(
                    base_row,
                    current_service_tier="",
                    recommended_service_tier="",
                    current_reserved_amount=0,
                    recommended_reserved_amount=0.0,
                    reserved_unit="",
                    current_reserve_exceeded_in_days=0,
                    current_iops_sla=0,
                    recommended_iops_sla=0.0,
                    current_bandwidth_sla_bytes_per_sec=0,
                    recommended_bandwidth_sla_bytes_per_sec=0.0,
                )
            ],
            RENEWAL: [
                dict(
                    base_row,
                    partner_name="",
                    subscription_term_months=0,
                    support_contracts=[
                        {
                            "target": {"id": "", "name": "", "resource_type": ""},
                            "target_model": "",
                            "expires": "1970-01-01 00:00:00 UTC",
                            "expires_epoch": 0,
                            "subscription_evergreen_tier": "",
                        }
                    ],
                )
            ],
            OTHER: [dict(base_row, recommendation_type="")],
        }
        # 0 is not turned into 0.0 or the reverse: the value is as Pure1 sent it
        row = result[LICENSE][0]
        assert isinstance(row["current_reserved_amount"], int)
        assert isinstance(row["recommended_reserved_amount"], float)
        assert isinstance(row["current_iops_sla"], int)
        assert isinstance(row["recommended_bandwidth_sla_bytes_per_sec"], float)

    def test_whole_numbers_of_a_license_stay_whole_numbers(self, pure1_client):
        """Pure1 can send an amount or a commitment as an integer"""
        _answer_kind(
            pure1_client,
            LICENSE,
            [
                _make_license(
                    current_reserved_amount=100,
                    recommended_reserved_amount=150,
                    current_iops_sla=2500,
                    recommended_iops_sla=5000,
                    current_bandwidth_sla_bytes_per_sec=104857600,
                    recommended_bandwidth_sla_bytes_per_sec=209715200,
                )
            ],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        row = result[LICENSE][0]
        assert row == _license_row(
            current_reserved_amount=100,
            recommended_reserved_amount=150,
            current_iops_sla=2500,
            recommended_iops_sla=5000,
            current_bandwidth_sla_bytes_per_sec=104857600,
            recommended_bandwidth_sla_bytes_per_sec=209715200,
        )
        assert isinstance(row["current_reserved_amount"], int)
        assert isinstance(row["recommended_bandwidth_sla_bytes_per_sec"], int)

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_are_in_utc_whatever_the_time_zone_of_the_host(self, pure1_client):
        _answer(pure1_client, [_make_hardware()], [], [_make_renewal()])

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        hardware = result[HARDWARE][0]
        assert hardware["created"] == "2021-02-03 13:04:05 UTC"
        assert hardware["as_of"] == "2026-11-24 10:15:30 UTC"
        assert hardware["end_of_life_components"][0]["end_of_life"] == (
            "2021-02-04 08:30:00 UTC"
        )
        contract = result[RENEWAL][0]["support_contracts"][0]
        assert contract["expires"] == "2026-11-24 11:16:35 UTC"

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_date_of_a_time_at_midnight_is_the_date_in_utc(self, pure1_client):
        """An end of life and an expiry are dates, sent as midnight in UTC"""
        midnight = 1814313600000
        _answer(
            pure1_client,
            [_make_hardware(end_of_life_components=[_component(end_of_life=midnight)])],
            [],
            [_make_renewal(support_contracts=[_contract(expires=midnight)])],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        component = result[HARDWARE][0]["end_of_life_components"][0]
        contract = result[RENEWAL][0]["support_contracts"][0]
        assert component == {
            "component_type": "CONTROLLER",
            "protocol": "NVME",
            "end_of_life": "2027-06-30 00:00:00 UTC",
            "end_of_life_epoch": 1814313600000,
        }
        assert contract["expires"] == "2027-06-30 00:00:00 UTC"
        assert contract["expires_epoch"] == 1814313600000

    def test_lists_inside_a_recommendation_keep_the_order_of_pure1(self, pure1_client):
        components = [
            _component(component_type="SHELF", protocol="SAS", end_of_life=EXPIRES),
            _component(component_type="CONTROLLER", protocol=None),
            _component(component_type="DRIVE", end_of_life=None),
        ]
        contracts = [_contract("array3"), _contract("array1", expires=CREATED)]
        _answer(
            pure1_client,
            [_make_hardware(end_of_life_components=components)],
            [],
            [_make_renewal(support_contracts=contracts)],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        assert result[HARDWARE][0]["end_of_life_components"] == [
            {
                "component_type": "SHELF",
                "protocol": "SAS",
                "end_of_life": "2026-11-24 11:16:35 UTC",
                "end_of_life_epoch": 1795518995000,
            },
            {
                "component_type": "CONTROLLER",
                "protocol": None,
                "end_of_life": "2021-02-04 08:30:00 UTC",
                "end_of_life_epoch": 1612427400000,
            },
            {
                "component_type": "DRIVE",
                "protocol": "NVME",
                "end_of_life": None,
                "end_of_life_epoch": None,
            },
        ]
        assert result[RENEWAL][0]["support_contracts"] == [
            _contract_row("array3"),
            _contract_row(
                "array1", expires="2021-02-03 13:04:05 UTC", expires_epoch=1612357445000
            ),
        ]

    @pytest.mark.parametrize("kind", KINDS)
    def test_recommendation_that_pure1_lists_twice_is_returned_once(
        self, pure1_client, kind
    ):
        """The id tells the recommendations apart: without one, both stay"""
        module = ansible_module(**_params())
        make = MAKE[kind]
        _answer_kind(
            pure1_client,
            kind,
            [
                make("r1"),
                make("r2", created_at=CREATED - 1),
                make("r1"),
                make("r3", id=None, created_at=CREATED - 2),
                make("r3", id=None, created_at=CREATED - 2),
                make("r4", id="", created_at=CREATED - 3),
                make("r4", id="", created_at=CREATED - 3),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["name"] for row in result[kind]] == ["r1", "r2", "r3", "r3", "r4"]
        module.warn.assert_not_called()

    @pytest.mark.parametrize("kind", KINDS)
    def test_recommendations_of_one_id_that_differ_are_all_returned(
        self, pure1_client, kind
    ):
        """Only one that repeats an earlier one of its id in every key is left
        out, wherever Pure1 lists it"""
        module = ansible_module(**_params())
        make = MAKE[kind]
        _answer_kind(
            pure1_client,
            kind,
            [
                make("first", id="one-id", created_at=CREATED - 1),
                make("second", id="one-id"),
                make("first", id="one-id", created_at=CREATED - 1),
                make("second", "array2", id="one-id"),
                make("second", id="one-id"),
                make("second", id="one-id", as_of=0),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert [
            (row["id"], row["name"], row["target"]["name"], row["as_of_epoch"])
            for row in result[kind]
        ] == [
            ("one-id", "second", "array1", AS_OF),
            ("one-id", "second", "array2", AS_OF),
            ("one-id", "second", "array1", 0),
            ("one-id", "first", "array1", AS_OF),
        ]
        module.warn.assert_not_called()

    def test_recommendations_of_one_id_that_differ_in_what_their_type_adds(
        self, pure1_client
    ):
        """A key of the type, and what a list of the recommendation holds"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            [
                _make_hardware(),
                _make_hardware(recommended_upgrade_sku="sku-other"),
                _make_hardware(end_of_life_components=[_component(protocol="SAS")]),
                _make_hardware(end_of_life_components=None),
                _make_hardware(),
            ],
            [
                _make_license(),
                _make_license(current_reserve_exceeded_in_days=0),
                _make_license(current_reserve_exceeded_in_days=None),
                _make_license(current_reserve_exceeded_in_days=0),
            ],
            [
                _make_renewal(),
                _make_renewal(support_contracts=[_contract("array1")]),
                _make_renewal(
                    support_contracts=[_contract("array2"), _contract("array1")]
                ),
                _make_renewal(support_contracts=[_contract("array1")]),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert [
            (row["recommended_upgrade_sku"], row["end_of_life_components"])
            for row in result[HARDWARE]
        ] == [
            ("sku-upgrade", _hardware_row()["end_of_life_components"]),
            ("sku-other", _hardware_row()["end_of_life_components"]),
            (
                "sku-upgrade",
                [dict(_hardware_row()["end_of_life_components"][0], protocol="SAS")],
            ),
            ("sku-upgrade", []),
        ]
        assert [row["current_reserve_exceeded_in_days"] for row in result[LICENSE]] == [
            45,
            0,
            None,
        ]
        assert [row["support_contracts"] for row in result[RENEWAL]] == [
            [_contract_row("array1"), _contract_row("array2")],
            [_contract_row("array1")],
            [_contract_row("array2"), _contract_row("array1")],
        ]
        assert {row["id"] for row in result[HARDWARE]} == {"hardware1-id"}
        assert {row["id"] for row in result[LICENSE]} == {"license-upgrade1-id"}
        assert {row["id"] for row in result[RENEWAL]} == {"renewal1-id"}
        module.warn.assert_not_called()

    @pytest.mark.parametrize("kind", KINDS)
    def test_null_among_the_recommendations_is_passed_over(self, pure1_client, kind):
        """The SDK does not check what Pure1 sends: a null arrives as it is,
        and is no recommendation without any key"""
        module = ansible_module(**_params())
        make = MAKE[kind]
        _answer_kind(
            pure1_client,
            kind,
            [None, make("r1"), None, make("r2", created_at=CREATED - 1), None],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert result[kind] == [
            ROW[kind]("r1"),
            ROW[kind](
                "r2", created="2021-02-03 13:04:04 UTC", created_epoch=CREATED - 1
            ),
        ]
        module.warn.assert_not_called()

    def test_null_in_a_list_of_a_recommendation_is_passed_over(self, pure1_client):
        """The SDK does not check what Pure1 sends: a null arrives as it is"""
        module = ansible_module(**_params())
        hardware = _make_hardware()
        hardware.end_of_life_components.insert(0, None)
        renewal = _make_renewal()
        renewal.support_contracts.insert(1, None)
        _answer(pure1_client, [hardware], [], [renewal])

        result = generate_recommendations_dict(module, pure1_client)

        assert result[HARDWARE] == [_hardware_row()]
        assert result[RENEWAL] == [_renewal_row()]
        module.warn.assert_not_called()

    def test_empty_list_of_a_recommendation_stays_empty(self, pure1_client):
        hardware = _make_hardware()
        del hardware.end_of_life_components[:]
        renewal = _make_renewal()
        del renewal.support_contracts[:]
        _answer(pure1_client, [hardware], [], [renewal])

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        assert result[HARDWARE] == [_hardware_row(end_of_life_components=[])]
        assert result[RENEWAL] == [_renewal_row(support_contracts=[])]

    @pytest.mark.parametrize("kind", KINDS)
    def test_each_list_is_ordered_by_creation_the_newest_first(
        self, pure1_client, kind
    ):
        """Pure1 is asked for no order: the same time is ordered by id, then by
        name, and a recommendation without a time comes last. The time 0 is a
        time: its id would put that one after those without a time"""
        make = MAKE[kind]
        _answer_kind(
            pure1_client,
            kind,
            [
                make("no-time-b", created_at=None),
                make("old", created_at=CREATED - 1),
                make("same-b"),
                make("zero-time", created_at=0),
                make("no-time-a", created_at=None),
                make("new", created_at=CREATED + 1),
                make("same-a"),
                make("same-no-id-b", id=None),
                make("same-no-id-a", id=None),
            ],
        )

        result = generate_recommendations_dict(
            ansible_module(**_params()), pure1_client
        )

        assert [row["name"] for row in result[kind]] == [
            "new",
            "same-no-id-a",
            "same-no-id-b",
            "same-a",
            "same-b",
            "old",
            "zero-time",
            "no-time-a",
            "no-time-b",
        ]
        assert [row["created_epoch"] for row in result[kind]] == [
            CREATED + 1,
            CREATED,
            CREATED,
            CREATED,
            CREATED,
            CREATED - 1,
            0,
            None,
            None,
        ]

    def test_no_array_named_returns_also_what_is_for_no_array(self, pure1_client):
        module = ansible_module(**_params(array=[]))
        _answer(
            pure1_client,
            [_make_hardware(target=None)],
            [_make_license(target=_reference("license1", "subscription-licenses"))],
            [_make_renewal(target=None, support_contracts=None)],
            every=[_make_other(target=None)],
        )

        result = generate_recommendations_dict(module, pure1_client)

        no_target = {"id": None, "name": None, "resource_type": None}
        assert result == {
            HARDWARE: [_hardware_row(target=no_target)],
            LICENSE: [
                _license_row(target=_target_row("license1", "subscription-licenses"))
            ],
            RENEWAL: [_renewal_row(target=no_target, support_contracts=[])],
            OTHER: [_other_row(target=no_target)],
        }
        # an empty list names no array, and costs no request
        pure1_client.get_arrays.assert_not_called()

    def test_arrays_are_resolved_first_and_every_list_is_narrowed(self, pure1_client):
        module = ansible_module(**_params(array=["ARRAY1", "array3"]))
        pure1_client.get_arrays.return_value = valid_response(
            _arrays("array1", "array3")
        )
        hardware = [
            _make_hardware("hardware1", "array1"),
            _make_hardware("hardware2", "array2"),
            _make_hardware("hardware3", "array3"),
        ]
        licenses = [
            _make_license("license-upgrade1", "array2"),
            _make_license("license-upgrade2", "array3"),
        ]
        renewals = [
            _make_renewal(
                "renewal1", "array1", support_contracts=[_contract("array1")]
            ),
            _make_renewal(
                "renewal2", "array2", support_contracts=[_contract("array2")]
            ),
        ]
        others = [_make_other("other1", "array2"), _make_other("other2", "array1")]
        _answer(
            pure1_client,
            hardware,
            licenses,
            renewals,
            every=_as_listed(*hardware + licenses + renewals) + others,
        )

        result = generate_recommendations_dict(module, pure1_client)

        pure1_client.get_arrays.assert_called_once_with(
            filter="name=('ARRAY1','array3')"
        )
        # Pure1 is asked for every recommendation: the module narrows them
        for kind in KINDS:
            getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        assert result == {
            HARDWARE: [
                _hardware_row("hardware1", "array1"),
                _hardware_row("hardware3", "array3"),
            ],
            LICENSE: [_license_row("license-upgrade2", "array3")],
            RENEWAL: [
                _renewal_row(
                    "renewal1", "array1", support_contracts=[_contract_row("array1")]
                )
            ],
            OTHER: [_other_row("other2", "array1")],
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "target, kept",
        [
            pytest.param(lambda: _reference("array1"), True, id="id and name"),
            pytest.param(
                lambda: _reference("old-name", id="array1-id"),
                True,
                id="id, under another name",
            ),
            pytest.param(
                lambda: sdk_model("FixedReference", id="array1-id"), True, id="id only"
            ),
            pytest.param(
                lambda: sdk_model("FixedReference", name="array1"), True, id="name only"
            ),
            pytest.param(
                lambda: _reference("array1", id="another-id"),
                True,
                id="name, with an id that Pure1 has not for the array",
            ),
            pytest.param(
                lambda: _reference("array1", resource_type=None),
                True,
                id="no resource type",
            ),
            pytest.param(
                lambda: _reference("Array1"), False, id="name in another case"
            ),
            pytest.param(lambda: _reference("array2"), False, id="another array"),
            pytest.param(lambda: _reference("array10"), False, id="longer name"),
            pytest.param(
                lambda: _reference("license1", "subscription-licenses"),
                False,
                id="license",
            ),
            pytest.param(
                lambda: sdk_model("FixedReference", resource_type="arrays"),
                False,
                id="no id, no name",
            ),
            pytest.param(lambda: sdk_model("FixedReference"), False, id="empty"),
            pytest.param(lambda: None, False, id="no target"),
            pytest.param(
                lambda: sdk_model(
                    "FixedReference",
                    id="license-9",
                    name="array1",
                    resource_type="subscription-licenses",
                ),
                False,
                id="license that carries the name of the array",
            ),
            pytest.param(
                lambda: sdk_model(
                    "FixedReference",
                    name="array1",
                    resource_type="subscription-licenses",
                ),
                False,
                id="name only, of a license",
            ),
            pytest.param(
                lambda: sdk_model(
                    "FixedReference", name="array1", resource_type="subscriptions"
                ),
                False,
                id="name only, of a subscription",
            ),
            pytest.param(
                lambda: sdk_model(
                    "FixedReference", name="array1", resource_type="arrays"
                ),
                True,
                id="name only, of an array",
            ),
            pytest.param(
                lambda: sdk_model("FixedReference", name="array1", resource_type=""),
                True,
                id="name only, of an empty kind",
            ),
            pytest.param(
                lambda: _reference("old-name", "subscription-licenses", id="array1-id"),
                True,
                id="id, whatever the kind",
            ),
        ],
    )
    @pytest.mark.parametrize("kind", KINDS)
    def test_target_decides_whether_a_recommendation_is_of_a_named_array(
        self, pure1_client, kind, target, kept
    ):
        """By the id of the array, or by its name as Pure1 spells it unless
        Pure1 gives the target as another kind of object than an array"""
        module = ansible_module(**_params(array=["array1"]))
        pure1_client.get_arrays.return_value = valid_response(_arrays("array1"))
        fields = dict(target=target())
        if kind == RENEWAL:
            fields["support_contracts"] = [_contract("array2")]
        _answer_kind(pure1_client, kind, [MAKE[kind]("r1", **fields)])

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["id"] for row in result[kind]] == (["r1-id"] if kept else [])
        if kept:
            # as Pure1 sent it: the selection changes nothing in it
            assert result[kind][0]["target"] == {
                name: getattr(fields["target"], name, None)
                for name in ("id", "name", "resource_type")
            }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "contracts, kept",
        [
            pytest.param(
                lambda: [_contract("array2"), _contract("array1")],
                True,
                id="second contract",
            ),
            pytest.param(
                lambda: [_contract(target=_reference("old-name", id="array1-id"))],
                True,
                id="id of a contract",
            ),
            pytest.param(
                lambda: [_contract(target=sdk_model("FixedReference", name="array1"))],
                True,
                id="name of a contract",
            ),
            pytest.param(
                lambda: [_contract("array2"), _contract("Array1")],
                False,
                id="other arrays",
            ),
            pytest.param(
                lambda: [sdk_model("SupportContractDetails", target_model="FA-X70R2")],
                False,
                id="contract without target",
            ),
            pytest.param(lambda: None, False, id="no contracts"),
            pytest.param(
                lambda: [
                    _contract(
                        target=sdk_model(
                            "FixedReference",
                            id="license-9",
                            name="array1",
                            resource_type="subscription-licenses",
                        )
                    )
                ],
                False,
                id="name of a contract for another kind of object",
            ),
        ],
    )
    def test_renewal_is_of_a_named_array_through_one_of_its_contracts(
        self, pure1_client, contracts, kept
    ):
        """And is then returned whole, with the contracts of other arrays"""
        module = ansible_module(**_params(type=RENEWAL, array=["array1"]))
        pure1_client.get_arrays.return_value = valid_response(_arrays("array1"))
        renewal = _make_renewal("r1", "array9", support_contracts=contracts())
        _answer_kind(pure1_client, RENEWAL, [renewal])

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["id"] for row in result[RENEWAL]] == (["r1-id"] if kept else [])
        if kept:
            assert len(result[RENEWAL][0]["support_contracts"]) == len(contracts())
            assert result[RENEWAL][0]["target"] == _target_row("array9")

    def test_null_beside_the_contract_of_a_named_array_is_passed_over(
        self, pure1_client
    ):
        module = ansible_module(**_params(type=RENEWAL, array=["array1"]))
        pure1_client.get_arrays.return_value = valid_response(_arrays("array1"))
        renewal = _make_renewal("r1", "array9", support_contracts=[_contract("array1")])
        renewal.support_contracts.insert(0, None)
        _answer_kind(pure1_client, RENEWAL, [renewal])

        result = generate_recommendations_dict(module, pure1_client)

        assert result[RENEWAL] == [
            _renewal_row("r1", "array9", support_contracts=[_contract_row("array1")])
        ]

    def test_array_that_pure1_lists_without_id_is_matched_by_its_name(
        self, pure1_client
    ):
        module = ansible_module(**_params(type=HARDWARE, array=["array1"]))
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="array1")]
        )
        _answer_kind(
            pure1_client,
            HARDWARE,
            [
                _make_hardware("hardware1", "array1"),
                # no id on either side is no match
                _make_hardware("hardware2", sdk_model("FixedReference", name="array2")),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert result[HARDWARE] == [_hardware_row("hardware1", "array1")]

    def test_named_arrays_keep_the_order_by_creation(self, pure1_client):
        module = ansible_module(**_params(type=LICENSE, array=["array2", "array1"]))
        pure1_client.get_arrays.return_value = valid_response(
            _arrays("array2", "array1")
        )
        _answer_kind(
            pure1_client,
            LICENSE,
            [
                _make_license("first", "array1", created_at=CREATED - 2),
                _make_license("second", "array2", created_at=CREATED),
                _make_license("third", "array1", created_at=CREATED - 1),
                _make_license("fourth", "array3", created_at=CREATED + 1),
            ],
        )

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["name"] for row in result[LICENSE]] == ["second", "third", "first"]

    @pytest.mark.parametrize(
        "names, known, message",
        [
            pytest.param(["foo"], [], "Array foo is not known to Pure1.", id="unknown"),
            pytest.param(
                ["array1", "foo", "Bar"],
                ["array1"],
                "Arrays foo, Bar are not known to Pure1.",
                id="two unknown beside a known one",
            ),
        ],
    )
    def test_unknown_array_fails_before_any_listing(
        self, pure1_client, names, known, message
    ):
        module = ansible_module(**_params(array=names))
        pure1_client.get_arrays.return_value = valid_response(_arrays(*known))

        with pytest.raises(SystemExit):
            generate_recommendations_dict(module, pure1_client)

        module.fail_json.assert_called_once_with(msg=message)
        assert _calls(pure1_client) == []

    def test_empty_array_name_fails_before_any_request(self, pure1_client):
        module = ansible_module(**_params(array=["array1", " "]))

        with pytest.raises(SystemExit):
            generate_recommendations_dict(module, pure1_client)

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_not_called()
        assert _calls(pure1_client) == []

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_page_of_a_listing_is_read(self, pure1_client, kind):
        """Pure1 gives a token while more remain, which the SDK follows"""
        module = ansible_module(**_params(type=TYPE_OPTION[kind]))
        items = [
            MAKE[kind]("r{0}".format(number), created_at=CREATED - number)
            for number in range(5)
        ]
        answers = paged_answers(RESPONSES[kind], items, page_size=2)
        _answer(pure1_client)
        getattr(pure1_client, METHODS[kind]).side_effect = answers

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["id"] for row in result[kind]] == [
            "r0-id",
            "r1-id",
            "r2-id",
            "r3-id",
            "r4-id",
        ]
        # the module asks once, the SDK for the later pages
        getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        assert answers.requests == [
            {},
            {"continuation_token": "page-2"},
            {"continuation_token": "page-4"},
        ]
        module.warn.assert_not_called()

    @pytest.mark.parametrize("kind", KINDS)
    def test_listing_shorter_than_pure1_counted_gives_a_warning(
        self, pure1_client, kind
    ):
        """Pure1 gave no token for the rest, and the calls take no offset: no
        second request is made"""
        module = ansible_module(**_params(type=TYPE_OPTION[kind]))
        items = [MAKE[kind]("r0"), MAKE[kind]("r1")]
        answers = paged_answers(RESPONSES[kind], items, page_size=2, total_item_count=5)
        _answer(pure1_client)
        getattr(pure1_client, METHODS[kind]).side_effect = answers

        result = generate_recommendations_dict(module, pure1_client)

        assert [row["id"] for row in result[kind]] == ["r0-id", "r1-id"]
        assert answers.requests == [{}]
        module.warn.assert_called_once_with(
            "Pure1 returned 2 of 5 {0}: the result may be "
            "incomplete.".format(WORDS[kind])
        )
        module.fail_json.assert_not_called()

    def test_every_short_listing_gives_its_own_warning(self, pure1_client):
        module = ansible_module(**_params())
        for kind in KINDS:
            getattr(pure1_client, METHODS[kind]).side_effect = paged_answers(
                RESPONSES[kind], [MAKE[kind]()], total_item_count=3
            )

        result = generate_recommendations_dict(module, pure1_client)

        assert [len(result[kind]) for kind in KINDS] == [1, 1, 1, 1]
        assert [call[0] for call in module.warn.call_args_list] == [
            (
                "Pure1 returned 1 of 3 FlashArray hardware upgrade "
                "recommendations: the result may be incomplete.",
            ),
            (
                "Pure1 returned 1 of 3 license upgrade recommendations: the "
                "result may be incomplete.",
            ),
            (
                "Pure1 returned 1 of 3 support contract renewal "
                "recommendations: the result may be incomplete.",
            ),
            ("Pure1 returned 1 of 3 recommendations: the result may be incomplete.",),
        ]

    def test_listing_without_a_total_that_may_be_short_gives_a_warning(
        self, pure1_client
    ):
        """Without a total and without an offset nothing tells whether more exist"""
        module = ansible_module(**_params(type=LICENSE))
        answers = paged_answers(
            RESPONSES[LICENSE], [_make_license()], total_item_count=None
        )
        pure1_client.get_recommendations_license_upgrade.side_effect = answers

        result = generate_recommendations_dict(module, pure1_client)

        assert result[LICENSE] == [_license_row()]
        assert answers.requests == [{}]
        module.warn.assert_called_once_with(
            "Pure1 returned 1 of an unknown number of license upgrade "
            "recommendations: the result may be incomplete."
        )

    def test_empty_listing_without_a_total_gives_no_warning(self, pure1_client):
        module = ansible_module(**_params(type=LICENSE))
        answers = paged_answers(RESPONSES[LICENSE], [], total_item_count=None)
        pure1_client.get_recommendations_license_upgrade.side_effect = answers

        result = generate_recommendations_dict(module, pure1_client)

        assert result == _empty()
        assert answers.requests == [{}]
        module.warn.assert_not_called()


class TestPure1RecommendationsMain:
    """Tests for the pure1_recommendations main() flow."""

    def test_recommendations_are_returned(self, pure1_client):
        hardware = [_make_hardware()]
        licenses = [
            _make_license(target=_reference("license1", "subscription-licenses"))
        ]
        renewals = [_make_renewal(subscription_license=None, partner_name=None)]
        _answer(
            pure1_client,
            hardware,
            licenses,
            renewals,
            every=_as_listed(*hardware + licenses + renewals)
            + [_make_other(subscription_license=None)],
        )

        # every option is left to the argument spec
        module = run_main(pure1_recommendations)

        # the check of get_pure1, and no request of the module for arrays
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        for kind in KINDS:
            getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_recommendations={
                "flasharray_hardware_upgrade": [
                    {
                        "id": "hardware1-id",
                        "name": "hardware1",
                        "description": "Text of hardware1",
                        "recommendation_type": "flasharray-hardware-upgrade",
                        "created": "2021-02-03 13:04:05 UTC",
                        "created_epoch": 1612357445000,
                        "as_of": "2026-11-24 10:15:30 UTC",
                        "as_of_epoch": 1795515330000,
                        "target": {
                            "id": "array1-id",
                            "name": "array1",
                            "resource_type": "arrays",
                        },
                        "trigger": "program1",
                        "company": "company1",
                        "subscription_license": {
                            "id": "license1-id",
                            "name": "license1",
                        },
                        "current_model": "FA-X70R2",
                        "recommended_model": "FA-X70R4",
                        "current_installed_capacity": 109951162777600,
                        "recommended_installed_capacity": 219902325555200,
                        "current_usable_capacity": 76965813944320,
                        "recommended_usable_capacity": 153931627888640,
                        "recommended_full_sku": "sku-full",
                        "recommended_upgrade_sku": "sku-upgrade",
                        "end_of_life_components": [
                            {
                                "component_type": "CONTROLLER",
                                "protocol": "NVME",
                                "end_of_life": "2021-02-04 08:30:00 UTC",
                                "end_of_life_epoch": 1612427400000,
                            }
                        ],
                    }
                ],
                "license_upgrade": [
                    {
                        "id": "license-upgrade1-id",
                        "name": "license-upgrade1",
                        "description": "Text of license-upgrade1",
                        "recommendation_type": "license-upgrade",
                        "created": "2021-02-03 13:04:05 UTC",
                        "created_epoch": 1612357445000,
                        "as_of": "2026-11-24 10:15:30 UTC",
                        "as_of_epoch": 1795515330000,
                        "target": {
                            "id": "license1-id",
                            "name": "license1",
                            "resource_type": "subscription-licenses",
                        },
                        "trigger": "program1",
                        "company": "company1",
                        "subscription_license": {
                            "id": "license1-id",
                            "name": "license1",
                        },
                        "current_service_tier": "//Block - Capacity",
                        "recommended_service_tier": "//Block - Performance",
                        "current_reserved_amount": 100.5,
                        "recommended_reserved_amount": 150.25,
                        "reserved_unit": "TiB",
                        "current_reserve_exceeded_in_days": 45,
                        "current_iops_sla": 2500.5,
                        "recommended_iops_sla": 5000.5,
                        "current_bandwidth_sla_bytes_per_sec": 104857600.5,
                        "recommended_bandwidth_sla_bytes_per_sec": 209715200.5,
                    }
                ],
                "support_contract_renewal": [
                    {
                        "id": "renewal1-id",
                        "name": "renewal1",
                        "description": "Text of renewal1",
                        "recommendation_type": "support-contract-renewal",
                        "created": "2021-02-03 13:04:05 UTC",
                        "created_epoch": 1612357445000,
                        "as_of": "2026-11-24 10:15:30 UTC",
                        "as_of_epoch": 1795515330000,
                        "target": {
                            "id": "array1-id",
                            "name": "array1",
                            "resource_type": "arrays",
                        },
                        "trigger": "program1",
                        "company": "company1",
                        "subscription_license": None,
                        "partner_name": None,
                        "subscription_term_months": 36,
                        "support_contracts": [
                            {
                                "target": {
                                    "id": "array1-id",
                                    "name": "array1",
                                    "resource_type": "arrays",
                                },
                                "target_model": "FA-X70R2",
                                "expires": "2026-11-24 11:16:35 UTC",
                                "expires_epoch": 1795518995000,
                                "subscription_evergreen_tier": "Evergreen//Forever",
                            },
                            {
                                "target": {
                                    "id": "array2-id",
                                    "name": "array2",
                                    "resource_type": "arrays",
                                },
                                "target_model": "FA-X70R2",
                                "expires": "2026-11-24 11:16:35 UTC",
                                "expires_epoch": 1795518995000,
                                "subscription_evergreen_tier": "Evergreen//Forever",
                            },
                        ],
                    }
                ],
                "other": [
                    {
                        "id": "other1-id",
                        "name": "other1",
                        "description": "Text of other1",
                        "recommendation_type": "flashblade-hardware-upgrade",
                        "created": "2021-02-03 13:04:05 UTC",
                        "created_epoch": 1612357445000,
                        "as_of": "2026-11-24 10:15:30 UTC",
                        "as_of_epoch": 1795515330000,
                        "target": {
                            "id": "array1-id",
                            "name": "array1",
                            "resource_type": "arrays",
                        },
                        "trigger": "program1",
                        "company": "company1",
                        "subscription_license": None,
                    }
                ],
            },
        )

    def test_check_mode_does_the_same(self, pure1_client):
        _answer(
            pure1_client,
            [_make_hardware()],
            [_make_license()],
            [_make_renewal()],
            every=[_make_other()],
        )

        module = run_main(pure1_recommendations, check_mode=True)

        for kind in KINDS:
            getattr(pure1_client, METHODS[kind]).assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_recommendations={
                HARDWARE: [_hardware_row()],
                LICENSE: [_license_row()],
                RENEWAL: [_renewal_row()],
                OTHER: [_other_row()],
            },
        )

    def test_every_option_goes_through_the_argument_spec(self, pure1_client):
        """A list given as one string is converted"""
        _known_arrays(pure1_client, "array1", "array2")
        _answer_kind(
            pure1_client,
            RENEWAL,
            [
                _make_renewal("renewal1", "array3"),
                _make_renewal("renewal2", "array3", support_contracts=None),
            ],
        )

        module = run_main(
            pure1_recommendations,
            type="support_contract_renewal",
            array="ARRAY1,array2",
        )

        assert [call[1] for call in pure1_client.get_arrays.call_args_list] == [
            {"limit": 0},
            {"filter": "name=('ARRAY1','array2')"},
        ]
        pure1_client.get_recommendations_support_contract_renewal.assert_called_once_with()
        assert _calls(pure1_client) == [RENEWAL]
        expected = _empty()
        expected[RENEWAL] = [_renewal_row("renewal1", "array3")]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_recommendations=expected
        )

    @pytest.mark.parametrize(
        "params, asked, requests",
        [
            pytest.param({}, list(KINDS), 4, id="default"),
            pytest.param({"type": "all"}, list(KINDS), 4, id="all"),
            pytest.param({"type": HARDWARE}, [HARDWARE], 1, id="hardware upgrades"),
            pytest.param({"type": LICENSE}, [LICENSE], 1, id="license upgrades"),
            pytest.param({"type": RENEWAL}, [RENEWAL], 1, id="renewals"),
            pytest.param({"array": ["array1"]}, list(KINDS), 5, id="default and array"),
            pytest.param(
                {"type": LICENSE, "array": ["array1", "array2"]},
                [LICENSE],
                2,
                id="one type and two arrays",
            ),
            pytest.param({"array": []}, list(KINDS), 4, id="empty list of arrays"),
        ],
    )
    def test_number_of_requests_of_a_task(self, pure1_client, params, asked, requests):
        """Beside the one request by which get_pure1 checks every new client"""
        _known_arrays(pure1_client, *params.get("array", []))
        _answer(pure1_client)

        module = run_main(pure1_recommendations, **params)

        assert _calls(pure1_client) == asked
        made = sum(
            getattr(pure1_client, METHODS[kind]).call_count for kind in KINDS
        ) + (pure1_client.get_arrays.call_count - 1)
        assert made == requests
        module.exit_json.assert_called_once_with(
            changed=False, pure1_recommendations=_empty()
        )

    def test_the_options_are_those_of_the_documentation(self, pure1_client):
        _answer(pure1_client)

        module = run_main(pure1_recommendations)

        options = yaml.safe_load(pure1_recommendations.DOCUMENTATION)["options"]
        assert set(module.params) - set(pure1_argument_spec()) == set(options)
        assert {name: module.params[name] for name in options} == {
            "type": "all",
            "array": None,
        }
        assert options["type"]["default"] == "all"
        assert options["type"]["choices"] == [
            "all",
            "flasharray_hardware_upgrade",
            "license_upgrade",
            "support_contract_renewal",
        ]

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param(
                {"type": "hardware"},
                r"value of type must be one of: all, flasharray_hardware_upgrade, "
                r"license_upgrade, support_contract_renewal, got: hardware$",
                id="type that is no choice",
            ),
            pytest.param(
                {"type": "license-upgrade"},
                r"value of type must be one of: all, flasharray_hardware_upgrade, "
                r"license_upgrade, support_contract_renewal, got: license-upgrade$",
                id="type as Pure1 names it",
            ),
            pytest.param(
                {"type": "other"},
                r"value of type must be one of: all, flasharray_hardware_upgrade, "
                r"license_upgrade, support_contract_renewal, got: other$",
                id="other is no type to ask for",
            ),
            pytest.param(
                {"array": {"name": "array1"}},
                # ansible-core names the type as <class 'dict'> up to 2.18 and
                # as dict from 2.19 on
                r"argument 'array' is of type (<class 'dict'>|dict) and we were unable to "
                r"convert to list: ",
                id="array is a dictionary",
            ),
            pytest.param(
                {"filter": "contains(target.name,'array1')"},
                r"Unsupported parameters for module: filter\.",
                id="no filter option",
            ),
            pytest.param(
                {"sort": "created_at-"},
                r"Unsupported parameters for module: sort\.",
                id="no sort option",
            ),
            pytest.param(
                {"limit": 5},
                r"Unsupported parameters for module: limit\.",
                id="no limit",
            ),
            pytest.param(
                {"name": ["recommendation1"]},
                r"Unsupported parameters for module: name\.",
                id="no name option",
            ),
        ],
    )
    def test_refused_option_fails_before_any_request(
        self, pure1_client, params, message
    ):
        module = run_main(pure1_recommendations, **params)

        module.fail_json.assert_called_once()
        assert re.match(message, module.fail_json.call_args[1]["msg"])
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_not_called()
        assert _calls(pure1_client) == []

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({}, id="default"),
            pytest.param({"type": HARDWARE}, id="hardware upgrades"),
            pytest.param({"type": LICENSE}, id="license upgrades"),
            pytest.param({"type": RENEWAL}, id="renewals"),
        ],
    )
    def test_nothing_in_pure1_gives_four_empty_lists(self, pure1_client, params):
        _answer(pure1_client)

        module = run_main(pure1_recommendations, **params)

        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_recommendations={
                "flasharray_hardware_upgrade": [],
                "license_upgrade": [],
                "support_contract_renewal": [],
                "other": [],
            },
        )

    def test_named_array_without_a_recommendation_gives_four_empty_lists(
        self, pure1_client
    ):
        _known_arrays(pure1_client, "array1")
        _answer(
            pure1_client,
            [_make_hardware(target="array2")],
            [_make_license(target="array2")],
            [_make_renewal(target="array2", support_contracts=[_contract("array2")])],
            every=[_make_other(target="array2")],
        )

        module = run_main(pure1_recommendations, array=["array1"])

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_recommendations=_empty()
        )

    def test_unknown_array_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [valid_response(), valid_response()]

        module = run_main(pure1_recommendations, array=["array9"])

        module.fail_json.assert_called_once_with(
            msg="Array array9 is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        assert _calls(pure1_client) == []

    @pytest.mark.parametrize("method", sorted(METHODS.values()))
    def test_py_pure_client_without_a_call_fails_before_any_request(
        self, pure1_client, monkeypatch, method
    ):
        """All four calls came with one release: a task needs them whatever
        its type"""
        monkeypatch.setattr(pypureclient, "__version__", "1.94.2")
        _answer(pure1_client)
        # a client of an older py-pure-client has no such method
        setattr(pure1_client, method, None)

        module = run_main(pure1_recommendations, type=LICENSE, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="py-pure-client 1.99.0 or later is required: this module needs "
            "Client.{0}(), which the installed py-pure-client 1.94.2 does not "
            "have.".format(method)
        )
        module.exit_json.assert_not_called()
        # the check of get_pure1 and nothing after it
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        for other in METHODS.values():
            if other != method:
                getattr(pure1_client, other).assert_not_called()

    def test_warnings_reach_the_task(self, pure1_client):
        _answer(pure1_client)
        pure1_client.get_recommendations_license_upgrade.side_effect = paged_answers(
            RESPONSES[LICENSE], [_make_license()], total_item_count=4
        )

        module = run_main(pure1_recommendations)

        module.warn.assert_called_once_with(
            "Pure1 returned 1 of 4 license upgrade recommendations: the result "
            "may be incomplete."
        )
        expected = _empty()
        expected[LICENSE] = [_license_row()]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_recommendations=expected
        )

    @pytest.mark.parametrize(
        "method, params",
        [
            pytest.param(METHODS[HARDWARE], {}, id="hardware upgrades"),
            pytest.param(
                METHODS[HARDWARE], {"type": HARDWARE}, id="hardware upgrades alone"
            ),
            pytest.param(METHODS[LICENSE], {}, id="license upgrades"),
            pytest.param(
                METHODS[LICENSE], {"type": LICENSE}, id="license upgrades alone"
            ),
            pytest.param(METHODS[RENEWAL], {}, id="renewals"),
            pytest.param(METHODS[RENEWAL], {"type": RENEWAL}, id="renewals alone"),
            pytest.param(METHODS[OTHER], {}, id="every type"),
            pytest.param("get_arrays", {"array": ["array1"]}, id="arrays"),
        ],
    )
    @pytest.mark.parametrize(
        "answer, reason",
        [
            pytest.param(
                error_response(403, "Access denied"),
                " (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                error_response(
                    400,
                    "Bad request",
                    "Multi-field sorting is not supported by the recommendation "
                    "service",
                ),
                " (HTTP 400): Bad request: Multi-field sorting is not supported "
                "by the recommendation service",
                id="error answer with context",
            ),
            pytest.param(
                ErrorResponse(503, [], {}),
                " (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                PureError("token refused"), ": PureError: token refused", id="PureError"
            ),
            pytest.param(
                TimeoutError("The read operation timed out"),
                ": TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, params, answer, reason
    ):
        _answer(
            pure1_client,
            [_make_hardware()],
            [_make_license()],
            [_make_renewal()],
            every=[_make_other()],
        )
        if method == "get_arrays":
            # the first answer is for the check of get_pure1
            pure1_client.get_arrays.side_effect = [valid_response(), answer]
        elif isinstance(answer, Exception):
            getattr(pure1_client, method).side_effect = answer
        else:
            getattr(pure1_client, method).return_value = answer

        module = run_main(pure1_recommendations, **params)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(method, reason)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()


def _contains(block):
    """Return the keys under a contains block of RETURN, by their path"""
    paths = set()
    for key, entry in block.items():
        paths.add(key)
        for path in _contains(entry.get("contains", {})):
            paths.add(key + "." + path)
    return paths


def _paths(row):
    """Return the keys of a row of a result by their path, through its
    dictionaries and through the first entry of its lists"""
    paths = set()
    for key, value in row.items():
        paths.add(key)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            paths.update(key + "." + path for path in _paths(value))
    return paths


RETURN_TYPES = {"dict": dict, "float": float, "int": int, "list": list, "str": str}


def _check_types(documented, row, where):
    """Compare the type RETURN gives each key with the type of its value"""
    assert list(documented) == list(row), where
    for key, entry in documented.items():
        value = row[key]
        assert RETURN_TYPES[entry["type"]] is type(value), where + "." + key
        if entry["type"] == "list":
            assert entry["elements"] == "dict", where + "." + key
            (value,) = value[:1]
        if "contains" in entry:
            _check_types(entry["contains"], value, where + "." + key)


def _check_sample(sample, like, where):
    """Compare a row of the sample of RETURN with a row of a result

    The keys and their order are the same, a value that is not null has the
    type of the result, and every time is the text of its epoch.
    """
    assert list(sample) == list(like), where
    for key, value in sample.items():
        if key.endswith("_epoch"):
            assert sample[key[: -len("_epoch")]] == pure1_time(value), where + "." + key
        if value is None:
            continue
        assert type(value) is type(like[key]), where + "." + key
        if isinstance(value, list) and value:
            assert len(value) == 1, where + "." + key
            value, other = value[0], like[key][0]
        else:
            other = like[key]
        if isinstance(value, dict):
            _check_sample(value, other, where + "." + key)


def _template(text, variables):
    """Return what a template of a task gives, in the type of its value

    As in a playbook, a key that a variable does not have is an error
    (jinja2.UndefinedError).
    """
    environment = NativeEnvironment(undefined=jinja2.StrictUndefined)
    environment.filters["flatten"] = FilterModule().filters()["flatten"]
    value = environment.from_string(text).render(**variables)
    if isinstance(value, jinja2.Undefined):
        # a template that is one expression hands a missing value on
        raise jinja2.UndefinedError(text)
    return value


def _condition(expression, variables):
    return bool(_template("{{ " + expression + " }}", variables))


def _play_examples(pure1_client, **answers):
    """Run the tasks of EXAMPLES in their order and return what is read

    A task of the module runs through main() with the options of the example
    and registers its result. answers gives, by the name the task registers,
    what Pure1 has for it, as the keywords of _answer. A task that reads a
    result gives a list with its templates rendered for each item of its
    loop, once when it has no loop.
    """
    registered = {}
    read = []
    for task in yaml.safe_load(pure1_recommendations.EXAMPLES):
        options = task.get("everpure.pure1.pure1_recommendations")
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            _known_arrays(pure1_client, *options.get("array", []))
            _answer(pure1_client, **answers[task["register"]])
            module = run_main(pure1_recommendations, **options)
            registered[task["register"]] = module.exit_json.call_args[1]
            continue
        (action,) = set(task) - {"name", "when", "loop"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        rendered = []
        for item in items:
            variables = dict(registered, item=item)
            made = {}
            if "when" in task:
                made["when"] = _condition(task["when"], variables)
            for key, value in task[action].items():
                if key == "that":
                    made[key] = [_condition(each, variables) for each in value]
                else:
                    made[key] = _template(value, variables)
            rendered.append(made)
        read.append(rendered)
    return read


class TestDocumentation:
    """RETURN and EXAMPLES against what the module returns."""

    def _result(self, pure1_client):
        _answer(
            pure1_client,
            [_make_hardware()],
            [_make_license()],
            [_make_renewal()],
            every=[_make_other()],
        )
        module = run_main(pure1_recommendations)
        return module.exit_json.call_args[1]

    def test_return_names_the_one_key_of_the_result_and_its_lists(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_recommendations.RETURN)
        assert set(result) - {"changed"} == set(documented) == {"pure1_recommendations"}
        assert list(result["pure1_recommendations"]) == list(
            documented["pure1_recommendations"]["contains"]
        )

    @pytest.mark.parametrize("kind", KINDS)
    def test_return_lists_the_keys_of_a_recommendation(self, pure1_client, kind):
        """In both directions, and down to the keys of what a key holds"""
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_recommendations.RETURN)
        documented = documented["pure1_recommendations"]["contains"][kind]
        (row,) = result["pure1_recommendations"][kind]
        assert _contains(documented["contains"]) == _paths(row)
        assert list(documented["contains"]) == list(row)

    def test_every_key_of_return_is_an_identifier(self):
        """validate-modules of ansible-core 2.21 refuses any other key"""
        documented = yaml.safe_load(pure1_recommendations.RETURN)

        paths = _contains(documented)
        assert len(paths) > 100
        for path in paths:
            assert all(key.isidentifier() for key in path.split(".")), path

    @pytest.mark.parametrize("kind", KINDS)
    def test_return_gives_the_type_of_every_key(self, pure1_client, kind):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_recommendations.RETURN)
        documented = documented["pure1_recommendations"]
        assert documented["type"] == "dict"
        assert documented["contains"][kind]["type"] == "list"
        assert documented["contains"][kind]["elements"] == "dict"
        (row,) = result["pure1_recommendations"][kind]
        _check_types(documented["contains"][kind]["contains"], row, kind)

    def test_sample_has_the_shape_of_a_result(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_recommendations.RETURN)
        sample = documented["pure1_recommendations"]["sample"]
        assert list(sample) == list(result["pure1_recommendations"])
        for kind in (HARDWARE, LICENSE, RENEWAL):
            (row,) = sample[kind]
            (like,) = result["pure1_recommendations"][kind]
            _check_sample(row, like, kind)
            assert row["recommendation_type"] == kind.replace("_", "-")
        assert sample[OTHER] == []

    def test_sample_shows_a_null_and_a_set_subscription_license(self):
        documented = yaml.safe_load(pure1_recommendations.RETURN)
        sample = documented["pure1_recommendations"]["sample"]

        assert sample[HARDWARE][0]["subscription_license"] is None
        assert list(sample[LICENSE][0]["subscription_license"]) == ["id", "name"]

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "Every recommendation when not set or empty, also one that is for "
                "no array.",
                id="an empty list of arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "A name is matched without regard to case and the result carries "
                "the name as Pure1 spells it. A name that Pure1 does not know "
                "fails the task.",
                id="how an array is named",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "A support contract renewal is also returned when one of its "
                "support contracts is for one of them, and then with all its "
                "support contracts.",
                id="renewal of a named array",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "A license upgrade can have a subscription license as its target "
                "and not an array. It is then not returned when I(array) is set.",
                id="license upgrade for a license",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "Naming arrays costs one more request to Pure1.",
                id="cost of naming arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.type",
                "With C(all) every list of the result is filled, which takes four "
                "requests to Pure1. With one of the types only the list of that "
                "type is filled, which takes one request, and the other lists "
                "are empty.",
                id="cost of the types",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations",
                "Each list is ordered by the time the recommendations were made, "
                "the newest first.",
                id="order of a list",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.other",
                "Empty when Pure1 has none, and when I(type) is not C(all).",
                id="other types",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.license_upgrade.contains."
                "subscription_license",
                "Null when it is linked to none.",
                id="no subscription license",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations",
                "A key of a recommendation is null when Pure1 reports no value "
                "for it, unless the description of the key says otherwise.",
                id="a key without a value",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.flasharray_hardware_upgrade."
                "contains.target",
                "Always a dictionary. Its keys are null when Pure1 names no target.",
                id="no target of a hardware upgrade",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.license_upgrade.contains.target",
                "Always a dictionary. Its keys are null when Pure1 names no target.",
                id="no target of a license upgrade",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.support_contract_renewal."
                "contains.target",
                "Always a dictionary. Its keys are null when Pure1 names no target.",
                id="no target of a renewal",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.other.contains.target",
                "Always a dictionary. Its keys are null when Pure1 names no target.",
                id="no target of another type",
            ),
            pytest.param(
                "RETURN",
                "pure1_recommendations.contains.support_contract_renewal."
                "contains.support_contracts.contains.target",
                "Always a dictionary. Its keys are null when Pure1 names no target.",
                id="no target of a support contract",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_recommendations, block))
        for key in path.split("."):
            entry = entry[key]

        assert says in " ".join(entry["description"])

    def test_documentation_names_the_py_pure_client_release_of_the_calls(self):
        """The calls came with the release the collection requires: the note
        names the calls and their Pure1 REST version, and no release"""
        documentation = yaml.safe_load(pure1_recommendations.DOCUMENTATION)

        assert documentation["version_added"] == "1.6.0"
        calls = ["C({0})".format(method) for method in pure1_recommendations.METHODS]
        assert documentation["notes"] == [
            "This module makes the Pure1 REST 1.6 calls {0} and {1}.".format(
                ", ".join(calls[:-1]), calls[-1]
            )
        ]
        assert "py-pure-client" not in " ".join(documentation["notes"])

    def test_examples_use_every_option_of_the_module(self):
        options = set(yaml.safe_load(pure1_recommendations.DOCUMENTATION)["options"])
        used = set()
        for task in yaml.safe_load(pure1_recommendations.EXAMPLES):
            arguments = task.get("everpure.pure1.pure1_recommendations")
            if arguments:
                used.update(set(arguments) - {"app_id", "key_file"})
        assert used == options

    def test_examples_read_what_the_module_returns(self, pure1_client):
        """The tasks after a task of the module, on the result it registered"""
        read = _play_examples(
            pure1_client,
            recommendations=dict(
                hardware=[
                    _make_hardware("hardware1", "array1"),
                    _make_hardware("hardware2", "array2", created_at=CREATED - 1),
                ]
            ),
            license_upgrades=dict(
                licenses=[
                    _make_license(current_reserve_exceeded_in_days=30),
                    _make_license(
                        "license-upgrade2", current_reserve_exceeded_in_days=None
                    ),
                ]
            ),
            renewals=dict(
                renewals=[
                    _make_renewal("renewal1"),
                    _make_renewal(
                        "renewal2",
                        "array2",
                        created_at=CREATED - 1,
                        support_contracts=[_contract("array2", expires=CREATED)],
                    ),
                    _make_renewal("renewal3", "array3", support_contracts=None),
                ]
            ),
        )

        assert read == [
            [{"msg": ["array1", "array2"]}],
            [
                {
                    "that": [True],
                    "fail_msg": "The reserve of a subscription license is exceeded "
                    "within 30 days",
                }
            ],
            [
                {"msg": "array1: 2026-11-24 11:16:35 UTC"},
                {"msg": "array2: 2026-11-24 11:16:35 UTC"},
                {"msg": "array2: 2021-02-03 13:04:05 UTC"},
            ],
        ]

    def test_examples_work_when_pure1_has_no_recommendation(self, pure1_client):
        read = _play_examples(
            pure1_client, recommendations={}, license_upgrades={}, renewals={}
        )

        assert read[0] == [{"msg": []}]
        assert [made["that"] for made in read[1]] == [[True]]
        # nothing to loop over
        assert read[2] == []

    def test_check_of_the_examples_fails_on_what_it_looks_for(self, pure1_client):
        read = _play_examples(
            pure1_client,
            recommendations={},
            license_upgrades=dict(
                licenses=[
                    _make_license(current_reserve_exceeded_in_days=45),
                    _make_license(
                        "license-upgrade2", current_reserve_exceeded_in_days=29
                    ),
                ]
            ),
            renewals={},
        )

        assert [made["that"] for made in read[1]] == [[False]]

    def test_check_of_the_examples_counts_a_reserve_exceeded_today(self, pure1_client):
        """0 days is a number, not a value that Pure1 did not send"""
        read = _play_examples(
            pure1_client,
            recommendations={},
            license_upgrades=dict(
                licenses=[_make_license(current_reserve_exceeded_in_days=0)]
            ),
            renewals={},
        )

        assert [made["that"] for made in read[1]] == [[False]]

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param(
                "{{ result['pure1_recommendations']['hardware_upgrade'] }}", id="key"
            ),
            pytest.param(
                "{{ result.pure1_recommendations.flashblade_hardware_upgrade | length }}",
                id="filter",
            ),
            pytest.param(
                "{{ result['pure1_recommendations']['flasharray_hardware_upgrade'] "
                "| map(attribute='array.name') | list }}",
                id="attribute of an item",
            ),
            pytest.param(
                "{{ result['pure1_recommendations']['support_contract_renewal'] "
                "| map(attribute='contracts') | flatten }}",
                id="list of an item",
            ),
        ],
    )
    def test_example_that_reads_a_key_the_module_does_not_return_fails(
        self, pure1_client, template
    ):
        result = self._result(pure1_client)

        with pytest.raises(jinja2.UndefinedError):
            _template(template, {"result": result})
