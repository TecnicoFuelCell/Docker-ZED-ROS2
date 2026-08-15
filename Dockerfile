FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Lisbon
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV PIP_NO_CACHE_DIR=1

ARG ROS_DISTRO=jazzy
ENV ROS_DISTRO=${ROS_DISTRO}
ARG WORKSPACE=/workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash-completion \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    locales \
    lsb-release \
    lsof \
    nano \
    psmisc \
    python3 \
    python3-dev \
    python3-matplotlib \
    python3-opencv \
    python3-pil \
    python3-pil.imagetk \
    python3-pip \
    python3-psutil \
    python3-requests \
    python3-scipy \
    python3-tk \
    python3-venv \
    python3-yaml \
    tmux \
    tzdata \
    usbutils \
    v4l-utils \
    vim \
    wget \
    libeigen3-dev \
    libgl1 \
    libglib2.0-0t64 \
    libgtsam-dev \
    libopencv-dev \
    cmake \
    pkg-config \
    libopenblas-dev \
    libjpeg-dev \
    zlib1g-dev \
    && locale-gen en_US en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
      -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu noble main" \
      > /etc/apt/sources.list.d/ros2.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions \
    python3-rosdep \
    python3-vcstool \
    ros-${ROS_DISTRO}-controller-manager \
    ros-${ROS_DISTRO}-compressed-depth-image-transport \
    ros-${ROS_DISTRO}-compressed-image-transport \
    ros-${ROS_DISTRO}-cv-bridge \
    ros-${ROS_DISTRO}-demo-nodes-py \
    ros-${ROS_DISTRO}-example-interfaces \
    ros-${ROS_DISTRO}-geometry-msgs \
    ros-${ROS_DISTRO}-gtsam \
    ros-${ROS_DISTRO}-image-transport \
    ros-${ROS_DISTRO}-image-transport-plugins \
    ros-${ROS_DISTRO}-joint-state-publisher \
    ros-${ROS_DISTRO}-nav-msgs \
    ros-${ROS_DISTRO}-nmea-msgs \
    ros-${ROS_DISTRO}-robot-localization \
    ros-${ROS_DISTRO}-robot-state-publisher \
    ros-${ROS_DISTRO}-ros-base \
    ros-${ROS_DISTRO}-rosbridge-server \
    ros-${ROS_DISTRO}-sensor-msgs \
    ros-${ROS_DISTRO}-std-msgs \
    ros-${ROS_DISTRO}-teleop-twist-keyboard \
    ros-${ROS_DISTRO}-tf2 \
    ros-${ROS_DISTRO}-tf2-geometry-msgs \
    ros-${ROS_DISTRO}-tf2-ros \
    ros-${ROS_DISTRO}-theora-image-transport \
    ros-${ROS_DISTRO}-vision-opencv \
    ros-${ROS_DISTRO}-visualization-msgs \
    ros-${ROS_DISTRO}-xacro \
    ros-${ROS_DISTRO}-yaml-cpp-vendor \
    && rm -rf /var/lib/apt/lists/*

RUN rosdep init || true
RUN rosdep update

# NVIDIA userspace math libraries the aarch64 PyTorch wheel links against
# (NVPL for CPU BLAS/LAPACK, cuDSS for the sparse solver). Base Ubuntu doesn't
# ship these, and community reports indicate a fresh JetPack 7.2 flash doesn't
# either -- torch fails to *import* (not just fails on CUDA) without them.
# This does not install the CUDA toolkit/nvcc itself: this image is built for
# inference only and is expected to run via the NVIDIA Container Runtime on
# the Jetson (e.g. `docker run --runtime nvidia ...`), which mounts the host's
# CUDA driver, cuDNN, and TensorRT userspace libraries into the container.
RUN wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/sbsa/cuda-keyring_1.1-1_all.deb \
      -O /tmp/cuda-keyring.deb && \
    dpkg -i /tmp/cuda-keyring.deb && \
    rm /tmp/cuda-keyring.deb && \
    apt-get update && apt-get install -y --no-install-recommends \
      nvpl \
      libcudss0-cuda-13 \
    && echo "/usr/lib/aarch64-linux-gnu/libcudss/13" > /etc/ld.so.conf.d/cudss.conf \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

# bezier==2020.1.14 (the old Foxy-era pin) does not support Python 3.12, so
# install the current wheel with --no-deps first so it can't drag NumPy along.
# NumPy is pinned to the 2.0.x/2.2.x range the JetPack 7.2 cu132 PyTorch wheels
# below were built and tested against; NumPy 2.x remains ABI-compatible with
# the apt-provided python3-opencv/python3-scipy stack (built against NumPy 1.x
# headers). Avoid upgrading Debian's pip and wheel packages in-place; Ubuntu
# 24.04's pip is sufficient for these wheels.
# --ignore-installed is required because python3-opencv/python3-scipy pulled
# in Debian's python3-numpy (1.26.4) as an apt dependency; apt-installed
# Python packages have no pip RECORD file, so pip can't safely "uninstall"
# it before upgrading and errors out without this flag.
RUN python3 -m pip install --break-system-packages --no-deps bezier==2024.6.20 && \
    python3 -m pip install --break-system-packages --ignore-installed \
      "numpy>=2.0,<2.3" \
      casadi \
      pandas \
      polars \
      py-cpuinfo \
      pyserial \
      seaborn \
      tqdm \
      transforms3d

# PyTorch + torchvision for JetPack 7.2 / CUDA 13.2 / Jetson Orin (sm_87) /
# Python 3.12. NVIDIA has not published an official Jetson wheel or install
# doc for this combination yet; as of writing this is a community-documented,
# prerelease-only path (see https://github.com/iuliaferoli/jetson-jp7.2-install).
# A plain "pip install torch" resolves a datacenter build (sm_100/sm_110)
# that imports fine and reports torch.cuda.is_available() == True, but fails
# with "no kernel image is available" on the first real op on Orin. --pre is
# required or pip won't see the cu132 wheels at all. --extra-index-url (not
# --index-url) keeps other deps resolving from PyPI as normal.
# If your board reports a different CUDA point release (check `nvcc --version`
# or `dpkg -l | grep cuda-toolkit` on the host), swap cu132 for the matching
# cuNNN suffix in the URL below.
RUN python3 -m pip install --break-system-packages --pre \
      torch \
      torchvision \
      --extra-index-url https://download.pytorch.org/whl/cu132

# Ultralytics (YOLO). Installed --no-deps so it can't silently pull in a
# generic PyPI torch/torchvision/opencv build and overwrite the Jetson-specific
# ones installed above. Its other runtime deps (numpy, matplotlib, opencv,
# pillow, pyyaml, requests, scipy, tqdm, psutil, py-cpuinfo, pandas, seaborn)
# are already satisfied by the apt/pip packages above; ultralytics-thop is the
# one that isn't, so it's installed alongside.
RUN python3 -m pip install --break-system-packages --no-deps \
      ultralytics \
      ultralytics-thop

RUN mkdir -p $WORKSPACE
WORKDIR $WORKSPACE

COPY .bashrc.example /tmp/.bashrc.example
RUN cat /tmp/.bashrc.example >> /root/.bashrc && \
    echo "source /opt/ros/${ROS_DISTRO}/setup.bash" >> /root/.bashrc

CMD ["bash", "-lc", "source /opt/ros/${ROS_DISTRO}/setup.bash && exec bash"]
