FROM stereolabs/zed:4.2-devel-cuda12.1-ubuntu22.04

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

# Install ROS 2 Humble
RUN add-apt-repository universe && \
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" | tee /etc/apt/sources.list.d/ros2.list > /dev/null

RUN apt update && apt upgrade -y && \
    apt install -y ros-humble-desktop python3-argcomplete && \
    apt install -y python3-colcon-common-extensions 

# Clone and build ZED Open Capture
WORKDIR /opt/share

RUN git clone https://github.com/stereolabs/zed-open-capture.git && \
    apt-get install -y libhidapi-dev libusb-1.0-0-dev libhidapi-libusb0 libhidapi-dev libopencv-dev libopencv-viz-dev && \
    cd zed-open-capture/udev && \
    cd .. && mkdir build && cd build && \
    cmake .. && \
    make -j$(nproc) && \
    make install && \
    ldconfig

# Source the project
RUN echo "source /opt/ros/humble/setup.bash" >> /etc/bash.bashrc
RUN echo "source /opt/share/workspace/install/setup.bash" >> ~/.bashrc

# Other usefull libraries
RUN apt-get update && apt-get install -y \
    build-essential

RUN pip install opencv-python pygame ultralytics pyserial 
RUN pip install --no-cache-dir numpy==1.24.4
RUN pip install --no-cache-dir pycocotools
RUN pip install --no-cache-dir transforms3d
RUN pip install --no-cache-dir gtsam
RUN pip install --no-cache-dir scikit-learn
RUN pip install --no-cache-dir ros-humble-tf-transformations

# Set the working directory to the workspace
WORKDIR /opt/share/workspace

RUN rm -rf var/lib/apt/lists/*

RUN echo "All done!"