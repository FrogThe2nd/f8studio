from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

from scapy.all import Ether, IP, UDP, Raw


def _load_udp_pcap_replay_module() -> object:
    script_path = Path("scripts/udp_pcap_replay.py").resolve()
    spec = importlib.util.spec_from_file_location("udp_pcap_replay", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load udp_pcap_replay module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class UdpPcapReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_udp_pcap_replay_module()

    def _options(self, *, dry_run: bool = True) -> object:
        return self.module.ReplayOptions(
            pcap_path=Path("capture.pcapng"),
            dst_ip="127.0.0.1",
            dst_port=39540,
            src_ip=None,
            src_port=None,
            preserve_timing=False,
            fixed_rate_hz=0.0,
            speed=1.0,
            max_delay_s=1.0,
            repeat=1,
            loop_delay_s=0.0,
            limit=None,
            dedupe_window_s=0.001,
            progress_interval_s=0.5,
            verbose_packets=False,
            dry_run=dry_run,
            quiet=True,
        )

    def test_parse_repeat_zero_means_forever(self) -> None:
        options = self.module.parse_args(["capture.pcapng", "--repeat", "0"])

        self.assertEqual(options.repeat, 0)

    def test_parse_preserves_capture_timing_by_default(self) -> None:
        options = self.module.parse_args(["capture.pcapng"])

        self.assertTrue(options.preserve_timing)
        self.assertEqual(options.max_delay_s, 0.0)

    def test_parse_can_disable_capture_timing(self) -> None:
        options = self.module.parse_args(["capture.pcapng", "--no-preserve-timing"])

        self.assertFalse(options.preserve_timing)

    def test_parse_fixed_rate_hz(self) -> None:
        options = self.module.parse_args(["capture.pcapng", "--fixed-rate-hz", "60"])

        self.assertEqual(options.fixed_rate_hz, 60.0)

    def test_parse_rejects_negative_repeat(self) -> None:
        with self.assertRaises(SystemExit):
            self.module.parse_args(["capture.pcapng", "--repeat", "-1"])

    def test_parse_rejects_negative_fixed_rate_hz(self) -> None:
        with self.assertRaises(SystemExit):
            self.module.parse_args(["capture.pcapng", "--fixed-rate-hz", "-60"])

    def test_select_udp_packets_strips_ether_and_applies_overrides_and_dedupe(self) -> None:
        first = Ether() / IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=20000) / Raw(b"pose")
        second = Ether() / IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=20000) / Raw(b"pose")
        third = Ether() / IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=20001) / Raw(b"next-pose")
        first.time = 10.0
        second.time = 10.0005
        third.time = 10.0007

        read_packets, udp_packets, selected_packets = self.module._select_udp_packets(
            [first, second, third],
            self._options(),
        )

        self.assertEqual(read_packets, 3)
        self.assertEqual(udp_packets, 3)
        self.assertEqual(len(selected_packets), 2)
        self.assertEqual(selected_packets[0].dst_ip, "127.0.0.1")
        self.assertEqual(selected_packets[0].dst_port, 39540)
        self.assertEqual(selected_packets[0].payload_len, 4)
        self.assertIsNotNone(selected_packets[0].packet[IP])
        self.assertNotIn(Ether, selected_packets[0].packet)

    def test_replay_dry_run_does_not_send(self) -> None:
        packet = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose")
        packet.time = 10.0

        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(self.module, "rdpcap", return_value=[packet]),
            mock.patch.object(self.module, "send") as send_mock,
        ):
            summary = self.module.replay_udp_pcap(self._options(dry_run=True))

        self.assertEqual(summary.sent_packets, 1)
        send_mock.assert_not_called()

    def test_fixed_rate_packet_batches_group_capture_windows(self) -> None:
        first = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-1")
        second = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-2")
        third = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-3")
        first.time = 10.0
        second.time = 10.010
        third.time = 10.040

        _read_packets, _udp_packets, selected_packets = self.module._select_udp_packets(
            [first, second, third],
            self._options(),
        )

        batches = self.module._fixed_rate_packet_batches(selected_packets, fixed_rate_hz=30.0)

        self.assertEqual([len(batch.packets) for batch in batches], [2, 1])

    def test_replay_fixed_rate_sleeps_once_per_non_initial_batch(self) -> None:
        first = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-1")
        second = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-2")
        third = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose-3")
        first.time = 10.0
        second.time = 10.010
        third.time = 10.040
        options = self._options(dry_run=True)
        options = self.module.ReplayOptions(
            pcap_path=options.pcap_path,
            dst_ip=options.dst_ip,
            dst_port=options.dst_port,
            src_ip=options.src_ip,
            src_port=options.src_port,
            preserve_timing=options.preserve_timing,
            fixed_rate_hz=30.0,
            speed=1.0,
            max_delay_s=options.max_delay_s,
            repeat=1,
            loop_delay_s=options.loop_delay_s,
            limit=options.limit,
            dedupe_window_s=0.0,
            progress_interval_s=options.progress_interval_s,
            verbose_packets=options.verbose_packets,
            dry_run=options.dry_run,
            quiet=options.quiet,
        )

        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(self.module, "rdpcap", return_value=[first, second, third]),
            mock.patch.object(self.module, "send") as send_mock,
            mock.patch.object(self.module.time, "sleep") as sleep_mock,
        ):
            summary = self.module.replay_udp_pcap(options)

        self.assertEqual(summary.sent_packets, 3)
        send_mock.assert_not_called()
        sleep_mock.assert_called_once_with(1.0 / 30.0)

    def test_repeat_zero_stops_cleanly_on_keyboard_interrupt(self) -> None:
        packet = IP(src="127.0.0.1", dst="127.0.0.1") / UDP(sport=10000, dport=39540) / Raw(b"pose")
        packet.time = 10.0
        options = self._options(dry_run=False)
        options = self.module.ReplayOptions(
            pcap_path=options.pcap_path,
            dst_ip=options.dst_ip,
            dst_port=options.dst_port,
            src_ip=options.src_ip,
            src_port=options.src_port,
            preserve_timing=options.preserve_timing,
            fixed_rate_hz=options.fixed_rate_hz,
            speed=options.speed,
            max_delay_s=options.max_delay_s,
            repeat=0,
            loop_delay_s=options.loop_delay_s,
            limit=options.limit,
            dedupe_window_s=options.dedupe_window_s,
            progress_interval_s=options.progress_interval_s,
            verbose_packets=options.verbose_packets,
            dry_run=options.dry_run,
            quiet=options.quiet,
        )

        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(self.module, "rdpcap", return_value=[packet]),
            mock.patch.object(self.module, "send", side_effect=[None, KeyboardInterrupt]),
        ):
            summary = self.module.replay_udp_pcap(options)

        self.assertEqual(summary.sent_packets, 1)


if __name__ == "__main__":
    unittest.main()
