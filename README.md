# Jetson environment

Docker environment used on Jetson with all the necessary tools and libraries to run the autonomous tech stack. Many libraries in this dockerfile are especifically for ARM64, to replicate the same environment on your computer, please use dockerfile in `desktop` branch.

## Prerequisites
Before running this script, ensure:

1. Docker is installed and running on your system.
2. You have NVIDIA drivers and the NVIDIA Container Toolkit installed for GPU support.
3. You have a working directory prepared for binding with the container.
3. For linux, run the following lines:
```bash
xhost +local:docker
```

## Script Procedure
- Run the lin script (tested in Linux and WSL2)

## No Script Procedure
* Download the docker file
* On the directory where the docker file is, open a terminal (not a wsl one) and write:
```bash
docker build -t <name_you_want_to_give_to_the_image> .
```
* Open xLaunch and select the display number as 0 and proceed with the pre-selected things
* Create on the same directory as the Dockerfile a directory /share/catkin_ws/src
* Create the container as follows:

```bash
docker run --name <name_you_want_to_give_to_the_container> --privileged --gpus all -v /dev:/dev -it -v "<your_workspace_path>:/opt/share/workspace" -env="DISPLAY" --env="QT_X11_NO_MITSHM=1" --volume="/tmp/.X11-unix:/tmp/.X11-unix:rw" <name_of_your_image>
```

## Inside Jetson

### Canonical repo location

This repo is shared team tooling, so it lives in the group-shared area that
`setup_jetson.py` provisions — not in a per-user home:

```
/opt/tfc-autonomous/repositories/Docker-ZED-ROS2
```

`setup_jetson.py` exports this path as `TFC_DOCKER_DIR` in `tfc_paths.env`; the
`jetson` CLI reads it, so nothing is hard-coded.

### Bootstrap (once, and after every manifest change)

```bash
# 1. bootstrap clone (temporary: setup must create the final directory first)
git clone <url> /home/tfcadmin/Docker-ZED-ROS2

# 2. run setup from the bootstrap copy (creates dirs + tfc_paths.env)
sudo python3 /home/tfcadmin/Docker-ZED-ROS2/jetson-setup/setup_jetson.py \
  --manifest /home/tfcadmin/Docker-ZED-ROS2/jetson-setup/manifest.json

# 3. move the repo into its canonical home
sudo mv /home/tfcadmin/Docker-ZED-ROS2 /opt/tfc-autonomous/repositories/Docker-ZED-ROS2
sudo chown -R tfcadmin:tfc-autonomous /opt/tfc-autonomous/repositories/Docker-ZED-ROS2

# 4. link onto PATH + install TFC env sourcing for all users (idempotent)
/opt/tfc-autonomous/repositories/Docker-ZED-ROS2/jetson bootstrap
```

`jetson bootstrap` is idempotent: it creates/fixes the `/usr/local/bin/jetson`
symlink and installs the guarded TFC env sourcing (`/etc/bash.bashrc` for
interactive terminals, `/etc/profile.d/tfc-autonomous-env.sh` for login/SSH).
Re-run it after any manifest change is applied so paths stay up to date.

### Usage

`jetson` is now on PATH for every user:

```bash
jetson docker up --build          # build + start (first run)
jetson docker up                  # subsequent runs
jetson docker enter               # enter with X11 forwarding
```

For a per-member instance from your own clone, add your overrides:

```bash
jetson --member ~/.config/tfc/compose.env docker up
```

All commands:

```bash
jetson docker build               # build the image from the Dockerfile
jetson docker up                  # start the container
jetson docker up --build          # build then start
jetson docker enter               # enter with X11 forwarding (SSH-safe)
jetson docker stop                # stop, keep the container
jetson docker down                # stop and remove the container
jetson docker rm                  # force-remove the container
jetson docker ps                  # status
jetson docker logs                # tail logs
```

Inside the container, ROS2 helpers are available (from `.bashrc.example`, baked
into the image — rebuild with `jetson docker up --build` after editing it):

```bash
ros_build            # colcon build --symlink-install
ros_source           # re-source ROS + workspace
ros_clean            # rm -rf build install log + re-source
ros_launch <pkg> <file>
foxglove_launch      # rosbridge websocket
rviz
pipeline_with_logs   # what_could_go_wrong pipeline, logs to $TFC_LOG_DIR
car_with_logs        # what_could_go_wrong car, logs to $TFC_LOG_DIR
```

X11 check inside the container:

```bash
xeyes # basic test with eyes to see if X11 is working
```

### Troubleshooting X11

- `ssh -X user@jetson` is required for X11 forwarding over SSH (sets
  `DISPLAY=localhost:10.0` and the auth cookie).
- If `ssh -X` reports `~/.Xauthority not writable, changes ignored`, the file is
  root-owned (an old compose version bind-mounted it into the container as
  `/root/.Xauthority` and created it as root). Fix once per member:
  ```bash
  sudo chown $(id -u):$(id -g) ~/.Xauthority
  ```
- If `jetson docker enter` says "could not extract X11 cookie", run
  `xauth list $DISPLAY` — an empty result usually means the cookie couldn't be
  stored (see above), or X11 forwarding wasn't negotiated.

Scripts:

```
cd VisionSystems/yolo_lane_detector
python yolo_stop.py
```

