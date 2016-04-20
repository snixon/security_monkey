# Copyright 2014 Netflix, Inc.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
"""
.. module: security_monkey.auditors.security_group
    :platform: Unix

.. version:: $$VERSION$$
.. moduleauthor:: Patrick Kelley <pkelley@netflix.com> @monkeysecurity

"""

from security_monkey.auditor import Auditor
from security_monkey.watchers.security_group import SecurityGroup
from security_monkey.datastore import NetworkWhitelistEntry
from security_monkey import app

import ipaddr


def _check_empty_security_group(sg_item):
    if app.config.get('SECURITYGROUP_INSTANCE_DETAIL', None) in ['SUMMARY', 'FULL'] and \
            not sg_item.config.get("assigned_to", None):
        return 0
    return 1


def _check_rfc_1918(cidr):
        """
        EC2-Classic SG's should never use RFC-1918 CIDRs
        """
        if ipaddr.IPNetwork(cidr) in ipaddr.IPNetwork('10.0.0.0/8'):
            return True

        if ipaddr.IPNetwork(cidr) in ipaddr.IPNetwork('172.16.0.0/12'):
            return True

        if ipaddr.IPNetwork(cidr) in ipaddr.IPNetwork('192.168.0.0/16'):
            return True

        return False


class SecurityGroupAuditor(Auditor):
    index = SecurityGroup.index
    i_am_singular = SecurityGroup.i_am_singular
    i_am_plural = SecurityGroup.i_am_plural
    network_whitelist = []

    def __init__(self, accounts=None, debug=False):
        super(SecurityGroupAuditor, self).__init__(accounts=accounts, debug=debug)

    def prep_for_audit(self):
        self.network_whitelist = NetworkWhitelistEntry.query.all()

    def _check_inclusion_in_network_whitelist(self, cidr):
        for entry in self.network_whitelist:
            if ipaddr.IPNetwork(cidr) in ipaddr.IPNetwork(str(entry.cidr)):
                return True
        return False

    def __port_for_rule__(self, rule):
        """
        Looks at the from_port and to_port and returns a sane representation
        """
        if rule['from_port'] == rule['to_port']:
            return "{} {}".format(rule['ip_protocol'], rule['from_port'])

        return "{} {}-{}".format(rule['ip_protocol'], rule['from_port'], rule['to_port'])

    def check_securitygroup_ec2_rfc1918(self, sg_item):
        """
        alert if EC2 SG contains RFC1918 CIDRS
        """
        tag = "Non-VPC Security Group contains private RFC-1918 CIDR"
        severity = 5

        if sg_item.config.get("vpc_id", None):
            return

        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip", None)
            if cidr and _check_rfc_1918(cidr):
                self.add_issue(severity * multiplier, tag, sg_item, notes=cidr)

    def check_securitygroup_rule_count(self, sg_item):
        """
        alert if SG has more than 50 rules
        """
        tag = "Security Group contains 50 or more rules"
        severity = 1
        multiplier = _check_empty_security_group(sg_item)

        rules = sg_item.config.get('rules', [])
        if len(rules) >= 50:
            self.add_issue(severity * multiplier, tag, sg_item)

    def check_securitygroup_large_port_range(self, sg_item):
        """
        Make sure the SG does not contain large port ranges.
        """
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            if rule['from_port'] == rule['to_port']:
                continue

            from_port = int(rule['from_port'])
            to_port = int(rule['to_port'])

            range_size = to_port - from_port

            name = ''

            if rule.get('cidr_ip', None):
                name = rule['cidr_ip']
            else:
                # TODO: Identify cross account SG
                name = rule.get('name', "Unknown")

            note = "{} on {}".format(name, self.__port_for_rule__(rule))

            if range_size > 2500:
                self.add_issue(4 * multiplier, "Port Range > 2500 Ports", sg_item, notes=note)
                continue

            if range_size > 750:
                self.add_issue(3 * multiplier, "Port Range > 750 Ports", sg_item, notes=note)
                continue

            if range_size > 250:
                self.add_issue(1 * multiplier, "Port Range > 250 Ports", sg_item, notes=note)
                continue

    def check_securitygroup_large_subnet(self, sg_item):
        """
        Make sure the SG does not contain large networks.
        """
        tag = "Security Group network larger than /24"
        severity = 3
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip", None)
            if cidr and not self._check_inclusion_in_network_whitelist(cidr):
                if '/' in cidr and not cidr == "0.0.0.0/0" and not cidr == "10.0.0.0/8":
                    mask = int(cidr.split('/')[1])
                    if mask < 24 and mask > 0:
                        notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                        self.add_issue(severity * multiplier, tag, sg_item, notes=notes)

    def check_securitygroup_zero_subnet(self, sg_item):
        """
        Make sure the SG does not contain a cidr with a subnet length of zero.
        """
        tag = "Security Group subnet mask is /0"
        severity = 10
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip", None)
            if cidr and '/' in cidr and not cidr == "0.0.0.0/0" and not cidr == "10.0.0.0/8":
                mask = int(cidr.split('/')[1])
                if mask == 0:
                    notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                    self.add_issue(severity * multiplier, tag, sg_item, notes=notes)

    def check_securitygroup_any(self, sg_item):
        """
        Make sure the SG does not contain 0.0.0.0/0
        """
        tag = "Security Group contains 0.0.0.0/0"
        severity = 5
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip")
            if "0.0.0.0/0" == cidr:
                notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                self.add_issue(severity * multiplier, tag, sg_item, notes=notes)

    def check_securitygroup_ingress_any(self, sg_item):
        """
        Make sure the SG does not contain any 0.0.0.0/0 ingress rules
        """
        tag = "Security Group ingress rule contains 0.0.0.0/0"
        severity = 10
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip")
            rtype = rule.get("rule_type")
            if "0.0.0.0/0" == cidr and rtype == "ingress":
                notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                self.add_issue(severity * multiplier, tag, sg_item, notes=notes)

    def check_securitygroup_egress_any(self, sg_item):
        """
        Make sure the SG does not contain any 0.0.0.0/0 egress rules
        """
        tag = "Security Group egress rule contains 0.0.0.0/0"
        severity = 5
        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip")
            rtype = rule.get("rule_type")
            if "0.0.0.0/0" == cidr and rtype == "egress":
                notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                self.add_issue(severity * multiplier, tag, sg_item, notes=notes)

    def check_securitygroup_10net(self, sg_item):
        """
        Make sure the SG does not contain 10.0.0.0/8
        """
        tag = "Security Group contains 10.0.0.0/8"
        severity = 5

        if sg_item.config.get("vpc_id", None):
            return

        multiplier = _check_empty_security_group(sg_item)

        for rule in sg_item.config.get("rules", []):
            cidr = rule.get("cidr_ip")
            if "10.0.0.0/8" == cidr:
                notes = "{} on {}".format(cidr, self.__port_for_rule__(rule))
                self.add_issue(severity * multiplier, tag, sg_item, notes=notes)
