from swsscommon import swsscommon
import os
import re
import time
import json

# Note: all test cases in this file are supposed to run in sequence together, fow now.

def test_OrchagentWarmRestartReadyCheck(dvs):

    dvs.runcmd("config warm_restart enable swss")
    # hostcfgd not running in VS, create the folder explicitly
    dvs.runcmd("mkdir -p /etc/sonic/warm_restart/swss")

    dvs.runcmd("ifconfig Ethernet0 10.0.0.0/31 up")
    dvs.runcmd("ifconfig Ethernet4 10.0.0.2/31 up")

    dvs.servers[0].runcmd("ifconfig eth0 10.0.0.1/31")
    dvs.servers[0].runcmd("ip route add default via 10.0.0.0")

    dvs.servers[1].runcmd("ifconfig eth0 10.0.0.3/31")
    dvs.servers[1].runcmd("ip route add default via 10.0.0.2")


    db = swsscommon.DBConnector(swsscommon.APPL_DB, dvs.redis_sock, 0)
    ps = swsscommon.ProducerStateTable(db, "ROUTE_TABLE")
    fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.1"), ("ifname", "Ethernet0")])

    ps.set("2.2.2.0/24", fvs)

    time.sleep(1)
    #
    result =  dvs.runcmd("/usr/bin/orchagent_restart_check")
    assert result == "RESTARTCHECK failed\n"

    # get neighbor and arp entry
    dvs.servers[1].runcmd("ping -c 1 10.0.0.1")

    time.sleep(1)
    result =  dvs.runcmd("/usr/bin/orchagent_restart_check")
    assert result == "RESTARTCHECK succeeded\n"


def test_swss_warm_restore(dvs):

    # syncd warm start with temp view not supported yet
    if dvs.tmpview == True:
        return

    dvs.runcmd("/usr/bin/stop_swss.sh")
    time.sleep(3)
    dvs.runcmd("mv /var/log/swss/sairedis.rec /var/log/swss/sairedis.rec.b")
    dvs.runcmd("/usr/bin/swss-flushdb")
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # No create/set/remove operations should be passed down to syncd for swss restore
    num = dvs.runcmd(['sh', '-c', 'grep \|c\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'
    num = dvs.runcmd(['sh', '-c', 'grep \|s\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'
    num = dvs.runcmd(['sh', '-c', 'grep \|r\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'

    db = swsscommon.DBConnector(0, dvs.redis_sock, 0)

    warmtbl = swsscommon.Table(db, "WARM_RESTART_TABLE")

    keys = warmtbl.getKeys()
    print(keys)

    # restart_count for each process in SWSS should be 1
    for key in ['vlanmgrd', 'portsyncd', 'orchagent', 'neighsyncd']:
        (status, fvs) = warmtbl.get(key)
        assert status == True
        for fv in fvs:
            if fv[0] == "restart_count":
                assert fv[1] == "1"
            elif fv[0] == "state_restored":
                assert fv[1] == "true"

def test_swss_port_state_syncup(dvs):
    # syncd warm start with temp view not supported yet
    if dvs.tmpview == True:
        return

    dvs.runcmd("/usr/bin/stop_swss.sh")
    time.sleep(3)
    dvs.runcmd("mv /var/log/swss/sairedis.rec /var/log/swss/sairedis.rec.b")

    # Change port state before swss up again
    dvs.runcmd("ifconfig Ethernet0 10.0.0.0/31 up")
    dvs.runcmd("ifconfig Ethernet4 10.0.0.2/31 up")
    dvs.runcmd("ifconfig Ethernet8 10.0.0.4/31 up")

    dvs.runcmd("arp -s 10.0.0.1 00:00:00:00:00:01")
    dvs.runcmd("arp -s 10.0.0.3 00:00:00:00:00:02")
    dvs.runcmd("arp -s 10.0.0.5 00:00:00:00:00:03")

    dvs.servers[0].runcmd("ip link set down dev eth0") == 0
    dvs.servers[1].runcmd("ip link set down dev eth0") == 0
    dvs.servers[2].runcmd("ip link set down dev eth0") == 0
    dvs.servers[2].runcmd("ip link set up dev eth0") == 0

    time.sleep(1)
    dvs.runcmd("/usr/bin/swss-flushdb")
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    db = swsscommon.DBConnector(0, dvs.redis_sock, 0)

    warmtbl = swsscommon.Table(db, "WARM_RESTART_TABLE")

    # restart_count for each process in SWSS should be 2
    keys = warmtbl.getKeys()
    print(keys)
    for key in keys:
        (status, fvs) = warmtbl.get(key)
        assert status == True
        for fv in fvs:
            if fv[0] == "restart_count":
                assert fv[1] == "2"
            elif fv[0] == "state_restored":
                assert fv[1] == "true"

    tbl = swsscommon.Table(db, "PORT_TABLE")

    for i in [0, 1, 2]:
        (status, fvs) = tbl.get("Ethernet%d" % (i * 4))
        assert status == True

        oper_status = "unknown"

        for v in fvs:
            if v[0] == "oper_status":
                oper_status = v[1]
                break
        if i == 2:
            assert oper_status == "up"
        else:
            assert oper_status == "down"


def create_entry(tbl, key, pairs):
    fvs = swsscommon.FieldValuePairs(pairs)
    tbl.set(key, fvs)

    # FIXME: better to wait until DB create them
    time.sleep(1)

def create_entry_tbl(db, table, key, pairs):
    tbl = swsscommon.Table(db, table)
    create_entry(tbl, key, pairs)

def del_entry_tbl(db, table, key):
    tbl = swsscommon.Table(db, table)
    tbl._del(key)

def create_entry_pst(db, table, key, pairs):
    tbl = swsscommon.ProducerStateTable(db, table)
    create_entry(tbl, key, pairs)

def how_many_entries_exist(db, table):
    tbl =  swsscommon.Table(db, table)
    return len(tbl.getKeys())

def getCrmCounterValue(dvs, key, counter):

    counters_db = swsscommon.DBConnector(swsscommon.COUNTERS_DB, dvs.redis_sock, 0)
    crm_stats_table = swsscommon.Table(counters_db, 'CRM')

    for k in crm_stats_table.get(key)[1]:
        if k[0] == counter:
            return int(k[1])
    return 0

def test_swss_fdb_syncup_and_crm(dvs):
    # syncd warm start with temp view not supported yet
    if dvs.tmpview == True:
        return

    # Prepare FDB entry before swss stop
    appl_db = swsscommon.DBConnector(swsscommon.APPL_DB, dvs.redis_sock, 0)
    asic_db = swsscommon.DBConnector(swsscommon.ASIC_DB, dvs.redis_sock, 0)
    conf_db = swsscommon.DBConnector(swsscommon.CONFIG_DB, dvs.redis_sock, 0)

    # create a FDB entry in Application DB
    create_entry_pst(
        appl_db,
        "FDB_TABLE", "Vlan12:52-54-00-25-06-E9",
        [
            ("port", "Ethernet12"),
            ("type", "dynamic"),
        ]
    )
    # create vlan
    create_entry_tbl(
        conf_db,
        "VLAN", "Vlan12",
        [
            ("vlanid", "12"),
        ]
    )

    # create vlan member entry in application db. Don't use Ethernet0/4/8 as IP configured on them in previous testing.
    create_entry_tbl(
        conf_db,
        "VLAN_MEMBER", "Vlan12|Ethernet12",
         [
            ("tagging_mode", "untagged"),
         ]
    )
    # check that the FDB entry was inserted into ASIC DB
    assert how_many_entries_exist(asic_db, "ASIC_STATE:SAI_OBJECT_TYPE_FDB_ENTRY") == 1, "The fdb entry wasn't inserted to ASIC"

    dvs.runcmd("crm config polling interval 1")
    time.sleep(2)
    # get counters
    used_counter = getCrmCounterValue(dvs, 'STATS', 'crm_stats_fdb_entry_used')
    assert used_counter == 1

    # Change the polling interval to 20 so we may see the crm counter changes after warm restart
    dvs.runcmd("crm config polling interval 20")

    dvs.runcmd("/usr/bin/stop_swss.sh")

    # delete the FDB entry in AppDB before swss is started again,
    # the orchagent is supposed to sync up the entry from ASIC DB after warm restart
    del_entry_tbl(appl_db, "FDB_TABLE", "Vlan12:52-54-00-25-06-E9")


    time.sleep(1)
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # restart_count for each process in SWSS should be 3
    warmtbl = swsscommon.Table(appl_db, "WARM_START_TABLE")
    keys = warmtbl.getKeys()
    print(keys)
    for key in keys:
        (status, fvs) = warmtbl.get(key)
        assert status == True
        for fv in fvs:
            if fv[0] == "restart_count":
                assert fv[1] == "3"
            elif fv[0] == "state_restored":
                assert fv[1] == "true"

    # get counters for FDB entries, it should be 0
    used_counter = getCrmCounterValue(dvs, 'STATS', 'crm_stats_fdb_entry_used')
    assert used_counter == 0
    dvs.runcmd("crm config polling interval 10")
    time.sleep(20)
     # get counters for FDB entries, it should be 1
    used_counter = getCrmCounterValue(dvs, 'STATS', 'crm_stats_fdb_entry_used')
    assert used_counter == 1


def test_VlanMgrWarmRestart(dvs):

    conf_db = swsscommon.DBConnector(swsscommon.CONFIG_DB, dvs.redis_sock, 0)
    appl_db = swsscommon.DBConnector(swsscommon.APPL_DB, dvs.redis_sock, 0)

    dvs.runcmd("ifconfig Ethernet16  up")
    dvs.runcmd("ifconfig Ethernet20  up")

    # create vlan
    create_entry_tbl(
        conf_db,
        "VLAN", "Vlan16",
        [
            ("vlanid", "16"),
        ]
    )
    # create vlan
    create_entry_tbl(
        conf_db,
        "VLAN", "Vlan20",
        [
            ("vlanid", "20"),
        ]
    )
    # create vlan member entry in config db. Don't use Ethernet0/4/8/12 as IP configured on them in previous testing.
    create_entry_tbl(
        conf_db,
        "VLAN_MEMBER", "Vlan16|Ethernet16",
         [
            ("tagging_mode", "untagged"),
         ]
    )

    create_entry_tbl(
        conf_db,
        "VLAN_MEMBER", "Vlan20|Ethernet20",
         [
            ("tagging_mode", "untagged"),
         ]
    )

    time.sleep(1)

    dvs.runcmd("ifconfig Vlan16 11.0.0.1/29 up")
    dvs.runcmd("ifconfig Vlan20 11.0.0.9/29 up")

    dvs.servers[4].runcmd("ifconfig eth0 11.0.0.2/29")
    dvs.servers[4].runcmd("ip route add default via 11.0.0.1")

    dvs.servers[5].runcmd("ifconfig eth0 11.0.0.10/29")
    dvs.servers[5].runcmd("ip route add default via 11.0.0.9")

    time.sleep(1)

    # Ping should work between servers via vs vlan interfaces
    ping_stats = dvs.servers[4].runcmd("ping -c 1 11.0.0.10")
    time.sleep(1)

    tbl = swsscommon.Table(appl_db, "NEIGH_TABLE")
    (status, fvs) = tbl.get("Vlan16:11.0.0.2")
    assert status == True

    (status, fvs) = tbl.get("Vlan20:11.0.0.10")
    assert status == True


    bv_before = dvs.runcmd("bridge vlan")
    print(bv_before)

   # dvs.runcmd("pkill -x vlanmgrd")
    #dvs.runcmd("cp /var/log/swss/sairedis.rec /var/log/swss/sairedis.rec.b; echo > /var/log/swss/sairedis.rec")
    dvs.runcmd(['sh', '-c', 'pkill -x vlanmgrd; cp /var/log/swss/sairedis.rec /var/log/swss/sairedis.rec.b; echo > /var/log/swss/sairedis.rec'])
    dvs.runcmd("supervisorctl start vlanmgrd")
    time.sleep(2)

    bv_after = dvs.runcmd("bridge vlan")
    assert bv_after == bv_before

     # No create/set/remove operations should be passed down to syncd for vlanmgr warm restart
    num = dvs.runcmd(['sh', '-c', 'grep \|c\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'
    num = dvs.runcmd(['sh', '-c', 'grep \|s\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'
    num = dvs.runcmd(['sh', '-c', 'grep \|r\| /var/log/swss/sairedis.rec | wc -l'])
    assert num == '0\n'

    #new ip on server 5
    dvs.servers[5].runcmd("ifconfig eth0 11.0.0.11/29")

    # Ping should work between servers via vs vlan interfaces
    ping_stats = dvs.servers[4].runcmd("ping -c 1 11.0.0.11")

    # new neighbor learn on VS
    (status, fvs) = tbl.get("Vlan20:11.0.0.11")
    assert status == True

    # restart_count for each process in vlanmgr should be 4 now
    warmtbl = swsscommon.Table(appl_db, "WARM_START_TABLE")
    keys = warmtbl.getKeys()
    for key in keys:
        if key != "vlanmgrd":
            continue
        (status, fvs) = warmtbl.get(key)
        assert status == True
        for fv in fvs:
            if fv[0] == "restart_count":
                assert fv[1] == "4"
            elif fv[0] == "state_restored":
                assert fv[1] == "true"

    dvs.runcmd("config warm_restart disable swss")
    # hostcfgd not running in VS, rm the folder explicitly
    dvs.runcmd("rm -f -r /etc/sonic/warm_restart/swss")


# function to check the restart counter
def check_restart_cnt(warmtbl, restart_cnt):
    keys = warmtbl.getKeys()
    print(keys)
    for key in keys:
        (status, fvs) = warmtbl.get(key)
        assert status == True
        for fv in fvs:
            if fv[0] == "restart_count":
                assert fv[1] == str(restart_cnt)
            elif fv[0] == "state_restored":
                assert fv[1] == "true"


# function to stop swss service and clear syslog and sairedis records
def stop_swss_clear_syslog_sairedis(dvs, save_number):
    dvs.runcmd("/usr/bin/stop_swss.sh")
    time.sleep(3)
    dvs.runcmd("mv /var/log/swss/sairedis.rec /var/log/swss/sairedis.rec.back1")
    dvs.runcmd("cp /var/log/syslog /var/log/syslog.back{}".format(save_number))
    dvs.runcmd(['sh', '-c', '> /var/log/syslog'])


# function to check neighbor entry reconciliation status written in syslog
def check_syslog_for_neighbor_entry(dvs, new_cnt, delete_cnt, iptype):
    # check reconciliation results (new or delete entries) for ipv4 and ipv6
    if iptype == "ipv4":
        num = dvs.runcmd(['sh', '-c', 'grep neighsyncd /var/log/syslog| grep cache-state:NEW | grep IPv4 | wc -l'])
        assert num.strip() == str(new_cnt)
        num = dvs.runcmd(['sh', '-c', 'grep neighsyncd /var/log/syslog| grep cache-state:DELETE | grep IPv4 | wc -l'])
        assert num.strip() == str(delete_cnt)
    elif iptype == "ipv6":
        num = dvs.runcmd(['sh', '-c', 'grep neighsyncd /var/log/syslog| grep cache-state:NEW | grep IPv6 | wc -l'])
        assert num.strip() == str(new_cnt)
        num = dvs.runcmd(['sh', '-c', 'grep neighsyncd /var/log/syslog| grep cache-state:DELETE | grep IPv6 | wc -l'])
        assert num.strip() == str(delete_cnt)
    else:
        assert "iptype is unknown" == ""


# function to check sairedis record for neighbor entries
def check_sairedis_for_neighbor_entry(dvs, create_cnt, set_cnt, remove_cnt):
    # check create/set/remove operations for neighbor entries during warm restart
    num = dvs.runcmd(['sh', '-c', 'grep \|c\| /var/log/swss/sairedis.rec | grep NEIGHBOR_ENTRY | wc -l'])
    assert num.strip() == str(create_cnt)
    num = dvs.runcmd(['sh', '-c', 'grep \|s\| /var/log/swss/sairedis.rec | grep NEIGHBOR_ENTRY | wc -l'])
    assert num.strip() == str(set_cnt)
    num = dvs.runcmd(['sh', '-c', 'grep \|r\| /var/log/swss/sairedis.rec | grep NEIGHBOR_ENTRY | wc -l'])
    assert num.strip() == str(remove_cnt)


def test_swss_neighbor_syncup(dvs):
    # syncd warm start with temp view not supported yet
    if dvs.tmpview == True:
        return

    # previous warm restart cnt
    restart_cnt = 4

    # Prepare neighbor entry before swss stop
    appl_db = swsscommon.DBConnector(swsscommon.APPL_DB, dvs.redis_sock, 0)
    asic_db = swsscommon.DBConnector(swsscommon.ASIC_DB, dvs.redis_sock, 0)
    conf_db = swsscommon.DBConnector(swsscommon.CONFIG_DB, dvs.redis_sock, 0)

    #
    # Testcase1:
    # Add neighbor entries in linux kernel, appDB should get all of them
    #

    # create neighbor entries (4 ipv4 and 4 ip6, two each on each interface) in linux kernel
    intfs = ["Ethernet24", "Ethernet28"]
    #enable ipv6 on docker
    dvs.runcmd("sysctl net.ipv6.conf.all.disable_ipv6=0")

    dvs.runcmd("ifconfig {} 24.0.0.1/24 up".format(intfs[0]))
    dvs.runcmd("ip -6 addr add 2400::1/64 dev {}".format(intfs[0]))

    dvs.runcmd("ifconfig {} 28.0.0.1/24 up".format(intfs[1]))
    dvs.runcmd("ip -6 addr add 2800::1/64 dev {}".format(intfs[1]))

    ips = ["24.0.0.2", "24.0.0.3", "28.0.0.2", "28.0.0.3"]
    v6ips = ["2400::2", "2400::3", "2800::2", "2800::3"]

    macs = ["00:00:00:00:24:02", "00:00:00:00:24:03", "00:00:00:00:28:02", "00:00:00:00:28:03"]

    for i in range(len(ips)):
        dvs.runcmd("ip neigh add {} dev {} lladdr {}".format(ips[i], intfs[i%2], macs[i]))

    for i in range(len(v6ips)):
        dvs.runcmd("ip -6 neigh add {} dev {} lladdr {}".format(v6ips[i], intfs[i%2], macs[i]))

    time.sleep(1)

    # Check the neighbor entries are inserted correctly
    db = swsscommon.DBConnector(0, dvs.redis_sock, 0)
    tbl = swsscommon.Table(db, "NEIGH_TABLE")

    for i in range(len(ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], ips[i]))
        assert status == True

        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv4"

    for i in range(len(v6ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], v6ips[i]))
        assert status == True

        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv6"

    #
    # Testcase 2:
    # Restart swss without change neighbor entries, nothing should be sent to appDB or sairedis,
    # appDB should be kept the same.
    #

    # stop swss service and clear syslog and sairedis.rec
    stop_swss_clear_syslog_sairedis(dvs, 1)

    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # check restart_count for each process in SWSS
    restart_cnt += 1
    warmtbl = swsscommon.Table(appl_db, "WARM_START_TABLE")
    check_restart_cnt(warmtbl, restart_cnt)

    # Check the neighbor entries are still in appDB correctly
    for i in range(len(ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], ips[i]))
        assert status == True

        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv4"

    for i in range(len(v6ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], v6ips[i]))
        assert status == True

        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv6"

    # check syslog and sairedis.rec file for activities
    check_syslog_for_neighbor_entry(dvs, 0, 0, "ipv4")
    check_syslog_for_neighbor_entry(dvs, 0, 0, "ipv6")
    check_sairedis_for_neighbor_entry(dvs, 0, 0, 0)

    #
    # Testcase 3:
    # stop swss, delete even nummber ipv4/ipv6 neighbor entries from each interface, warm start swss.
    # the neighsyncd is supposed to sync up the entries from kernel after warm restart
    # note: there was an issue for neighbor delete, it will be marked as FAILED instead of deleted in kernel
    #       but it will send netlink message to be removed from appDB, so it works ok here,
    #       just that if we want to add the same neighbor again, use "change" instead of "add"

    # stop swss service and clear syslog and sairedis.rec
    stop_swss_clear_syslog_sairedis(dvs, 2)

    # deledelete even nummber of ipv4/ipv6 neighbor entries from each interface
    for i in range(0, len(ips), 2):
        dvs.runcmd("ip neigh del {} dev {}".format(ips[i], intfs[i%2]))

    for i in range(0, len(v6ips), 2):
        dvs.runcmd("ip -6 neigh del {} dev {}".format(v6ips[i], intfs[i%2]))

    # start swss service again
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # check restart_count for each process in SWSS
    restart_cnt += 1
    warmtbl = swsscommon.Table(appl_db, "WARM_START_TABLE")
    check_restart_cnt(warmtbl, restart_cnt)

    # check ipv4 and ipv6 neighbors
    for i in range(len(ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], ips[i]))
        #should not see deleted neighbor entries
        if i %2 == 0:
            assert status == False
            continue
        else:
            assert status == True

        #undeleted entries should still be there.
        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv4"

    for i in range(len(v6ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], v6ips[i]))
        #should not see deleted neighbor entries
        if i %2 == 0:
            assert status == False
            continue
        else:
            assert status == True

        #undeleted entries should still be there.
        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv6"

    # check syslog and sairedis.rec file for activities
    # 2 deletes each for ipv4 and ipv6
    # 4 remove actions in sairedis
    check_syslog_for_neighbor_entry(dvs, 0, 2, "ipv4")
    check_syslog_for_neighbor_entry(dvs, 0, 2, "ipv6")
    check_sairedis_for_neighbor_entry(dvs, 0, 0, 4)

    #
    # Testcase 4:
    # Stop swss, add even nummber of ipv4/ipv6 neighbor entries to each interface again,
    # use "change" due to the kernel behaviour, start swss.
    # The neighsyncd is supposed to sync up the entries from kernel after warm restart

    # stop swss service and clear syslog and sairedis.rec
    stop_swss_clear_syslog_sairedis(dvs, 3)

    # add even nummber of ipv4/ipv6 neighbor entries to each interface
    for i in range(0, len(ips), 2):
        dvs.runcmd("ip neigh change {} dev {} lladdr {}".format(ips[i], intfs[i%2], macs[i]))

    for i in range(0, len(v6ips), 2):
        dvs.runcmd("ip -6 neigh change {} dev {} lladdr {}".format(v6ips[i], intfs[i%2], macs[i]))

    # start swss service again
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # check restart_count for each process in SWSS
    restart_cnt += 1
    check_restart_cnt(warmtbl, restart_cnt)

    # check ipv4 and ipv6 neighbors, should see all neighbors
    for i in range(len(ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], ips[i]))
        assert status == True
        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv4"

    for i in range(len(v6ips)):
        (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], v6ips[i]))
        assert status == True
        for v in fvs:
            if v[0] == "neigh":
                assert v[1] == macs[i]
            if v[0] == "family":
                assert v[1] == "IPv6"

    # check syslog and sairedis.rec file for activities
    # 2 news entries for ipv4 and ipv6 each
    # 4 create actions for sairedis
    check_syslog_for_neighbor_entry(dvs, 2, 0, "ipv4")
    check_syslog_for_neighbor_entry(dvs, 2, 0, "ipv6")
    check_sairedis_for_neighbor_entry(dvs, 4, 0, 0)

    #
    # Testcase 5:
    # Even number of ip4/6 neigbors updated with new mac.
    # Odd number of ipv4/6 neighbors removed and added to different interfaces.
    # neighbor syncd should sync it up after warm restart

    # stop swss service and clear syslog and sairedis.rec
    stop_swss_clear_syslog_sairedis(dvs, 4)

    # Even number of ip4/6 neigbors updated with new mac.
    # Odd number of ipv4/6 neighbors removed and added to different interfaces.
    newmacs = ["00:00:00:01:12:02", "00:00:00:01:12:03", "00:00:00:01:16:02", "00:00:00:01:16:03"]

    for i in range(len(ips)):
        if i % 2 == 0:
            dvs.runcmd("ip neigh change {} dev {} lladdr {}".format(ips[i], intfs[i%2], newmacs[i]))
        else:
            dvs.runcmd("ip neigh del {} dev {}".format(ips[i], intfs[i%2]))
            dvs.runcmd("ip neigh add {} dev {} lladdr {}".format(ips[i], intfs[1-i%2], macs[i]))

    for i in range(len(v6ips)):
        if i % 2 == 0:
            dvs.runcmd("ip -6 neigh change {} dev {} lladdr {}".format(v6ips[i], intfs[i%2], newmacs[i]))
        else:
            dvs.runcmd("ip -6 neigh del {} dev {}".format(v6ips[i], intfs[i%2]))
            dvs.runcmd("ip -6 neigh add {} dev {} lladdr {}".format(v6ips[i], intfs[1-i%2], macs[i]))

    # start swss service again
    dvs.runcmd("/usr/bin/start_swss.sh")
    time.sleep(10)

    # check restart_count for each process in SWSS
    restart_cnt += 1
    check_restart_cnt(warmtbl, restart_cnt)

    # check ipv4 and ipv6 neighbors, should see all neighbors with updated info
    for i in range(len(ips)):
        if i % 2 == 0:
            (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], ips[i]))
            assert status == True
            for v in fvs:
                if v[0] == "neigh":
                    assert v[1] == newmacs[i]
                if v[0] == "family":
                    assert v[1] == "IPv4"
        else:
            (status, fvs) = tbl.get("{}:{}".format(intfs[1-i%2], ips[i]))
            assert status == True
            for v in fvs:
                if v[0] == "neigh":
                    assert v[1] == macs[i]
                if v[0] == "family":
                    assert v[1] == "IPv4"

    for i in range(len(v6ips)):
        if i % 2 == 0:
            (status, fvs) = tbl.get("{}:{}".format(intfs[i%2], v6ips[i]))
            assert status == True
            for v in fvs:
                if v[0] == "neigh":
                    assert v[1] == newmacs[i]
                if v[0] == "family":
                    assert v[1] == "IPv6"
        else:
            (status, fvs) = tbl.get("{}:{}".format(intfs[1-i%2], v6ips[i]))
            assert status == True
            for v in fvs:
                if v[0] == "neigh":
                    assert v[1] == macs[i]
                if v[0] == "family":
                    assert v[1] == "IPv6"

    # check syslog and sairedis.rec file for activities
    # 4 news, 2 deletes for ipv4 and ipv6 each
    # 8 create, 4 set, 4 removes for sairedis
    check_syslog_for_neighbor_entry(dvs, 4, 2, "ipv4")
    check_syslog_for_neighbor_entry(dvs, 4, 2, "ipv6")
    check_sairedis_for_neighbor_entry(dvs, 4, 4, 4)
