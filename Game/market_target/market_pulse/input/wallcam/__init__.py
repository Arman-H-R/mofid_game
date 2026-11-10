"""Ball hits on the wall, seen by one IP camera (replaces the LiDAR).

    stream.py    camera frames, each with the time it was taken (the camera's own clock)
    geometry.py  camera picture -> wall cm: lens correction + homography (WallMap)
    markers.py   ArUco markers shown on the screen for automatic calibration
    detect.py    background + motion -> ball tracks -> the moment the ball turns at the wall (HitDetector);
                 a hand held still on the wall (TouchDetector)
    source.py    the game's input source (a thread: stream -> detector -> Impact)

The camera looks at the wall from the front. The ball's picture, mapped onto the wall, runs towards
the point it will hit and turns there (it bounces back or drops): that turn is the hit, at the wall,
so the mapping is exact there. Needs OpenCV and PyAV (pip install opencv-python av).
"""
