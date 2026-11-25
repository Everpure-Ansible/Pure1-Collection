# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_buckets module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import jinja2
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule
from jinja2.nativetypes import NativeEnvironment
from pypureclient import ErrorResponse, PureError

from plugins.module_utils.pure1 import pure1_argument_spec, pure1_time
from plugins.modules import pure1_buckets
from plugins.modules.pure1_buckets import generate_accounts_dict
from tests.unit.conftest import (
    ansible_module,
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# Every time differs in month, day, hour, minute and second, and the times
# of an account differ from those of a bucket
ACCOUNT_CREATED = 1612357445000
ACCOUNT_AS_OF = 1795515330000
BUCKET_CREATED = 1612427400000
BUCKET_AS_OF = 1795518995000


def _make_account(name="account1", array="array1", **overrides):
    fields = dict(
        id=name + "-id",
        name=name,
        arrays=[array_reference(array)],
        created=ACCOUNT_CREATED,
        object_count=1500,
        as_of=ACCOUNT_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("ObjectStoreAccount", **fields)


def _account_reference(name="account1", **overrides):
    fields = dict(id=name + "-id", name=name, resource_type="object-store-accounts")
    fields.update(overrides)
    return sdk_model("FixedReference", **fields)


def _make_bucket(name="bucket1", account="account1", array="array1", **overrides):
    """account is the name of the account, its reference, or None for none"""
    if isinstance(account, str):
        account = _account_reference(account)
    fields = dict(
        id=name + "-id",
        name=name,
        arrays=[array_reference(array)],
        account=account,
        created=BUCKET_CREATED,
        destroyed=False,
        object_count=700,
        versioning="enabled",
        as_of=BUCKET_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("Bucket", **fields)


def _account_row(name="account1", buckets=None, **overrides):
    """The result of the account that _make_account(name) builds"""
    row = {
        "id": name + "-id",
        "created": "2021-02-03 13:04:05 UTC",
        "created_epoch": 1612357445000,
        "object_count": 1500,
        "as_of": "2026-11-24 10:15:30 UTC",
        "as_of_epoch": 1795515330000,
        "buckets": buckets or {},
    }
    row.update(overrides)
    return row


def _bucket_row(name="bucket1", **overrides):
    """The result of the bucket that _make_bucket(name) builds"""
    row = {
        "id": name + "-id",
        "created": "2021-02-04 08:30:00 UTC",
        "created_epoch": 1612427400000,
        "destroyed": False,
        "object_count": 700,
        "versioning": "enabled",
        "as_of": "2026-11-24 11:16:35 UTC",
        "as_of_epoch": 1795518995000,
    }
    row.update(overrides)
    return row


# What only the reference of a bucket tells of an account
REFERENCE_ONLY = dict(
    created=None, created_epoch=None, object_count=None, as_of=None, as_of_epoch=None
)


def _params(**params):
    """The options as the argument spec leaves them when the task sets none"""
    options = dict(array=None, name=None, account=None, destroyed=None)
    options.update(params)
    return options


def _answer(pure1_client, buckets=(), accounts=()):
    pure1_client.get_buckets.return_value = valid_response(buckets)
    pure1_client.get_object_store_accounts.return_value = valid_response(accounts)


def _known_arrays(pure1_client, *names):
    """Answer the request that resolves the arrays of a task, after the check
    of get_pure1"""
    arrays = [sdk_model("Array", id=name + "-id", name=name) for name in names]
    pure1_client.get_arrays.side_effect = [valid_response(), valid_response(arrays)]


def _without_arrays(item):
    item.arrays = None
    return item


def _with_empty_arrays(item):
    # The SDK reads a response without validating it, so an empty list and a
    # null in a list arrive as they are; the constructor of the model refuses
    # both
    del item.arrays[:]
    return item


def _with_null_array(item):
    item.arrays[0] = None
    return item


class TestGenerateAccountsDict:
    """Tests for generate_accounts_dict."""

    def test_buckets_are_filed_by_array_and_account(self, pure1_client):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1"),
                _make_bucket("bucket2", versioning="none", object_count=800),
                _make_bucket("bucket3", account="account2"),
                _make_bucket("bucket4", account="account3", array="array2"),
            ],
            accounts=[
                _make_account("account1"),
                _make_account("account2", object_count=700),
                _make_account("account3", array="array2"),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    buckets={
                        "bucket1": _bucket_row(),
                        "bucket2": _bucket_row(
                            "bucket2", versioning="none", object_count=800
                        ),
                    }
                ),
                "account2": _account_row(
                    "account2",
                    object_count=700,
                    buckets={"bucket3": _bucket_row("bucket3")},
                ),
            },
            "array2": {
                "account3": _account_row(
                    "account3", buckets={"bucket4": _bucket_row("bucket4")}
                )
            },
        }
        module.warn.assert_not_called()

    def test_no_option_sends_no_filter_and_two_requests(self, pure1_client):
        _answer(pure1_client, [_make_bucket()], [_make_account()])

        generate_accounts_dict(ansible_module(**_params()), pure1_client)

        pure1_client.get_buckets.assert_called_once_with(filter=None)
        pure1_client.get_object_store_accounts.assert_called_once_with(filter=None)
        pure1_client.get_arrays.assert_not_called()

    @pytest.mark.parametrize(
        "params, bucket_filter, account_filter",
        [
            pytest.param(
                {"account": "account1"},
                "account.name='account1'",
                "name='account1'",
                id="account",
            ),
            pytest.param({"name": ["bucket1"]}, "name='bucket1'", None, id="name"),
            pytest.param(
                {"name": ["bucket1", "bucket2", "bucket1"]},
                "name=('bucket1','bucket2')",
                None,
                id="names",
            ),
            pytest.param({"destroyed": True}, "destroyed=true", None, id="destroyed"),
            pytest.param(
                {"destroyed": False}, "destroyed=false", None, id="not destroyed"
            ),
            pytest.param(
                {"account": "account1", "name": ["bucket1"], "destroyed": False},
                "account.name='account1' and name='bucket1' and destroyed=false",
                "name='account1'",
                id="account, name and destroyed",
            ),
            pytest.param(
                {"account": "o'brien", "name": ["back\\slash", "it's"]},
                "account.name='o\\'brien' and name=('back\\\\slash','it\\'s')",
                "name='o\\'brien'",
                id="escaped",
            ),
            pytest.param(
                {"account": "a or b", "name": ["x and y"]},
                "account.name='a or b' and name='x and y'",
                "name='a or b'",
                id="words of the filter language",
            ),
            pytest.param({"name": [""]}, "name=''", None, id="empty bucket name"),
            pytest.param(
                {"array": [], "name": [], "account": ""}, None, None, id="empty values"
            ),
        ],
    )
    def test_options_become_filters(
        self, pure1_client, params, bucket_filter, account_filter
    ):
        _answer(pure1_client, [_make_bucket()], [_make_account()])

        generate_accounts_dict(ansible_module(**_params(**params)), pure1_client)

        pure1_client.get_buckets.assert_called_once_with(filter=bucket_filter)
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter=account_filter
        )
        pure1_client.get_arrays.assert_not_called()

    @pytest.mark.parametrize(
        "params, array_filter, bucket_filter, account_filter",
        [
            pytest.param(
                {"array": ["ARRAY1"]},
                "name='ARRAY1'",
                "arrays.name='array1'",
                "arrays.name='array1'",
                id="one array, as Pure1 spells it",
            ),
            pytest.param(
                {"array": ["array2", "Array1"]},
                "name=('array2','Array1')",
                "arrays.name=('array2','array1')",
                "arrays.name=('array2','array1')",
                id="two arrays",
            ),
            pytest.param(
                {
                    "array": ["array1"],
                    "account": "account1",
                    "name": ["bucket1", "bucket2"],
                    "destroyed": True,
                },
                "name='array1'",
                "arrays.name='array1' and account.name='account1' and "
                "name=('bucket1','bucket2') and destroyed=true",
                "arrays.name='array1' and name='account1'",
                id="every option",
            ),
        ],
    )
    def test_arrays_are_resolved_first_and_filtered_by_the_names_of_pure1(
        self, pure1_client, params, array_filter, bucket_filter, account_filter
    ):
        pure1_client.get_arrays.return_value = valid_response(
            [
                sdk_model("Array", id="array1-id", name="array1"),
                sdk_model("Array", id="array2-id", name="array2"),
            ]
        )
        _answer(pure1_client, [_make_bucket()], [_make_account()])

        result = generate_accounts_dict(
            ansible_module(**_params(**params)), pure1_client
        )

        pure1_client.get_arrays.assert_called_once_with(filter=array_filter)
        pure1_client.get_buckets.assert_called_once_with(filter=bucket_filter)
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter=account_filter
        )
        assert list(result) == ["array1"]

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
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name=name) for name in known]
        )

        with pytest.raises(SystemExit):
            generate_accounts_dict(module, pure1_client)

        module.fail_json.assert_called_once_with(msg=message)
        pure1_client.get_buckets.assert_not_called()
        pure1_client.get_object_store_accounts.assert_not_called()

    def test_empty_array_name_fails_before_any_request(self, pure1_client):
        module = ansible_module(**_params(array=["array1", " "]))

        with pytest.raises(SystemExit):
            generate_accounts_dict(module, pure1_client)

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_not_called()
        pure1_client.get_buckets.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({}, id="no option"),
            pytest.param({"account": "account1"}, id="account"),
            pytest.param({"name": [], "account": ""}, id="empty values"),
        ],
    )
    def test_account_without_bucket_is_listed_without_a_selection_of_buckets(
        self, pure1_client, params
    ):
        module = ansible_module(**_params(**params))
        _answer(
            pure1_client,
            buckets=[_make_bucket()],
            accounts=[
                _make_account("account1"),
                _make_account("account2"),
                _make_account("account3", array="array2"),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(buckets={"bucket1": _bucket_row()}),
                "account2": _account_row("account2"),
            },
            "array2": {"account3": _account_row("account3")},
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": ["bucket1"]}, id="name"),
            pytest.param({"destroyed": True}, id="destroyed"),
            pytest.param({"destroyed": False}, id="not destroyed"),
            pytest.param(
                {"account": "account1", "destroyed": False}, id="account and destroyed"
            ),
        ],
    )
    def test_account_without_returned_bucket_is_left_out_with_a_selection(
        self, pure1_client, params
    ):
        """And with it an array that has no other account"""
        module = ansible_module(**_params(**params))
        _answer(
            pure1_client,
            buckets=[_make_bucket()],
            accounts=[
                _make_account("account1"),
                _make_account("account2"),
                _make_account("account3", array="array2"),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": ["bucket1"]}, id="name"),
            pytest.param({"destroyed": True}, id="destroyed"),
            pytest.param({"destroyed": False}, id="not destroyed"),
        ],
    )
    def test_selection_without_a_bucket_asks_for_no_account(self, pure1_client, params):
        # get_object_store_accounts has no answer: reading one fails the test
        pure1_client.get_buckets.return_value = valid_response([])

        result = generate_accounts_dict(
            ansible_module(**_params(**params)), pure1_client
        )

        assert result == {}
        pure1_client.get_buckets.assert_called_once()
        pure1_client.get_object_store_accounts.assert_not_called()

    @pytest.mark.parametrize(
        "params, account_filter",
        [
            pytest.param({}, None, id="no option"),
            pytest.param({"account": "account1"}, "name='account1'", id="account"),
        ],
    )
    def test_accounts_are_listed_when_there_is_no_bucket(
        self, pure1_client, params, account_filter
    ):
        _answer(pure1_client, accounts=[_make_account()])

        result = generate_accounts_dict(
            ansible_module(**_params(**params)), pure1_client
        )

        assert result == {"array1": {"account1": _account_row()}}
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter=account_filter
        )

    def test_objects_with_only_their_identifying_fields(self, pure1_client):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                sdk_model(
                    "Bucket",
                    name="bucket1",
                    arrays=[array_reference("array1")],
                    account=sdk_model("FixedReference", name="account1"),
                )
            ],
            accounts=[
                sdk_model(
                    "ObjectStoreAccount",
                    name="account1",
                    arrays=[array_reference("array1")],
                )
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": {
                    "id": None,
                    "created": None,
                    "created_epoch": None,
                    "object_count": None,
                    "as_of": None,
                    "as_of_epoch": None,
                    "buckets": {
                        "bucket1": {
                            "id": None,
                            "created": None,
                            "created_epoch": None,
                            "destroyed": None,
                            "object_count": None,
                            "versioning": None,
                            "as_of": None,
                            "as_of_epoch": None,
                        }
                    },
                }
            }
        }
        module.warn.assert_not_called()

    def test_values_that_are_false_are_values(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(
                    object_count=0, destroyed=False, versioning="", created=0, as_of=0
                )
            ],
            accounts=[_make_account(object_count=0, created=0, as_of=0, id="")],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {
                "account1": {
                    "id": "",
                    "created": "1970-01-01 00:00:00 UTC",
                    "created_epoch": 0,
                    "object_count": 0,
                    "as_of": "1970-01-01 00:00:00 UTC",
                    "as_of_epoch": 0,
                    "buckets": {
                        "bucket1": {
                            "id": "bucket1-id",
                            "created": "1970-01-01 00:00:00 UTC",
                            "created_epoch": 0,
                            "destroyed": False,
                            "object_count": 0,
                            "versioning": "",
                            "as_of": "1970-01-01 00:00:00 UTC",
                            "as_of_epoch": 0,
                        }
                    },
                }
            }
        }

    def test_destroyed_bucket_is_reported_as_destroyed(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", destroyed=True, versioning="suspended"),
                _make_bucket("bucket2"),
            ],
            accounts=[_make_account()],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result["array1"]["account1"]["buckets"] == {
            "bucket1": _bucket_row(destroyed=True, versioning="suspended"),
            "bucket2": _bucket_row("bucket2"),
        }

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_are_in_utc_whatever_the_time_zone_of_the_host(self, pure1_client):
        _answer(pure1_client, [_make_bucket()], [_make_account()])

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        account = result["array1"]["account1"]
        assert account["created"] == "2021-02-03 13:04:05 UTC"
        assert account["as_of"] == "2026-11-24 10:15:30 UTC"
        assert account["buckets"]["bucket1"]["created"] == "2021-02-04 08:30:00 UTC"
        assert account["buckets"]["bucket1"]["as_of"] == "2026-11-24 11:16:35 UTC"

    def test_bucket_of_an_account_that_pure1_does_not_list(self, pure1_client):
        """The account is returned with what the reference of the bucket tells"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", account="account2"),
                _make_bucket("bucket2", account="account2"),
            ],
            accounts=[_make_account("account1")],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(),
                "account2": _account_row(
                    "account2",
                    buckets={
                        "bucket1": _bucket_row("bucket1"),
                        "bucket2": _bucket_row("bucket2"),
                    },
                    **REFERENCE_ONLY
                ),
            }
        }
        module.warn.assert_not_called()

    def test_bucket_on_an_array_that_has_no_listed_account(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[_make_bucket(array="array2")],
            accounts=[_make_account("account1", array="array1")],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {"account1": _account_row()},
            "array2": {
                "account1": _account_row(
                    buckets={"bucket1": _bucket_row()}, **REFERENCE_ONLY
                )
            },
        }

    def test_bucket_is_filed_by_the_id_of_its_account(self, pure1_client):
        """The name in the reference of the bucket does not count beside the id"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(
                    "bucket1",
                    account=_account_reference("old-name", id="account1-id"),
                ),
                _make_bucket(
                    "bucket2", account=sdk_model("FixedReference", id="account1-id")
                ),
            ],
            accounts=[_make_account("account1"), _make_account("old-name", id="x")],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    buckets={
                        "bucket1": _bucket_row("bucket1"),
                        "bucket2": _bucket_row("bucket2"),
                    }
                ),
                "old-name": _account_row("old-name", id="x"),
            }
        }
        module.warn.assert_not_called()

    def test_bucket_without_account_id_is_filed_by_the_name_of_its_account(
        self, pure1_client
    ):
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(account=sdk_model("FixedReference", name="account1"))
            ],
            accounts=[_make_account("account1")],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }

    def test_bucket_without_account_is_not_filed_under_an_account_without_id(
        self, pure1_client
    ):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[_make_bucket(account=None)],
            accounts=[
                sdk_model(
                    "ObjectStoreAccount",
                    name="account1",
                    arrays=[array_reference("array1")],
                )
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(id=None, **REFERENCE_ONLY)}
        }
        module.warn.assert_called_once_with(
            "Buckets that Pure1 returned without a name, without an account or "
            "without an array are left out of the result: 1"
        )

    def test_account_id_counts_per_array(self, pure1_client):
        """An id that another array has for an account files no bucket there"""
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(
                    array="array2", account=_account_reference("other", id="same-id")
                )
            ],
            accounts=[
                _make_account("account1", array="array1", id="same-id"),
                _make_account("account2", array="array2"),
            ],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {"account1": _account_row(id="same-id")},
            "array2": {
                "account2": _account_row("account2"),
                "other": _account_row(
                    id="same-id", buckets={"bucket1": _bucket_row()}, **REFERENCE_ONLY
                ),
            },
        }

    def test_same_names_under_other_accounts_and_arrays_stay_apart(self, pure1_client):
        """A bucket name is unique within its account, an account name within
        its array"""
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", account="account1", id="b1"),
                _make_bucket("bucket1", account="account2", id="b2"),
                _make_bucket("bucket1", account="account1", array="array2", id="b3"),
            ],
            accounts=[
                _make_account("account1", id="a1"),
                _make_account("account2", id="a2"),
                _make_account("account1", array="array2", id="a3"),
            ],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    id="a1", buckets={"bucket1": _bucket_row(id="b1")}
                ),
                "account2": _account_row(
                    id="a2", buckets={"bucket1": _bucket_row(id="b2")}
                ),
            },
            "array2": {
                "account1": _account_row(
                    id="a3", buckets={"bucket1": _bucket_row(id="b3")}
                )
            },
        }

    def test_object_on_two_arrays_is_listed_under_each(self, pure1_client):
        """And the account of one array does not get the buckets of the other"""
        both = [array_reference("array1"), array_reference("array2")]
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", arrays=list(both)),
                _make_bucket("bucket2", array="array2"),
            ],
            accounts=[_make_account(arrays=list(both))],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})},
            "array2": {
                "account1": _account_row(
                    buckets={
                        "bucket1": _bucket_row(),
                        "bucket2": _bucket_row("bucket2"),
                    }
                )
            },
        }

    def test_object_on_two_arrays_is_listed_under_the_arrays_of_the_task_only(
        self, pure1_client
    ):
        module = ansible_module(**_params(array=["ARRAY1"]))
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", name="array1")]
        )
        both = [array_reference("array1"), array_reference("array2")]
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", arrays=list(both)),
                # Pure1 matches a name whatever its case: another array
                _make_bucket("bucket2", array="Array1"),
            ],
            accounts=[
                _make_account(arrays=list(both)),
                _make_account("account2", array="Array1"),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        pure1_client.get_buckets.assert_called_once_with(filter="arrays.name='array1'")
        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        # what is on another array is not of the task, and not a defect
        module.warn.assert_not_called()

    def test_array_named_twice_in_an_object_is_one_array(self, pure1_client):
        twice = [array_reference("array1"), array_reference("array1")]
        _answer(
            pure1_client,
            buckets=[_make_bucket(arrays=list(twice))],
            accounts=[_make_account(arrays=list(twice))],
        )

        result = generate_accounts_dict(ansible_module(**_params()), pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(
                lambda: sdk_model(
                    "ObjectStoreAccount", id="x", arrays=[array_reference("array1")]
                ),
                id="no name",
            ),
            pytest.param(
                lambda: _without_arrays(_make_account("account2")), id="no arrays"
            ),
            pytest.param(
                lambda: _with_empty_arrays(_make_account("account2")),
                id="empty arrays",
            ),
            pytest.param(
                lambda: _with_null_array(_make_account("account2")), id="null array"
            ),
            pytest.param(
                lambda: _make_account(
                    "account2",
                    arrays=[sdk_model("FixedReferenceFqdn", fqdn="a.example.com")],
                ),
                id="array without name",
            ),
        ],
    )
    def test_account_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = ansible_module(**_params())
        _answer(pure1_client, [_make_bucket()], [broken(), _make_account()])

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_called_once_with(
            "Object store accounts that Pure1 returned without a name or "
            "without an array are left out of the result: 1"
        )

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(
                lambda: sdk_model(
                    "Bucket",
                    id="x",
                    arrays=[array_reference("array1")],
                    account=_account_reference(),
                ),
                id="no name",
            ),
            pytest.param(
                lambda: _without_arrays(_make_bucket("bucket2")), id="no arrays"
            ),
            pytest.param(
                lambda: _with_empty_arrays(_make_bucket("bucket2")), id="empty arrays"
            ),
            pytest.param(
                lambda: _with_null_array(_make_bucket("bucket2")), id="null array"
            ),
            pytest.param(
                lambda: _make_bucket(
                    "bucket2",
                    arrays=[sdk_model("FixedReferenceFqdn", fqdn="a.example.com")],
                ),
                id="array without name",
            ),
            pytest.param(
                lambda: _make_bucket("bucket2", account=None), id="no account"
            ),
            pytest.param(
                lambda: _make_bucket("bucket2", account=sdk_model("FixedReference")),
                id="empty account reference",
            ),
            pytest.param(
                lambda: _make_bucket(
                    "bucket2", account=sdk_model("FixedReference", id="unlisted-id")
                ),
                id="account id that is not listed, without a name",
            ),
        ],
    )
    def test_bucket_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = ansible_module(**_params())
        _answer(pure1_client, [broken(), _make_bucket()], [_make_account()])

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_called_once_with(
            "Buckets that Pure1 returned without a name, without an account or "
            "without an array are left out of the result: 1"
        )

    def test_objects_left_out_give_one_warning_per_kind(self, pure1_client):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _without_arrays(_make_bucket("bucket2")),
                _make_bucket(),
                _make_bucket("bucket3", account=None),
                _with_null_array(_make_bucket("bucket4")),
            ],
            accounts=[
                _without_arrays(_make_account("account2")),
                _make_account(),
                _with_null_array(_make_account("account3")),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        assert [call[0] for call in module.warn.call_args_list] == [
            (
                "Object store accounts that Pure1 returned without a name or "
                "without an array are left out of the result: 2",
            ),
            (
                "Buckets that Pure1 returned without a name, without an account "
                "or without an array are left out of the result: 3",
            ),
        ]

    def test_bucket_on_two_arrays_is_left_out_once(self, pure1_client):
        """It is filed where its account is known and counted where it is not"""
        module = ansible_module(**_params())
        both = [array_reference("array1"), array_reference("array2")]
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(
                    arrays=list(both),
                    account=sdk_model("FixedReference", id="account1-id"),
                ),
                _make_bucket(
                    "bucket2",
                    arrays=list(both),
                    account=sdk_model("FixedReference", id="unlisted-id"),
                ),
            ],
            accounts=[_make_account()],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_called_once_with(
            "Buckets that Pure1 returned without a name, without an account or "
            "without an array are left out of the result: 2"
        )

    def test_null_beside_an_array_is_passed_over(self, pure1_client):
        module = ansible_module(**_params())
        bucket = _make_bucket()
        bucket.arrays.append(None)
        account = _make_account()
        account.arrays.insert(0, None)
        _answer(pure1_client, [bucket], [account])

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_not_called()

    def test_empty_names_are_names(self, pure1_client):
        """Only a name that Pure1 did not send leaves an object out"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[_make_bucket("", account="", array="")],
            accounts=[_make_account("", array="")],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "": {"": _account_row("", buckets={"": _bucket_row("")})},
        }
        module.warn.assert_not_called()

    def test_every_page_of_both_listings_is_read(self, pure1_client):
        buckets = [
            _make_bucket("bucket{0}".format(number), account="account1")
            for number in range(5)
        ]
        accounts = [_make_account("account{0}".format(number)) for number in range(4)]
        bucket_answers = paged_answers("BucketGetResponse", buckets, page_size=2)
        account_answers = paged_answers(
            "ObjectStoreAccountGetResponse", accounts, page_size=3
        )
        pure1_client.get_buckets.side_effect = bucket_answers
        pure1_client.get_object_store_accounts.side_effect = account_answers

        result = generate_accounts_dict(
            ansible_module(**_params(account="account1")), pure1_client
        )

        assert sorted(result["array1"]) == [
            "account0",
            "account1",
            "account2",
            "account3",
        ]
        assert sorted(result["array1"]["account1"]["buckets"]) == [
            "bucket0",
            "bucket1",
            "bucket2",
            "bucket3",
            "bucket4",
        ]
        # the module asks once for each listing, the SDK for the later pages
        pure1_client.get_buckets.assert_called_once_with(
            filter="account.name='account1'"
        )
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter="name='account1'"
        )
        assert bucket_answers.requests == [
            {"filter": "account.name='account1'"},
            {"filter": "account.name='account1'", "continuation_token": "page-2"},
            {"filter": "account.name='account1'", "continuation_token": "page-4"},
        ]
        assert account_answers.requests == [
            {"filter": "name='account1'"},
            {"filter": "name='account1'", "continuation_token": "page-3"},
        ]

    def test_listing_shorter_than_pure1_counted_gives_a_warning(self, pure1_client):
        """For each listing, after one more request for what is missing"""
        module = ansible_module(**_params())
        buckets = [_make_bucket("bucket{0}".format(number)) for number in range(3)]
        accounts = [_make_account("account{0}".format(number)) for number in range(2)]
        bucket_answers = paged_answers(
            "BucketGetResponse", buckets, page_size=2, total_item_count=5
        )
        account_answers = paged_answers(
            "ObjectStoreAccountGetResponse", accounts, page_size=2, total_item_count=4
        )
        pure1_client.get_buckets.side_effect = bucket_answers
        pure1_client.get_object_store_accounts.side_effect = account_answers

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account0": _account_row("account0"),
                "account1": _account_row(
                    "account1",
                    buckets={
                        "bucket0": _bucket_row("bucket0"),
                        "bucket1": _bucket_row("bucket1"),
                        "bucket2": _bucket_row("bucket2"),
                    },
                ),
            }
        }
        assert bucket_answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "offset": 3},
        ]
        assert account_answers.requests == [
            {"filter": None},
            {"filter": None, "offset": 2},
        ]
        assert [call[0] for call in module.warn.call_args_list] == [
            ("Pure1 returned 3 of 5 buckets: the result may be incomplete.",),
            (
                "Pure1 returned 2 of 4 object store accounts: the result may be "
                "incomplete.",
            ),
        ]

    def test_listing_without_a_total_is_asked_for_more_once(self, pure1_client):
        module = ansible_module(**_params())
        bucket_answers = paged_answers(
            "BucketGetResponse", [_make_bucket()], total_item_count=None
        )
        account_answers = paged_answers(
            "ObjectStoreAccountGetResponse", [_make_account()], total_item_count=None
        )
        pure1_client.get_buckets.side_effect = bucket_answers
        pure1_client.get_object_store_accounts.side_effect = account_answers

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        assert bucket_answers.requests == [
            {"filter": None},
            {"filter": None, "offset": 1},
        ]
        assert account_answers.requests == [
            {"filter": None},
            {"filter": None, "offset": 1},
        ]
        module.warn.assert_not_called()

    def test_listing_that_may_be_short_without_a_total_gives_a_warning(
        self, pure1_client
    ):
        """Pure1 gives no total and the same page again for the one after it"""
        module = ansible_module(**_params())
        bucket_answers = paged_answers(
            "BucketGetResponse",
            [_make_bucket(), _make_bucket("bucket2")],
            total_item_count=None,
        )
        account_answers = paged_answers(
            "ObjectStoreAccountGetResponse", [_make_account()], total_item_count=None
        )
        pure1_client.get_buckets.side_effect = lambda **page: bucket_answers(
            filter=page["filter"]
        )
        pure1_client.get_object_store_accounts.side_effect = (
            lambda **page: account_answers(filter=page["filter"])
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    buckets={
                        "bucket1": _bucket_row(),
                        "bucket2": _bucket_row("bucket2"),
                    }
                )
            }
        }
        assert [call[0] for call in module.warn.call_args_list] == [
            (
                "Pure1 returned 2 of an unknown number of buckets: the result "
                "may be incomplete.",
            ),
            (
                "Pure1 returned 1 of an unknown number of object store accounts: "
                "the result may be incomplete.",
            ),
        ]

    def test_accounts_of_one_name_on_an_array_give_the_one_created_last(
        self, pure1_client
    ):
        """Each keeps its own buckets, and those of the others go with them"""
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket("bucket1", account=_account_reference(id="id-a")),
                _make_bucket("bucket2", account=_account_reference(id="id-b")),
                _make_bucket("bucket3", account=_account_reference(id="id-c")),
                _make_bucket("bucket4", account="account2", array="array2"),
            ],
            accounts=[
                _make_account(id="id-a", created=ACCOUNT_CREATED - 1),
                _make_account(id="id-b"),
                _make_account(id="id-c", created=None),
                # the same name on another array is another account
                _make_account(array="array2", id="id-d"),
                _make_account("account2", array="array2"),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    id="id-b", buckets={"bucket2": _bucket_row("bucket2")}
                )
            },
            "array2": {
                "account1": _account_row(id="id-d"),
                "account2": _account_row(
                    "account2", buckets={"bucket4": _bucket_row("bucket4")}
                ),
            },
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 2 object store accounts under the name of another "
            "account of the same array: of each such name only the account "
            "created last is in the result, with its buckets."
        )

    def test_buckets_of_one_name_in_an_account_give_the_one_created_last(
        self, pure1_client
    ):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(id="id-a", destroyed=True),
                _make_bucket(id="id-b", created=BUCKET_CREATED + 1),
                _make_bucket(id="id-c", created=None),
                # created at the same time: the id decides
                _make_bucket("bucket2", id="id-d"),
                _make_bucket("bucket2", id="id-e"),
                # the same name in another account is another bucket
                _make_bucket(account="account2", id="id-f"),
            ],
            accounts=[_make_account(), _make_account("account2")],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    buckets={
                        "bucket1": _bucket_row(
                            id="id-b",
                            created="2021-02-04 08:30:00 UTC",
                            created_epoch=1612427400001,
                        ),
                        "bucket2": _bucket_row("bucket2", id="id-e"),
                    }
                ),
                "account2": _account_row(
                    "account2", buckets={"bucket1": _bucket_row(id="id-f")}
                ),
            }
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 3 buckets under the name of another bucket of the "
            "same account: of each such name only the bucket created last is in "
            "the result."
        )

    def test_object_that_pure1_lists_twice_is_one_object(self, pure1_client):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[_make_bucket(), _make_bucket()],
            accounts=[_make_account(), _make_account()],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {"account1": _account_row(buckets={"bucket1": _bucket_row()})}
        }
        module.warn.assert_not_called()

    def test_object_without_id_whose_array_is_named_twice_is_one_object(
        self, pure1_client
    ):
        module = ansible_module(**_params())
        twice = [array_reference("array1"), array_reference("array1")]
        _answer(
            pure1_client,
            buckets=[_make_bucket(id=None, arrays=list(twice))],
            accounts=[_make_account(id=None, arrays=list(twice))],
        )

        result = generate_accounts_dict(module, pure1_client)

        assert result == {
            "array1": {
                "account1": _account_row(
                    id=None, buckets={"bucket1": _bucket_row(id=None)}
                )
            }
        }
        module.warn.assert_not_called()

    def test_objects_of_one_name_without_id_are_told_apart(self, pure1_client):
        module = ansible_module(**_params())
        _answer(
            pure1_client,
            buckets=[
                _make_bucket(id=None, account=sdk_model("FixedReference", name="a")),
                _make_bucket(
                    id=None,
                    account=sdk_model("FixedReference", name="a"),
                    object_count=5,
                ),
            ],
            accounts=[
                _make_account("a", id=None),
                _make_account("a", id=None, object_count=7),
            ],
        )

        result = generate_accounts_dict(module, pure1_client)

        # created at the same time and without an id: the first listed stays
        assert result == {
            "array1": {
                "a": _account_row(
                    "a", id=None, buckets={"bucket1": _bucket_row(id=None)}
                )
            }
        }
        assert [call[0] for call in module.warn.call_args_list] == [
            (
                "Pure1 returned 1 object store accounts under the name of "
                "another account of the same array: of each such name only the "
                "account created last is in the result, with its buckets.",
            ),
            (
                "Pure1 returned 1 buckets under the name of another bucket of "
                "the same account: of each such name only the bucket created "
                "last is in the result.",
            ),
        ]


class TestPure1BucketsMain:
    """Tests for the pure1_buckets main() flow."""

    def test_accounts_and_buckets_are_returned(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[_make_bucket(), _make_bucket("bucket2", array="array2")],
            accounts=[
                _make_account(),
                _make_account("account2"),
                _make_account(array="array2"),
            ],
        )

        # every option is left to the argument spec
        module = run_main(pure1_buckets)

        # the check of get_pure1, and no request of the module for arrays
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_buckets.assert_called_once_with(filter=None)
        pure1_client.get_object_store_accounts.assert_called_once_with(filter=None)
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_buckets={
                "accounts": {
                    "array1": {
                        "account1": {
                            "id": "account1-id",
                            "created": "2021-02-03 13:04:05 UTC",
                            "created_epoch": 1612357445000,
                            "object_count": 1500,
                            "as_of": "2026-11-24 10:15:30 UTC",
                            "as_of_epoch": 1795515330000,
                            "buckets": {
                                "bucket1": {
                                    "id": "bucket1-id",
                                    "created": "2021-02-04 08:30:00 UTC",
                                    "created_epoch": 1612427400000,
                                    "destroyed": False,
                                    "object_count": 700,
                                    "versioning": "enabled",
                                    "as_of": "2026-11-24 11:16:35 UTC",
                                    "as_of_epoch": 1795518995000,
                                }
                            },
                        },
                        "account2": {
                            "id": "account2-id",
                            "created": "2021-02-03 13:04:05 UTC",
                            "created_epoch": 1612357445000,
                            "object_count": 1500,
                            "as_of": "2026-11-24 10:15:30 UTC",
                            "as_of_epoch": 1795515330000,
                            "buckets": {},
                        },
                    },
                    "array2": {
                        "account1": {
                            "id": "account1-id",
                            "created": "2021-02-03 13:04:05 UTC",
                            "created_epoch": 1612357445000,
                            "object_count": 1500,
                            "as_of": "2026-11-24 10:15:30 UTC",
                            "as_of_epoch": 1795515330000,
                            "buckets": {
                                "bucket2": {
                                    "id": "bucket2-id",
                                    "created": "2021-02-04 08:30:00 UTC",
                                    "created_epoch": 1612427400000,
                                    "destroyed": False,
                                    "object_count": 700,
                                    "versioning": "enabled",
                                    "as_of": "2026-11-24 11:16:35 UTC",
                                    "as_of_epoch": 1795518995000,
                                }
                            },
                        }
                    },
                }
            },
        )

    def test_check_mode_does_the_same(self, pure1_client):
        _answer(pure1_client, [_make_bucket()], [_make_account()])

        module = run_main(pure1_buckets, check_mode=True)

        pure1_client.get_buckets.assert_called_once_with(filter=None)
        pure1_client.get_object_store_accounts.assert_called_once_with(filter=None)
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_buckets={
                "accounts": {
                    "array1": {
                        "account1": _account_row(buckets={"bucket1": _bucket_row()})
                    }
                }
            },
        )

    def test_every_option_goes_through_the_argument_spec(self, pure1_client):
        """A list given as one string and a boolean given as text are converted"""
        _known_arrays(pure1_client, "array1", "array2")
        _answer(
            pure1_client,
            buckets=[_make_bucket(destroyed=True)],
            accounts=[_make_account(), _make_account("account2")],
        )

        module = run_main(
            pure1_buckets,
            array="ARRAY1,array2",
            name="bucket1,bucket2",
            account="account1",
            destroyed="yes",
        )

        assert [call[1] for call in pure1_client.get_arrays.call_args_list] == [
            {"limit": 0},
            {"filter": "name=('ARRAY1','array2')"},
        ]
        pure1_client.get_buckets.assert_called_once_with(
            filter="arrays.name=('array1','array2') and account.name='account1' "
            "and name=('bucket1','bucket2') and destroyed=true"
        )
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter="arrays.name=('array1','array2') and name='account1'"
        )
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_buckets={
                "accounts": {
                    "array1": {
                        "account1": _account_row(
                            buckets={"bucket1": _bucket_row(destroyed=True)}
                        )
                    }
                }
            },
        )

    def test_account_is_one_name_and_not_a_list(self, pure1_client):
        _answer(pure1_client)

        run_main(pure1_buckets, account="account1,account2")

        pure1_client.get_buckets.assert_called_once_with(
            filter="account.name='account1,account2'"
        )
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter="name='account1,account2'"
        )

    def test_account_given_as_a_list_is_the_text_of_the_list(self, pure1_client):
        """ansible-core makes one string of it, which names no account"""
        _answer(pure1_client)

        module = run_main(pure1_buckets, account=["account1"])

        pure1_client.get_buckets.assert_called_once_with(
            filter="account.name='[\\'account1\\']'"
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_buckets={"accounts": {}}
        )

    def test_the_options_are_those_of_the_documentation(self, pure1_client):
        _answer(pure1_client)

        module = run_main(pure1_buckets)

        options = yaml.safe_load(pure1_buckets.DOCUMENTATION)["options"]
        assert set(module.params) - set(pure1_argument_spec()) == set(options)
        assert {name: module.params[name] for name in options} == {
            "array": None,
            "name": None,
            "account": None,
            "destroyed": None,
        }

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param(
                {"destroyed": "perhaps"},
                "argument 'destroyed' is of type <class 'str'> and we were unable "
                "to convert to bool: The value 'perhaps' is not a valid boolean.",
                id="destroyed is no boolean",
            ),
            pytest.param(
                {"array": {"name": "array1"}},
                "argument 'array' is of type <class 'dict'> and we were unable to "
                "convert to list: <class 'dict'> cannot be converted to a list",
                id="array is a dictionary",
            ),
            pytest.param(
                {"filter": "name='bucket1'"},
                "Unsupported parameters for module: filter.",
                id="no filter option",
            ),
            pytest.param(
                {"limit": 5}, "Unsupported parameters for module: limit.", id="no limit"
            ),
            pytest.param(
                {"buckets": ["bucket1"]},
                "Unsupported parameters for module: buckets.",
                id="the buckets are named with name",
            ),
        ],
    )
    def test_refused_option_fails_before_any_request(
        self, pure1_client, params, message
    ):
        module = run_main(pure1_buckets, **params)

        module.fail_json.assert_called_once()
        assert module.fail_json.call_args[1]["msg"].startswith(message)
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_not_called()
        pure1_client.get_buckets.assert_not_called()

    def test_nothing_in_pure1_gives_an_empty_result(self, pure1_client):
        _answer(pure1_client)

        module = run_main(pure1_buckets)

        pure1_client.get_buckets.assert_called_once_with(filter=None)
        pure1_client.get_object_store_accounts.assert_called_once_with(filter=None)
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_buckets={"accounts": {}}
        )

    @pytest.mark.parametrize(
        "params, bucket_filter",
        [
            pytest.param({"name": ["bucket9"]}, "name='bucket9'", id="name"),
            pytest.param({"destroyed": True}, "destroyed=true", id="destroyed"),
        ],
    )
    def test_selection_that_matches_nothing_gives_an_empty_result(
        self, pure1_client, params, bucket_filter
    ):
        """With one request: no account is asked for"""
        pure1_client.get_buckets.return_value = valid_response([])

        module = run_main(pure1_buckets, **params)

        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_buckets.assert_called_once_with(filter=bucket_filter)
        pure1_client.get_object_store_accounts.assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_buckets={"accounts": {}}
        )

    def test_unknown_account_gives_an_empty_result(self, pure1_client):
        _answer(pure1_client)

        module = run_main(pure1_buckets, account="account9")

        pure1_client.get_buckets.assert_called_once_with(
            filter="account.name='account9'"
        )
        pure1_client.get_object_store_accounts.assert_called_once_with(
            filter="name='account9'"
        )
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_buckets={"accounts": {}}
        )

    def test_unknown_array_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [valid_response(), valid_response()]

        module = run_main(pure1_buckets, array=["array9"])

        module.fail_json.assert_called_once_with(
            msg="Array array9 is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        pure1_client.get_buckets.assert_not_called()
        pure1_client.get_object_store_accounts.assert_not_called()

    def test_warnings_reach_the_task(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[_make_bucket(), _make_bucket("bucket2", account=None)],
            accounts=[_make_account(), _without_arrays(_make_account("account2"))],
        )

        module = run_main(pure1_buckets)

        assert [call[0] for call in module.warn.call_args_list] == [
            (
                "Object store accounts that Pure1 returned without a name or "
                "without an array are left out of the result: 1",
            ),
            (
                "Buckets that Pure1 returned without a name, without an account "
                "or without an array are left out of the result: 1",
            ),
        ]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_buckets={
                "accounts": {
                    "array1": {
                        "account1": _account_row(buckets={"bucket1": _bucket_row()})
                    }
                }
            },
        )

    @pytest.mark.parametrize(
        "method, params",
        [
            pytest.param("get_buckets", {}, id="buckets"),
            pytest.param("get_buckets", {"destroyed": True}, id="selected buckets"),
            pytest.param("get_object_store_accounts", {}, id="accounts"),
            pytest.param(
                "get_object_store_accounts",
                {"name": ["bucket1"]},
                id="accounts of selected buckets",
            ),
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
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                " (HTTP 400): Illegal filter format: Unexpected input at character 16",
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
        _answer(pure1_client, [_make_bucket()], [_make_account()])
        if method == "get_arrays":
            # the first answer is for the check of get_pure1
            pure1_client.get_arrays.side_effect = [valid_response(), answer]
        elif isinstance(answer, Exception):
            getattr(pure1_client, method).side_effect = answer
        else:
            getattr(pure1_client, method).return_value = answer

        module = run_main(pure1_buckets, **params)

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
    paths = set(row)
    for name in row["buckets"].values():
        paths.update("buckets." + key for key in name)
    return paths


def _template(text, variables):
    """Return what a template of a task gives, in the type of its value

    As in a playbook, a key that a variable does not have is an error
    (jinja2.UndefinedError).
    """
    environment = NativeEnvironment(undefined=jinja2.StrictUndefined)
    environment.filters["dict2items"] = FilterModule().filters()["dict2items"]
    value = environment.from_string(text).render(**variables)
    if isinstance(value, jinja2.Undefined):
        # a template that is one expression hands a missing value on
        raise jinja2.UndefinedError(text)
    return value


def _condition(expression, variables):
    return bool(_template("{{ " + expression + " }}", variables))


def _play_examples(pure1_client, **buckets):
    """Run the tasks of EXAMPLES in their order and return what is read

    A task of the module runs through main() with the options of the example
    and registers its result. buckets gives, by the name the task registers,
    the buckets Pure1 has for it; the one account is account1 of array1. A
    task that reads a result gives a list with its templates rendered for
    each item of its loop, once when it has no loop.
    """
    registered = {}
    read = []
    for task in yaml.safe_load(pure1_buckets.EXAMPLES):
        options = task.get("everpure.pure1.pure1_buckets")
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            _known_arrays(pure1_client, *options.get("array", []))
            _answer(pure1_client, buckets[task["register"]], [_make_account()])
            module = run_main(pure1_buckets, **options)
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
        _answer(pure1_client, [_make_bucket()], [_make_account()])
        module = run_main(pure1_buckets)
        return module.exit_json.call_args[1]

    def test_return_names_the_one_key_of_the_result(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_buckets.RETURN)
        assert set(result) - {"changed"} == set(documented) == {"pure1_buckets"}
        assert set(result["pure1_buckets"]) == set(
            documented["pure1_buckets"]["contains"]
        )

    def test_return_lists_the_keys_of_an_account_and_a_bucket(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_buckets.RETURN)["pure1_buckets"]["contains"]
        row = result["pure1_buckets"]["accounts"]["array1"]["account1"]
        assert _contains(documented["accounts"]["contains"]) == _paths(row)

    def test_every_key_of_return_is_an_identifier(self):
        """validate-modules of ansible-core 2.21 refuses any other key"""
        documented = yaml.safe_load(pure1_buckets.RETURN)

        for path in _contains(documented):
            assert all(key.isidentifier() for key in path.split(".")), path

    def test_sample_has_the_shape_of_a_result(self, pure1_client):
        result = self._result(pure1_client)

        sample = yaml.safe_load(pure1_buckets.RETURN)["pure1_buckets"]["sample"]
        row = result["pure1_buckets"]["accounts"]["array1"]["account1"]
        assert list(sample) == ["accounts"]
        assert list(sample["accounts"]) == ["array1"]
        account = sample["accounts"]["array1"]["account1"]
        assert _paths(account) == _paths(row)
        bucket = account["buckets"]["bucket1"]
        for item, like in ((account, row), (bucket, row["buckets"]["bucket1"])):
            assert {key: type(value) for key, value in item.items()} == {
                key: type(value) for key, value in like.items()
            }
            assert item["created"] == pure1_time(item["created_epoch"])
            assert item["as_of"] == pure1_time(item["as_of_epoch"])

    def test_return_gives_the_type_of_every_key(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_buckets.RETURN)["pure1_buckets"]
        accounts = documented["contains"]["accounts"]
        account = result["pure1_buckets"]["accounts"]["array1"]["account1"]
        types = {"bool": bool, "dict": dict, "int": int, "str": str}
        assert types[documented["type"]] is type(result["pure1_buckets"])
        assert types[accounts["type"]] is type(result["pure1_buckets"]["accounts"])
        for keys, row in (
            (accounts["contains"], account),
            (
                accounts["contains"]["buckets"]["contains"],
                account["buckets"]["bucket1"],
            ),
        ):
            assert {key: types[entry["type"]] for key, entry in keys.items()} == {
                key: type(value) for key, value in row.items()
            }

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "All arrays when not set or empty.",
                id="an empty list of arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "All buckets when not set or empty.",
                id="an empty list of names",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.account",
                "All accounts when not set or empty.",
                id="an empty account",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.account",
                "This is a single name, not a list of names.",
                id="account is one name",
            ),
            pytest.param(
                "RETURN",
                "pure1_buckets.contains.accounts",
                "has only its C(id) and its C(buckets) set, its other keys are null.",
                id="account that only a bucket names",
            ),
            pytest.param(
                "RETURN",
                "pure1_buckets.contains.accounts",
                "the one created last is returned and the module gives a warning.",
                id="two objects of one name",
            ),
            pytest.param(
                "RETURN",
                "pure1_buckets.contains.accounts.contains.object_count",
                "It can differ from the sum of the C(object_count) of the buckets",
                id="object count of an account",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_buckets, block))
        for key in path.split("."):
            entry = entry[key]

        assert says in " ".join(entry["description"])

    def test_examples_use_every_option_of_the_module(self):
        options = set(yaml.safe_load(pure1_buckets.DOCUMENTATION)["options"])
        used = set()
        for task in yaml.safe_load(pure1_buckets.EXAMPLES):
            arguments = task.get("everpure.pure1.pure1_buckets")
            if arguments:
                used.update(set(arguments) - {"app_id", "key_file"})
        assert used == options

    def test_examples_read_what_the_module_returns(self, pure1_client):
        """The tasks after a task of the module, on the result it registered"""
        buckets = [_make_bucket(), _make_bucket("bucket2")]

        read = _play_examples(
            pure1_client,
            object_store=buckets,
            account_buckets=buckets,
            destroyed_buckets=[],
            named_buckets=buckets,
        )

        account = _account_row(
            buckets={"bucket1": _bucket_row(), "bucket2": _bucket_row("bucket2")}
        )
        assert read == [
            [{"msg": {"array1": {"account1": account}}}],
            [{"when": True, "msg": ["bucket1", "bucket2"]}],
            [{"that": [True], "fail_msg": "Some arrays hold destroyed buckets"}],
            [
                {
                    "that": [True],
                    "fail_msg": "Account account1 has a bucket without versioning",
                }
            ],
        ]

    def test_checks_of_the_examples_fail_on_what_they_look_for(self, pure1_client):
        buckets = [_make_bucket(), _make_bucket("bucket2")]

        read = _play_examples(
            pure1_client,
            object_store=buckets,
            account_buckets=buckets,
            destroyed_buckets=[_make_bucket(destroyed=True)],
            named_buckets=[_make_bucket(), _make_bucket("bucket2", versioning="none")],
        )

        assert [made["that"] for made in read[2] + read[3]] == [[False], [False]]

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param("{{ result['pure1_buckets']['account'] }}", id="key"),
            pytest.param("{{ result.pure1_buckets.account | length }}", id="filter"),
            pytest.param(
                "{{ result['pure1_buckets']['accounts']['array1'].values() "
                "| selectattr('bucket', 'equalto', {}) | list }}",
                id="attribute of an item",
            ),
        ],
    )
    def test_example_that_reads_a_key_the_module_does_not_return_fails(
        self, pure1_client, template
    ):
        result = self._result(pure1_client)

        with pytest.raises(jinja2.UndefinedError):
            _template(template, {"result": result})
