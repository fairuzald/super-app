"""Cross-platform helpers: adb mDNS discovery (used where there is no avahi), QR, process stop."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import osdeps  # noqa: E402

OUT = """List of discovered mdns services
adb-R58M123-AbCdEf\t_adb-tls-connect._tcp\t192.168.0.12:37891
adb-R58M123-XyZ\t_adb-tls-pairing._tcp\t192.168.0.12:41234
garbage line
"""


def test_adb_mdns_reports_new_and_gone_devices(monkeypatch):
    seen, gone = [], []
    outputs = [OUT, "List of discovered mdns services\n"]
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, outputs.pop(0), ""))
    m = osdeps.AdbMdns(lambda: "adb", interval=3600)
    m.watch[1] = ("_adb-tls-connect._tcp", lambda n, ip, p: seen.append((n, ip, p)), gone.append, {})
    m._scan()                      # posts _apply to the main loop; call it directly here
    m._apply({"_adb-tls-connect._tcp": {"adb-R58M123-AbCdEf": ("192.168.0.12", 37891)}})
    assert seen == [("adb-R58M123-AbCdEf", "192.168.0.12", 37891)]
    m._apply({})
    assert gone == ["adb-R58M123-AbCdEf"]


def test_adb_mdns_line_format():
    m = osdeps.AdbMdns.LINE.match("adb-ABC-def\t_adb-tls-connect._tcp\t10.0.0.5:5555")
    assert m and m.group(1) == "adb-ABC-def" and m.group(3) == "10.0.0.5" and m.group(4) == "5555"
    assert not osdeps.AdbMdns.LINE.match("adb-ABC\t_adb-tls-connect._tcp\tnot-an-ip:1; reboot")


def test_qr_png(tmp_path):
    path = tmp_path / "qr.png"
    if not osdeps.write_qr_png("WIFI:T:ADB;S:test;P:123456;;", str(path)):
        pytest.skip("no QR encoder installed")
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_stop_process_ends_a_child():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **osdeps.NO_WINDOW)
    osdeps.stop_process(p)
    assert p.wait(timeout=10) is not None


def test_feature_table_matches_os():
    assert all(osdeps.FEATURES.values()) == osdeps.IS_LINUX
    assert osdeps.find_adb().endswith("adb" + osdeps.EXE)
