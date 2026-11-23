# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_array_tags module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
import types
import pytest
from unittest.mock import Mock, patch, MagicMock, call

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

from plugins.modules.pure1_array_tags import create_tag, update_tag, delete_tag, main


def _ok():
    return Mock(status_code=200)


def _err(message="boom"):
    return _err_answer([types.SimpleNamespace(message=message, context=None)])


def _err_answer(errors):
    # Like the SDK error answer: a status and an errors list, no items
    return types.SimpleNamespace(status_code=400, errors=errors)


def _sdk_put(recorder):
    """put_arrays_tags with the py-pure-client 1.99.0 signature."""

    def put_arrays_tags(
        tag_put,
        resources=None,
        authorization=None,
        x_request_id=None,
        namespaces=None,
        resource_ids=None,
        resource_names=None,
        _preload_content=True,
        _return_http_data_only=None,
        _request_timeout=None,
    ):
        return recorder(tag_put=tag_put, resource_names=resource_names)

    return put_arrays_tags


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
    ([types.SimpleNamespace(message=None, context=None)], "HTTP status 400"),
    ([types.SimpleNamespace(message="Invalid value", context=None)], "Invalid value"),
    (
        [
            types.SimpleNamespace(
                message="Invalid value", context="No arrays with name: foo"
            )
        ],
        "Invalid value: No arrays with name: foo",
    ),
]


class TestCreateTag:
    """Tests for create_tag."""

    def test_check_mode_makes_no_api_call(self):
        module = Mock()
        module.check_mode = True
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        client = Mock()

        create_tag(module, client)

        client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_creates_tag_with_parsed_key_value(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        create_tag(module, client)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_all_tags_are_sent_in_one_call(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1", "key2:value2"]}
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        create_tag(module, client)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value1"},
                {"key": "key2", "value": "value2"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_repeated_key_is_sent_once_with_the_last_value(self):
        module = Mock()
        module.check_mode = False
        module.params = {
            "name": "foo",
            "tag": ["key1:value1", "key2:value2", "key1:value3"],
        }
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        create_tag(module, client)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value3"},
                {"key": "key2", "value": "value2"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_empty_tag_list_makes_no_api_call(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": []}
        client = Mock()

        create_tag(module, client)

        client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=True)

    def test_api_failure_calls_fail_json(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        module.fail_json.side_effect = SystemExit("fail_json called")
        client = Mock()
        client.put_arrays_tags.return_value = _err()

        with pytest.raises(SystemExit):
            create_tag(module, client)

        module.fail_json.assert_called_once()


class TestUpdateTag:
    """Tests for update_tag."""

    def test_check_mode_makes_no_api_call(self):
        module = Mock()
        module.check_mode = True
        module.params = {"name": "foo", "tag": ["key1:value2"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_existing_tag_same_value_no_change(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_existing_tag_changed_value_updates(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value2"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value2"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_new_tag_is_added(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key2:value2"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key2", "value": "value2"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_only_changed_and_new_tags_are_sent_in_one_call(self):
        module = Mock()
        module.check_mode = False
        module.params = {
            "name": "foo",
            "tag": ["key1:value1", "key2:new", "key3:value3"],
        }
        current = [
            types.SimpleNamespace(key="key1", value="value1"),
            types.SimpleNamespace(key="key2", value="old"),
        ]
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key2", "value": "new"},
                {"key": "key3", "value": "value3"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_repeated_key_is_sent_once_with_the_last_value(self):
        module = Mock()
        module.check_mode = False
        module.params = {
            "name": "foo",
            "tag": ["key1:value2", "key2:value2", "key1:value3", "key2:value3"],
        }
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"],
            tag_put=[
                {"key": "key1", "value": "value3"},
                {"key": "key2", "value": "value3"},
            ],
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_key_read_twice_is_sent_once(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:new"]}
        current = [
            types.SimpleNamespace(key="key1", value="old"),
            types.SimpleNamespace(key="key1", value="older"),
        ]
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        update_tag(module, client, current)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "new"}]
        )
        module.exit_json.assert_called_once_with(changed=True)


class TestDeleteTag:
    """Tests for delete_tag."""

    def test_matching_tag_is_deleted(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.delete_arrays_tags.return_value = _ok()

        delete_tag(module, client, current)

        client.delete_arrays_tags.assert_called_once_with(
            resource_names=["foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_each_matching_key_is_deleted_in_its_own_call(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1", "key2:value2"]}
        current = [
            types.SimpleNamespace(key="key1", value="value1"),
            types.SimpleNamespace(key="key2", value="value2"),
        ]
        client = Mock()
        client.delete_arrays_tags.return_value = _ok()

        delete_tag(module, client, current)

        assert client.delete_arrays_tags.call_args_list == [
            call(resource_names=["foo"], keys=["key1"]),
            call(resource_names=["foo"], keys=["key2"]),
        ]
        module.exit_json.assert_called_once_with(changed=True)

    def test_non_matching_tag_is_not_deleted(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key2:value2"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()

        delete_tag(module, client, current)

        client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)

    def test_check_mode_makes_no_api_call(self):
        module = Mock()
        module.check_mode = True
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()

        delete_tag(module, client, current)

        client.delete_arrays_tags.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False)


class TestTagKeyword:
    """The tags go under the keyword the SDK's put_arrays_tags takes."""

    def test_create_is_accepted_by_the_sdk_signature(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        recorder = Mock(return_value=_ok())
        client = Mock()
        client.put_arrays_tags = _sdk_put(recorder)

        create_tag(module, client)

        recorder.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value1"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_update_is_accepted_by_the_sdk_signature(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value2"]}
        current = [types.SimpleNamespace(key="key1", value="value1")]
        recorder = Mock(return_value=_ok())
        client = Mock()
        client.put_arrays_tags = _sdk_put(recorder)

        update_tag(module, client, current)

        recorder.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value2"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_old_sdk_signature_gets_the_tag_keyword(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        recorder = Mock(return_value=_ok())
        client = Mock()
        client.put_arrays_tags = _old_sdk_put(recorder)

        create_tag(module, client)

        recorder.assert_called_once_with(
            tag=[{"key": "key1", "value": "value1"}], resource_names=["foo"]
        )
        module.exit_json.assert_called_once_with(changed=True)

    def test_signature_with_both_keywords_gets_tag_put(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        recorder = Mock(return_value=_ok())

        def put_arrays_tags(tag_put=None, tag=None, resource_names=None):
            return recorder(tag_put=tag_put, tag=tag, resource_names=resource_names)

        client = Mock()
        client.put_arrays_tags = put_arrays_tags

        create_tag(module, client)

        recorder.assert_called_once_with(
            tag_put=[{"key": "key1", "value": "value1"}],
            tag=None,
            resource_names=["foo"],
        )

    def test_uninspectable_method_gets_tag_put(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        client = Mock()
        client.put_arrays_tags.return_value = _ok()

        with patch("inspect.signature", side_effect=ValueError("no signature")):
            create_tag(module, client)

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )

    def test_type_error_inside_the_sdk_call_is_not_retried(self):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        recorder = Mock(side_effect=TypeError("raised inside the SDK"))
        client = Mock()
        client.put_arrays_tags = _sdk_put(recorder)

        with pytest.raises(TypeError, match="raised inside the SDK"):
            create_tag(module, client)

        recorder.assert_called_once()


@pytest.mark.parametrize("errors, text", ERROR_ANSWERS)
class TestErrorAnswers:
    """An error answer becomes a failure message, whatever its errors list holds."""

    def test_create(self, errors, text):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1", "key2:value2"]}
        module.fail_json.side_effect = SystemExit("fail_json called")
        client = Mock()
        client.put_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            create_tag(module, client)

        module.fail_json.assert_called_once_with(
            msg="Failed to add tag KVP key1:value1, key2:value2 to array foo. "
            "Error: " + text
        )

    def test_update(self, errors, text):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value2", "key2:value2"]}
        module.fail_json.side_effect = SystemExit("fail_json called")
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.put_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            update_tag(module, client, current)

        module.fail_json.assert_called_once_with(
            msg="Failed to update tags 'key1, key2' on array foo. Error: " + text
        )

    def test_delete(self, errors, text):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"]}
        module.fail_json.side_effect = SystemExit("fail_json called")
        current = [types.SimpleNamespace(key="key1", value="value1")]
        client = Mock()
        client.delete_arrays_tags.return_value = _err_answer(errors)

        with pytest.raises(SystemExit):
            delete_tag(module, client, current)

        module.fail_json.assert_called_once_with(
            msg="Failed to remove tag KVP 'key1:value1' from volume foo. "
            "Error: " + text
        )

    @patch("plugins.modules.pure1_array_tags.get_pure1")
    @patch("plugins.modules.pure1_array_tags.AnsibleModule")
    def test_tag_read(self, mock_am, mock_get_pure1, errors, text):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"], "state": "present"}
        module.fail_json.side_effect = SystemExit("fail_json called")
        mock_am.return_value = module
        client = Mock()
        client.get_arrays_tags.return_value = _err_answer(errors)
        mock_get_pure1.return_value = client

        with pytest.raises(SystemExit):
            main()

        client.get_arrays_tags.assert_called_once_with(resource_names=["foo"])
        client.put_arrays_tags.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Failed to get tags of array foo. Error: " + text
        )


class TestMain:
    """Tests for the pure1_array_tags main() flow."""

    @patch("plugins.modules.pure1_array_tags.get_pure1")
    @patch("plugins.modules.pure1_array_tags.AnsibleModule")
    def test_present_without_tags_creates(self, mock_am, mock_get_pure1):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"], "state": "present"}
        module.exit_json.side_effect = SystemExit("exit_json called")
        mock_am.return_value = module
        client = Mock()
        client.get_arrays_tags.return_value = Mock(status_code=200, items=[])
        client.put_arrays_tags.return_value = _ok()
        mock_get_pure1.return_value = client

        with pytest.raises(SystemExit):
            main()

        client.put_arrays_tags.assert_called_once_with(
            resource_names=["foo"], tag_put=[{"key": "key1", "value": "value1"}]
        )
        module.exit_json.assert_called_once_with(changed=True)

    @patch("plugins.modules.pure1_array_tags.get_pure1")
    @patch("plugins.modules.pure1_array_tags.AnsibleModule")
    def test_absent_with_matching_tag_deletes(self, mock_am, mock_get_pure1):
        module = Mock()
        module.check_mode = False
        module.params = {"name": "foo", "tag": ["key1:value1"], "state": "absent"}
        module.exit_json.side_effect = SystemExit("exit_json called")
        mock_am.return_value = module
        client = Mock()
        client.get_arrays_tags.return_value = Mock(
            status_code=200,
            items=[types.SimpleNamespace(key="key1", value="value1")],
        )
        client.delete_arrays_tags.return_value = _ok()
        mock_get_pure1.return_value = client

        with pytest.raises(SystemExit):
            main()

        client.delete_arrays_tags.assert_called_once_with(
            resource_names=["foo"], keys=["key1"]
        )
        module.exit_json.assert_called_once_with(changed=True)
