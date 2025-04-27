FROM dustynv/l4t-pytorch:r36.2.0

# Set environment variable to non-interactive mode for apt
ENV DEBIAN_FRONTEND=noninteractive


# Update and install essential packages
RUN apt update && apt install -y \
    apt-utils \
    locales \
    gnupg2 \
    lsb-release \
    curl \
    git python3-pip \
    tmux nano \
    x11-apps \
    software-properties-common && \
    locale-gen en_US en_US.UTF-8 && \
    update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 && \
    export LANG=en_US.UTF-8

# Install ROS 2 Humble key and repository
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/ros2-latest.list > /dev/null

# Install Python3, ROS 2 Humble, and other dependencies
RUN apt update && apt upgrade -y && \
    apt install -y ros-humble-desktop python3-argcomplete && \
    apt install -y python3-colcon-common-extensions build-essential

# Install dependencies for ZED Open Capture
RUN apt-get install -y \
    libhidapi-dev \
    libusb-1.0-0-dev \
    libopencv-dev 

# ────────────────────────────────────────────────────────────────
# Python stuff
#    1. Remove numpy, torch, torchvision
#    2. Install NumPy 1.26.1
#    3. Install JetPack 6 GPU wheels
# ────────────────────────────────────────────────────────────────
#ARG TORCH_WHL="https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl"
#ARG TV_WHL="https://pypi.jetson-ai-lab.dev/jp6/cu126/+f/5f9/67f920de3953f/torchvision-0.20.0-cp310-cp310-linux_aarch64.whl"

#RUN set -e ;\
#    pip uninstall -y torch torchvision torchaudio numpy || true && \
#    pip install --no-cache-dir numpy==1.26.1 && \
#    \
#    # download wheels under their original, valid names
#    wget -q --show-progress -P /tmp "$TORCH_WHL" && \
#    wget -q --show-progress -P /tmp "$TV_WHL" && \
#    \
#    # install them (basename preserves the good filename)
#    pip install --no-cache-dir /tmp/$(basename "$TORCH_WHL") \
#                              /tmp/$(basename "$TV_WHL") && \
#    \
#    # any extra python deps *after* the GPU wheels
#    pip install --no-cache-dir timm==0.6.12 --no-deps && \
#    \
#    rm -f /tmp/*.whl

    
# Source ROS 2 Humble and ZED workspace setup files
RUN echo "source /opt/ros/humble/setup.bash" >> /etc/bash.bashrc
RUN echo "source /opt/share/workspace/install/setup.bash" >> ~/.bashrc

# Install additional Python libraries
RUN pip install opencv-python pygame ultralytics pyserial

# Set the working directory to the workspace
WORKDIR /opt/share/workspace

# Clean up
RUN rm -rf /var/lib/apt/lists/*

RUN echo "All done!"
