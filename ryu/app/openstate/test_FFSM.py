
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import struct

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, HANDSHAKE_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.topology import event
import time

'''
Applicazione di test che fa uso di Global States (flags), Flow States e Metadata contemporaneamente e dei comandi OFPSC_EXP_SET_FLOW_STATE e OFPSC_EXP_DEL_FLOW_STATE

Ci sono 6 host:
h1 e h2 si pingano sempre
h3 e h4 si pingano per 5 secondi, poi non riescono per altri 5 e infine riescono sempre
h5 e h6 si pingano sempre

TABLE 0 (stateless)

ipv4_src=10.0.0.1, in_port=1    --->    SetState(state=0xfffffffa,table_id=1), SetFlag("1*01********"), WriteMetadata(64954), GotoTable(1)
ipv4_src=10.0.0.2, in_port=2    --->    forward(1)
ipv4_src=10.0.0.3, in_port=3    --->    GotoTable(1)
ipv4_src=10.0.0.4, in_port=4    --->    forward(3)
ipv4_src=10.0.0.5, in_port=5    --->    SetState(state = 3, state_mask = 255, table_id=1), GotoTable(1)
ipv4_src=10.0.0.6, in_port=6    --->    forward(5)

TABLE 1 (stateful) Lookup-scope=Update-scope=OXM_OF_IPV4_SRC)

ipv4_src=10.0.0.1, metadata=64954, flags="1*01********", state=0xfffffffa   --->    forward(2)
ipv4_src=10.0.0.3, state=2                                                  --->    forward(4)
ipv4_src=10.0.0.5, state=3, state_mask = 255                                --->    forward(6)
'''

class OSTestFFSM(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(OSTestFFSM, self).__init__(*args, **kwargs)

    @set_ev_cls(ofp_event.EventOFPExperimenterStatsReply, MAIN_DISPATCHER)
    def state_stats_reply_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        if ev.msg.body.exp_type==0:
            # EXP_STATE_STATS
            print("OFPExpStateStatsMultipartReply received:")
            offset=0
            for stats in ev.msg.body:
                extractor = [ofp.OXM_OF_IPV4_SRC]
                stat = parser.OFPStateStats.parser(ev.msg.body.data, offset)
                print('{table_id=%s, key={%s}, state=%d}' %(stat.table_id,parser.state_entry_key_to_str(extractor,stat.entry.key,stat.entry.key_count),stat.entry.state))
                offset+=stat.length

        elif ev.msg.body.exp_type==1:
            # EXP_GLOBAL_STATE_STATS
            print("OFPExpGlobalStateStatsMultipartReply received:")
            stat = parser.OFPGlobalStateStats.parser(ev.msg.body.data, 0)
            print("{global_states="+'{:032b}'.format(stat.flags)+"}")


    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        flows = []
        for stat in ev.msg.body:
            flows.append('{table_id=%s '
                         'duration_sec=%d duration_nsec=%d '
                         'priority=%d '
                         'idle_timeout=%d hard_timeout=%d flags=0x%04x '
                         'cookie=%d packet_count=%d byte_count=%d '
                         'match=%s instructions=%s}' %
                         (stat.table_id,
                          stat.duration_sec, stat.duration_nsec,
                          stat.priority,
                          stat.idle_timeout, stat.hard_timeout, stat.flags,
                          stat.cookie, stat.packet_count, stat.byte_count,
                          stat.match, stat.instructions))
        print('')
        print('OFPFlowStatsReply received: '+str(flows))
        print('')
    
    '''@set_ev_cls(ofp_event.EventOFPStateNotification, MAIN_DISPATCHER)
    def state_notification_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        extractor = [ofp.OXM_OF_IPV4_SRC]
        print('OFPStateNotification received: table_id=%s, key={%s}, state=%s ' %(
                          msg.table_id, parser.state_entry_key_to_str(extractor,msg.key), msg.state))'''

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto

        self.send_features_request(datapath)
        self.send_table_mod(datapath)

        self.send_key_lookup(datapath)
        self.send_key_update(datapath)

        self.add_flow(datapath)
        self.set_substate_entry(datapath)
        time.sleep(5)
        self.set_substate_entry2(datapath)
        
        self.set_state_entry(datapath)
        time.sleep(5)
        self.del_state_entry(datapath)
        time.sleep(5)
        self.set_state_entry(datapath)

        #self.send_flow_stats_request(datapath)
        self.send_state_stats_request(datapath)
        self.send_global_state_stats_request(datapath)

    def add_flow(self, datapath, table_miss=False):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # ARP packets flooding
        match = parser.OFPMatch(eth_type=0x0806)
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32760, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)


        match = parser.OFPMatch(
            ipv4_src="10.0.0.1", in_port=1, eth_type=0x0800)
        (flag, flag_mask) = parser.maskedflags("1*01",8)
        (state, state_mask) = parser.substate(state=4294967290,section=1,sec_count=1)
        actions = [parser.OFPExpActionSetState(state=state,state_mask=state_mask,table_id=1),
            parser.OFPExpActionSetFlag(flag=flag,flag_mask=flag_mask)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions),
            parser.OFPInstructionGotoTable(1),
            parser.OFPInstructionWriteMetadata(64954, 0xffffffffffffffff)
            ]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.1", in_port=1, eth_type=0x0800, metadata=64954, state=parser.substate(state=4294967290,section=1,sec_count=1), flags=parser.maskedflags("1*01",8))
        actions = [parser.OFPActionOutput(2)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=1,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.5", in_port=5, eth_type=0x0800)
        (state, state_mask) = parser.substate(state=3,section=1,sec_count=4)
        actions = [parser.OFPExpActionSetState(state=state,state_mask=state_mask,table_id=1)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions),
            parser.OFPInstructionGotoTable(1)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.5", in_port=5, eth_type=0x0800, state=parser.substate(state=3,section=1,sec_count=4))
        actions = [parser.OFPActionOutput(6)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=1,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.3", in_port=3, eth_type=0x0800)

        inst = [parser.OFPInstructionGotoTable(1)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.4", in_port=4, eth_type=0x0800)
        actions = [parser.OFPActionOutput(3)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.6", in_port=6, eth_type=0x0800)
        actions = [parser.OFPActionOutput(5)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.3", in_port=3, eth_type=0x0800, state=parser.substate(state=2,section=1,sec_count=1))
        actions = [parser.OFPActionOutput(4)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=1,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)

        match = parser.OFPMatch(
            ipv4_src="10.0.0.2", in_port=2, eth_type=0x0800)
        actions = [parser.OFPActionOutput(1)]
        inst = [parser.OFPInstructionActions(
            datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=0, cookie_mask=0, table_id=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=32768, buffer_id=ofproto.OFP_NO_BUFFER,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            flags=0, match=match, instructions=inst)
        datapath.send_msg(mod)


    def send_table_mod(self, datapath):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser
        req = ofp_parser.OFPExpMsgConfigureStatefulTable(datapath=datapath, table_id=1, statefulness=1)
        datapath.send_msg(req)

    def send_features_request(self, datapath):
        ofp_parser = datapath.ofproto_parser
        req = ofp_parser.OFPFeaturesRequest(datapath)
        datapath.send_msg(req)

    def set_substate_entry(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        (state, state_mask) = parser.substate(state=2,section=4,sec_count=4)
        msg = datapath.ofproto_parser.OFPExpMsgStateMod(
            datapath=datapath, command=ofproto.OFPSC_EXP_SET_FLOW_STATE, state=state, state_mask=state_mask,  keys=[10,0,0,5], table_id=1)
        datapath.send_msg(msg)

    def set_substate_entry2(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        (state, state_mask) = parser.substate(state=6,section=3,sec_count=4)
        msg = datapath.ofproto_parser.OFPExpMsgStateMod(
            datapath=datapath, command=ofproto.OFPSC_EXP_SET_FLOW_STATE, state=state, state_mask=state_mask,  keys=[10,0,0,5], table_id=1)
        datapath.send_msg(msg)

    def set_state_entry(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        (state, state_mask) = parser.substate(state=2,section=1,sec_count=1)
        msg = datapath.ofproto_parser.OFPExpMsgStateMod(
            datapath=datapath, command=ofproto.OFPSC_EXP_SET_FLOW_STATE, state=state, state_mask=state_mask,  keys=[10,0,0,3], table_id=1)
        datapath.send_msg(msg)

    def del_state_entry(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        (state, state_mask) = parser.substate(state=2,section=1,sec_count=1)
        msg = datapath.ofproto_parser.OFPExpMsgStateMod(
            datapath=datapath, command=ofproto.OFPSC_EXP_DEL_FLOW_STATE, state=state, state_mask=state_mask,  keys=[10,0,0,3], table_id=1)
        datapath.send_msg(msg)

    def send_key_lookup(self, datapath):
        ofp = datapath.ofproto
        key_lookup_extractor = datapath.ofproto_parser.OFPExpMsgKeyExtract(
            datapath=datapath, command=ofp.OFPSC_EXP_SET_L_EXTRACTOR,  fields=[ofp.OXM_OF_IPV4_SRC], table_id=1)
        datapath.send_msg(key_lookup_extractor)

    def send_key_update(self, datapath):
        ofp = datapath.ofproto
        key_update_extractor = datapath.ofproto_parser.OFPExpMsgKeyExtract(
            datapath=datapath, command=ofp.OFPSC_EXP_SET_U_EXTRACTOR,  fields=[ofp.OXM_OF_IPV4_SRC], table_id=1)
        datapath.send_msg(key_update_extractor)

    def send_state_stats_request(self, datapath):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser

        match = ofp_parser.OFPMatch(ipv4_src="10.0.0.2")
        #req = ofp_parser.OFPExpStateStatsMultipartRequest(datapath=datapath, table_id=0, match=None)
        #req = ofp_parser.OFPExpStateStatsMultipartRequest(datapath=datapath, table_id=ofproto.OFPTT_ALL, match=match)
        req = ofp_parser.OFPExpStateStatsMultipartRequest(datapath=datapath, match=None)
        datapath.send_msg(req)

    def send_global_state_stats_request(self, datapath):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser

        req = ofp_parser.OFPExpGlobalStateStatsMultipartRequest(datapath=datapath)
        datapath.send_msg(req)

    def send_flow_stats_request(self, datapath):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser

        cookie = cookie_mask = 0
        #match = ofp_parser.OFPMatch(in_port=1)
        req = ofp_parser.OFPFlowStatsRequest(datapath, 0,
                                             ofp.OFPTT_ALL,
                                             ofp.OFPP_ANY, ofp.OFPG_ANY,
                                             cookie, cookie_mask,
                                             match=None)
        datapath.send_msg(req)