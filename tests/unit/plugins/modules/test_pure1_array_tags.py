# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_array_tags module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import inspect
import pytest
from unittest.mock import Mock, call, patch

from pypureclient import ApiError, ErrorResponse, ValidResponse

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


# Error lists an error answer can carry, and the text the module reports
ERROR_ANSWERS = [
    ([], "HTTP status 400"),
    ([ApiError(context=None, message=None)], "HTTP status 400"),
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
        module.exit_json.assert_called_once_with(changed=True)

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
        module.exit_json.assert_called_once_with(changed=False)


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

        with pytest.raises(TypeError, match="raised inside the SDK"):
            create_tag(module, pure1_client)

        pure1_client.put_arrays_tags.assert_called_once()


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
            msg="Failed to remove tag KVP 'key1:value1' from volume foo. "
            "Error: " + text
        )

    def test_tag_read(self, pure1_client, errors, text):
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
        pure1_client.get_arrays_tags.return_value = valid_response(
            [_tag("key1", "value1")]
        )

        module = run_main(pure1_array_tags, name="foo", tag=["key1:value1"])

        # the one call that lists arrays is the check of get_pure1
        pure1_client.get_arrays.assert_called_once_with()
        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_absent_without_tags_changes_nothing(self, pure1_client):
        pure1_client.get_arrays_tags.return_value = valid_response([])

        module = run_main(
            pure1_array_tags, name="foo", tag=["key1:value1"], state="absent"
        )

        pure1_client.delete_arrays_tags.assert_not_called()
        pure1_client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_check_mode_reads_the_tags_and_writes_nothing(self, pure1_client):
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
