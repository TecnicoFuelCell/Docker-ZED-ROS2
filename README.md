# Sim

This Dockerfile provides the essential tools needed to work with the simulation and vision pipeline of the autonomous vehicle: **ROS2 Jazzy**, **Gazebo Harmonic**, **Python 3** (with its most used robotics/vision libraries), and **GTSAM**.

It is intended to be accessible to everyone on the team, regardless of hardware — it does **not require** an Nvidia GPU to run. If you do have an Nvidia graphics card, the included launcher script can optionally enable GPU passthrough for better simulation and rendering performance; if you don't, everything still works normally on CPU (just slower for GPU-accelerated tasks like rendering or ML inference).

The environment does **NOT** have ZED tools.

## Requirements

- [Docker](https://www.docker.com/products/docker-desktop/) installed and running
- Minimum 15GB free storage space
- Minimum 8GB RAM
- (Optional) Nvidia GPU + drivers + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), if you want GPU acceleration

## Setup

> [!NOTE]
> For Windows users, you may need to open a terminal and enter WSL to be able to run the following commands.

### 1. Run the setup script

Everything — building the image and starting the container — is handled by `run_container.sh`. Open a terminal in this directory and run:

```sh
./run_container.sh
```

The script will:
- Build the image automatically the first time (subsequent runs skip rebuilding if the image already exists)
- Ask you a few questions and use sensible defaults if you just press Enter

You'll be prompted for:

| Prompt | What it does | Default |
|---|---|---|
| Docker image name | Name of the built image | `sim` |
| Container name | Name of the running container | `sim` |
| Workspace path to mount | Your local ROS2 workspace, mounted into the container so you can edit code from your host and build/run it inside | `../Autonomous_Systems/ros2_ws` |
| Enable GUI/X11 forwarding? | Lets GUI apps (Gazebo, RViz) running inside the container display windows on your screen | No |
| Enable GPU passthrough? | Gives the container access to your Nvidia GPU | Yes if image name contains `cuda`, otherwise No |
| Privilege the container? | Grants broader hardware access (`/dev` mount), useful if you're connecting physical robot hardware | No |

If you answer **Yes** to GUI forwarding, the script automatically handles the X11 display permission (`xhost`) for you — including revoking it again once you're done, so it isn't left open indefinitely.

### 2. Re-running the script

If a container with the same name already exists, the script detects this and asks whether you want to reattach to it (starting it first if it's stopped) instead of trying to create a new one. This means you can just run `./run_container.sh` again any time you want to get back into your environment.

### 3. Checking GPU acceleration is working

If you enabled GPU passthrough, you can verify it's actually being used (rather than silently falling back to slower CPU rendering) by running the following inside the container:

```sh
gpu-check
```

This prints the active OpenGL renderer and Nvidia driver status, and will clearly flag it if software rendering is being used instead of your GPU.

## Manual usage (without the script)

If you prefer to build and run manually instead of using `run_container.sh`:

```sh
docker build -t sim .
```

```sh
docker run -it --name sim \
  -v <path_to_your_ros2_ws>:/opt/share/workspace \
  sim
```

To additionally enable GUI windows (Gazebo, RViz):

```sh
xhost +local:docker
docker run -it --name sim \
  -e DISPLAY=$DISPLAY \
  -v <path_to_your_ros2_ws>:/opt/share/workspace \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  sim
xhost -local:docker # disable display forwarding after running docker
```

To additionally enable GPU passthrough, add `--gpus all` to the `docker run` command above (requires the NVIDIA Container Toolkit installed on your host).