"""
Witness IP camera viewer with person detection (OpenCV).

Camera  : 192.168.70.66 (on "Ethernet 3"; the PC needs 192.168.70.200 on that adapter)
Streams : main 1920x1080, sub 640x360, both ~25 fps - RTSP, no login

Usage:
    python camera_viewer.py                    # sub stream (640x360, lowest delay) + YOLOv4-tiny person detector
    python camera_viewer.py --stream main      # 1920x1080 - needs a lot more CPU to decode in real time
    python camera_viewer.py --detector hog     # OpenCV's built-in HOG detector (no model files, weaker)
    python camera_viewer.py --detector none    # video only

YOLO model files live in models/ and are downloaded (~24 MB) automatically if missing.

Keys (click the video window first):
    q / Esc   quit
    d         person detection on / off
    s         save snapshot to snapshots/
    f         fullscreen on / off
"""
import argparse
import os
import threading
import time
import urllib.request
from pathlib import Path

# Must be set before OpenCV opens the stream:
#   rtsp_transport tcp   - avoids grey / smeared frames from lost UDP packets
#   nobuffer, low_delay  - hand each frame over as soon as it arrives instead of buffering
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")

import cv2
import numpy as np

CAMERA_IP = "192.168.70.66"
STREAMS = {
    "main": f"rtsp://{CAMERA_IP}:554/media/live/1/1",
    "sub": f"rtsp://{CAMERA_IP}:554/media/live/1/2",
}

# FFmpeg's multi-threaded decoder holds back about one frame per thread (OpenCV uses one per CPU:
# 16 here = ~0.75 s delay, measured). A single thread decodes this camera easily with ~0.1 s delay.
DECODER_THREADS = 1
# If the PC decodes slower than the camera sends (slow or throttled CPU) frames pile up and the delay
# grows without limit. Past this many seconds behind, reconnect (drops the backlog) with more threads.
MAX_LAG = 1.0

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models"
SNAPSHOT_DIR = HERE / "snapshots"
YOLO_FILES = {
    "yolov4-tiny.cfg": "https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg",
    "yolov4-tiny.weights": "https://github.com/AlexeyAB/darknet/releases/download/darknet_yolo_v4_pre/yolov4-tiny.weights",
}

WINDOW = "Witness camera"
GREEN = (0, 220, 0)
WHITE = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX


class CameraStream:
    """Reads the RTSP stream in a background thread and keeps only the newest frame,
    so slow detection never makes the picture lag behind real time. Reconnects on its own."""

    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.fps = 0.0
        self.status = "connecting"
        self.threads = DECODER_THREADS
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_N_THREADS, self.threads,
            ])
            if not cap.isOpened():
                self.status = "camera not reachable - retrying"
                print(f"[camera] cannot open {self.url} "
                      f"(check Ethernet 3 has 192.168.70.200 and ping {CAMERA_IP})")
                time.sleep(2)
                continue

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"[camera] connected {width}x{height} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps  {self.url}")
            self.status = "live"

            count, t0 = 0, time.time()
            min_offset = None
            while self.running:
                ok, frame = cap.read()
                if not ok:
                    print("[camera] stream lost - reconnecting")
                    self.status = "reconnecting"
                    break
                with self.lock:
                    self.frame = frame
                    self.frame_id += 1

                # wall clock minus stream time stays constant while we keep up and grows when we fall behind
                pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if pos > 0:
                    offset = time.time() - pos
                    min_offset = offset if min_offset is None else min(min_offset, offset)
                    if offset - min_offset > MAX_LAG:
                        self.threads = min(self.threads * 2, 8)
                        print(f"[camera] PC decodes too slowly ({offset - min_offset:.1f} s behind) - jumping back "
                              f"to live with {self.threads} decoder threads (--stream sub needs far less CPU)")
                        self.status = "catching up"
                        break
                count += 1
                now = time.time()
                if now - t0 >= 1.0:
                    self.fps = count / (now - t0)
                    count, t0 = 0, now
            cap.release()
            time.sleep(1)

    def read(self):
        with self.lock:
            return self.frame_id, self.frame

    def stop(self):
        self.running = False
        self.thread.join(timeout=3)


def nms(boxes, scores, conf, overlap=0.4):
    """Drop weak boxes and merge boxes that cover the same person."""
    if len(boxes) == 0:
        return []
    keep = cv2.dnn.NMSBoxes(boxes, scores, conf, overlap)
    return [(boxes[i], scores[i]) for i in np.ravel(keep)]


class HogDetector:
    """OpenCV's built-in HOG + SVM people detector. Needs no download.
    Works best on upright people whose whole body is visible."""
    name = "HOG"

    def __init__(self, conf=0.5, width=640):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.conf = conf
        self.width = width

    def detect(self, frame):
        scale = min(1.0, self.width / frame.shape[1])
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
        rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        boxes = [[int(v / scale) for v in r] for r in rects]
        return nms(boxes, np.ravel(weights).tolist(), self.conf)


def ensure_model_file(name):
    path = MODEL_DIR / name
    if not path.exists():
        MODEL_DIR.mkdir(exist_ok=True)
        print(f"[model] downloading {name} ...")
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(YOLO_FILES[name], tmp)
        tmp.replace(path)
    return path


class YoloDetector:
    """YOLOv4-tiny (COCO) through OpenCV's DNN module. Much better than HOG for partly hidden,
    sitting or far-away people. Model files (~24 MB) are downloaded into models/ on first use."""
    name = "YOLOv4-tiny"

    def __init__(self, conf=0.4, size=416):
        cfg = ensure_model_file("yolov4-tiny.cfg")
        weights = ensure_model_file("yolov4-tiny.weights")
        net = cv2.dnn.readNetFromDarknet(str(cfg), str(weights))
        self.model = cv2.dnn.DetectionModel(net)
        self.model.setInputParams(size=(size, size), scale=1 / 255.0, swapRB=True)
        self.conf = conf

    def detect(self, frame):
        class_ids, scores, boxes = self.model.detect(frame, confThreshold=self.conf, nmsThreshold=0.4)
        return [([int(v) for v in box], float(score))
                for class_id, score, box in zip(np.ravel(class_ids), np.ravel(scores), boxes)
                if class_id == 0]  # COCO class 0 = person


def draw_people(img, people, s):
    thick = max(2, round(2 * s))
    for (x, y, w, h), score in people:
        cv2.rectangle(img, (x, y), (x + w, y + h), GREEN, thick)
        cv2.putText(img, f"person {score:.2f}", (x, max(y - int(8 * s), int(20 * s))),
                    FONT, 0.6 * s, GREEN, thick)


def draw_info(img, lines, s):
    """Lines of (text, color) on a darkened box in the top-left corner."""
    pad, line_h = int(10 * s), int(26 * s)
    box_w = min(img.shape[1], int(440 * s))
    box_h = min(img.shape[0], pad * 2 + line_h * len(lines))
    img[:box_h, :box_w] //= 3
    for i, (text, color) in enumerate(lines):
        y = pad + line_h * (i + 1) - int(7 * s)
        cv2.putText(img, text, (pad, y), FONT, 0.6 * s, color, max(1, round(1.5 * s)), cv2.LINE_AA)


def status_screen(status):
    img = np.zeros((720, 1280, 3), np.uint8)
    cv2.putText(img, f"{status} ...", (40, 360), FONT, 1.2, WHITE, 2, cv2.LINE_AA)
    cv2.putText(img, STREAMS["main"].rsplit("/media", 1)[0], (40, 410), FONT, 0.8, (160, 160, 160), 1, cv2.LINE_AA)
    return img


def make_detector(args):
    if args.detector == "none":
        return None
    if args.detector == "yolo":
        return YoloDetector(conf=0.4 if args.conf is None else args.conf)
    return HogDetector(conf=0.5 if args.conf is None else args.conf, width=args.det_width)


def parse_args():
    p = argparse.ArgumentParser(description="Witness IP camera viewer with person detection")
    p.add_argument("--stream", choices=list(STREAMS), default="sub",
                   help="sub = 640x360 (default, light), main = 1920x1080 (heavy to decode)")
    p.add_argument("--url", help="custom RTSP URL (overrides --stream)")
    p.add_argument("--detector", choices=["yolo", "hog", "none"], default="yolo")
    p.add_argument("--conf", type=float, help="minimum detection score (default: hog 0.5, yolo 0.4)")
    p.add_argument("--det-width", type=int, default=640,
                   help="HOG only: resize frames to this width before detecting "
                        "(bigger finds smaller / farther people but is slower)")
    return p.parse_args()


def main():
    args = parse_args()
    url = args.url or STREAMS[args.stream]
    stream_label = "custom" if args.url else args.stream
    detector = make_detector(args)  # before the camera, YOLO may need to download first
    cam = CameraStream(url)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)

    detect_on = detector is not None
    fullscreen = False
    last_id = 0
    last_view = None
    last_count = None
    people, det_ms = [], 0.0
    view_fps, frames, t0 = 0.0, 0, time.time()

    try:
        while True:
            frame_id, frame = cam.read()
            view = None
            if frame is None or cam.status != "live":
                view = status_screen(cam.status)
            elif frame_id != last_id:
                last_id = frame_id
                people = []
                if detect_on:
                    t = time.perf_counter()
                    people = detector.detect(frame)
                    det_ms = (time.perf_counter() - t) * 1000
                    if len(people) != last_count:
                        print(f"[{time.strftime('%H:%M:%S')}] persons: {len(people)}")
                        last_count = len(people)

                frames += 1
                now = time.time()
                if now - t0 >= 1.0:
                    view_fps = frames / (now - t0)
                    frames, t0 = 0, now

                view = frame.copy()
                s = max(1.0, view.shape[1] / 1280)
                draw_people(view, people, s)
                h, w = frame.shape[:2]
                det_text = f"{detector.name}  {det_ms:.0f} ms" if detect_on else "detection OFF"
                draw_info(view, [
                    (f"{stream_label}  {w}x{h}  camera {cam.fps:.1f} fps", WHITE),
                    (f"view {view_fps:.1f} fps   {det_text}", WHITE),
                    (f"persons: {len(people)}", GREEN if people else WHITE),
                    (time.strftime("%Y-%m-%d %H:%M:%S"), WHITE),
                ], s)

            if view is not None:
                cv2.imshow(WINDOW, view)
                last_view = view

            key = cv2.waitKey(1 if view is not None else 5) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("d") and detector is not None:
                detect_on = not detect_on
                last_count = None
                print(f"[detect] {'ON' if detect_on else 'OFF'}")
            elif key == ord("s") and last_view is not None:
                SNAPSHOT_DIR.mkdir(exist_ok=True)
                path = SNAPSHOT_DIR / time.strftime("snap_%Y%m%d_%H%M%S.jpg")
                cv2.imwrite(str(path), last_view)
                print(f"[snapshot] {path}")
            elif key == ord("f"):
                fullscreen = not fullscreen
                cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN,
                                      cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:  # closed with the X button
                break
    finally:
        cam.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
