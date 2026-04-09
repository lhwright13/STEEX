# Vision-Guided Robotic Arm Project Reference

## Hardware

### Arduino Braccio - Two Generations

| Feature | Original Braccio | Braccio++ |
|---------|-----------------|-----------|
| Servo type | Standard PWM (analog) | Smart serial servos (RS485) |
| Controller | Arduino Uno/Mega + Shield | Arduino Nano RP2040 Connect + carrier |
| Position feedback | None (open-loop) | Yes - `getPosition(id)` reads actual angle |
| Max angle range | 0-180 deg/joint | 0-315 deg/joint |
| Display | None | 240x240 LCD with LVGL UI |
| Power | 5V external via shield | USB-C PD (7.2V/2A PPS) |

**DOF**: 6 (base rotation, shoulder, elbow, wrist vertical, wrist rotation, gripper)

#### Original Braccio Joint Limits
| Joint | Min | Max | Servo Pin |
|-------|-----|-----|-----------|
| Base (M1) | 0 | 180 | 11 |
| Shoulder (M2) | 15 | 165 | 10 |
| Elbow (M3) | 0 | 180 | 9 |
| Wrist vertical (M4) | 0 | 180 | 5 |
| Wrist rotation (M5) | 0 | 180 | 6 |
| Gripper (M6) | 10 | 73 | 3 |

**Reach**: ~80cm extended | **Payload**: ~150-200g | **Link lengths**: base 71.5mm, upper arm 125mm, forearm 125mm, hand+gripper 192mm

#### Libraries
- **Original**: `Braccio` v2.0.4 - [github.com/arduino-libraries/Braccio](https://github.com/arduino-libraries/Braccio)
- **Braccio++**: `Arduino_Braccio_plusplus` v1.3.3 - [github.com/arduino-libraries/Arduino_Braccio_plusplus](https://github.com/arduino-libraries/Arduino_Braccio_plusplus)

#### Original Braccio API
```cpp
#include <Braccio.h>
#include <Servo.h>
Servo base, shoulder, elbow, wrist_ver, wrist_rot, gripper;

Braccio.begin();  // or begin(SOFT_START_DISABLED) for Shield V1.6
Braccio.ServoMovement(20, 90, 90, 90, 90, 90, 10);
// params: step_delay(10-30ms), base, shoulder, elbow, wrist_ver, wrist_rot, gripper
```

#### Braccio++ API
```cpp
#include <Braccio++.h>
Braccio.begin();
Braccio.move(1).to(90.0f);                       // single motor
Braccio.moveTo(a1, a2, a3, a4, a5, a6);          // all 6 synchronized
Braccio.positions(angles);                         // read current angles (float[6])
Braccio.setAngularVelocity(45.0f);                // deg/sec
Braccio.setMaxTorque(500);                         // 0-1000
Braccio.engage(1); / Braccio.disengage(1);        // enable/disable torque
```

---

### Intel RealSense Depth Camera

**Status**: Intel wound down RealSense in late 2021. Cameras still available on secondary market but stock is dwindling.

| Spec | D415 | D435/D435i | D455 | D405 |
|------|------|------------|------|------|
| RGB | 1920x1080@30 | 1920x1080@30 | 1920x1080@30 | No RGB |
| Depth | 1280x720@30 | 1280x720@30 | 1280x720@30 | 1280x720@30 |
| Min range | 0.45m | 0.28m | 0.52m | **0.07m** |
| Max range | ~10m | ~10m | ~6m | 0.7m |
| FOV (depth) | 65x40 | **87x58** | 87x58 | 87x58 |
| Shutter | Rolling | Global | Global | Global |
| IMU | No | D435i only | Yes | No |
| Best for | Scanning | **General/robotics** | Outdoor | Close-up manipulation |

#### macOS / Apple Silicon Support

- **Problematic on Apple Silicon.** librealsense has incomplete arm64 support.
- pip `pyrealsense2` wheels may not exist for macOS ARM. Build from source is recommended:

```bash
brew install cmake libusb pkg-config
git clone https://github.com/IntelRealSense/librealsense.git
cd librealsense && mkdir build && cd build
cmake .. \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DPYTHON_EXECUTABLE=$(which python3) \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_EXAMPLES=OFF \
  -DBUILD_GRAPHICAL_EXAMPLES=OFF \
  -DFORCE_RSUSB_BACKEND=ON \
  -DCMAKE_OSX_ARCHITECTURES=arm64
make -j$(sysctl -n hw.ncpu)
sudo make install
```

**Alternative cameras with better macOS support:**
- **Luxonis OAK-D** - Best macOS/Apple Silicon support, `depthai` installs via pip, stereo depth + on-device neural inference
- **Orbbec Femto/Gemini** - Orbbec SDK 2 supports macOS

#### RealSense Python Usage

```python
import pyrealsense2 as rs
import numpy as np

# Setup pipeline with aligned RGB + depth
pipeline = rs.pipeline()
config = rs.config()
config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
profile = pipeline.start(config)
align = rs.align(rs.stream.color)

# Capture loop
frames = pipeline.wait_for_frames()
aligned = align.process(frames)
depth_frame = aligned.get_depth_frame()
color_frame = aligned.get_color_frame()

depth_image = np.asanyarray(depth_frame.get_data())  # uint16, mm
color_image = np.asanyarray(color_frame.get_data())   # uint8, BGR

# Deprojection: pixel (u,v) + depth -> 3D point (X,Y,Z) in meters
depth_intrin = depth_frame.profile.as_video_stream_profile().intrinsics
pixel = [320, 240]
depth_value = depth_frame.get_distance(pixel[0], pixel[1])
point_3d = rs.rs2_deproject_pixel_to_point(depth_intrin, pixel, depth_value)

# Depth filters for cleanup
spatial = rs.spatial_filter()
temporal = rs.temporal_filter()
hole_filling = rs.hole_filling_filter()
```

---

## System Architecture

```
[Intel RealSense]
      |  pyrealsense2 (RGB + Depth)
      v
[Mac Mini - Python Main Loop]
  |-- Claude Vision API  -> scene understanding, object ID
  |-- OpenCV             -> precise pixel localization
  |-- rs2_deproject()    -> 2D pixel -> 3D camera coords
  |-- T_cam_to_robot     -> 3D camera -> 3D robot coords
  |-- ikpy               -> 3D coords -> joint angles
  |
  |  pyserial (binary protocol, ACK-based)
  v
[Arduino - Braccio]
  |-- Joint limit enforcement
  |-- Smooth interpolation
  |-- ACK responses
  v
[6 Servos]
```

### Pipeline Steps
1. **Capture**: RealSense grabs aligned RGB + depth
2. **Detect**: Claude Vision API identifies objects, returns approximate bounding boxes
3. **Localize**: OpenCV refines to precise (u,v) pixel within Claude's region
4. **Deproject**: `rs2_deproject_pixel_to_point()` -> (X,Y,Z) in camera frame
5. **Transform**: Calibration matrix converts camera coords to robot base coords
6. **IK Solve**: ikpy converts (X,Y,Z) + approach angle to 6 joint angles
7. **Execute**: Send angles over serial, Arduino drives servos smoothly

---

## Inverse Kinematics

**Neither Braccio library includes IK.** Run IK on Mac Mini, send computed angles to Arduino.

### Recommended: ikpy
```bash
pip install ikpy
```
- Pure Python, no ROS needed
- Load robot from URDF or define chain with DH parameters
- Solves in 7-50ms

### Alternatives
- **roboticstoolbox-python** (Peter Corke) - More mature, IK in ~4us, `ikine_LM()` method
- **CGx-InverseK** - Arduino C++ library tested with Braccio specifically

### Braccio DH Parameters
| Joint | d (mm) | a (mm) | alpha (rad) |
|-------|--------|--------|-------------|
| 1 (base) | 71.5 | 0 | -pi/2 |
| 2 (shoulder) | 0 | -125 | 0 |
| 3 (elbow) | 0 | -125 | 0 |
| 4 (wrist pitch) | 0 | 0 | pi/2 |
| 5 (wrist roll) | 192 | 0 | 0 |

### URDF Files
- [github.com/jonabalzer/braccio_description](https://github.com/jonabalzer/braccio_description)
- [github.com/grassjelly/ros_braccio_urdf](https://github.com/grassjelly/ros_braccio_urdf)

---

## Camera-to-Robot Calibration (Eye-to-Hand)

### Simple Method (Point Correspondence)
1. Attach marker to gripper
2. Move to 8-12 known positions
3. Record robot (X,Y,Z) from FK and camera (X,Y,Z) from deprojection at each
4. Solve with `cv2.estimateAffinePartial3D()`
5. Accurate enough for objects >1cm

### Precise Method (OpenCV calibrateHandEye)
```python
R_cam2base, t_cam2base = cv2.calibrateHandEye(
    R_gripper2base, t_gripper2base,
    R_target2cam, t_target2cam,
    method=cv2.CALIB_HAND_EYE_TSAI
)

def camera_to_robot(point_camera):
    p = np.array(point_camera).reshape(3, 1)
    return (R_cam2base @ p + t_cam2base).flatten()
```

### Coordinate Frames
- **Camera**: +X right, +Y down, +Z forward
- **Robot base** (typical): +X forward, +Y left, +Z up

---

## Claude Vision API for Object Detection

```python
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=8000,
    system="Return JSON with bounding boxes: {element, bbox: [x1,y1,x2,y2] normalized 0-1, confidence}",
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_image}},
        {"type": "text", "text": "Detect all objects in this image"}
    ]}]
)
```

**Strengths**: Semantic understanding, handles novel objects, scene reasoning
**Limitations**: ~5-15% bbox error, 1-3s latency, 2D only, non-deterministic

**Recommended hybrid**: Claude for high-level scene understanding -> OpenCV for precise pixel localization -> RealSense depth for 3D

---

## Serial Communication (Python <-> Arduino)

### Binary Protocol (Recommended)
```python
import serial
from enum import IntEnum

class Order(IntEnum):
    MOVE = 0
    GRIPPER = 1
    HOME = 2
    ACK = 3
    ERROR = 4
    PING = 5

ser = serial.Serial('/dev/cu.usbmodemXXXX', 115200, timeout=1.0)
time.sleep(2)  # Wait for Arduino reset

def write_order(order): ser.write(order.to_bytes(1, 'little'))
def write_i16(value): ser.write(value.to_bytes(2, 'little', signed=True))

def send_angles(angles):
    write_order(Order.MOVE)
    for a in angles:
        write_i16(a)
    ack = ser.read(1)
    if not ack or ack[0] != Order.ACK:
        raise TimeoutError("No ACK")
```

### Best Practices
- Wait 2-3s after opening port (Arduino resets)
- Use binary, not ASCII (faster, unambiguous)
- Implement ACK-based flow control (2-token semaphore)
- Arduino should interpolate smoothly (1-2 deg/step, 10-20ms delay)
- macOS serial port: `/dev/cu.usbmodemXXXX`

---

## Safety

### Software Limits (enforce on BOTH sides)
```python
JOINT_LIMITS = {
    'base': (0, 180), 'shoulder': (15, 165), 'elbow': (0, 180),
    'wrist_v': (0, 180), 'wrist_r': (0, 180), 'gripper': (10, 73),
}
MAX_REACH = 0.40        # 400mm from base
MIN_HEIGHT = 0.05       # 50mm above table
MAX_DEG_PER_STEP = 2    # Smooth motion
```

### Critical
- **Power**: 5V/4A+ dedicated supply for original; USB-C PD charger for Braccio++
- **E-stop**: Wire a physical kill switch on servo power
- **Homing**: Always start from known position (no encoders on original)
- **Payload**: Max 150-200g at full extension
- **Self-collision**: Enforce minimum radius from base axis (>80mm)
- **Stall protection**: Timeout if servo can't reach target in 3s

---

## Reference Projects

| Project | Stack | Link |
|---------|-------|------|
| **su_chef** (best reference) | Braccio + overhead cam + ROS + YOLOv3 | [github.com/lots-of-things/su_chef](https://github.com/lots-of-things/su_chef) |
| EI Pick-n-Place | Braccio++ + OAK-D + ROS2 + YOLOv5 | [Edge Impulse docs](https://docs.edgeimpulse.com/experts/readme/featured-machine-learning-projects/robotic-arm-sorting-arduino-braccio) |
| robo_hslu_braccio | Braccio + Python FABRIK IK | [github.com/Joelius300/robo_hslu_braccio](https://github.com/Joelius300/robo_hslu_braccio) |
| CGx-InverseK | Arduino IK library for Braccio | [github.com/cgxeiji/CGx-InverseK](https://github.com/cgxeiji/CGx-InverseK) |
| Claude Vision Detection | Claude API bounding boxes | [github.com/Doriandarko/Claude-Vision-Object-Detection](https://github.com/Doriandarko/Claude-Vision-Object-Detection) |
| Braccio URDF | Robot description for IK | [github.com/jonabalzer/braccio_description](https://github.com/jonabalzer/braccio_description) |

## Python Dependencies

```
pyrealsense2    # depth camera SDK (build from source on macOS)
anthropic       # Claude Vision API
opencv-python   # precise localization, calibration
ikpy            # inverse kinematics
pyserial        # Arduino communication
numpy           # everything
```
