"""
STL27L (LDROBOT) lidar driver.

Packet = 47 bytes, little endian:
    0       header 0x54
    1       ver/len 0x2C (12 points)
    2-3     speed (deg/s)
    4-5     start angle (0.01 deg)
    6-41    12 x [distance mm (2 bytes), intensity (1 byte)]
    42-43   end angle (0.01 deg)
    44-45   timestamp (ms)
    46      crc8

A background thread reads the serial port all the time and builds complete
360 degree scans, so the main program never sees half a rotation and the
USB buffer never overflows while the main loop is busy.
"""

import threading
import time
from collections import deque, namedtuple

import numpy as np
import serial


PACKET_SIZE = 47
POINTS_PER_PACKET = 12
HEADER = b"\x54\x2C"


CRC_TABLE = [
    0x00,0x4D,0x9A,0xD7,0x79,0x34,0xE3,0xAE,
    0xF2,0xBF,0x68,0x25,0x8B,0xC6,0x11,0x5C,
    0xA9,0xE4,0x33,0x7E,0xD0,0x9D,0x4A,0x07,
    0x5B,0x16,0xC1,0x8C,0x22,0x6F,0xB8,0xF5,
    0x1F,0x52,0x85,0xC8,0x66,0x2B,0xFC,0xB1,
    0xED,0xA0,0x77,0x3A,0x94,0xD9,0x0E,0x43,
    0xB6,0xFB,0x2C,0x61,0xCF,0x82,0x55,0x18,
    0x44,0x09,0xDE,0x93,0x3D,0x70,0xA7,0xEA,
    0x3E,0x73,0xA4,0xE9,0x47,0x0A,0xDD,0x90,
    0xCC,0x81,0x56,0x1B,0xB5,0xF8,0x2F,0x62,
    0x97,0xDA,0x0D,0x40,0xEE,0xA3,0x74,0x39,
    0x65,0x28,0xFF,0xB2,0x1C,0x51,0x86,0xCB,
    0x21,0x6C,0xBB,0xF6,0x58,0x15,0xC2,0x8F,
    0xD3,0x9E,0x49,0x04,0xAA,0xE7,0x30,0x7D,
    0x88,0xC5,0x12,0x5F,0xF1,0xBC,0x6B,0x26,
    0x7A,0x37,0xE0,0xAD,0x03,0x4E,0x99,0xD4,
    0x7C,0x31,0xE6,0xAB,0x05,0x48,0x9F,0xD2,
    0x8E,0xC3,0x14,0x59,0xF7,0xBA,0x6D,0x20,
    0xD5,0x98,0x4F,0x02,0xAC,0xE1,0x36,0x7B,
    0x27,0x6A,0xBD,0xF0,0x5E,0x13,0xC4,0x89,
    0x63,0x2E,0xF9,0xB4,0x1A,0x57,0x80,0xCD,
    0x91,0xDC,0x0B,0x46,0xE8,0xA5,0x72,0x3F,
    0xCA,0x87,0x50,0x1D,0xB3,0xFE,0x29,0x64,
    0x38,0x75,0xA2,0xEF,0x41,0x0C,0xDB,0x96,
    0x42,0x0F,0xD8,0x95,0x3B,0x76,0xA1,0xEC,
    0xB0,0xFD,0x2A,0x67,0xC9,0x84,0x53,0x1E,
    0xEB,0xA6,0x71,0x3C,0x92,0xDF,0x08,0x45,
    0x19,0x54,0x83,0xCE,0x60,0x2D,0xFA,0xB7,
    0x5D,0x10,0xC7,0x8A,0x24,0x69,0xBE,0xF3,
    0xAF,0xE2,0x35,0x78,0xD6,0x9B,0x4C,0x01,
    0xF4,0xB9,0x6E,0x23,0x8D,0xC0,0x17,0x5A,
    0x06,0x4B,0x9C,0xD1,0x7F,0x32,0xE5,0xA8
]


def crc8(data):
    crc = 0
    for b in data:
        crc = CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


# One full rotation. angle (deg), dist (mm), intensity, x, y (mm) are numpy
# arrays in scan order, only valid points. t = time.monotonic() when the
# rotation finished, step = degrees between two beams, hz = rotation speed.
Scan = namedtuple("Scan", "angle dist intensity x y t step hz")


# =========================
# BYTES -> SCANS
# =========================

class ScanAssembler:

    def __init__(self, max_range=5000, min_range=20, min_intensity=0, split_angle=0.0):

        self.max_range = max_range
        self.min_range = min_range
        self.min_intensity = min_intensity

        # a new scan starts when the beam passes this angle; put it where
        # nothing can be touched so a hand is never cut in two scans
        self.split_angle = split_angle

        self.new_split = None

        self.buffer = bytearray()
        self.packets = []
        self.last_rel = None
        self.crc_errors = 0


    def reset(self):

        self.buffer.clear()
        self.packets = []
        self.last_rel = None


    def set_split_angle(self, value):
        """Safe from another thread: applied before the next packet."""

        self.new_split = value % 360


    def feed(self, data, now=None):
        """Add raw bytes, return the list of scans that were completed."""

        self.buffer.extend(data)

        scans = []

        for packet in self._read_packets():

            if self.new_split is not None:
                # drop the half built scan, start clean at the new seam
                self.split_angle = self.new_split
                self.new_split = None
                self.packets = []
                self.last_rel = None

            start = (packet[4] | packet[5] << 8) / 100
            rel = (start - self.split_angle) % 360

            if self.last_rel is not None and rel < self.last_rel - 180:

                scan = self._build_scan(now)

                if scan is not None:
                    scans.append(scan)

            self.last_rel = rel
            self.packets.append(packet)

        return scans


    def _read_packets(self):

        buf = self.buffer
        pos = 0
        packets = []

        while True:

            pos = buf.find(HEADER, pos)

            if pos == -1:
                # keep the last byte, it may be the first half of a header
                pos = max(len(buf) - 1, 0)
                break

            if len(buf) - pos < PACKET_SIZE:
                break

            packet = bytes(buf[pos:pos + PACKET_SIZE])

            if crc8(packet[:46]) == packet[46]:
                packets.append(packet)
                pos += PACKET_SIZE
            else:
                self.crc_errors += 1
                pos += 1

        del buf[:pos]

        return packets


    def _build_scan(self, now):

        packets = self.packets
        self.packets = []

        if len(packets) < 10:
            return None

        raw = np.frombuffer(b"".join(packets), dtype=np.uint8).reshape(-1, PACKET_SIZE)
        raw16 = raw.astype(np.uint16)

        speed = (raw16[:, 2] | raw16[:, 3] << 8).mean()
        start = (raw16[:, 4] | raw16[:, 5] << 8) / 100
        end = (raw16[:, 42] | raw16[:, 43] << 8) / 100
        end = np.where(end < start, end + 360, end)
        step = (end - start) / (POINTS_PER_PACKET - 1)

        angle = (start[:, None] + step[:, None] * np.arange(POINTS_PER_PACKET)) % 360

        pts = raw16[:, 6:42].reshape(-1, POINTS_PER_PACKET, 3)
        dist = pts[:, :, 0] | pts[:, :, 1] << 8
        intensity = pts[:, :, 2]

        angle = angle.ravel()
        dist = dist.ravel().astype(np.float64)
        intensity = intensity.ravel().astype(np.uint8)

        beam_step = 360.0 / len(angle)

        valid = (
            (dist >= self.min_range) &
            (dist <= self.max_range) &
            (intensity >= self.min_intensity)
        )

        angle = angle[valid]
        dist = dist[valid]
        intensity = intensity[valid]

        r = np.radians(angle)

        return Scan(
            angle=angle,
            dist=dist,
            intensity=intensity,
            x=np.cos(r) * dist,
            y=np.sin(r) * dist,
            t=time.monotonic() if now is None else now,
            step=beam_step,
            hz=speed / 360.0,
        )



# =========================
# LIDAR
# =========================

class D800:

    def __init__(
        self,
        port,
        baud=921600,
        max_range=5000,
        min_range=20,
        min_intensity=0
    ):

        self.port = port
        self.baud = baud

        self.assembler = ScanAssembler(
            max_range=max_range,
            min_range=min_range,
            min_intensity=min_intensity
        )

        self.ser = None
        self.scans = deque(maxlen=50)
        self.cond = threading.Condition()
        self.running = True

        self.scan_count = 0
        self.dropped = 0

        self.connect()

        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()


    @property
    def split_angle(self):
        pending = self.assembler.new_split
        return self.assembler.split_angle if pending is None else pending


    @split_angle.setter
    def split_angle(self, value):
        self.assembler.set_split_angle(value)


    @property
    def crc_errors(self):
        return self.assembler.crc_errors


    # -------------------------
    # CONNECT
    # -------------------------

    def connect(self):

        waiting_printed = False

        print("Connecting lidar...")

        while self.running:

            try:

                self.ser = serial.Serial(
                    self.port,
                    self.baud,
                    bytesize=8,
                    parity=serial.PARITY_NONE,
                    stopbits=1,
                    timeout=0.05
                )

                try:
                    # Windows only: bigger driver buffer
                    self.ser.set_buffer_size(rx_size=1 << 16)
                except (AttributeError, serial.SerialException):
                    pass

                self.ser.reset_input_buffer()
                self.assembler.reset()

                print("Lidar connected")

                return True

            except serial.SerialException:

                if not waiting_printed:
                    print("Waiting for lidar USB...")
                    waiting_printed = True

                time.sleep(1)

        return False


    def disconnect(self):

        try:
            if self.ser:
                self.ser.close()
        except (serial.SerialException, OSError):
            pass

        self.ser = None


    # -------------------------
    # READER THREAD
    # -------------------------

    def _reader(self):

        while self.running:

            if self.ser is None:
                if not self.connect():
                    break

            try:
                data = self.ser.read(self.ser.in_waiting or 1)

            except (serial.SerialException, OSError, TypeError, AttributeError):

                if not self.running:
                    break

                print("Lidar disconnected")
                self.disconnect()
                self.assembler.reset()
                continue

            if not data:
                continue

            scans = self.assembler.feed(data)

            if scans:
                with self.cond:
                    self.scans.extend(scans)
                    self.scan_count += len(scans)
                    self.cond.notify_all()


    # -------------------------
    # PUBLIC
    # -------------------------

    def get_scans(self, timeout=0.2):
        """All complete scans since the last call (oldest first)."""

        with self.cond:

            if not self.scans:
                self.cond.wait(timeout)

            scans = list(self.scans)
            self.scans.clear()

        return scans


    def get_scan(self, timeout=0.2):
        """Newest complete scan, or None. Older unread scans are skipped."""

        scans = self.get_scans(timeout)

        if not scans:
            return None

        self.dropped += len(scans) - 1

        return scans[-1]


    def get_points(self):
        """Old API: list of (x, y) of the newest full scan ([] if none yet)."""

        scan = self.get_scan(timeout=0)

        if scan is None:
            return []

        return list(zip(scan.x.tolist(), scan.y.tolist()))


    def close(self):

        self.running = False

        with self.cond:
            self.cond.notify_all()

        self.disconnect()

        if self.thread.is_alive():
            self.thread.join(timeout=1)
