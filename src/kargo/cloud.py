#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of Kargo.
#
#    Foobar is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Foobar is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Foobar.  If not, see <http://www.gnu.org/licenses/>.

"""
kargo.cloud
~~~~~~~~~~~~

Run Instances on cloud providers and generate inventory
"""

import sys
import os
import yaml
import json
from kargo.inventory import CfgInventory
from kargo.common import get_logger, query_yes_no, run_command, which
from ansible.utils.display import Display
display = Display()
playbook_exec = which('ansible-playbook')

try:
    import configparser
except ImportError:
    import ConfigParser as configparser


class Cloud(object):
    '''
    Run Instances on cloud providers and generates inventory
    '''
    def __init__(self, options, cloud):
        self.options = options
        self.cloud = cloud
        self.inventorycfg = options['inventory_path']
        self.playbook = os.path.join(options['kargo_path'], 'local.yml')
        self.cparser = configparser.ConfigParser(allow_no_value=True)
        self.localcfg = os.path.join(
            options['kargo_path'],
            'inventory/local.cfg'
        )
        self.instances_file = os.path.join(
            options['kargo_path'],
            'instances.json'
        )
        self.logger = get_logger(
            options.get('logfile'),
            options.get('loglevel')
        )
        self.pbook_content = [{
            'gather_facts': False,
            'hosts': 'localhost',
            'become': False,
            'tasks': []
        }]
        self.logger.debug('''
             The following options were used to generate the inventory: %s
             ''' % self.options)

    def write_local_inventory(self):
        '''Generates inventory for local tasks'''
        self.cparser.add_section('local')
        self.cparser.set('local', 'localhost ansible_python_interpreter=python2 ansible_connection=local')
        try:
            with open(self.localcfg, 'wb') as f:
                self.cparser.write(f)
        except IOError as e:
            display.error(
                'Cannot write inventory %s: %s'
                % (self.localcfg, e)
            )
            sys.exit(1)

    def write_playbook(self):
        '''Write the playbook for instances creation'''
        try:
            with open(self.playbook, "w") as pb:
                pb.write(yaml.dump(self.pbook_content, default_flow_style=True))
        except IOError as e:
            display.error(
                'Cant write the playbook %s: %s'
                % (self.playbook, e)
            )
            sys.exit(1)

    def write_inventory(self):
        '''Generate the inventory according the instances created'''
        with open(self.instances_file) as f:
            instances = json.load(f)
        Cfg = CfgInventory(self.options, self.cloud)
        Cfg.write_inventory(instances)

    def create_instances(self):
        '''Run ansible-playbook for instances creation'''
        cmd = [
            playbook_exec, '-i', self.localcfg, '-e',
            'ansible_connection=local', self.playbook
        ]
        if not self.options['assume_yes']:
            if not query_yes_no('Create %s instances on %s ?' % (
                self.options['count'], self.cloud
                )
            ):
                display.display('Aborted', color='red')
                sys.exit(1)
        rcode, emsg = run_command('Create %s instances' % self.cloud, cmd)
        if rcode != 0:
            self.logger.critical('Cannot create instances: %s' % emsg)
            sys.exit(1)


class AWS(Cloud):

    def __init__(self, options):
        Cloud.__init__(self, options, "aws")
        self.options = options

    def gen_ec2_playbook(self):
        data = self.options
        data.pop('func')
        # Options list of ansible EC2 module
        ec2_options = [
            'aws_access_key', 'aws_secret_key', 'count', 'group',
            'instance_type', 'key_name', 'region', 'vpc_subnet_id',
            'image'
        ]
        # Define EC2 task
        ec2_task = {'ec2': {},
                    'name': 'Provision EC2 instances',
                    'register': 'ec2'}
        for opt in ec2_options:
            if opt in self.options.keys():
                d = {opt: self.options[opt]}
                ec2_task['ec2'].update(d)
        ec2_task['ec2'].update({'wait': True})
        self.pbook_content[0]['tasks'].append(ec2_task)
        # Write ec2 instances json
        self.pbook_content[0]['tasks'].append(
            {'name': 'Generate a file with ec2 instances list',
             'copy':
                 {'dest': '%s' % self.instances_file,
                  'content': '{{ec2.instances}}'}}
        )
        # Wait for ssh task
        self.pbook_content[0]['tasks'].append(
            {'local_action': {'host': '{{ item.public_ip }}',
                              'module': 'wait_for',
                              'port': 22,
                              'state': 'started',
                              'timeout': 600},
             'name': 'Wait until SSH is available',
             'with_items': 'ec2.instances'}
        )
        self.write_local_inventory()
        self.write_playbook()


class GCE(Cloud):

    def __init__(self, options):
        Cloud.__init__(self, options, "gce")
        self.options = options

    def gen_gce_playbook(self):
        data = self.options
        data.pop('func')
        # Options list of ansible GCE module
        gce_options = [
            'machine_type', 'image', 'zone', 'service_account_email',
            'pem_file', 'project_id'
        ]
        # Define instance names
        gce_instance_names = list()
        for x in range(self.options['count']):
            gce_instance_names.append(
                self.options['cluster_name'] + '-node' + str(x + 1)
            )
        gce_instance_names = ','.join(gce_instance_names)
        # Define GCE task
        gce_task = {'gce': {},
                    'name': 'Provision GCE instances',
                    'register': 'gce'}
        for opt in gce_options:
            if opt in self.options.keys():
                d = {opt: self.options[opt]}
                gce_task['gce'].update(d)
        gce_task['gce'].update({'instance_names': '%s' % gce_instance_names})
        self.pbook_content[0]['tasks'].append(gce_task)
        # Write gce instances json
        self.pbook_content[0]['tasks'].append(
            {'name': 'Generate a file with gce instances list',
             'copy':
                 {'dest': '%s' % self.instances_file,
                  'content': '{{gce.instance_data}}'}}
        )
        # Wait for ssh task
        self.pbook_content[0]['tasks'].append(
            {'local_action': {'host': '{{ item.public_ip }}',
                              'module': 'wait_for',
                              'port': 22,
                              'state': 'started',
                              'timeout': 600},
             'name': 'Wait until SSH is available',
             'with_items': 'gce.instance_data'}
        )
        self.write_local_inventory()
        self.write_playbook()
