# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_pods module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
import types
from unittest.mock import Mock, MagicMock

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

from plugins.modules.pure1_pods import generate_pods_dict


def _make_pod(array_name="array1", **overrides):
    pod = types.SimpleNamespace(
        name="pod1",
        mediator="purestorage",
        arrays=[types.SimpleNamespace(name=array_name)],
    )
    for key, value in overrides.items():
        setattr(pod, key, value)
    return pod


def _make_stretched_pod(name="stretched", array_names=("array1", "array2")):
    return _make_pod(
        name=name,
        arrays=[types.SimpleNamespace(name=array) for array in array_names],
    )


POD1 = {"pod1": {"mediator": "purestorage", "source": []}}
STRETCHED = {"stretched": {"mediator": "purestorage", "source": []}}


class TestGeneratePodsDict:
    """Tests for generate_pods_dict."""

    def test_basic_mapping_no_source(self):
        module = Mock()
        module.params = {"array": None}
        client = Mock()
        client.get_pods.return_value = Mock(items=[_make_pod()])

        result = generate_pods_dict(module, client)

        client.get_pods.assert_called_once_with()
        assert result["array1"] == [{"pod1": {"mediator": "purestorage", "source": []}}]

    def test_source_is_unwrapped_to_name(self):
        module = Mock()
        module.params = {"array": None}
        pod = _make_pod(source=types.SimpleNamespace(name="sourcepod"))
        client = Mock()
        client.get_pods.return_value = Mock(items=[pod])

        result = generate_pods_dict(module, client)

        assert result["array1"][0]["pod1"]["source"] == "sourcepod"

    def test_array_filter_applied(self):
        module = Mock()
        module.params = {"array": "myarray"}
        client = Mock()
        client.get_pods.return_value = Mock(items=[_make_pod("myarray")])

        generate_pods_dict(module, client)

        client.get_pods.assert_called_once_with(filter="arrays.name='myarray'")

    def test_grouping_across_arrays(self):
        module = Mock()
        module.params = {"array": None}
        client = Mock()
        client.get_pods.return_value = Mock(
            items=[_make_pod("array1"), _make_pod("array2")]
        )

        result = generate_pods_dict(module, client)

        assert set(result.keys()) == {"array1", "array2"}

    def test_missing_mediator_defaults_to_none(self):
        module = Mock()
        module.params = {"array": None}
        bare = types.SimpleNamespace(
            name="pod1", arrays=[types.SimpleNamespace(name="array1")]
        )
        client = Mock()
        client.get_pods.return_value = Mock(items=[bare])

        result = generate_pods_dict(module, client)

        assert result["array1"][0]["pod1"]["mediator"] is None

    def test_stretched_pod_listed_under_each_array(self):
        module = Mock()
        module.params = {"array": None}
        client = Mock()
        client.get_pods.return_value = Mock(
            items=[_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(module, client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_stretched_pod_array_filter_first_array(self):
        module = Mock()
        module.params = {"array": "array1"}
        client = Mock()
        client.get_pods.return_value = Mock(items=[_make_stretched_pod()])

        result = generate_pods_dict(module, client)

        assert result == {"array1": [STRETCHED]}

    def test_stretched_pod_array_filter_second_array(self):
        module = Mock()
        module.params = {"array": "array2"}
        client = Mock()
        client.get_pods.return_value = Mock(
            items=[_make_stretched_pod(), _make_pod("array2")]
        )

        result = generate_pods_dict(module, client)

        client.get_pods.assert_called_once_with(filter="arrays.name='array2'")
        assert result == {"array2": [STRETCHED, POD1]}

    def test_array_filter_match_ignores_case(self):
        module = Mock()
        module.params = {"array": "ARRAY2"}
        client = Mock()
        client.get_pods.return_value = Mock(items=[_make_stretched_pod()])

        result = generate_pods_dict(module, client)

        client.get_pods.assert_called_once_with(filter="arrays.name='ARRAY2'")
        assert result == {"array2": [STRETCHED]}

    def test_array_filter_unmatched_name_keeps_pod(self):
        module = Mock()
        module.params = {"array": "other"}
        client = Mock()
        client.get_pods.return_value = Mock(
            items=[_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(module, client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_array_reference_without_name_is_skipped(self):
        module = Mock()
        module.params = {"array": None}
        pod = _make_pod(
            arrays=[types.SimpleNamespace(name="array1"), types.SimpleNamespace(), None]
        )
        client = Mock()
        client.get_pods.return_value = Mock(items=[pod])

        result = generate_pods_dict(module, client)

        assert result == {"array1": [POD1]}

    def test_repeated_array_name_lists_pod_once(self):
        module = Mock()
        module.params = {"array": None}
        pod = _make_pod(
            arrays=[
                types.SimpleNamespace(name="array1"),
                types.SimpleNamespace(name="array1"),
            ]
        )
        client = Mock()
        client.get_pods.return_value = Mock(items=[pod])

        result = generate_pods_dict(module, client)

        assert result == {"array1": [POD1]}

    def test_pod_without_arrays_is_skipped(self):
        module = Mock()
        module.params = {"array": None}
        no_arrays = types.SimpleNamespace(name="orphan", mediator="purestorage")
        client = Mock()
        client.get_pods.return_value = Mock(
            items=[no_arrays, _make_pod(name="empty", arrays=[]), _make_pod()]
        )

        result = generate_pods_dict(module, client)

        assert result == {"array1": [POD1]}
