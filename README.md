[![Release](https://img.shields.io/github/v/tag/Everpure-Ansible/Pure1-Collection?label=release)](https://github.com/Everpure-Ansible/Pure1-Collection/releases/latest)
[![License](https://img.shields.io/badge/license-GPL%20v3.0-brightgreen.svg)](https://github.com/Everpure-Ansible/Pure1-Collection/blob/master/COPYING.GPLv3)
[![CLA assistant](https://cla-assistant.io/readme/badge/Everpure-Ansible/Pure1-Collection)](https://cla-assistant.io/Everpure-Ansible/Pure1-Collection)
[![Everpure Ansible CI](https://github.com/Everpure-Ansible/Pure1-Collection/workflows/Everpure%20Ansible%20CI/badge.svg)](https://github.com/Everpure-Ansible/Pure1-Collection/actions)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

# Everpure Pure1 Collection

## Description

The Everpure Pure1 collection consists of the latest versions of the Pure1 modules.

## Requirements

- Authorized API Application ID for Everpure Pure1 and associated Private Key
  Refer to Everpure documentation on how to create these.
- python >= 3.9
- py-pure-client >= 1.99.0

## Installation

As Red Hat Ansible Certified Content, this collection is available from
[Red Hat Ansible Automation Hub](https://console.redhat.com/ansible/automation-hub/collections/published/everpure/pure1).
Once your Automation Hub credentials are configured in `ansible.cfg`, install it with:

```
ansible-galaxy collection install everpure.pure1
```

You can also include it in a `requirements.yml` file and install it with `ansible-galaxy collection install -r requirements.yml`, using the format:

```
collections:
  - name: everpure.pure1
```

Note that the collection is not upgraded automatically when you upgrade the Ansible Automation Platform.

To upgrade the collection to the latest available version, run the following command:

```
ansible-galaxy collection install everpure.pure1 --upgrade
```

You can also install a specific version of the collection, for example, if you need to downgrade when something is broken in the latest version (please report an issue in this repository). Use the following syntax to install version 1.0.0:

```
ansible-galaxy collection install everpure.pure1:==1.0.0
```

For configuring Automation Hub as a collection source, see
[Configuring Red Hat automation hub as your primary source](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.4/html/getting_started_with_automation_hub/configure-hub-primary).

The collection is also published on
[Ansible Galaxy](https://galaxy.ansible.com/ui/repo/published/everpure/pure1/)
for community users. See
[using Ansible collections](https://docs.ansible.com/ansible/devel/user_guide/collections_using.html)
for more details.

## Use Cases

This collection can be used to report on the Everpure arrays of a Pure1 organization and to manage array tags in Pure1, either on its own or as part of larger playbooks. For example:
* Collect organization-wide information from Pure1: appliances, subscriptions and subscription licenses, support contracts, invoices and environmental data
* List the volumes, pods, drives, ports and network interfaces of all appliances or of a single array
* Collect open, closing or closed alerts of a chosen severity, for all appliances or for a single array
* Collect historical capacity and performance metrics for volumes
* Create, update and remove array tags in Pure1

### Example Playbook
```yaml
- hosts: localhost
  tasks:
  - name: Collect information for all Everpure appliances in Pure1
    everpure.pure1.pure1_info:
      gather_subset: all
      app_id: <Pure1 API Application ID>
      key_file: <private key file name>
      password: <private key password>
```

## Contributing

There are many ways in which you can participate in the project, for example:

* Submit bugs and feature requests, and help us verify as they are checked in
* Review source code changes
* Review the documentation and make pull requests for anything from typos to new content
* If you are interested in fixing issues and contributing directly to the code base, please see the details below:
    1. Fork this project into your account if you are a first-time contributor.
    2. Create a branch based on the latest `master` branch, commit your changes on this branch.
    3. You may merge the Pull Request in once you have the sign-off of two other developers, or if you do not have permission to do that, you may request the second reviewer to merge it for you.
 
## Support

Only the latest release of this collection is supported.

As Red Hat Ansible Certified Content, this collection is entitled to support through the Ansible Automation Platform (AAP) using the **Create issue** button on the top right corner. This is the Primary Support Route for a Certified Collection.

Community-Only help may be available on the [Ansible Forum](https://forum.ansible.com/) or raise a [GitHub issue](https://github.com/Everpure-Ansible/Pure1-Collection/issues) in the Collection repo.

## Release Notes

The full changelog is shipped with the collection as `CHANGELOG.rst` and is also available
[in the repository](https://github.com/Everpure-Ansible/Pure1-Collection/blob/master/CHANGELOG.rst).

Per-release notes can be found on the
[releases page](https://github.com/Everpure-Ansible/Pure1-Collection/releases).

## Related Information
### Available Modules

- pure1_alerts - Get alerts from Pure1
- pure1_array_tags - Manage array tags for managed devices in Pure1
- pure1_arrays - Collect the arrays of the organization from Pure1
- pure1_audits - Collect audit records from Pure1
- pure1_buckets - Collect object store accounts and their buckets from Pure1
- pure1_controllers - Collect array controllers information from Pure1
- pure1_cves - Collect CVEs and the arrays they affect from Pure1
- pure1_drives - Get array drives information from Pure1
- pure1_file_systems - Collect file systems information from Pure1
- pure1_fleets - Collect fleets and their members from Pure1
- pure1_hardware - Collect the hardware components of arrays from Pure1
- pure1_info - Collect information from Pure1
- pure1_metrics - Collect metric data of arrays and their objects from Pure1
- pure1_network_interfaces - Get array network interface details from Pure1 (deprecated, to be removed in version 2.0.0: use pure1_nics instead)
- pure1_nics - Get network interface information from Pure1
- pure1_pgroup_snapshots - Collect replicated protection group snapshots from Pure1
- pure1_pods - Get FlashArray pod information from Pure1
- pure1_ports - Get port information from Pure1
- pure1_recommendations - Collect the recommendations of Pure1
- pure1_replica_links - Collect replica links and replication targets from Pure1
- pure1_security - Collect the security assessment of arrays from Pure1
- pure1_topology_groups - Collect the topology groups of fleets from Pure1
- pure1_volume_metrics - Get FlashArray volume metrics from Pure1
- pure1_volumes - Get FlashArray volume information from Pure1

## License Information

[BSD-2-Clause](https://directory.fsf.org/wiki?title=License:FreeBSD)

[GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.en.html)

## Author

This collection was created in 2020 by [Simon Dodsley](https://github.com/sdodsley) for, and on behalf of, the [Everpure Ansible Team](mailto:pure-ansible-team@everpuredata.com)
