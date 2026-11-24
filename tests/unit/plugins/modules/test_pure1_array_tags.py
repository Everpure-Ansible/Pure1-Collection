# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_array_tags module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import inspect
import pytest
from unittest.mock import Mock, call, patch

from pypureclient import ApiError, ErrorResponse, PureError, ValidResponse

from plugins.modules import pure1_array_tags
from plugins.modules.pure1_array_tags import create_tag, delete_tag, update_tag
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _tag(key, value):
    """A tag of the array foo, as get_arrays_tags lists it."""
    return sdk_model(
        "Tag",
        key=key,
        value=value,
        resource=sdk_model("FixedReference", name="foo"),
    )


def _known(pure1_client, *names):
    """Answer the check of get_pure1, then the lookup of the array by its name.

    The lookup finds one array for each name, the array foo when none is given.
    """
    pure1_client.get_arrays.side_effect = [
        valid_response(),
        valid_response([sdk_model("Array", name=name) for name in names or ("foo",)]),
    ]


def _ok():
    """The answer to a put that worked; the module reads only its status."""
    return valid_response()


def _deleted():
    """The answer to a delete that worked: the SDK gives a status and no items."""
    return ValidResponse(200, None, None, None, {})


def _err(message="boom"):
    return error_response(400, message)


def _err_answer(errors):
    # The SDK error answer: a status and an errors list, no items
    return ErrorResponse(400, errors, {})


def _old_sdk_put(recorder):
    """put_arrays_tags with the py-pure-client 1.62.0 signature."""

    def put_arrays_tags(
        resources=None,
        tag=None,
        authorization=None,
        x_request_id=None,
        namespaces=None,
        resource_ids=None,
        resource_names=None,
        async_req=False,
        _return_http_data_only=False,
        _preload_content=True,
        _request_timeout=None,
    ):
        return recorder(tag=tag, resource_names=resource_names)

    return put_arrays_tags


class _Pure1:
    """Pure1 with one array and its tags, as far as the module uses it.

    It answers as Pure1 did on a lab organisation. A filter finds the array
    whatever the case of the name, and resource_names takes the name only as
    Pure1 spells it. A key matches whatever its case: a put replaces the tag
    and its key takes the spelling of the put. A delete of a key that is not
    there is no error. A value is stored whole, and an empty one is refused.
    """

    def __init__(self, client, name="Array1", tags=()):
        self.name = name
        self.tags = [list(tag) for tag in tags]
        client.get_arrays.side_effect = self._get_arrays
        client.get_arrays_tags.side_effect = self._get_tags
        client.put_arrays_tags.side_effect = self._put_tags
        client.delete_arrays_tags.side_effect = self._delete_tags

    def _get_arrays(self, **keywords):
        if "filter" not in keywords:
            # the check of get_pure1
            return valid_response()
        if keywords["filter"].lower() != "name='{0}'".format(self.name.lower()):
            return valid_response()
        return valid_response([sdk_model("Array", name=self.name)])

    def _no_array(self, resource_names):
        if resource_names != [self.name]:
            return error_response(
                400, "Invalid value", "No arrays with name: " + resource_names[0]
            )
        return None

    def _get_tags(self, resource_names):
        resource = sdk_model("FixedReference", name=self.name)
        return self._no_array(resource_names) or valid_response(
            [
                sdk_model("Tag", key=key, value=value, resource=resource)
                for key, value in self.tags
            ]
        )

    def _put_tags(self, resource_names, tag_put):
        refused = self._no_array(resource_names)
        if refused is None and not all(tag["value"] for tag in tag_put):
            refused = error_response(500, "Internal server error")
        if refused is None:
            for tag in tag_put:
                self._remove(tag["key"])
                self.tags.append([tag["key"], tag["value"]])
        return refused or valid_response()

    def _delete_tags(self, resource_names, keys):
        refused = self._no_array(resource_names)
        if refused is None:
            for key in keys:
                self._remove(key)
        return refused or _deleted()

    def _remove(self, key):
        self.tags = [tag for tag in self.tags if tag[0].lower() != key.lower()]


# Error lists an error answer can carry, and the text the module reports
ERROR_ANSWERS = [
    ([], "Bad Request"),
    ([ApiError(context=None, message=None)], "Bad Request"),
    ([ApiError(context=None, message="Invalid value")], "Invalid value"),
    (
        [ApiError(context="No arrays with name: foo", message="Invalid value")],
        "Invalid value: No arrays with name: foo",
    ),
]


class TestCreateTag:
    """Tests for create_tag."""

    def test_check_mode_makes_no_api_call(self, pure1_client):
        module = ansible_module(check_mode=True, name="foo", tag=["key1:value1"])

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_creates_tag_with_parsed_key_value(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_all_tags_are_sent_in_one_call(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1", "key2:value2"])
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value1"},
                {"key": "key2", "value": "value2"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_repeated_key_is_sent_once_with_the_last_value(self, pure1_client):
        module = ansible_module(
            name="foo", tag=["key1:value1", "key2:value2", "key1:value3"]
        )
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value3"},
                {"key": "key2", "value": "value2"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_empty_tag_list_makes_no_api_call(self, pure1_client):
        module = ansible_module(name="foo", tag=[])

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_value_keeps_its_colons(self, pure1_client):
        module = ansible_module(name="foo", tag=["url:http://x:80/y", "key2:a:"])
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "url", "value": "http://x:80/y"},
                {"key": "key2", "value": "a:"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_tags_go_to_the_array_as_pure1_spells_it(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1:value1"])
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client, "Foo")

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_raised_error_calls_fail_json(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1:value1", "key2:value2"])
        pure1_client.put_arrays_tags.side_effect = PureError("connection reset")

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client, "Foo")

        module.fail_json.assert_called_once_with(
            msg="Failed to add tag KVP key1:value1, key2:value2 to array Foo. "
            "Error: PureError: connection reset"
        )
        module.exit_json.assert_not_called()

    def test_api_failure_calls_fail_json(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        pure1_client.put_arrays_tags.return_value = _err()

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        module.fail_json.assert_called_once()
        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to add tag KVP key1:value1 to array foo. Error: boom"
        )
        module.exit_json.assert_not_called()


class TestUpdateTag:
    """Tests for update_tag."""

    def test_check_mode_makes_no_api_call(self, pure1_client):
        module = ansible_module(check_mode=True, name="foo", tag=["key1:value2"])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_check_mode_reports_a_new_tag(self, pure1_client):
        module = ansible_module(check_mode=True, name="foo", tag=["key2:value2"])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    @pytest.mark.parametrize("tags", [["key1:value1"], ["KEY1:value1"], []])
    def test_check_mode_reports_no_change_when_nothing_differs(
        self, pure1_client, tags
    ):
        module = ansible_module(check_mode=True, name="foo", tag=tags)
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_empty_tag_list_makes_no_api_call(self, pure1_client):
        module = ansible_module(name="foo", tag=[])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_existing_tag_same_value_no_change(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_existing_tag_changed_value_updates(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value2"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_new_tag_is_added(self, pure1_client):
        module = ansible_module(name="foo", tag=["key2:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key2", "value": "value2"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_only_changed_and_new_tags_are_sent_in_one_call(self, pure1_client):
        module = ansible_module(
            name="foo", tag=["key1:value1", "key2:new", "key3:value3"]
        )
        current = [_tag("key1", "value1"), _tag("key2", "old")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key2", "value": "new"},
                {"key": "key3", "value": "value3"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_repeated_key_is_sent_once_with_the_last_value(self, pure1_client):
        module = ansible_module(
            name="foo",
            tag=["key1:value2", "key2:value2", "key1:value3", "key2:value3"],
        )
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value3"},
                {"key": "key2", "value": "value3"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_key_read_twice_is_sent_once(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:new"])
        current = [_tag("key1", "old"), _tag("key1", "older")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "new"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    @pytest.mark.parametrize("values", [("new", "old"), ("old", "new")])
    def test_key_read_twice_with_one_other_value_is_sent(self, pure1_client, values):
        module = ansible_module(name="foo", tag=["key1:new"])
        current = [_tag("key1", value) for value in values]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "new"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_repeated_key_with_the_last_value_stored_changes_nothing(
        self, pure1_client
    ):
        module = ansible_module(name="foo", tag=["key1:value1", "key1:value3"])
        current = [_tag("key1", "value3")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_value_is_compared_with_its_colons(self, pure1_client):
        module = ansible_module(name="foo", tag=["url:http://x:80/y"])
        current = [_tag("url", "http://x:80/y")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_value_stored_cut_is_written_whole(self, pure1_client):
        module = ansible_module(name="foo", tag=["url:http://x:80/y"])
        current = [_tag("url", "http")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "url", "value": "http://x:80/y"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_key_in_another_case_with_the_same_value_no_change(self, pure1_client):
        module = ansible_module(name="foo", tag=["owner:X", "KEY2:value2"])
        current = [_tag("Owner", "X"), _tag("key2", "value2")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_key_in_another_case_is_updated_as_the_task_spells_it(self, pure1_client):
        module = ansible_module(name="foo", tag=["owner:Y", "key2:value2"])
        current = [_tag("Owner", "X"), _tag("key2", "value2")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "owner", "value": "Y"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_stored_tag_without_key_or_value(self, pure1_client):
        # Pure1 can leave any field of a tag out
        module = ansible_module(name="foo", tag=["key1:value1", "key2:value2"])
        current = [sdk_model("Tag", value="value1"), sdk_model("Tag", key="key2")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value1"},
                {"key": "key2", "value": "value2"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_tags_go_to_the_array_as_pure1_spells_it(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _ok()

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current, "Foo")

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], tag_put=[{"key": "key1", "value": "value2"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_raised_error_calls_fail_json(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1:value2", "key2:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.side_effect = TimeoutError("timed out")

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current, "Foo")

        module.fail_json.assert_called_once_with(
            msg="Failed to update tags 'key1, key2' on array Foo. "
            "Error: TimeoutError: timed out"
        )
        module.exit_json.assert_not_called()


class TestDeleteTag:
    """Tests for delete_tag."""

    def test_matching_tag_is_deleted(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        current = [_tag("key1", "value1")]
        pure1_client.delete_arrays_tags.return_value = _deleted()

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_each_matching_key_is_deleted_in_its_own_call(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1", "key2:value2"])
        current = [_tag("key1", "value1"), _tag("key2", "value2")]
        pure1_client.delete_arrays_tags.side_effect = [_deleted(), _deleted()]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        assert pure1_client.delete_arrays_tags.call_args_list == [
            call(resource_names=["foo"], keys=["key1"]),
            call(resource_names=["foo"], keys=["key2"]),
        ]
        module.exit_json.assert_called_once_with(changed=True)

    def test_non_matching_tag_is_not_deleted(self, pure1_client):
        module = ansible_module(name="foo", tag=["key2:value2"])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_check_mode_makes_no_api_call(self, pure1_client):
        module = ansible_module(check_mode=True, name="foo", tag=["key1:value1"])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    @pytest.mark.parametrize("tags", [["key2:value2"], ["key2"], []])
    def test_check_mode_reports_no_change_when_no_key_matches(self, pure1_client, tags):
        module = ansible_module(check_mode=True, name="foo", tag=tags)
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    @pytest.mark.parametrize("tag", ["key1", "key1:", "key1:other:value", "KEY1"])
    def test_only_the_key_before_the_first_colon_counts(self, pure1_client, tag):
        module = ansible_module(name="foo", tag=[tag])
        current = [_tag("key1", "value1"), _tag("key2", "value2")]
        pure1_client.delete_arrays_tags.return_value = _deleted()

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    @pytest.mark.parametrize("tag", ["", ":", ":value1"])
    def test_tag_without_key_deletes_nothing(self, pure1_client, tag):
        module = ansible_module(name="foo", tag=[tag])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_key_in_another_case_is_deleted_as_pure1_spells_it(self, pure1_client):
        module = ansible_module(name="foo", tag=["owner:X", "KEY2"])
        current = [_tag("Owner", "X"), _tag("Key2", "value2"), _tag("key3", "c")]
        pure1_client.delete_arrays_tags.side_effect = [_deleted(), _deleted()]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        assert pure1_client.delete_arrays_tags.call_args_list == [
            call(resource_names=["foo"], keys=["Owner"]),
            call(resource_names=["foo"], keys=["Key2"]),
        ]
        module.exit_json.assert_called_once_with(changed=True)

    def test_stored_tag_without_key_is_not_deleted(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1", "none"])
        current = [sdk_model("Tag", value="value1")]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        pure1_client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_tags_are_deleted_from_the_array_as_pure1_spells_it(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1"])
        current = [_tag("key1", "value1")]
        pure1_client.delete_arrays_tags.return_value = _deleted()

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current, "Foo")

        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_raised_error_calls_fail_json(self, pure1_client):
        module = ansible_module(name="FOO", tag=["key1:value1", "key2"])
        current = [_tag("key1", "value1"), _tag("Key2", "value2")]
        pure1_client.delete_arrays_tags.side_effect = [
            _deleted(),
            PureError("connection reset"),
        ]

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current, "Foo")

        assert pure1_client.delete_arrays_tags.call_count == 2
        module.fail_json.assert_called_once_with(
            msg="Failed to remove tag KVP 'key2' from array Foo. "
            "Error: PureError: connection reset"
        )
        module.exit_json.assert_not_called()


class TestTagKeyword:
    """The tags go under the keyword the SDK's put_arrays_tags takes."""

    def test_create_is_accepted_by_the_sdk_signature(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        pure1_client.put_arrays_tags.return_value = _ok()
        # The stand-in checks each call against the signature of the SDK
        keywords = inspect.signature(pure1_client.put_arrays_tags).parameters
        assert "tag_put" in keywords and "tag" not in keywords

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value1"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_update_is_accepted_by_the_sdk_signature(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _ok()
        keywords = inspect.signature(pure1_client.put_arrays_tags).parameters
        assert "tag_put" in keywords and "tag" not in keywords

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value2"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_old_sdk_signature_gets_the_tag_keyword(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        recorder = Mock(return_value=_ok())
        pure1_client.put_arrays_tags = _old_sdk_put(recorder)

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        recorder.assert_called_once_with(
            tag=[{"key": "key1", "value": "value1"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_signature_with_both_keywords_gets_tag_put(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        recorder = Mock(return_value=_ok())

        def put_arrays_tags(tag_put=None, tag=None, resource_names=None):
            return recorder(tag_put=tag_put, tag=tag, resource_names=resource_names)

        pure1_client.put_arrays_tags = put_arrays_tags

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        recorder.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value1"}],
            tag=None,
            resource_names=["foo"],
        )

    def test_uninspectable_method_gets_tag_put(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        # Before the patch: a stand-in method first used under it checks nothing
        pure1_client.put_arrays_tags.return_value = _ok()

        with patch("inspect.signature", side_effect=ValueError("no signature")):
            with pytest.raises(SystemExit):
                create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )

    def test_type_error_inside_the_sdk_call_is_not_retried(self, pure1_client):
        module = ansible_module(name="foo", tag=["key1:value1"])
        pure1_client.put_arrays_tags.side_effect = TypeError("raised inside the SDK")

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once()
        module.fail_json.assert_called_once_with(
            msg="Failed to add tag KVP key1:value1 to array foo. "
            "Error: TypeError: raised inside the SDK"
        )


@pytest.mark.parametrize("errors, text", ERROR_ANSWERS)
class TestErrorAnswers:
    """An error answer becomes a failure message, whatever its errors list holds."""

    def test_create(self, pure1_client, errors, text):
        module = ansible_module(name="foo", tag=["key1:value1", "key2:value2"])
        pure1_client.put_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        module.fail_json.assert_called_once_with(
            msg="Failed to add tag KVP key1:value1, key2:value2 to array foo. "
            "Error: " + text
        )

    def test_update(self, pure1_client, errors, text):
        module = ansible_module(name="foo", tag=["key1:value2", "key2:value2"])
        current = [_tag("key1", "value1")]
        pure1_client.put_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        module.fail_json.assert_called_once_with(
            msg="Failed to update tags 'key1, key2' on array foo. Error: " + text
        )

    def test_delete(self, pure1_client, errors, text):
        module = ansible_module(name="foo", tag=["key1:value1"])
        current = [_tag("key1", "value1")]
        pure1_client.delete_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            delete_tag(module, pure1_client, current)

        module.fail_json.assert_called_once_with(
            msg="Failed to remove tag KVP 'key1:value1' from array foo. "
            "Error: " + text
        )

    def test_tag_read(self, pure1_client, errors, text):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = _err_answer(errors)

        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="present"
        )

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["foo"])
        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Failed to get tags of array foo. Error: " + text
        )


class TestMain:
    """Tests for the pure1_array_tags main() flow."""

    def test_present_without_tags_creates(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response([])
        pure1_client.put_arrays_tags.return_value = _ok()

        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="present"
        )

        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_absent_with_matching_tag_deletes(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )
        pure1_client.delete_arrays_tags.return_value = _deleted()

        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="absent"
        )

        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_present_with_tags_updates(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1"), _tag("key2", "old")]
        )
        pure1_client.put_arrays_tags.return_value = _ok()

        # state is left to the argument spec
        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1", "key2:new"])

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["foo"])
        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key2", "value": "new"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_present_with_the_same_tags_changes_nothing(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )

        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1"])

        # the check of get_pure1, then the one lookup of the array
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='foo'"),
        ]
        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_absent_without_tags_changes_nothing(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response([])

        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="absent"
        )

        pure1_client.delete_arrays_tags.assert_not_called()
        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_check_mode_reads_the_tags_and_writes_nothing(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response([])

        module = run_main(
            pure1_array_tags, check_mode=True, name="foo", tag=["key1:value1"]
        )

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["foo"])
        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_name_and_tag_are_required(self):
        module = run_main(pure1_array_tags)

        module.fail_json.assert_called_once_with(
            msg="missing required arguments: name, tag"
        )

    def test_state_is_absent_or_present(self):
        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="gone"
        )

        module.fail_json.assert_called_once_with(
            msg="value of state must be one of: absent, present, got: gone"
        )

    def test_check_mode_with_tags_reports_the_update(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1"), _tag("key2", "old")]
        )

        module = run_main(
            pure1_array_tags,
            check_mode=True,
            name="foo",
            tag=["key1:value1", "key2:new"],
        )

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_check_mode_with_matching_tag_reports_the_delete(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )

        module = run_main(
            pure1_array_tags,
            check_mode=True,
            name="foo",
            tag=["key1"],
            state="absent",
        )

        pure1_client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    @pytest.mark.parametrize("stored", [[], [("key1", "value1")]])
    def test_present_with_an_empty_list_changes_nothing(self, pure1_client, stored):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag(key, value) for key, value in stored]
        )

        module = run_main(pure1_array_tags, name="foo", tag=[])

        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)


class TestArrayLookup:
    """main() finds the array by its name and uses the name Pure1 gives."""

    def test_name_goes_into_the_filter_as_a_quoted_string(self, pure1_client):
        _known(pure1_client, "fo'o\\")
        pure1_client.get_arrays_tags.return_value = valid_response([])

        module = run_main(pure1_array_tags, name="fo'o\\", tag=[])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='fo\\'o\\\\'"),
        ]
        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["fo'o\\"])
        module.exit_json.assert_called_once_with(changed=False)

    def test_present_uses_the_name_as_pure1_spells_it(self, pure1_client):
        _known(pure1_client, "Foo")
        pure1_client.get_arrays_tags.return_value = valid_response([])
        pure1_client.put_arrays_tags.return_value = _ok()

        module = run_main(pure1_array_tags, name="FOO", tag=["key1:value1"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='FOO'"),
        ]
        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["Foo"])
        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_update_uses_the_name_as_pure1_spells_it(self, pure1_client):
        _known(pure1_client, "Foo")
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "old")]
        )
        pure1_client.put_arrays_tags.return_value = _ok()

        module = run_main(pure1_array_tags, name="FOO", tag=["key1:new"])

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["Foo"])
        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], tag_put=[{"key": "key1", "value": "new"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_absent_uses_the_name_as_pure1_spells_it(self, pure1_client):
        _known(pure1_client, "Foo")
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )
        pure1_client.delete_arrays_tags.return_value = _deleted()

        module = run_main(pure1_array_tags, name="FOO", tag=["key1"], state="absent")

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["Foo"])
        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_name_as_typed_is_kept_when_pure1_holds_it(self, pure1_client):
        # the filter finds every spelling, the one of the task is among them
        _known(pure1_client, "FOO", "foo")
        pure1_client.get_arrays_tags.return_value = valid_response([])

        run_main(pure1_array_tags, name="foo", tag=["key1:value1"], state="absent")

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["foo"])

    def test_only_the_name_of_the_task_in_another_case_is_taken(self, pure1_client):
        # whatever else the lookup answers, the tags of another array stay
        _known(pure1_client, "foo-2", "Foo", "other-array")
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )
        pure1_client.delete_arrays_tags.return_value = _deleted()

        module = run_main(pure1_array_tags, name="FOO", tag=["key1"], state="absent")

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["Foo"])
        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["Foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_one_spelling_answered_twice_is_taken(self, pure1_client):
        _known(pure1_client, "Foo", "Foo")
        pure1_client.get_arrays_tags.return_value = valid_response([])

        module = run_main(pure1_array_tags, name="FOO", tag=["key1"], state="absent")

        pure1_client.get_arrays_tags.assert_called_once_with(resource_names=["Foo"])
        module.exit_json.assert_called_once_with(changed=False)

    @pytest.mark.parametrize("check_mode", [False, True])
    @pytest.mark.parametrize("state", ["present", "absent"])
    @pytest.mark.parametrize(
        "name, answered",
        [
            ("foo", ["other-array"]),
            ("ar*", ["array1", "array2"]),
            ("foo", ["foo-2", "xfoo"]),
        ],
    )
    def test_array_of_another_name_is_not_known(
        self, pure1_client, name, answered, state, check_mode
    ):
        # an answer that is not the name of the task in any case is no array of it
        _known(pure1_client, *answered)

        module = run_main(
            pure1_array_tags,
            check_mode=check_mode,
            name=name,
            tag=["key1:value1"],
            state=state,
        )

        pure1_client.get_arrays_tags.assert_not_called()
        pure1_client.put_arrays_tags.assert_not_called()
        pure1_client.delete_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Array {0} is not known to Pure1.".format(name)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("check_mode", [False, True])
    @pytest.mark.parametrize("state", ["present", "absent"])
    def test_two_arrays_in_other_cases_fail(self, pure1_client, state, check_mode):
        # the task spells the name a third way: neither array is the one it means
        _known(pure1_client, "Foo", "FOO", "Foo")

        module = run_main(
            pure1_array_tags,
            check_mode=check_mode,
            name="foo",
            tag=["key1:value1"],
            state=state,
        )

        pure1_client.get_arrays_tags.assert_not_called()
        pure1_client.put_arrays_tags.assert_not_called()
        pure1_client.delete_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 knows more than one array whose name is foo in another "
            "case: FOO, Foo. Give the name as Pure1 spells it."
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("check_mode", [False, True])
    @pytest.mark.parametrize("state", ["present", "absent"])
    def test_unknown_array_fails(self, pure1_client, state, check_mode):
        module = run_main(
            pure1_array_tags,
            check_mode=check_mode,
            name="foo",
            tag=["key1:value1"],
            state=state,
        )

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='foo'"),
        ]
        pure1_client.get_arrays_tags.assert_not_called()
        pure1_client.put_arrays_tags.assert_not_called()
        pure1_client.delete_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")
        module.exit_json.assert_not_called()

    def test_array_without_a_name_is_not_known(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response([sdk_model("Array", model="FA-X20R4")]),
        ]

        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1"])

        pure1_client.get_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")

    def test_error_answer_to_the_lookup_fails(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(400, "Illegal filter format", "Unexpected input"),
        ]

        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1"])

        pure1_client.get_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 400): "
            "Illegal filter format: Unexpected input"
        )

    def test_raised_error_in_the_lookup_fails(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            PureError("connection reset"),
        ]

        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1"])

        pure1_client.get_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed: PureError: connection reset"
        )

    def test_raised_error_in_the_tag_read_fails(self, pure1_client):
        _known(pure1_client, "Foo")
        pure1_client.get_arrays_tags.side_effect = TimeoutError("timed out")

        module = run_main(pure1_array_tags, name="FOO", tag=["key1:value1"])

        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Failed to get tags of array Foo. Error: TimeoutError: timed out"
        )
        module.exit_json.assert_not_called()

    def test_failed_put_names_the_array_as_pure1_spells_it(self, pure1_client):
        _known(pure1_client, "Foo")
        pure1_client.get_arrays_tags.return_value = valid_response([])
        pure1_client.put_arrays_tags.return_value = _err()

        module = run_main(pure1_array_tags, name="FOO", tag=["key1:value1"])

        module.fail_json.assert_called_once_with(
            msg="Failed to add tag KVP key1:value1 to array Foo. Error: boom"
        )


# A tag that state present refuses, whatever else the task asks for
NOT_KEY_VALUE = ["key1", "key1:", ":value1", ":", ""]


class TestTagForm:
    """state present takes key:value only, and one spelling of a key."""

    @pytest.mark.parametrize("tag", NOT_KEY_VALUE)
    def test_create_refuses_a_tag_without_key_or_value(self, pure1_client, tag):
        module = ansible_module(name="foo", tag=["key2:value2", tag])

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag '{0}' is not valid: with state present a tag has the form "
            "key:value, and neither the key nor the value can be empty.".format(tag)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("tag", NOT_KEY_VALUE)
    def test_update_refuses_a_tag_without_key_or_value(self, pure1_client, tag):
        module = ansible_module(name="foo", tag=["key2:value2", tag])
        current = [_tag("key1", "value1")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag '{0}' is not valid: with state present a tag has the form "
            "key:value, and neither the key nor the value can be empty.".format(tag)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("check_mode", [False, True])
    @pytest.mark.parametrize("tag", NOT_KEY_VALUE)
    def test_main_refuses_it_before_pure1_is_asked(
        self, client_factory, tag, check_mode
    ):
        # no pure1_client: creating the client would fail the test
        module = run_main(
            pure1_array_tags, check_mode=check_mode, name="foo", tag=["key2:b", tag]
        )

        client_factory.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag '{0}' is not valid: with state present a tag has the form "
            "key:value, and neither the key nor the value can be empty.".format(tag)
        )
        module.exit_json.assert_not_called()

    @pytest.mark.parametrize("tag", NOT_KEY_VALUE)
    def test_main_takes_it_for_state_absent(self, pure1_client, tag):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key2", "value2")]
        )

        module = run_main(pure1_array_tags, name="foo", tag=[tag], state="absent")

        pure1_client.delete_arrays_tags.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_create_refuses_two_keys_that_differ_only_in_case(self, pure1_client):
        module = ansible_module(name="foo", tag=["Owner:a", "key2:b", "owner:c"])

        with pytest.raises(SystemExit):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag keys 'Owner' and 'owner' differ only in case, and Pure1 "
            "holds them as one key. Give the key once."
        )
        module.exit_json.assert_not_called()

    def test_update_refuses_two_keys_that_differ_only_in_case(self, pure1_client):
        module = ansible_module(name="foo", tag=["Owner:a", "owner:a"])
        current = [_tag("owner", "a")]

        with pytest.raises(SystemExit):
            update_tag(module, pure1_client, current)

        pure1_client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag keys 'Owner' and 'owner' differ only in case, and Pure1 "
            "holds them as one key. Give the key once."
        )
        module.exit_json.assert_not_called()

    def test_main_refuses_them_before_pure1_is_asked(self, client_factory):
        module = run_main(pure1_array_tags, name="foo", tag=["owner:a", "OWNER:b"])

        client_factory.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Tag keys 'owner' and 'OWNER' differ only in case, and Pure1 "
            "holds them as one key. Give the key once."
        )

    def test_main_takes_them_for_state_absent(self, pure1_client):
        _known(pure1_client)
        pure1_client.get_arrays_tags.return_value = valid_response([_tag("Owner", "a")])
        pure1_client.delete_arrays_tags.side_effect = [_deleted(), _deleted()]

        module = run_main(
            pure1_array_tags, name="foo", tag=["owner", "OWNER:b"], state="absent"
        )

        # as for a key the task gives twice: one call for each tag of the task
        assert pure1_client.delete_arrays_tags.call_args_list == [
            call(resource_names=["foo"], keys=["Owner"]),
            call(resource_names=["foo"], keys=["Owner"]),
        ]
        module.exit_json.assert_called_once_with(changed=True)


# What Pure1 holds, the task, and what Pure1 holds after the task
CHANGES = [
    pytest.param(
        [],
        dict(tag=["key1:value1", "key2:value2"]),
        [["key1", "value1"], ["key2", "value2"]],
        id="create",
    ),
    pytest.param(
        [("key1", "old"), ("key2", "value2")],
        dict(tag=["key1:new", "key2:value2"]),
        [["key2", "value2"], ["key1", "new"]],
        id="update",
    ),
    pytest.param(
        [("key1", "value1")],
        dict(tag=["key2:value2"]),
        [["key1", "value1"], ["key2", "value2"]],
        id="add",
    ),
    pytest.param(
        [("Owner", "a")],
        dict(tag=["owner:b"]),
        [["owner", "b"]],
        id="update a key in another case",
    ),
    pytest.param(
        [],
        dict(tag=["url:http://x:80/y"]),
        [["url", "http://x:80/y"]],
        id="create a value with colons",
    ),
    pytest.param(
        [("url", "http")],
        dict(tag=["url:http://x:80/y"]),
        [["url", "http://x:80/y"]],
        id="update a value that was stored cut",
    ),
    pytest.param(
        [("key1", "value1")],
        dict(tag=["key1:value1", "key1:value3"]),
        [["key1", "value3"]],
        id="key given twice",
    ),
    pytest.param(
        [],
        dict(name="ARRAY1", tag=["key1:value1"]),
        [["key1", "value1"]],
        id="create with the array name in another case",
    ),
    pytest.param(
        [("key1", "old")],
        dict(name="array1", tag=["key1:new"]),
        [["key1", "new"]],
        id="update with the array name in another case",
    ),
    pytest.param(
        [("key1", "value1"), ("key2", "value2")],
        dict(tag=["key1"], state="absent"),
        [["key2", "value2"]],
        id="absent",
    ),
    pytest.param(
        [("key1", "value1"), ("key2", "value2")],
        dict(tag=["key1:other", "key2:value2"], state="absent"),
        [],
        id="absent with values",
    ),
    pytest.param(
        [("Owner", "a"), ("key2", "value2")],
        dict(tag=["owner"], state="absent"),
        [["key2", "value2"]],
        id="absent with the key in another case",
    ),
    pytest.param(
        [("key1", "value1")],
        dict(name="ARRAY1", tag=["key1"], state="absent"),
        [],
        id="absent with the array name in another case",
    ),
]

# What Pure1 holds and a task that finds it as it should be
NO_CHANGES = [
    pytest.param([("key1", "value1")], dict(tag=["key1:value1"]), id="same tag"),
    pytest.param(
        [("Owner", "a")], dict(tag=["owner:a"]), id="same tag in another case"
    ),
    pytest.param(
        [("key1", "value3")],
        dict(tag=["key1:value1", "key1:value3"]),
        id="key given twice",
    ),
    pytest.param([], dict(tag=[]), id="no tag for an array without tags"),
    pytest.param(
        [("key1", "value1")], dict(tag=[]), id="no tag for an array with tags"
    ),
    pytest.param([], dict(tag=["key1"], state="absent"), id="absent without tags"),
    pytest.param(
        [("key1", "value1")],
        dict(tag=["key2", "key3:value1"], state="absent"),
        id="absent with other tags",
    ),
    pytest.param(
        [("key1", "value1")],
        dict(tag=[], state="absent"),
        id="absent with no tag",
    ),
]


class TestRuns:
    """Whole tasks against a Pure1 that keeps the tags from one run to the next."""

    @staticmethod
    def _run(pure1, task, check_mode=False):
        """Return changed of one run of the task and the tags Pure1 holds after it."""
        task = dict({"name": "Array1"}, **task)
        module = run_main(pure1_array_tags, check_mode=check_mode, **task)
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once()
        return module.exit_json.call_args[1], pure1.tags

    @pytest.mark.parametrize("stored, task, left", CHANGES)
    def test_second_run_changes_nothing(self, pure1_client, stored, task, left):
        pure1 = _Pure1(pure1_client, tags=stored)

        assert self._run(pure1, task) == ({"changed": True}, left)
        assert self._run(pure1, task) == ({"changed": False}, left)

    @pytest.mark.parametrize("stored, task, left", CHANGES)
    def test_check_mode_reports_the_change_and_makes_none(
        self, pure1_client, stored, task, left
    ):
        pure1 = _Pure1(pure1_client, tags=stored)
        before = [list(tag) for tag in stored]

        # twice: the first run in check mode left everything as it was
        assert self._run(pure1, task, check_mode=True) == ({"changed": True}, before)
        assert self._run(pure1, task, check_mode=True) == ({"changed": True}, before)
        pure1_client.put_arrays_tags.assert_not_called()
        pure1_client.delete_arrays_tags.assert_not_called()

        assert self._run(pure1, task) == ({"changed": True}, left)
        assert self._run(pure1, task, check_mode=True) == ({"changed": False}, left)

    @pytest.mark.parametrize("check_mode", [False, True])
    @pytest.mark.parametrize("stored, task", NO_CHANGES)
    def test_nothing_to_change(self, pure1_client, stored, task, check_mode):
        pure1 = _Pure1(pure1_client, tags=stored)
        before = [list(tag) for tag in stored]

        assert self._run(pure1, task, check_mode) == ({"changed": False}, before)
        pure1_client.put_arrays_tags.assert_not_called()
        pure1_client.delete_arrays_tags.assert_not_called()

    def test_array_name_in_another_case_reaches_pure1_as_it_spells_it(
        self, pure1_client
    ):
        pure1 = _Pure1(pure1_client, tags=[("key1", "value1")])

        self._run(pure1, dict(name="ARRAY1", tag=["key2:value2"]))
        self._run(pure1, dict(name="array1", tag=["KEY1"], state="absent"))

        assert pure1_client.get_arrays_tags.call_args_list == [
            call(resource_names=["Array1"]),
            call(resource_names=["Array1"]),
        ]
        pure1_client.put_arrays_tags.assert_called_once_with(
            resource_names=["Array1"], tag_put=[{"key": "key2", "value": "value2"}]
        )
        pure1_client.delete_arrays_tags.assert_called_once_with(
            resource_names=["Array1"], keys=["key1"]
        )
        assert pure1.tags == [["key2", "value2"]]
