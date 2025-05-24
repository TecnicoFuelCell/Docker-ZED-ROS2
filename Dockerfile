FROM stereolabs/zed:4.2-tools-devel-l4t-r35.4

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

# Install Foxy
RUN add-apt-repository universe && \
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null 


#RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key | apt-key add - && \
#    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu focal main" > /etc/apt/sources.list.d/ros2-latest.list


RUN apt update && apt upgrade -y && \
    apt install -y ros-foxy-desktop python3-argcomplete && \
    apt install -y python3-colcon-common-extensions ros-foxy-tf2-geometry-msgs ros-foxy-rviz2   

# # Clone and build ZED Open Capture
# WORKDIR /opt/share

# RUN git clone https://github.com/stereolabs/zed-open-capture.git && \
#     apt-get install -y libhidapi-dev libusb-1.0-0-dev libhidapi-libusb0 libhidapi-dev libopencv-dev libopencv-viz-dev && \
#     cd zed-open-capture/udev && \
#     cd .. && mkdir build && cd build && \
#     cmake .. && \
#     make -j$(nproc) && \
#     make install && \
#     ldconfig

#Source the project
RUN echo "source /opt/ros/foxy/setup.bash" >> /etc/bash.bashrc
RUN echo "source /opt/share/workspace/install/setup.bash" >> ~/.bashrc

# Other usefull libraries
RUN apt-get update && apt-get install -y \
    build-essential

# torch 
RUN curl -L -o /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl \
      https://developer.download.nvidia.cn/compute/redist/jp/v512/pytorch/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl && \
    pip3 install /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl && \
    rm /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl

# -- build torchvision from source --
# dependencies
RUN apt-get update && apt-get install -y \
    libjpeg-dev zlib1g-dev libpython3-dev libopenblas-dev libavcodec-dev libavformat-dev libswscale-dev

# build torchvision
WORKDIR /tmp
RUN git clone --branch v0.16.0 https://github.com/pytorch/vision.git && \
    cd vision && \
    python3 setup.py install --user && \
    cd .. && rm -rf vision

RUN pip install opencv-python pygame ultralytics pyserial
# TODO: ADD THIS (guil stuff):
# pip install bezier==2020.1.14
# pip install casadi
# pip install transforms3d

# install eigen
WORKDIR /tmp
RUN wget https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz && \
    tar -xzf eigen-3.4.0.tar.gz && \
    cd eigen-3.4.0 && \
    mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install

# install GTSAM
WORKDIR /tmp
RUN git clone https://github.com/borglab/gtsam.git && \
    cd gtsam && \
    sed -i 's/\bint m_runtime;/[[maybe_unused]] int m_runtime;/' gtsam/navigation/ManifoldEKF.h && \
    mkdir build && cd build && \
    cmake .. \
      -DGTSAM_BUILD_EXAMPLES=OFF \
      -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
      -DGTSAM_BUILD_TESTS=OFF \
      -DGTSAM_USE_SYSTEM_EIGEN=ON \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install

# setup envs
RUN echo "source /opt/ros/foxy/setup.bash" >> /etc/bash.bashrc
RUN echo "source /opt/share/workspace/install/setup.bash" >> ~/.bashrc
ENV CMAKE_PREFIX_PATH="/usr/local:$CMAKE_PREFIX_PATH"

# Set the working directory to the workspace
WORKDIR /opt/share/workspace

RUN rm -rf var/lib/apt/lists/*

RUN echo "All done!"