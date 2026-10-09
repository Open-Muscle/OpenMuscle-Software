"""Windows firewall reading and the rule helper, from canned netsh output."""

import json
import sys

import pytest

from openmuscle import firewall as fw, netutil


def _rule(name, ports="Any", action="Allow", profiles="Private,Public,Domain",
          protocol="UDP", program="Any", enabled="Yes", direction="In"):
    return f"""Rule Name:                            {name}
----------------------------------------------------------------------
Enabled:                              {enabled}
Direction:                            {direction}
Profiles:                             {profiles}
Grouping:
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             {protocol}
LocalPort:                            {ports}
RemotePort:                           Any
Edge traversal:                       No
Action:                               {action}
Program:                              {program}

"""


NOISE = (_rule("Core Networking - DHCP", ports="68", profiles="Domain,Private,Public",
               program=r"C:\Windows\system32\svchost.exe")
         + _rule("mDNS", ports="5353", program=r"C:\Windows\system32\svchost.exe"))


def test_parse_reads_rules():
    rules = fw.parse_netsh_rules(NOISE + _rule("OpenMuscle UDP 3140-3141",
                                               ports="3140,3141"))
    assert [r["name"] for r in rules][-1] == "OpenMuscle UDP 3140-3141"
    last = rules[-1]
    assert last["enabled"] and last["action"] == "allow" and last["protocol"] == "udp"
    assert last["profiles"] == {"private", "public", "domain"}


def test_parse_unknown_language_gives_nothing():
    assert fw.parse_netsh_rules("Nom de la regle : x\nActivee : Oui\n") == []


def test_no_inbound_rule_blocks_both_ports():
    res = fw.evaluate(fw.parse_netsh_rules(NOISE), profile="private")
    assert res["ports"] == {3140: False, 3141: False}


def test_rule_for_3141_only_leaves_beacons_blocked():
    rules = fw.parse_netsh_rules(NOISE + _rule("OpenMuscle data", ports="3141"))
    res = fw.evaluate(rules, profile="private")
    assert res["ports"][3141] is True
    assert res["ports"][3140] is False


def test_port_list_and_range_both_count():
    for spec in ("3140,3141", "3140-3141", "3000-4000", "Any"):
        res = fw.evaluate(fw.parse_netsh_rules(_rule("r", ports=spec)), "private")
        assert res["ports"] == {3140: True, 3141: True}, spec


def test_python_program_rule_with_any_port_allows():
    rules = fw.parse_netsh_rules(_rule(
        "python.exe", program=r"C:\Python313\python.exe", profiles="Private"))
    assert fw.evaluate(rules, "private")["ports"] == {3140: True, 3141: True}


def test_other_programs_rule_does_not_count():
    rules = fw.parse_netsh_rules(_rule("chrome", program=r"C:\x\chrome.exe"))
    assert fw.evaluate(rules, "private")["ports"] == {3140: False, 3141: False}


def test_rule_for_other_network_type_is_called_out():
    rules = fw.parse_netsh_rules(_rule("OpenMuscle", ports="3140,3141",
                                       profiles="Private"))
    res = fw.evaluate(rules, profile="public")
    assert res["ports"] == {3140: False, 3141: False}
    assert res["other_profile_rule"] is True
    assert fw.evaluate(rules, profile="private")["ports"][3140] is True


def test_block_rule_beats_allow():
    rules = fw.parse_netsh_rules(
        _rule("allow", ports="3140,3141")
        + _rule("python.exe", program=r"C:\Python313\python.exe", action="Block"))
    res = fw.evaluate(rules, "private")
    assert res["ports"] == {3140: False, 3141: False}
    assert "python.exe" in res["blocked_by"]


def test_disabled_and_outbound_rules_are_ignored():
    rules = fw.parse_netsh_rules(
        _rule("off", ports="3140,3141", enabled="No")
        + _rule("out", ports="3140,3141", direction="Out"))
    assert fw.evaluate(rules, "private")["ports"] == {3140: False, 3141: False}


def test_firewall_switched_off_allows_everything():
    res = fw.evaluate(fw.parse_netsh_rules(NOISE), "private", state="off")
    assert res["ports"] == {3140: True, 3141: True}


def test_parse_current_profile():
    out = "Public Profile Settings: \n----------\nState                                 ON\n"
    assert fw.parse_current_profile(out) == ("public", "on")
    assert fw.parse_current_profile("nothing") == (None, None)


def test_the_exact_commands():
    cmd = fw.command_text(fw.add_rule_args())
    assert cmd == ('netsh advfirewall firewall add rule name="OpenMuscle UDP 3140-3141" '
                   'dir=in action=allow protocol=UDP localport=3140,3141 '
                   'profile=private,domain')
    assert "profile=any" in fw.command_text(fw.add_rule_args("any"))
    assert "delete rule" in fw.command_text(fw.remove_rule_args())
    assert "New-NetFirewallRule" in fw.powershell_text()


def test_inspect_reads_through_run_cmd(monkeypatch):
    calls = []

    def fake_run(args, timeout=4.0):
        calls.append(args)
        if "show" in args and "rule" in args:
            return 0, _rule("OpenMuscle", ports="3140,3141", profiles="Private")
        return 0, "Private Profile Settings:\nState   ON\n"

    monkeypatch.setattr(fw.netutil, "run_cmd", fake_run)
    res = fw.inspect()
    assert res["readable"] and res["profile"] == "private"
    assert res["ports"] == {3140: True, 3141: True}


def test_inspect_unreadable_output(monkeypatch):
    monkeypatch.setattr(fw.netutil, "run_cmd", lambda *a, **k: (None, ""))
    assert fw.inspect()["readable"] is False


def test_quote_args_keeps_the_rule_name_whole():
    line = fw.quote_args(fw.add_rule_args())
    assert 'name="OpenMuscle UDP 3140-3141"' in line
    assert line.startswith("advfirewall firewall add rule ")
    assert "localport=3140,3141" in line
    assert fw.quote_args(["a", "b c", "d=e f"]) == 'a "b c" d="e f"'


def test_start_process_script_passes_one_quoted_string():
    ps = fw.start_process_script("netsh", fw.remove_rule_args())
    assert "-Verb RunAs" in ps
    assert "-ArgumentList 'advfirewall firewall delete rule name=\"OpenMuscle UDP 3140-3141\"'" in ps
    assert "-Verb" not in fw.start_process_script("netsh", ["x"], elevated=False)
    assert "'it''s'" in fw.start_process_script("it's", [])


@pytest.mark.skipif(not netutil.IS_WINDOWS, reason="Start-Process is Windows PowerShell")
def test_real_start_process_delivers_the_name_as_one_argument(tmp_path):
    """Run the real builder through PowerShell against a stub that records argv
    (the elevation verb is left off so no prompt appears)."""
    out = tmp_path / "argv.json"
    stub = tmp_path / "stub.py"
    stub.write_text("import json, sys\njson.dump(sys.argv[2:], open(sys.argv[1], 'w'))\n")
    args = [str(stub), str(out)] + fw.add_rule_args()
    ps = fw.start_process_script(sys.executable, args, elevated=False)
    rc, _ = netutil.run_cmd(["powershell", "-NoProfile", "-NonInteractive",
                             "-Command", ps], 60.0)
    assert rc == 0
    got = json.loads(out.read_text())
    assert "name=OpenMuscle UDP 3140-3141" in got
    assert got == fw.add_rule_args()
