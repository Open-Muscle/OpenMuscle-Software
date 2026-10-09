"""Windows firewall helpers: read the inbound rules that matter, and add or remove
the OpenMuscle rule on request.

Bands send UDP to this computer (3141 for data, 3140 for discovery beacons). A
fresh Windows install blocks unsolicited inbound UDP until a rule allows it, and
the rule is per network type (Private / Public / Domain). `openmuscle doctor`
reads the rules to say plainly whether that is the reason no band shows up;
`openmuscle firewall` prints and (only when asked) adds the one rule needed.

Nothing here changes anything on its own. Adding a rule needs an administrator,
so it is run through a UAC prompt the user sees and approves.
"""

import os
import re
import sys

from openmuscle import netutil

RULE_NAME = "OpenMuscle UDP 3140-3141"
PORTS = (3140, 3141)
# Programs that may own the sockets (python itself, or a packaged launcher).
_PROGRAM_NAMES = {"python.exe", "pythonw.exe", "openmuscle.exe"}


# ----- the exact commands ----------------------------------------------------

def add_rule_args(profiles="private,domain", name=RULE_NAME):
    """netsh arguments that add the inbound allow rule (profiles: a netsh list,
    e.g. "private,domain" or "any")."""
    return ["advfirewall", "firewall", "add", "rule", "name=" + name, "dir=in",
            "action=allow", "protocol=UDP",
            "localport=%d,%d" % PORTS, "profile=" + profiles]


def remove_rule_args(name=RULE_NAME):
    return ["advfirewall", "firewall", "delete", "rule", "name=" + name]


def command_text(args):
    """The command a person could paste into an administrator prompt."""
    parts = []
    for a in args:
        if a.startswith("name="):
            a = 'name="%s"' % a[5:]
        parts.append(a)
    return "netsh " + " ".join(parts)


def powershell_text(profiles="Private,Domain", name=RULE_NAME):
    """The same rule as one PowerShell line."""
    return ('New-NetFirewallRule -DisplayName "%s" -Direction Inbound -Protocol UDP '
            '-LocalPort 3140,3141 -Action Allow -Profile %s' % (name, profiles))


# ----- reading the rules -----------------------------------------------------

def _truthy(v):
    return (v or "").strip().lower() in ("yes", "true", "1", "enabled")


def parse_netsh_rules(text):
    """Parse `netsh advfirewall firewall show rule name=all dir=in verbose` (English
    output) into a list of dicts. Returns [] for output it does not recognise
    (another Windows language), which callers treat as "could not read"."""
    rules = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m = re.match(r"^\s*Rule Name:\s*(.*)$", line)
        if m:
            cur = {"name": m.group(1).strip()}
            rules.append(cur)
            continue
        if cur is None or ":" not in line or line.lstrip().startswith("-"):
            continue
        key, _sep, val = line.partition(":")
        key = key.strip().lower()
        if key in ("enabled", "direction", "profiles", "protocol", "localport",
                   "action", "program"):
            cur[key] = val.strip()
    out = []
    for r in rules:
        out.append({
            "name": r["name"],
            "enabled": _truthy(r.get("enabled", "yes")),
            "direction": r.get("direction", "in").lower(),
            "profiles": {p.strip().lower() for p in r.get("profiles", "all").split(",")
                         if p.strip()},
            "protocol": r.get("protocol", "any").lower(),
            "localport": r.get("localport", "any").lower(),
            "action": r.get("action", "allow").lower(),
            "program": r.get("program", "any").strip().lower(),
        })
    return out


def parse_current_profile(text):
    """(profile, state) from `netsh advfirewall show currentprofile`:
    ("public"|"private"|"domain"|None, "on"|"off"|None)."""
    profile = state = None
    m = re.search(r"^\s*(Domain|Private|Public) Profile Settings", text or "", re.M | re.I)
    if m:
        profile = m.group(1).lower()
    m = re.search(r"^\s*State\s+(ON|OFF)\b", text or "", re.M | re.I)
    if m:
        state = m.group(1).lower()
    return profile, state


def _port_covers(spec, port):
    spec = (spec or "any").strip().lower()
    if spec in ("any", "", "*"):
        return True
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, _s, hi = part.partition("-")
            if lo.strip().isdigit() and hi.strip().isdigit() and \
                    int(lo) <= port <= int(hi):
                return True
        elif part.isdigit() and int(part) == port:
            return True
    return False


def _program_matches(program):
    prog = (program or "any").strip().lower()
    if prog in ("any", "", "n/a", "*"):
        return True
    base = os.path.basename(prog.replace("\\", "/"))
    mine = {os.path.basename(p).lower() for p in
            (sys.executable, getattr(sys, "_base_executable", "") or "") if p}
    return base in _PROGRAM_NAMES or base in mine


def _profile_matches(profiles, profile):
    if profile is None or not profiles:
        return True
    return "all" in profiles or "any" in profiles or profile.lower() in profiles


def evaluate(rules, profile=None, state=None, ports=PORTS):
    """Is inbound UDP allowed on each port, on the active network type?

    Windows blocks inbound traffic unless an enabled allow rule matches, and a
    matching block rule beats any allow. A rule matches when it is inbound,
    covers UDP, covers the port (a port list/range, or any port for a rule
    tied to python.exe) and applies to the active profile. Returns
      {"ports": {3140: bool, 3141: bool}, "profile": ..., "state": ...,
       "blocked_by": [rule names], "other_profile_rule": bool}
    """
    result = {"ports": {}, "profile": profile, "state": state,
              "blocked_by": [], "other_profile_rule": False}
    for port in ports:
        allowed = blocked = False
        for r in rules:
            if not r["enabled"] or r["direction"] not in ("in", "inbound"):
                continue
            if r["protocol"] not in ("udp", "any", "17"):
                continue
            if not _port_covers(r["localport"], port) or not _program_matches(r["program"]):
                continue
            if not _profile_matches(r["profiles"], profile):
                if r["action"] == "allow":
                    result["other_profile_rule"] = True
                continue
            if r["action"] == "block":
                blocked = True
                if r["name"] not in result["blocked_by"]:
                    result["blocked_by"].append(r["name"])
            elif r["action"] == "allow":
                allowed = True
        result["ports"][port] = (allowed and not blocked) or state == "off"
    return result


def read_rules(timeout=8.0):
    """(rules or None, profile, state). None when netsh could not be read."""
    rc, out = netutil.run_cmd(
        ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in",
         "verbose"], timeout)
    rules = parse_netsh_rules(out) if rc == 0 else []
    _rc, cur = netutil.run_cmd(["netsh", "advfirewall", "show", "currentprofile"], 2.0)
    profile, state = parse_current_profile(cur)
    return (rules or None), profile, state


def inspect(timeout=8.0):
    """Read the rules and evaluate them for the active profile.
    Returns the evaluate() dict plus "readable" (False when output was not understood)."""
    rules, profile, state = read_rules(timeout)
    if rules is None:
        return {"readable": False, "ports": {}, "profile": profile, "state": state,
                "blocked_by": [], "other_profile_rule": False, "rules": 0}
    res = evaluate(rules, profile, state)
    res["readable"] = True
    res["rules"] = len(rules)
    return res


# ----- changing the rules (only on request, through a UAC prompt) -------------

def quote_args(args):
    """One command-line string for `args`, quoted the way Windows programs parse
    it. Start-Process joins an -ArgumentList array with plain spaces and quotes
    nothing, so `name=OpenMuscle UDP 3140-3141` would reach netsh as three
    arguments; passing one explicitly quoted string avoids that."""
    parts = []
    for a in args:
        if re.search(r"[\s\"]", a):
            m = re.match(r"^([A-Za-z]+=)(.*)$", a, re.S)
            key, val = (m.group(1), m.group(2)) if m else ("", a)
            a = '%s"%s"' % (key, val.replace('"', '\\"'))
        parts.append(a)
    return " ".join(parts)


def start_process_script(program, args, elevated=True):
    """The PowerShell that runs `program` with `args` and exits with its code.
    `elevated` adds -Verb RunAs (the UAC prompt)."""
    def lit(s):
        return "'%s'" % s.replace("'", "''")
    return ("$p = Start-Process -FilePath %s -ArgumentList %s %s-Wait -PassThru "
            "-WindowStyle Hidden; exit $p.ExitCode"
            % (lit(program), lit(quote_args(args)), "-Verb RunAs " if elevated else ""))


def run_elevated(netsh_args, timeout=120.0):
    """Run netsh with `netsh_args` as administrator via a UAC prompt.
    Returns True when the process ran and exited 0. Windows only."""
    if not netutil.IS_WINDOWS:
        return False
    ps = start_process_script("netsh", netsh_args)
    rc, _out = netutil.run_cmd(["powershell", "-NoProfile", "-NonInteractive",
                                "-Command", ps], timeout)
    return rc == 0
