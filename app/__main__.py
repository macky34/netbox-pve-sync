import os
import sys

import pynetbox
from proxmoxer import ProxmoxAPI
from rich import print


def _parse_pve_network_definition(_raw_network_definition: str) -> dict:
    _network_definition = {}

    for _component in _raw_network_definition.split(','):
        _component_parts = _component.split('=')
        _network_definition[_component_parts[0]] = _component_parts[1]

    return _network_definition


def main():
    # Instantiate connection to the Proxmox VE API
    pve_api = ProxmoxAPI(
        host=os.environ['PVE_API_HOST'],
        user=os.environ['PVE_API_USER'],
        token_name=os.environ['PVE_API_TOKEN'],
        token_value=os.environ['PVE_API_SECRET'],
        verify_ssl=os.getenv('PVE_API_VERIFY_SSL', 'false').lower() == 'true',
    )

    # Instantiate connection to the Netbox API
    nb_api = pynetbox.api(
        url=os.environ['NB_API_URL'],
        token=os.environ['NB_API_TOKEN'],
    )

    # Load NetBox devices
    nb_devices = {}
    for _nb_device in nb_api.dcim.devices.all():
        nb_devices[_nb_device.name.lower()] = _nb_device

    # Load NetBox virtual machines
    nb_virtual_machines = {}
    for _nb_virtual_machine in nb_api.virtualization.virtual_machines.all():
        nb_virtual_machines[_nb_virtual_machine.serial] = _nb_virtual_machine

    # Load NetBox interfaces
    nb_virtual_machines_interfaces = {}
    for _nb_interface in nb_api.virtualization.interfaces.all():
        if _nb_interface.virtual_machine.id not in nb_virtual_machines_interfaces:
            nb_virtual_machines_interfaces[_nb_interface.virtual_machine.id] = {}

        nb_virtual_machines_interfaces[_nb_interface.virtual_machine.id][_nb_interface.name] = _nb_interface

    # Load NetBox mac addresses
    nb_mac_addresses = {}
    for _nb_mac_address in nb_api.dcim.mac_addresses.all():
        nb_mac_addresses[_nb_mac_address.mac_address] = _nb_mac_address

    # Load NetBox IP addresses
    nb_ip_addresses = {}
    for _nb_ip_address in nb_api.ipam.ip_addresses.all():
        nb_ip_addresses[_nb_ip_address['address']] = _nb_ip_address

    # Process Proxmox nodes
    for pve_node in pve_api.nodes.get():
        # Process Proxmox virtual machines per node
        for pve_virtual_machine in pve_api.nodes(pve_node["node"]).qemu.get():
            pve_virtual_machine_config = pve_api.nodes(pve_node['node']).qemu(pve_virtual_machine['vmid']).config.get()

            # This script does not create the hardware devices.
            nb_device = nb_devices.get(pve_node['node'].lower())
            if nb_device is None:
                print(f'The device {pve_node["node"]} is not created on NetBox. Exiting.')
                sys.exit(1)
            else:
                pass

            # Create the virtual machine if it exists, update it otherwise
            nb_virtual_machine = nb_virtual_machines.get(str(pve_virtual_machine['vmid']))
            if nb_virtual_machine is None:
                nb_virtual_machine = nb_api.virtualization.virtual_machines.create(
                    serial=pve_virtual_machine['vmid'],
                    name=pve_virtual_machine['name'],
                    site=nb_device.site.id,
                    cluster=1,  # TODO
                    device=nb_device.id,
                    vcpus=pve_virtual_machine_config['cores'],
                    memory=int(pve_virtual_machine_config['memory']),
                )
            else:
                nb_virtual_machine.name = pve_virtual_machine['name']
                nb_virtual_machine.site = nb_device.site.id
                nb_virtual_machine.cluster = 1
                nb_virtual_machine.device = nb_device.id
                nb_virtual_machine.vcpus = pve_virtual_machine_config['cores']
                nb_virtual_machine.memory = int(pve_virtual_machine_config['memory'])
                nb_virtual_machine.save()

            # Handle the VM network interfaces
            for (_config_key, _config_value) in pve_virtual_machine_config.items():
                if not _config_key.startswith('net'):
                    continue

                _network_definition = _parse_pve_network_definition(_config_value)

                # Determinate MAC address
                network_mac_address = None
                for _model in ['virtio', 'e1000']:
                    if _model in _network_definition:
                        network_mac_address = _network_definition[_model]
                        break

                if network_mac_address is None:
                    continue

                nb_virtual_machines_interface = nb_virtual_machines_interfaces \
                    .get(nb_virtual_machine.id, {}) \
                    .get(_config_key)

                if nb_virtual_machines_interface is None:
                    nb_virtual_machines_interface = nb_api.virtualization.interfaces.create(
                        virtual_machine=nb_virtual_machine.id,
                        name=_config_key,
                        description=network_mac_address,
                    )

                    if nb_virtual_machine.id not in nb_virtual_machines_interfaces:
                        nb_virtual_machines_interfaces[nb_virtual_machine.id] = {}

                    nb_virtual_machines_interfaces[nb_virtual_machine.id][_config_key] = nb_virtual_machines_interface

                # Create the MAC address and link it to the VM
                nb_mac_address = nb_mac_addresses.get(network_mac_address)
                if nb_mac_address is None:
                    nb_mac_address = nb_api.dcim.mac_addresses.create(
                        mac_address=network_mac_address,
                        assigned_object_type='virtualization.vminterface',
                        assigned_object_id=nb_virtual_machines_interface.id,
                    )

                    nb_mac_addresses[network_mac_address] = nb_mac_address

                    nb_virtual_machines_interface.primary_mac_address = nb_mac_address.id
                    nb_virtual_machines_interface.save()

            # TODO: Handle the disk

            # Then create the disks if not exists, update them otherwise
            # Then create the network interface if not exists, update them otherwise
            # Link the network interface to the range if not exists

            # print(pve_api.nodes(pve_node['node']).qemu(pve_virtual_machine['vmid']).config.get())

            # if vm['status'] == 'running':
            # print(pve_api.nodes(node['node']).qemu(vm['vmid']).agent('network-get-interfaces').get())


if __name__ == '__main__':
    main()
